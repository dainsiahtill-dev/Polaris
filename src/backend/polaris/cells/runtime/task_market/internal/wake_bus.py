"""In-process wake signals for task-market consumers.

The task market is persisted in durable storage, but the local CE/Director/QA
daemon threads do not need timer-driven scans. Mutating service operations set
these per-workspace events after commit; consumers clear-before-claim and then
block until the next commit signal.
"""

from __future__ import annotations

import threading

_ROLES = ("chief_engineer", "director", "qa")
_guard = threading.Lock()
_work_events: dict[tuple[str, str], threading.Event] = {}
_outbox_events: dict[str, threading.Event] = {}


def _workspace_token(workspace: str) -> str:
    token = str(workspace or "").strip()
    if not token:
        raise ValueError("workspace must be a non-empty string")
    return token


def get_task_market_work_event(workspace: str, role: str) -> threading.Event:
    """Return the event used to wake a role consumer for a workspace."""

    workspace_token = _workspace_token(workspace)
    role_token = str(role or "").strip().lower()
    if not role_token:
        raise ValueError("role must be a non-empty string")
    key = (workspace_token, role_token)
    with _guard:
        event = _work_events.get(key)
        if event is None:
            event = threading.Event()
            _work_events[key] = event
        return event


def get_task_market_outbox_event(workspace: str) -> threading.Event:
    """Return the event used to wake the outbox relay for a workspace."""

    workspace_token = _workspace_token(workspace)
    with _guard:
        event = _outbox_events.get(workspace_token)
        if event is None:
            event = threading.Event()
            _outbox_events[workspace_token] = event
        return event


def notify_task_market_workspace(workspace: str) -> None:
    """Wake all local task-market workers and the outbox relay for a workspace."""

    workspace_token = _workspace_token(workspace)
    with _guard:
        for role in _ROLES:
            event = _work_events.get((workspace_token, role))
            if event is not None:
                event.set()
        outbox_event = _outbox_events.get(workspace_token)
        if outbox_event is not None:
            outbox_event.set()


def notify_task_market_outbox(workspace: str) -> None:
    """Wake the local outbox relay after a direct outbox append."""

    workspace_token = _workspace_token(workspace)
    with _guard:
        outbox_event = _outbox_events.get(workspace_token)
        if outbox_event is not None:
            outbox_event.set()


__all__ = [
    "get_task_market_outbox_event",
    "get_task_market_work_event",
    "notify_task_market_outbox",
    "notify_task_market_workspace",
]
