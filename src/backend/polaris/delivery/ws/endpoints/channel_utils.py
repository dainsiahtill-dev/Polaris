"""Channel utility functions for runtime WebSocket endpoint.

This module contains:
- Channel classification helpers
- Channel path resolution
- Channel configuration constants
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


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
        roles: Comma-separated roles string (e.g., "pm,director,qa").

    Returns:
        Set of normalized role tokens.
    """
    if not roles:
        return set()
    normalized: set[str] = set()
    for raw in str(roles).split(","):
        token = raw.strip().lower()
        if token in {"pm", "director", "qa"}:
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

    rel = CHANNEL_FILES.get(channel)
    if not rel:
        return ""
    return resolve_artifact_path(workspace, cache_root, rel)


__all__ = [
    "channel_max_chars",
    "is_llm_channel",
    "is_process_channel",
    "normalize_roles",
    "resolve_channel_path",
    "resolve_current_run_id",
    "wants_role",
]
