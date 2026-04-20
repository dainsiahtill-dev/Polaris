"""Kernel Cell Service Layer Contracts - 服务层接口契约定义

本模块定义了Kernel Cell内部服务层的核心接口契约，包括：
1. LLM调用协议 (LLMInvokerProtocol)
2. 工具执行协议 (ToolExecutorProtocol) - DEPRECATED: 使用 KernelOne CellToolExecutorPort
3. 上下文组装协议 (ContextAssemblerProtocol)
4. 共享数据类 (请求/响应/事件/工具调用等)
5. 异常层次结构

设计原则：
- 使用Protocol实现依赖倒置，便于测试和Mock
- 使用@dataclass(frozen=True)确保不可变性
- 完整的类型注解支持mypy静态检查
- 与KernelOne现有契约保持兼容

P0-010 Unified Interface:
    工具执行接口已统一到 KernelOne:
    - KernelOne canonical: ToolExecutorPort (execute_call)
    - Cells layer: CellToolExecutorPort (execute)
    导入方式:
    from polaris.kernelone.llm.contracts import (
        ToolExecutorPort,       # KernelOne 规范接口
        CellToolExecutorPort,   # Cells 层统一接口
    )
    本文件的 ToolExecutorProtocol 将在后续版本移除。

P1-TYPE-004: ToolCall 统一
    - ToolCall: 从 polaris.kernelone.llm.contracts.tool 导入（canonical）

P1-TYPE-006: StreamEventType 说明
    - 本地定义已移除
    - canonical StreamEventType 位于 polaris.kernelone.llm.shared_contracts
    - 从该模块导入使用
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

# Import canonical types from KernelOne contracts
# P1-TYPE-004: This is the single source of truth for tool call representation
# P1-LLM-001: Usage is imported from shared_contracts (canonical)
from polaris.kernelone.errors import KernelOneError
from polaris.kernelone.llm.contracts.tool import ToolCall
from polaris.kernelone.llm.shared_contracts import StreamEventType, Usage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

# ═══════════════════════════════════════════════════════════════════════════════
# 异常层次结构
# ═══════════════════════════════════════════════════════════════════════════════


class KernelError(KernelOneError):
    """Kernel Cell服务层异常基类

    所有Kernel Cell内部异常都应继承此类，便于统一捕获和处理。

    P1-014 Migration:
        此异常现已迁移到继承自 canonical KernelOneError。
        - message: 错误消息（继承自 KernelOneError._message）
        - code: 错误码（继承自 KernelOneError.code，默认 "KERNEL_ERROR"）
        - details: 详细信息（继承自 KernelOneError.details）
        - retryable: 是否可重试（继承自 KernelOneError.retryable）
        - error_code: 向后兼容别名（映射到 code）

    Intent:
        - KernelError: Kernel Cell 服务层业务异常（LLM调用、工具执行、策略控制）
        - KernelOneError: KernelOne 运行时基础层异常（基础设施、事件、审计）
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "KERNEL_ERROR",
        error_code: str | None = None,
        context: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        # Map both code and error_code to same canonical value
        effective_code = error_code if error_code is not None else code
        super().__init__(
            message,
            code=effective_code,
            details=context or {},
            retryable=retryable,
        )
        # Backward-compat: error_code alias for code
        self.error_code = effective_code

    def __str__(self) -> str:
        if self.error_code:
            return f"[{self.error_code}] {self._message} (context: {self.details})"
        return f"[{self.code}] {self._message}"


class LLMError(KernelError):
    """LLM调用相关异常

    包括：
    - 网络超时/连接失败
    - 模型返回错误
    - Token超限
    - 内容安全拦截
    """

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        model: str | None = None,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="LLM_ERROR", context=context, retryable=retryable)
        self.provider_id = provider_id
        self.model = model


