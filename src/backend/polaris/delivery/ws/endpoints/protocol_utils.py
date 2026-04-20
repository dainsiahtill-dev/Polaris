"""Protocol utility functions for runtime WebSocket endpoint.

This module contains:
- v2 Protocol Channel Mapping
- Workspace key resolution
"""

from __future__ import annotations

import logging
import os

from polaris.delivery.ws.endpoints.models import V2_CHANNEL_TO_SUBJECT

logger = logging.getLogger(__name__)


# =============================================================================
# v2 Protocol Channel Mapping Helpers
# =============================================================================


def resolve_v2_subject(workspace_key: str, channel: str) -> str:
    """Resolve logical channel to JetStream subject.

    Args:
        workspace_key: Workspace identifier.
        channel: Logical channel name (e.g., "log.llm").

    Returns:
        Full JetStream subject path.
    """
    base = V2_CHANNEL_TO_SUBJECT.get(channel, channel)
    return f"hp.runtime.{workspace_key}.{base}"


def build_v2_subscription_subjects(workspace_key: str, channels: list[str]) -> list[str]:
    """Build list of JetStream subjects for subscription.

    Args:
        workspace_key: Workspace identifier.
        channels: List of logical channel names.

    Returns:
        List of JetStream subjects to subscribe to.
    """
    subjects: set[str] = set()
    for ch in channels:
        if ch in {"*", "all"}:
            # Subscribe to all channels for this workspace
            subjects.add(f"hp.runtime.{workspace_key}.>")
        else:
            subjects.add(resolve_v2_subject(workspace_key, ch))
    return list(subjects)


def resolve_runtime_v2_workspace_key(
    *,
    connection_workspace: str,
    requested_workspace: str = "",
) -> str:
    """Resolve the canonical workspace_key for runtime.v2 JetStream subjects.

    The connection workspace is already validated/resolved during websocket open.
    Client SUBSCRIBE payloads are advisory only and may contain a display name
    rather than the canonical hashed workspace key. Always bind JetStream
    consumers to the connection-scoped workspace context to avoid subject drift.
    """
    from polaris.cells.runtime.projection.public.service import (
        DEFAULT_WORKSPACE,
        resolve_workspace_runtime_context,
    )

    preferred_workspace = str(connection_workspace or "").strip()
    advisory_workspace = str(requested_workspace or "").strip()
    try:
        context = resolve_workspace_runtime_context(
            configured_workspace=preferred_workspace,
            default_workspace=preferred_workspace or advisory_workspace or DEFAULT_WORKSPACE,
        )
        return str(context.workspace_key or "").strip() or "default"
    except (RuntimeError, ValueError) as exc:
        logger.debug("resolve_workspace_runtime_context failed: %s", exc)
        fallback = preferred_workspace or advisory_workspace
        if fallback:
            return os.path.basename(fallback.rstrip("/\\")) or "default"
        return "default"


__all__ = [
    "build_v2_subscription_subjects",
    "resolve_runtime_v2_workspace_key",
    "resolve_v2_subject",
]
