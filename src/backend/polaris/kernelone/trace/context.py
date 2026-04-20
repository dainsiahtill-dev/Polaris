"""KernelOne trace context with Polaris-compatible env var names.

This module provides trace context management that is backward-compatible with
Polaris's POLARIS_* env var naming convention while also supporting
the generic KERNELONE_* prefix.

Environment variable propagation uses both naming schemes:
- Primary: KERNELONE_TRACE_ID, KERNELONE_RUN_ID, KERNELONE_WORKSPACE, etc.
- Legacy fallback: POLARIS_TRACE_ID, POLARIS_RUN_ID, POLARIS_WORKSPACE, etc.

This dual-naming ensures that KernelOne can run both as a standalone kernel
and as part of the Polaris application stack without environment reconfiguration.
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, Any

from polaris.kernelone import _runtime_config

if TYPE_CHECKING:
    from collections.abc import Callable, Generator


def _generate_id(prefix: str) -> str:
    """生成统一格式的ID: hp-{prefix}-{uuid8}"""
    return f"hp-{prefix}-{uuid.uuid4().hex[:8]}"


# contextvars 定义 - 协程本地存储
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_hp_trace_id", default=None)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_hp_run_id", default=None)
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_hp_request_id", default=None)
_workflow_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_hp_workflow_id", default=None)
_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_hp_task_id", default=None)
_span_stack: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "_hp_span_stack", default=None
)
_context_metadata: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_hp_context_metadata", default=None
)


def _current_span_stack() -> list[dict[str, Any]]:
    """Return an isolated copy of the current span stack."""
    return list(_span_stack.get() or [])


def _current_metadata() -> dict[str, Any]:
    """Return an isolated copy of the current metadata mapping."""
    return dict(_context_metadata.get() or {})


@dataclass(frozen=True)
class PolarisContext:
    """不可变的统一上下文对象

    作为所有可观测性数据的单一事实来源。
    不可变性确保上下文在传递过程中不会被意外修改。

    Attributes:
        trace_id: 追踪ID，贯穿整个请求生命周期
        run_id: 运行ID，标识一次完整的运行
        request_id: HTTP请求ID
        workflow_id: 工作流ID
        task_id: 任务ID
        workspace: 工作空间路径
        span_stack: Span调用栈
        metadata: 额外的元数据
    """

    trace_id: str
    run_id: str | None = None
    request_id: str | None = None
    workflow_id: str | None = None
    task_id: str | None = None
    workspace: str | None = None
    span_stack: list = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于日志/追踪）"""
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "workspace": self.workspace,
            "span_depth": len(self.span_stack),
            "metadata": self.metadata,
        }

    def to_env_vars(self) -> dict[str, str]:
        """转换为环境变量字典（用于子进程传递）。

        同时写入 KERNELONE_* 和 POLARIS_* 两套命名，
        保证 KernelOne 独立运行和 Polaris 集成都能正常工作。
        """
        env = {
            "KERNELONE_TRACE_ID": self.trace_id,
            "POLARIS_TRACE_ID": self.trace_id,
        }
        if self.run_id:
            env["KERNELONE_RUN_ID"] = self.run_id
            env["POLARIS_RUN_ID"] = self.run_id
        if self.request_id:
            env["KERNELONE_REQUEST_ID"] = self.request_id
            env["POLARIS_REQUEST_ID"] = self.request_id
        if self.workflow_id:
            env["KERNELONE_WORKFLOW_ID"] = self.workflow_id
            env["POLARIS_WORKFLOW_ID"] = self.workflow_id
        if self.task_id:
            env["KERNELONE_TASK_ID"] = self.task_id
            env["POLARIS_TASK_ID"] = self.task_id
        if self.workspace:
            env["KERNELONE_WORKSPACE"] = self.workspace
            env["POLARIS_WORKSPACE"] = self.workspace
        return env

    @classmethod
    def from_env_vars(cls) -> PolarisContext | None:
        """从环境变量恢复上下文。

        优先读取 KERNELONE_* env vars，回退到 POLARIS_*（向后兼容）。
        Uses _runtime_config for consistent fallback resolution.
        """
        # Use _runtime_config for KERNELONE_* / POLARIS_* fallback
        trace_id = _runtime_config.resolve_env_str("trace_id")
        if not trace_id:
            return None
        return cls(
            trace_id=trace_id,
            run_id=_runtime_config.resolve_env_str("run_id") or None,
            request_id=_runtime_config.resolve_env_str("request_id") or None,
            workflow_id=_runtime_config.resolve_env_str("workflow_id") or None,
            task_id=_runtime_config.resolve_env_str("task_id") or None,
            workspace=_runtime_config.resolve_env_str("workspace") or None,
        )

    def with_span(self, span_name: str, span_id: str | None = None) -> PolarisContext:
        """创建带有新span的上下文"""
        new_span = {
            "span_id": span_id or _generate_id("span"),
            "name": span_name,
            "parent_span_id": self.span_stack[-1]["span_id"] if self.span_stack else None,
        }
        new_stack = [*self.span_stack, new_span]
        return PolarisContext(
            trace_id=self.trace_id,
            run_id=self.run_id,
            request_id=self.request_id,
            workflow_id=self.workflow_id,
            task_id=self.task_id,
            workspace=self.workspace,
            span_stack=new_stack,
            metadata=self.metadata.copy(),
        )

    def with_metadata(self, **kwargs: Any) -> PolarisContext:
        """创建带有额外元数据的上下文"""
        new_metadata = {**self.metadata, **kwargs}
        return PolarisContext(
            trace_id=self.trace_id,
            run_id=self.run_id,
            request_id=self.request_id,
            workflow_id=self.workflow_id,
            task_id=self.task_id,
            workspace=self.workspace,
            span_stack=self.span_stack.copy(),
            metadata=new_metadata,
        )


