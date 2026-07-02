"""LLM Invoker Service Protocols - LLM调用服务协议

定义 RoleExecutionKernel Facade 所需的服务层协议。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

from polaris.kernelone.llm.contracts import CellToolExecutorPort
from polaris.kernelone.llm.engine.contracts import AIResponse, Usage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.turn_engine import AssistantRawContent
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
class IPromptBuilder(Protocol):
    """提示词构建器协议"""

    def build_system_prompt(
        self,
        profile: Any,
        prompt_appendix: str,
        domain: str = "code",
        message: str = "",
        include_working_memory_contract: bool = True,
        include_tool_policy: bool = True,
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
    """Output parser contract for role-kernel execution.

    Execution-facing callers must use the typed raw-content boundary. The
    concrete OutputParser may keep compatibility helpers for tests or
    non-execution callers, but this service protocol exposes only the
    executable parser stage so dependency-injected implementations cannot
    accidentally consume sanitized transcript text as tool-call input.
    """

    def parse_execution_tool_calls(
        self,
        content: AssistantRawContent,
        *,
        allowed_tool_names: Iterable[str] | None = None,
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_provider: str = "auto",
    ) -> list[Any]:
        """Parse executable native tool calls from typed raw assistant content."""
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


__all__ = [
    "CellToolExecutorPort",
    "IEventEmitter",
    "ILLMInvoker",
    "IOutputParser",
    "IPromptBuilder",
    "IQualityChecker",
    "RoleInvokeResult",
    "StreamEvent",
    "StructuredResult",
]
