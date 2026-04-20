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

import functools
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from polaris.kernelone.audit.omniscient.adapters.sanitization_hook import (
    get_default_sanitizer,
)
from polaris.kernelone.audit.omniscient.bus import (
    AuditEventEnvelope,
    AuditPriority,
    OmniscientAuditBus,
)
from polaris.kernelone.audit.omniscient.context_manager import (
    get_current_audit_context,
)
from polaris.kernelone.audit.omniscient.schemas.llm_event import (
    LLMFinishReason,
    LLMStrategy,
)

logger = logging.getLogger(__name__)

# Type variable for generic function wrapping
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


# =============================================================================
# LLM Call Tracker
# =============================================================================


@dataclass
class LLMCallTracker:
    """Tracks an in-progress LLM call for audit.

    Usage:
        async with LLMCallTracker.track(model="claude-3") as tracker:
            result = await llm.call(prompt)
            tracker.add_response(
                completion=result.text,
                finish_reason="stop",
                tokens=100,
            )
    """

    model: str
    provider: str = ""
    role: str = ""
    workspace: str = ""
    strategy: LLMStrategy = LLMStrategy.PRIMARY
    fallback_model: str = ""

    # Timing
    _start_time: datetime = field(default_factory=datetime.now)
    _first_token_time: datetime | None = None
    _end_time: datetime | None = None

    # Tokens
    _prompt_tokens: int = 0
    _completion_tokens: int = 0

    # Content
    _prompt: str = ""
    _completion: str = ""
    _finish_reason: LLMFinishReason | None = None

    # Errors
    _error: str = ""
    _error_type: str = ""

    # Context
    _trace_id: str = ""
    _run_id: str = ""
    _span_id: str = ""

    @classmethod
    @asynccontextmanager
    async def track(
        cls,
        model: str,
        provider: str = "",
        role: str = "",
        workspace: str = "",
        strategy: LLMStrategy = LLMStrategy.PRIMARY,
        fallback_model: str = "",
    ) -> Any:
        """Track an LLM call within this context manager.

        Usage:
            async with LLMCallTracker.track(
                model="claude-3-sonnet",
                provider="anthropic",
                role="director",
            ) as tracker:
                result = await llm.generate(prompt)
                tracker.add_response(result)
        """
        tracker = cls(
            model=model,
            provider=provider,
            role=role,
            workspace=workspace,
            strategy=strategy,
            fallback_model=fallback_model,
        )

        # Capture trace context
        ctx = get_current_audit_context()
        if ctx:
            tracker._trace_id = ctx.trace_id
            tracker._run_id = ctx.run_id
            tracker._span_id = ctx.span_id

        # Emit start event
        await tracker._emit_start()

        try:
            yield tracker
        except (RuntimeError, ValueError) as exc:
            tracker._error = str(exc)
            tracker._error_type = type(exc).__name__
            tracker._finish_reason = LLMFinishReason.ERROR
            raise
        finally:
            tracker._end_time = datetime.now()
            await tracker._emit_complete()

    def add_prompt(
        self,
        prompt: str,
        prompt_tokens: int | None = None,
    ) -> None:
        """Record prompt details.

        Args:
            prompt: The prompt text.
            prompt_tokens: Token count (optional, will be estimated if None).
        """
        self._prompt = prompt[:500]  # Truncate for storage
        if prompt_tokens is not None:
            self._prompt_tokens = prompt_tokens

    def add_response(
        self,
        completion: str,
        finish_reason: str | None = None,
        completion_tokens: int | None = None,
        prompt_tokens: int | None = None,
    ) -> None:
        """Record response details.

        Args:
            completion: The completion text.
            finish_reason: Stop reason (stop, length, content_filter, error).
            completion_tokens: Token count (optional).
            prompt_tokens: Prompt token count (optional, updates earlier estimate).
        """
        self._completion = completion[:500]  # Truncate for storage
        self._end_time = datetime.now()

        if completion_tokens is not None:
            self._completion_tokens = completion_tokens
        if prompt_tokens is not None:
            self._prompt_tokens = prompt_tokens

        if finish_reason:
            try:
                self._finish_reason = LLMFinishReason(finish_reason.lower())
            except ValueError:
                self._finish_reason = LLMFinishReason.ERROR

    def record_first_token(self) -> None:
        """Record when first token was received (for streaming)."""
        if self._first_token_time is None:
            self._first_token_time = datetime.now()

    async def _emit_start(self) -> None:
        """Emit LLM call start event."""
        bus = OmniscientAuditBus.get_optional()
        if bus is None:
            return

        await bus.emit(
            {
                "event_type": "llm_interaction_start",
                "model": self.model,
                "provider": self.provider,
                "strategy": self.strategy.value,
                "fallback_model": self.fallback_model,
                "role": self.role,
                "workspace": self.workspace,
                "trace_id": self._trace_id,
                "run_id": self._run_id,
                "span_id": self._span_id,
                "start_time": self._start_time.isoformat(),
            },
            priority=AuditPriority.INFO,
        )

    async def _emit_complete(self) -> None:
        """Emit LLM call complete event."""
        bus = OmniscientAuditBus.get_optional()
        if bus is None:
            return

        # Calculate latency
        end_time = self._end_time or datetime.now()
        latency_ms = (end_time - self._start_time).total_seconds() * 1000

        first_token_latency_ms = 0.0
        if self._first_token_time:
            first_token_latency_ms = (self._first_token_time - self._start_time).total_seconds() * 1000

        # Build event
        event = {
            "event_type": "llm_interaction_complete",
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self._prompt_tokens + self._completion_tokens,
            "latency_ms": latency_ms,
            "first_token_latency_ms": first_token_latency_ms,
            "strategy": self.strategy.value,
            "fallback_model": self.fallback_model,
            "finish_reason": self._finish_reason.value if self._finish_reason else None,
            "error": self._error,
            "error_type": self._error_type,
            "prompt_preview": self._prompt,
            "completion_preview": self._completion,
            "role": self.role,
            "workspace": self.workspace,
            "trace_id": self._trace_id,
            "run_id": self._run_id,
            "span_id": self._span_id,
            "is_success": self._error == "" and self._finish_reason != LLMFinishReason.ERROR,
        }

        # Apply sanitization
        sanitizer = get_default_sanitizer()
        event = sanitizer.sanitize(event)

        priority = AuditPriority.INFO
        if self._error or self._finish_reason == LLMFinishReason.ERROR:
            priority = AuditPriority.ERROR

        await bus.emit(event, priority=priority)


