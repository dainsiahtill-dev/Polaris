"""Polaris AI Platform - Unified Contracts

统一 AI 调用契约定义，所有 LLM 调用统一经过此契约。
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, AsyncContextManager, Protocol, TypeAlias, TypeVar

# ErrorCategory is used via AIResponse.error_category annotations.
from polaris.kernelone.errors import ErrorCategory

# Re-export shared types so consumers can import from engine.contracts
# without needing a direct dependency on contracts.core.
from ..contracts.core import (
    AIRequest,
    AIResponse,
    CompressionResult,
    ModelSpec,
    ProviderFormatter,
    StreamEventType,
    TaskType,
    TokenBudgetDecision,
    Usage,
)

_DispatchResultT = TypeVar("_DispatchResultT")
_StreamResponseT = TypeVar("_StreamResponseT")
_EXACT_HASH_64_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_ATTEMPT_SCOPES = frozenset({"factory", "role_session"})


def _deep_freeze_provider_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze_provider_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_provider_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError("frozen provider attempts require JSON-safe values")


def _deep_thaw_provider_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw_provider_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw_provider_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class FrozenFinalProviderAttemptV1:
    """Immutable semantic and physical views for one outbound attempt."""

    provider_request_id: str
    request_freeze_id: str
    factory_run_id: str
    scope_id: str
    run_id: str
    turn_id: str
    call_id: str
    role: str
    provider: str
    model: str
    attempt_number: int
    verification_scope: str
    semantic_request_hash: str
    physical_wire_hash: str
    composite_request_hash: str
    dispatch_view: Mapping[str, Any]
    durable_view: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in (
            "provider_request_id",
            "request_freeze_id",
            "scope_id",
            "run_id",
            "turn_id",
            "call_id",
            "role",
            "provider",
            "model",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        if self.verification_scope not in _PROVIDER_ATTEMPT_SCOPES:
            raise ValueError("verification_scope must be factory or role_session")
        if self.verification_scope == "factory":
            if not self.factory_run_id or self.factory_run_id != self.scope_id:
                raise ValueError("Factory attempt requires factory_run_id equal to scope_id")
        elif self.factory_run_id:
            raise ValueError("role-session attempt cannot claim factory_run_id")
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int) or self.attempt_number < 1:
            raise ValueError("attempt_number must be an int >= 1")
        for hash_field in ("semantic_request_hash", "physical_wire_hash", "composite_request_hash"):
            if not _EXACT_HASH_64_RE.fullmatch(str(getattr(self, hash_field) or "")):
                raise ValueError(f"{hash_field} must be exactly 64 lowercase hex")
        for view_field in ("dispatch_view", "durable_view"):
            if not isinstance(getattr(self, view_field), Mapping):
                raise TypeError(f"{view_field} must be a mapping")
        object.__setattr__(self, "dispatch_view", _deep_freeze_provider_value(self.dispatch_view))
        object.__setattr__(self, "durable_view", _deep_freeze_provider_value(self.durable_view))

    def dispatch_copy(self) -> dict[str, Any]:
        thawed = _deep_thaw_provider_value(self.dispatch_view)
        if not isinstance(thawed, dict):
            raise TypeError("frozen dispatch view must be a mapping")
        return thawed

    def durable_copy(self) -> dict[str, Any]:
        thawed = _deep_thaw_provider_value(self.durable_view)
        if not isinstance(thawed, dict):
            raise TypeError("frozen durable view must be a mapping")
        return thawed


class PhysicalProviderDispatchPort(Protocol):
    """Physical transport boundary consumed by HTTP/SDK/CLI adapters."""

    def dispatch_sync(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _DispatchResultT],
    ) -> _DispatchResultT: ...


class AsyncPhysicalProviderDispatchPort(Protocol):
    """Generic async boundary, independent of any concrete transport."""

    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Awaitable[_DispatchResultT]],
    ) -> _DispatchResultT: ...

    async def dispatch_blocking_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _DispatchResultT],
    ) -> _DispatchResultT: ...

    def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], AsyncContextManager[_StreamResponseT]],
        consume: Callable[[_StreamResponseT], AsyncIterator[_DispatchResultT]],
    ) -> AsyncIterator[_DispatchResultT]: ...


PhysicalProviderDispatchRuntimePort: TypeAlias = PhysicalProviderDispatchPort | AsyncPhysicalProviderDispatchPort

_physical_provider_dispatch_port: ContextVar[PhysicalProviderDispatchRuntimePort | None] = ContextVar(
    "kernelone_physical_provider_dispatch_port",
    default=None,
)


def get_physical_provider_dispatch_port() -> PhysicalProviderDispatchRuntimePort | None:
    """Return the request-scoped physical provider dispatch sidecar, if bound."""

    return _physical_provider_dispatch_port.get()


@contextmanager
def bind_physical_provider_dispatch_port(
    port: PhysicalProviderDispatchRuntimePort | None,
) -> Iterator[None]:
    """Bind a non-serializable physical dispatch port for one provider call."""

    token = _physical_provider_dispatch_port.set(port)
    try:
        yield
    finally:
        _physical_provider_dispatch_port.reset(token)


@dataclass(frozen=True, slots=True)
class ProviderAttemptTerminalFailureV1:
    provider_request_id: str
    error_type: str
    error: str

    def __post_init__(self) -> None:
        if not self.provider_request_id or not self.error_type or not self.error:
            raise ValueError("terminal failure requires request id, error type, and error")


@dataclass(frozen=True, slots=True)
class ProviderAttemptDrainResultV1:
    verification_scope: str
    scope_id: str
    settled: bool
    inflight_request_ids: tuple[str, ...]
    terminal_failures: tuple[ProviderAttemptTerminalFailureV1, ...]

    def __post_init__(self) -> None:
        if self.verification_scope not in _PROVIDER_ATTEMPT_SCOPES or not self.scope_id:
            raise ValueError("drain result requires an explicit Factory or role-session scope")
        if not isinstance(self.inflight_request_ids, tuple) or not isinstance(self.terminal_failures, tuple):
            raise TypeError("drain result collections must be tuples")
        if any(not isinstance(item, str) or not item for item in self.inflight_request_ids):
            raise ValueError("drain result contains an invalid in-flight request id")
        if tuple(sorted(set(self.inflight_request_ids))) != self.inflight_request_ids:
            raise ValueError("drain result in-flight request ids must be unique and sorted")
        if any(not isinstance(item, ProviderAttemptTerminalFailureV1) for item in self.terminal_failures):
            raise TypeError("drain result terminal failures must be typed")
        failure_ids = tuple(item.provider_request_id for item in self.terminal_failures)
        if tuple(sorted(set(failure_ids))) != failure_ids:
            raise ValueError("drain result terminal failures must be unique and sorted")
        if any(request_id not in self.inflight_request_ids for request_id in failure_ids):
            raise ValueError("terminal failure must remain registered as in-flight")
        if self.settled != (not self.inflight_request_ids and not self.terminal_failures):
            raise ValueError("drain settled flag contradicts in-flight diagnostics")


class ProviderAttemptInFlightDrainPort(Protocol):
    """Explicit run/session-scoped drain contract for provider attempts."""

    @property
    def verification_scope(self) -> str: ...

    @property
    def scope_id(self) -> str: ...

    async def wait_settled(
        self,
        *,
        verification_scope: str,
        scope_id: str,
        timeout_seconds: float | None = None,
    ) -> ProviderAttemptDrainResultV1: ...


class ProviderAttemptLifecyclePort(Protocol):
    def append_start(
        self, attempt: FrozenFinalProviderAttemptV1, *, context_snapshot_ref: str, pin_hash: str
    ) -> None: ...

    def append_terminal(
        self,
        attempt: FrozenFinalProviderAttemptV1,
        *,
        context_snapshot_ref: str,
        pin_hash: str,
        status: str,
        error: str | None = None,
    ) -> None: ...


@dataclass
class AIStreamEvent:
    """统一流式事件契约

    Attributes:
        type: 事件类型
        chunk: 文本块（CHUNK 类型）
        reasoning: 推理内容（REASONING_CHUNK 类型）
        tool_call: 结构化工具调用载荷（TOOL_CALL 类型）
        tool_result: 结构化工具结果载荷（TOOL_RESULT 类型）
        meta: 元数据
        done: 是否完成
        error: 错误信息
    """

    type: StreamEventType
    chunk: str | None = None
    reasoning: str | None = None
    tool_call: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    done: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this stream event to a JSON-compatible dictionary.

        Omits fields that are None to keep output minimal.

        Returns:
            Dictionary with type, chunk, reasoning, meta, done, and error keys.
        """
        result: dict[str, Any] = {"type": self.type.value}
        if self.chunk is not None:
            result["chunk"] = self.chunk
        if self.reasoning is not None:
            result["reasoning"] = self.reasoning
        if self.tool_call is not None:
            result["tool_call"] = self.tool_call
        if self.tool_result is not None:
            result["tool_result"] = self.tool_result
        if self.meta is not None:
            result["meta"] = self.meta
        if self.done:
            result["done"] = True
        if self.error:
            result["error"] = self.error
        return result

    @classmethod
    def chunk_event(cls, text: str, meta: dict[str, Any] | None = None) -> AIStreamEvent:
        """Factory: create a CHUNK event containing text.

        NOTE:
            Do not name this factory `chunk` because `AIStreamEvent` already has a
            dataclass field named `chunk`. Using the same name corrupts dataclass
            defaults and can leak callable defaults into runtime events.

        Args:
            text: The text chunk from the stream.
            meta: Optional per-chunk metadata (e.g. token counts).

        Returns:
            AIStreamEvent with type=CHUNK.
        """
        return cls(type=StreamEventType.CHUNK, chunk=str(text), meta=meta)

    @classmethod
    def reasoning_event(
        cls,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> AIStreamEvent:
        """Factory: create a REASONING_CHUNK event for transparent thinking output.

        NOTE:
            Do not name this factory `reasoning` because `AIStreamEvent` already has
            a dataclass field named `reasoning`.

        Args:
            text: The reasoning / chain-of-thought text.
            meta: Optional per-chunk metadata.

        Returns:
            AIStreamEvent with type=REASONING_CHUNK.
        """
        return cls(
            type=StreamEventType.REASONING_CHUNK,
            reasoning=str(text),
            meta=meta,
        )

    @classmethod
    def tool_call_event(
        cls,
        tool_call: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> AIStreamEvent:
        """Factory: create a TOOL_CALL event for structured native tool dispatch."""

        return cls(type=StreamEventType.TOOL_CALL, tool_call=dict(tool_call), meta=meta)

    @classmethod
    def tool_result_event(
        cls,
        tool_result: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> AIStreamEvent:
        """Factory: create a TOOL_RESULT event for structured tool receipts."""

        return cls(
            type=StreamEventType.TOOL_RESULT,
            tool_result=dict(tool_result),
            meta=meta,
        )

    @classmethod
    def tool_start_event(
        cls,
        tool_name: str,
        call_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AIStreamEvent:
        """Factory: create a TOOL_START event marking tool execution start.

        Use this to signal the beginning of a tool execution lifecycle,
        enabling frontend to display tool-in-progress UI before the
        full tool_call payload is assembled.

        Args:
            tool_name: Name of the tool being executed.
            call_id: Optional call ID for correlation.
            meta: Optional metadata (provider, trace_id, etc.).

        Returns:
            AIStreamEvent with type=TOOL_START.
        """
        return cls(
            type=StreamEventType.TOOL_START,
            tool_call={"tool": tool_name, "call_id": call_id or ""},
            meta=meta,
        )

    @classmethod
    def tool_end_event(
        cls,
        tool_name: str,
        call_id: str | None = None,
        success: bool = True,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AIStreamEvent:
        """Factory: create a TOOL_END event marking tool execution completion.

        Use this to signal the end of a tool execution lifecycle,
        enabling frontend to close tool-in-progress UI.

        Args:
            tool_name: Name of the tool that was executed.
            call_id: Optional call ID for correlation.
            success: Whether the tool execution succeeded.
            error: Optional error message if success=False.
            meta: Optional metadata (duration_ms, etc.).

        Returns:
            AIStreamEvent with type=TOOL_END.
        """
        event_meta: dict[str, Any] = dict(meta) if meta else {}
        event_meta["success"] = success
        if error:
            event_meta["error"] = error
        return cls(
            type=StreamEventType.TOOL_END,
            tool_call={"tool": tool_name, "call_id": call_id or ""},
            meta=event_meta,
        )

    @classmethod
    def complete(cls, data: dict[str, Any] | None = None) -> AIStreamEvent:
        """Factory: create a COMPLETE event marking the end of a stream.

        Args:
            data: Optional final metadata (e.g. usage stats, model name).

        Returns:
            AIStreamEvent with done=True and type=COMPLETE.
        """
        return cls(type=StreamEventType.COMPLETE, done=True, meta=data)

    @classmethod
    def error_event(cls, error: str) -> AIStreamEvent:
        """Factory: create an ERROR event from an error message.

        Args:
            error: Human-readable error description.

        Returns:
            AIStreamEvent with done=True and type=ERROR.
        """
        return cls(type=StreamEventType.ERROR, error=error, done=True)


@dataclass
class EvaluationCase:
    """评测用例定义"""

    id: str
    name: str
    suite: str  # 所属套件
    prompt: str
    expected_patterns: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    validation_fn: str | None = None  # 可选的验证函数名
    required: bool = True
    timeout_seconds: int = 60

    def to_dict(self) -> dict[str, Any]:
        """Serialize this evaluation case to a JSON-compatible dictionary.

        Returns:
            Dictionary with all case fields including patterns and timeout.
        """
        return {
            "id": self.id,
            "name": self.name,
            "suite": self.suite,
            "prompt": self.prompt,
            "expected_patterns": self.expected_patterns,
            "forbidden_patterns": self.forbidden_patterns,
            "validation_fn": self.validation_fn,
            "required": self.required,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class EvaluationResult:
    """单个用例评测结果"""

    case_id: str
    passed: bool
    output: str = ""
    latency_ms: int = 0
    error: str | None = None
    score: float = 0.0  # 0-1 分数
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this evaluation result to a JSON-compatible dictionary.

        Returns:
            Dictionary with case_id, passed, output, latency_ms, error, score, details.
        """
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "output": self.output,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "score": self.score,
            "details": self.details,
        }


@dataclass
class EvaluationSuiteResult:
    """套件评测结果"""

    suite_name: str
    results: list[EvaluationResult] = field(default_factory=list)
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    total_latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize this suite result to a JSON-compatible dictionary.

        Recursively serializes nested EvaluationResult objects.

        Returns:
            Dictionary with suite_name, results list, and aggregate counts.
        """
        return {
            "suite_name": self.suite_name,
            "results": [r.to_dict() for r in self.results],
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "total_latency_ms": self.total_latency_ms,
        }


@dataclass
class EvaluationReport:
    """统一评测报告契约（v2 schema）

    Attributes:
        report_id: 报告唯一 ID
        run_id: 运行 ID
        timestamp: 时间戳
        provider_id: Provider ID
        model: 模型名称
        role: 角色（可选）
        suites: 套件结果列表
        summary: 摘要信息
        metadata: 元数据
    """

    report_id: str
    run_id: str
    timestamp: str
    provider_id: str
    model: str
    role: str | None = None
    suites: list[EvaluationSuiteResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this evaluation report to a JSON-compatible dictionary.

        Includes schema_version="v2" to identify the v2 contract format.
        Recursively serializes nested suite results.

        Returns:
            Dictionary with report metadata and nested suite results.
        """
        return {
            "report_id": self.report_id,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "provider_id": self.provider_id,
            "model": self.model,
            "role": self.role,
            "suites": [s.to_dict() for s in self.suites],
            "summary": self.summary,
            "metadata": self.metadata,
            "schema_version": "v2",
        }

    @property
    def total_cases(self) -> int:
        """Total number of evaluation cases across all suites."""
        return sum(s.total_cases for s in self.suites)

    @property
    def passed_cases(self) -> int:
        """Total number of passed cases across all suites."""
        return sum(s.passed_cases for s in self.suites)

    @property
    def failed_cases(self) -> int:
        """Total number of failed cases across all suites."""
        return sum(s.failed_cases for s in self.suites)

    @property
    def pass_rate(self) -> float:
        """Fraction of cases that passed, in range [0.0, 1.0].

        Returns 0.0 when there are no cases.
        """
        total = self.total_cases
        return self.passed_cases / total if total > 0 else 0.0


@dataclass
class EvaluationRequest:
    """统一评测请求契约

    Attributes:
        provider_id: Provider ID（可选，默认从 role 解析）
        model: 模型名称（可选，默认从 role 解析）
        role: 角色（用于解析 provider/model）
        suites: 要运行的套件列表（空列表表示运行所有）
        context: 上下文信息
        options: 评测选项
    """

    provider_id: str | None = None
    model: str | None = None
    role: str | None = None
    suites: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this evaluation request to a JSON-compatible dictionary.

        Returns:
            Dictionary with provider_id, model, role, suites, context, options.
        """
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "role": self.role,
            "suites": self.suites,
            "context": self.context,
            "options": self.options,
        }


# 类型别名
AIStreamGenerator = AsyncGenerator[AIStreamEvent, None]


# Public surface — must list all re-exported shared types so ruff --fix
# F401 does not silently delete them. Engine-specific types (AIStreamEvent,
# EvaluationCase, EvaluationResult, EvaluationSuiteResult, EvaluationReport,
# EvaluationRequest, AIStreamGenerator) are also exported here.
__all__ = [
    # Re-exported shared types (single source of truth: contracts.core)
    "AIRequest",
    "AIResponse",
    # Engine-specific types
    "AIStreamEvent",
    "AIStreamGenerator",
    "AsyncPhysicalProviderDispatchPort",
    "CompressionResult",
    "ErrorCategory",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationRequest",
    "EvaluationResult",
    "EvaluationSuiteResult",
    "FrozenFinalProviderAttemptV1",
    "ModelSpec",
    "PhysicalProviderDispatchPort",
    "ProviderAttemptDrainResultV1",
    "ProviderAttemptInFlightDrainPort",
    "ProviderAttemptLifecyclePort",
    "ProviderAttemptTerminalFailureV1",
    "ProviderFormatter",
    "StreamEventType",
    "TaskType",
    "TokenBudgetDecision",
    "Usage",
]
