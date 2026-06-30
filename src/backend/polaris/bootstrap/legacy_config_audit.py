"""Audit support for accepted legacy bootstrap configuration aliases."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass
from typing import Final

logger = logging.getLogger(__name__)

LEGACY_CONFIG_AUDIT_SCHEMA_VERSION: Final = "polaris.legacy_config_migration_event.v1"
LEGACY_CONFIG_SUNSET_POLICY_VERSION: Final = "legacy-config-sunset.v1"
LEGACY_CONFIG_SUNSET_NOT_BEFORE: Final = "2026-12-31"
_MAX_RECORDED_EVENTS: Final = 256


@dataclass(frozen=True, slots=True)
class LegacyConfigMigrationEvent:
    """A bounded in-process record for a legacy config alias migration."""

    schema_version: str
    sunset_policy_version: str
    sunset_not_before: str
    source: str
    legacy_key: str
    canonical_key: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable audit payload."""
        return asdict(self)


_events: deque[LegacyConfigMigrationEvent] = deque(maxlen=_MAX_RECORDED_EVENTS)


def record_legacy_config_migration(*, source: str, legacy_key: str, canonical_key: str) -> LegacyConfigMigrationEvent:
    """Record and log a legacy-to-canonical config key migration.

    The compatibility path is intentionally retained for existing user settings,
    but every use must leave a machine-readable breadcrumb so the remaining debt
    can be measured before the sunset policy is enforced.
    """
    event = LegacyConfigMigrationEvent(
        schema_version=LEGACY_CONFIG_AUDIT_SCHEMA_VERSION,
        sunset_policy_version=LEGACY_CONFIG_SUNSET_POLICY_VERSION,
        sunset_not_before=LEGACY_CONFIG_SUNSET_NOT_BEFORE,
        source=source,
        legacy_key=legacy_key,
        canonical_key=canonical_key,
    )
    _events.append(event)
    logger.warning("legacy_config_key_migrated %s", event.to_dict())
    return event


def get_legacy_config_migration_events() -> list[LegacyConfigMigrationEvent]:
    """Return a snapshot of recently observed legacy config migrations."""
    return list(_events)


def clear_legacy_config_migration_events() -> None:
    """Clear recorded migration events for deterministic tests."""
    _events.clear()
