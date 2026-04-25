"""Tests for polaris.kernelone.llm.engine.stream.result_tracker."""

from __future__ import annotations

from unittest.mock import patch

from polaris.kernelone.llm.engine.contracts import AIStreamEvent
from polaris.kernelone.llm.engine.stream.config import LLMStreamResult, StreamState
from polaris.kernelone.llm.engine.stream.result_tracker import _StreamResultTracker
from polaris.kernelone.llm.shared_contracts import StreamEventType


class TestStreamResultTrackerInit:
    def test_default_config(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        assert tracker.trace_id == "trace-1"
        assert tracker.token_timeout > 0
        assert tracker.stream_timeout > 0
        assert tracker.state == StreamState.IDLE
        assert tracker.last_token_time is None
        assert tracker.stream_start_time is None

    def test_custom_timeouts(self) -> None:
        tracker = _StreamResultTracker("trace-2", token_timeout=30.0, stream_timeout=120.0)
        assert tracker.token_timeout == 30.0
        assert tracker.stream_timeout == 120.0

    def test_custom_config(self) -> None:
        from polaris.kernelone.llm.engine.stream.config import StreamConfig

        cfg = StreamConfig(token_timeout_sec=45.0, timeout_sec=90.0)
        tracker = _StreamResultTracker("trace-3", config=cfg)
        assert tracker.token_timeout == 45.0
        assert tracker.stream_timeout == 90.0


class TestStreamResultTrackerStart:
    def test_sets_start_time(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        assert tracker.stream_start_time is not None
        assert tracker.last_token_time == tracker.stream_start_time


class TestStreamResultTrackerRecordEvent:
    def test_chunk_event(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")
        tracker.record_event(event)
        assert tracker.chunk_count == 1
        assert tracker._has_received_content is True
        assert tracker.state == StreamState.IN_CONTENT
        assert tracker.last_token_time is not None

    def test_reasoning_event(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        event = AIStreamEvent(type=StreamEventType.REASONING_CHUNK, reasoning="thinking...")
        tracker.record_event(event)
        assert tracker.state == StreamState.IN_THINKING

    def test_tool_call_event(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        event = AIStreamEvent(type=StreamEventType.TOOL_CALL, tool_call={"tool": "test"})
        tracker.record_event(event)
        assert tracker.tool_calls_count == 1
        assert tracker.state == StreamState.IN_TOOL_CALL

    def test_error_event(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        event = AIStreamEvent(type=StreamEventType.ERROR, error="boom")
        tracker.record_event(event)
        assert tracker._has_error is True
        assert tracker.state == StreamState.ERROR
        assert len(tracker._validation_errors) == 1

    def test_complete_event(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        event = AIStreamEvent(type=StreamEventType.COMPLETE)
        tracker.record_event(event)
        assert tracker._has_completed_cleanly is True
        assert tracker.state == StreamState.COMPLETE

    def test_invalid_state_transition_logged(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        tracker.state = StreamState.COMPLETE
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")
        with patch("polaris.kernelone.llm.engine.stream.result_tracker.logger"):
            tracker.record_event(event)
        # State should remain COMPLETE since transition is invalid
        assert tracker.state == StreamState.COMPLETE

    def test_events_list_populated(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        event1 = AIStreamEvent(type=StreamEventType.CHUNK, chunk="a")
        event2 = AIStreamEvent(type=StreamEventType.CHUNK, chunk="b")
        tracker.record_event(event1)
        tracker.record_event(event2)
        assert len(tracker.events) == 2


class TestStreamResultTrackerCheckTimeouts:
    def test_no_timeout_when_not_started(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        is_timeout, msg = tracker.check_timeouts()
        assert is_timeout is False
        assert msg is None

    def test_stream_timeout(self) -> None:
        tracker = _StreamResultTracker("trace-1", stream_timeout=0.001)
        tracker.start()
        import time

        time.sleep(0.01)
        is_timeout, msg = tracker.check_timeouts()
        assert is_timeout is True
        assert msg is not None
        assert "Stream timeout" in msg

    def test_token_timeout(self) -> None:
        tracker = _StreamResultTracker("trace-1", token_timeout=0.001)
        tracker.start()
        import time

        time.sleep(0.01)
        is_timeout, msg = tracker.check_timeouts()
        assert is_timeout is True
        assert msg is not None
        assert "Token timeout" in msg

    def test_no_token_timeout_when_completed(self) -> None:
        tracker = _StreamResultTracker("trace-1", token_timeout=0.001)
        tracker.start()
        tracker._has_completed_cleanly = True
        import time

        time.sleep(0.01)
        is_timeout, msg = tracker.check_timeouts()
        assert is_timeout is False
        assert msg is None

    def test_no_timeout_within_limits(self) -> None:
        tracker = _StreamResultTracker("trace-1", stream_timeout=300.0, token_timeout=60.0)
        tracker.start()
        is_timeout, msg = tracker.check_timeouts()
        assert is_timeout is False
        assert msg is None


class TestStreamResultTrackerBuildResult:
    def test_build_result_success(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        event = AIStreamEvent(type=StreamEventType.COMPLETE)
        tracker.record_event(event)
        result = tracker.build_result(latency_ms=100)
        assert isinstance(result, LLMStreamResult)
        assert result.is_complete is True
        assert result.trace_id == "trace-1"
        assert result.latency_ms == 100
        assert result.chunk_count == 0
        assert result.tool_calls_count == 0

    def test_build_result_with_error(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        tracker.record_event(AIStreamEvent(type=StreamEventType.ERROR, error="boom"))
        result = tracker.build_result(latency_ms=50)
        assert result.is_complete is False
        assert len(result.validation_errors) == 1

    def test_build_result_with_content(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        tracker.record_event(AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello"))
        tracker.record_event(AIStreamEvent(type=StreamEventType.TOOL_CALL, tool_call={"tool": "t"}))
        tracker.record_event(AIStreamEvent(type=StreamEventType.COMPLETE))
        result = tracker.build_result(latency_ms=200)
        assert result.chunk_count == 1
        assert result.tool_calls_count == 1
        assert result.is_complete is True


class TestStreamResultTrackerGetStats:
    def test_stats_before_start(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        stats = tracker.get_stats()
        assert stats["trace_id"] == "trace-1"
        assert stats["state"] == "idle"
        assert stats["elapsed_seconds"] == 0
        assert stats["seconds_since_last_token"] == 0

    def test_stats_after_events(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        tracker.record_event(AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello"))
        stats = tracker.get_stats()
        assert stats["chunk_count"] == 1
        assert stats["has_received_content"] is True
        assert stats["state"] == "in_content"
        assert stats["elapsed_seconds"] >= 0
        assert stats["seconds_since_last_token"] >= 0

    def test_stats_with_error(self) -> None:
        tracker = _StreamResultTracker("trace-1")
        tracker.start()
        tracker.record_event(AIStreamEvent(type=StreamEventType.ERROR, error="boom"))
        stats = tracker.get_stats()
        assert stats["has_error"] is True
        assert stats["validation_errors_count"] == 1
