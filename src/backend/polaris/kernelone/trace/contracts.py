"""Trace / observability subsystem contracts for KernelOne.

This module defines the stable port surface for distributed tracing,
structured logging, and context propagation.

Architecture:
    - TracePort: async span lifecycle management
    - LogPort: structured logging with trace context injection
    - ContextPort: asyncio-safe trace context management
    - UnifiedTracer/UnifiedLogger are the default in-process adapters

Design constraints:
    - KernelOne-only: no Polaris business semantics
    - All trace IDs must be propagated across async boundaries
    - Sensitive data must be redacted in logs
    - Explicit UTF-8: all file I/O uses encoding="utf-8"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Generator

# -----------------------------------------------------------------------------


class SpanStatus(str, Enum):
    """Lifecycle status of a trace span."""

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SpanSnapshot:
    """Immutable snapshot of a trace span at a point in time."""

    span_id: str
    name: str
    trace_id: str
    parent_span_id: str | None = None
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    duration_ms: float | None = None
    status: SpanStatus = SpanStatus.UNKNOWN
    status_message: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TraceContextSnapshot:
    """Immutable snapshot of the current trace context."""

    trace_id: str
    run_id: str | None = None
    request_id: str | None = None
    workflow_id: str | None = None
    task_id: str | None = None
    workspace: str | None = None
    span_depth: int = 0


# -----------------------------------------------------------------------------


class TracePort(Protocol):
    """Abstract interface for distributed tracing.

    Implementations: UnifiedTracer (in-process).
    """

    def start_span(
        self,
        name: str,
        *,
        tags: dict[str, Any] | None = None,
        parent_span_id: str | None = None,
        trace_id: str | None = None,
    ) -> SpanSnapshot:
        """Start a new span. Returns an immutable snapshot."""
        ...

    def end_span(
        self,
        span: SpanSnapshot,
        *,
        status: SpanStatus | None = None,
        status_message: str | None = None,
    ) -> None:
        """End a span and record its final state."""
        ...

    def record_event(
        self,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Record a timestamped event on the current span."""
        ...

    def record_error(
        self,
        error: BaseException,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record an exception on the current span."""
        ...

    def inject_context_into_headers(self) -> dict[str, str]:
        """Serialize trace context into HTTP headers for propagation."""
        ...

    def extract_context_from_headers(
        self,
        headers: dict[str, str],
    ) -> TraceContextSnapshot:
        """Reconstruct trace context from HTTP headers."""
        ...


# -----------------------------------------------------------------------------


class LogPort(Protocol):
    """Abstract interface for structured logging with trace context.

    Implementations: UnifiedLogger (in-process).
    """

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def configure(
        self,
        level: str,
        *,
        json_output: bool = True,
        log_file: str | None = None,
    ) -> None:
        """Configure logging. Idempotent."""
        ...


# -----------------------------------------------------------------------------


class ContextPort(Protocol):
    """Abstract interface for asyncio-safe trace context management.

    Implementations: ContextManager (in-process via contextvars).
    """

    def get_current(self) -> TraceContextSnapshot:
        """Get an immutable snapshot of the current trace context."""
        ...

    def set_context(self, ctx: TraceContextSnapshot) -> None:
        """Install a trace context as current."""
        ...

    def clear(self) -> None:
        """Clear the current trace context."""
        ...

    def bind_context(
        self,
        ctx: TraceContextSnapshot,
    ) -> Generator[TraceContextSnapshot, None, None]:
        """Context-manager: install a context, restore on exit."""
        ...


# -----------------------------------------------------------------------------


class TraceRecorderPort(Protocol):
    """Abstract interface for persisting trace spans.

    Implementations: InMemoryTraceRecorder, FileTraceRecorder.
    """

    def record_span(self, span: SpanSnapshot) -> None:
        """Persist a span snapshot."""
        ...

    def get_trace(self, trace_id: str) -> list[SpanSnapshot]:
        """Retrieve all spans for a given trace ID."""
        ...

    def flush(self) -> None:
        """Synchronously flush buffered spans to storage."""
        ...


__all__ = [
    "ContextPort",
    "LogPort",
    "SpanSnapshot",
    # Types
    "SpanStatus",
    "TraceContextSnapshot",
    # Ports
    "TracePort",
    "TraceRecorderPort",
]
