"""Task step-trace event domain model.

Migrated from:
  polaris/cells/orchestration/workflow_runtime/internal/task_trace.py

ACGA 2.0: This module is Cell-local and must NOT be imported by other Cells
without going through the public contract.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class TaskTraceEvent:
    """Task trace event."""

    event_id: str
    run_id: str
    role: str  # pm|director|qa|architect|chief_engineer
    task_id: str
    seq: int
    phase: str  # planning|analyzing|executing|verify|report|completed|failed
    step_kind: str  # phase|llm|tool|validation|retry|system
    step_title: str
    step_detail: str
    status: str  # started|running|completed|failed|skipped
    attempt: int = 0
    visibility: str = "summary"  # summary|debug
    ts: str = ""
    refs: dict = field(default_factory=dict)


class TaskTraceBuilder:
    """Task trace event builder."""

    def __init__(self, run_id: str, role: str, task_id: str) -> None:
        self._run_id = run_id
        self._role = role
        self._task_id = task_id
        self._seq = 0

    def build(
        self,
        phase: str,
        step_kind: str,
        step_title: str,
        step_detail: str,
        status: str,
        attempt: int = 0,
        visibility: str = "summary",
        **refs,
    ) -> TaskTraceEvent:
        """Build a task trace event."""
        self._seq += 1
        return TaskTraceEvent(
            event_id=str(uuid.uuid4()),
            run_id=self._run_id,
            role=self._role,
            task_id=self._task_id,
            seq=self._seq,
            phase=phase,
            step_kind=step_kind,
            step_title=step_title,
            step_detail=_sanitize_step_detail(step_detail),
            status=status,
            attempt=attempt,
            visibility=visibility,
            ts=datetime.now(timezone.utc).isoformat(),
            refs=refs,
        )

    def to_ws_payload(self, event: TaskTraceEvent) -> dict:
        """Convert to WebSocket payload format."""
        return {
            "type": "task_trace",
            "event": {
                "event_id": event.event_id,
                "run_id": event.run_id,
                "role": event.role,
                "task_id": event.task_id,
                "seq": event.seq,
                "phase": event.phase,
                "step_kind": event.step_kind,
                "step_title": event.step_title,
                "step_detail": event.step_detail,
                "status": event.status,
                "attempt": event.attempt,
                "visibility": event.visibility,
                "ts": event.ts,
                "refs": event.refs,
            },
        }


def _sanitize_step_detail(detail: str, max_length: int = 280) -> str:
    """Sanitize step detail, enforce UTF-8 and truncate."""
    if not detail:
        return ""
    try:
        detail = detail.encode("utf-8", errors="ignore").decode("utf-8")
    except (UnicodeError, AttributeError):
        detail = str(detail)
    # Mask sensitive tokens
    detail = re.sub(r"[a-zA-Z0-9]{32,}", "[MASKED]", detail)
    detail = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[MASKED]", detail)
    # Truncate
    if len(detail) > max_length:
        detail = detail[: max_length - 3] + "..."
    return detail
