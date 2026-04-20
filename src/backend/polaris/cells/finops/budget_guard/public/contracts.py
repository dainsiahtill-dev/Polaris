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
class ReserveBudgetCommandV1:
    scope_id: str
    workspace: str
    role: str
    token_budget: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", _require_non_empty("scope_id", self.scope_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.token_budget < 0:
            raise ValueError("token_budget must be >= 0")


@dataclass(frozen=True)
class RecordUsageCommandV1:
    scope_id: str
    workspace: str
    role: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", _require_non_empty("scope_id", self.scope_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.prompt_tokens < 0 or self.completion_tokens < 0:
            raise ValueError("tokens must be >= 0")
        if self.cost_usd < 0:
            raise ValueError("cost_usd must be >= 0")


@dataclass(frozen=True)
class GetBudgetStatusQueryV1:
    scope_id: str
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", _require_non_empty("scope_id", self.scope_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class BudgetThresholdExceededEventV1:
    event_id: str
    scope_id: str
    role: str
    threshold: float
    observed: float
    occurred_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "scope_id", _require_non_empty("scope_id", self.scope_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "occurred_at", _require_non_empty("occurred_at", self.occurred_at))


@dataclass(frozen=True)
class BudgetDecisionResultV1:
    allowed: bool
    scope_id: str
    role: str
    remaining_tokens: int
    estimated_cost_usd: float
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", _require_non_empty("scope_id", self.scope_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        if self.remaining_tokens < 0:
            raise ValueError("remaining_tokens must be >= 0")
        if self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be >= 0")


class FinOpsBudgetError(RuntimeError):
    """Raised when `finops.budget_guard` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "finops_budget_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "BudgetDecisionResultV1",
    "BudgetThresholdExceededEventV1",
    "FinOpsBudgetError",
    "GetBudgetStatusQueryV1",
    "RecordUsageCommandV1",
    "ReserveBudgetCommandV1",
]
