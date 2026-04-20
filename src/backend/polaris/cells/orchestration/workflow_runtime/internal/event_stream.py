"""Event stream for orchestration.

This module provides EventStream and OrchestrationEvent for
structured event logging in the process orchestration system.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from polaris.kernelone.fs.text_ops import open_text_log_append

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class EventLevel(Enum):
    """Event severity levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventType(Enum):
    """Orchestration event types."""

    # Lifecycle events
    SPAWNED = "spawned"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"

    # Retry events
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_ATTEMPT = "retry_attempt"
    RETRY_EXHAUSTED = "retry_exhausted"

    # Status events
    HEARTBEAT = "heartbeat"
    STATUS_CHANGE = "status_change"
    PROGRESS = "progress"

    # Audit events
    AUDIT_LOG = "audit_log"


@dataclass(frozen=True)
class OrchestrationEvent:
    """Structured orchestration event.

    Attributes:
        timestamp: Event timestamp
        level: Event severity level
        event_type: Event type
        source: Source component (pm, director, backend)
        process_id: Process ID if applicable
        pid: System process ID
        payload: Event-specific data
        trace_id: Distributed trace ID
        span_id: Span ID within trace
    """

    timestamp: datetime = field(default_factory=datetime.now)
    level: EventLevel = EventLevel.INFO
    event_type: EventType = EventType.AUDIT_LOG
    source: str = ""
    process_id: str | None = None
    pid: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    span_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.value,
            "event_type": self.event_type.value,
            "source": self.source,
            "process_id": self.process_id,
            "pid": self.pid,
            "payload": self.payload,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def spawned(
        cls,
        source: str,
        process_id: str,
        pid: int,
        command: list[str],
        **kwargs: Any,
    ) -> OrchestrationEvent:
        """Create a spawned event."""
        return cls(
            level=EventLevel.INFO,
            event_type=EventType.SPAWNED,
            source=source,
            process_id=process_id,
            pid=pid,
            payload={"command": command, **kwargs},
        )

    @classmethod
    def completed(
        cls,
        source: str,
        process_id: str,
        pid: int,
        exit_code: int,
        duration_ms: int,
        **kwargs: Any,
    ) -> OrchestrationEvent:
        """Create a completed event."""
        return cls(
            level=EventLevel.INFO if exit_code == 0 else EventLevel.WARNING,
            event_type=EventType.COMPLETED,
            source=source,
            process_id=process_id,
            pid=pid,
            payload={"exit_code": exit_code, "duration_ms": duration_ms, **kwargs},
        )

    @classmethod
    def failed(
        cls,
        source: str,
        process_id: str | None,
        error: str,
        **kwargs: Any,
    ) -> OrchestrationEvent:
        """Create a failed event."""
        return cls(
            level=EventLevel.ERROR,
            event_type=EventType.FAILED,
            source=source,
            process_id=process_id,
            payload={"error": error, **kwargs},
        )


class EventStream:
    """Event stream for orchestration events.

    This class manages event publishing, subscription, and persistence
    for the orchestration system.

    Attributes:
        _subscribers: List of event subscriber callbacks
        _event_log: Optional file path for event persistence
        _events: In-memory event buffer
    """

    def __init__(self, event_log: Path | None = None, max_buffer: int = 1000) -> None:
        """Initialize event stream.

        Args:
            event_log: Optional path to event log file
            max_buffer: Maximum number of events to keep in memory
        """
        self._subscribers: list[Callable[[OrchestrationEvent], None]] = []
        self._event_log = event_log
        self._max_buffer = max_buffer
        self._events: list[OrchestrationEvent] = []

    def subscribe(self, callback: Callable[[OrchestrationEvent], None]) -> None:
        """Subscribe to events.

        Args:
            callback: Function to call when event is published
        """
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[OrchestrationEvent], None]) -> None:
        """Unsubscribe from events.

        Args:
            callback: Previously subscribed callback
        """
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def publish(self, event: OrchestrationEvent) -> None:
        """Publish an event.

        Args:
            event: Event to publish
        """
        # Add to buffer
        self._events.append(event)
        if len(self._events) > self._max_buffer:
            self._events.pop(0)

        # Persist to log
        if self._event_log:
            self._persist_event(event)

        # Notify subscribers
        for callback in self._subscribers:
            try:
                callback(event)
            except (RuntimeError, ValueError) as exc:
                # Don't let subscriber errors break the chain
                _logger.debug("subscriber callback raised: %s", exc)

    def get_events(
        self,
        source: str | None = None,
        event_type: EventType | None = None,
        limit: int = 100,
    ) -> list[OrchestrationEvent]:
        """Get filtered events from buffer.

        Args:
            source: Filter by source
            event_type: Filter by event type
            limit: Maximum number of events

        Returns:
            List of matching events
        """
        events = self._events

        if source:
            events = [e for e in events if e.source == source]

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        return events[-limit:]

    def _persist_event(self, event: OrchestrationEvent) -> None:
        """Persist event to log file.

        Args:
            event: Event to persist
        """
        try:
            handle = open_text_log_append(str(self._event_log))
            try:
                handle.write(event.to_json() + "\n")
            finally:
                handle.close()
        except (RuntimeError, ValueError) as exc:
            # Don't let persistence errors break the chain
            _logger.debug("event persistence failed (non-critical): %s", exc)

    def create_span(self, trace_id: str | None = None) -> EventSpan:
        """Create a new event span.

        Args:
            trace_id: Optional parent trace ID

        Returns:
            New EventSpan
        """
        return EventSpan(self, trace_id)


class EventSpan:
    """Event span for grouping related events.

    This class provides a context manager for publishing events
    within a distributed trace span.
    """

    def __init__(self, stream: EventStream, trace_id: str | None = None) -> None:
        """Initialize event span.

        Args:
            stream: Parent event stream
            trace_id: Optional parent trace ID
        """
        self._stream = stream
        self.trace_id = trace_id or self._generate_trace_id()
        self.span_id = self._generate_span_id()

    def _generate_trace_id(self) -> str:
        """Generate a trace ID."""
        return f"trace_{int(time.time() * 1000)}_{id(self)}"

    def _generate_span_id(self) -> str:
        """Generate a span ID."""
        return f"span_{id(self)}"

    def emit(
        self,
        event_type: EventType,
        source: str,
        level: EventLevel = EventLevel.INFO,
        **payload: Any,
    ) -> OrchestrationEvent:
        """Emit an event within this span.

        Args:
            event_type: Event type
            source: Source component
            level: Event level
            **payload: Event payload

        Returns:
            Created event
        """
        event = OrchestrationEvent(
            event_type=event_type,
            source=source,
            level=level,
            payload=payload,
            trace_id=self.trace_id,
            span_id=self.span_id,
        )
        self._stream.publish(event)
        return event

    def __enter__(self) -> EventSpan:
        """Enter span context."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Exit span context."""
        pass
