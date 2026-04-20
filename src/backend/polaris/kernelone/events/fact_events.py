"""Unified fact event emission for KernelOne.

This module provides emit_fact_event() for emitting fact events to the audit chain.
Fact events represent immutable factual records about system operations.

Usage:
    from polaris.kernelone.events.fact_events import emit_fact_event

    emit_fact_event(
        workspace="/path/to/workspace",
        event_name="file_created",
        payload={"path": "/path/to/file", "size": 1024},
    )
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.events.io_events import emit_event

logger = logging.getLogger(__name__)


def emit_fact_event(
    workspace: str,
    event_name: str,
    payload: dict[str, Any],
    *,
    actor: str = "System",
    run_id: str = "",
    refs: dict[str, Any] | None = None,
) -> None:
    """Emit a fact event to the audit chain.

    Fact events represent immutable factual records about system operations.
    They are always written to disk for audit purposes.

    Args:
        workspace: The workspace path
        event_name: The name of the fact event (e.g., "file_created", "config_changed")
        payload: The event payload containing factual data
        actor: The actor that triggered the event (default: "System")
        run_id: Optional run identifier for correlation
        refs: Optional reference data (e.g., session_id, task_id)
    """
    event_path = _resolve_fact_event_path(workspace)

    emit_event(
        event_path=event_path,
        kind="observation",
        actor=actor,
        name=event_name,
        refs=refs or {},
        summary=f"Fact: {event_name}",
        output=payload,
        ok=True,
    )


def _resolve_fact_event_path(workspace: str) -> str:
    """Resolve the fact event log path for a workspace.

    Args:
        workspace: The workspace path

    Returns:
        The logical path for fact events
    """
    return f"runtime/events/{workspace}/facts"


__all__ = ["emit_fact_event"]
