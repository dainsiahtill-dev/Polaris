"""Tracing Audit Interceptor — bridges OmniscientAuditBus to Polaris UnifiedTracer.

This interceptor subscribes to the audit bus and creates structured spans for
every audit event, integrating with the existing Polaris tracing infrastructure.

Design:
- Subscribes to the OmniscientAuditBus
- Creates a span per audit envelope (event_type as span name)
- Uses AuditContext.run_id as trace_id for cross-event correlation
- Maps AuditPriority to SpanStatus
- Records spans to UnifiedTracer (persisted to JSONL if storage configured)
- Non-blocking: no synchronous I/O in the dispatch path
- Builds on existing Polaris UnifiedTracer without modifying it
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.bus import AuditEventEnvelope, AuditPriority
from polaris.kernelone.audit.omniscient.interceptors.base import BaseAuditInterceptor

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus
    from polaris.kernelone.trace.tracer import UnifiedTracer

logger = logging.getLogger(__name__)


def _map_priority_to_status(priority: AuditPriority, has_error: bool) -> str:
    """Map AuditPriority and error flag to SpanStatus string.

    Args:
        priority: The audit event priority.
        has_error: Whether the event contains an error.

    Returns:
        SpanStatus value string.
    """
    if has_error:
        return "error"
    priority_str = str(priority.name)
    status_map = {
        "CRITICAL": "error",
        "ERROR": "error",
        "WARNING": "ok",
        "INFO": "ok",
        "DEBUG": "ok",
    }
    return status_map.get(priority_str, "ok")


def _build_span_tags(event_data: dict[str, Any]) -> dict[str, Any]:
    """Build span tags from event data.

    Args:
        event_data: The raw event dict.

    Returns:
        Dict of tags for the span.
    """
    tags: dict[str, Any] = {}

    # Core event identification
    tags["event_type"] = event_data.get("type", "unknown")

    # LLM-specific tags
    if "model" in event_data:
        tags["llm.model"] = event_data["model"]
    if "provider" in event_data:
        tags["llm.provider"] = event_data["provider"]
    if "total_tokens" in event_data:
        tags["llm.tokens"] = event_data["total_tokens"]
    if "latency_ms" in event_data:
        tags["llm.latency_ms"] = event_data["latency_ms"]

    # Tool-specific tags
    if "tool_name" in event_data:
        tags["tool.name"] = event_data["tool_name"]
    if "duration_ms" in event_data:
        tags["tool.duration_ms"] = event_data["duration_ms"]
    if "success" in event_data:
        tags["tool.success"] = event_data["success"]

    # Task-specific tags
    if "task_id" in event_data:
        tags["task.id"] = event_data["task_id"]
    if "state" in event_data:
        tags["task.state"] = event_data["state"]
    if "dag_id" in event_data:
        tags["task.dag_id"] = event_data["dag_id"]

    # Agent communication tags
    if "sender_role" in event_data:
        tags["agent.sender"] = event_data["sender_role"]
    if "receiver_role" in event_data:
        tags["agent.receiver"] = event_data["receiver_role"]
    if "intent" in event_data:
        tags["agent.intent"] = event_data["intent"]

    # Context management tags
    if "occupancy_pct" in event_data:
        tags["context.occupancy_pct"] = event_data["occupancy_pct"]
    if "operation" in event_data:
        tags["context.operation"] = event_data["operation"]

    # Error flag
    if event_data.get("error"):
        tags["error"] = True
        tags["error.message"] = str(event_data["error"])[:200]

    return tags


class TracingAuditInterceptor(BaseAuditInterceptor):
    """Interceptor that bridges audit bus to Polaris UnifiedTracer.

    Creates structured spans for every audit event envelope, enabling:
    - End-to-end trace of audit event flow
    - Correlation via AuditContext.run_id as trace_id
    - Span attributes for event classification (LLM/Tool/Task/Agent/Context)
    - Error status propagation from AuditPriority + error flag

    Each span captures the event at emission time with all metadata attributes.
    Duration is set to 0 since dispatch is async and fire-and-forget — the
    span serves as a structured log entry, not a performance measurement.

    Usage:
        bus = OmniscientAuditBus.get_default()
        await bus.start()
        tracing_int = TracingAuditInterceptor(bus)
        # Spans are automatically created for each emitted event
    """

    def __init__(
        self,
        bus: OmniscientAuditBus,
        tracer: UnifiedTracer | None = None,
    ) -> None:
        """Initialize the tracing interceptor.

        Args:
            bus: The audit bus to subscribe to.
            tracer: Optional tracer instance. Uses global tracer if None.
        """
        super().__init__(name="tracing", priority=AuditPriority.DEBUG)
        self._bus = bus
        self._bus.subscribe(self._handle_envelope)

        self._tracer = tracer

    def _get_tracer(self) -> UnifiedTracer:
        """Get the tracer instance (lazy import to avoid circular deps).

        Returns:
            UnifiedTracer instance.
        """
        if self._tracer is None:
            from polaris.kernelone.trace import get_tracer

            self._tracer = get_tracer()
        return self._tracer

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        self.intercept(envelope)

    def intercept(self, event: Any) -> None:
        """Process an audit event and create a trace span.

        Creates a span at emission time with all event metadata as attributes.
        The span is immediately ended (0 duration) since the actual dispatch
        is async and fire-and-forget — the span serves as a structured log.

        Args:
            event: The audit event (AuditEventEnvelope or dict).
        """
        # Always call base first for stats tracking
        super().intercept(event)

        # Extract envelope
        if isinstance(event, AuditEventEnvelope):
            envelope = event
            event_data = event.event
        elif isinstance(event, dict):
            envelope = None
            event_data = event
        else:
            return

        if not isinstance(event_data, dict):
            return

        # Extract trace_id from correlation context
        trace_id: str | None = None
        parent_span_id: str | None = None

        if envelope is not None and envelope.correlation_context is not None:
            ctx = envelope.correlation_context
            trace_id = ctx.run_id or None
            if ctx.turn_id:
                parent_span_id = f"turn-{ctx.turn_id}"

        # Build span name
        event_type = event_data.get("type", "unknown")
        span_name = f"audit.{event_type}"

        # Build span tags
        tags = _build_span_tags(event_data)
        if envelope is not None:
            tags["audit.priority"] = envelope.priority.name
            tags["audit.envelope_id"] = envelope.envelope_id

        # Determine error state
        has_error = bool(event_data.get("error"))

        try:
            tracer = self._get_tracer()
            span = tracer.start_span(
                span_name,
                tags=tags,
                trace_id=trace_id,
                parent_span_id=parent_span_id,
            )

            # Immediately end span with correct status.
            # Duration = 0 is intentional: this is a structured log entry
            # at emission time, not a performance measurement.
            from polaris.kernelone.trace.tracer import SpanStatus

            span_status = SpanStatus.ERROR if has_error else SpanStatus.OK
            error_msg = str(event_data.get("error", ""))[:200] if has_error else None
            tracer.end_span(span, status=span_status, status_message=error_msg)

        except (RuntimeError, ValueError):
            # Never propagate errors from tracing
            logger.debug(
                "[tracing_interceptor] Failed to create span: event_type=%s",
                event_type,
            )

    def get_stats(self) -> dict[str, Any]:
        """Get tracing interceptor statistics.

        Returns:
            Dictionary with tracing-specific metrics.
        """
        return super().get_stats()
