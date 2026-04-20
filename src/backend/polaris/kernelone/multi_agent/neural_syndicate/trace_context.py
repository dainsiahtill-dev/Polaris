"""Neural Syndicate Trace Context - OpenTelemetry-compatible distributed tracing.

This module provides trace context propagation for multi-agent message passing.

Key features:
1. **Trace ID extraction/injection**: Extracts trace context from incoming
   AgentMessage and injects into outgoing messages.
2. **Span creation**: Creates spans for message processing and forwarding.
3. **W3C Trace Context compatible**: Uses standard traceparent format.

Design decisions:
- Trace context is carried in AgentMessage.trace_id and AgentMessage.span_id
- Uses W3C Trace Context format for traceparent header
- Integrates with UnifiedTracer from polaris.kernelone.trace

Usage:
    from polaris.kernelone.multi_agent.neural_syndicate.trace_context import (
        extract_trace_context,
        inject_trace_context,
        create_message_span,
    )

    # Extract trace from incoming message
    trace_ctx = extract_trace_context(message)

    # Create span for processing
    with create_message_span("process_message", trace_ctx) as span:
        # Process message
        ...

    # Inject trace into outgoing message
    outgoing = inject_trace_context(original_message, trace_ctx)
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.multi_agent.neural_syndicate.protocol import AgentMessage

if TYPE_CHECKING:
    from collections.abc import Generator

    from polaris.kernelone.trace.tracer import Span

logger = logging.getLogger(__name__)

# W3C Trace Context version
TRACE_CONTEXT_VERSION = "00"

# Maximum trace_id length (32 hex chars)
_MAX_TRACE_ID_LEN = 32

# Maximum span_id length (16 hex chars)
_MAX_SPAN_ID_LEN = 16


@dataclass(frozen=True)
class TraceContext:
    """Immutable trace context for distributed tracing.

    Attributes:
        trace_id: 32-char hex trace identifier
        span_id: 16-char hex span identifier
        trace_flags: Trace flags (0x01 = sampled)
    """

    trace_id: str
    span_id: str
    trace_flags: str = "01"

    @classmethod
    def from_message(cls, message: AgentMessage) -> TraceContext | None:
        """Extract trace context from an AgentMessage.

        Args:
            message: The message to extract from

        Returns:
            TraceContext if trace data exists, None otherwise
        """
        if not message.trace_id:
            return None

        return cls(
            trace_id=message.trace_id,
            span_id=message.span_id or _generate_span_id(),
            trace_flags="01",
        )

    @classmethod
    def new(cls) -> TraceContext:
        """Create a new trace context with fresh IDs.

        Returns:
            New TraceContext with generated IDs
        """
        return cls(
            trace_id=_generate_trace_id(),
            span_id=_generate_span_id(),
            trace_flags="01",
        )

    def to_traceparent(self) -> str:
        """Convert to W3C Trace Context traceparent header.

        Returns:
            traceparent header value: version-trace_id-span_id-trace_flags
        """
        return f"{TRACE_CONTEXT_VERSION}-{self.trace_id}-{self.span_id}-{self.trace_flags}"

    @classmethod
    def from_traceparent(cls, traceparent: str) -> TraceContext | None:
        """Parse W3C Trace Context traceparent header.

        Args:
            traceparent: traceparent header value

        Returns:
            TraceContext if valid, None otherwise
        """
        try:
            parts = traceparent.split("-")
            if len(parts) != 4:
                return None

            _version, trace_id, span_id, flags = parts

            if len(trace_id) != _MAX_TRACE_ID_LEN:
                return None
            if len(span_id) != _MAX_SPAN_ID_LEN:
                return None

            return cls(
                trace_id=trace_id,
                span_id=span_id,
                trace_flags=flags,
            )
        except (RuntimeError, ValueError):
            return None

    def with_new_span(self) -> TraceContext:
        """Create new context with a new span ID (for creating child spans).

        Returns:
            New TraceContext with same trace_id but new span_id
        """
        return TraceContext(
            trace_id=self.trace_id,
            span_id=_generate_span_id(),
            trace_flags=self.trace_flags,
        )


def _generate_trace_id() -> str:
    """Generate a 32-char hex trace ID.

    Returns:
        32-character hex string
    """
    import uuid

    return uuid.uuid4().hex


def _generate_span_id() -> str:
    """Generate a 16-char hex span ID.

    Returns:
        16-character hex string
    """
    import uuid

    return uuid.uuid4().hex[:16]


def extract_trace_context(message: AgentMessage) -> TraceContext | None:
    """Extract trace context from an AgentMessage.

    Args:
        message: The message to extract from

    Returns:
        TraceContext if trace_id exists, None otherwise
    """
    return TraceContext.from_message(message)


def inject_trace_context(
    message: AgentMessage,
    trace_ctx: TraceContext,
) -> AgentMessage:
    """Inject trace context into an AgentMessage.

    Args:
        message: The message to inject into
        trace_ctx: The trace context to inject

    Returns:
        New AgentMessage with trace context
    """
    # Create new message with updated trace context
    return AgentMessage(
        message_id=message.message_id,
        timestamp_utc=message.timestamp_utc,
        sender=message.sender,
        receiver=message.receiver,
        performative=message.performative,
        intent=message.intent,
        message_type=message.message_type,
        payload=message.payload,
        correlation_id=message.correlation_id,
        in_reply_to=message.in_reply_to,
        trace_id=trace_ctx.trace_id,
        span_id=trace_ctx.span_id,
        ttl=message.ttl,
        hop_count=message.hop_count,
        priority=message.priority,
        deadline_utc=message.deadline_utc,
        metadata=message.metadata,
    )


@contextmanager
def create_message_span(
    span_name: str,
    trace_ctx: TraceContext | None,
    *,
    tags: dict[str, Any] | None = None,
) -> Generator[Span, None, None]:
    """Create a span for message processing.

    Args:
        span_name: Name for the span
        trace_ctx: Trace context (creates new if None)
        tags: Optional span tags

    Yields:
        The created Span
    """
    from polaris.kernelone.trace import get_tracer

    tracer = get_tracer()

    # Determine trace IDs
    if trace_ctx is None:
        trace_ctx = TraceContext.new()

    span_tags = tags or {}
    span_tags["agent.trace_id"] = trace_ctx.trace_id
    span_tags["agent.span_id"] = trace_ctx.span_id

    try:
        span = tracer.start_span(
            span_name,
            trace_id=trace_ctx.trace_id,
            parent_span_id=trace_ctx.span_id,
            tags=span_tags,
        )
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to create message span: %s",
            exc,
        )
        # Return dummy span for error recovery - caller gets a valid span
        from polaris.kernelone.trace.tracer import Span

        dummy_span = Span(
            span_id=_generate_span_id(),
            name=span_name,
            trace_id=trace_ctx.trace_id,
        )
        yield dummy_span
        return

    try:
        yield span
    finally:
        from polaris.kernelone.trace.tracer import SpanStatus

        tracer.end_span(span, status=SpanStatus.OK)


def propagate_trace_context(
    original: AgentMessage,
    forwarded: AgentMessage,
) -> AgentMessage:
    """Propagate trace context from original to forwarded message.

    When a message is forwarded, the span context should be updated
    to reflect the forwarding hop.

    Args:
        original: The original message
        forwarded: The forwarded message (with hop_count+1)

    Returns:
        New forwarded message with propagated trace context
    """
    original_ctx = extract_trace_context(original)

    if original_ctx is None:
        return forwarded

    # Create new span for the forward hop
    new_ctx = original_ctx.with_new_span()

    return inject_trace_context(forwarded, new_ctx)


__all__ = [
    "TraceContext",
    "create_message_span",
    "extract_trace_context",
    "inject_trace_context",
    "propagate_trace_context",
]
