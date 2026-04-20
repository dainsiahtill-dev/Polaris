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
class GeneratePmTaskContractCommandV1:
    run_id: str
    workspace: str
    directive: str
    task_count_hint: int = 0
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "directive", _require_non_empty("directive", self.directive))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        if self.task_count_hint < 0:
            raise ValueError("task_count_hint must be >= 0")


@dataclass(frozen=True)
class GetPmPlanningStatusQueryV1:
    run_id: str
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class PmTaskContractGeneratedEventV1:
    event_id: str
    run_id: str
    workspace: str
    contract_path: str
    generated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "contract_path", _require_non_empty("contract_path", self.contract_path))
        object.__setattr__(self, "generated_at", _require_non_empty("generated_at", self.generated_at))


@dataclass(frozen=True)
class PmTaskContractResultV1:
    ok: bool
    run_id: str
    workspace: str
    status: str
    contract_ids: tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "contract_ids", tuple(str(v) for v in self.contract_ids if str(v).strip()))


class PmPlanningError(RuntimeError):
    """Raised when `orchestration.pm_planning` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "pm_planning_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "GeneratePmTaskContractCommandV1",
    "GetPmPlanningStatusQueryV1",
    "PmPlanningError",
    "PmTaskContractGeneratedEventV1",
    "PmTaskContractResultV1",
]
