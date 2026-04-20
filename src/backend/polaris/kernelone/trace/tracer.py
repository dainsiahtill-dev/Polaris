"""统一追踪器

整合所有追踪系统，提供统一的追踪API。

解决现有追踪系统的问题：
- 4个独立追踪系统并存（tracing_context, telemetry, event_stream, task_trace）
- trace_id格式不统一
- LLM调用与HTTP请求trace_id不关联
- span上下文未跨异步边界传递
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .context import ContextManager, PolarisContext, _generate_id, get_context
from .logger import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Generator


class SpanStatus(str, Enum):
    """Span状态"""

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class SpanEvent:
    """Span事件"""

    name: str
    timestamp: datetime
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "timestamp": self.timestamp.isoformat(),
            "attributes": self.attributes,
        }


@dataclass
class Span:
    """追踪Span

    表示一个追踪单元，包含开始/结束时间、标签、事件等。
    """

    span_id: str
    name: str
    trace_id: str
    parent_span_id: str | None = None
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    duration_ms: float | None = None
    status: SpanStatus = SpanStatus.OK
    status_message: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "span_id": self.span_id,
            "name": self.name,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "status_message": self.status_message,
            "tags": self.tags,
            "events": [e.to_dict() for e in self.events],
        }

    def set_tag(self, key: str, value: Any) -> None:
        """设置标签"""
        self.tags[key] = value

    def add_event(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """添加事件"""
        self.events.append(
            SpanEvent(
                name=name,
                timestamp=datetime.now(timezone.utc),
                attributes=attributes or {},
            )
        )

    def set_status(
        self,
        status: SpanStatus,
        message: str | None = None,
    ) -> None:
        """设置状态"""
        self.status = status
        self.status_message = message

    def finish(self, end_time: datetime | None = None) -> None:
        """结束span"""
        self.end_time = end_time or datetime.now(timezone.utc)
        self.duration_ms = (self.end_time - self.start_time).total_seconds() * 1000


class TraceRecorder:
    """追踪记录器

    负责记录和管理span，支持内存存储和文件持久化。
    """

    def __init__(self, max_spans: int = 1000) -> None:
        self.max_spans = max_spans
        self._spans: list[Span] = []
        self._lock = threading.RLock()
        self._logger = get_logger(__name__)
        self._storage_path: Path | None = None

    def set_storage_path(self, path: str | Path) -> None:
        """设置持久化存储路径"""
        self._storage_path = Path(path)
        self._storage_path.mkdir(parents=True, exist_ok=True)

    def record_span(self, span: Span) -> None:
        """记录span"""
        with self._lock:
            for index, existing in enumerate(self._spans):
                if existing.span_id == span.span_id:
                    self._spans[index] = span
                    break
            else:
                self._spans.append(span)

            # 限制内存中的span数量
            removed: list[Span] = []
            if len(self._spans) > self.max_spans:
                overflow = len(self._spans) - self.max_spans
                removed = self._spans[:overflow]
                self._spans = self._spans[overflow:]

        for old_span in removed:
            self._persist_span(old_span)

    def _persist_span(self, span: Span) -> None:
        """持久化span到文件"""
        if not self._storage_path:
            return

        try:
            date_str = span.start_time.strftime("%Y-%m-%d")
            file_path = self._storage_path / f"spans_{date_str}.jsonl"
            payload = json.dumps(span.to_dict(), ensure_ascii=False) + "\n"
            from polaris.kernelone.fs.registry import get_default_adapter

            get_default_adapter().append_text(str(file_path), payload, encoding="utf-8")
        except (RuntimeError, ValueError) as e:
            self._logger.warning(f"Failed to persist span: {e}")

    def get_trace(self, trace_id: str) -> list[Span]:
        """获取指定trace的所有span"""
        with self._lock:
            return [s for s in list(self._spans) if s.trace_id == trace_id]

    def get_current_span(self) -> Span | None:
        """获取当前span"""
        ctx = get_context()
        if ctx.span_stack:
            span_id = ctx.span_stack[-1].get("span_id")
            with self._lock:
                for span in reversed(self._spans):
                    if span.span_id == span_id:
                        return span
        return None

    def get_spans_by_name(self, name: str) -> list[Span]:
        """按名称获取span"""
        with self._lock:
            return [s for s in list(self._spans) if s.name == name]

    def flush(self) -> None:
        """刷新所有span到持久化存储"""
        with self._lock:
            spans = list(self._spans)
        for span in spans:
            self._persist_span(span)


class UnifiedTracer:
    """统一追踪器

    整合所有追踪系统的统一API。

    Example:
        from polaris.kernelone.trace import get_tracer

        tracer = get_tracer()

        # 方式1: 使用上下文管理器
        with tracer.span("operation") as span:
            span.set_tag("key", "value")
            span.add_event("checkpoint")
            result = do_operation()

        # 方式2: 手动管理
        span = tracer.start_span("operation")
        try:
            result = do_operation()
            span.set_tag("success", True)
        except (RuntimeError, ValueError) as e:
            span.set_status(SpanStatus.ERROR, str(e))
            raise
        finally:
            tracer.end_span(span)
    """

    def __init__(self, recorder: TraceRecorder | None = None) -> None:
        self._recorder = recorder or TraceRecorder()
        self._logger = get_logger(__name__)

    @property
    def recorder(self) -> TraceRecorder:
        return self._recorder

    def start_span(
        self,
        name: str,
        *,
        tags: dict[str, Any] | None = None,
        parent_span_id: str | None = None,
        trace_id: str | None = None,
    ) -> Span:
        """开始新的span

        Args:
            name: span名称
            tags: 初始标签
            parent_span_id: 父span ID，不提供则使用当前span
            trace_id: trace ID，不提供则使用当前上下文的trace_id

        Returns:
            新创建的Span对象
        """
        ctx = get_context()

        # 确定trace_id
        if trace_id is None:
            trace_id = ctx.trace_id

        # 确定父span
        if parent_span_id is None and ctx.span_stack:
            parent_span_id = ctx.span_stack[-1].get("span_id")

        span = Span(
            span_id=_generate_id("span"),
            name=name,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            tags=tags or {},
        )

        # 更新上下文
        new_ctx = ctx.with_span(name, span.span_id)
        ContextManager.set_context(new_ctx)

        # 记录span
        self._recorder.record_span(span)

        # 记录开始
        self._logger.debug(
            f"Span started: {name}",
            span_id=span.span_id,
            trace_id=span.trace_id,
            parent_span_id=parent_span_id,
        )

        return span

    def end_span(
        self,
        span: Span,
        status: SpanStatus | None = None,
        status_message: str | None = None,
    ) -> None:
        """结束span

        Args:
            span: 要结束的span
            status: 可选的状态覆盖
            status_message: 可选的状态消息
        """
        if status:
            span.set_status(status, status_message)

        span.finish()
        self._pop_span_from_context(span)

        # 记录结束
        self._logger.debug(
            f"Span ended: {span.name}",
            span_id=span.span_id,
            trace_id=span.trace_id,
            duration_ms=span.duration_ms,
            status=span.status.value,
        )

    def _pop_span_from_context(self, span: Span) -> None:
        """Remove a completed span from the current context stack."""
        ctx = get_context()
        if not ctx.span_stack:
            return

        if ctx.span_stack[-1].get("span_id") == span.span_id:
            next_stack = list(ctx.span_stack[:-1])
        else:
            next_stack = [item for item in ctx.span_stack if item.get("span_id") != span.span_id]

        ContextManager.set_context(
            PolarisContext(
                trace_id=ctx.trace_id,
                run_id=ctx.run_id,
                request_id=ctx.request_id,
                workflow_id=ctx.workflow_id,
                task_id=ctx.task_id,
                workspace=ctx.workspace,
                span_stack=next_stack,
                metadata=dict(ctx.metadata),
            )
        )

    @contextmanager
    def span(
        self,
        name: str,
        *,
        tags: dict[str, Any] | None = None,
        parent_span_id: str | None = None,
    ) -> Generator[Span, None, None]:
        """上下文管理器方式的span追踪

        Example:
            with tracer.span("operation", tags={"key": "value"}) as span:
                result = do_operation()
                span.set_tag("result", result)
        """
        span = self.start_span(name, tags=tags, parent_span_id=parent_span_id)
        try:
            yield span
            if span.status == SpanStatus.UNKNOWN:
                span.set_status(SpanStatus.OK)
        except (RuntimeError, ValueError) as e:
            span.set_status(SpanStatus.ERROR, str(e))
            span.add_event(
                "exception",
                attributes={
                    "type": type(e).__name__,
                    "message": str(e),
                },
            )
            raise
        finally:
            self.end_span(span)

    def record_event(
        self,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """记录事件到当前span

        Args:
            name: 事件名称
            attributes: 事件属性
        """
        ctx = get_context()
        if not ctx.span_stack:
            # 没有当前span，记录到日志
            self._logger.info(
                f"Event (no active span): {name}",
                attributes=attributes,
            )
            return

        # 获取当前span
        span = self._recorder.get_current_span()
        if span:
            span.add_event(name, attributes)

        # 同时记录到日志
        self._logger.info(
            f"Event: {name}",
            trace_id=ctx.trace_id,
            span_id=ctx.span_stack[-1].get("span_id") if ctx.span_stack else None,
            attributes=attributes,
        )

    def record_error(
        self,
        error: Exception,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """记录错误

        Args:
            error: 异常对象
            context: 额外的上下文信息
        """
        ctx = get_context()

        # 如果有当前span，添加事件
        span = self._recorder.get_current_span()
        if span:
            span.set_status(SpanStatus.ERROR, str(error))
            span.add_event(
                "exception",
                attributes={
                    "type": type(error).__name__,
                    "message": str(error),
                    **(context or {}),
                },
            )

        # 记录到日志
        self._logger.error(
            f"Error: {type(error).__name__}: {error}",
            trace_id=ctx.trace_id,
            error_type=type(error).__name__,
            error_message=str(error),
            context=context,
            exc_info=True,
        )

    def get_current_trace(self) -> list[Span]:
        """获取当前trace的所有span"""
        ctx = get_context()
        return self._recorder.get_trace(ctx.trace_id)

    def get_trace(self, trace_id: str) -> list[Span]:
        """获取指定trace的所有span"""
        return self._recorder.get_trace(trace_id)

    def set_tag(self, key: str, value: Any) -> None:
        """为当前span设置标签"""
        span = self._recorder.get_current_span()
        if span:
            span.set_tag(key, value)

    def inject_context_into_headers(self) -> dict[str, str]:
        """将上下文注入HTTP头（用于服务间传播）

        Returns:
            HTTP头字典
        """
        ctx = get_context()
        headers = {
            "X-Trace-ID": ctx.trace_id,
        }
        if ctx.span_stack:
            headers["X-Span-ID"] = ctx.span_stack[-1].get("span_id", "")
        return headers

    def extract_context_from_headers(
        self,
        headers: dict[str, str],
        *,
        create_new_if_missing: bool = True,
    ) -> PolarisContext | None:
        """从HTTP头提取上下文

        Args:
            headers: HTTP头字典
            create_new_if_missing: 如果头中无上下文，是否创建新的

        Returns:
            PolarisContext或None
        """
        trace_id = headers.get("X-Trace-ID") or headers.get("x-trace-id")

        if not trace_id and not create_new_if_missing:
            return None

        if not trace_id:
            trace_id = _generate_id("trace")

        span_id = headers.get("X-Span-ID") or headers.get("x-span-id")

        ctx = PolarisContext(
            trace_id=trace_id,
        )

        if span_id:
            ctx = ctx.with_span("incoming-request", span_id)

        return ctx


# 全局追踪器实例
_tracer: UnifiedTracer | None = None


def get_tracer() -> UnifiedTracer:
    """获取全局追踪器实例

    Returns:
        UnifiedTracer实例
    """
    global _tracer
    if _tracer is None:
        _tracer = UnifiedTracer()
    return _tracer


def set_tracer(tracer: UnifiedTracer) -> None:
    """设置全局追踪器实例

    用于测试或需要自定义配置的场景。
    """
    global _tracer
    _tracer = tracer


# 便捷的装饰器


def traced(
    name: str | None = None,
    tags: dict[str, Any] | None = None,
) -> Callable:
    """装饰器：自动追踪函数执行

    Args:
        name: span名称，默认为函数名
        tags: 初始标签

    Example:
        @traced(name="data-processing", tags={"source": "api"})
        async def process_data(data: dict) -> Result:
            return await do_process(data)
    """

    def decorator(func: Callable) -> Callable:
        tracer = get_tracer()
        span_name = name or func.__qualname__

        if asyncio.iscoroutinefunction(func):

            async def async_wrapper(*args, **kwargs):
                with tracer.span(span_name, tags=tags or {}) as span:
                    span.set_tag("function", func.__qualname__)
                    span.set_tag("args_count", len(args))
                    span.set_tag("kwargs_keys", list(kwargs.keys()))
                    return await func(*args, **kwargs)

            return async_wrapper
        else:

            def sync_wrapper(*args, **kwargs):
                with tracer.span(span_name, tags=tags or {}) as span:
                    span.set_tag("function", func.__qualname__)
                    span.set_tag("args_count", len(args))
                    span.set_tag("kwargs_keys", list(kwargs.keys()))
                    return func(*args, **kwargs)

            return sync_wrapper

    return decorator


# 辅助函数


def get_current_span_id() -> str | None:
    """获取当前span ID"""
    ctx = get_context()
    if ctx.span_stack:
        return ctx.span_stack[-1].get("span_id")
    return None


def get_current_trace_id() -> str:
    """获取当前trace ID"""
    return get_context().trace_id