class ToolError(KernelError):
    """工具执行相关异常

    包括：
    - 工具未找到
    - 参数验证失败
    - 执行超时
    - 执行结果异常
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str | None = None,
        call_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="TOOL_ERROR", context=context)
        self.tool_name = tool_name
        self.call_id = call_id


class PolicyError(KernelError):
    """策略层违规异常

    包括：
    - 工具白名单拒绝
    - 预算超限
    - 安全策略拦截
    - 审批被拒绝
    """

    def __init__(
        self,
        message: str,
        *,
        policy_type: str | None = None,
        violation_details: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="POLICY_ERROR", context=context)
        self.policy_type = policy_type
        self.violation_details = violation_details or {}


# ═══════════════════════════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════════════════════════


# StreamEventType is now imported from polaris.kernelone.llm.shared_contracts


class ToolExecutionStatus(Enum):
    """工具执行状态枚举"""

    PENDING = auto()  # 等待执行
    RUNNING = auto()  # 执行中
    SUCCESS = auto()  # 执行成功
    ERROR = auto()  # 执行失败
    TIMEOUT = auto()  # 执行超时
    ABORTED = auto()  # 被中止
    REJECTED = auto()  # 被策略拒绝


class ContextCompressionStrategy(Enum):
    """上下文压缩策略枚举"""

    NONE = "none"  # 不压缩
    SUMMARY = "summary"  # 摘要压缩
    SELECTIVE_DROP = "selective_drop"  # 选择性丢弃
    SLIDING_WINDOW = "sliding_window"  # 滑动窗口


# ═══════════════════════════════════════════════════════════════════════════════
# 共享数据类
# ═══════════════════════════════════════════════════════════════════════════════
# Usage is now imported from polaris.kernelone.llm.shared_contracts (canonical)


@dataclass(frozen=True)
class LLMRequest:
    """LLM调用请求

    Attributes:
        messages: 对话消息列表，格式为[{"role": str, "content": str}]
        model: 模型标识符
        provider_id: Provider标识符
        temperature: 采样温度
        max_tokens: 最大输出token数
        tools: 可用工具定义列表
        tool_choice: 工具选择策略
        metadata: 扩展元数据
    """

    messages: Sequence[dict[str, str]]
    model: str | None = None
    provider_id: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: Sequence[dict[str, Any]] = field(default_factory=tuple)
    tool_choice: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    """LLM调用响应

    Attributes:
        content: 响应内容
        tool_calls: 原始工具调用列表（来自provider的原始格式）
        usage: Token使用量
        model: 实际使用的模型
        finish_reason: 完成原因
        thinking: 推理内容（如适用）
        metadata: 扩展元数据
        error: 错误信息（如果有）
    """

    content: str = ""
    tool_calls: Sequence[dict[str, Any]] = field(default_factory=tuple)  # Raw format from provider
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    finish_reason: str | None = None
    thinking: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ToolCall is now imported from polaris.kernelone.llm.contracts.tool
# This is the canonical definition with fields: id, name, arguments, source, raw, parse_error


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果

    Attributes:
        call_id: 对应ToolCall的id
        status: 执行状态
        output: 执行输出
        error: 错误信息（如失败）
        execution_time_ms: 执行耗时（毫秒）
        metadata: 扩展元数据
    """

    call_id: str
    status: ToolExecutionStatus
    output: Any = None
    error: str | None = None
    execution_time_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StreamEvent:
    """流式事件

    Attributes:
        event_type: 事件类型
        content: 事件内容
        tool_call: 工具调用（如适用）
        tool_result: 工具结果（如适用）
        metadata: 扩展元数据
        is_final: 是否为最终事件
    """

    event_type: StreamEventType
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    is_final: bool = False


@dataclass(frozen=True)
class ContextRequest:
    """上下文组装请求

    Attributes:
        user_message: 用户消息
        conversation_history: 历史对话记录
        system_prompt: 系统提示词
        context_override: 上下文覆盖配置
        compression_strategy: 压缩策略
        max_tokens: 最大token限制
    """

    user_message: str
    conversation_history: Sequence[dict[str, Any]] = field(default_factory=tuple)
    system_prompt: str | None = None
    context_override: dict[str, Any] = field(default_factory=dict)
    compression_strategy: ContextCompressionStrategy = ContextCompressionStrategy.NONE
    max_tokens: int = 32768


