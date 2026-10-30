"""LLM Provider Runtime Audit Interceptor.

Provides LLMProviderAuditInterceptor for integrating audit tracing
into the provider runtime invoke path without modifying existing code.

Features:
- Automatic trace_id propagation from UnifiedAuditContext
- Token usage extraction from RuntimeProviderInvokeResult.usage
- Strategy tracking (primary/fallback) via model comparison
- Provider switching detection
- Complete event emission to OmniscientAuditBus

Usage:
    from polaris.kernelone.audit.omniscient.interceptors.llm_provider_integration import (
        LLMProviderAuditInterceptor,
    )

    interceptor = LLMProviderAuditInterceptor()
    interceptor.attach_to_bus()

    # Now all invoke_role_runtime_provider calls will be audited
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.bus import (
    AuditEventEnvelope,
    AuditPriority,
    OmniscientAuditBus,
)
from polaris.kernelone.audit.omniscient.context_manager import (
    get_current_audit_context,
)
from polaris.kernelone.audit.omniscient.interceptors.base import BaseAuditInterceptor
from polaris.kernelone.audit.omniscient.schemas.llm_event import (
    LLMEvent,
    LLMFinishReason,
    LLMStrategy,
)

if TYPE_CHECKING:
    from polaris.kernelone.llm import RuntimeProviderInvokeResult

logger = logging.getLogger(__name__)

# Fields expected in RuntimeProviderInvokeResult.usage dict
_USAGE_PROMPT_TOKENS_KEYS: tuple[str, ...] = (
    "prompt_tokens",
    "input_tokens",
    "PromptTokens",
    "prompt_tokens_used",
)
_USAGE_COMPLETION_TOKENS_KEYS: tuple[str, ...] = (
    "completion_tokens",
    "output_tokens",
    "CompletionTokens",
    "completion_tokens_used",
)


class LLMProviderAuditInterceptor(BaseAuditInterceptor):
    """Interceptor that bridges provider runtime to Omniscient Audit Bus.

    This interceptor subscribes to the bus and processes provider runtime
    invoke results, converting them to structured LLMEvent audit records.

    It does NOT wrap the provider directly. Instead, it expects the
    runtime_invoke module to emit events through the bus, and this
    interceptor enriches/validates and forwards them.

    Alternatively, it can be used directly to emit events:

        interceptor = LLMProviderAuditInterceptor()
        interceptor.emit_runtime_result(
            result=invoke_result,
            role="director",
            workspace="/path/to/workspace",
            fallback_model="claude-3-haiku",
        )

    Attributes:
        total_calls: Total number of LLM calls processed.
        total_prompt_tokens: Cumulative prompt tokens.
        total_completion_tokens: Cumulative completion tokens.
        total_latency_ms: Cumulative latency in ms.
    """

    def __init__(
        self,
        bus: OmniscientAuditBus | None = None,
        failure_threshold: int = 5,
    ) -> None:
        """Initialize the LLM provider audit interceptor.

        Args:
            bus: Audit bus to subscribe to. Uses default if None.
            failure_threshold: Consecutive failures before circuit opens.
        """
        super().__init__(name="llm_provider_audit", priority=AuditPriority.INFO)
        self._bus = bus
        self._failure_threshold = failure_threshold
        self._consecutive_failures = 0

        # Metrics
        self.total_calls: int = 0
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.total_latency_ms: float = 0.0

    def attach_to_bus(self) -> None:
        """Attach this interceptor to the default audit bus."""
        bus = self._bus or OmniscientAuditBus.get_default()
        self._bus = bus
        bus.subscribe(self._handle_envelope)

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        self.intercept(envelope)

    def intercept(self, event: Any) -> None:
        """Process an audit event.

        Args:
            event: The audit event (AuditEventEnvelope or dict).
        """
        if isinstance(event, AuditEventEnvelope):
            event_data: dict[str, Any] = event.event if isinstance(event.event, dict) else {}
        elif isinstance(event, dict):
            event_data = event
        else:
            return

        event_type = event_data.get("event_type", "")
        if event_type not in (
            "llm_interaction_start",
            "llm_interaction_complete",
            "llm_interaction_error",
        ):
            return

        self._update_metrics(event_data)

    def _update_metrics(self, event: dict[str, Any]) -> None:
        """Update internal metrics from an LLM event.

        Args:
            event: LLM event dict.
        """
        is_success = event.get("is_success", True)

        self.total_calls += 1
        self.total_prompt_tokens += event.get("prompt_tokens", 0)
        self.total_completion_tokens += event.get("completion_tokens", 0)
        self.total_latency_ms += event.get("latency_ms", 0.0)

        if is_success:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self.open_circuit()
                logger.warning(
                    "[llm_provider_audit] Circuit opened after %d consecutive failures",
                    self._consecutive_failures,
                )

    def emit_runtime_result(
        self,
        result: RuntimeProviderInvokeResult,
        role: str,
        workspace: str,
        fallback_model: str = "",
        prompt: str = "",
    ) -> str:
        """Emit an audit event from a RuntimeProviderInvokeResult.

        This is the primary integration point for runtime_invoke.py.
        Call this after each provider invocation to record the event.

        Args:
            result: The runtime invoke result.
            role: Role that made the call.
            workspace: Workspace path.
            fallback_model: Fallback model configured (if any).
            prompt: The prompt text (for audit trail).

        Returns:
            Envelope ID if emitted, empty string if bus unavailable.
        """
        bus = self._bus or OmniscientAuditBus.get_optional()
        if bus is None:
            return ""

        # Get trace context
        ctx = get_current_audit_context()
        trace_id = ctx.trace_id if ctx else ""
        run_id = ctx.run_id if ctx else ""
        span_id = ctx.span_id if ctx else ""

        # Determine strategy
        model = result.model or ""
        strategy = LLMStrategy.FALLBACK if fallback_model and fallback_model == model else LLMStrategy.PRIMARY

        # Extract token usage
        prompt_tokens, completion_tokens = _extract_usage(result.usage)

        # Build event
        event = LLMEvent(
            model=model,
            provider=result.provider_type or "",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=float(result.latency_ms or 0),
            strategy=strategy,
            fallback_model=fallback_model if strategy == LLMStrategy.FALLBACK else "",
            finish_reason=LLMFinishReason.STOP if result.ok else LLMFinishReason.ERROR,
            error=result.error or "",
            error_type=_error_type_from_error(result.error) if result.error else "",
            prompt_preview=prompt[:500] if prompt else "",
            completion_preview=(result.output or "")[:500],
            role=role,
            workspace=workspace,
            trace_id=trace_id,
            run_id=run_id,
            span_id=span_id,
        )

        priority = AuditPriority.INFO
        if not result.ok:
            priority = AuditPriority.ERROR

        # Emit via bus
        try:
            return bus.emit_sync(event.to_audit_dict(), priority=priority)
        except (RuntimeError, ValueError) as exc:
            logger.debug("[llm_provider_audit] Failed to emit event: %s", exc)
            return ""

    def get_stats(self) -> dict[str, Any]:
        """Get LLM provider audit statistics.

        Returns:
            Dictionary with metrics.
        """
        return {
            "total_calls": self.total_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "total_latency_ms": self.total_latency_ms,
            "avg_latency_ms": (self.total_latency_ms / self.total_calls if self.total_calls > 0 else 0.0),
            "circuit_open": self._circuit_open,
        }


def _extract_usage(usage: Any) -> tuple[int, int]:
    """Extract prompt and completion tokens from usage dict.

    Args:
        usage: Provider-specific usage dict or None.

    Returns:
        Tuple of (prompt_tokens, completion_tokens).
    """
    if not isinstance(usage, dict):
        return 0, 0

    prompt_tokens = 0
    for key in _USAGE_PROMPT_TOKENS_KEYS:
        if key in usage:
            val = usage[key]
            if isinstance(val, int) and val > 0:
                prompt_tokens = val
                break

    completion_tokens = 0
    for key in _USAGE_COMPLETION_TOKENS_KEYS:
        if key in usage:
            val = usage[key]
            if isinstance(val, int) and val > 0:
                completion_tokens = val
                break

    return prompt_tokens, completion_tokens


def _error_type_from_error(error: str) -> str:
    """Categorize error string into error type.

    Args:
        error: Error message string.

    Returns:
        Error type category string.
    """
    error_lower = error.lower()
    if "timeout" in error_lower or "timed out" in error_lower:
        return "timeout"
    if "auth" in error_lower or "api_key" in error_lower or "unauthorized" in error_lower:
        return "auth"
    if "rate_limit" in error_lower or "rate limit" in error_lower or "throttle" in error_lower:
        return "rate_limit"
    if "connection" in error_lower or "network" in error_lower:
        return "network"
    if "validation" in error_lower or "invalid" in error_lower:
        return "validation"
    if "not found" in error_lower or "404" in error_lower:
        return "not_found"
    return "unknown"


__all__ = [
    "LLMProviderAuditInterceptor",
]
