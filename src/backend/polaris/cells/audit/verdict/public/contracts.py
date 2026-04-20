"""Public contracts for `audit.verdict` cell."""

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
class RunAuditVerdictCommandV1:
    workspace: str
    run_id: str
    task_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))


@dataclass(frozen=True)
class QueryAuditVerdictV1:
    workspace: str
    run_id: str | None = None
    task_id: str | None = None
    include_artifacts: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))


@dataclass(frozen=True)
class AuditVerdictIssuedEventV1:
    event_id: str
    workspace: str
    run_id: str
    verdict: str
    issued_at: str
    task_id: str | None = None
    review_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "verdict", _require_non_empty("verdict", self.verdict))
        object.__setattr__(self, "issued_at", _require_non_empty("issued_at", self.issued_at))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        if self.review_id is not None:
            object.__setattr__(self, "review_id", _require_non_empty("review_id", self.review_id))


@dataclass(frozen=True)
class AuditVerdictResultV1:
    ok: bool
    status: str
    workspace: str
    run_id: str
    verdict: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "details", _to_dict_copy(self.details))
        if self.verdict is not None:
            object.__setattr__(self, "verdict", _require_non_empty("verdict", self.verdict))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class AuditVerdictError(RuntimeError):
    """Structured contract error for `audit.verdict`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "audit_verdict_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IAuditVerdictService(Protocol):
    async def run_verdict(self, command: RunAuditVerdictCommandV1) -> AuditVerdictResultV1:
        """Run one verdict flow."""

    async def query_verdict(self, query: QueryAuditVerdictV1) -> AuditVerdictResultV1:
        """Query verdict state/details."""


__all__ = [
    "AuditVerdictError",
    "AuditVerdictIssuedEventV1",
    "AuditVerdictResultV1",
    "IAuditVerdictService",
    "QueryAuditVerdictV1",
    "RunAuditVerdictCommandV1",
]
