"""Polaris AI Platform - Unified Executor

统一执行器：提供统一的 LLM 调用能力。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator

from polaris.kernelone.trace import get_trace_id

from .._timeout_config import get_invoke_timeout, get_max_concurrency
from ..providers import get_provider_manager
from ._executor_base import (
    build_invoke_config,
    classify_error,
    get_provider_config,
    resolve_provider_model,
    resolve_requested_output_tokens,
)
from .contracts import (
    AIRequest,
    AIResponse,
    AIStreamEvent,
    ErrorCategory,
    ModelSpec,
    StreamEventType,
    TaskType,
    Usage,
)
from .model_catalog import ModelCatalog
from .normalizer import ResponseNormalizer
from .prompt_budget import TokenBudgetManager
from .resilience import ResilienceManager, RetryConfig, TimeoutConfig

if TYPE_CHECKING:
    from .telemetry import TelemetryCollector

logger = logging.getLogger(__name__)

# ─── Global concurrency and timeout configuration ───────────────────────────────
# Now unified via _timeout_config module

# Global semaphore for concurrency control
_global_llm_semaphore: asyncio.Semaphore | None = None
# Use threading.Lock for thread-safe initialization of asyncio resources
_semaphore_init_lock = threading.Lock()

# Global sync lock for executor manager (thread-safe singleton access)
_global_executor_manager_sync_lock = threading.Lock()

# Track if cleanup has been registered
_semaphore_cleanup_registered = False


async def _get_global_semaphore() -> asyncio.Semaphore:
    """Get or create the global LLM semaphore (thread-safe initialization).

    Uses threading.Lock to protect asyncio.Semaphore creation, ensuring
    thread-safety in multi-threaded environments where asyncio.Lock
    initialization itself is not atomic.
    """
    global _global_llm_semaphore

    if _global_llm_semaphore is None:
        with _semaphore_init_lock:
            if _global_llm_semaphore is None:
                _global_llm_semaphore = asyncio.Semaphore(get_max_concurrency())
                _register_semaphore_cleanup()
    return _global_llm_semaphore


async def _invoke_with_timeout(coro, timeout: float | None = None) -> Any:
    """Execute a coroutine with timeout, falling back to asyncio.to_thread pattern.

    For sync functions wrapped in asyncio.to_thread, this adds explicit timeout control.
    """
    if timeout is None:
        timeout = get_invoke_timeout()
    return await asyncio.wait_for(coro, timeout=timeout)


class AIExecutor:
    """统一 AI 执行器

    提供统一的 LLM 调用入口，包含：
    - Provider 解析
    - 弹性策略（重试、超时）
    - 响应标准化
    - 观测收集
    """

    def __init__(
        self,
        workspace: str | None = None,
        telemetry: TelemetryCollector | None = None,
        resilience: ResilienceManager | None = None,
        model_catalog: ModelCatalog | None = None,
        token_budget: TokenBudgetManager | None = None,
    ) -> None:
        self.workspace = workspace
        self.telemetry = telemetry
        self.resilience = resilience or ResilienceManager()
        self.model_catalog = model_catalog or ModelCatalog(workspace=workspace or ".")
        self.token_budget = token_budget or TokenBudgetManager()

    async def invoke(self, request: AIRequest) -> AIResponse:
        """执行 AI 调用

        Args:
            request: AI 请求

        Returns:
            AIResponse: 标准化响应
        """
        # Support stream option - raise error to prevent silent fallback
        if request.options.get("stream", False):
            raise NotImplementedError(
                "stream=True is not supported in invoke(). Use invoke_stream() for streaming responses."
            )

        trace_id = get_trace_id()
        start_time = time.time()

        # 记录调用开始
        if self.telemetry:
            self.telemetry.record_invoke_start(trace_id, request)

        try:
            # 解析 provider 和 model
            provider_id, model = self._resolve_provider_model(request)
            if not provider_id or not model:
                response = AIResponse.failure(
                    error="Provider or model not resolved",
                    category=ErrorCategory.CONFIG_ERROR,
                )
                if self.telemetry:
                    self.telemetry.record_invoke_end(trace_id, request, response, start_time)
                return response

            # 更新 request 中的解析结果
            request.provider_id = provider_id
            request.model = model

            # 执行带弹性策略的调用
            response = await self._invoke_with_resilience(request, trace_id)

            # 记录调用结束
            if self.telemetry:
                self.telemetry.record_invoke_end(trace_id, request, response, start_time)

            return response

        except asyncio.CancelledError:
            # CancelledError must be re-raised, not swallowed
            logger.info("[executor] invoke cancelled for trace_id=%s", trace_id)
            raise
        except (RuntimeError, ConnectionError, TimeoutError) as exc:
            logger.exception("[executor] invoke failed")
            response = AIResponse.failure(
                error=str(exc),
                category=ErrorCategory.UNKNOWN,
                latency_ms=int((time.time() - start_time) * 1000),
                trace_id=trace_id,
            )
            if self.telemetry:
                self.telemetry.record_invoke_end(trace_id, request, response, start_time)
            return response

    async def invoke_stream(self, request: AIRequest) -> AsyncIterator[AIStreamEvent]:
        """执行流式 AI 调用

        Args:
            request: AI 请求

        Yields:
            流式事件块
        """
        import warnings

        warnings.warn(
            "AIExecutor.invoke_stream is experimental. The streaming interface is subject to change.",
            DeprecationWarning,
            stacklevel=2,
        )

        try:
            from .stream_executor import StreamExecutor

            stream_executor = StreamExecutor(
                workspace=self.workspace,
                telemetry=self.telemetry,
                model_catalog=self.model_catalog,
                token_budget=self.token_budget,
            )

            async for event in stream_executor.invoke_stream(request):
                metadata = dict(event.meta or {})
                metadata.setdefault("provider_id", request.provider_id)
                metadata.setdefault("model", request.model)

                if event.type == StreamEventType.CHUNK:
                    yield AIStreamEvent.chunk_event(str(event.chunk or ""), meta=metadata)
                elif event.type == StreamEventType.REASONING_CHUNK:
                    yield AIStreamEvent.reasoning_event(str(event.reasoning or ""), meta=metadata)
                elif event.type == StreamEventType.TOOL_CALL:
                    tool_call = dict(event.tool_call or {})
                    metadata.setdefault("tool_call", tool_call)
                    yield AIStreamEvent.tool_call_event(tool_call, meta=metadata)
                elif event.type == StreamEventType.TOOL_RESULT:
                    tool_result = dict(event.tool_result or {})
                    metadata.setdefault("tool_result", tool_result)
                    yield AIStreamEvent.tool_result_event(tool_result, meta=metadata)
                elif event.type == StreamEventType.COMPLETE:
                    yield AIStreamEvent.complete(data=metadata)
                elif event.type == StreamEventType.ERROR:
                    yield AIStreamEvent.error_event(str(event.error or "provider_stream_failed"))
                    return

        except asyncio.TimeoutError as exc:
            logger.warning("[executor] invoke_stream timeout: %s", exc)
            yield AIStreamEvent.error_event(f"timeout: {exc}")
        except (AttributeError, TypeError, RuntimeError, ConnectionError, TimeoutError) as exc:
            # These are typically programming errors, not transient failures
            logger.exception("[executor] invoke_stream error: %s", exc)
            yield AIStreamEvent.error_event(f"internal_error: {exc}")

    # 兼容别名 (deprecated)
    async def execute(self, request: AIRequest) -> AIResponse:
        """DEPRECATED: 使用 invoke() 代替"""
        import warnings

        warnings.warn(
            "AIExecutor.execute is deprecated. Use invoke() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.invoke(request)

    async def execute_stream(self, request: AIRequest) -> AsyncIterator[AIStreamEvent]:
        """DEPRECATED: 使用 invoke_stream() 代替"""
        import warnings

        warnings.warn(
            "AIExecutor.execute_stream is deprecated. Use invoke_stream() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        async for chunk in self.invoke_stream(request):
            yield chunk

    async def _invoke_with_resilience(
        self,
        request: AIRequest,
        trace_id: str,
    ) -> AIResponse:
        """带弹性策略的执行"""

        async def _do_invoke() -> AIResponse:
            return await self._execute_invoke(request, trace_id)

        resilience = self._build_request_resilience(request)
        return await resilience.execute_with_resilience(
            _do_invoke,
            operation_name=f"invoke_{request.task_type.value}",
        )

    def _build_request_resilience(self, request: AIRequest) -> ResilienceManager:
        """基于请求 options 构建弹性策略，确保 timeout/retry 配置生效。"""
        options = request.options if isinstance(request.options, dict) else {}
        if not options:
            return self.resilience

        try:
            timeout_config = TimeoutConfig.from_options(options)
            retry_config = RetryConfig.from_options(options)
        except (TypeError, ValueError, KeyError):
            # options contains invalid/unexpected values; fall back to default resilience manager
            return self.resilience

        return ResilienceManager(
            timeout_config=timeout_config,
            retry_config=retry_config,
            truncation_config=self.resilience.truncation_config,
        )

    async def _execute_invoke(
        self,
        request: AIRequest,
        trace_id: str,
    ) -> AIResponse:
        """执行实际的 LLM 调用"""
        provider_id = request.provider_id
        model = request.model

        if provider_id is None:
            return AIResponse.failure(
                error="Provider ID is required",
                category=ErrorCategory.CONFIG_ERROR,
            )

        # 获取 provider 配置 - provider_id is guaranteed non-None after check
        provider_cfg = self._get_provider_config(provider_id)  # type: ignore[arg-type]
        provider_type = str(provider_cfg.get("type") or "").strip().lower()

        if not provider_type:
            return AIResponse.failure(
                error=f"Provider type not found for {provider_id}",
                category=ErrorCategory.CONFIG_ERROR,
            )

        # 获取 provider 实例
        provider_instance = get_provider_manager().get_provider_instance(provider_type)
        if provider_instance is None:
            return AIResponse.failure(
                error=f"Provider not found: {provider_type}",
                category=ErrorCategory.CONFIG_ERROR,
            )

        # 构建调用配置
        invoke_cfg = self._build_invoke_config(provider_cfg, request.options)
        model_spec = self.model_catalog.resolve(
            str(provider_id),  # provider_id is guaranteed non-None after check
            str(model or ""),
            provider_cfg,
        )

        requested_output_tokens = self._resolve_requested_output_tokens(request.options, invoke_cfg, model_spec)
        if requested_output_tokens > 0:
            invoke_cfg["max_tokens"] = requested_output_tokens

        budget_decision = self.token_budget.enforce(
            request.input,
            model_spec,
            requested_output_tokens=requested_output_tokens,
            workspace=self.workspace,
            role=request.role,
        )
        if not budget_decision.allowed:
            return AIResponse.failure(
                error=budget_decision.error or "Prompt exceeds model context budget",
                category=ErrorCategory.INVALID_RESPONSE,
            )

        prompt_input = request.input
        if budget_decision.compression_applied and budget_decision.compression is not None:
            prompt_input = budget_decision.compression.compressed_input
            request.context["token_budget"] = budget_decision.to_dict()

        # Acquire semaphore for concurrency control
        semaphore = await _get_global_semaphore()
        async with semaphore:
            # 执行调用
            start_time = time.time()
            effective_timeout = invoke_cfg.get("timeout")
            try:
                # 使用带超时的 asyncio.to_thread 避免阻塞
                result = await _invoke_with_timeout(
                    asyncio.to_thread(
                        provider_instance.invoke,
                        prompt_input,
                        str(model or ""),  # Ensure model is str
                        invoke_cfg,
                    ),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                timeout_val = effective_timeout if effective_timeout is not None else get_invoke_timeout()
                logger.warning("[executor] invoke timeout after %ss", timeout_val)
                return AIResponse.failure(
                    error=f"Invoke timeout after {timeout_val}s",
                    category=ErrorCategory.TIMEOUT,
                )
            except (RuntimeError, ConnectionError, TimeoutError) as exc:
                logger.warning("[executor] invoke error: %s", exc)
                return AIResponse.failure(
                    error=str(exc),
                    category=classify_error(exc),
                )

        latency_ms = int((time.time() - start_time) * 1000)

        # 标准化响应
        if result.ok:
            output = str(result.output or "")
            # 尝试提取结构化数据
            structured = ResponseNormalizer.extract_json_object(output)

            return AIResponse.success(
                output=output,
                usage=result.usage if result.usage else Usage.estimate(prompt_input, output),
                latency_ms=latency_ms,
                structured=structured,
                trace_id=trace_id,
                thinking=result.thinking,
                raw=self._build_raw_payload(
                    raw=result.raw,
                    model_spec=model_spec,
                    budget=budget_decision.to_dict(),
                ),
            )
        else:
            return AIResponse.failure(
                error=str(result.error or "invoke_failed"),
                category=ErrorCategory.PROVIDER_ERROR,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

    def _resolve_provider_model(self, request: AIRequest) -> tuple[str | None, str | None]:
        """解析 provider_id 和 model"""
        return resolve_provider_model(
            provider_id=request.provider_id,
            model=request.model,
            role=request.role,
            logger_prefix="[executor]",
        )

    def _get_provider_config(self, provider_id: str) -> dict[str, Any]:
        """获取 provider 配置"""
        return get_provider_config(
            workspace=self.workspace,
            provider_id=provider_id,
            logger_prefix="[executor]",
        )

    def _build_invoke_config(
        self,
        provider_cfg: dict[str, Any],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """构建调用配置"""
        return build_invoke_config(provider_cfg, options, streaming=False)

    def _resolve_requested_output_tokens(
        self,
        options: dict[str, Any],
        invoke_cfg: dict[str, Any],
        model_spec: ModelSpec,
    ) -> int:
        return resolve_requested_output_tokens(options, invoke_cfg, model_spec)

    def _build_raw_payload(
        self,
        *,
        raw: Any,
        model_spec: ModelSpec,
        budget: dict[str, Any],
    ) -> dict[str, Any]:
        payload = raw if isinstance(raw, dict) else {}
        if "model_spec" not in payload:
            payload["model_spec"] = model_spec.to_dict()
        payload["token_budget"] = budget
        return payload

    async def invoke_with_repair(
        self,
        request: AIRequest,
        required_keys: list[str] | None = None,
    ) -> AIResponse:
        """执行 AI 调用，带截断修复"""
        response = await self.invoke(request)

        if not response.ok:
            return response

        # 检查是否需要修复
        if not self.resilience.should_attempt_repair(response.output):
            return response

        logger.debug("[executor] attempting repair for truncated output")

        # 构建修复请求
        repair_prompt = self.resilience.build_repair_prompt(response.output, required_keys)
        repair_request = AIRequest(
            task_type=TaskType.GENERATION,
            role=request.role,
            input=repair_prompt,
            options={
                "temperature": 0.0,
                "max_tokens": self.resilience.truncation_config.max_repair_tokens,
            },
        )

        # 执行修复
        repair_response = await self.invoke(repair_request)

        if repair_response.ok and repair_response.structured:
            # 使用修复后的结果
            response.structured = repair_response.structured
            response.output = repair_response.output
            return response

        return response


@dataclass
class _ExecutorEntry:
    """执行器条目，包含实例与观测用引用计数。"""

    executor: AIExecutor
    ref_count: int = 0


class WorkspaceExecutorManager:
    """按 workspace 隔离 AIExecutor，且并发安全。

    使用统一的 threading.Lock 保护 _executors 字典，避免
    asyncio.Lock 和 threading.Lock 混用导致的死锁风险。
    """

    def __init__(self) -> None:
        self._executors: dict[str, _ExecutorEntry] = {}
        # Use single threading.Lock for both sync and async paths
        # to prevent deadlock from mixed lock types
        self._lock = threading.Lock()

    @staticmethod
    def _workspace_key(workspace: str | None) -> str:
        token = str(workspace or "").strip()
        return token or "_default_"

    def get_executor_sync(self, workspace: str | None = None) -> AIExecutor:
        ws_key = self._workspace_key(workspace)
        with self._lock:
            entry = self._executors.get(ws_key)
            if entry is None:
                entry = _ExecutorEntry(executor=AIExecutor(workspace=workspace), ref_count=0)
                self._executors[ws_key] = entry
            entry.ref_count += 1
            return entry.executor

    async def get_executor(self, workspace: str | None = None) -> AIExecutor:
        """Async wrapper for get_executor_sync.

        Uses the same threading.Lock for consistency with sync path.
        The lock is held briefly during dict access only.
        """
        return self.get_executor_sync(workspace)

    def set_executor(self, executor: AIExecutor, workspace: str | None = None) -> None:
        ws_key = self._workspace_key(workspace)
        with self._lock:
            self._executors[ws_key] = _ExecutorEntry(executor=executor, ref_count=1)


_executor_manager: WorkspaceExecutorManager | None = None
_executor_manager_async_lock: asyncio.Lock | None = None


async def _get_executor_manager_async() -> WorkspaceExecutorManager:
    """Get or create the global executor manager (async-safe)."""
    global _executor_manager, _executor_manager_async_lock

    if _executor_manager is None:
        if _executor_manager_async_lock is None:
            _executor_manager_async_lock = asyncio.Lock()
        async with _executor_manager_async_lock:
            if _executor_manager is None:
                _executor_manager = WorkspaceExecutorManager()
    return _executor_manager


def get_executor(workspace: str | None = None) -> AIExecutor:
    """获取执行器实例（同步兼容接口）。"""
    manager = _get_executor_manager_sync()
    return manager.get_executor_sync(workspace)


def _get_executor_manager_sync() -> WorkspaceExecutorManager:
    """Get or create the global executor manager (sync fallback)."""
    global _executor_manager, _executor_manager_async_lock
    with _global_executor_manager_sync_lock:
        if _executor_manager is None:
            _executor_manager_async_lock = None
            _executor_manager = WorkspaceExecutorManager()
        return _executor_manager


async def get_executor_async(workspace: str | None = None) -> AIExecutor:
    """获取执行器实例（异步接口，推荐并发场景使用）。"""
    manager = await _get_executor_manager_async()
    return await manager.get_executor(workspace)


def set_executor(executor: AIExecutor, workspace: str | None = None) -> None:
    """设置特定 workspace 的执行器实例（主要用于测试）。"""
    manager = _get_executor_manager_sync()
    manager.set_executor(executor, workspace)


def reset_executor_manager() -> None:
    """Reset global executor manager (test-only helper)."""
    global _executor_manager, _executor_manager_async_lock
    # Both resets must be inside the sync lock to avoid race with
    # _get_executor_manager_sync() which reads _executor_manager under the
    # same sync lock. Resetting _executor_manager_async_lock outside the
    # lock could cause a coroutine to acquire the old lock reference while
    # a concurrent reset is about to set it to None.
    with _global_executor_manager_sync_lock:
        _executor_manager = None
        _executor_manager_async_lock = None


def _cleanup_global_semaphore() -> None:
    """Cleanup global LLM semaphore on application exit."""
    global _global_llm_semaphore
    with _semaphore_init_lock:
        if _global_llm_semaphore is not None:
            _global_llm_semaphore = None
            logger.debug("Global LLM semaphore cleaned up")


def reset_llm_semaphore() -> None:
    """Reset global LLM semaphore (test-only helper).

    Use this when tests need to reset concurrency state, e.g. after
    modifying the semaphore's internal counter.
    """
    global _global_llm_semaphore, _semaphore_cleanup_registered
    with _semaphore_init_lock:
        _global_llm_semaphore = None
        _semaphore_cleanup_registered = False


def shutdown_llm_executor() -> None:
    """Shutdown LLM executor and cleanup global resources.

    Call this during application shutdown to properly release resources.
    """
    global _semaphore_cleanup_registered
    _cleanup_global_semaphore()
    with _global_executor_manager_sync_lock:
        global _executor_manager, _executor_manager_async_lock
        _executor_manager = None
        _executor_manager_async_lock = None
    logger.info("LLM executor shutdown complete")


def _register_semaphore_cleanup() -> None:
    """Register atexit cleanup for global semaphore (idempotent)."""
    global _semaphore_cleanup_registered
    if not _semaphore_cleanup_registered:
        atexit.register(_cleanup_global_semaphore)
        _semaphore_cleanup_registered = True