@dataclass(frozen=True)
class ContextResult:
    """上下文组装结果

    Attributes:
        messages: 组装后的消息列表
        original_tokens: 原始token数
        compressed_tokens: 压缩后token数
        compression_applied: 是否应用了压缩
        compression_notes: 压缩说明
        metadata: 扩展元数据
    """

    messages: Sequence[dict[str, str]]
    original_tokens: int = 0
    compressed_tokens: int = 0
    compression_applied: bool = False
    compression_notes: Sequence[str] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# TypedDict定义（用于JSON序列化场景）
# ═══════════════════════════════════════════════════════════════════════════════


class ToolCallDict(TypedDict, total=False):
    """工具调用的TypedDict表示"""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResultDict(TypedDict, total=False):
    """工具结果的TypedDict表示"""

    call_id: str
    status: str
    output: Any
    error: str | None
    execution_time_ms: int


class StreamEventDict(TypedDict, total=False):
    """流式事件的TypedDict表示"""

    event_type: str
    content: str
    tool_call: ToolCallDict | None
    tool_result: ToolResultDict | None
    is_final: bool


# ═══════════════════════════════════════════════════════════════════════════════
# Protocol定义
# ═══════════════════════════════════════════════════════════════════════════════


@runtime_checkable
class LLMInvokerProtocol(Protocol):
    """LLM调用器协议

    定义了与LLM交互的标准接口。实现类负责：
    - 管理Provider选择和模型路由
    - 处理非流式和流式调用
    - 错误处理和重试逻辑
    - Token使用量追踪

    Example:
        >>> class MyLLMInvoker:
        ...     async def invoke(self, request: LLMRequest) -> LLMResponse:
        ...         # 实现调用逻辑
        ...         pass
        ...
        ...     async def invoke_stream(
        ...         self, request: LLMRequest
        ...     ) -> AsyncGenerator[StreamEvent, None]:
        ...         # 实现流式调用逻辑
        ...         pass
    """

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """非流式调用LLM

        Args:
            request: LLM调用请求

        Returns:
            LLM调用响应

        Raises:
            LLMError: 当调用失败时抛出
        """
        ...

    async def invoke_stream(
        self,
        request: LLMRequest,
    ) -> AsyncGenerator[StreamEvent, None]:
        """流式调用LLM

        Args:
            request: LLM调用请求

        Yields:
            StreamEvent: 流式事件序列

        Raises:
            LLMError: 当调用失败时抛出
        """
        ...


@runtime_checkable
class ToolExecutorProtocol(Protocol):
    """工具执行器协议 [DEPRECATED - P0-010 已废弃, P0-NEW-017 统一命名]

    此 Protocol 已被 P0-010 修复方案废弃。
    请使用 CellToolExecutorPort:

        from polaris.kernelone.llm.contracts import CellToolExecutorPort

    本类将在后续版本中移除。
    """

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        """执行单个工具调用 [DEPRECATED]

        Args:
            tool_call: 工具调用定义

        Returns:
            工具执行结果

        Raises:
            ToolError: 当执行失败时抛出
        """
        ...

    async def execute_batch(
        self,
        tool_calls: Sequence[ToolCall],
    ) -> Sequence[ToolResult]:
        """批量执行工具调用 [DEPRECATED]

        实现类应决定执行策略：
        - 只读工具可并行执行
        - 写工具应串行执行
        - 异步工具返回pending状态

        Args:
            tool_calls: 工具调用列表

        Returns:
            工具执行结果列表（与输入顺序一致）

        Raises:
            ToolError: 当批量执行失败时抛出
        """
        ...

    def validate_tool_call(self, tool_call: ToolCall) -> tuple[bool, str | None]:
        """验证工具调用是否合法 [DEPRECATED]

        Args:
            tool_call: 工具调用定义

        Returns:
            (是否合法, 错误信息)
        """
        ...


