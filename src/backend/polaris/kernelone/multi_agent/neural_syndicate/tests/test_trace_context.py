"""Unit tests for Neural Syndicate trace context module."""

from __future__ import annotations

from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentMessage,
    Intent,
    Performative,
)
from polaris.kernelone.multi_agent.neural_syndicate.trace_context import (
    TraceContext,
    _generate_span_id,
    _generate_trace_id,
    extract_trace_context,
    inject_trace_context,
    propagate_trace_context,
)


class TestTraceContext:
    """Tests for TraceContext dataclass."""

    def test_new_creates_valid_context(self) -> None:
        """TraceContext.new() should create valid IDs."""
        ctx = TraceContext.new()
        assert len(ctx.trace_id) == 32
        assert len(ctx.span_id) == 16
        assert ctx.trace_flags == "01"

    def test_from_message_with_trace(self) -> None:
        """from_message should extract trace context from message."""
        msg = AgentMessage(
            sender="sender",
            receiver="receiver",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            trace_id="abc123" * 5 + "abcd",
            span_id="span1234567890ab",
        )
        ctx = TraceContext.from_message(msg)
        assert ctx is not None
        assert ctx.trace_id == "abc123" * 5 + "abcd"
        assert ctx.span_id == "span1234567890ab"

    def test_from_message_without_trace(self) -> None:
        """from_message should return None when no trace_id."""
        msg = AgentMessage(
            sender="sender",
            receiver="receiver",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        ctx = TraceContext.from_message(msg)
        assert ctx is None

    def test_to_traceparent(self) -> None:
        """to_traceparent should return W3C format."""
        ctx = TraceContext(
            trace_id="0" * 32,
            span_id="1" * 16,
            trace_flags="01",
        )
        parent = ctx.to_traceparent()
        assert parent == f"00-{'0' * 32}-{'1' * 16}-01"

    def test_from_traceparent_valid(self) -> None:
        """from_traceparent should parse valid header."""
        traceparent = f"00-{'0' * 32}-{'1' * 16}-01"
        ctx = TraceContext.from_traceparent(traceparent)
        assert ctx is not None
        assert ctx.trace_id == "0" * 32
        assert ctx.span_id == "1" * 16
        assert ctx.span_id == "1" * 16

    def test_from_traceparent_invalid(self) -> None:
        """from_traceparent should return None for invalid header."""
        ctx = TraceContext.from_traceparent("invalid")
        assert ctx is None

    def test_from_traceparent_wrong_length(self) -> None:
        """from_traceparent should return None for wrong lengths."""
        ctx = TraceContext.from_traceparent("00-abc-def-01")
        assert ctx is None

    def test_with_new_span(self) -> None:
        """with_new_span should keep trace_id but new span_id."""
        ctx = TraceContext(
            trace_id="a" * 32,
            span_id="b" * 16,
            trace_flags="01",
        )
        new_ctx = ctx.with_new_span()
        assert new_ctx.trace_id == ctx.trace_id
        assert new_ctx.span_id != ctx.span_id
        assert len(new_ctx.span_id) == 16


class TestGenerateFunctions:
    """Tests for ID generation functions."""

    def test_generate_trace_id_length(self) -> None:
        """_generate_trace_id should return 32-char hex."""
        trace_id = _generate_trace_id()
        assert len(trace_id) == 32
        assert all(c in "0123456789abcdef" for c in trace_id)

    def test_generate_span_id_length(self) -> None:
        """_generate_span_id should return 16-char hex."""
        span_id = _generate_span_id()
        assert len(span_id) == 16
        assert all(c in "0123456789abcdef" for c in span_id)

    def test_generate_trace_id_unique(self) -> None:
        """_generate_trace_id should generate unique IDs."""
        ids = {_generate_trace_id() for _ in range(100)}
        assert len(ids) == 100


class TestExtractInjectTraceContext:
    """Tests for extract and inject functions."""

    def test_extract_with_trace(self) -> None:
        """extract_trace_context should return context when trace_id exists."""
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            trace_id="trace123" * 4 + "1234",
            span_id="span1234567890ab",
        )
        ctx = extract_trace_context(msg)
        assert ctx is not None
        assert ctx.trace_id == msg.trace_id

    def test_extract_without_trace(self) -> None:
        """extract_trace_context should return None when no trace_id."""
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        ctx = extract_trace_context(msg)
        assert ctx is None

    def test_inject_trace_context(self) -> None:
        """inject_trace_context should inject into message."""
        original = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        ctx = TraceContext(
            trace_id="newtrace123" * 3 + "newt",
            span_id="newspan123456789a",
        )
        injected = inject_trace_context(original, ctx)

        assert injected.trace_id == ctx.trace_id
        assert injected.span_id == ctx.span_id
        # Original fields preserved
        assert injected.sender == original.sender
        assert injected.receiver == original.receiver
        assert injected.performative == original.performative


class TestPropagateTraceContext:
    """Tests for propagate_trace_context function."""

    def test_propagate_with_existing_trace(self) -> None:
        """propagate_trace_context should propagate to forwarded message."""
        original = AgentMessage(
            sender="orchestrator",
            receiver="",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            trace_id="original_trace123" * 2 + "ori",
            span_id="original_span123456",
            ttl=10,
            hop_count=0,
        )
        forwarded = original.model_copy()
        forwarded.__dict__["_hop_count"] = 1  # Simulate forward

        propagated = propagate_trace_context(original, forwarded)

        assert propagated.trace_id == original.trace_id
        # New span_id should be generated (different from original)
        assert propagated.span_id != original.span_id

    def test_propagate_without_trace(self) -> None:
        """propagate_trace_context should pass through when no trace."""
        original = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        forwarded = original.model_copy()

        propagated = propagate_trace_context(original, forwarded)

        # Should pass through unchanged
        assert propagated.trace_id is None
