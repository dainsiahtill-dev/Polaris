"""Stable service exports for finops.budget_guard."""

from __future__ import annotations

from ..internal.budget_agent import CFOAgent
from .contracts import (
    BudgetDecisionResultV1,
    BudgetThresholdExceededEventV1,
    FinOpsBudgetError,
    GetBudgetStatusQueryV1,
    RecordUsageCommandV1,
    ReserveBudgetCommandV1,
)


def reserve_budget(command: ReserveBudgetCommandV1) -> BudgetDecisionResultV1:
    """Reserve a token budget through the FinOps public command contract."""
    if not isinstance(command, ReserveBudgetCommandV1):
        raise TypeError("command must be a ReserveBudgetCommandV1")
    agent = CFOAgent(command.workspace)
    result = agent._tool_allocate_budget(
        task_id=command.scope_id,
        budget_type=str(command.metadata.get("budget_type") or "context_tokens"),
        limit=command.token_budget,
        unit=str(command.metadata.get("unit") or "tokens"),
    )
    allowed = bool(result.get("ok"))
    return BudgetDecisionResultV1(
        allowed=allowed,
        scope_id=command.scope_id,
        role=command.role,
        remaining_tokens=command.token_budget if allowed else 0,
        estimated_cost_usd=float(command.metadata.get("estimated_cost_usd") or 0.0),
        reason="reserved" if allowed else str(result.get("error") or "budget reservation failed"),
    )


__all__ = [
    "BudgetDecisionResultV1",
    "BudgetThresholdExceededEventV1",
    "CFOAgent",
    "FinOpsBudgetError",
    "GetBudgetStatusQueryV1",
    "RecordUsageCommandV1",
    "ReserveBudgetCommandV1",
    "reserve_budget",
]
