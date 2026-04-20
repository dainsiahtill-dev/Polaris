"""Trace context propagation for KernelOne telemetry/ subsystem.

Provides TraceCarrier for carrying distributed trace context across
KernelOne subsystems, and trace_context for thread-local/async-local
context management.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text operations use encoding="utf-8"
"""

from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from polaris.kernelone.contracts.technical import TraceContext as MasterTraceContext
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

# Async-local storage for trace context
_trace_var: contextvars.ContextVar[TraceCarrier | None] = contextvars.ContextVar("_trace_var", default=None)


@dataclass(frozen=True)
class TraceCarrier:
    """Immutable carrier for distributed trace context.

    Carries trace_id, span_id, baggage, and sampling flag through
    KernelOne subsystems. Analogous to W3C TraceContext headers.

    Usage::

        # Create a new root trace
        carrier = TraceCarrier.new()

        # Create a child span
        child = carrier.child(span_name="fs.read")

        # Propagate via async context
        token = trace_context.set(child)
        try:
            await do_work()
        finally:
            trace_context.reset(token)
    """

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    parent_span_id: str = ""
    baggage: dict[str, str] = field(default_factory=dict)
    sampled: bool = True
    span_name: str = ""
    started_at: datetime = field(default_factory=_utc_now)

    @classmethod
    def new(cls, span_name: str = "", **kwargs: Any) -> TraceCarrier:
        """Create a new root trace carrier."""
        return cls(span_name=span_name, **kwargs)

    def child(self, span_name: str = "") -> TraceCarrier:
        """Create a child span carrier."""
        return TraceCarrier(
            trace_id=self.trace_id,
            parent_span_id=self.span_id,
            span_id=uuid.uuid4().hex[:8],
            baggage=dict(self.baggage),
            sampled=self.sampled,
            span_name=span_name,
        )

    def with_baggage(self, key: str, value: str) -> TraceCarrier:
        """Return a new carrier with an additional baggage item."""
        new_baggage = dict(self.baggage)
        new_baggage[key] = value
        return TraceCarrier(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            baggage=new_baggage,
            sampled=self.sampled,
            span_name=self.span_name,
            started_at=self.started_at,
        )

    def to_w3c_headers(self) -> dict[str, str]:
        """Encode as W3C TraceContext headers."""
        return {
            "traceparent": f"00-{self.trace_id}-{self.span_id}" + ("-01" if not self.sampled else "-00"),
            "tracestate": ",".join(f"{k}={v}" for k, v in self.baggage.items()),
        }

    @classmethod
    def from_w3c_headers(cls, headers: dict[str, str]) -> TraceCarrier:
        """Decode from W3C TraceContext headers."""
        traceparent = headers.get("traceparent", "")
        if traceparent:
            parts = traceparent.split("-")
            if len(parts) >= 3:
                trace_id = parts[1]
                span_id = parts[2]
                sampled = len(parts) > 3 and parts[3] == "00"
            else:
                trace_id = uuid.uuid4().hex[:16]
                span_id = uuid.uuid4().hex[:8]
                sampled = True
        else:
            trace_id = uuid.uuid4().hex[:16]
            span_id = uuid.uuid4().hex[:8]
            sampled = True
        tracestate = headers.get("tracestate", "")
        baggage: dict[str, str] = {}
        if tracestate:
            for item in tracestate.split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    baggage[k.strip()] = v.strip()
        return cls(trace_id=trace_id, span_id=span_id, baggage=baggage, sampled=sampled)

    def to_master(self) -> MasterTraceContext:
        """Convert to master_types.TraceContext."""
        return MasterTraceContext(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            baggage=dict(self.baggage),
            sampled=self.sampled,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "baggage": dict(self.baggage),
            "sampled": self.sampled,
            "span_name": self.span_name,
            "started_at": self.started_at.isoformat(),
        }


class _TraceContextManager:
    """Async-local trace context manager.

    Thread-safe and asyncio-safe via contextvars.
    """

    def get(self) -> TraceCarrier | None:
        """Get the current trace carrier, or None."""
        return _trace_var.get()

    def set(self, carrier: TraceCarrier) -> contextvars.Token:
        """Set the current trace carrier. Returns a token for reset()."""
        return _trace_var.set(carrier)

    def reset(self, token: contextvars.Token) -> None:
        """Reset to the previous carrier using a token from set()."""
        _trace_var.reset(token)

    def clear(self) -> None:
        """Clear the current carrier (set to None)."""
        _trace_var.set(None)

    def new_span(self, span_name: str = "") -> TraceCarrier:
        """Get a new child span from the current carrier, or a new root if none."""
        current = self.get()
        if current is None:
            return TraceCarrier.new(span_name=span_name)
        return current.child(span_name=span_name)


# Module-level singleton
trace_context = _TraceContextManager()


# -----------------------------------------------------------------------------
# Simplified Trace ID API (for business metrics integration)
# -----------------------------------------------------------------------------

TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """Get the current trace ID from context."""
    return TRACE_ID.get()


def set_trace_id(trace_id: str) -> None:
    """Set the current trace ID in context."""
    TRACE_ID.set(trace_id)


def new_trace_id() -> str:
    """Generate a new trace ID (UUID string)."""
    return str(uuid.uuid4())


class TraceContext:
    """Trace context manager for scoped trace ID propagation.

    Usage::

        with TraceContext("build_context") as ctx:
            # trace_id is set automatically
            result = await do_work()
            # trace_id is cleared on exit
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.trace_id: str = ""
        self._token: contextvars.Token | None = None

    def __enter__(self) -> TraceContext:
        self.trace_id = new_trace_id()
        self._token = TRACE_ID.set(self.trace_id)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._token is not None:
            TRACE_ID.reset(self._token)
        set_trace_id("")
