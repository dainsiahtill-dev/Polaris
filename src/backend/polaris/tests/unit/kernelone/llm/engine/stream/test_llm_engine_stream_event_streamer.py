"""Tests for polaris.kernelone.llm.engine.stream.event_streamer."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from polaris.kernelone.llm.engine.contracts import AIStreamEvent
from polaris.kernelone.llm.engine.stream.event_streamer import (
    EventStreamer,
    SerializationFormat,
    infer_channel,
)
from polaris.kernelone.llm.shared_contracts import StreamEventType


class TestInferChannel:
    def test_reasoning_chunk(self) -> None:
        event = AIStreamEvent(type=StreamEventType.REASONING_CHUNK, reasoning="think")
        assert infer_channel(event) == "thinking"

    def test_tool_start(self) -> None:
        event = AIStreamEvent(type=StreamEventType.TOOL_START, tool_call={"tool": "t"})
        assert infer_channel(event) == "tool_log"

    def test_tool_call(self) -> None:
        event = AIStreamEvent(type=StreamEventType.TOOL_CALL, tool_call={"tool": "t"})
        assert infer_channel(event) == "tool_log"

    def test_tool_end(self) -> None:
        event = AIStreamEvent(type=StreamEventType.TOOL_END, tool_call={"tool": "t"})
        assert infer_channel(event) == "tool_log"

    def test_tool_result(self) -> None:
        event = AIStreamEvent(type=StreamEventType.TOOL_RESULT, tool_result={"r": 1})
        assert infer_channel(event) == "tool_log"

    def test_chunk(self) -> None:
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")
        assert infer_channel(event) == "final_answer"

    def test_complete(self) -> None:
        event = AIStreamEvent(type=StreamEventType.COMPLETE)
        assert infer_channel(event) == "final_answer"


class TestEventStreamerInit:
    def test_default_format(self) -> None:
        streamer = EventStreamer()
        assert streamer.serialization_format == SerializationFormat.JSON

    def test_msgpack_format(self) -> None:
        streamer = EventStreamer(serialization_format=SerializationFormat.MSGPACK)
        assert streamer.serialization_format == SerializationFormat.MSGPACK

    def test_max_queue_size(self) -> None:
        streamer = EventStreamer(max_queue_size=512)
        assert streamer._max_queue_size == 512

    def test_max_queue_size_minimum(self) -> None:
        streamer = EventStreamer(max_queue_size=0)
        assert streamer._max_queue_size == 1


class TestEventStreamerSerializeEvent:
    def test_json_serialization(self) -> None:
        streamer = EventStreamer()
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")
        data = streamer.serialize_event(event)
        assert data.startswith(b"event: chunk\ndata: ")
        assert b"channel" in data
        assert b"format" in data

    def test_custom_channel(self) -> None:
        streamer = EventStreamer()
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")
        data = streamer.serialize_event(event, channel="custom")
        assert b'"channel":"custom"' in data

    def test_msgpack_not_installed(self) -> None:
        streamer = EventStreamer(serialization_format=SerializationFormat.MSGPACK)
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")
        with (
            patch.object(
                __import__("polaris.kernelone.llm.engine.stream.event_streamer", fromlist=["msgpack"]),
                "msgpack",
                None,
            ),
            pytest.raises(RuntimeError, match="msgpack is not installed"),
        ):
            streamer.serialize_event(event)


@pytest.mark.asyncio
class TestEventStreamerPublish:
    async def test_publish_to_subscribers(self) -> None:
        streamer = EventStreamer()
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")

        # Subscribe to channel
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1024)
        streamer._subscribers.setdefault("final_answer", []).append(queue)

        await streamer.publish(event)
        assert queue.qsize() == 1

    async def test_publish_to_wildcard(self) -> None:
        streamer = EventStreamer()
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")

        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1024)
        streamer._subscribers.setdefault("*", []).append(queue)

        await streamer.publish(event)
        assert queue.qsize() == 1

    async def test_publish_when_closed(self) -> None:
        streamer = EventStreamer()
        streamer._closed = True
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")
        await streamer.publish(event)  # Should not raise

    async def test_publish_drops_oldest_on_full(self) -> None:
        streamer = EventStreamer(max_queue_size=1)
        event = AIStreamEvent(type=StreamEventType.CHUNK, chunk="hello")

        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)
        streamer._subscribers.setdefault("final_answer", []).append(queue)

        # Fill the queue
        queue.put_nowait(b"old")
        await streamer.publish(event)
        # Should have dropped old and added new
        assert queue.qsize() == 1


@pytest.mark.asyncio
class TestEventStreamerBroadcast:
    async def test_broadcast_events(self) -> None:
        streamer = EventStreamer()

        async def gen():
            yield AIStreamEvent(type=StreamEventType.CHUNK, chunk="a")
            yield AIStreamEvent(type=StreamEventType.CHUNK, chunk="b")

        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1024)
        streamer._subscribers.setdefault("final_answer", []).append(queue)

        await streamer.broadcast(gen())
        assert queue.qsize() == 2
        assert streamer._closed is True

    async def test_broadcast_closes_on_exception(self) -> None:
        streamer = EventStreamer()

        async def gen():
            yield AIStreamEvent(type=StreamEventType.CHUNK, chunk="a")
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await streamer.broadcast(gen())
        assert streamer._closed is True


@pytest.mark.asyncio
class TestEventStreamerSubscribe:
    async def test_subscribe_receives_events(self) -> None:
        streamer = EventStreamer()
        sub = streamer.subscribe("test")

        # Put an event in the queue after starting subscription
        async def send_event():
            await asyncio.sleep(0.01)
            queues = streamer._subscribers.get("test", [])
            if queues:
                queues[0].put_nowait(b"event data")
            # Close to end subscription
            await streamer.close()

        task = asyncio.create_task(send_event())
        messages = []
        async for msg in sub:
            messages.append(msg)
        await task
        assert len(messages) == 1
        assert messages[0] == b"event data"

    async def test_unsubscribe_on_exit(self) -> None:
        streamer = EventStreamer()
        sub = streamer.subscribe("test")
        # Immediately close
        await streamer.close()
        messages = []
        async for msg in sub:
            messages.append(msg)
        assert len(messages) == 0
        assert "test" not in streamer._subscribers


@pytest.mark.asyncio
class TestEventStreamerClose:
    async def test_close_sets_flag(self) -> None:
        streamer = EventStreamer()
        await streamer.close()
        assert streamer._closed is True

    async def test_close_idempotent(self) -> None:
        streamer = EventStreamer()
        await streamer.close()
        await streamer.close()  # Should not raise
        assert streamer._closed is True

    async def test_close_sends_none_to_queues(self) -> None:
        streamer = EventStreamer()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1024)
        streamer._subscribers.setdefault("test", []).append(queue)
        await streamer.close()
        assert queue.qsize() == 1
        assert queue.get_nowait() is None


class TestEventStreamerGetStats:
    def test_stats(self) -> None:
        streamer = EventStreamer(serialization_format=SerializationFormat.MSGPACK, max_queue_size=512)
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=512)
        streamer._subscribers.setdefault("test", []).append(queue)

        stats = streamer.get_stats()
        assert stats["closed"] is False
        assert stats["serialization_format"] == "msgpack"
        assert stats["channels"] == {"test": 1}
        assert stats["max_queue_size"] == 512