class ContextManager:
    """上下文管理器 - 提供对contextvars的统一操作"""

    @staticmethod
    def get_current() -> PolarisContext:
        """获取当前上下文，如果不存在则创建新的

        注意：自动创建新trace_id是为了向后兼容，
        但理想情况下应该显式调用new_trace()创建上下文
        """
        trace_id = _trace_id.get()
        if trace_id is None:
            trace_id = _generate_id("auto")
            _trace_id.set(trace_id)
        return PolarisContext(
            trace_id=trace_id,
            run_id=_run_id.get(),
            request_id=_request_id.get(),
            workflow_id=_workflow_id.get(),
            task_id=_task_id.get(),
            span_stack=_current_span_stack(),
            metadata=_current_metadata(),
        )

    @staticmethod
    def set_context(ctx: PolarisContext) -> None:
        """设置当前上下文"""
        _trace_id.set(ctx.trace_id)
        _run_id.set(ctx.run_id)
        _request_id.set(ctx.request_id)
        _workflow_id.set(ctx.workflow_id)
        _task_id.set(ctx.task_id)
        _span_stack.set(list(ctx.span_stack))
        _context_metadata.set(dict(ctx.metadata))

    @staticmethod
    def clear() -> None:
        """清除当前上下文"""
        _trace_id.set(None)
        _run_id.set(None)
        _request_id.set(None)
        _workflow_id.set(None)
        _task_id.set(None)
        _span_stack.set([])
        _context_metadata.set({})

    @staticmethod
    @contextmanager
    def bind_context(ctx: PolarisContext) -> Generator[PolarisContext, None, None]:
        """绑定上下文到当前执行范围（上下文管理器）

        这是一个reentrant的上下文管理器，支持嵌套调用。
        它会正确保存和恢复之前的上下文状态。

        Example:
            with ContextManager.bind_context(ctx) as bound_ctx:
                # 在此范围内使用新上下文
                assert get_context().trace_id == ctx.trace_id
            # 离开范围后恢复之前的上下文
        """
        # 保存旧值
        old_trace = _trace_id.get()
        old_run = _run_id.get()
        old_request = _request_id.get()
        old_workflow = _workflow_id.get()
        old_task = _task_id.get()
        old_spans = _current_span_stack()
        old_metadata = _current_metadata()

        # 设置新值
        ContextManager.set_context(ctx)

        try:
            yield ctx
        finally:
            # 恢复旧值（即使是None也要恢复）
            _trace_id.set(old_trace)
            _run_id.set(old_run)
            _request_id.set(old_request)
            _workflow_id.set(old_workflow)
            _task_id.set(old_task)
            _span_stack.set(old_spans)
            _context_metadata.set(old_metadata)


