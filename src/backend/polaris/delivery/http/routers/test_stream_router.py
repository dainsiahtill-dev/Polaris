"""Tests for polaris.delivery.http.routers.stream_router module.

Covers:
- format_sse_event: AIStreamEvent to SSE bytes formatting
- sse_stream_generator: EventStreamer subscription to SSE bytes
- stream_health: Health check endpoint
- EventStreamer integration with streaming
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import pytest
from polaris.delivery.http.routers.stream_router import format_sse_event
from polaris.kernelone.llm.engine.contracts import AIStreamEvent
from polaris.kernelone.llm.engine.stream.config import StreamConfig
from polaris.kernelone.stream import EventStreamer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class TestFormatSSEEvent:
    """Tests for format_sse_event function."""

    def test_chunk_event(self) -> None:
        """Verify chunk event formatting."""
        event = AIStreamEvent.chunk_event("Hello, world!")
        result = format_sse_event(event)

        assert b"event: text" in result
        assert b"Hello, world!" in result
        assert result.endswith(b"\n\n")

    def test_reasoning_event(self) -> None:
        """Verify reasoning event formatting."""
        event = AIStreamEvent.reasoning_event("Let me think...")
        result = format_sse_event(event)

        assert b"event: thinking" in result
        assert b"Let me think..." in result

    def test_tool_call_event(self) -> None:
        """Verify tool call event formatting."""
        tool_call = {
            "tool": "test_tool",
            "arguments": {"arg1": "value1"},
            "call_id": "call_123",
        }
        event = AIStreamEvent.tool_call_event(tool_call)
        result = format_sse_event(event)

        assert b"event: tool" in result
        assert b"test_tool" in result

    def test_complete_event(self) -> None:
        """Verify complete event formatting."""
        event = AIStreamEvent.complete({"output": "done"})
        result = format_sse_event(event)

        assert b"event: done" in result

    def test_error_event(self) -> None:
        """Verify error event formatting."""
        event = AIStreamEvent.error_event("Something went wrong")
        result = format_sse_event(event)

        assert b"event: error" in result
        assert b"Something went wrong" in result


class TestEventStreamerBroadcast:
    """Tests for EventStreamer broadcast with SSE formatting."""

    @pytest.fixture
    def streamer(self) -> EventStreamer:
        """Create a test EventStreamer."""
        config = StreamConfig(buffer_size=100)
        return EventStreamer(config=config, max_queue_size=50)

    @pytest.mark.asyncio
    async def test_broadcast_sse_formatting(self, streamer: EventStreamer) -> None:
        """Verify broadcast events are properly formatted as SSE."""
        received: list[bytes] = []

        async def consumer() -> None:
            async for event in streamer.subscribe():
                formatted = format_sse_event(event)
                received.append(formatted)

        consumer_task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        await streamer.publish(AIStreamEvent.chunk_event("first"))
        await streamer.publish(AIStreamEvent.chunk_event("second"))
        await streamer.publish(None)

        try:
            await asyncio.wait_for(consumer_task, timeout=5.0)
        except asyncio.TimeoutError:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
            raise

        assert len(received) == 2
        assert b"first" in received[0]
        assert b"second" in received[1]

    @pytest.mark.asyncio
    async def test_broadcast_with_tool_lifecycle_sse(self, streamer: EventStreamer) -> None:
        """Verify inject_tool_lifecycle adds TOOL_START/TOOL_END events with proper SSE format."""
        received: list[bytes] = []

        async def consumer() -> None:
            async for event in streamer.subscribe():
                formatted = format_sse_event(event)
                received.append(formatted)

        async def event_generator() -> AsyncGenerator[AIStreamEvent, None]:
            yield AIStreamEvent.tool_call_event(
                {
                    "tool": "test_tool",
                    "call_id": "c1",
                    "arguments": {},
                }
            )
            yield AIStreamEvent.tool_result_event(
                {
                    "tool": "test_tool",
                    "call_id": "c1",
                    "output": "result",
                }
            )

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

        # Should have: tool_start, tool_call, tool_end, tool_result (4 events)
        assert len(received) == 4
        assert b"tool_start" in received[0]
        assert b"tool_call" in received[1]
        assert b"tool_end" in received[2]
        assert b"tool_result" in received[3]


class TestStreamHealth:
    """Tests for stream health endpoint."""

    @pytest.mark.asyncio
    async def test_stream_health_response(self) -> None:
        """Verify stream health returns correct structure."""
        # Import here to avoid circular imports
        from polaris.delivery.http.routers.stream_router import stream_health

        result = await stream_health()

        assert result == {"status": "healthy", "streaming": "enabled"}
