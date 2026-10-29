"""RoleAgent hosting for the optional deterministic CFO budget advisor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.finops.budget_guard.public.service import BudgetService

from .agent_runtime_base import AgentMessage, MessageType, RoleAgent

if TYPE_CHECKING:
    from polaris.kernelone.fs import KernelFileSystem


class CFOAgent(RoleAgent):
    """Retain role lifecycle and messaging while FinOps owns budget facts."""

    def __init__(self, workspace: str, *, fs: KernelFileSystem | None = None) -> None:
        super().__init__(workspace=workspace, agent_name="CFO")
        self._budget_service = BudgetService(workspace, fs=fs)

    def setup_toolbox(self) -> None:
        tb = self.toolbox
        tb.register("set_global_budget", self._tool_set_global_budget, description="Set global budget")
        tb.register("allocate_budget", self._tool_allocate_budget, description="Allocate budget to task")
        tb.register("check_budget", self._tool_check_budget, description="Check budget before execution")
        tb.register("record_usage", self._tool_record_usage, description="Record budget usage")
        tb.register("get_budget_status", self._tool_get_budget_status, description="Get task budget status")
        tb.register("get_usage_stats", self._tool_get_usage_stats, description="Get usage statistics")
        tb.register("set_budget_limit", self._tool_set_budget_limit, description="Update budget limit")

    def _tool_set_global_budget(self, limit: int, unit: str = "tokens") -> dict[str, Any]:
        return cast("dict[str, Any]", (self._budget_service.set_global_budget(limit=limit, unit=unit)))

    def _tool_allocate_budget(
        self,
        task_id: str,
        budget_type: str,
        limit: int,
        unit: str = "tokens",
    ) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            (self._budget_service.allocate_budget(task_id=task_id, budget_type=budget_type, limit=limit, unit=unit)),
        )

    def _tool_check_budget(self, task_id: str, estimated_cost: int) -> dict[str, Any]:
        return cast(
            "dict[str, Any]", (self._budget_service.check_budget(task_id=task_id, estimated_cost=estimated_cost))
        )

    def _tool_record_usage(
        self,
        task_id: str,
        agent_id: str,
        resource_type: str,
        amount: int,
    ) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            (
                self._budget_service.record_usage(
                    task_id=task_id, agent_id=agent_id, resource_type=resource_type, amount=amount
                )
            ),
        )

    def _tool_get_budget_status(self, task_id: str) -> dict[str, Any]:
        return cast("dict[str, Any]", (self._budget_service.get_budget_status(task_id=task_id)))

    def _tool_get_usage_stats(
        self,
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        return cast("dict[str, Any]", (self._budget_service.get_usage_stats(task_id=task_id, agent_id=agent_id)))

    def _tool_set_budget_limit(self, budget_id: str, new_limit: int) -> dict[str, Any]:
        return cast("dict[str, Any]", (self._budget_service.set_budget_limit(budget_id=budget_id, new_limit=new_limit)))

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
