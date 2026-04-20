"""异步任务上下文传播工具

解决asyncio.create_task导致的上下文丢失问题。

这是整个可观测性架构的关键组件，用于修复关键的上下文断裂点，
例如 runtime workflow engine、background task service 和 message bus 异步分支。

Usage:
    # 旧代码（丢失上下文）:
    asyncio.create_task(my_async_func())

    # 新代码（保留上下文）:
    from polaris.kernelone.trace import create_task_with_context
    create_task_with_context(my_async_func())
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .context import (
    ContextManager,
    PolarisContext,
    _generate_id,
    get_context,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

T = TypeVar("T")

logger = logging.getLogger(__name__)


def create_task_with_context(
    coro: Coroutine[Any, Any, T],
    *,
    name: str | None = None,
    context: contextvars.Context | None = None,
) -> asyncio.Task[T]:
    """创建带有上下文传播的任务

    这是asyncio.create_task的增强版本，自动复制当前上下文到任务中。
    这是修复上下文断裂的核心函数。

    Args:
        coro: 要执行的协程
        name: 任务名称（用于调试）
        context: 可选的contextvars.Context，如果不提供则复制当前上下文

    Returns:
        asyncio.Task对象

    Example:
        # 在当前上下文内启动后台任务
        task = create_task_with_context(process_data(data))

        # 启动命名任务
        task = create_task_with_context(
            process_data(data),
            name=f"process-{data.id}"
        )

    Note:
        这个函数应该替换所有asyncio.create_task的调用，
        特别是那些需要保持追踪上下文的场景。
    """
    if context is None:
        # 复制当前上下文 - 这是关键操作！
        context = contextvars.copy_context()

    # 在复制的上下文中创建任务
    # 注意：asyncio.create_task在Python 3.11+支持context参数
    # 但为了兼容性，我们使用context.run()来执行
    return context.run(asyncio.create_task, coro, name=name)


def copy_context_to_task(
    coro: Coroutine[Any, Any, T],
    context: contextvars.Context | None = None,
) -> Coroutine[Any, Any, T]:
    """将当前上下文复制到协程

    这是一个更低级的接口，用于需要更多控制的场景。

    Args:
        coro: 原始协程
        context: 可选的contextvars.Context

    Returns:
        包装后的协程
    """
    if context is None:
        context = contextvars.copy_context()

    async def wrapper() -> T:
        # ``context.run`` 只对同步入口生效。要让协程体真正运行在复制的
        # context 中，必须在该 context 内创建独立 Task 再 await。
        task = context.run(asyncio.create_task, coro)
        return await task

    return wrapper()


class ContextPreservingTask:
    """保留上下文的任务包装器

    用于需要多次创建任务或更复杂控制的场景。

    Example:
        task_wrapper = ContextPreservingTask(my_async_func(), name="my-task")
        task = task_wrapper.create()
        # 后续可以再次创建新任务，上下文仍然保留
        task2 = task_wrapper.create()
    """

    def __init__(
        self,
        coro: Coroutine[Any, Any, T],
        name: str | None = None,
        context: contextvars.Context | None = None,
    ) -> None:
        self.coro = coro
        self.name = name
        # 在创建时捕获上下文
        self._context = context or contextvars.copy_context()

    def create(self) -> asyncio.Task[T]:
        """创建任务（使用捕获的上下文）"""
        coro = cast("Coroutine[Any, Any, T]", self.coro)
        name = self.name

        def _create_task() -> asyncio.Task[T]:
            return asyncio.create_task(coro, name=name)

        return self._context.run(_create_task)


# 装饰器


def traced_task(
    name: str | None = None,
    trace_type: str = "task",
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """装饰器：自动追踪异步任务

    自动创建新的span，并记录任务开始和结束。

    Args:
        name: 任务名称，默认为函数名
        trace_type: 追踪类型前缀

    Example:
        @traced_task(name="data-processor")
        async def process_data(data: Data) -> Result:
            # 自动创建span，记录开始/结束
            return await do_process(data)
    """

    def decorator(func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            task_name = name or func.__qualname__

            # 获取或创建上下文
            try:
                ctx = get_context()
                # 创建子span
                new_ctx = ctx.with_span(task_name)
            except (RuntimeError, ValueError) as exc:
                logger.warning("kernelone.trace.async_utils.get_context failed: %s", exc, exc_info=True)
                # 没有上下文，创建新的
                ctx = PolarisContext(
                    trace_id=_generate_id(trace_type),
                )
                new_ctx = ctx

            with ContextManager.bind_context(new_ctx):
                span_id = new_ctx.span_stack[-1]["span_id"] if new_ctx.span_stack else None

                # 记录任务开始
                logger.info(
                    f"Task started: {task_name}",
                    extra={
                        "task_name": task_name,
                        "trace_id": new_ctx.trace_id,
                        "span_id": span_id,
                        "event_type": "task_start",
                    },
                )

                try:
                    result = await func(*args, **kwargs)
                    logger.info(
                        f"Task completed: {task_name}",
                        extra={
                            "task_name": task_name,
                            "trace_id": new_ctx.trace_id,
                            "span_id": span_id,
                            "event_type": "task_complete",
                        },
                    )
                    return result
                except (RuntimeError, ValueError) as e:
                    logger.error(
                        f"Task failed: {task_name}",
                        extra={
                            "task_name": task_name,
                            "trace_id": new_ctx.trace_id,
                            "span_id": span_id,
                            "event_type": "task_error",
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                        },
                    )
                    raise

        return wrapper

    return decorator


def with_child_context(
    trace_type: str = "subtask",
    inherit_from_parent: bool = True,
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """装饰器：在子上下文中执行

    创建新的trace_id，但可以选择是否继承父上下文的元数据。

    Args:
        trace_type: 子上下文类型前缀
        inherit_from_parent: 是否继承父上下文的元数据

    Example:
        @with_child_context(trace_type="subtask")
        async def handle_subtask(data: Data) -> Result:
            # 在新上下文中执行，但保留父trace_id
            return await process(data)
    """

    def decorator(func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                parent_ctx = get_context()
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "kernelone.trace.async_utils.get_parent_context failed: %s",
                    exc,
                    exc_info=True,
                )
                parent_ctx = None

            if parent_ctx and inherit_from_parent:
                # 继承父上下文，但创建新的子span
                child_ctx = parent_ctx.with_span(func.__qualname__)
            else:
                # 创建完全新的上下文
                child_ctx = PolarisContext(
                    trace_id=_generate_id(trace_type),
                )

            with ContextManager.bind_context(child_ctx):
                return await func(*args, **kwargs)

        return wrapper

    return decorator


# 工具函数


async def run_in_context(
    coro: Coroutine[Any, Any, T],
    ctx: PolarisContext | None = None,
) -> T:
    """在指定上下文中运行协程

    Args:
        coro: 要运行的协程
        ctx: 上下文，不提供则使用当前上下文

    Returns:
        协程的结果
    """
    if ctx is None:
        ctx = get_context()

    with ContextManager.bind_context(ctx):
        return await coro


def create_task_group_with_context(
    *coros: Coroutine[Any, Any, Any],
    name: str | None = None,
) -> list[asyncio.Task[Any]]:
    """创建带有相同上下文的多个任务

    Args:
        *coros: 多个协程
        name: 任务组名称前缀

    Returns:
        Task列表
    """
    context = contextvars.copy_context()
    tasks = []
    for i, coro in enumerate(coros):
        task_name = f"{name}-{i}" if name else None
        task = context.run(asyncio.create_task, coro, name=task_name)
        tasks.append(task)
    return tasks


# 向后兼容：提供旧API的适配


def patch_asyncio_create_task() -> None:
    """猴子补丁：替换asyncio.create_task以自动传播上下文

    ⚠️ 警告：谨慎使用！这会全局修改asyncio.create_task的行为，
    可能影响性能，并可能与某些库不兼容。

    推荐做法：显式使用create_task_with_context()替换所有调用点。

    如果必须使用此补丁，请确保：
    1. 在应用启动早期调用
    2. 充分测试所有异步代码路径
    3. 监控性能影响
    """
    original_create_task = asyncio.create_task

    def patched_create_task(
        coro,
        *,
        name=None,
        context=None,
    ):
        # Python 3.11+ asyncio.create_task支持context参数
        # 但我们仍然需要复制我们的自定义上下文
        if context is None:
            # 注意：这里只复制contextvars，不包括asyncio任务上下文
            copied = contextvars.copy_context()
            return copied.run(original_create_task, coro, name=name)
        return original_create_task(coro, name=name, context=context)

    asyncio.create_task = patched_create_task  # type: ignore
    logger.warning(
        "asyncio.create_task has been patched to auto-propagate context. This may affect performance and compatibility."
    )


# 便捷的批量替换辅助函数


def safe_create_task(
    coro: Coroutine[Any, Any, T],
    *,
    name: str | None = None,
    fallback_to_standard: bool = False,
) -> asyncio.Task[T]:
    """安全地创建任务（兼容模式）

    这个函数会先尝试使用上下文传播，如果失败则回退到标准create_task。
    用于过渡期，确保稳定性。

    Args:
        coro: 协程
        name: 任务名称
        fallback_to_standard: 失败时是否回退到标准create_task

    Returns:
        Task对象
    """
    try:
        return create_task_with_context(coro, name=name)
    except (RuntimeError, ValueError) as e:
        if fallback_to_standard:
            logger.warning(f"Failed to create task with context, falling back to standard: {e}")
            return asyncio.create_task(coro, name=name)
        raise
