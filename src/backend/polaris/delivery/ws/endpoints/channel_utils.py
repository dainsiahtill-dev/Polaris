"""Channel utility functions for runtime WebSocket endpoint.

This module contains:
- Canonical runtime.v2 channel classification helpers
- Current-run ID resolution
- Role filter normalization
"""

from __future__ import annotations

import logging
import os

from polaris.kernelone.constants import RoleId

logger = logging.getLogger(__name__)

#: Valid role tokens accepted in runtime observability filters.
#: This is broader than TaskMarket consumers and intentionally includes
#: ``resident_agi`` for status/audit visibility.
RUNTIME_OBSERVABLE_ROLE_TOKENS: frozenset[str] = frozenset(role.value for role in RoleId.runtime_observable_roles())
# Backward-compatible export for older imports. Do not use for new code; this
# no longer means "TaskMarket consumer".
CONSUMER_ROLE_TOKENS: frozenset[str] = RUNTIME_OBSERVABLE_ROLE_TOKENS


# =============================================================================
# Channel Classification Helpers
# =============================================================================


def is_llm_channel(channel: str) -> bool:
    """Return whether ``channel`` is the canonical runtime.v2 LLM channel.

    Args:
        channel: Channel name.

    Returns:
        True only for the canonical ``llm`` channel.
    """
    return channel == "llm"


def is_process_channel(channel: str) -> bool:
    """Return whether ``channel`` is a canonical runtime.v2 process channel.

    Args:
        channel: Channel name.

    Returns:
        True for canonical journal channels used by runtime.v2.
    """
    return channel in {"system", "process"}


def channel_max_chars(channel: str) -> int:
    """Get max character limit for channel content.

    Args:
        channel: Channel name.

    Returns:
        Character limit for the channel.
    """
    return 500000 if is_llm_channel(channel) else 20000


def wants_role(roles: set[str], role: str) -> bool:
    """Check if a role should be included based on filter set.

    Args:
        roles: Role filter set. Empty set means all roles.
        role: Role to check.

    Returns:
        True if role should be included.
    """
    return not roles or role in roles


def normalize_roles(roles: str | None) -> set[str]:
    """Normalize comma-separated roles string to a set of role tokens.

    Args:
        roles: Comma-separated roles string (e.g., "pm,director,resident_agi").

    Returns:
        Set of normalized role tokens.
    """
    if not roles:
        return set()
    normalized: set[str] = set()
    for raw in str(roles).split(","):
        token = raw.strip().lower()
        if token in RUNTIME_OBSERVABLE_ROLE_TOKENS:
            normalized.add(token)
    return normalized


def resolve_current_run_id(cache_root: str) -> str:
    """Resolve the current run ID from latest_run.json.

    Args:
        cache_root: Runtime cache root directory.

    Returns:
        Current run ID string, empty if not found.
    """
    from polaris.cells.runtime.projection.public.service import read_json

    latest_file = os.path.join(cache_root, "latest_run.json")
    if not os.path.isfile(latest_file):
        return ""
    try:
        payload = read_json(latest_file)
    except (RuntimeError, ValueError) as exc:
        logger.debug("read_json latest_run.json failed: %s", exc)
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("run_id") or "").strip()


__all__ = [
    "CONSUMER_ROLE_TOKENS",
    "RUNTIME_OBSERVABLE_ROLE_TOKENS",
    "channel_max_chars",
    "is_llm_channel",
    "is_process_channel",
    "normalize_roles",
    "resolve_current_run_id",
    "wants_role",
]
