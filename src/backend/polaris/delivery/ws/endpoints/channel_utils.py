"""Channel utility functions for runtime WebSocket endpoint.

This module contains:
- Channel classification helpers
- Channel path resolution
- Channel configuration constants
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
    """Check if channel is an LLM stream channel.

    Args:
        channel: Channel name.

    Returns:
        True if LLM channel.
    """
    return channel == "llm" or channel.endswith("_llm")


def is_process_channel(channel: str) -> bool:
    """Check if channel is a process/console stream channel.

    Args:
        channel: Channel name.

    Returns:
        True if process channel.
    """
    return channel in {
        "system",
        "process",
        "pm_subprocess",
        "director_console",
        "pm_report",
        "pm_log",
        "ollama",
        "qa",
        "runlog",
        "planner",
        "engine_status",
    }


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


# =============================================================================
# File Path Helpers
# =============================================================================


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


def resolve_channel_path(workspace: str, cache_root: str, channel: str) -> str:
    """Resolve file path for a channel.

    Args:
        workspace: Workspace path.
        cache_root: Runtime cache root.
        channel: Channel name.

    Returns:
        Absolute file path for the channel, empty if not found.
    """
    from polaris.cells.runtime.projection.public.service import (
        CHANNEL_FILES,
        resolve_artifact_path,
    )

    if channel in {"system", "process", "llm"}:
        run_id = resolve_current_run_id(cache_root)
        if not run_id:
            return ""
        return os.path.join(cache_root, "runs", run_id, "logs", "journal.norm.jsonl")

    if channel == "runtime_events":
        # Context / runtime observation events (``context.build`` /
        # ``prompt_context`` / ``context.snapshot``) are emitted to the
        # *per-run* events file (``runs/<run_id>/events/runtime.events.jsonl``);
        # the workspace-level ``CHANNEL_FILES`` path is only a fallback for
        # legacy writers. Resolve the active run first so the realtime ContextOS
        # dashboard tails the events the live run actually produces, instead of a
        # stale workspace-level file the live emit path never writes.
        run_id = resolve_current_run_id(cache_root)
        if run_id:
            per_run = os.path.join(cache_root, "runs", run_id, "events", "runtime.events.jsonl")
            if os.path.isfile(per_run):
                return per_run

    rel = CHANNEL_FILES.get(channel)
    if not rel:
        return ""
    return resolve_artifact_path(workspace, cache_root, rel)


__all__ = [
    "CONSUMER_ROLE_TOKENS",
    "RUNTIME_OBSERVABLE_ROLE_TOKENS",
    "channel_max_chars",
    "is_llm_channel",
    "is_process_channel",
    "normalize_roles",
    "resolve_channel_path",
    "resolve_current_run_id",
    "wants_role",
]
