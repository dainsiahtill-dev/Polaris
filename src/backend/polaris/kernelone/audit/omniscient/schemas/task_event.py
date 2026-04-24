"""TaskEvent — Pydantic schema for task orchestration audit.

Captures task lifecycle:
- State transitions
- Assignment and claiming
- Deadlock detection
- Timeout warnings
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Self

from polaris.kernelone.audit.omniscient.schemas.base import (
    AuditEvent,
    AuditPriority,
    EventDomain,
)
from pydantic import ConfigDict, Field


class TaskState(str, Enum):
    """Task state in orchestration lifecycle."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TaskEvent(AuditEvent):  # type: ignore[call-arg]  # frozen=True inherited from AuditEvent model_config; mypy flags redundant kwarg
    """Task orchestration audit event.

    Tracks task lifecycle for:
    - Deadlock detection
    - Timeout analysis
    - Workload distribution
    - DAG validation

    Attributes:
        task_id: Task identifier.
        task_name: Human-readable task name.
        state: Current task state.
        previous_state: Previous state for transition tracking.
        assigned_role: Role assigned to task.
        claim_time_ms: Time to claim task in ms.
        execution_time_ms: Time spent executing.
        retry_count: Number of retries.
        max_retries: Maximum retries allowed.
        deadline: Task deadline (ISO timestamp).
        timeout_warning: Whether timeout warning was issued.
        deadlock_detected: Whether deadlock was detected.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    domain: EventDomain = Field(default=EventDomain.TASK)
    event_type: str = Field(default="task_orchestration")

    task_id: str = Field(default="", max_length=64)
    task_name: str = Field(default="", max_length=256)
    state: TaskState = Field(default=TaskState.PENDING)
    previous_state: TaskState | None = Field(default=None)
    assigned_role: str = Field(default="", max_length=32)
    claim_time_ms: float = Field(default=0.0, ge=0.0)
    execution_time_ms: float = Field(default=0.0, ge=0.0)
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=0, ge=0)
    deadline: str = Field(default="", max_length=32)
    timeout_warning: bool = Field(default=False)
    deadlock_detected: bool = Field(default=False)

    def to_audit_dict(self) -> dict[str, Any]:
        base = super().to_audit_dict()
        base.update(
            {
                "task_id": self.task_id,
                "task_name": self.task_name,
                "state": self.state.value,
                "previous_state": self.previous_state.value if self.previous_state else None,
                "assigned_role": self.assigned_role,
                "claim_time_ms": self.claim_time_ms,
                "execution_time_ms": self.execution_time_ms,
                "retry_count": self.retry_count,
                "max_retries": self.max_retries,
                "deadline": self.deadline,
                "timeout_warning": self.timeout_warning,
                "deadlock_detected": self.deadlock_detected,
            }
        )
        return base

    @classmethod
    def from_audit_dict(cls, data: dict[str, Any]) -> Self:
        state = data.get("state", "pending")
        if isinstance(state, str):
            state = TaskState(state.lower())

        prev_state = data.get("previous_state")
        if prev_state and isinstance(prev_state, str):
            try:
                prev_state = TaskState(prev_state.lower())
            except ValueError:
                prev_state = None

        return cls(
            event_id=data.get("event_id", ""),
            version=data.get("version", "3.0"),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            trace_id=data.get("trace_id", ""),
            run_id=data.get("run_id", ""),
            span_id=data.get("span_id", ""),
            parent_span_id=data.get("parent_span_id", ""),
            priority=AuditPriority[data.get("priority", "info").upper()],
            workspace=data.get("workspace", ""),
            role=data.get("role", ""),
            task_id=data.get("task_id", ""),
            task_name=data.get("task_name", ""),
            state=state,
            previous_state=prev_state,
            assigned_role=data.get("assigned_role", ""),
            claim_time_ms=data.get("claim_time_ms", 0.0),
            execution_time_ms=data.get("execution_time_ms", 0.0),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 0),
            deadline=data.get("deadline", ""),
            timeout_warning=data.get("timeout_warning", False),
            deadlock_detected=data.get("deadlock_detected", False),
            data=data.get("data", {}),
            correlation_context=data.get("correlation_context", {}),
        )

    @classmethod
    def create(
        cls,
        task_id: str,
        state: TaskState,
        task_name: str = "",
        previous_state: TaskState | None = None,
        role: str = "",
        workspace: str = "",
        trace_id: str = "",
        run_id: str = "",
        **kwargs: Any,
    ) -> Self:
        return cls(
            task_id=task_id,
            task_name=task_name,
            state=state,
            previous_state=previous_state,
            role=role,
            workspace=workspace,
            trace_id=trace_id,
            run_id=run_id,
            **kwargs,
        )
