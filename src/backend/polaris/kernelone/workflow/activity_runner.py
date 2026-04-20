"""Activity Runner - Activity 执行器。

参考 Workflow _activity.py 的：
- 运行中 activity 表
- heartbeat 合并
- 取消传播
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = logging.getLogger(__name__)


def _get_retry_policy_values(policy: Any) -> tuple[int, float, float]:
    """Extract retry policy values from RetryPolicy or dict.

    Args:
        policy: Either a RetryPolicy dataclass or a dict with retry configuration.

    Returns:
        A tuple of (max_attempts, initial_interval_seconds, backoff_coefficient).
    """
    if hasattr(policy, "max_attempts"):  # RetryPolicy dataclass
        return (
            int(policy.max_attempts),
            float(policy.initial_interval_seconds),
            float(policy.backoff_coefficient),
        )
    else:  # dict
        return (
            int(policy.get("max_attempts", 1)),
            float(policy.get("initial_interval_seconds", 1.0)),
            float(policy.get("backoff_coefficient", 2.0)),
        )


def _spawn_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Create an asyncio task without application-layer dependency."""
    return asyncio.create_task(coro)


@dataclass
class ActivityExecution:
    """Activity 执行状态"""

    activity_id: str
    activity_name: str
    workflow_id: str
    input: dict[str, Any]
    status: str = "pending"  # pending, running, completed, failed, cancelled
    start_time: datetime | None = None
    end_time: datetime | None = None
    result: Any = None
    error: str | None = None
    heartbeat_count: int = 0
    last_heartbeat: datetime | None = None
    attempt: int = 0


@dataclass
class ActivityConfig:
    """Activity 配置"""

    timeout_seconds: int = 300
    retry_policy: dict[str, Any] = field(
        default_factory=lambda: {
            "max_attempts": 3,
            "initial_interval_seconds": 1,
            "backoff_coefficient": 2.0,
        }
    )


