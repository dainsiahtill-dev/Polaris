"""Immutable configuration snapshot with source tracking.

This module provides ConfigSnapshot, an immutable, hashable configuration
representation that tracks the source of each configuration value.

Example:
    >>> from config_snapshot import ConfigSnapshot, SourceType
    >>>
    >>> # Create from merged sources
    >>> snapshot = ConfigSnapshot.merge_sources(
    ...     default={"pm.backend": "auto", "pm.timeout": 300},
    ...     persisted={"pm.backend": "embedded"},
    ...     env={"pm.timeout": "600"},
    ...     cli={"pm.backend": "test"}
    ... )
    >>>
    >>> # Query values
    >>> snapshot.get("pm.backend")
    "test"
    >>> snapshot.get_source("pm.backend")
    SourceType.CLI
    >>>
    >>> # Functional update (returns new instance)
    >>> new_snapshot = snapshot.with_override({"pm.timeout": 900}, SourceType.CLI)
    >>> snapshot.get("pm.timeout")  # Original unchanged
    600
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


class SourceType(IntEnum):
    """Configuration source priority (lower = lower priority).

    Priority order: DEFAULT < PERSISTED < ENV < CLI
    """

    DEFAULT = 0
    PERSISTED = 1
    ENV = 2
    CLI = 3

    def __str__(self) -> str:
        return self.name.lower()


class ConfigSnapshotImmutableError(TypeError):
    """Raised when attempting to modify a frozen ConfigSnapshot.

    Note: Inherits from TypeError because this represents a programming error
    (attempting to assign to an immutable object's attribute), consistent with
    Python's dataclasses.FrozenInstanceError which also inherits from TypeError.
    """

    pass


# Backward compatibility alias
FrozenInstanceError = ConfigSnapshotImmutableError


@dataclass(frozen=True)
class ConfigValidationResult:
    """Result of validating a configuration snapshot.

    Attributes:
        is_valid: Whether the configuration is valid
        errors: List of error messages
        warnings: List of warning messages
    """

    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> ConfigValidationResult:
        """Create new result with added error (immutable update)."""
        return ConfigValidationResult(
            is_valid=False,
            errors=[*self.errors, message],
            warnings=self.warnings,
        )

    def add_warning(self, message: str) -> ConfigValidationResult:
        """Create new result with added warning (immutable update)."""
        return ConfigValidationResult(
            is_valid=self.is_valid,
            errors=self.errors,
            warnings=[*self.warnings, message],
        )

    def merge(self, other: ConfigValidationResult) -> ConfigValidationResult:
        """Merge another validation result into this one."""
        return ConfigValidationResult(
            is_valid=self.is_valid and other.is_valid,
            errors=self.errors + other.errors,
            warnings=self.warnings + other.warnings,
        )


@dataclass(frozen=True)
class ConfigSnapshot:
    """Immutable configuration snapshot with source tracking.

    This class represents a point-in-time, immutable view of the configuration
    after merging all sources according to the priority:
    default < persisted < env < cli

    Once created, it cannot be modified - any changes create a new snapshot.

    Attributes:
        _config: Internal immutable mapping of configuration values
        _sources: Internal mapping tracking the source of each key
        _timestamp: When this snapshot was created

    Example:
        >>> snapshot = ConfigSnapshot.merge_sources(
        ...     default={"server.host": "127.0.0.1", "server.port": 8080}
        ... )
        >>> snapshot.get("server.host")
        "127.0.0.1"
        >>> snapshot.with_override({"server.port": 9000}, SourceType.CLI)
        ConfigSnapshot(...)  # New instance with updated port
    """

    _config: MappingProxyType = field(repr=False)
    _sources: MappingProxyType = field(repr=False)
    _timestamp: datetime = field(default_factory=datetime.now)
    _hash: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Calculate hash for fast equality comparison and caching
        object.__setattr__(
            self,
            "_hash",
            hash(json.dumps(self._to_hashable(), sort_keys=True, default=str)),
        )

    def _to_hashable(self) -> tuple[tuple[str, Any], ...]:
        """Convert config to hashable format."""
        return tuple(sorted(self._flatten_dict(self._config)))

    def _flatten_dict(self, d: dict[str, Any] | MappingProxyType, parent_key: str = "") -> list[tuple[str, Any]]:
        """Flatten nested dict to list of (key, value) tuples."""
        items: list[tuple[str, Any]] = []
        for k, v in d.items():
            new_key = f"{parent_key}.{k}" if parent_key else k
            if isinstance(v, (dict, MappingProxyType)):
                items.extend(self._flatten_dict(v, new_key))
            else:
                items.append((new_key, v))
        return items

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (dot notation supported).

        Args:
            key: Configuration key, supports dot notation (e.g., 'server.port')
            default: Default value if key not found

        Returns:
            Configuration value or default

        Example:
            >>> snapshot.get('server.port')
            8080
            >>> snapshot.get('missing.key', 'default')
            'default'
        """
        keys = key.split(".")
        value: Any = self._config
        for k in keys:
            if isinstance(value, (dict, MappingProxyType)) and k in value:
                value = value[k]
            else:
                return default
        return value

    def get_typed(self, key: str, type_: type, default: Any = None) -> Any:
        """Get configuration value with type conversion.

        Args:
            key: Configuration key
            type_: Expected type
            default: Default value if key not found or type mismatch

        Returns:
            Configuration value converted to type_, or default
        """
        value = self.get(key, default)
        if value is None:
            return default
        try:
            if type_ == bool and isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return type_(value)
        except (ValueError, TypeError):
            return default

    def get_section(self, section: str) -> Mapping[str, Any]:
        """Get a configuration section as immutable mapping.

        Args:
            section: Section name (top-level key)

        Returns:
            Immutable mapping of section contents

        Raises:
            KeyError: If section does not exist
        """
        if section not in self._config:
            raise KeyError(f"Configuration section not found: {section}")
        section_data = self._config[section]
        if not isinstance(section_data, dict):
            raise KeyError(f"Configuration key is not a section: {section}")
        return MappingProxyType(dict(section_data))

    def has(self, key: str) -> bool:
        """Check if configuration key exists.

        Args:
            key: Configuration key (dot notation supported)

        Returns:
            True if key exists, False otherwise
        """
        keys = key.split(".")
        value: Any = self._config
        for k in keys:
            if isinstance(value, (dict, MappingProxyType)) and k in value:
                value = value[k]
            else:
                return False
        return True

    def get_source(self, key: str) -> SourceType | None:
        """Get the source type for a specific key.

        Args:
            key: Configuration key (dot notation supported)

        Returns:
            SourceType if tracked, None otherwise
        """
        # For nested keys, we track at the leaf level
        return self._sources.get(key)

    def get_all_sources(self) -> Mapping[str, SourceType]:
        """Get mapping of all keys to their sources."""
        return MappingProxyType(dict(self._sources))

    def to_mutable_dict(self) -> dict[str, Any]:
        """Convert to mutable dictionary (creates deep copy).

        Returns:
            Deep copy of configuration as mutable dict
        """
        return deepcopy(dict(self._config))

    def with_override(self, overrides: dict[str, Any], source: SourceType) -> ConfigSnapshot:
        """Create new snapshot with overrides applied.

        This is a functional update - the original snapshot is unchanged.

        Args:
            overrides: Dictionary of key-value pairs to override
            source: Source type for the overrides

        Returns:
            New ConfigSnapshot with merged values
        """
        # Create deep copy of current config
        new_config = self.to_mutable_dict()
        new_sources = dict(self._sources)

        # Apply overrides (higher priority wins)
        for key, value in overrides.items():
            self._set_nested(new_config, key, value)
            new_sources[key] = source

        return ConfigSnapshot(
            _config=MappingProxyType(new_config),
            _sources=MappingProxyType(new_sources),
        )

    def with_defaults(self, defaults: dict[str, Any]) -> ConfigSnapshot:
        """Create new snapshot with defaults merged (lower priority).

        Only applies defaults for keys that don't already exist.

        Args:
            defaults: Default values to apply

        Returns:
            New ConfigSnapshot with defaults filled in
        """
        new_config = self.to_mutable_dict()
        new_sources = dict(self._sources)

        for key, value in defaults.items():
            if not self.has(key):
                self._set_nested(new_config, key, value)
                new_sources[key] = SourceType.DEFAULT

        return ConfigSnapshot(
            _config=MappingProxyType(new_config),
            _sources=MappingProxyType(new_sources),
        )

    def _set_nested(self, config: dict[str, Any], key: str, value: Any) -> None:
        """Set a nested configuration value using dot notation."""
        keys = key.split(".")
        current = config
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value

    def validate(self, schema: dict[str, Any] | None = None) -> ConfigValidationResult:
        """Validate configuration against schema.

        Args:
            schema: Optional validation schema

        Returns:
            ConfigValidationResult with validation status
        """
        result = ConfigValidationResult()

        # Basic validation
        port = self.get("server.port")
        if port is not None:
            try:
                port_int = int(port)
                if not (0 <= port_int <= 65535):
                    result = result.add_error(f"Invalid port number: {port}")
            except (ValueError, TypeError):
                result = result.add_error(f"Port must be a number: {port}")

        log_level = self.get("logging.level")
        if log_level is not None:
            valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
            if str(log_level).upper() not in valid_levels:
                result = result.add_warning(f"Unusual log level: {log_level}")

        # Add more validation rules as needed

        return result

    def diff(self, other: ConfigSnapshot) -> dict[str, tuple[Any, Any]]:
        """Compute difference between two snapshots.

        Args:
            other: Another ConfigSnapshot to compare with

        Returns:
            Dictionary of {key: (self_value, other_value)} for differing keys
        """
        differences: dict[str, tuple[Any, Any]] = {}

        all_keys = set(self._flatten_dict(self._config)) | set(other._flatten_dict(other._config))

        for key_tuple in all_keys:
            key = key_tuple[0] if isinstance(key_tuple, tuple) else key_tuple
            self_value = self.get(key)
            other_value = other.get(key)
            if self_value != other_value:
                differences[key] = (self_value, other_value)

        return differences

    def __hash__(self) -> int:
        return self._hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConfigSnapshot):
            return NotImplemented
        return self._hash == other._hash and self._config == other._config

    def __repr__(self) -> str:
        return f"ConfigSnapshot(keys={len(list(self._flatten_dict(self._config)))}, timestamp={self._timestamp.isoformat()})"

    def to_json(self, indent: int | None = None) -> str:
        """Serialize configuration to JSON string."""
        return json.dumps(self.to_mutable_dict(), indent=indent, default=str)

    @classmethod
    def merge_sources(
        cls,
        default: dict[str, Any] | None = None,
        persisted: dict[str, Any] | None = None,
        env: dict[str, Any] | None = None,
        cli: dict[str, Any] | None = None,
    ) -> ConfigSnapshot:
        """Factory method to create snapshot from multiple sources.

        Merges sources according to priority:
        default < persisted < env < cli

        Args:
            default: Default configuration values
            persisted: Persisted configuration (e.g., from file)
            env: Environment variable configuration
            cli: Command-line argument configuration

        Returns:
            New ConfigSnapshot with merged configuration
        """
        config: dict[str, Any] = {}
        sources: dict[str, SourceType] = {}

        # Apply in priority order (lower first, higher override)
        for source_type, source_data in [
            (SourceType.DEFAULT, default or {}),
            (SourceType.PERSISTED, persisted or {}),
            (SourceType.ENV, env or {}),
            (SourceType.CLI, cli or {}),
        ]:
            for key, value in source_data.items():
                cls._apply_value(config, sources, key, value, source_type)

        return cls(
            _config=MappingProxyType(config),
            _sources=MappingProxyType(sources),
        )

    @staticmethod
    def _apply_value(
        config: dict[str, Any],
        sources: dict[str, SourceType],
        key: str,
        value: Any,
        source: SourceType,
    ) -> None:
        """Apply a value to config, tracking its source."""
        keys = key.split(".")
        current = config
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
        sources[key] = source

    @classmethod
    def from_flat_dict(cls, data: dict[str, Any], source: SourceType = SourceType.DEFAULT) -> ConfigSnapshot:
        """Create snapshot from flat dictionary with dot-notation keys.

        Args:
            data: Flat dictionary with dot-notation keys
            source: Source type for all values

        Returns:
            New ConfigSnapshot
        """
        config: dict[str, Any] = {}
        sources: dict[str, SourceType] = {}

        for key, value in data.items():
            cls._apply_value(config, sources, key, value, source)

        return cls(
            _config=MappingProxyType(config),
            _sources=MappingProxyType(sources),
        )

    @classmethod
    def empty(cls) -> ConfigSnapshot:
        """Create empty snapshot."""
        return cls(
            _config=MappingProxyType({}),
            _sources=MappingProxyType({}),
        )


def merge_priority_test() -> None:
    """Test that merge priority is correctly applied."""
    snapshot = ConfigSnapshot.merge_sources(
        default={"key": "default", "only_default": "value"},
        persisted={"key": "persisted", "only_persisted": "value"},
        env={"key": "env", "only_env": "value"},
        cli={"key": "cli", "only_cli": "value"},
    )

    assert snapshot.get("key") == "cli", "CLI should have highest priority"
    assert snapshot.get("only_default") == "value"
    assert snapshot.get("only_persisted") == "value"
    assert snapshot.get("only_env") == "value"
    assert snapshot.get("only_cli") == "value"

    # Check source tracking
    assert snapshot.get_source("key") == SourceType.CLI
    assert snapshot.get_source("only_default") == SourceType.DEFAULT
    assert snapshot.get_source("only_persisted") == SourceType.PERSISTED
    assert snapshot.get_source("only_env") == SourceType.ENV

    logger.info("Priority test passed!")


if __name__ == "__main__":
    merge_priority_test()
