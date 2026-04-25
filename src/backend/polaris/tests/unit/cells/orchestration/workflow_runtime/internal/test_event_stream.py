"""Tests for workflow_runtime internal event_stream module."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.orchestration.workflow_runtime.internal.event_stream import (
    EventLevel,
    EventSpan,
    EventStream,
    EventType,
    OrchestrationEvent,
)


class TestOrchestrationEvent:
    def test_to_dict(self) -> None:
        event = OrchestrationEvent(
            level=EventLevel.INFO,
            event_type=EventType.SPAWNED,
            source="pm",
            process_id="p1",
            pid=123,
            payload={"cmd": ["echo"]},
        )
        d = event.to_dict()
        assert d["level"] == "info"
        assert d["event_type"] == "spawned"
        assert d["source"] == "pm"

    def test_to_json(self) -> None:
        event = OrchestrationEvent(source="test")
        json_str = event.to_json()
        assert "test" in json_str

    def test_spawned_factory(self) -> None:
        event = OrchestrationEvent.spawned("pm", "p1", 123, ["echo", "hi"])
        assert event.event_type == EventType.SPAWNED
        assert event.payload["command"] == ["echo", "hi"]

    def test_completed_factory(self) -> None:
        event = OrchestrationEvent.completed("pm", "p1", 123, 0, 1000)
        assert event.event_type == EventType.COMPLETED
        assert event.level == EventLevel.INFO

    def test_failed_factory(self) -> None:
        event = OrchestrationEvent.failed("pm", "p1", "boom")
        assert event.event_type == EventType.FAILED
        assert event.level == EventLevel.ERROR


class TestEventStream:
    def test_subscribe_and_publish(self) -> None:
        stream = EventStream()
        handler = MagicMock()
        stream.subscribe(handler)
        event = OrchestrationEvent(source="test")
        stream.publish(event)
        handler.assert_called_once_with(event)

    def test_unsubscribe(self) -> None:
        stream = EventStream()
        handler = MagicMock()
        stream.subscribe(handler)
        stream.unsubscribe(handler)
        stream.publish(OrchestrationEvent(source="test"))
        handler.assert_not_called()

    def test_get_events_filter_source(self) -> None:
        stream = EventStream()
        stream.publish(OrchestrationEvent(source="a"))
        stream.publish(OrchestrationEvent(source="b"))
        results = stream.get_events(source="a")
        assert len(results) == 1
        assert results[0].source == "a"

    def test_get_events_filter_type(self) -> None:
        stream = EventStream()
        stream.publish(OrchestrationEvent(source="a", event_type=EventType.SPAWNED))
        stream.publish(OrchestrationEvent(source="a", event_type=EventType.COMPLETED))
        results = stream.get_events(event_type=EventType.SPAWNED)
        assert len(results) == 1

    def test_buffer_limit(self) -> None:
        stream = EventStream(max_buffer=2)
        stream.publish(OrchestrationEvent(source="1"))
        stream.publish(OrchestrationEvent(source="2"))
        stream.publish(OrchestrationEvent(source="3"))
        assert len(stream._events) == 2
        assert stream._events[0].source == "2"

    def test_persist_event_no_log_path(self) -> None:
        stream = EventStream()
        event = OrchestrationEvent(source="test")
        stream._persist_event(event)

    def test_create_span(self) -> None:
        stream = EventStream()
        span = stream.create_span()
        assert isinstance(span, EventSpan)
        assert span.trace_id.startswith("trace_")


class TestEventSpan:
    def test_emit(self) -> None:
        stream = EventStream()
        span = stream.create_span()
        event = span.emit(EventType.AUDIT_LOG, "test", level=EventLevel.DEBUG, msg="hello")
        assert event.trace_id == span.trace_id
        assert event.span_id == span.span_id

    def test_context_manager(self) -> None:
        stream = EventStream()
        with stream.create_span() as span:
            assert isinstance(span, EventSpan)
