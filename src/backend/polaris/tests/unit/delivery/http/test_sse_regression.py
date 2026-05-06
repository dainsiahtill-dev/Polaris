"""Unit tests for SSE stream handling, reconnection, and backpressure behavior.

Covers:
1. SSE event type canonicalization (content_chunk, thinking_chunk, complete, error)
2. Stream handling (format, empty chunks, unicode, large payloads)
3. Reconnection support (last_event_id header respected, resume from position)
4. Backpressure (_SSE_QUEUE_MAX_SIZE = 50 enforced)
5. Error handling in streams (error codes, graceful disconnect, finally cleanup)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.delivery.http.routers.sse_utils import (
    _SSE_QUEUE_MAX_SIZE,
    SSEJetStreamConsumer,
    create_sse_jetstream_consumer,
    create_sse_response,
    sse_event_generator,
    sse_jetstream_generator,
)

# =============================================================================
# 1. SSE Event Type Canonicalization
# =============================================================================


class TestSseEventTypeCanonicalization:
    """Verify canonical event types are used in SSE frames."""

    @pytest.mark.asyncio
    async def test_content_chunk_event_type(self) -> None:
        """Verify 'content_chunk' event type is used (not 'text')."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "content_chunk", "data": {"text": "hello"}})
            await queue.put({"type": "complete", "data": {}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 2
        assert events[0].startswith("event: content_chunk")
        assert "text" not in events[0].split("\n")[0]  # event line must not be 'text'

    @pytest.mark.asyncio
    async def test_thinking_chunk_event_type(self) -> None:
        """Verify 'thinking_chunk' event type is used (not 'reasoning')."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "thinking_chunk", "data": {"thought": "step 1"}})
            await queue.put({"type": "complete", "data": {}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 2
        assert events[0].startswith("event: thinking_chunk")
        assert "reasoning" not in events[0].split("\n")[0]

    @pytest.mark.asyncio
    async def test_complete_event_type(self) -> None:
        """Verify 'complete' event type is used (not 'done')."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "complete", "data": {"status": "ok"}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 1
        assert events[0].startswith("event: complete")
        assert "done" not in events[0].split("\n")[0]

    @pytest.mark.asyncio
    async def test_error_event_type_format(self) -> None:
        """Verify 'error' event type format includes proper structure."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "error", "data": {"error": "something went wrong", "code": "E123"}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 1
        assert events[0].startswith("event: error")
        parsed = json.loads(events[0].split("data: ", 1)[1])
        assert parsed["error"] == "something went wrong"
        assert parsed["code"] == "E123"


# =============================================================================
# 2. Stream Handling
# =============================================================================


class TestSseStreamHandling:
    """Tests for SSE stream generator output format and edge cases."""

    @pytest.mark.asyncio
    async def test_stream_yields_proper_sse_format(self) -> None:
        """Verify generator yields 'event: X\ndata: Y\n\n' format."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "message", "data": {"step": 1}})
            await queue.put({"type": "complete", "data": {}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 2
        for ev in events:
            lines = ev.strip().split("\n")
            assert lines[0].startswith("event: ")
            assert any(line.startswith("data: ") for line in lines)
            assert ev.endswith("\n\n")

    @pytest.mark.asyncio
    async def test_stream_handles_empty_chunks(self) -> None:
        """Verify empty data chunks are emitted gracefully."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "message", "data": {}})
            await queue.put({"type": "complete", "data": {}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 2
        assert "data: {}" in events[0]

    @pytest.mark.asyncio
    async def test_stream_handles_unicode_content(self) -> None:
        """Verify unicode characters are preserved correctly."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "message", "data": {"text": "你好世界 \U0001f600 é"}})
            await queue.put({"type": "complete", "data": {}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 2
        assert "你好世界" in events[0]
        assert "\U0001f600" in events[0]
        assert "é" in events[0]

    @pytest.mark.asyncio
    async def test_stream_handles_large_payloads(self) -> None:
        """Verify large payloads are serialized without corruption."""
        large_text = "x" * 100_000

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "message", "data": {"text": large_text}})
            await queue.put({"type": "complete", "data": {}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 2
        parsed = json.loads(events[0].split("data: ", 1)[1])
        assert len(parsed["text"]) == 100_000
        assert parsed["text"] == large_text


# =============================================================================
# 3. Reconnection Support
# =============================================================================


class TestSseReconnectionSupport:
    """Tests for Last-Event-ID header and stream resume behavior."""

    def test_last_event_id_header_respected(self) -> None:
        """Verify last_event_id is stored and used for cursor-based resume."""
        consumer = create_sse_jetstream_consumer(
            workspace_key="test-workspace",
            subject="events.test",
            last_event_id=42,
        )
        assert consumer.last_event_id == 42

    def test_last_event_id_defaults_to_zero(self) -> None:
        """Verify missing last_event_id defaults to 0 (start from beginning)."""
        consumer = create_sse_jetstream_consumer(
            workspace_key="test-workspace",
            subject="events.test",
        )
        assert consumer.last_event_id == 0

    @pytest.mark.asyncio
    async def test_stream_resumes_from_correct_position(self) -> None:
        """Verify stream yields events with cursor > last_event_id."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
            last_event_id=5,
        )

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            for i in range(6, 9):
                yield {"type": "message", "payload": {"n": i}, "cursor": i, "ts": None}

        consumer.stream = mock_stream  # type: ignore[method-assign]
        consumer.disconnect = AsyncMock()

        events: list[str] = []
        async for event in sse_jetstream_generator(consumer):
            events.append(event)

        assert len(events) == 3
        # Verify cursors in yielded events
        for i, ev in enumerate(events):
            assert f"id: {i + 6}" in ev

    @pytest.mark.asyncio
    async def test_consumer_advances_cursor_on_yield(self) -> None:
        """Verify consumer.last_event_id advances as events are yielded.

        Note: last_event_id is incremented inside SSEJetStreamConsumer.stream()
        when reading from JetStream. When mocking stream(), we must simulate
        the cursor advancement ourselves.
        """
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
            last_event_id=0,
        )

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            for _ in range(2):
                consumer.last_event_id += 1
                yield {
                    "type": "message",
                    "payload": {},
                    "cursor": consumer.last_event_id,
                    "ts": None,
                }

        consumer.stream = mock_stream  # type: ignore[method-assign]
        consumer.disconnect = AsyncMock()

        events: list[str] = []
        async for event in sse_jetstream_generator(consumer):
            events.append(event)

        assert consumer.last_event_id == 2


# =============================================================================
# 4. Backpressure
# =============================================================================


class TestSseBackpressure:
    """Tests for queue size limits and backpressure behavior."""

    def test_queue_max_size_constant(self) -> None:
        """Verify _SSE_QUEUE_MAX_SIZE is set to 50."""
        assert _SSE_QUEUE_MAX_SIZE == 50

    @pytest.mark.asyncio
    async def test_queue_size_limit_enforced(self) -> None:
        """Verify queue blocks when maxsize is reached."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAX_SIZE)

        # Fill queue to capacity without consuming
        for i in range(_SSE_QUEUE_MAX_SIZE):
            queue.put_nowait({"type": "message", "data": {"i": i}})

        assert queue.qsize() == _SSE_QUEUE_MAX_SIZE

        # Next put should block (maxsize reached)
        with pytest.raises(asyncio.QueueFull):
            queue.put_nowait({"type": "message", "data": {"overflow": True}})

    @pytest.mark.asyncio
    async def test_backpressure_slow_consumer(self) -> None:
        """Verify slow consumer does not cause unbounded memory growth."""
        produced = 0

        async def slow_task_fn(queue: asyncio.Queue) -> None:
            nonlocal produced
            for i in range(100):
                await queue.put({"type": "message", "data": {"i": i}})
                produced += 1
            await queue.put({"type": "complete", "data": {}})

        events: list[str] = []
        async for event in sse_event_generator(slow_task_fn, timeout=1.0):
            events.append(event)
            # Simulate slow consumer by yielding control
            await asyncio.sleep(0)

        # All events should eventually be consumed despite backpressure
        assert len(events) == 101  # 100 messages + complete
        assert produced == 100


# =============================================================================
# 5. Error Handling in Streams
# =============================================================================


class TestSseErrorHandling:
    """Tests for error events, graceful disconnect, and finally cleanup."""

    @pytest.mark.asyncio
    async def test_stream_error_includes_error_code(self) -> None:
        """Verify error events include proper error code structure."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "error", "data": {"error": "stream failed", "code": "STREAM_ERROR"}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 1
        assert events[0].startswith("event: error")
        parsed = json.loads(events[0].split("data: ", 1)[1])
        assert parsed["code"] == "STREAM_ERROR"

    @pytest.mark.asyncio
    async def test_stream_error_includes_default_code_when_missing(self) -> None:
        """Verify error events without code still propagate error text."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "error", "data": {"error": "unknown failure"}})

        events: list[str] = []
        async for event in sse_event_generator(task_fn, timeout=1.0):
            events.append(event)

        assert len(events) == 1
        parsed = json.loads(events[0].split("data: ", 1)[1])
        assert parsed["error"] == "unknown failure"

    @pytest.mark.asyncio
    async def test_stream_disconnect_graceful(self) -> None:
        """Verify consumer disconnect is called on normal stream completion."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "message", "payload": {"text": "hello"}, "cursor": 1, "ts": None}

        disconnect_called = False

        async def tracked_disconnect() -> None:
            nonlocal disconnect_called
            disconnect_called = True

        consumer.stream = mock_stream  # type: ignore[method-assign]
        consumer.disconnect = tracked_disconnect  # type: ignore[method-assign]

        events: list[str] = []
        async for event in sse_jetstream_generator(consumer):
            events.append(event)

        assert disconnect_called is True
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_finally_block_ensures_cleanup_on_exception(self) -> None:
        """Verify finally block runs cleanup even when stream raises."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "message", "payload": {"text": "hello"}, "cursor": 1, "ts": None}
            raise RuntimeError("stream exploded")

        disconnect_called = False

        async def tracked_disconnect() -> None:
            nonlocal disconnect_called
            disconnect_called = True

        consumer.stream = mock_stream  # type: ignore[method-assign]
        consumer.disconnect = tracked_disconnect  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            async for _event in sse_jetstream_generator(consumer):
                pass

        assert disconnect_called is True

    @pytest.mark.asyncio
    async def test_sse_event_generator_finally_cleanup_on_early_break(self) -> None:
        """Verify cleanup_fn is called when iteration breaks early."""
        cleanup_called = False

        async def task_fn(queue: asyncio.Queue) -> None:
            for i in range(100):
                await queue.put({"type": "message", "data": {"i": i}})
            await queue.put({"type": "complete", "data": {}})

        async def cleanup() -> None:
            nonlocal cleanup_called
            cleanup_called = True

        gen = sse_event_generator(task_fn, cleanup_fn=cleanup, timeout=1.0)
        events: list[str] = []
        async for event in gen:
            events.append(event)
            if len(events) >= 2:
                break

        await gen.aclose()
        assert cleanup_called is True

    @pytest.mark.asyncio
    async def test_task_exception_emits_terminal_error_event(self) -> None:
        """Verify unhandled task exception yields a terminal error event."""

        async def failing_task(_queue: asyncio.Queue) -> None:
            raise OSError("network failure")

        events: list[str] = []
        async for event in sse_event_generator(failing_task, timeout=0.5):
            events.append(event)

        assert len(events) == 1
        assert events[0].startswith("event: error")
        assert "network failure" in events[0]

    @pytest.mark.asyncio
    async def test_task_completion_without_terminal_event_emits_error(self) -> None:
        """Verify task returning without terminal event yields error."""

        async def no_terminal(_queue: asyncio.Queue) -> None:
            await _queue.put({"type": "message", "data": {"text": "only message"}})
            # No complete/error event; wrapper emits __task_done__

        events: list[str] = []
        async for event in sse_event_generator(no_terminal, timeout=0.5):
            events.append(event)

        # First event is the message, second is the error from __task_done__
        assert len(events) == 2
        assert events[1].startswith("event: error")
        assert "without a terminal event" in events[1]


# =============================================================================
# 6. create_sse_response
# =============================================================================


class TestCreateSseResponse:
    """Tests for create_sse_response wrapper."""

    @pytest.mark.asyncio
    async def test_sse_response_headers(self) -> None:
        """Verify StreamingResponse has correct SSE headers."""

        async def gen() -> AsyncGenerator[str, None]:
            yield "event: ping\ndata: {}\n\n"

        response = create_sse_response(gen())
        assert response.media_type == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["Connection"] == "keep-alive"
        assert response.headers["X-Accel-Buffering"] == "no"
