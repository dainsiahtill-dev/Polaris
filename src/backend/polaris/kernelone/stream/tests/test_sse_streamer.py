"""Tests for polaris.kernelone.stream.sse_streamer module.

Covers:
- EventStreamer: SSE serialization, multiplexing, broadcast
- AsyncBackpressureBuffer: async feed/drain operations
- SSEEvent: serialization format
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from polaris.kernelone.llm.engine.contracts import AIStreamEvent, AIStreamGenerator
from polaris.kernelone.llm.engine.stream.config import StreamConfig
from polaris.kernelone.llm.shared_contracts import StreamEventType
from polaris.kernelone.stream.sse_streamer import (
    AsyncBackpressureBuffer,
    EventStreamer,
    SSEEvent,
)


class TestSSEEvent:
    """Tests for SSEEvent dataclass."""

    def test_sse_event_to_bytes_basic(self) -> None:
        """Verify basic SSE event serialization."""
        event = SSEEvent(event="chunk", data={"text": "hello"})
        result = event.to_bytes()
        assert b"event: chunk" in result
        assert b"data: " in result
        assert result.endswith(b"\n\n")

    def test_sse_event_to_bytes_with_id(self) -> None:
        """Verify SSE event with ID field."""
        event = SSEEvent(event="chunk", data={"text": "hi"}, id="123")
        result = event.to_bytes()
        assert b"id: 123" in result

    def test_sse_event_to_bytes_dict_data(self) -> None:
        """Verify dict data is JSON serialized."""
        event = SSEEvent(event="chunk", data={"key": "value"})
        result = event.to_bytes()
        assert b'"key"' in result
        assert b'"value"' in result


class TestEventStreamer:
    """Tests for EventStreamer SSE multiplexer."""

    @pytest.fixture
    def streamer(self) -> EventStreamer:
        """Create a test EventStreamer."""
        config = StreamConfig(buffer_size=100)
        return EventStreamer(config=config, max_queue_size=50)

    @pytest.mark.asyncio
    async def test_sse_serialize_chunk(self, streamer: EventStreamer) -> None:
        """Verify chunk event serialization to SSE format."""
        event = AIStreamEvent.chunk_event("Hello, world!")
        result = streamer.sse_serialize(event)

        assert b"event: chunk" in result
        assert b"Hello, world!" in result
        assert result.endswith(b"\n\n")

    @pytest.mark.asyncio
    async def test_sse_serialize_reasoning(self, streamer: EventStreamer) -> None:
        """Verify reasoning event serialization."""
        event = AIStreamEvent.reasoning_event("Let me think...")
        result = streamer.sse_serialize(event)

        assert b"event: reasoning" in result
        assert b"Let me think..." in result

    @pytest.mark.asyncio
    async def test_sse_serialize_tool_call(self, streamer: EventStreamer) -> None:
        """Verify tool_call event serialization."""
        tool_call = {
            "tool": "test_tool",
            "arguments": {"arg1": "value1"},
            "call_id": "call_123",
        }
        event = AIStreamEvent.tool_call_event(tool_call)
        result = streamer.sse_serialize(event)

        assert b"event: tool" in result
        assert b"test_tool" in result

    @pytest.mark.asyncio
    async def test_sse_serialize_complete(self, streamer: EventStreamer) -> None:
        """Verify complete event serialization."""
        event = AIStreamEvent.complete({"output": "done"})
        result = streamer.sse_serialize(event)

        assert b"event: complete" in result

    @pytest.mark.asyncio
    async def test_sse_serialize_error(self, streamer: EventStreamer) -> None:
        """Verify error event serialization."""
        event = AIStreamEvent.error_event("Something went wrong")
        result = streamer.sse_serialize(event)

        assert b"event: error" in result
        assert b"Something went wrong" in result

    @pytest.mark.asyncio
    async def test_publish_and_subscribe(self, streamer: EventStreamer) -> None:
        """Verify basic publish/subscribe cycle."""
        events_received: list[AIStreamEvent] = []

        async def consumer() -> None:
            async for event in streamer.subscribe():
                events_received.append(event)

        # Start consumer and give it time to subscribe
        consumer_task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)  # Let consumer start and subscribe

        # Publish some events
        await streamer.publish(AIStreamEvent.chunk_event("first"))
        await streamer.publish(AIStreamEvent.chunk_event("second"))

        # Signal end
        await streamer.publish(None)

        # Wait for consumer with timeout
        try:
            await asyncio.wait_for(consumer_task, timeout=5.0)
        except asyncio.TimeoutError:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
            raise

        assert len(events_received) == 2
        assert events_received[0].chunk == "first"
        assert events_received[1].chunk == "second"

    @pytest.mark.asyncio
    async def test_multiplex_multiple_consumers(self, streamer: EventStreamer) -> None:
        """Verify multiple consumers can subscribe simultaneously."""
        consumer1_events: list[AIStreamEvent] = []
        consumer2_events: list[AIStreamEvent] = []

        async def consumer1() -> None:
            async for event in streamer.subscribe():
                consumer1_events.append(event)

        async def consumer2() -> None:
            async for event in streamer.subscribe():
                consumer2_events.append(event)

        # Start both consumers and give them time to subscribe
        c1_task = asyncio.create_task(consumer1())
        c2_task = asyncio.create_task(consumer2())
        await asyncio.sleep(0.05)  # Let consumers start and subscribe

        # Publish events
        await streamer.publish(AIStreamEvent.chunk_event("shared"))
        await streamer.publish(AIStreamEvent.reasoning_event("thinking"))

        # Signal end
        await streamer.publish(None)

        # Wait for consumers with timeout handling
        try:
            await asyncio.wait_for(asyncio.gather(c1_task, c2_task), timeout=5.0)
        except asyncio.TimeoutError:
            c1_task.cancel()
            c2_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(c1_task, c2_task)
            raise

        assert len(consumer1_events) == 2
        assert len(consumer2_events) == 2
        assert consumer1_events[0].chunk == "shared"
        assert consumer2_events[0].chunk == "shared"

    @pytest.mark.asyncio
    async def test_broadcast_from_generator(self, streamer: EventStreamer) -> None:
        """Verify broadcast() consumes a generator and distributes events."""
        received: list[AIStreamEvent] = []

        async def consumer() -> None:
            async for event in streamer.subscribe():
                received.append(event)

        async def event_generator() -> AIStreamGenerator:
            yield AIStreamEvent.chunk_event("from")
            yield AIStreamEvent.chunk_event("generator")

        # Start consumer and give it time to subscribe
        consumer_task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)  # Let consumer start and subscribe

        # Broadcast from generator
        await streamer.broadcast(event_generator())

        # Wait for consumer with timeout handling
        try:
            await asyncio.wait_for(consumer_task, timeout=5.0)
        except asyncio.TimeoutError:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
            raise

        assert len(received) == 2
        assert received[0].chunk == "from"
        assert received[1].chunk == "generator"

    @pytest.mark.asyncio
    async def test_close(self, streamer: EventStreamer) -> None:
        """Verify close() sends end signals to all consumers."""
        events: list[AIStreamEvent] = []

        async def consumer() -> None:
            async for event in streamer.subscribe():
                events.append(event)

        consumer_task = asyncio.create_task(consumer())
        await asyncio.sleep(0.1)  # Let consumer subscribe

        await streamer.close()
        await asyncio.wait_for(consumer_task, timeout=5.0)

        assert streamer.closed is True

    @pytest.mark.asyncio
    async def test_get_stats(self, streamer: EventStreamer) -> None:
        """Verify get_stats() returns correct statistics."""
        stats = streamer.get_stats()

        assert "consumer_count" in stats
        assert "closed" in stats
        assert "total_published" in stats
        assert "total_dropped" in stats
        assert stats["closed"] is False

    @pytest.mark.asyncio
    async def test_max_subscriptions(self, streamer: EventStreamer) -> None:
        """Verify max_subscriptions limit is enforced when iteration starts."""
        limited_streamer = EventStreamer(max_subscriptions=1)

        # Start first consumer - should succeed
        sub1_task = asyncio.create_task(self._consume_all(limited_streamer.subscribe()))
        await asyncio.sleep(0.05)  # Let it start

        # Start second consumer via iteration - should raise
        gen = limited_streamer.subscribe()
        with pytest.raises(asyncio.QueueFull):
            # Need to iterate to trigger the check
            await gen.__anext__()

        # Clean up
        await limited_streamer.close()
        sub1_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sub1_task

    @pytest.mark.asyncio
    async def test_broadcast_with_tool_lifecycle(self, streamer: EventStreamer) -> None:
        """Verify broadcast auto-injects tool_start/tool_end around tool_call/tool_result."""
        received: list[AIStreamEvent] = []

        async def consumer() -> None:
            async for event in streamer.subscribe():
                received.append(event)

        async def event_generator() -> AIStreamGenerator:
            yield AIStreamEvent.tool_call_event({"tool": "test_tool", "call_id": "c1", "arguments": {}})
            yield AIStreamEvent.chunk_event("thinking...")
            yield AIStreamEvent.tool_result_event({"tool": "test_tool", "call_id": "c1", "output": "done"})

        consumer_task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        await streamer.broadcast(event_generator(), inject_tool_lifecycle=True)

        try:
            await asyncio.wait_for(consumer_task, timeout=5.0)
        except asyncio.TimeoutError:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
            raise

        # Should have: tool_start, tool_call, chunk, tool_end, tool_result
        assert len(received) == 5
        assert received[0].type == StreamEventType.TOOL_START
        assert received[1].type == StreamEventType.TOOL_CALL
        assert received[2].type == StreamEventType.CHUNK
        assert received[3].type == StreamEventType.TOOL_END
        assert received[4].type == StreamEventType.TOOL_RESULT

    async def _consume_all(self, gen: Any) -> list[AIStreamEvent]:
        """Helper to consume all events from a subscription."""
        events = []
        async for event in gen:
            events.append(event)
        return events


class TestAsyncBackpressureBuffer:
    """Tests for AsyncBackpressureBuffer."""

    @pytest.fixture
    def buffer(self) -> AsyncBackpressureBuffer:
        """Create a test buffer."""
        config = StreamConfig(buffer_size=10)
        return AsyncBackpressureBuffer(max_size=5, backoff_seconds=0.01, config=config)

    @pytest.mark.asyncio
    async def test_feed_and_drain(self, buffer: AsyncBackpressureBuffer) -> None:
        """Verify basic feed/drain cycle."""
        await buffer.feed("chunk1")
        await buffer.feed("chunk2")

        chunks = await buffer.drain()
        assert len(chunks) == 2
        assert chunks[0] == "chunk1"
        assert chunks[1] == "chunk2"

    @pytest.mark.asyncio
    async def test_feed_sync(self, buffer: AsyncBackpressureBuffer) -> None:
        """Verify synchronous feed."""
        result = buffer.feed_sync("sync_chunk")
        assert result is True

    @pytest.mark.asyncio
    async def test_feed_sync_full_buffer(self, buffer: AsyncBackpressureBuffer) -> None:
        """Verify feed_sync returns False when buffer is full."""
        # Fill the buffer
        for _ in range(5):
            buffer.feed_sync("x")

        # Next one should fail
        result = buffer.feed_sync("should_fail")
        assert result is False

    @pytest.mark.asyncio
    async def test_drain_sync(self, buffer: AsyncBackpressureBuffer) -> None:
        """Verify synchronous drain."""
        await buffer.feed("a")
        await buffer.feed("b")

        chunks = buffer.drain_sync()
        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_clear(self, buffer: AsyncBackpressureBuffer) -> None:
        """Verify clear empties the buffer."""
        await buffer.feed("x")
        await buffer.feed("y")

        await buffer.clear()
        chunks = await buffer.drain()
        assert len(chunks) == 0

    @pytest.mark.asyncio
    async def test_get_stats(self, buffer: AsyncBackpressureBuffer) -> None:
        """Verify statistics."""
        await buffer.feed("a")
        await buffer.feed("b")

        stats = buffer.get_stats()
        assert stats["total_queued"] == 2
        assert stats["current_size"] == 2

    @pytest.mark.asyncio
    async def test_backpressure_on_full(self, buffer: AsyncBackpressureBuffer) -> None:
        """Verify feed_sync returns False when buffer is full (non-blocking backpressure)."""
        # Fill buffer using feed_sync (non-blocking)
        for i in range(5):
            result = buffer.feed_sync(f"chunk{i}")
            assert result is True

        # Next feed should return False (buffer full)
        result = buffer.feed_sync("blocked")
        assert result is False
        assert buffer.backpressure_events > 0
