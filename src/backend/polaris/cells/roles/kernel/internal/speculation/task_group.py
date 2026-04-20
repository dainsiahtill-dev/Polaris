from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from polaris.cells.roles.kernel.internal.speculation.events import (
    SpeculationEvent,
    emit,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    SalvageDecision,
    ShadowTaskRecord,
)
from polaris.cells.roles.kernel.internal.speculation.salvage import SalvageGovernor

T = TypeVar("T")


class TurnScopedTaskGroup:
    """Turn 级结构化并发容器：管理单个 turn 内的所有 shadow task 生命周期.

    与 asyncio.TaskGroup 的区别：
    - 支持 "salvage" 模式下的部分任务保留（允许完成并缓存或 JOIN）。
    - 提供显式的 cancel_all() 和 join_all()，而非强制在上下文退出时等待全部完成。
    """

    def __init__(
        self,
        turn_id: str,
        *,
        salvage_governor: SalvageGovernor | None = None,
    ) -> None:
        self._turn_id = turn_id
        self._salvage_governor = salvage_governor or SalvageGovernor()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._detached: set[asyncio.Task[Any]] = set()
        self._closed = False

    def create_task(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        """在任务组中注册一个新的 asyncio.Task."""
        if self._closed:
            raise RuntimeError("TurnScopedTaskGroup is closed")
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cancel_all(self, *, salvage: bool = True) -> None:
        """取消任务组中的所有任务.

        Args:
            salvage: 若为 True，先按 SalvageGovernor 策略分别处理；
                     若为 False，一律立刻取消。
        """
        if self._closed:
            return

        pending = list(self._tasks)
        if not pending:
            return

        if not salvage:
            for task in pending:
                if not task.done():
                    task.cancel()
            return

        # Salvage 模式需要 ShadowTaskRecord 才能决策。
        # 由于 task 本身不携带 record， caller 应通过 registry 决策后再调用本方法。
        # 因此 salvage=True 但无 record 信息时，默认全部取消。
        for task in pending:
            if not task.done():
                task.cancel()

    async def cancel_with_salvage(
        self,
        records: list[ShadowTaskRecord],
    ) -> dict[str, SalvageDecision]:
        """基于 SalvageGovernor 对给定的 shadow tasks 分别处理.

        注意：records 必须与任务组中的 task 对应（由 caller 通过 registry 提供映射）。
        """
        decisions: dict[str, SalvageDecision] = {}
        for record in records:
            decision = self._salvage_governor.evaluate(record)
            decisions[record.task_id] = decision

            future = record.future
            if future is None:
                continue

            if decision == SalvageDecision.CANCEL_NOW:
                if not future.done():
                    future.cancel(msg="salvage_cancel_now")
            elif decision == SalvageDecision.LET_FINISH_AND_CACHE:
                # 从任务组中剥离，允许在后台继续运行
                self._tasks.discard(future)
                self._detached.add(future)
                future.add_done_callback(self._detached.discard)
            elif decision == SalvageDecision.JOIN_AUTHORITATIVE:
                # 保持运行，由 authoritative 路径接管
                pass

        emit(
            SpeculationEvent(
                event_type="speculation.guardrail.salvage_evaluated",
                turn_id=self._turn_id,
                action="cancel_with_salvage",
                reason=str({k: v.value for k, v in decisions.items()}),
            )
        )
        return decisions

    async def join_all(self, *, timeout: float | None = None) -> None:
        """等待任务组中所有任务完成（不包括已 detach 的任务）."""
        pending = list(self._tasks)
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout, return_when=asyncio.ALL_COMPLETED)

    def close(self) -> None:
        """关闭任务组，禁止再创建新任务."""
        self._closed = True

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        """Task 完成时的内部清理回调."""
        self._tasks.discard(task)
        self._detached.discard(task)
