"""Tests for distributed tracer."""

from __future__ import annotations

from polaris.kernelone.observability.tracer import DistributedTracer, SpanStatus


class TestDistributedTracer:
    """Test cases for DistributedTracer."""

    def test_init(self) -> None:
        """Test tracer initialization."""
        tracer = DistributedTracer("test-service")
        assert tracer.service_name == "test-service"

    def test_start_span(self) -> None:
        """Test starting a new span."""
        tracer = DistributedTracer("test-service")
        span_id = tracer.start_span("test_operation")
        assert span_id is not None
        assert len(span_id) > 0

    def test_start_span_with_tags(self) -> None:
        """Test starting a span with tags."""
        tracer = DistributedTracer("test-service")
        span_id = tracer.start_span("test_operation", tags={"key": "value"})
        assert span_id is not None

    def test_start_span_with_parent(self) -> None:
        """Test starting a span with parent."""
        tracer = DistributedTracer("test-service")
        parent_id = tracer.start_span("parent_operation")
        child_id = tracer.start_span("child_operation", parent_span_id=parent_id)
        assert child_id is not None
        assert child_id != parent_id

    def test_end_span(self) -> None:
        """Test ending a span."""
        tracer = DistributedTracer("test-service")
        span_id = tracer.start_span("test_operation")
        tracer.end_span(span_id)
        assert span_id not in tracer._active_spans

    def test_end_span_with_status(self) -> None:
        """Test ending a span with status."""
        tracer = DistributedTracer("test-service")
        span_id = tracer.start_span("test_operation")
        tracer.end_span(span_id, SpanStatus.ERROR)
        completed = [s for s in tracer._completed_spans if s.span_id == span_id]
        assert len(completed) == 1
        assert completed[0].status == SpanStatus.ERROR

    def test_record_exception(self) -> None:
        """Test recording an exception in a span."""
        tracer = DistributedTracer("test-service")
        span_id = tracer.start_span("test_operation")
        tracer.record_exception(span_id, ValueError("test error"))
        span = tracer._active_spans.get(span_id)
        assert span is not None
        assert span.status == SpanStatus.ERROR
        assert len(span.logs) > 0
        log = span.logs[-1]
        assert log["event"] == "exception"
        assert log["exception.type"] == "ValueError"

    def test_get_trace(self) -> None:
        """Test getting all spans for a trace."""
        tracer = DistributedTracer("test-service")
        span_id = tracer.start_span("operation1")
        tracer.end_span(span_id)
        span = tracer._active_spans.get(span_id)
        trace_id = span.trace_id if span else None
        if trace_id:
            trace = tracer.get_trace(trace_id)
            assert len(trace) >= 1

    def test_get_traces(self) -> None:
        """Test getting recent traces."""
        tracer = DistributedTracer("test-service")
        for i in range(5):
            span_id = tracer.start_span(f"operation_{i}")
            tracer.end_span(span_id)
        traces = tracer.get_traces(limit=3)
        assert len(traces) <= 3

    def test_span_duration_ms(self) -> None:
        """Test span duration calculation."""
        tracer = DistributedTracer("test-service")
        span_id = tracer.start_span("test_operation")
        tracer.end_span(span_id)
        completed = [s for s in tracer._completed_spans if s.span_id == span_id]
        assert len(completed) == 1
        assert completed[0].duration_ms >= 0
