"""Pydantic v2 domain models for the Task entity.

This module provides validated DTO-style Task models for API, JSON, and
schema-generation boundaries. The mutable runtime aggregate remains
``polaris.domain.entities.task.Task``.

Key capabilities:
    - Automatic JSON Schema generation for API documentation
    - Built-in validation with ``model_validator`` and ``field_validator``
    - ``model_validate()`` / ``model_dump()`` for explicit serialization
    - ``strict=False`` allows coercion from JSON (str→enum, float→int)
    - Frozen value objects prevent accidental mutation

Boundary guide:
    1. Replace ``Task(**data)`` with ``TaskModel.model_validate(data)``
    2. Replace ``task.to_dict()`` with ``task.model_dump()``
    3. Replace ``Task.from_dict(data)`` with ``TaskModel.model_validate(data)``
    4. Enum fields auto-coerce from strings via ``model_config = {"use_enum_values": True}``
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from polaris.kernelone.utils import utc_now
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Canonical enums
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """Polaris task lifecycle states."""

    QUEUED = "queued"
    PENDING = "pending"
    READY = "ready"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    RUNNING = "in_progress"  # backward compat alias
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    WAITING_HUMAN = "waiting_human"

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        }

    @property
    def is_active(self) -> bool:
        return self in {
            TaskStatus.QUEUED,
            TaskStatus.PENDING,
            TaskStatus.READY,
            TaskStatus.CLAIMED,
            TaskStatus.BLOCKED,
            TaskStatus.WAITING_HUMAN,
        }

    @property
    def is_executing(self) -> bool:
        return self in {TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS}


class TaskPriority(str, Enum):
    """Polaris task priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def numeric_value(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(self.value, 1)


# ---------------------------------------------------------------------------
# Value objects (frozen)
# ---------------------------------------------------------------------------


class TaskEvidenceModel(BaseModel):
    """Evidence of task execution (file reference, test result, log, etc.)."""

    model_config = {"frozen": True}

    type: str = Field(..., description="Evidence type (file, test_result, log)")
    path: str | None = Field(default=None, description="File path")
    content: str | None = Field(default=None, description="Inline content")
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Evidence metadata")


class TaskResultModel(BaseModel):
    """Outcome of a task execution attempt."""

    model_config = {"frozen": True}

    success: bool = Field(..., description="Whether execution succeeded")
    output: str = Field(default="", description="Execution output")
    exit_code: int = Field(default=0, description="Process exit code")
    duration_ms: int = Field(default=0, description="Execution duration in ms")
    evidence: list[TaskEvidenceModel] = Field(default_factory=list, description="Execution evidence")
    error: str | None = Field(default=None, description="Error message if failed")


# ---------------------------------------------------------------------------
# Task entity
# ---------------------------------------------------------------------------


class TaskModel(BaseModel):
    """Polaris Task domain entity (Pydantic v2).

    Provides full validation, serialization, and JSON Schema support for
    external data boundaries.

    Usage::

        # Create from dict (e.g. API input, JSON file)
        task = TaskModel.model_validate({"id": 1, "subject": "Fix bug"})

        # Serialize to dict
        data = task.model_dump()

        # JSON roundtrip
        json_str = task.model_dump_json()
        task2 = TaskModel.model_validate_json(json_str)
    """

    model_config = {
        "use_enum_values": True,
        "validate_assignment": True,
        "populate_by_name": True,
    }

    # Identity
    id: int | str = Field(..., description="Task identifier")
    subject: str = Field(..., min_length=1, description="Task subject / title")
    description: str = Field(default="", description="Task description")

    # Lifecycle
    status: str = Field(default=TaskStatus.PENDING.value, description="Task lifecycle status")
    priority: str = Field(default=TaskPriority.MEDIUM.value, description="Task priority")

    # DAG dependencies
    blocked_by: list[int | str] = Field(default_factory=list, description="Blocking dependency IDs")
    blocks: list[int | str] = Field(default_factory=list, description="Tasks blocked by this task")

    # Assignment
    owner: str = Field(default="", description="Task owner")
    assignee: str = Field(default="", description="Task assignee")
    claimed_by: str | None = Field(default=None, description="Worker that claimed this task")

    # PM planning
    role: str = Field(default="", description="PM role assignment")
    constraints: list[str] = Field(default_factory=list, description="Task constraints")
    acceptance_criteria: list[str] = Field(default_factory=list, description="Acceptance criteria")

    # Execution config
    command: str | None = Field(default=None, description="Shell command to execute")
    working_directory: str | None = Field(default=None, description="Working directory")
    timeout_seconds: int = Field(
        default=DEFAULT_OPERATION_TIMEOUT_SECONDS,
        ge=1,
        description="Execution timeout",
    )
    max_retries: int = Field(default=3, ge=0, description="Maximum retry attempts")
    retry_count: int = Field(default=0, ge=0, description="Current retry count")

    # Timestamps (unix epoch seconds)
    created_at: float = Field(
        default_factory=lambda: utc_now().timestamp(),
        description="Creation timestamp",
    )
    started_at: float | None = Field(default=None, description="Start timestamp")
    completed_at: float | None = Field(default=None, description="Completion timestamp")
    claimed_at: float | None = Field(default=None, description="Claim timestamp")

    # Result
    result_summary: str = Field(default="", description="Brief result summary")
    error_message: str | None = Field(default=None, description="Error message")
    evidence_refs: list[str] = Field(default_factory=list, description="Evidence file paths")
    _result: TaskResultModel | None = None

    # Annotation
    tags: list[str] = Field(default_factory=list, description="Task tags")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")

    # -- validators ---------------------------------------------------------

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, v: str | TaskStatus) -> str:
        if isinstance(v, TaskStatus):
            return v.value
        return str(v or "pending").strip().lower()

    @field_validator("priority", mode="before")
    @classmethod
    def coerce_priority(cls, v: str | TaskPriority) -> str:
        if isinstance(v, TaskPriority):
            return v.value
        return str(v or "medium").strip().lower()

    @field_validator("id", mode="before")
    @classmethod
    def coerce_id(cls, v: Any) -> int | str:
        if isinstance(v, (int, str)):
            return v
        if isinstance(v, float):
            return int(v)
        return str(v)

    # -- computed properties ------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return TaskStatus(self.status).is_terminal

    @property
    def is_blocked(self) -> bool:
        return len(self.blocked_by) > 0 and self.status in (TaskStatus.PENDING.value, TaskStatus.BLOCKED.value)

    @property
    def is_claimable(self) -> bool:
        return (
            self.status == TaskStatus.READY.value
            and not self.claimed_by
            and not any(b for b in self.blocked_by if b != 0)
        )

    @property
    def result(self) -> TaskResultModel | None:
        return self._result

    # -- state transitions (mutating methods) -------------------------------

    def mark_ready(self) -> None:
        if self.status != TaskStatus.PENDING.value:
            raise ValueError(f"Cannot mark_ready from {self.status!r}")
        self.status = TaskStatus.READY.value

    def claim(self, worker_id: str) -> None:
        if not self.is_claimable:
            raise ValueError(f"Cannot claim task in status {self.status!r}")
        self.status = TaskStatus.CLAIMED.value
        self.claimed_by = worker_id
        self.claimed_at = time.time()

    def start(self) -> None:
        if self.status != TaskStatus.CLAIMED.value:
            raise ValueError(f"Cannot start from {self.status!r}; task must be CLAIMED first")
        self.status = TaskStatus.IN_PROGRESS.value
        if self.started_at is None:
            self.started_at = time.time()

    def complete(self, result: TaskResultModel) -> None:
        if self.status not in (
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.CLAIMED.value,
        ):
            raise ValueError(f"Cannot complete from {self.status!r}")

        self._result = result

        if not result.success and self.retry_count < self.max_retries:
            self.status = TaskStatus.READY.value
            self.retry_count += 1
            self.claimed_by = None
            self.claimed_at = None
            self.started_at = None
            self._result = None
        else:
            self.status = TaskStatus.COMPLETED.value if result.success else TaskStatus.FAILED.value
            self.completed_at = time.time()
            self.result_summary = result.output
            if result.evidence:
                for ev in result.evidence:
                    if ev.path:
                        self.evidence_refs.append(ev.path)
            if result.error:
                self.error_message = result.error

    def cancel(self) -> None:
        if TaskStatus(self.status).is_terminal:
            raise ValueError("Cannot cancel a terminal task")
        self.status = TaskStatus.CANCELLED.value
        self.completed_at = time.time()

    def timeout_task(self) -> None:
        if TaskStatus(self.status).is_terminal:
            raise ValueError("Cannot timeout a terminal task")
        self.status = TaskStatus.TIMEOUT.value
        self.completed_at = time.time()
        self.error_message = "Execution exceeded timeout limit"

    def reopen(self) -> None:
        if not TaskStatus(self.status).is_terminal:
            raise ValueError(f"Cannot reopen non-terminal task: {self.status!r}")
        self.status = TaskStatus.BLOCKED.value if self.blocked_by else TaskStatus.PENDING.value
        self.claimed_by = None
        self.claimed_at = None
        self.started_at = None
        self.completed_at = None
        self._result = None

    def resolve_dependency(self, dep_id: int | str) -> None:
        if dep_id in self.blocked_by:
            self.blocked_by.remove(dep_id)


__all__ = [
    "TaskEvidenceModel",
    "TaskModel",
    "TaskPriority",
    "TaskResultModel",
    "TaskStatus",
]
