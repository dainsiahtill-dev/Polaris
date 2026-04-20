"""Public contracts for director.tasking cell.

These contracts define the stable public interface for task lifecycle management,
worker pool orchestration, and task execution within the Director system.

All symbols here should be imported by external consumers (Facade, other Cells).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateTaskCommandV1:
    """Command to create a new task in the Director tasking system."""

    subject: str
    workspace: str
    description: str = ""
    command: str | None = None
    priority: str = "medium"
    blocked_by: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _require_non_empty("subject", self.subject))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "description", str(self.description))
        object.__setattr__(self, "blocked_by", list(self.blocked_by or []))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class CancelTaskCommandV1:
    """Command to cancel a pending or ready task."""

    task_id: str
    workspace: str
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskStatusQueryV1:
    """Query the status of one or more tasks."""

    workspace: str
    task_id: str | None = None
    status: str | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class TaskResultQueryV1:
    """Query the result of a completed task."""

    task_id: str
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskCreatedResultV1:
    """Result of a task creation command."""

    ok: bool
    task_id: str
    workspace: str
    subject: str
    status: str = "pending"
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "subject", _require_non_empty("subject", self.subject))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


@dataclass(frozen=True)
class TaskStatusResultV1:
    """Result of a task status query."""

    ok: bool
    workspace: str
    tasks: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class TaskResultResultV1:
    """Result of a task result query."""

    ok: bool
    task_id: str
    workspace: str
    success: bool | None = None
    output: str = ""
    error: str | None = None
    duration_ms: int | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DirectorTaskingError(RuntimeError):
    """Raised when director.tasking contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "director_tasking_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "CancelTaskCommandV1",
    "CreateTaskCommandV1",
    "DirectorTaskingError",
    "TaskCreatedResultV1",
    "TaskResultQueryV1",
    "TaskResultResultV1",
    "TaskStatusQueryV1",
    "TaskStatusResultV1",
]
