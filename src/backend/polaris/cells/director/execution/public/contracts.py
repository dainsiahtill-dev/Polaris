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


@dataclass(frozen=True)
class ExecuteDirectorTaskCommandV1:
    task_id: str
    workspace: str
    instruction: str
    run_id: str | None = None
    attempt: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "instruction", _require_non_empty("instruction", self.instruction))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")


@dataclass(frozen=True)
class RetryDirectorTaskCommandV1:
    task_id: str
    workspace: str
    reason: str
    max_attempts: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")


@dataclass(frozen=True)
class GetDirectorTaskStatusQueryV1:
    task_id: str
    workspace: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class DirectorTaskStartedEventV1:
    event_id: str
    task_id: str
    workspace: str
    started_at: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "started_at", _require_non_empty("started_at", self.started_at))


@dataclass(frozen=True)
class DirectorTaskCompletedEventV1:
    event_id: str
    task_id: str
    workspace: str
    status: str
    completed_at: str
    run_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class DirectorExecutionResultV1:
    ok: bool
    task_id: str
    workspace: str
    status: str
    run_id: str | None = None
    evidence_paths: tuple[str, ...] = field(default_factory=tuple)
    output_summary: str = ""
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "evidence_paths", tuple(str(v) for v in self.evidence_paths))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class DirectorExecutionError(RuntimeError):
    """Raised when `director.execution` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "director_execution_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "DirectorExecutionError",
    "DirectorExecutionResultV1",
    "DirectorTaskCompletedEventV1",
    "DirectorTaskStartedEventV1",
    "ExecuteDirectorTaskCommandV1",
    "GetDirectorTaskStatusQueryV1",
    "RetryDirectorTaskCommandV1",
]
