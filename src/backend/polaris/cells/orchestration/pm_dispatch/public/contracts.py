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
class DispatchPmTasksCommandV1:
    run_id: str
    workspace: str
    dispatcher: str
    task_ids: tuple[str, ...] = field(default_factory=tuple)
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "dispatcher", _require_non_empty("dispatcher", self.dispatcher))
        object.__setattr__(self, "task_ids", tuple(str(v) for v in self.task_ids if str(v).strip()))
        object.__setattr__(self, "options", _to_dict_copy(self.options))


@dataclass(frozen=True)
class ResumePmIterationCommandV1:
    run_id: str
    workspace: str
    iteration_id: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "iteration_id", _require_non_empty("iteration_id", self.iteration_id))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))


@dataclass(frozen=True)
class GetPmDispatchStatusQueryV1:
    run_id: str
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class PmTaskDispatchedEventV1:
    event_id: str
    run_id: str
    task_id: str
    dispatched_to: str
    dispatched_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "dispatched_to", _require_non_empty("dispatched_to", self.dispatched_to))
        object.__setattr__(self, "dispatched_at", _require_non_empty("dispatched_at", self.dispatched_at))


@dataclass(frozen=True)
class PmIterationAdvancedEventV1:
    event_id: str
    run_id: str
    iteration_id: str
    status: str
    advanced_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "iteration_id", _require_non_empty("iteration_id", self.iteration_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "advanced_at", _require_non_empty("advanced_at", self.advanced_at))


@dataclass(frozen=True)
class PmDispatchResultV1:
    ok: bool
    run_id: str
    status: str
    dispatched_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        if self.dispatched_count < 0 or self.skipped_count < 0 or self.failed_count < 0:
            raise ValueError("dispatch counters must be >= 0")


class PmDispatchError(RuntimeError):
    """Raised when `orchestration.pm_dispatch` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "pm_dispatch_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "DispatchPmTasksCommandV1",
    "GetPmDispatchStatusQueryV1",
    "PmDispatchError",
    "PmDispatchResultV1",
    "PmIterationAdvancedEventV1",
    "PmTaskDispatchedEventV1",
    "ResumePmIterationCommandV1",
]
