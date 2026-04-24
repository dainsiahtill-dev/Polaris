"""Tests for polaris.cells.orchestration.workflow_runtime.internal.event_stream module.

This module tests the event stream for orchestration.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from polaris.cells.orchestration.workflow_runtime.internal.event_stream import (
    EventLevel,
    EventSpan,
    EventStream,
    EventType,
    OrchestrationEvent,
)


class TestEventLevel:
    """Tests for EventLevel enum."""

    def test_all_levels_exist(self) -> None:
        """All expected event levels exist."""
        assert EventLevel.DEBUG.value == "debug"
        assert EventLevel.INFO.value == "info"
        assert EventLevel.WARNING.value == "warning"
        assert EventLevel.ERROR.value == "error"
        assert EventLevel.CRITICAL.value == "critical"


class TestEventType:
    """Tests for EventType enum."""

    def test_lifecycle_events_exist(self) -> None:
        """Lifecycle event types exist."""
        assert EventType.SPAWNED.value == "spawned"
        assert EventType.STARTED.value == "started"
        assert EventType.COMPLETED.value == "completed"
        assert EventType.FAILED.value == "failed"
        assert EventType.TERMINATED.value == "terminated"

    def test_retry_events_exist(self) -> None:
        """Retry event types exist."""
        assert EventType.RETRY_SCHEDULED.value == "retry_scheduled"
        assert EventType.RETRY_ATTEMPT.value == "retry_attempt"
        assert EventType.RETRY_EXHAUSTED.value == "retry_exhausted"

    def test_status_events_exist(self) -> None:
        """Status event types exist."""
        assert EventType.HEARTBEAT.value == "heartbeat"
        assert EventType.STATUS_CHANGE.value == "status_change"
        assert EventType.PROGRESS.value == "progress"

    def test_audit_event_exists(self) -> None:
        """Audit event type exists."""
        assert EventType.AUDIT_LOG.value == "audit_log"


class TestOrchestrationEvent:
    """Tests for OrchestrationEvent dataclass."""

    def test_construction_with_defaults(self) -> None:
        """OrchestrationEvent can be constructed with defaults."""
        event = OrchestrationEvent()
        assert event.timestamp is not None
        assert event.level == EventLevel.INFO
        assert event.event_type == EventType.AUDIT_LOG
        assert event.source == ""

    def test_construction_with_values(self) -> None:
        """OrchestrationEvent can be constructed with values."""
        timestamp = datetime.now(timezone.utc)
        event = OrchestrationEvent(
            timestamp=timestamp,
            level=EventLevel.ERROR,
            event_type=EventType.FAILED,
            source="pm",
            process_id="proc-1",
            pid=12345,
            payload={"error": "test error"},
            trace_id="trace-1",
            span_id="span-1",
        )
        assert event.level == EventLevel.ERROR
        assert event.event_type == EventType.FAILED
        assert event.source == "pm"
        assert event.process_id == "proc-1"
        assert event.pid == 12345
        assert event.payload == {"error": "test error"}

    def test_to_dict(self) -> None:
        """OrchestrationEvent.to_dict returns correct structure."""
        event = OrchestrationEvent(
            level=EventLevel.INFO,
            event_type=EventType.STARTED,
            source="test",
        )
        result = event.to_dict()
        assert isinstance(result, dict)
        assert result["level"] == "info"
        assert result["event_type"] == "started"
        assert result["source"] == "test"
        assert "timestamp" in result

    def test_to_json(self) -> None:
        """OrchestrationEvent.to_json returns JSON string."""
        event = OrchestrationEvent(
            level=EventLevel.WARNING,
            event_type=EventType.STATUS_CHANGE,
            source="test",
        )
        result = event.to_json()
        parsed = json.loads(result)
        assert parsed["level"] == "warning"
        assert parsed["event_type"] == "status_change"

    def test_spawned_factory(self) -> None:
        """OrchestrationEvent.spawned creates correct event."""
        event = OrchestrationEvent.spawned(
            source="pm",
            process_id="proc-1",
            pid=1234,
            command=["python", "test.py"],
            extra_data="test",
        )
        assert event.level == EventLevel.INFO
        assert event.event_type == EventType.SPAWNED
        assert event.source == "pm"
        assert event.process_id == "proc-1"
        assert event.pid == 1234
        assert event.payload["command"] == ["python", "test.py"]
        assert event.payload["extra_data"] == "test"

    def test_completed_factory_success(self) -> None:
        """OrchestrationEvent.completed creates INFO event for exit_code 0."""
        event = OrchestrationEvent.completed(
            source="pm",
            process_id="proc-1",
            pid=1234,
            exit_code=0,
            duration_ms=1000,
        )
        assert event.level == EventLevel.INFO
        assert event.event_type == EventType.COMPLETED
        assert event.payload["exit_code"] == 0
        assert event.payload["duration_ms"] == 1000

    def test_completed_factory_failure(self) -> None:
        """OrchestrationEvent.completed creates WARNING for non-zero exit_code."""
        event = OrchestrationEvent.completed(
            source="pm",
            process_id="proc-1",
            pid=1234,
            exit_code=1,
            duration_ms=500,
        )
        assert event.level == EventLevel.WARNING

    def test_failed_factory(self) -> None:
        """OrchestrationEvent.failed creates ERROR event."""
        event = OrchestrationEvent.failed(
            source="pm",
            process_id="proc-1",
            error="Something went wrong",
            extra="data",
        )
        assert event.level == EventLevel.ERROR
        assert event.event_type == EventType.FAILED
        assert event.payload["error"] == "Something went wrong"
        assert event.payload["extra"] == "data"

    def test_failed_factory_without_process_id(self) -> None:
        """OrchestrationEvent.failed works without process_id."""
        event = OrchestrationEvent.failed(
            source="pm",
            process_id=None,
            error="Error",
        )
        assert event.process_id is None


class TestEventStream:
    """Tests for EventStream class."""

    def test_construction(self) -> None:
        """EventStream can be constructed."""
        stream = EventStream()
        assert stream._subscribers == []
        assert stream._events == []
        assert stream._max_buffer == 1000

    def test_construction_with_options(self) -> None:
        """EventStream accepts optional parameters."""
        log_path = Path("/tmp/events.log")
        stream = EventStream(event_log=log_path, max_buffer=100)
        assert stream._event_log == log_path
        assert stream._max_buffer == 100

    def test_subscribe(self) -> None:
        """EventStream.subscribe adds callback."""
        stream = EventStream()
        callback = MagicMock()
        stream.subscribe(callback)
        assert callback in stream._subscribers

    def test_unsubscribe(self) -> None:
        """EventStream.unsubscribe removes callback."""
        stream = EventStream()
        callback = MagicMock()
        stream.subscribe(callback)
        stream.unsubscribe(callback)
        assert callback not in stream._subscribers

    def test_unsubscribe_not_subscribed(self) -> None:
        """EventStream.unsubscribe handles non-subscribed callback gracefully."""
        stream = EventStream()
        callback = MagicMock()
        stream.unsubscribe(callback)  # Should not raise

    def test_publish_adds_to_buffer(self) -> None:
        """EventStream.publish adds event to buffer."""
        stream = EventStream()
        event = OrchestrationEvent(source="test")
        stream.publish(event)
        assert len(stream._events) == 1
        assert stream._events[0] == event

    def test_publish_buffer_overflow(self) -> None:
        """EventStream.publish removes old events when buffer overflows."""
        stream = EventStream(max_buffer=3)
        for i in range(5):
            stream.publish(OrchestrationEvent(source=f"test-{i}"))
        assert len(stream._events) == 3
        # Last 3 events should be test-2, test-3, test-4
        assert stream._events[0].source == "test-2"
        assert stream._events[2].source == "test-4"

    def test_publish_notifies_subscribers(self) -> None:
        """EventStream.publish notifies all subscribers."""
        stream = EventStream()
        callback1 = MagicMock()
        callback2 = MagicMock()
        stream.subscribe(callback1)
        stream.subscribe(callback2)

        event = OrchestrationEvent(source="test")
        stream.publish(event)

        callback1.assert_called_once_with(event)
        callback2.assert_called_once_with(event)

    def test_publish_handles_subscriber_exception(self) -> None:
        """EventStream.publish continues if subscriber raises."""
        stream = EventStream()
        bad_callback = MagicMock(side_effect=RuntimeError("Subscriber error"))
        good_callback = MagicMock()
        stream.subscribe(bad_callback)
        stream.subscribe(good_callback)

        event = OrchestrationEvent(source="test")
        stream.publish(event)

        bad_callback.assert_called_once()
        good_callback.assert_called_once()

    def test_get_events_no_filter(self) -> None:
        """EventStream.get_events returns all events when no filter."""
        stream = EventStream()
        for i in range(5):
            stream.publish(OrchestrationEvent(source=f"test-{i}"))
        events = stream.get_events()
        assert len(events) == 5

    def test_get_events_filter_by_source(self) -> None:
        """EventStream.get_events filters by source."""
        stream = EventStream()
        stream.publish(OrchestrationEvent(source="pm"))
        stream.publish(OrchestrationEvent(source="director"))
        stream.publish(OrchestrationEvent(source="pm"))

        pm_events = stream.get_events(source="pm")
        assert len(pm_events) == 2

    def test_get_events_filter_by_event_type(self) -> None:
        """EventStream.get_events filters by event_type."""
        stream = EventStream()
        stream.publish(OrchestrationEvent.spawned("pm", "p1", 1, []))
        stream.publish(OrchestrationEvent.completed("pm", "p1", 1, 0, 100))
        stream.publish(OrchestrationEvent.spawned("pm", "p2", 2, []))

        spawned = stream.get_events(event_type=EventType.SPAWNED)
        assert len(spawned) == 2

    def test_get_events_with_limit(self) -> None:
        """EventStream.get_events respects limit."""
        stream = EventStream()
        for i in range(10):
            stream.publish(OrchestrationEvent(source=f"test-{i}"))
        events = stream.get_events(limit=3)
        assert len(events) == 3

    def test_get_events_combined_filters(self) -> None:
        """EventStream.get_events applies multiple filters."""
        stream = EventStream()
        stream.publish(OrchestrationEvent.spawned("pm", "p1", 1, []))
        stream.publish(OrchestrationEvent.completed("pm", "p1", 1, 0, 100))
        stream.publish(OrchestrationEvent.spawned("director", "d1", 1, []))

        events = stream.get_events(source="pm", event_type=EventType.SPAWNED)
        assert len(events) == 1

    def test_create_span(self) -> None:
        """EventStream.create_span returns EventSpan."""
        stream = EventStream()
        span = stream.create_span()
        assert isinstance(span, EventSpan)
        assert span._stream is stream


class TestEventSpan:
    """Tests for EventSpan class."""

    def test_construction(self) -> None:
        """EventSpan can be constructed."""
        stream = EventStream()
        span = EventSpan(stream)
        assert span._stream is stream
        assert span.trace_id is not None
        assert span.span_id is not None

    def test_construction_with_trace_id(self) -> None:
        """EventSpan uses provided trace_id."""
        stream = EventStream()
        span = EventSpan(stream, trace_id="my-trace-123")
        assert span.trace_id == "my-trace-123"

    def test_emit_creates_event(self) -> None:
        """EventSpan.emit creates and publishes event."""
        stream = EventStream()
        span = EventSpan(stream)

        event = span.emit(EventType.HEARTBEAT, "test", EventLevel.INFO, key="value")

        assert event.event_type == EventType.HEARTBEAT
        assert event.source == "test"
        assert event.level == EventLevel.INFO
        assert event.payload["key"] == "value"
        assert event.trace_id == span.trace_id
        assert event.span_id == span.span_id
        assert len(stream._events) == 1

    def test_emit_default_level(self) -> None:
        """EventSpan.emit uses INFO as default level."""
        stream = EventStream()
        span = EventSpan(stream)
        event = span.emit(EventType.HEARTBEAT, "test")
        assert event.level == EventLevel.INFO

    def test_context_manager(self) -> None:
        """EventSpan works as context manager."""
        stream = EventStream()
        with EventSpan(stream) as span:
            assert isinstance(span, EventSpan)
            span.emit(EventType.STATUS_CHANGE, "test")

        # Span exits without error

    def test_context_manager_with_return(self) -> None:
        """EventSpan context manager returns self."""
        stream = EventStream()
        with EventSpan(stream) as span:
            assert span is not None

    def test_span_ids_are_unique(self) -> None:
        """EventSpan generates unique span IDs."""
        stream = EventStream()
        span1 = EventSpan(stream)
        span2 = EventSpan(stream)
        assert span1.span_id != span2.span_id

    def test_trace_ids_can_be_shared(self) -> None:
        """EventSpan can share trace_id with parent."""
        stream = EventStream()
        parent = EventSpan(stream, trace_id="shared-trace")
        child = EventSpan(stream, trace_id="shared-trace")
        assert parent.trace_id == child.trace_id
        assert parent.span_id != child.span_id