class ActivityRunner:
    """Activity Runner - 参考 Workflow 语义

    核心功能：
    1. Activity 生命周期管理
    2. Heartbeat 支持
    3. 取消传播
    4. 重试逻辑
    """

    def __init__(
        self,
        max_concurrent: int = 50,
    ) -> None:
        self._max_concurrent = max_concurrent
        self._running = False
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_retained_activities = max(200, int(max_concurrent) * 20)

        # 运行中的 activities
        self._activities: dict[str, ActivityExecution] = {}

        # Activity 处理函数注册表
        self._handlers: dict[str, Callable[..., Any]] = {}

        # 取消事件
        self._cancel_events: dict[str, asyncio.Event] = {}

        # 等待中的 activity start（有界队列防止内存泄漏）
        self._pending_starts: asyncio.Queue[tuple[str, str, dict[str, Any]]] = asyncio.Queue(maxsize=500)

        self._lock = asyncio.Lock()

    def register_handler(
        self,
        activity_name: str,
        handler: Callable[..., Any],
    ) -> None:
        """注册 Activity 处理函数"""
        self._handlers[activity_name] = handler
        logger.debug(f"Registered activity handler: {activity_name}")

    def has_handler(self, activity_name: str) -> bool:
        """检查 Activity 处理函数是否存在"""
        return str(activity_name or "").strip() in self._handlers

    async def execute(
        self,
        activity_name: str,
        input: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        quota_agent_id: str | None = None,
    ) -> Any:
        """直接执行 Activity（供调度引擎调用）"""
        if not self._running:
            raise RuntimeError("ActivityRunner not started")

        # ── Resource Quota 检查 ──────────────────────────────────────────────
        if quota_agent_id:
            try:
                from polaris.kernelone.resource import get_global_quota_manager

                manager = get_global_quota_manager()
                try:
                    status = manager.check_quota(quota_agent_id)
                except KeyError:
                    manager.allocate(quota_agent_id)
                    status = manager.check_quota(quota_agent_id)

                if status.value != "allowed":
                    usage = manager.get_usage(quota_agent_id)
                    raise RuntimeError(
                        f"Activity quota exceeded: status={status.value}, "
                        f"turns={usage.turns}, wall_time={usage.wall_time_seconds}s"
                    )
            except (RuntimeError, ValueError) as exc:
                logger.warning("[ActivityRunner] Quota check failed (allowing execution): %s", exc)

        handler = self._handlers.get(str(activity_name or "").strip())
        if handler is None:
            raise RuntimeError(f"No handler for activity: {activity_name}")
        async with self._semaphore:
            return await self._execute_handler(handler, input, timeout_seconds=timeout_seconds)

    async def start(self) -> None:
        """启动 Activity Runner"""
        if self._running:
            return
        self._running = True
        logger.info(f"ActivityRunner started (max_concurrent={self._max_concurrent})")

    async def stop(self) -> None:
        """停止 Activity Runner"""
        self._running = False

        # 等待所有 running activities 完成
        async with self._lock:
            running = [(aid, exec) for aid, exec in self._activities.items() if exec.status == "running"]

        if running:
            logger.info(f"Waiting for {len(running)} activities to complete")
            # 发送取消信号
            for aid, _ in running:
                await self.request_cancel(aid)

            # 等待活动真正完成（有上限，避免 shutdown 无限卡死）
            shutdown_deadline = asyncio.get_running_loop().time() + 10.0
            while True:
                async with self._lock:
                    still_running = [exec for exec in self._activities.values() if exec.status == "running"]
                if not still_running:
                    break
                if asyncio.get_running_loop().time() >= shutdown_deadline:
                    logger.warning(
                        "ActivityRunner stop timeout with %s running activities",
                        len(still_running),
                    )
                    break
                await asyncio.sleep(0.1)

        logger.info("ActivityRunner stopped")

    async def submit_activity(
        self,
        activity_id: str,
        activity_name: str,
        workflow_id: str,
        input: dict[str, Any],
        config: ActivityConfig | None = None,
    ) -> None:
        """提交 Activity 执行"""
        config = config or ActivityConfig()

        async with self._lock:
            execution = ActivityExecution(
                activity_id=activity_id,
                activity_name=activity_name,
                workflow_id=workflow_id,
                input=input,
                status="pending",
            )
            self._activities[activity_id] = execution
            self._cancel_events[activity_id] = asyncio.Event()

        # 使用信号量控制并发
        _spawn_task(self._run_activity(activity_id, config))

        logger.debug(f"Submitted activity {activity_id} ({activity_name})")

    async def _run_activity(
        self,
        activity_id: str,
        config: ActivityConfig,
    ) -> None:
        """运行 Activity"""
        # 获取重试策略配置
        retry_policy = config.retry_policy if config.retry_policy else {}
        max_attempts, initial_interval_seconds, backoff_coefficient = _get_retry_policy_values(retry_policy)

        # 用于跟踪重试次数
        execution = self._activities.get(activity_id)
        if not execution:
            return

        async def _execute_with_retry() -> None:
            """带重试的 Activity 执行"""
            last_error = None

            for attempt in range(1, max_attempts + 1):
                execution.attempt = attempt
                execution.status = "pending"

                async with self._semaphore:
                    # 再次检查是否已取消
                    cancel_event = self._cancel_events.get(activity_id)
                    if cancel_event and cancel_event.is_set():
                        execution.status = "cancelled"
                        execution.error = "Activity cancelled"
                        return

                    execution.status = "running"
                    execution.start_time = datetime.now()

                    # ── Resource Quota 检查 ─────────────────────────────────────
                    _quota_agent_id = f"workflow@{execution.workflow_id}"
                    try:
                        from polaris.kernelone.resource import get_global_quota_manager

                        manager = get_global_quota_manager()
                        try:
                            status = manager.check_quota(_quota_agent_id)
                        except KeyError:
                            manager.allocate(_quota_agent_id)
                            status = manager.check_quota(_quota_agent_id)

                        if status.value != "allowed":
                            usage = manager.get_usage(_quota_agent_id)
                            execution.status = "failed"
                            execution.error = (
                                f"Activity quota exceeded: status={status.value}, "
                                f"turns={usage.turns}, wall_time={usage.wall_time_seconds}s"
                            )
                            execution.end_time = datetime.now()
                            logger.warning(
                                "[ActivityRunner] Quota exceeded for activity %s: %s",
                                activity_id,
                                execution.error,
                            )
                            return
                    except (RuntimeError, ValueError) as exc:
                        logger.warning("[ActivityRunner] Quota check failed (allowing execution): %s", exc)
                    # ── End Resource Quota 检查 ───────────────────────────────

                    handler = self._handlers.get(execution.activity_name)
                    if not handler:
                        execution.status = "failed"
                        execution.error = f"No handler for activity: {execution.activity_name}"
                        logger.error(f"No handler for activity {execution.activity_name}")
                        return

                    # 执行 Activity
                    try:
                        result_task = self._execute_with_cancel(
                            activity_id,
                            handler,
                            execution.input,
                        )
                        timeout = max(1, int(config.timeout_seconds or 300))
                        result = await asyncio.wait_for(result_task, timeout=timeout)

                        execution.result = result
                        execution.status = "completed"
                        execution.end_time = datetime.now()
                        logger.info(f"Activity {activity_id} completed (attempt {attempt}/{max_attempts})")
                        return  # 成功完成，返回

                    except asyncio.CancelledError:
                        execution.status = "cancelled"
                        execution.error = "Activity cancelled"
                        execution.end_time = datetime.now()
                        logger.info(f"Activity {activity_id} cancelled")
                        return  # 取消，不重试

                    except (RuntimeError, ValueError) as e:
                        last_error = e
                        execution.status = "failed"
                        execution.error = str(e)
                        execution.end_time = datetime.now()
                        logger.warning(f"Activity {activity_id} failed (attempt {attempt}/{max_attempts}): {e}")

                        # 如果还有重试次数，等待后重试
                        if attempt < max_attempts:
                            # 计算指数退避延迟
                            delay = initial_interval_seconds * (backoff_coefficient ** (attempt - 1))
                            logger.info(f"Activity {activity_id} will retry in {delay}s...")
                            await asyncio.sleep(delay)
                        # 继续循环重试

            # 所有重试都失败
            execution.status = "failed"
            execution.error = f"All {max_attempts} attempts failed. Last error: {last_error}"
            execution.end_time = datetime.now()
            logger.error(f"Activity {activity_id} failed after {max_attempts} attempts: {last_error}")

        # 启动带重试的执行
        await _execute_with_retry()

        # 保留最近终态记录用于查询，并做有界裁剪防止内存泄漏。
        await self._prune_terminal_activities()

    async def _execute_with_cancel(
        self,
        activity_id: str,
        handler: Callable[..., Any],
        input: dict[str, Any],
    ) -> Any:
        """带取消支持的执行"""
        cancel_event = self._cancel_events.get(activity_id)

        # 并行等待 handler 和取消事件 - 使用 create_task 包装协程
        async def run_handler():
            return await self._execute_handler(handler, input)

        if cancel_event:
            # 使用 create_task 将协程转换为任务
            handler_task = asyncio.create_task(run_handler())
            cancel_task = asyncio.create_task(cancel_event.wait())

            done, _pending = await asyncio.wait(
                [handler_task, cancel_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_task in done:
                # 取消事件先完成，抛出取消异常
                handler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await handler_task
                raise asyncio.CancelledError()

            # Handler 完成，取消等待中的取消事件
            cancel_event.clear()
            cancel_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_task

            # 返回 handler 结果
            return handler_task.result()

        # 没有取消事件，直接运行
        return await run_handler()

    async def _execute_handler(
        self,
        handler: Callable[..., Any],
        input: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        """执行 handler，兼容 sync/async"""
        # 安全处理输入参数
        safe_input = input if isinstance(input, dict) else {}
        result = handler(**safe_input)
        if inspect.isawaitable(result):
            if timeout_seconds is not None:
                return await asyncio.wait_for(result, timeout=max(0.01, float(timeout_seconds)))
            return await result
        return result

    async def request_cancel(self, activity_id: str) -> bool:
        """请求取消 Activity"""
        async with self._lock:
            execution = self._activities.get(activity_id)
            if not execution:
                return False

            cancel_event = self._cancel_events.get(activity_id)
            if cancel_event:
                cancel_event.set()

            if execution.status == "running":
                logger.info(f"Requested cancel for activity {activity_id}")
                return True

            return False

    async def record_heartbeat(self, activity_id: str, details: dict[str, Any] | None = None) -> None:
        """记录 Heartbeat"""
        async with self._lock:
            execution = self._activities.get(activity_id)
            if execution and execution.status == "running":
                execution.heartbeat_count += 1
                execution.last_heartbeat = datetime.now()
                logger.debug(f"Heartbeat {execution.heartbeat_count} for activity {activity_id}")

    async def _cleanup_activity(self, activity_id: str) -> None:
        """清理已完成的 Activity 记录，防止内存泄漏"""
        async with self._lock:
            self._activities.pop(activity_id, None)
            self._cancel_events.pop(activity_id, None)
            logger.debug(f"Cleaned up activity {activity_id}")

    async def _prune_terminal_activities(self) -> None:
        """Prune old terminal activities while preserving recent execution states."""
        async with self._lock:
            if len(self._activities) <= self._max_retained_activities:
                return
            terminal_status = {"completed", "failed", "cancelled"}
            terminal = [
                (activity_id, execution)
                for activity_id, execution in self._activities.items()
                if execution.status in terminal_status
            ]
            overflow = len(self._activities) - self._max_retained_activities
            if overflow <= 0 or not terminal:
                return

            terminal.sort(key=lambda item: item[1].end_time or datetime.min)
            for activity_id, _ in terminal[:overflow]:
                self._activities.pop(activity_id, None)
                self._cancel_events.pop(activity_id, None)

    async def get_activity_status(self, activity_id: str) -> ActivityExecution | None:
        """获取 Activity 状态"""
        async with self._lock:
            return self._activities.get(activity_id)

    async def list_running_activities(self, workflow_id: str | None = None) -> list[ActivityExecution]:
        """列出运行中的 Activities"""
        async with self._lock:
            activities = [exec for exec in self._activities.values() if exec.status == "running"]
            if workflow_id:
                activities = [a for a in activities if a.workflow_id == workflow_id]
            return activities

    async def get_activity_count(self) -> dict[str, int]:
        """获取 Activity 统计"""
        async with self._lock:
            counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0}
            for exec in self._activities.values():
                if exec.status in counts:
                    counts[exec.status] += 1
            return counts