# 便捷函数


def get_context() -> PolarisContext:
    """获取当前上下文"""
    return ContextManager.get_current()


def get_trace_id() -> str:
    """获取当前trace_id，如果不存在则创建新的

    这是为了向后兼容旧代码，新代码应该使用get_context().trace_id
    """
    trace_id = _trace_id.get()
    if trace_id is None:
        trace_id = _generate_id("auto")
        _trace_id.set(trace_id)
    return trace_id


@contextmanager
def new_trace(
    trace_type: str = "trace",
    run_id: str | None = None,
    request_id: str | None = None,
    workflow_id: str | None = None,
    task_id: str | None = None,
    workspace: str | None = None,
    metadata: dict[str, Any] | None = None,
    **extra_metadata: Any,
) -> Generator[PolarisContext, None, None]:
    """创建新的追踪上下文（上下文管理器）

    Args:
        trace_type: 追踪类型前缀 (req, run, task, llm等)
        run_id: 可选的运行ID
        request_id: 可选的请求ID
        workflow_id: 可选的工作流ID
        task_id: 可选的任务ID
        workspace: 可选的工作区路径
        metadata: 可选的元数据字典
        **extra_metadata: 额外的元数据键值对

    Example:
        with new_trace("api-request", request_id="req-123") as ctx:
            logger.info("Request started", extra=ctx.to_dict())
            # 处理请求...
        # 离开范围后自动恢复之前的上下文
    """
    merged_metadata = dict(metadata or {})
    merged_metadata.update(extra_metadata)

    ctx = PolarisContext(
        trace_id=_generate_id(trace_type),
        run_id=run_id,
        request_id=request_id,
        workflow_id=workflow_id,
        task_id=task_id,
        workspace=workspace,
        metadata=merged_metadata,
    )
    with ContextManager.bind_context(ctx) as bound_ctx:
        yield bound_ctx


@contextmanager
def inherit_context(
    parent_ctx: PolarisContext | None = None, **overrides: Any
) -> Generator[PolarisContext, None, None]:
    """继承父上下文并创建子上下文（上下文管理器）

    用于在保持trace_id的同时，创建新的子span或添加元数据。

    Args:
        parent_ctx: 父上下文，默认为当前上下文
        **overrides: 要覆盖的字段

    Example:
        with inherit_context(span_name="child-operation") as ctx:
            # 新上下文继承父trace_id，但添加新span
            assert ctx.trace_id == parent_ctx.trace_id
    """
    if parent_ctx is None:
        parent_ctx = get_context()

    # 应用覆盖
    kwargs = {
        "trace_id": overrides.get("trace_id", parent_ctx.trace_id),
        "run_id": overrides.get("run_id", parent_ctx.run_id),
        "request_id": overrides.get("request_id", parent_ctx.request_id),
        "workflow_id": overrides.get("workflow_id", parent_ctx.workflow_id),
        "task_id": overrides.get("task_id", parent_ctx.task_id),
        "workspace": overrides.get("workspace", parent_ctx.workspace),
        "span_stack": list(parent_ctx.span_stack),
        "metadata": {**parent_ctx.metadata, **overrides.get("metadata", {})},
    }

    # 如果提供了span_name，添加新span
    if "span_name" in overrides:
        kwargs["span_stack"] = kwargs["span_stack"] + [
            {
                "span_id": _generate_id("span"),
                "name": overrides["span_name"],
                "parent_span_id": kwargs["span_stack"][-1]["span_id"] if kwargs["span_stack"] else None,
            }
        ]

    ctx = PolarisContext(**kwargs)
    with ContextManager.bind_context(ctx) as bound_ctx:
        yield bound_ctx


def ensure_context(func: Callable) -> Callable:
    """装饰器：确保函数在有效的上下文中执行

    如果当前没有上下文，会自动创建一个新的。
    这是一个为了向后兼容的装饰器。
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if _trace_id.get() is None:
            # 没有上下文，创建一个
            _trace_id.set(_generate_id("auto"))
        return func(*args, **kwargs)

    return wrapper