# =============================================================================
# LLM Audit Decorator
# =============================================================================


def llm_audit(
    model: str,
    provider: str = "",
    role: str = "",
    strategy: LLMStrategy = LLMStrategy.PRIMARY,
) -> Callable[[F], F]:
    """Decorator to audit an LLM call function.

    Usage:
        @llm_audit("claude-3-sonnet", provider="anthropic", role="director")
        async def generate_response(prompt: str) -> str:
            return await llm.generate(prompt)

    Args:
        model: Model name for the audit event.
        provider: Provider name.
        role: Role making the call.
        strategy: Invocation strategy.

    Returns:
        Decorated function.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracker = LLMCallTracker(
                model=model,
                provider=provider,
                role=role,
                strategy=strategy,
            )

            # Capture trace context
            ctx = get_current_audit_context()
            if ctx:
                tracker._trace_id = ctx.trace_id
                tracker._run_id = ctx.run_id
                tracker._span_id = ctx.span_id

            await tracker._emit_start()

            try:
                result = await func(*args, **kwargs)

                # Try to extract completion from result
                if hasattr(result, "text"):
                    tracker.add_response(result.text)
                elif isinstance(result, str):
                    tracker.add_response(result)

                return result
            except (RuntimeError, ValueError) as exc:
                tracker._error = str(exc)
                tracker._error_type = type(exc).__name__
                tracker._finish_reason = LLMFinishReason.ERROR
                raise
            finally:
                tracker._end_time = datetime.now()
                await tracker._emit_complete()

        return wrapper  # type: ignore

    return decorator


# =============================================================================
# LLM Call Interceptor (Bus subscriber)
# =============================================================================


class LLMCallInterceptor:
    """Bus interceptor for LLM audit events.

    This interceptor subscribes to the OmniscientAuditBus and processes
    LLM interaction events, extracting telemetry and updating metrics.

    Features:
    - Token usage aggregation
    - Latency tracking per model/provider
    - Error rate monitoring
    - Circuit breaker on consecutive failures

    Usage:
        bus = OmniscientAuditBus.get_default()
        await bus.start()
        interceptor = LLMCallInterceptor(bus)
    """

    def __init__(
        self,
        bus: OmniscientAuditBus,
        failure_threshold: int = 5,
    ) -> None:
        """Initialize the LLM interceptor.

        Args:
            bus: The audit bus to subscribe to.
            failure_threshold: Consecutive failures before circuit opens.
        """
        self._bus = bus
        self._bus.subscribe(self._handle_envelope)

        # Metrics
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_latency_ms = 0.0
        self._success_count = 0
        self._error_count = 0
        self._model_counts: dict[str, int] = {}
        self._provider_counts: dict[str, int] = {}

        # Circuit breaker
        self._failure_threshold = failure_threshold
        self._consecutive_failures = 0
        self._circuit_open = False

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        if self._circuit_open:
            return

        try:
            self._process_event(envelope)
        except (RuntimeError, ValueError) as exc:
            logger.error("[llm_interceptor] Error processing event: %s", exc)

    def _process_event(self, envelope: AuditEventEnvelope) -> None:
        """Process an LLM audit event.

        Args:
            envelope: The audit event envelope.
        """
        event = envelope.event
        if not isinstance(event, dict):
            return

        event_type = event.get("event_type", "")

        # Only process complete events
        if event_type not in ("llm_interaction_complete", "llm_interaction_error"):
            return

        # Extract metrics
        prompt_tokens = event.get("prompt_tokens", 0)
        completion_tokens = event.get("completion_tokens", 0)
        latency_ms = event.get("latency_ms", 0.0)
        model = event.get("model", "unknown")
        provider = event.get("provider", "unknown")
        is_success = event.get("is_success", True)
        _error = event.get("error", "")  # kept for future error tracking

        # Update metrics
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens
        self._total_latency_ms += latency_ms
        self._model_counts[model] = self._model_counts.get(model, 0) + 1
        self._provider_counts[provider] = self._provider_counts.get(provider, 0) + 1

        # Track success/error
        if is_success:
            self._success_count += 1
            self._consecutive_failures = 0
        else:
            self._error_count += 1
            self._consecutive_failures += 1
            self._check_failure_threshold()

        logger.debug(
            "[llm_interceptor] LLM event: model=%s, tokens=%d, latency=%.2fms",
            model,
            prompt_tokens + completion_tokens,
            latency_ms,
        )

    def _check_failure_threshold(self) -> None:
        """Check if failure threshold exceeded."""
        if self._consecutive_failures >= self._failure_threshold and not self._circuit_open:
            self._circuit_open = True
            self._bus.open_circuit()
            logger.warning(
                "[llm_interceptor] Circuit opened after %d consecutive failures",
                self._consecutive_failures,
            )

    def get_stats(self) -> dict[str, Any]:
        """Get LLM audit statistics.

        Returns:
            Dictionary with LLM-specific metrics.
        """
        total_calls = self._success_count + self._error_count
        return {
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
            "total_latency_ms": self._total_latency_ms,
            "avg_latency_ms": (self._total_latency_ms / self._success_count if self._success_count > 0 else 0.0),
            "success_count": self._success_count,
            "error_count": self._error_count,
            "success_rate": (self._success_count / total_calls if total_calls > 0 else 0.0),
            "model_counts": dict(self._model_counts),
            "provider_counts": dict(self._provider_counts),
            "circuit_open": self._circuit_open,
        }

    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_latency_ms = 0.0
        self._success_count = 0
        self._error_count = 0
        self._model_counts.clear()
        self._provider_counts.clear()
        self._consecutive_failures = 0


# =============================================================================
# Convenience functions
# =============================================================================


async def emit_llm_event(
    model: str,
    provider: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    strategy: LLMStrategy = LLMStrategy.PRIMARY,
    role: str = "",
    workspace: str = "",
    error: str = "",
    **kwargs: Any,
) -> str:
    """Convenience function to emit an LLM audit event.

    Args:
        model: Model name.
        provider: Provider name.
        prompt_tokens: Input token count.
        completion_tokens: Output token count.
        latency_ms: Wall-clock time in ms.
        strategy: Invocation strategy.
        role: Emitting role.
        workspace: Workspace path.
        error: Error message if failed.
        **kwargs: Additional event fields.

    Returns:
        Envelope ID if emitted, empty string otherwise.
    """
    bus = OmniscientAuditBus.get_optional()
    if bus is None:
        return ""

    # Get current audit context
    ctx = get_current_audit_context()
    trace_id = ctx.trace_id if ctx else ""
    run_id = ctx.run_id if ctx else ""

    # Apply sanitization
    sanitizer = get_default_sanitizer()

    event = {
        "event_type": "llm_interaction_complete",
        "model": model,
        "provider": provider,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "latency_ms": latency_ms,
        "strategy": strategy.value,
        "error": error,
        "role": role,
        "workspace": workspace,
        "trace_id": trace_id,
        "run_id": run_id,
        "is_success": error == "",
        **kwargs,
    }

    event = sanitizer.sanitize(event)

    priority = AuditPriority.INFO
    if error:
        priority = AuditPriority.ERROR

    return await bus.emit(event, priority=priority)


# =============================================================================
# Aliases for backward compatibility
# =============================================================================

# Keep old names as aliases
LLMAuditInterceptor = LLMCallInterceptor
LLMAuditTracker = LLMCallTracker

__all__ = [
    "LLMAuditInterceptor",  # backward compat
    "LLMAuditTracker",  # backward compat
    "LLMCallInterceptor",
    "LLMCallTracker",
    "emit_llm_event",
    "llm_audit",
]
