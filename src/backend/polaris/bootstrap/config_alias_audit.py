"""Audit support for accepted bootstrap configuration aliases."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass
from typing import Final

logger = logging.getLogger(__name__)

CONFIG_ALIAS_AUDIT_SCHEMA_VERSION: Final = "polaris.config_alias_migration_event.v1"
CONFIG_ALIAS_SUNSET_POLICY_VERSION: Final = "config-alias-sunset.v1"
CONFIG_ALIAS_SUNSET_NOT_BEFORE: Final = "2026-12-31"
_MAX_RECORDED_EVENTS: Final = 256


@dataclass(frozen=True, slots=True)
class ConfigAliasMigrationEvent:
    """A bounded in-process record for a config alias migration."""

    schema_version: str
    sunset_policy_version: str
    sunset_not_before: str
    source: str
    source_key: str
    canonical_key: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable audit payload."""
        return asdict(self)


_events: deque[ConfigAliasMigrationEvent] = deque(maxlen=_MAX_RECORDED_EVENTS)


def record_config_alias_migration(
    *,
    source: str,
    source_key: str,
    canonical_key: str,
) -> ConfigAliasMigrationEvent:
    """Record and log a source-to-canonical config key migration.

    Accepted flat or historical config keys are intentionally retained for
    existing user settings, but every use must leave a machine-readable
    breadcrumb so the remaining alias surface can be measured before the
    sunset policy is enforced.
    """
    event = ConfigAliasMigrationEvent(
        schema_version=CONFIG_ALIAS_AUDIT_SCHEMA_VERSION,
        sunset_policy_version=CONFIG_ALIAS_SUNSET_POLICY_VERSION,
        sunset_not_before=CONFIG_ALIAS_SUNSET_NOT_BEFORE,
        source=source,
        source_key=source_key,
        canonical_key=canonical_key,
    )
    _events.append(event)
    logger.warning("config_alias_key_migrated %s", event.to_dict())
    return event


def get_config_alias_migration_events() -> list[ConfigAliasMigrationEvent]:
    """Return a snapshot of recently observed config alias migrations."""
    return list(_events)


def clear_config_alias_migration_events() -> None:
    """Clear recorded migration events for deterministic tests."""
    _events.clear()
