"""Public contracts for `resident.autonomy`."""

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
class RunResidentCycleCommandV1:
    workspace: str
    cycle_id: str
    goal: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "goal", _require_non_empty("goal", self.goal))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class RecordResidentEvidenceCommandV1:
    workspace: str
    cycle_id: str
    evidence_kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "evidence_kind", _require_non_empty("evidence_kind", self.evidence_kind))
        payload = _to_dict_copy(self.payload)
        if not payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True)
class QueryResidentStatusV1:
    workspace: str
    cycle_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.cycle_id is not None:
            object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))


@dataclass(frozen=True)
class ResidentCycleCompletedEventV1:
    event_id: str
    workspace: str
    cycle_id: str
    status: str
    completed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class ResidentAutonomyResultV1:
    ok: bool
    workspace: str
    cycle_id: str
    status: str
    actions: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "actions", tuple(str(v) for v in self.actions if str(v).strip()))
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(str(v) for v in self.evidence_refs if str(v).strip()),
        )
        object.__setattr__(self, "metrics", _to_dict_copy(self.metrics))


class ResidentAutonomyError(RuntimeError):
    """Raised when `resident.autonomy` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "resident_autonomy_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "QueryResidentStatusV1",
    "RecordResidentEvidenceCommandV1",
    "ResidentAutonomyError",
    "ResidentAutonomyResultV1",
    "ResidentCycleCompletedEventV1",
    "RunResidentCycleCommandV1",
]
