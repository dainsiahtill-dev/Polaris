"""FinOps budget agent implementation.

State ownership: all budget and usage state is owned by ``BudgetKFSStore``
(single source of truth backed by KernelOne FS at
``runtime/state/budget/<scope_id>.json``).

The in-memory store inside ``BudgetKFSStore`` is a write-through cache only.
Events emitted to the JSONL event stream are notification projections — they
are never read back to reconstruct state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.cells.finops.budget_guard.internal.budget_store import (
    BudgetKFSStore,
    BudgetRecord,
    UsageRecord,
)
from polaris.cells.roles.runtime.public.service import (
    AgentMessage,
    MessageType,
    RoleAgent,
)

if TYPE_CHECKING:
    from polaris.kernelone.fs import KernelFileSystem


class CFOAgent(RoleAgent):
    """CFO role agent (FinOps).

    All persistent state flows through ``BudgetKFSStore``.  The agent accepts
    an optional ``fs`` parameter so tests can inject a ``KernelFileSystem``
    without touching the global registry.
    """

    def __init__(self, workspace: str, *, fs: KernelFileSystem | None = None) -> None:
        super().__init__(workspace=workspace, agent_name="CFO")
        # Use workspace as the default scope so all CFOAgent instances in the
        # same workspace share state.  Callers can override by constructing
        # the store directly if per-task scoping is needed.
        self._store = BudgetKFSStore(workspace, scope_id="global", fs=fs)

    def setup_toolbox(self) -> None:
        tb = self.toolbox
        tb.register("set_global_budget", self._tool_set_global_budget, description="Set global budget")
        tb.register("allocate_budget", self._tool_allocate_budget, description="Allocate budget to task")
        tb.register("check_budget", self._tool_check_budget, description="Check budget before execution")
        tb.register("record_usage", self._tool_record_usage, description="Record budget usage")
        tb.register("get_budget_status", self._tool_get_budget_status, description="Get task budget status")
        tb.register("get_usage_stats", self._tool_get_usage_stats, description="Get usage statistics")
        tb.register("set_budget_limit", self._tool_set_budget_limit, description="Update budget limit")

    def _next_budget_id(self, prefix: str, task_id: str) -> str:
        task_token = str(task_id or "task").strip().replace(" ", "_")
        return f"{prefix}_{task_token}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    def _tool_set_global_budget(self, limit: int, unit: str = "tokens") -> dict[str, Any]:
        budget = BudgetRecord(
            budget_id=self._next_budget_id("global", "global"),
            task_id="global",
            budget_type="global",
            limit=max(0, int(limit)),
            unit=str(unit or "tokens").strip() or "tokens",
        )
        self._store.save_budget(budget)
        return {"ok": True, "budget": budget.to_dict()}

    def _tool_allocate_budget(
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

    def _tool_check_budget(self, task_id: str, estimated_cost: int) -> dict[str, Any]:
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

    def _tool_record_usage(
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

    def _tool_get_budget_status(self, task_id: str) -> dict[str, Any]:
        rows = self._store.budgets_by_task(task_id)
        return {
            "ok": True,
            "task_id": task_id,
            "budgets": [row.to_dict() for row in rows],
            "has_budget": bool(rows),
        }

    def _tool_get_usage_stats(
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

    def _tool_set_budget_limit(self, budget_id: str, new_limit: int) -> dict[str, Any]:
        budget = self._store.get_budget(budget_id)
        if budget is None:
            return {"ok": False, "error": "budget_not_found", "budget_id": budget_id}
        budget.limit = max(0, int(new_limit))
        self._store.save_budget(budget)
        return {"ok": True, "budget": budget.to_dict()}

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        payload = dict(message.payload or {})
        if message.type != MessageType.TASK:
            return None
        action = str(payload.get("action") or "").strip().lower()
        if action == "allocate_budget":
            result = self._tool_allocate_budget(
                task_id=str(payload.get("task_id") or "").strip(),
                budget_type=str(payload.get("budget_type") or "general").strip(),
                limit=int(payload.get("limit") or 0),
                unit=str(payload.get("unit") or "tokens").strip(),
            )
        elif action == "check_budget":
            result = self._tool_check_budget(
                task_id=str(payload.get("task_id") or "").strip(),
                estimated_cost=int(payload.get("estimated_cost") or 0),
            )
        elif action == "record_usage":
            result = self._tool_record_usage(
                task_id=str(payload.get("task_id") or "").strip(),
                agent_id=str(payload.get("agent_id") or "").strip(),
                resource_type=str(payload.get("resource_type") or "general").strip(),
                amount=int(payload.get("amount") or 0),
            )
        elif action == "get_budget_status":
            result = self._tool_get_budget_status(
                task_id=str(payload.get("task_id") or "").strip(),
            )
        else:
            result = {"ok": False, "error": "unsupported_action", "action": action}
        return AgentMessage.create(
            msg_type=MessageType.RESULT,
            sender=self.agent_name,
            receiver=message.sender,
            payload={"role": "cfo", "action": action, "result": result},
            correlation_id=message.id,
        )

    def run_cycle(self) -> bool:
        message = self.message_queue.receive(block=False)
        if message is None:
            return False
        response = self.handle_message(message)
        if response is not None:
            self.message_queue.send(response)
        return True