@runtime_checkable
class ContextAssemblerProtocol(Protocol):
    """上下文组装器协议

    定义了对话上下文组装的标准接口。实现类负责：
    - 系统提示词管理
    - 历史消息组装
    - Token预算管理
    - 上下文压缩策略

    Example:
        >>> class MyContextAssembler:
        ...     async def assemble(
        ...         self, request: ContextRequest
        ...     ) -> ContextResult:
        ...         # 实现上下文组装逻辑
        ...         pass
        ...
        ...     def estimate_tokens(
        ...         self, messages: Sequence[dict[str, str]]
        ...     ) -> int:
        ...         # 实现token估算
        ...         pass
    """

    async def assemble(self, request: ContextRequest) -> ContextResult:
        """组装对话上下文

        Args:
            request: 上下文组装请求

        Returns:
            组装后的上下文结果

        Raises:
            KernelError: 当组装失败时抛出
        """
        ...

    def estimate_tokens(self, messages: Sequence[dict[str, str]]) -> int:
        """估算消息列表的token数

        Args:
            messages: 消息列表

        Returns:
            估算的token数
        """
        ...

    def compress_context(
        self,
        messages: Sequence[dict[str, str]],
        strategy: ContextCompressionStrategy,
        target_tokens: int,
    ) -> ContextResult:
        """压缩上下文至目标token数

        Args:
            messages: 原始消息列表
            strategy: 压缩策略
            target_tokens: 目标token数

        Returns:
            压缩后的上下文结果
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# 复合服务协议（可选，用于需要组合能力的场景）
# ═══════════════════════════════════════════════════════════════════════════════


@runtime_checkable
class KernelServiceProtocol(Protocol):
    """Kernel复合服务协议

    组合了LLM调用、工具执行和上下文组装能力的完整服务接口。
    适用于需要统一服务入口的场景。
    """

    @property
    def llm_invoker(self) -> LLMInvokerProtocol:
        """获取LLM调用器"""
        ...

    @property
    def tool_executor(self) -> ToolExecutorProtocol:
        """获取工具执行器"""
        ...

    @property
    def context_assembler(self) -> ContextAssemblerProtocol:
        """获取上下文组装器"""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# 导出列表
# ═══════════════════════════════════════════════════════════════════════════════

# Backward compatibility alias: ToolExecutorProtocol -> CellToolExecutorPort
# (P0-NEW-017: Deprecated, will be removed in future. Use CellToolExecutorPort.)
try:
    from polaris.kernelone.llm.contracts import CellToolExecutorPort

    def __getattr__(name: str) -> Any:
        if name == "ToolExecutorProtocol":
            warnings.warn(
                "ToolExecutorProtocol is deprecated, use CellToolExecutorPort from "
                "polaris.kernelone.llm.contracts instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            return ToolExecutorProtocol
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
except ImportError:
    CellToolExecutorPort = None  # type: ignore[assignment, misc]


__all__ = [
    "CellToolExecutorPort",
    "ContextAssemblerProtocol",
    "ContextCompressionStrategy",
    "ContextRequest",
    "ContextResult",
    # 异常类
    "KernelError",
    "KernelServiceProtocol",
    "LLMError",
    # Protocol
    "LLMInvokerProtocol",
    "LLMRequest",
    "LLMResponse",
    "PolicyError",
    "StreamEvent",
    "StreamEventDict",
    # 枚举
    "StreamEventType",
    "ToolCall",
    # TypedDict
    "ToolCallDict",
    "ToolError",
    "ToolExecutionStatus",
    "ToolExecutorProtocol",
    "ToolResult",
    "ToolResultDict",
    # 数据类
    "Usage",
]
