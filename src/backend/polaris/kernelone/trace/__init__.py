"""Polaris 统一可观测性模块

提供贯穿整个请求生命周期的统一可观测性能力：
- 统一上下文管理 (PolarisContext)
- 异步任务上下文传播 (create_task_with_context)
- 结构化JSON日志 (UnifiedLogger)
- 统一追踪 (UnifiedTracer)

Usage:
    from polaris.kernelone.trace import (
        get_context,
        new_trace,
        create_task_with_context,
        get_logger,
        get_tracer,
    )

    # 创建新的追踪上下文
    with new_trace("my-operation") as ctx:
        logger = get_logger(__name__)
        logger.info("Operation started", extra={"trace_id": ctx.trace_id})

        # 异步任务自动继承上下文
        await create_task_with_context(my_async_task())
"""

from .async_utils import (
    copy_context_to_task,
    create_task_with_context,
    traced_task,
)
from .context import (
    ContextManager,
    PolarisContext,
    get_context,
    get_trace_id,
    new_trace,
)
from .logger import (
    JSONFormatter,
    UnifiedLogger,
    configure_logging,
    get_logger,
)
from .tracer import (
    Span,
    UnifiedTracer,
    get_tracer,
)

__all__ = [
    "ContextManager",
    "JSONFormatter",
    # Context
    "PolarisContext",
    "Span",
    # Logger
    "UnifiedLogger",
    # Tracer
    "UnifiedTracer",
    "configure_logging",
    "copy_context_to_task",
    # Async utils
    "create_task_with_context",
    "get_context",
    "get_logger",
    "get_trace_id",
    "get_tracer",
    "new_trace",
    "traced_task",
]
