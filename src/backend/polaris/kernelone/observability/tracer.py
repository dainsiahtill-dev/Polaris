"""Distributed tracing system for Polaris."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.constants import MAX_COMPLETED_SPANS, MAX_TRACES


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True)
class Span:
    """A distributed trace span."""

    span_id: str
    trace_id: str
    parent_span_id: str | None
    operation_name: str
    start_time_ms: float
    end_time_ms: float | None = None
    status: SpanStatus = SpanStatus.OK
    tags: dict[str, Any] = field(default_factory=dict)
    logs: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def duration_ms(self) -> float:
        if self.end_time_ms is None:
            return time.time() * 1000 - self.start_time_ms
        return self.end_time_ms - self.start_time_ms


@dataclass
class DistributedTracer:
    """Distributed tracing system."""

    def __init__(self, service_name: str) -> None:
        self._service_name = service_name
        self._active_spans: dict[str, Span] = {}
        self._completed_spans: list[Span] = []
        self._trace_counter = 0
        self._trace_spans: dict[str, list[Span]] = {}

    def start_span(
        self,
        operation_name: str,
        parent_span_id: str | None = None,
        tags: dict[str, Any] | None = None,
    ) -> str:
        """Start a new span. Returns span_id."""
        self._trace_counter += 1
        trace_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        start_time = time.time() * 1000

        span = Span(
            span_id=span_id,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            operation_name=operation_name,
            start_time_ms=start_time,
            tags=tags or {},
        )
        self._active_spans[span_id] = span
        if trace_id not in self._trace_spans:
            self._trace_spans[trace_id] = []
        self._trace_spans[trace_id].append(span)
        return span_id

    def end_span(self, span_id: str, status: SpanStatus = SpanStatus.OK) -> None:
        """End a span."""
        if span_id not in self._active_spans:
            return
        span = self._active_spans.pop(span_id)
        end_time = time.time() * 1000
        completed_span = Span(
            span_id=span.span_id,
            trace_id=span.trace_id,
            parent_span_id=span.parent_span_id,
            operation_name=span.operation_name,
            start_time_ms=span.start_time_ms,
            end_time_ms=end_time,
            status=status,
            tags=span.tags,
            logs=span.logs,
        )
        self._completed_spans.append(completed_span)
        self._prune_completed_spans()
        for trace_spans in self._trace_spans.values():
            for i, s in enumerate(trace_spans):
                if s.span_id == span_id:
                    trace_spans[i] = completed_span
                    break
        self._prune_traces()

    def record_exception(self, span_id: str, exception: Exception) -> None:
        """Record an exception in a span."""
        if span_id not in self._active_spans:
            return
        span = self._active_spans[span_id]
        log_entry = {
            "event": "exception",
            "exception.type": type(exception).__name__,
            "exception.message": str(exception),
        }
        updated_logs = (*span.logs, log_entry)
        updated_span = Span(
            span_id=span.span_id,
            trace_id=span.trace_id,
            parent_span_id=span.parent_span_id,
            operation_name=span.operation_name,
            start_time_ms=span.start_time_ms,
            end_time_ms=span.end_time_ms,
            status=SpanStatus.ERROR,
            tags=span.tags,
            logs=updated_logs,
        )
        self._active_spans[span_id] = updated_span
        for trace_spans in self._trace_spans.values():
            for i, s in enumerate(trace_spans):
                if s.span_id == span_id:
                    trace_spans[i] = updated_span
                    break

    def _prune_completed_spans(self) -> None:
        """Remove oldest completed spans when limit exceeded."""
        if len(self._completed_spans) > MAX_COMPLETED_SPANS:
            excess = len(self._completed_spans) - MAX_COMPLETED_SPANS
            self._completed_spans = self._completed_spans[excess:]

    def _prune_traces(self) -> None:
        """Remove oldest traces when limit exceeded."""
        if len(self._trace_spans) > MAX_TRACES:
            excess = len(self._trace_spans) - MAX_TRACES
            oldest_keys = list(self._trace_spans.keys())[:excess]
            for key in oldest_keys:
                del self._trace_spans[key]

    def get_trace(self, trace_id: str) -> list[Span]:
        """Get all spans for a trace."""
        return self._trace_spans.get(trace_id, [])

    def get_traces(self, limit: int = 100) -> list[list[Span]]:
        """Get recent traces."""
        traces = []
        for trace_spans in list(self._trace_spans.values())[-limit:]:
            if trace_spans:
                traces.append(trace_spans)
        return traces

    @property
    def service_name(self) -> str:
        return self._service_name
