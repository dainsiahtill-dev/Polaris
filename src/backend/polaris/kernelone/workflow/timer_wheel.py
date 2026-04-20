"""Timer Wheel - 最早截止优先的定时器调度。

参考 Workflow state_machine_timers.go 的"只调度下一批最早 timer"语义。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class TimerJob:
    """定时任务。

    ``due_monotonic`` drives scheduling correctness. ``due_at`` is retained for
    observability and APIs that need a wall-clock timestamp.
    """

    timer_id: str
    workflow_id: str
    due_monotonic: float
    due_at: datetime
    callback: Callable[[], Awaitable[None]]
    seq: int = 0  # 用于同截止时间时的顺序保证


class TimerWheel:
    """Timer Wheel - 最早截止优先调度器

    核心语义：
    1. 所有 timer 按截止时间排序
    2. 只调度下一批最早到达的 timer
    3. 支持分组去重（相同 workflow 的多个 timer 只会触发一次调度）
    """

    def __init__(self, tick_interval: float = 0.1) -> None:
        """初始化 Timer Wheel

        Args:
            tick_interval: 心跳间隔（秒）
        """
        self._tick_interval = tick_interval
        self._timers: dict[str, TimerJob] = {}  # timer_id -> TimerJob
        self._workflow_timers: dict[str, set[str]] = {}  # workflow_id -> set of timer_ids
        self._running = False
        self._task: asyncio.Task | None = None
        self._pending_callbacks: asyncio.Queue[tuple[TimerJob, Exception | None]] = asyncio.Queue(maxsize=500)
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def start(self) -> None:
        """启动定时器轮"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("TimerWheel started")

    async def stop(self) -> None:
        """停止定时器轮"""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("TimerWheel stopped")

    async def schedule_timer(
        self,
        timer_id: str,
        workflow_id: str,
        delay_seconds: float,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        """调度定时器

        Args:
            timer_id: 定时器 ID
            workflow_id: 工作流 ID
            delay_seconds: 延迟秒数
            callback: 回调函数
        """
        delay = max(float(delay_seconds), 0.0)
        loop = asyncio.get_running_loop()
        async with self._lock:
            due_monotonic = loop.time() + delay
            due_at = datetime.now() + timedelta(seconds=delay)
            self._sequence += 1

            # 分组去重：同一 workflow 已有 timer 时保留最早截止时间
            if workflow_id in self._workflow_timers:
                existing = self._workflow_timers[workflow_id]
                # 如果旧 timer 已存在，比较截止时间，保留最早的
                if timer_id in self._timers:
                    old_job = self._timers[timer_id]
                    # 如果新时间更晚，不更新
                    if due_monotonic > old_job.due_monotonic:
                        logger.debug(
                            "Timer %s already scheduled with earlier due_at",
                            timer_id,
                        )
                        return
                    # 删除旧的
                    existing.discard(timer_id)
                    del self._timers[timer_id]
                existing.add(timer_id)
            else:
                self._workflow_timers[workflow_id] = {timer_id}

            self._timers[timer_id] = TimerJob(
                timer_id=timer_id,
                workflow_id=workflow_id,
                due_monotonic=due_monotonic,
                due_at=due_at,
                callback=callback,
                seq=self._sequence,
            )

            logger.debug(
                "Scheduled timer %s for workflow %s due at %s",
                timer_id,
                workflow_id,
                due_at,
            )

    async def cancel_timer(self, timer_id: str) -> bool:
        """取消定时器"""
        async with self._lock:
            if timer_id not in self._timers:
                return False

            job = self._timers[timer_id]
            workflow_timers = self._workflow_timers.get(job.workflow_id, set())
            workflow_timers.discard(timer_id)
            if not workflow_timers:
                self._workflow_timers.pop(job.workflow_id, None)

            del self._timers[timer_id]
            logger.debug("Cancelled timer %s", timer_id)
            return True

    async def cancel_workflow_timers(self, workflow_id: str) -> int:
        """取消工作流所有定时器"""
        async with self._lock:
            timer_ids = self._workflow_timers.pop(workflow_id, set())
            for timer_id in timer_ids:
                self._timers.pop(timer_id, None)
            logger.debug(
                "Cancelled %d timers for workflow %s",
                len(timer_ids),
                workflow_id,
            )
            return len(timer_ids)

    async def get_next_due_time(self) -> datetime | None:
        """获取下一个到期时间"""
        async with self._lock:
            if not self._timers:
                return None
            return min(job.due_at for job in self._timers.values())

    async def get_timer_info(self, timer_id: str) -> TimerJob | None:
        """获取定时器信息（用于持久化层）"""
        async with self._lock:
            return self._timers.get(timer_id)

    def get_all_timer_ids(self) -> list[str]:
        """获取所有活跃定时器ID（不需要锁，浅拷贝）"""
        return list(self._timers.keys())

    async def _run(self) -> None:
        """Timer Wheel 主循环"""
        while self._running:
            try:
                await self._tick()
                await asyncio.sleep(self._tick_interval)
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError) as e:
                logger.exception("TimerWheel tick error: %s", e)
                await asyncio.sleep(self._tick_interval)

    async def _tick(self) -> None:
        """Timer tick - 检查并触发到期的 timer"""
        now_monotonic = asyncio.get_running_loop().time()

        # 找出所有到期的 timer（按 workflow 分组）
        due_workflows: dict[str, list[TimerJob]] = {}

        async with self._lock:
            due_timer_ids: list[str] = []

            for timer_id, job in list(self._timers.items()):
                if job.due_monotonic <= now_monotonic:
                    due_timer_ids.append(timer_id)

            due_timer_ids.sort(
                key=lambda timer_id: (
                    self._timers[timer_id].due_monotonic,
                    self._timers[timer_id].seq,
                )
            )

            # 按 workflow 分组（修复：确保 workflow_timers 是字典中的引用）
            for timer_id in due_timer_ids:
                job = self._timers.pop(timer_id)

                # 确保 workflow_timers 中存在该 workflow 的 entry
                if job.workflow_id not in self._workflow_timers:
                    self._workflow_timers[job.workflow_id] = set()

                workflow_timers = self._workflow_timers[job.workflow_id]
                workflow_timers.discard(timer_id)

                # 添加到到期工作流列表（使用 set 避免重复）
                if job.workflow_id not in due_workflows:
                    due_workflows[job.workflow_id] = []
                due_workflows[job.workflow_id].append(job)

        # 触发回调（不在 lock 内，避免死锁）
        # 在回调处理前检查 workflow 是否仍然存在，避免对已清理的 workflow 执行回调
        for workflow_id, jobs in due_workflows.items():
            # 在锁内复制回调列表，避免在执行期间被修改
            async with self._lock:
                if workflow_id not in self._workflow_timers:
                    logger.debug(
                        "Skipping timer callbacks for cancelled workflow %s",
                        workflow_id,
                    )
                    continue
                # 复制回调列表，在锁外执行
                executing_jobs = list(jobs)

            # 在锁外执行回调
            for job in executing_jobs:
                try:
                    await job.callback()
                except (RuntimeError, ValueError) as e:
                    logger.exception(
                        "Timer callback error for %s: %s",
                        job.timer_id,
                        e,
                    )
                    self._pending_callbacks.put_nowait((job, e))

    async def get_pending_count(self) -> int:
        """获取待处理 timer 数量"""
        async with self._lock:
            return len(self._timers)
