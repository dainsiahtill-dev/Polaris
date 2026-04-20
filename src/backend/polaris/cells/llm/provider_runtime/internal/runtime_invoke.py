"""Non-invasive LLM Audit Interceptor with full tracing.

This module provides:
1. LLMCallInterceptor — Bus interceptor for LLM audit events
2. LLMCallTracker — Context manager for tracking LLM calls
3. llm_audit decorator — Decorator for wrapping LLM provider calls

Design principles:
- Non-invasive: No modification to existing LLM provider code
- Automatic trace propagation: Uses UnifiedAuditContext for trace_id
- Full telemetry: Tokens, latency, strategy, fallback, errors
- Async-safe: Uses contextvars for async propagation

Usage:
    # 1. Bus interceptor (passive, receives events from bus)
    bus = OmniscientAuditBus.get_default()
    interceptor = LLMCallInterceptor(bus)
    bus.subscribe(interceptor.intercept)

    # 2. Context manager (active, use in LLM call sites)
    async with LLMCallTracker.track("claude", role="director") as tracker:
        result = await llm_provider.generate(prompt)
        tracker.add_response(result)  # Adds completion tokens

    # 3. Decorator (wraps existing functions)
    @llm_audit("claude", role="director")
    async def my_llm_call(prompt):
        return await provider.generate(prompt)

    # 4. Manual event emission (for integration points)
    await emit_llm_event(
        model="claude-3-sonnet",
        provider="anthropic",
        prompt_tokens=500,
        completion_tokens=200,
        latency_ms=1500.0,
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from polaris.infrastructure.llm import AppLLMRuntimeAdapter
from polaris.kernelone.audit.omniscient.interceptors.llm_interceptor import (
    LLMCallTracker,
    LLMStrategy,
)
from polaris.kernelone.llm import (
    KernelLLM,
    RuntimeProviderInvokeResult,
    normalize_provider_type,
    resolve_provider_api_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


def invoke_role_runtime_provider(
    *,
    role: str,
    workspace: str,
    prompt: str,
    fallback_model: str,
    timeout: int,
    blocked_provider_types: Iterable[str] | None = None,
) -> RuntimeProviderInvokeResult:
    """Invoke LLM provider with audit tracing.

    Wraps the underlying KernelLLM call with LLMCallTracker to automatically
    emit audit events for token usage, latency, strategy, and provider switching.

    Args:
        role: Role making the call (e.g. "director", "pm").
        workspace: Workspace path.
        prompt: The prompt text.
        fallback_model: Fallback model name if primary fails.
        timeout: Provider timeout in seconds.
        blocked_provider_types: Provider types to skip.

    Returns:
        RuntimeProviderInvokeResult from the LLM call.
    """
    # Determine strategy: primary vs fallback
    strategy = LLMStrategy.PRIMARY

    # Track the LLM call for audit
    tracker = LLMCallTracker(
        model="",  # Will be set after call
        provider="",
        role=role,
        workspace=workspace,
        strategy=strategy,
        fallback_model=fallback_model,
    )

    # Emit start event
    try:
        result = KernelLLM(AppLLMRuntimeAdapter()).invoke_role_provider(
            role=role,
            workspace=workspace,
            prompt=prompt,
            fallback_model=fallback_model,
            timeout=timeout,
            blocked_provider_types=blocked_provider_types,
        )
    except (RuntimeError, ValueError) as exc:
        tracker._error = str(exc)
        tracker._error_type = type(exc).__name__
        tracker._end_time = __import__("datetime").datetime.now()
        # Synchronous emit for non-async context
        import asyncio
        from contextlib import suppress

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        with suppress(RuntimeError):
            asyncio.run(tracker._emit_complete())
        raise

    # Update tracker with result details
    if result.model:
        tracker.model = result.model
    if result.provider_type:
        tracker.provider = result.provider_type

    # Extract token usage from provider result
    _extract_and_apply_usage(tracker, result)

    # Record prompt
    tracker.add_prompt(prompt)

    # Record response
    if result.output:
        # Determine finish reason from error state
        finish_reason = "stop" if result.ok else "error"
        tracker.add_response(
            completion=result.output,
            finish_reason=finish_reason,
        )

    if result.ok and fallback_model and fallback_model != result.model:
        # Successful call using fallback model indicates fallback strategy
        tracker.strategy = LLMStrategy.FALLBACK

    # Emit complete event (synchronous-safe)
    _emit_complete_sync(tracker)

    return result


def _extract_and_apply_usage(
    tracker: LLMCallTracker,
    result: RuntimeProviderInvokeResult,
) -> None:
    """Extract token usage from provider result and apply to tracker.

    Args:
        tracker: The LLM call tracker.
        result: The provider invoke result.
    """
    if result.usage is None:
        return

    usage = result.usage
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        completion_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
        if isinstance(prompt_tokens, int) and prompt_tokens > 0:
            tracker._prompt_tokens = prompt_tokens
        if isinstance(completion_tokens, int) and completion_tokens > 0:
            tracker._completion_tokens = completion_tokens


def _emit_complete_sync(tracker: LLMCallTracker) -> None:
    """Emit complete event synchronously (for non-async call sites).

    Args:
        tracker: The LLM call tracker with final state.
    """
    try:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            # If we're in an async context, schedule the emit as a task
            loop.create_task(tracker._emit_complete())
        except RuntimeError:
            # No running loop, use new event loop in a thread to avoid blocking
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, tracker._emit_complete())
                future.result(timeout=5)
    except (RuntimeError, ValueError) as exc:
        logger.debug("[runtime_invoke] Failed to emit audit event: %s", exc)


__all__ = [
    "RuntimeProviderInvokeResult",
    "invoke_role_runtime_provider",
    "normalize_provider_type",
    "resolve_provider_api_key",
]
