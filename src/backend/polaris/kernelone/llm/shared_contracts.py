"""Shared LLM contracts reused by multiple KernelOne subsystems.

This module is the single source of truth for the common request/response
contracts that both ``engine`` and ``toolkit`` depend on. It exists to avoid
contract drift and circular imports between the two packages.

---
边界规则 (TypedDict vs Dataclass):
- TypedDict: API边界契约（序列化/反序列化）
- dataclass: 内部数据结构和运行时对象

注意：
- AIRequest/AIResponse 是 dataclass，包含工厂方法和业务逻辑
- HTTP边界使用 Pydantic BaseModel（如 stream_router.py 中的 StreamChatRequest）
- 本模块的 dataclass 用于内部 Provider 接口契约
---
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol

from polaris.kernelone.errors import ErrorCategory


class TaskType(str, Enum):
    """Task categories supported by the KernelOne LLM runtime."""

    DIALOGUE = "dialogue"
    INTERVIEW = "interview"
    EVALUATION = "evaluation"
    READINESS = "readiness"
    GENERATION = "generation"
    CLASSIFICATION = "classification"


class StreamEventType(str, Enum):
    """Event kinds emitted by streaming responses.

    Tool lifecycle events (TOOL_START/TOOL_END) provide boundaries for
    frontend rendering of tool call progress. These complement TOOL_CALL
    which carries the actual tool invocation payload.
    """

    CHUNK = "chunk"
    REASONING_CHUNK = "reasoning_chunk"
    TOOL_START = "tool_start"  # Tool execution began
    TOOL_CALL = "tool_call"  # Structured tool invocation payload
    TOOL_END = "tool_end"  # Tool execution completed
    TOOL_RESULT = "tool_result"
    META = "meta"
    COMPLETE = "complete"
    ERROR = "error"

    @classmethod
    def from_string(cls, value: str) -> StreamEventType:
        """Convert a string to a StreamEventType.

        This method enables compatibility with downstream layers that emit
        event type as a string (e.g., from kernel events).
        """
        try:
            return cls(value)
        except ValueError:
            return cls.ERROR


@dataclass
class ModelSpec:
    """Model capability and context specification."""

    provider_id: str
    provider_type: str
    model: str
    max_context_tokens: int = 32768
    max_output_tokens: int = 4096
    tokenizer: str = "char_estimate"
    supports_tools: bool = False
    supports_json_schema: bool = False
    supports_vision: bool = False
    cost_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompressionResult:
    """Context compression decision details."""

    compressed_input: str
    original_tokens: int
    compressed_tokens: int
    strategy: str = "none"
    quality_flag: str = "ok"
    drop_ratio: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TokenBudgetDecision:
    """Token budget enforcement result."""

    allowed: bool
    max_context_tokens: int
    allowed_prompt_tokens: int
    requested_prompt_tokens: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    compression_applied: bool = False
    compression: CompressionResult | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "allowed": self.allowed,
            "max_context_tokens": self.max_context_tokens,
            "allowed_prompt_tokens": self.allowed_prompt_tokens,
            "requested_prompt_tokens": self.requested_prompt_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "compression_applied": self.compression_applied,
        }
        if self.compression is not None:
            payload["compression"] = self.compression.to_dict()
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class AIRequest:
    """Canonical LLM request contract."""

    task_type: TaskType
    role: str
    provider_id: str | None = None
    model: str | None = None
    input: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type.value,
            "role": self.role,
            "provider_id": self.provider_id,
            "model": self.model,
            "input": self.input,
            "options": self.options,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AIRequest:
        return cls(
            task_type=TaskType(data.get("task_type", "generation")),
            role=str(data.get("role", "") or ""),
            provider_id=str(data.get("provider_id", "") or "") or None,
            model=str(data.get("model", "") or "") or None,
            input=str(data.get("input", "") or ""),
            options=dict(data.get("options") or {}),
            context=dict(data.get("context") or {}),
        )


@dataclass
class Usage:
    """Token usage statistics."""

    cached_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    prompt_chars: int = 0
    completion_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Usage:
        payload = dict(data or {})
        return cls(
            cached_tokens=int(payload.get("cached_tokens", 0) or 0),
            prompt_tokens=int(payload.get("prompt_tokens", 0) or 0),
            completion_tokens=int(payload.get("completion_tokens", 0) or 0),
            total_tokens=int(payload.get("total_tokens", 0) or 0),
            estimated=bool(payload.get("estimated", False)),
            prompt_chars=int(payload.get("prompt_chars", 0) or 0),
            completion_chars=int(payload.get("completion_chars", 0) or 0),
        )

    @classmethod
    def estimate(cls, prompt: str, output: str) -> Usage:
        prompt_chars = len(prompt or "")
        completion_chars = len(output or "")
        prompt_tokens = max(1, prompt_chars // 4) if prompt_chars else 0
        completion_tokens = max(1, completion_chars // 4) if completion_chars else 0
        return cls(
            cached_tokens=0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated=True,
            prompt_chars=prompt_chars,
            completion_chars=completion_chars,
        )


@dataclass
class AIResponse:
    """Canonical LLM response contract."""

    ok: bool
    output: str = ""
    structured: dict[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    model: str | None = None
    provider_id: str | None = None
    error: str | None = None
    error_category: ErrorCategory | None = None
    trace_id: str | None = None
    raw: dict[str, Any] | None = None
    thinking: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    platform_retry_count: int = 0
    platform_retry_exhausted: bool = False
    last_transport_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": self.ok,
            "output": self.output,
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
        }
        if self.model:
            result["model"] = self.model
        if self.provider_id:
            result["provider_id"] = self.provider_id
        if self.structured is not None:
            result["structured"] = self.structured
        if self.error:
            result["error"] = self.error
        if self.error_category:
            result["error_category"] = self.error_category.value
        if self.trace_id:
            result["trace_id"] = self.trace_id
        if self.raw is not None:
            result["raw"] = self.raw
        if self.thinking is not None:
            result["thinking"] = self.thinking
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        if self.platform_retry_count > 0:
            result["platform_retry_count"] = self.platform_retry_count
            result["platform_retry_exhausted"] = self.platform_retry_exhausted
        if self.last_transport_error:
            result["last_transport_error"] = self.last_transport_error
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AIResponse:
        payload = dict(data or {})
        raw_category = payload.get("error_category")
        category_value: ErrorCategory | None = None
        if raw_category is not None and isinstance(raw_category, ErrorCategory):
            category_value = raw_category
        elif isinstance(raw_category, str) and raw_category:
            try:
                category_value = ErrorCategory(raw_category)
            except ValueError:
                category_value = ErrorCategory.UNKNOWN
        return cls(
            ok=bool(payload.get("ok", True)),
            output=str(payload.get("output", "") or ""),
            structured=dict(payload.get("structured") or {}) if isinstance(payload.get("structured"), dict) else None,
            usage=Usage.from_dict(payload.get("usage") or {}),
            latency_ms=int(payload.get("latency_ms", 0) or 0),
            model=str(payload.get("model", "") or "") or None,
            provider_id=str(payload.get("provider_id", "") or "") or None,
            error=str(payload.get("error", "") or "") or None,
            error_category=category_value,
            trace_id=str(payload.get("trace_id", "") or "") or None,
            raw=dict(payload.get("raw") or {}) if isinstance(payload.get("raw"), dict) else None,
            thinking=str(payload.get("thinking", "") or "") or None,
            metadata=dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {},
            platform_retry_count=int(payload.get("platform_retry_count", 0) or 0),
            platform_retry_exhausted=bool(payload.get("platform_retry_exhausted", False)),
            last_transport_error=str(payload.get("last_transport_error", "") or "") or None,
        )

    @classmethod
    def success(
        cls,
        output: str,
        usage: Usage | None = None,
        latency_ms: int = 0,
        structured: dict[str, Any] | None = None,
        model: str | None = None,
        provider_id: str | None = None,
        trace_id: str | None = None,
        thinking: str | None = None,
        raw: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AIResponse:
        return cls(
            ok=True,
            output=output,
            structured=structured,
            usage=usage or Usage.estimate("", output),
            latency_ms=latency_ms,
            model=model,
            provider_id=provider_id,
            trace_id=trace_id,
            thinking=thinking,
            raw=raw,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failure(
        cls,
        error: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        latency_ms: int = 0,
        model: str | None = None,
        provider_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        platform_retry_count: int = 0,
        platform_retry_exhausted: bool = False,
        last_transport_error: str | None = None,
    ) -> AIResponse:
        return cls(
            ok=False,
            error=error,
            error_category=category,
            latency_ms=latency_ms,
            model=model,
            provider_id=provider_id,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
            platform_retry_count=platform_retry_count,
            platform_retry_exhausted=platform_retry_exhausted,
            last_transport_error=last_transport_error,
        )


class ProviderFormatter(Protocol):
    """延迟序列化协议 - Provider内部决定格式化时机

    该协议允许不同Provider实现自己的工具和消息格式化逻辑，
    实现延迟序列化，即Provider在运行时决定何时格式化。
    """

    def format_tools(self, tools: list[dict[str, Any]], provider: str) -> Any:
        """将工具列表格式化为Provider特定的格式

        Args:
            tools: 工具定义列表
            provider: Provider标识符

        Returns:
            Provider特定的格式化结果
        """
        ...

    def format_messages(self, messages: list[dict[str, Any]], provider: str) -> Any:
        """将消息列表格式化为Provider特定的格式

        Args:
            messages: 消息字典列表
            provider: Provider标识符

        Returns:
            Provider特定的格式化结果
        """
        ...


__all__ = [
    "AIRequest",
    "AIResponse",
    "CompressionResult",
    "ModelSpec",
    "ProviderFormatter",
    "StreamEventType",
    "TaskType",
    "TokenBudgetDecision",
    "Usage",
]
