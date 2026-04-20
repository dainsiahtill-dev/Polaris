"""Event streamer for SSE serialization and channel multiplexing."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from collections.abc import AsyncGenerator
from enum import Enum
from typing import Any

from polaris.kernelone.llm.engine.contracts import AIStreamEvent
from polaris.kernelone.llm.shared_contracts import StreamEventType

try:
    import msgpack  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    msgpack = None


class SerializationFormat(str, Enum):
    """Supported payload serialization formats."""

    JSON = "json"
    MSGPACK = "msgpack"


def infer_channel(event: AIStreamEvent) -> str:
    """Map stream events to a default multiplex channel."""
    if event.type == StreamEventType.REASONING_CHUNK:
        return "thinking"
    if event.type in {
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_CALL,
        StreamEventType.TOOL_END,
        StreamEventType.TOOL_RESULT,
    }:
        return "tool_log"
    return "final_answer"


class EventStreamer:
    """Serialize AI stream events to SSE and multiplex by channel."""

    def __init__(
        self,
        *,
        serialization_format: SerializationFormat = SerializationFormat.JSON,
        max_queue_size: int = 1024,
    ) -> None:
        self._serialization_format = serialization_format
        self._max_queue_size = max(1, int(max_queue_size))
        self._subscribers: dict[str, list[asyncio.Queue[bytes | None]]] = {}
        self._closed = False

    @property
    def serialization_format(self) -> SerializationFormat:
        return self._serialization_format

    def serialize_event(
        self,
        event: AIStreamEvent,
        *,
        channel: str | None = None,
    ) -> bytes:
        """Serialize one event to SSE bytes."""
        selected_channel = channel or infer_channel(event)
        payload = event.to_dict()
        payload["channel"] = selected_channel
        payload["format"] = self._serialization_format.value

        if self._serialization_format == SerializationFormat.MSGPACK:
            if msgpack is None:
                raise RuntimeError("msgpack serialization requested but msgpack is not installed")
            binary = msgpack.packb(payload, use_bin_type=True)
            data = base64.b64encode(binary).decode("ascii")
        else:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        event_name = event.type.value
        return f"event: {event_name}\ndata: {data}\n\n".encode()

    async def publish(self, event: AIStreamEvent, *, channel: str | None = None) -> None:
        """Publish one event to subscribers of a channel."""
        if self._closed:
            return
        selected_channel = channel or infer_channel(event)
        packet = self.serialize_event(event, channel=selected_channel)
        queues = list(self._subscribers.get(selected_channel, [])) + list(self._subscribers.get("*", []))
        for queue in queues:
            try:
                queue.put_nowait(packet)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(packet)

    async def broadcast(self, events: AsyncGenerator[AIStreamEvent, None]) -> None:
        """Publish all events from an async generator."""
        try:
            async for event in events:
                await self.publish(event)
        finally:
            await self.close()

    async def subscribe(self, channel: str = "*") -> AsyncGenerator[bytes, None]:
        """Subscribe to one channel ('*' subscribes all channels)."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.setdefault(channel, []).append(queue)
        try:
            while True:
                payload = await queue.get()
                if payload is None:
                    break
                yield payload
        finally:
            subscribers = self._subscribers.get(channel, [])
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                self._subscribers.pop(channel, None)

    async def close(self) -> None:
        """Close all channel subscribers."""
        if self._closed:
            return
        self._closed = True
        for queues in self._subscribers.values():
            for queue in queues:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)

    def get_stats(self) -> dict[str, Any]:
        """Current streamer runtime statistics."""
        return {
            "closed": self._closed,
            "serialization_format": self._serialization_format.value,
            "channels": {channel: len(queues) for channel, queues in self._subscribers.items()},
            "max_queue_size": self._max_queue_size,
        }


__all__ = [
    "EventStreamer",
    "SerializationFormat",
    "infer_channel",
]
