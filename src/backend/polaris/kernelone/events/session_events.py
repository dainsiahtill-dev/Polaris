"""Unified session event emission for KernelOne.

This module provides emit_session_event() for emitting session lifecycle events.
Session events track the lifecycle of user/role sessions.

Usage:
    from polaris.kernelone.events.session_events import emit_session_event

    emit_session_event(
        workspace="/path/to/workspace",
        event_name="session_created",
        session_id="sess_123",
        payload={"role": "pm", "host_kind": "cli"},
    )
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.events.io_events import emit_event

logger = logging.getLogger(__name__)


def emit_session_event(
    workspace: str,
    event_name: str,
    session_id: str,
    payload: dict[str, Any],
    *,
    actor: str = "System",
    run_id: str = "",
) -> None:
    """Emit a session event to the audit chain.

    Session events track the lifecycle of user/role sessions including:
    - session_created: Session was created
    - session_updated: Session state was updated
    - session_ended: Session ended normally
    - session_expired: Session expired due to TTL
    - session_message_added: A message was added to the session

    Args:
        workspace: The workspace path
        event_name: The name of the session event
        session_id: The session identifier
        payload: The event payload containing session data
        actor: The actor that triggered the event (default: "System")
        run_id: Optional run identifier for correlation
    """
    event_path = _resolve_session_event_path(workspace)

    emit_event(
        event_path=event_path,
        kind="action",
        actor=actor,
        name=event_name,
        refs={"session_id": session_id},
        summary=f"Session {event_name}: {session_id}",
        input=payload,
    )


def _resolve_session_event_path(workspace: str) -> str:
    """Resolve the session event log path for a workspace.

    Args:
        workspace: The workspace path

    Returns:
        The logical path for session events
    """
    return "runtime/sessions/events"


__all__ = ["emit_session_event"]
