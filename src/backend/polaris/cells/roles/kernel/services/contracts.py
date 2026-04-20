"""LLM Invoker Service Protocols - LLM调用服务协议

定义 RoleExecutionKernel Facade 所需的服务层协议。
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

from polaris.kernelone.llm.shared_contracts import AIResponse, Usage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.kernelone.llm.engine.contracts import AIRequest

T = TypeVar("T")


@runtime_checkable
class ILLMInvoker(Protocol):
    """LLM调用器协议

    提供统一的LLM调用接口，支持同步、流式和结构化输出模式。
    """

    async def invoke(
        self,
        request: AIRequest,
        timeout_seconds: float | None = None,
    ) -> RoleInvokeResult:
        """执行非流式LLM调用

        Args:
            request: AI请求规范
            timeout_seconds: 可选超时覆盖

        Returns:
            RoleInvokeResult，包含响应内容和元数据
        """
        ...

    def invoke_stream(
        self,
        request: AIRequest,
        timeout_seconds: float | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """执行流式LLM调用

        Args:
            request: AI请求规范
            timeout_seconds: 可选超时覆盖

        Yields:
            StreamEvent对象，包含标准化的事件结构
        """
        ...

    async def invoke_structured(
        self,
        request: AIRequest,
        response_model: type[T],
        max_validation_retries: int = 2,
        timeout_seconds: float | None = None,
    ) -> StructuredResult:
        """执行结构化LLM调用（带验证）

        Args:
            request: AI请求规范
            response_model: Pydantic模型类，用于响应验证
            max_validation_retries: 验证失败时的最大重试次数
            timeout_seconds: 可选超时覆盖

        Returns:
            StructuredResult，包含验证后的数据
        """
        ...


@runtime_checkable
class IToolExecutor(Protocol):
    """工具执行器协议 [DEPRECATED - P0-NEW-017 已废弃]

    此 Protocol 已被 P0-NEW-017 修复方案废弃。
    请使用 CellToolExecutorPort:

        from polaris.kernelone.llm.contracts import CellToolExecutorPort

    本类将在后续版本中移除。

    负责工具调用执行、写预算分割、事件发射等。
    """

    async def execute_single(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行单个工具调用

        Args:
            tool_name: 工具名称
            args: 工具参数
            context: 可选执行上下文

        Returns:
            工具执行结果字典
        """
        ...

    async def execute_batch(
        self,
        tool_calls: list[tuple[str, dict[str, Any]]],
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """批量执行工具调用

        Args:
            tool_calls: 工具调用列表，每项为 (tool_name, args)
            context: 可选执行上下文

        Returns:
            工具执行结果列表
        """
        ...

    def split_by_write_budget(
        self,
        role_id: str,
        tool_calls: list[Any],
    ) -> tuple[list[Any], list[Any], int]:
        """按写预算分割工具调用

        Args:
            role_id: 角色标识
            tool_calls: 工具调用列表

        Returns:
            (executable_calls, deferred_calls, write_limit)
        """
        ...


@runtime_checkable
class IPromptBuilder(Protocol):
    """提示词构建器协议"""

    def build_system_prompt(
        self,
        profile: Any,
        prompt_appendix: str,
        domain: str = "code",
        message: str = "",
    ) -> str:
        """构建系统提示词"""
        ...

    def build_fingerprint(self, profile: Any, prompt_appendix: str) -> Any:
        """构建提示词指纹"""
        ...

    def build_retry_prompt(
        self,
        base_system_prompt: str,
        quality_result: dict[str, Any] | None,
        attempt: int,
    ) -> str:
        """构建重试提示词"""
        ...


@runtime_checkable
class IOutputParser(Protocol):
    """输出解析器协议"""

    def parse_tool_calls(
        self,
        content: str,
        native_tool_calls: list[dict[str, Any]] | None,
        native_provider: str,
    ) -> list[Any]:
        """解析工具调用"""
        ...

    def extract_json(self, content: str) -> dict[str, Any] | None:
        """从内容中提取JSON"""
        ...


@runtime_checkable
class IQualityChecker(Protocol):
    """质量检查器协议"""

    def validate_output(
        self,
        output: str,
        profile: Any,
        pre_validated_data: dict[str, Any] | None,
        instructor_validated: bool,
    ) -> Any:
        """验证输出质量"""
        ...


@runtime_checkable
class IEventEmitter(Protocol):
    """事件发射器协议"""

    def emit_runtime_llm_event(
        self,
        *,
        event_type: str,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        publish_realtime: bool,
        workspace: str,
        **kwargs: Any,
    ) -> None:
        """发射运行时LLM事件"""
        ...

    def resolve_observer_run_id(self, role: str, run_id: str | None) -> str:
        """解析观察器运行ID"""
        ...


# Data classes for results


class RoleInvokeResult:
    """非流式LLM调用结果（角色内核专用）

    这是角色内核层的调用结果契约，与 kernelone/llm/types.py 中的
    InvokeResult（ok/output/error 模式）不同。
    """

    def __init__(
        self,
        content: str,
        structured: dict[str, Any] | None = None,
        usage: Usage | None = None,
        latency_ms: int = 0,
        model: str | None = None,
        provider_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.structured = structured
        self.usage = usage or Usage()
        self.latency_ms = latency_ms
        self.model = model
        self.provider_id = provider_id
        self.trace_id = trace_id
        self.metadata = metadata or {}

    @property
    def is_success(self) -> bool:
        """Check if invocation was successful.

        Non-streaming always returns success or raises an exception.
        """
        return True


class StructuredResult:
    """结构化LLM调用结果"""

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        raw_content: str = "",
        usage: Usage | None = None,
        latency_ms: int = 0,
        validation_errors: list[str] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.data = data or {}
        self.raw_content = raw_content
        self.usage = usage or Usage()
        self.latency_ms = latency_ms
        self.validation_errors = validation_errors or []
        self.trace_id = trace_id

    @property
    def is_success(self) -> bool:
        return not self.validation_errors


class StreamEvent:
    """流事件"""

    def __init__(
        self,
        event_type: str,
        content: str = "",
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        tool_call_id: str = "",
        tool_result: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        error: str = "",
        done: bool = False,
    ) -> None:
        self.event_type = event_type
        self.content = content
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        self.tool_call_id = tool_call_id
        self.tool_result = tool_result or {}
        self.metadata = metadata or {}
        self.error = error
        self.done = done


# Backward compatibility alias: IToolExecutor -> CellToolExecutorPort
# (P0-NEW-017: Deprecated, will be removed in future. Use CellToolExecutorPort.)
try:
    from polaris.kernelone.llm.contracts import CellToolExecutorPort

    # Emit deprecation warning when IToolExecutor is used
    def __getattr__(name: str) -> Any:
        if name == "IToolExecutor":
            warnings.warn(
                "IToolExecutor is deprecated, use CellToolExecutorPort from polaris.kernelone.llm.contracts instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Return the Protocol class itself for isinstance checks
            return IToolExecutor
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
except ImportError:
    CellToolExecutorPort = None  # type: ignore[assignment, misc]


__all__ = [
    "CellToolExecutorPort",
    "IEventEmitter",
    "ILLMInvoker",
    "IOutputParser",
    "IPromptBuilder",
    "IQualityChecker",
    "IToolExecutor",
    "RoleInvokeResult",
    "StreamEvent",
    "StructuredResult",
]
