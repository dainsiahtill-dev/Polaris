"""Deterministic budget operations owned by the KFS budget state service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .budget_store import BudgetKFSStore, BudgetRecord, UsageRecord

if TYPE_CHECKING:
    from polaris.kernelone.fs import KernelFileSystem


class BudgetService:
    """Apply budget operations through the existing global BudgetKFSStore."""

    def __init__(self, workspace: str, *, fs: KernelFileSystem | None = None) -> None:
        self._store = BudgetKFSStore(workspace, scope_id="global", fs=fs)

    def _next_budget_id(self, prefix: str, task_id: str) -> str:
        task_token = str(task_id or "task").strip().replace(" ", "_")
        return f"{prefix}_{task_token}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    def set_global_budget(self, limit: int, unit: str = "tokens") -> dict[str, Any]:
        budget = BudgetRecord(
            budget_id=self._next_budget_id("global", "global"),
            task_id="global",
            budget_type="global",
            limit=max(0, int(limit)),
            unit=str(unit or "tokens").strip() or "tokens",
        )
        self._store.save_budget(budget)
        return {"ok": True, "budget": budget.to_dict()}

    def allocate_budget(
        self,
        task_id: str,
        budget_type: str,
        limit: int,
        unit: str = "tokens",
    ) -> dict[str, Any]:
        task_token = str(task_id or "").strip()
        if not task_token:
            return {"ok": False, "error": "task_id_required"}
        budget = BudgetRecord(
            budget_id=self._next_budget_id("budget", task_token),
            task_id=task_token,
            budget_type=str(budget_type or "general").strip() or "general",
            limit=max(0, int(limit)),
            unit=str(unit or "tokens").strip() or "tokens",
        )
        self._store.save_budget(budget)
        return {"ok": True, "budget": budget.to_dict()}

    def check_budget(self, task_id: str, estimated_cost: int) -> dict[str, Any]:
        rows = self._store.budgets_by_task(task_id)
        estimate = max(0, int(estimated_cost))
        if not rows:
            return {"ok": True, "within_budget": True, "reason": "no_budget_allocated"}
        for row in rows:
            remaining = max(0, int(row.limit) - int(row.used))
            if estimate > remaining:
                return {
                    "ok": True,
                    "within_budget": False,
                    "reason": "exceeds_limit",
                    "budget": row.to_dict(),
                    "remaining": remaining,
                    "requested": estimate,
                }
        return {
            "ok": True,
            "within_budget": True,
            "remaining": max(0, int(rows[0].limit) - int(rows[0].used)),
        }

    def record_usage(
        self,
        task_id: str,
        agent_id: str,
        resource_type: str,
        amount: int,
    ) -> dict[str, Any]:
        usage = UsageRecord(
            record_id=self._next_budget_id("usage", task_id),
            task_id=str(task_id or "").strip(),
            agent_id=str(agent_id or "").strip(),
            resource_type=str(resource_type or "general").strip() or "general",
            amount=max(0, int(amount)),
        )
        # Persist usage first (write-through to KFS).
        self._store.append_usage(usage)

        # Update matching budget's ``used`` counter — also write-through.
        for budget in self._store.budgets_by_task(task_id):
            if budget.budget_type in {usage.resource_type, "general"}:
                budget.used = int(budget.used) + int(usage.amount)
                self._store.save_budget(budget)

        return {"ok": True, "usage": usage.to_dict()}

    def get_budget_status(self, task_id: str) -> dict[str, Any]:
        rows = self._store.budgets_by_task(task_id)
        return {
            "ok": True,
            "task_id": task_id,
            "budgets": [row.to_dict() for row in rows],
            "has_budget": bool(rows),
        }

    def get_usage_stats(
        self,
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        if str(agent_id or "").strip():
            rows = self._store.usage_by_agent(str(agent_id))
        elif str(task_id or "").strip():
            rows = self._store.usage_by_task(str(task_id))
        else:
            rows = []
        return {
            "ok": True,
            "task_id": task_id,
            "agent_id": agent_id,
            "totals": self._store.usage_totals(task_id),
            "record_count": len(rows),
        }

    def set_budget_limit(self, budget_id: str, new_limit: int) -> dict[str, Any]:
        budget = self._store.get_budget(budget_id)
        if budget is None:
            return {"ok": False, "error": "budget_not_found", "budget_id": budget_id}
        budget.limit = max(0, int(new_limit))
        self._store.save_budget(budget)
        return {"ok": True, "budget": budget.to_dict()}
