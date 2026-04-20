from __future__ import annotations

from polaris.cells.roles.kernel.internal.speculation.events import (
    SpeculationEvent,
    emit,
)
from polaris.cells.roles.kernel.internal.speculation.registry import (
    ShadowTaskRegistry,
)
from polaris.cells.roles.kernel.internal.speculation.task_group import (
    TurnScopedTaskGroup,
)


class CancellationCoordinator:
    """协调 CancelToken 广播、task.cancel() 调用以及 refusal/turn 级批量清理."""

    async def refuse_turn(
        self,
        turn_id: str,
        registry: ShadowTaskRegistry,
        *,
        task_group: TurnScopedTaskGroup | None = None,
    ) -> None:
        """Refusal abort：将整个 turn 的 shadow task 标记为废弃或取消.

        语义：
        - 运行中任务立刻取消（refusal 意味着用户意图已改变，继续执行无意义）。
        - 已完成未 ADOPT 的任务标记为 ABANDONED，防止后续 authoritative 路径错误复用。
        - 从 active_spec_index 中移除该 turn 的所有条目。
        """
        await registry.abandon_turn(turn_id, reason="refusal_abort")

        if task_group is not None:
            await task_group.cancel_all(salvage=False)

        emit(
            SpeculationEvent(
                event_type="speculation.guardrail.refusal_abort",
                turn_id=turn_id,
                action="refuse_turn",
                reason="all_shadow_tasks_abandoned_or_cancelled",
            )
        )

    async def cancel_turn(
        self,
        turn_id: str,
        registry: ShadowTaskRegistry,
        task_group: TurnScopedTaskGroup,
        *,
        salvage: bool = True,
    ) -> None:
        """Turn 级取消：由外部（如用户中断）触发的批量清理.

        Args:
            salvage: 是否启用 salvage 策略；若为 False，一律强杀。
        """
        if salvage:
            records = registry.get_turn_records(turn_id)
            await task_group.cancel_with_salvage(records)
        else:
            await task_group.cancel_all(salvage=False)
            await registry.drain_turn(turn_id, timeout_s=0.2)
            # Completed but unadopted tasks should also be invalidated on hard cancel
            await registry.abandon_turn(turn_id, reason="turn_cancel")

        emit(
            SpeculationEvent(
                event_type="speculation.guardrail.turn_cancelled",
                turn_id=turn_id,
                action="cancel_turn",
                reason="salvage" if salvage else "hard_cancel",
            )
        )
