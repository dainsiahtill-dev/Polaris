"""LLM Audit Interceptor for tracking LLM interactions.

This interceptor subscribes to the OmniscientAuditBus and processes
LLM interaction events, capturing usage metrics, latency, and errors.

Design:
- Subscribes to LLM interaction events from the bus
- Extracts metadata from AIRequest/AIResponse objects
- Aggregates metrics (token usage, latency, errors)
- Works WITHOUT modifying existing LLM code
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.bus import AuditEventEnvelope, AuditPriority
from polaris.kernelone.audit.omniscient.interceptors.base import BaseAuditInterceptor

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus

logger = logging.getLogger(__name__)


class LLMAuditInterceptor(BaseAuditInterceptor):
    """Interceptor for auditing LLM interactions.

    Captures:
    - Prompt tokens, completion tokens, total tokens
    - Latency (ms)
    - Model name and provider
    - Finish reason
    - Safety flags
    - Error details

    Usage:
        bus = OmniscientAuditBus.get_default()
        await bus.start()
        interceptor = LLMAuditInterceptor(bus)
        # Events will be automatically processed
    """

    def __init__(
        self,
        bus: OmniscientAuditBus,
        failure_threshold: int = 5,
        window_seconds: int = 60,
    ) -> None:
        """Initialize the LLM audit interceptor.

        Args:
            bus: The audit bus to subscribe to.
            failure_threshold: Number of consecutive failures to open circuit.
            window_seconds: Time window for failure tracking.
        """
        super().__init__(name="llm_audit", priority=AuditPriority.INFO)
        self._bus = bus
        self._bus.subscribe(self._handle_envelope)

        # Metrics tracking
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_tokens = 0
        self._total_latency_ms = 0.0
        self._error_count = 0
        self._success_count = 0
        self._model_counts: dict[str, int] = {}
        self._provider_counts: dict[str, int] = {}

        # Failure tracking for circuit breaker
        self._failure_threshold = failure_threshold
        self._window_seconds = window_seconds
        self._consecutive_failures = 0
        self._last_failure_time: float | None = None

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        self.intercept(envelope)

    def intercept(self, event: Any) -> None:
        """Process an LLM audit event.

        Args:
            event: The audit event (AuditEventEnvelope or dict).
        """
        # Call base implementation first for stats tracking
        super().intercept(event)

        # Extract event data
        if isinstance(event, AuditEventEnvelope):
            event_data = event.event
        elif isinstance(event, dict):
            event_data = event
        else:
            return

        # Process LLM interaction events
        if isinstance(event_data, dict):
            event_type = event_data.get("type", "")
            if event_type in ("llm_interaction", "llm_interaction_complete", "llm_interaction_error"):
                self._process_llm_event(event_data)

    def _process_llm_event(self, event: dict[str, Any]) -> None:
        """Process a single LLM interaction event.

        Args:
            event: The LLM interaction event dict.
        """
        event_type = event.get("type", "")

        # Extract metrics
        prompt_tokens = event.get("prompt_tokens", 0)
        completion_tokens = event.get("completion_tokens", 0)
        total_tokens = event.get("total_tokens", 0)
        latency_ms = event.get("latency_ms", 0.0)
        model = event.get("model", "unknown")
        provider = event.get("provider", "unknown")
        error = event.get("error")

        # Update metrics
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens
        self._total_tokens += total_tokens
        self._total_latency_ms += latency_ms

        # Track model and provider usage
        self._model_counts[model] = self._model_counts.get(model, 0) + 1
        self._provider_counts[provider] = self._provider_counts.get(provider, 0) + 1

        # Track success/error
        if event_type == "llm_interaction_error" or error:
            self._error_count += 1
            self._consecutive_failures += 1
            self._check_failure_threshold()
        else:
            self._success_count += 1
            self._consecutive_failures = 0

        logger.debug(
            "[llm_audit] Processed LLM event: model=%s, tokens=%d, latency=%.2fms, error=%s",
            model,
            total_tokens,
            latency_ms,
            error,
        )

    def _check_failure_threshold(self) -> None:
        """Check if failure threshold is exceeded and open circuit if needed."""
        if self._consecutive_failures >= self._failure_threshold and not self._circuit_open:
            self.open_circuit()
            logger.warning(
                "[llm_audit] Circuit breaker opened after %d consecutive failures",
                self._consecutive_failures,
            )

    def get_stats(self) -> dict[str, Any]:
        """Get LLM audit statistics.

        Returns:
            Dictionary with LLM-specific metrics.
        """
        base_stats = super().get_stats()
        return {
            **base_stats,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_tokens,
            "total_latency_ms": self._total_latency_ms,
            "avg_latency_ms": (self._total_latency_ms / self._success_count if self._success_count > 0 else 0.0),
            "error_count": self._error_count,
            "success_count": self._success_count,
            "success_rate": (
                self._success_count / (self._success_count + self._error_count)
                if (self._success_count + self._error_count) > 0
                else 0.0
            ),
            "model_counts": dict(self._model_counts),
            "provider_counts": dict(self._provider_counts),
        }

    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        super().reset_stats()
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_tokens = 0
        self._total_latency_ms = 0.0
        self._error_count = 0
        self._success_count = 0
        self._model_counts.clear()
        self._provider_counts.clear()
        self._consecutive_failures = 0
        self._last_failure_time = None


class LLMAuditWrapper:
    """Wrapper/decorator for LLM provider calls.

    This class provides a decorator pattern for wrapping LLM calls
    to automatically emit audit events without modifying existing code.

    Usage:
        wrapper = LLMAuditWrapper(bus)

        # Wrap a provider call
        original_generate = provider.generate
        wrapped_generate = wrapper.wrap_generate(original_generate)
        provider.generate = wrapped_generate
    """

    def __init__(self, bus: OmniscientAuditBus) -> None:
        """Initialize the LLM audit wrapper.

        Args:
            bus: The audit bus for emitting events.
        """
        self._bus = bus

    def wrap_generate(
        self,
        generate_func: Any,
    ) -> Any:
        """Wrap a provider's generate method.

        Args:
            generate_func: The generate function to wrap.

        Returns:
            Wrapped function that emits audit events.
        """
        import functools

        @functools.wraps(generate_func)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            async with self._bus.track_llm_interaction("llm_wrapper"):
                return await generate_func(*args, **kwargs)

        return wrapped
