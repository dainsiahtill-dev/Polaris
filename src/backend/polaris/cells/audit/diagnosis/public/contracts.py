"""Public contracts for `audit.diagnosis` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

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
class RunAuditDiagnosisCommandV1:
    workspace: str
    command: str
    args: Mapping[str, Any] = field(default_factory=dict)
    cache_root: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "command", _require_non_empty("command", self.command))
        object.__setattr__(self, "args", _to_dict_copy(self.args))
        if self.cache_root is not None:
            object.__setattr__(self, "cache_root", _require_non_empty("cache_root", self.cache_root))


@dataclass(frozen=True)
class QueryAuditDiagnosisTrailV1:
    workspace: str
    run_id: str | None = None
    task_id: str | None = None
    limit: int = 200

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


@dataclass(frozen=True)
class AuditDiagnosisCompletedEventV1:
    event_id: str
    workspace: str
    command: str
    status: str
    completed_at: str
    run_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "command", _require_non_empty("command", self.command))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))


@dataclass(frozen=True)
class AuditDiagnosisResultV1:
    ok: bool
    status: str
    workspace: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class AuditDiagnosisError(RuntimeError):
    """Structured contract error for `audit.diagnosis`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "audit_diagnosis_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IAuditDiagnosisService(Protocol):
    async def run_diagnosis(self, command: RunAuditDiagnosisCommandV1) -> AuditDiagnosisResultV1:
        """Execute one diagnosis command."""

    async def query_trail(self, query: QueryAuditDiagnosisTrailV1) -> AuditDiagnosisResultV1:
        """Query diagnosis events/trail."""


__all__ = [
    "AuditDiagnosisCompletedEventV1",
    "AuditDiagnosisError",
    "AuditDiagnosisResultV1",
    "IAuditDiagnosisService",
    "QueryAuditDiagnosisTrailV1",
    "RunAuditDiagnosisCommandV1",
]
