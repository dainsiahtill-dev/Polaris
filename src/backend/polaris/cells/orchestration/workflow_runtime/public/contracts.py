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
class StartWorkflowCommandV1:
    workflow_id: str
    workspace: str
    workflow_type: str
    input_payload: Mapping[str, Any] = field(default_factory=dict)
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _require_non_empty("workflow_id", self.workflow_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "workflow_type", _require_non_empty("workflow_type", self.workflow_type))
        object.__setattr__(self, "input_payload", _to_dict_copy(self.input_payload))


@dataclass(frozen=True)
class CancelWorkflowCommandV1:
    workflow_id: str
    workspace: str
    reason: str
    requested_by: str = "system"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _require_non_empty("workflow_id", self.workflow_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "requested_by", _require_non_empty("requested_by", self.requested_by))


@dataclass(frozen=True)
class QueryWorkflowStatusV1:
    workflow_id: str
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _require_non_empty("workflow_id", self.workflow_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class QueryWorkflowEventsV1:
    workflow_id: str
    workspace: str
    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _require_non_empty("workflow_id", self.workflow_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")


@dataclass(frozen=True)
class WorkflowExecutionStartedEventV1:
    event_id: str
    workflow_id: str
    workspace: str
    started_at: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workflow_id", _require_non_empty("workflow_id", self.workflow_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "started_at", _require_non_empty("started_at", self.started_at))


@dataclass(frozen=True)
class WorkflowExecutionCompletedEventV1:
    event_id: str
    workflow_id: str
    workspace: str
    status: str
    completed_at: str
    run_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workflow_id", _require_non_empty("workflow_id", self.workflow_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class WorkflowExecutionResultV1:
    ok: bool
    workflow_id: str
    workspace: str
    status: str
    current_step: str | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _require_non_empty("workflow_id", self.workflow_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "metrics", _to_dict_copy(self.metrics))


class WorkflowRuntimeError(RuntimeError):
    """Raised when `orchestration.workflow_runtime` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "workflow_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "CancelWorkflowCommandV1",
    "QueryWorkflowEventsV1",
    "QueryWorkflowStatusV1",
    "StartWorkflowCommandV1",
    "WorkflowExecutionCompletedEventV1",
    "WorkflowExecutionResultV1",
    "WorkflowExecutionStartedEventV1",
    "WorkflowRuntimeError",
]
