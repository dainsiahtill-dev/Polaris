"""Public contracts for `llm.evaluation` cell."""

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
class RunLlmEvaluationCommandV1:
    workspace: str
    provider_id: str
    model: str
    role: str = "default"
    suites: tuple[str, ...] = field(default_factory=tuple)
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "suites", tuple(str(v).strip() for v in self.suites if str(v).strip()))
        object.__setattr__(self, "options", _to_dict_copy(self.options))


@dataclass(frozen=True)
class QueryLlmEvaluationIndexV1:
    workspace: str
    provider_id: str | None = None
    model: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.provider_id is not None:
            object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        if self.model is not None:
            object.__setattr__(self, "model", _require_non_empty("model", self.model))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class LlmEvaluationCompletedEventV1:
    event_id: str
    workspace: str
    run_id: str
    provider_id: str
    model: str
    role: str
    grade: str
    completed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "grade", _require_non_empty("grade", self.grade))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class LlmEvaluationResultV1:
    ok: bool
    status: str
    workspace: str
    run_id: str
    summary: Mapping[str, Any] = field(default_factory=dict)
    suites: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "suites", tuple(dict(item) for item in self.suites))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class LlmEvaluationError(RuntimeError):
    """Structured contract error for `llm.evaluation`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_evaluation_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class ILlmEvaluationService(Protocol):
    async def run_evaluation(self, command: RunLlmEvaluationCommandV1) -> LlmEvaluationResultV1:
        """Run evaluation suite(s)."""

    async def query_index(self, query: QueryLlmEvaluationIndexV1) -> Mapping[str, Any]:
        """Query persisted evaluation index."""


__all__ = [
    "ILlmEvaluationService",
    "LlmEvaluationCompletedEventV1",
    "LlmEvaluationError",
    "LlmEvaluationResultV1",
    "QueryLlmEvaluationIndexV1",
    "RunLlmEvaluationCommandV1",
]
