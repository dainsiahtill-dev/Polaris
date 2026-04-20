"""SchemaRegistry — Registry and validation for AuditEvent schemas.

Provides schema registration, version management, and validation
for CloudEvents-aligned audit schemas.

Features:
- Register AuditEvent subclasses with version tracking
- Validate events against registered schemas
- Support schema version evolution
- Track schema lineage for backward compatibility

Reference:
- CloudEvents spec v1.0.2: https://cloudevents.io/
- JSON Schema: https://json-schema.org/

Usage:
    from polaris.kernelone.audit.omniscient.schema_registry import get_schema_registry

    registry = get_schema_registry()
    registry.register(LLMEvent)
    is_valid = registry.validate(event)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.kernelone.audit.omniscient.schemas.base import AuditEvent
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="BaseModel")


@dataclass
class SchemaVersion:
    """Version information for a registered schema."""

    version: str
    schema_uri: str
    registered_at: float
    event_count: int = 0


@dataclass
class SchemaRegistration:
    """Registration record for an AuditEvent schema."""

    domain: str
    event_type: str
    latest_version: str
    versions: dict[str, SchemaVersion] = field(default_factory=dict)
    validator: Callable[[dict[str, Any]], bool] | None = None


class SchemaRegistry:
    """Registry for AuditEvent schemas with version tracking.

    Manages schema registration, validation, and version evolution
    for CloudEvents-aligned audit schemas.

    Attributes:
        DOMAINS: Set of valid event domains.
    """

    # Valid event domains per EventDomain enum
    DOMAINS: frozenset[str] = frozenset(
        [
            "llm",
            "tool",
            "dialogue",
            "context",
            "task",
            "system",
            "security",
        ]
    )

    def __init__(self) -> None:
        """Initialize the schema registry."""
        self._registrations: dict[str, SchemaRegistration] = {}
        self._schema_classes: dict[str, type[AuditEvent]] = {}
        self._lock = RLock()
        self._import_time = _get_import_time()

    def register(
        self,
        schema_class: type[AuditEvent],
        domain: str | None = None,
        event_type: str | None = None,
    ) -> None:
        """Register an AuditEvent schema.

        Args:
            schema_class: The AuditEvent subclass to register.
            domain: Override domain (defaults to schema's domain).
            event_type: Override event_type (defaults to schema's event_type).

        Raises:
            ValueError: If domain is not valid.
            TypeError: If schema_class is not a valid AuditEvent subclass.
        """
        import time

        from polaris.kernelone.audit.omniscient.schemas.base import AuditEvent

        if not issubclass(schema_class, AuditEvent):
            raise TypeError(f"Schema must be AuditEvent subclass, got {schema_class}")

        # Extract domain from Pydantic v2 model_fields (not class attribute)
        # In Pydantic v2, field defaults are stored in model_fields, not __dict__
        if domain is not None:
            domain_value = domain
        else:
            field_info = schema_class.model_fields.get("domain")
            if field_info is not None:
                raw_default = getattr(field_info, "default", None)
                if raw_default is not None and hasattr(raw_default, "value"):
                    # It's an enum like EventDomain.LLM
                    domain_value = raw_default.value
                elif isinstance(raw_default, str):
                    domain_value = raw_default
                else:
                    domain_value = ""
            else:
                domain_value = ""

        # Extract event_type from Pydantic model_fields
        if event_type is not None:
            schema_event_type = event_type
        else:
            event_field = schema_class.model_fields.get("event_type")
            if event_field is not None:
                ev_default = getattr(event_field, "default", None)
                schema_event_type = ev_default if isinstance(ev_default, str) else ""
            else:
                schema_event_type = ""

        if domain_value not in self.DOMAINS:
            logger.warning(f"Registering schema with non-standard domain: {domain_value}")

        # Get version from schema
        schema_version = getattr(schema_class, "version", "3.0")
        schema_uri = getattr(schema_class, "schema_uri", f"https://polaris.dev/schemas/audit/{schema_version}")

        key = self._make_key(domain_value, schema_event_type)

        with self._lock:
            if key in self._registrations:
                reg = self._registrations[key]
                if schema_version in reg.versions:
                    logger.debug(f"Schema {key} v{schema_version} already registered")
                    return
                reg.versions[schema_version] = SchemaVersion(
                    version=schema_version,
                    schema_uri=schema_uri,
                    registered_at=time.time(),
                )
                if self._compare_versions(schema_version, reg.latest_version) > 0:
                    reg.latest_version = schema_version
            else:
                self._registrations[key] = SchemaRegistration(
                    domain=domain_value,
                    event_type=schema_event_type,
                    latest_version=schema_version,
                    versions={
                        schema_version: SchemaVersion(
                            version=schema_version,
                            schema_uri=schema_uri,
                            registered_at=time.time(),
                        )
                    },
                )

            self._schema_classes[key] = schema_class
            logger.info(f"Registered schema: {key} v{schema_version}")

    def register_all_known_schemas(self) -> int:
        """Register all known AuditEvent schemas from the schemas module.

        Returns:
            Number of schemas registered.
        """
        from polaris.kernelone.audit.omniscient.schemas import (
            AuditEvent,
            ContextEvent,
            DialogueEvent,
            LLMEvent,
            TaskEvent,
            ToolEvent,
        )

        schemas_to_register: list[type[AuditEvent]] = [
            AuditEvent,
            LLMEvent,
            ToolEvent,
            DialogueEvent,
            ContextEvent,
            TaskEvent,
        ]

        for schema_class in schemas_to_register:
            try:
                self.register(schema_class)
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Failed to register {schema_class.__name__}: {e}")

        return len(schemas_to_register)

    def validate(self, event: AuditEvent | dict[str, Any], key: str | None = None) -> bool:
        """Validate an event against registered schema.

        Args:
            event: AuditEvent instance or dict to validate.
            key: Optional schema key (domain:event_type). If None, derives from event.

        Returns:
            True if valid or no schema registered, False if validation fails.
        """
        with self._lock:
            if key is None:
                if isinstance(event, dict):
                    domain = event.get("domain", "")
                    event_type = event.get("event_type", "")
                else:
                    domain = event.domain.value if hasattr(event.domain, "value") else str(event.domain)
                    event_type = event.event_type
                key = self._make_key(domain, event_type)

            if key not in self._registrations:
                logger.debug(f"No schema registered for {key}, skipping validation")
                return True

            # Get the appropriate schema class
            if key not in self._schema_classes:
                return True

            schema_class = self._schema_classes[key]

            # Validate using Pydantic model
            try:
                if isinstance(event, dict):
                    schema_class.model_validate(event)
                # Already validated as AuditEvent, ensure it's the right type
                elif not isinstance(event, schema_class):
                    logger.warning(f"Event type mismatch for {key}")
                    return False
                return True
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Schema validation failed for {key}: {e}")
                return False

    def get_schema(self, domain: str, event_type: str) -> type[AuditEvent] | None:
        """Get the registered schema class for a domain/event_type.

        Args:
            domain: Event domain.
            event_type: Event type.

        Returns:
            Registered schema class or None if not found.
        """
        key = self._make_key(domain, event_type)
        with self._lock:
            return self._schema_classes.get(key)

    def get_versions(self, domain: str, event_type: str) -> list[str]:
        """Get all registered versions for a schema.

        Args:
            domain: Event domain.
            event_type: Event type.

        Returns:
            List of version strings, sorted oldest to newest.
        """
        key = self._make_key(domain, event_type)
        with self._lock:
            if key not in self._registrations:
                return []
            reg = self._registrations[key]
            versions = list(reg.versions.keys())
            versions.sort(key=self._parse_version)
            return versions

    def get_latest_version(self, domain: str, event_type: str) -> str | None:
        """Get the latest version for a schema.

        Args:
            domain: Event domain.
            event_type: Event type.

        Returns:
            Latest version string or None if not registered.
        """
        key = self._make_key(domain, event_type)
        with self._lock:
            if key not in self._registrations:
                return None
            return self._registrations[key].latest_version

    def get_schema_uri(self, domain: str, event_type: str, version: str | None = None) -> str | None:
        """Get the schema URI for a specific version or latest.

        Args:
            domain: Event domain.
            event_type: Event type.
            version: Optional specific version. Defaults to latest.

        Returns:
            Schema URI string or None if not found.
        """
        key = self._make_key(domain, event_type)
        with self._lock:
            if key not in self._registrations:
                return None
            reg = self._registrations[key]
            if version is None:
                version = reg.latest_version
            if version not in reg.versions:
                return None
            return reg.versions[version].schema_uri

    def list_registered(self) -> list[dict[str, str]]:
        """List all registered schemas.

        Returns:
            List of dictionaries with domain, event_type, latest_version.
        """
        with self._lock:
            result = []
            for key, reg in self._registrations.items():
                domain, event_type = self._parse_key(key)
                result.append(
                    {
                        "domain": domain,
                        "event_type": event_type,
                        "latest_version": reg.latest_version,
                        "schema_uri": self.get_schema_uri(domain, event_type) or "",
                    }
                )
            return result

    def increment_event_count(
        self,
        domain: str,
        event_type: str,
        version: str | None = None,
    ) -> None:
        """Increment the event count for a schema version.

        Args:
            domain: Event domain.
            event_type: Event type.
            version: Optional specific version. Defaults to latest.
        """
        key = self._make_key(domain, event_type)
        with self._lock:
            if key not in self._registrations:
                return
            reg = self._registrations[key]
            if version is None:
                version = reg.latest_version
            if version in reg.versions:
                reg.versions[version].event_count += 1

    def _make_key(self, domain: str, event_type: str) -> str:
        """Create a unique key for domain/event_type pair.

        Args:
            domain: Event domain.
            event_type: Event type.

        Returns:
            Composite key string.
        """
        return f"{domain}:{event_type}"

    def _parse_key(self, key: str) -> tuple[str, str]:
        """Parse a composite key into domain and event_type.

        Args:
            key: Composite key string.

        Returns:
            Tuple of (domain, event_type).
        """
        parts = key.split(":", 1)
        return parts[0] if len(parts) > 0 else "", parts[1] if len(parts) > 1 else ""

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare two version strings.

        Args:
            v1: First version.
            v2: Second version.

        Returns:
            -1 if v1 < v2, 0 if equal, 1 if v1 > v2.
        """
        parsed_v1 = self._parse_version(v1)
        parsed_v2 = self._parse_version(v2)
        if parsed_v1 < parsed_v2:
            return -1
        if parsed_v1 > parsed_v2:
            return 1
        return 0

    @staticmethod
    def _parse_version(version: str) -> tuple[int, int, int]:
        """Parse a semantic version string.

        Args:
            version: Version string (e.g., "3.0" or "1.2.3").

        Returns:
            Tuple of (major, minor, patch).
        """
        parts = version.split(".")
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return (major, minor, patch)


def _get_import_time() -> float:
    """Get the import time for uptime tracking.

    Returns:
        Current timestamp.
    """
    import time

    return time.time()


# Global schema registry instance
_schema_registry: SchemaRegistry | None = None
_registry_lock = RLock()


def get_schema_registry() -> SchemaRegistry:
    """Get the global schema registry instance.

    Returns:
        The singleton SchemaRegistry instance.
    """
    global _schema_registry
    with _registry_lock:
        if _schema_registry is None:
            _schema_registry = SchemaRegistry()
            # Auto-register known schemas
            _schema_registry.register_all_known_schemas()
        return _schema_registry
