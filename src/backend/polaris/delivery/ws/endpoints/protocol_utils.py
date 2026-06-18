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
            continue
        if ch == "event.bench:all" or ch == "event.bench":
            # Workspace-agnostic bench stream. The factory-bench subprocess
            # publishes to ``hp.runtime.bench.<session_id>`` regardless of
            # workspace, so a single subscription here lets the front-end
            # observe every active bench session through the same WebSocket
            # that already carries log.llm / log.process / etc.
            subjects.add("hp.runtime.bench.>")
            continue
        if ch.startswith("event.bench:"):
            # Pin a specific bench session: ``event.bench:<session_id>`` maps
            # to ``hp.runtime.bench.<session_id>`` (workspace-agnostic, since
            # the bench spans L1-L8 workspaces).
            session_id = ch[len("event.bench:") :].strip()
            if session_id and _is_safe_subject_token(session_id):
                subjects.add(f"hp.runtime.bench.{session_id}")
            continue
        if ch == "event.factory:all" or ch == "event.factory":
            # Workspace-scoped wildcard: the user's WebSocket is bound to
            # ``workspace_key`` at connect time, so this subject only fans
            # in factory events for the current workspace. The factory SSE
            # pattern is replaced by the same NAT JetStream + WebSocket
            # transport the rest of the platform uses.
            subjects.add(f"hp.runtime.{workspace_key}.event.factory.>")
            continue
        if ch.startswith("event.factory:"):
            # Pin a specific factory run: ``event.factory:<run_id>`` maps to
            # the same per-workspace subject the legacy SSE consumer used to
            # subscribe to (``hp.runtime.<workspace_key>.event.factory.<run_id>``).
            run_id = ch[len("event.factory:") :].strip()
            if run_id and _is_safe_subject_token(run_id):
                subjects.add(f"hp.runtime.{workspace_key}.event.factory.{run_id}")
            continue
        if ch == "chat:all" or ch == "chat":
            # Workspace-agnostic role-chat stream. The role-chat jetstream
            # publisher publishes to ``hp.runtime.chat.<session_id>``
            # regardless of workspace, so a single subscription here lets the
            # front-end observe every active chat session through the same
            # WebSocket that already carries log.llm / event.bench / etc.
            # This is the chat-streaming replacement for the legacy
            # ``/v2/role/{role}/chat/stream`` SSE endpoint.
            subjects.add("hp.runtime.chat.>")
            continue
        if ch.startswith("chat:"):
            # Pin a specific chat session: ``chat:<session_id>`` maps to
            # ``hp.runtime.chat.<session_id>`` (workspace-agnostic, mirroring
            # the bench pattern that the factory panel already uses).
            session_id = ch[len("chat:") :].strip()
            if session_id and _is_safe_subject_token(session_id):
                subjects.add(f"hp.runtime.chat.{session_id}")
            continue
        subjects.add(resolve_v2_subject(workspace_key, ch))
    return list(subjects)


def _is_safe_subject_token(token: str) -> bool:
    """Allow only the same character class V2 subjects use (defence in depth).

    Bench session ids are produced server-side via ``f"bench-{int(time.time())}-{uuid.uuid4().hex[:6]}"``,
    so this filter is belt-and-braces against a malicious client crafting
    ``event.bench:../../../foo`` to escape the ``hp.runtime.bench.`` subject.
    """
    import re

    return bool(re.match(r"^[A-Za-z0-9_-]{1,64}$", token))


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
