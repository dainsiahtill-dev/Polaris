"""Polaris AI Platform - SSE Event Streamer

Full-duplex SSE (Server-Sent Events) streaming with multiplexing support.
Converts AIStreamEvent to SSE format and broadcasts to multiple consumers.

Reuses:
- AIStreamEvent from kernelone.llm.engine.contracts
- StreamConfig from kernelone.llm.engine.stream.config
- StreamEventType from kernelone.llm.shared_contracts
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.constants import SSE_EVENT_ID_MODULO
from polaris.kernelone.llm.engine.contracts import AIStreamEvent
from polaris.kernelone.llm.engine.stream.config import StreamConfig
from polaris.kernelone.llm.shared_contracts import StreamEventType

logger = logging.getLogger(__name__)


# Type alias for the stream generator
AIStreamGenerator = AsyncGenerator[AIStreamEvent, None]


@dataclass
class SSEEvent:
    """SSE-formatted event with optional comment and data fields.

    Attributes:
        event: Optional event type field (e.g. "chunk", "tool_call").
        data: The data payload (can be str or dict).
        id: Optional event ID for client-side tracking.
        retry: Optional retry timeout in milliseconds.
    """

    event: str | None = None
    data: str | dict[str, Any] | None = None
    id: str | None = None
    retry: int | None = None

    def to_bytes(self) -> bytes:
        """Serialize to SSE wire format bytes.

        Format::
            id: <event_id>\n
            event: <event_type>\n
            data: <json_payload>\n
            \n
            \n
        """
        lines: list[str] = []

        if self.id is not None:
            lines.append(f"id: {self.id}")
        if self.event is not None:
            lines.append(f"event: {self.event}")
        if self.data is not None:
            if isinstance(self.data, dict):
                lines.append(f"data: {json.dumps(self.data, ensure_ascii=False)}")
            else:
                lines.append(f"data: {self.data}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")

        # SSE requires blank line to terminate event (\n\n)
        lines.append("")
        lines.append("")
        return "\n".join(lines).encode("utf-8")


class EventStreamer:
    """SSE serializer + asyncio.Queue-based multiplexer.

    Publishes AIStreamEvent to multiple async consumers via per-consumer
    asyncio.Queue instances. Provides backpressure-aware buffering with
    graceful shutdown and subscription limits.

    Features:
    - Multiple independent consumer subscriptions
    - Backpressure-aware event dropping (oldest dropped first)
    - Graceful shutdown with in-flight event drainage
    - Configurable max subscriptions limit
    - Per-consumer dropped event statistics

    Usage::

        streamer = EventStreamer()

        # Publisher side
        async def events():
            yield AIStreamEvent.chunk_event("Hello")
            yield AIStreamEvent.complete()

        await streamer.broadcast(events())

        # Consumer side (multiple consumers supported)
        async for event in streamer.subscribe():
            print(event.type, event.chunk)
    """

    def __init__(
        self,
        config: StreamConfig | None = None,
        max_queue_size: int = 100,
        max_subscriptions: int | None = None,
    ) -> None:
        """Initialize the event streamer.

        Args:
            config: Stream configuration for defaults.
            max_queue_size: Maximum events per consumer queue before backpressure.
            max_subscriptions: Maximum number of concurrent subscriptions.
                None means unlimited.
        """
        self._config = config or StreamConfig.from_env()
        self._max_queue_size = max_queue_size
        self._max_subscriptions = max_subscriptions
        self._consumers: list[asyncio.Queue[AIStreamEvent | None]] = []
        self._consumer_dropped: dict[int, int] = {}  # id(q) -> dropped count
        self._closed: bool = False
        self._total_published: int = 0
        self._total_dropped: int = 0

    @property
    def config(self) -> StreamConfig:
        """The stream configuration."""
        return self._config

    @property
    def max_subscriptions(self) -> int | None:
        """Maximum concurrent subscriptions (None = unlimited)."""
        return self._max_subscriptions

    @property
    def subscription_count(self) -> int:
        """Current number of active subscriptions."""
        return len(self._consumers)

    def sse_serialize(self, event: AIStreamEvent) -> bytes:
        """Serialize an AIStreamEvent to SSE wire format.

        Args:
            event: The AIStreamEvent to serialize.

        Returns:
            SSE-formatted bytes ready for HTTP streaming.
        """
        # Map StreamEventType to SSE event field
        event_type_map = {
            StreamEventType.CHUNK: "content_chunk",
            StreamEventType.REASONING_CHUNK: "thinking_chunk",
            StreamEventType.TOOL_START: "tool_start",
            StreamEventType.TOOL_CALL: "tool_call",
            StreamEventType.TOOL_END: "tool_end",
            StreamEventType.TOOL_RESULT: "tool_result",
            StreamEventType.META: "meta",
            StreamEventType.COMPLETE: "complete",
            StreamEventType.ERROR: "error",
        }
        sse_event = SSEEvent(
            event=event_type_map.get(event.type),
            data=event.to_dict(),
            id=str(hash(str(event.to_dict())) % SSE_EVENT_ID_MODULO),
        )
        return sse_event.to_bytes()

    async def publish(self, event: AIStreamEvent | None) -> None:
        """Publish an event to all subscriber queues directly.

        Args:
            event: The event to publish, or None to signal end-of-stream.
        """
        if self._closed:
            return

        if event is not None:
            self._total_published += 1

        # Fan out directly to all consumer queues
        for consumer_queue in self._consumers:
            q_id = id(consumer_queue)
            try:
                consumer_queue.put_nowait(event)
            except asyncio.QueueFull:
                # Consumer slow: drop oldest to make room
                dropped = 0
                with contextlib.suppress(asyncio.QueueEmpty):
                    consumer_queue.get_nowait()
                    dropped = 1
                try:
                    consumer_queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Even after dropping one, still full - consumer is overwhelmed
                    with contextlib.suppress(asyncio.QueueEmpty):
                        consumer_queue.get_nowait()
                        dropped = 2
                    try:
                        consumer_queue.put_nowait(event)
                    except asyncio.QueueFull:
                        dropped = 3

                if dropped > 0:
                    self._consumer_dropped[q_id] = self._consumer_dropped.get(q_id, 0) + dropped
                    self._total_dropped += dropped
                    logger.warning(
                        "[event-streamer] consumer %d queue overflow, dropped %d events",
                        q_id,
                        dropped,
                    )

    async def subscribe(self) -> AsyncGenerator[AIStreamEvent, None]:
        """Subscribe to this stream, receiving a copy of all published events.

        Each call creates an independent consumer queue. Raises asyncio.QueueFull
        if max_subscriptions limit is reached.

        Yields:
            AIStreamEvent instances published to this streamer.

        Raises:
            asyncio.QueueFull: If max_subscriptions limit is reached.

        Example:
            # Two independent consumers
            async def consumer1():
                async for event in streamer.subscribe():
                    print("C1:", event.type)

            async def consumer2():
                async for event in streamer.subscribe():
                    print("C2:", event.type)
        """
        if self._max_subscriptions is not None and len(self._consumers) >= self._max_subscriptions:
            raise asyncio.QueueFull(f"Max subscriptions ({self._max_subscriptions}) reached")

        consumer_queue: asyncio.Queue[AIStreamEvent | None] = asyncio.Queue(maxsize=self._max_queue_size)
        q_id = id(consumer_queue)
        self._consumer_dropped[q_id] = 0
        self._consumers.append(consumer_queue)
        try:
            while True:
                event = await consumer_queue.get()
                if event is None:
                    break
                yield event
        except asyncio.CancelledError:
            # Graceful shutdown path: drain any remaining events before exiting
            while not consumer_queue.empty():
                try:
                    event = consumer_queue.get_nowait()
                    if event is None:
                        break
                    yield event
                except asyncio.QueueEmpty:
                    break
            raise
        finally:
            self._consumers.remove(consumer_queue)
            self._consumer_dropped.pop(q_id, None)

    async def broadcast(
        self,
        events: AIStreamGenerator,
        *,
        task_name: str = "event-streamer-broadcast",
        inject_tool_lifecycle: bool = False,
    ) -> None:
        """Consume an event stream and broadcast to all subscribers.

        This is the main entry point for connecting a StreamExecutor output
        to the SSE multiplexer.

        Args:
            events: The AIStreamEvent generator to consume.
            task_name: Name for the broadcast task (for debugging).
            inject_tool_lifecycle: If True, automatically inject tool_start
                before tool_call events and tool_end after tool_result events.
        """
        active_tool_call: tuple[str, str] | None = None  # (tool_name, call_id)

        try:
            async for event in events:
                if inject_tool_lifecycle:
                    # Auto-inject tool_start before tool_call
                    if event.type == StreamEventType.TOOL_CALL and event.tool_call:
                        tool_name = str(event.tool_call.get("tool") or "")
                        call_id = str(event.tool_call.get("call_id") or "")
                        if tool_name and not active_tool_call:
                            await self.publish(
                                AIStreamEvent.tool_start_event(
                                    tool_name=tool_name,
                                    call_id=call_id or None,
                                )
                            )
                            active_tool_call = (tool_name, call_id)

                    # Auto-inject tool_end after tool_result
                    if event.type == StreamEventType.TOOL_RESULT and active_tool_call:
                        await self.publish(
                            AIStreamEvent.tool_end_event(
                                tool_name=active_tool_call[0],
                                call_id=active_tool_call[1] or None,
                                success=True,
                            )
                        )
                        active_tool_call = None

                await self.publish(event)

            # Flush any remaining tool lifecycle at stream end
            if inject_tool_lifecycle and active_tool_call:
                await self.publish(
                    AIStreamEvent.tool_end_event(
                        tool_name=active_tool_call[0],
                        call_id=active_tool_call[1] or None,
                        success=True,
                        meta={"stream_end": True},
                    )
                )

        finally:
            # Signal end of stream
            await self.publish(None)

    async def close(self, *, timeout: float = 5.0) -> None:
        """Close the streamer gracefully, waiting for in-flight events.

        Args:
            timeout: Maximum seconds to wait for graceful shutdown.
        """
        if self._closed:
            return
        self._closed = True

        # Send end signals to all consumers
        for consumer_queue in self._consumers:
            with contextlib.suppress(asyncio.QueueFull):
                consumer_queue.put_nowait(None)

    @property
    def closed(self) -> bool:
        """Whether the streamer has been closed."""
        return self._closed

    def get_stats(self) -> dict[str, Any]:
        """Get streamer statistics for monitoring.

        Returns:
            Dict with consumer count, queue sizes, dropped events, and status.
        """
        consumer_stats: list[dict[str, Any]] = []
        for consumer_queue in self._consumers:
            q_id = id(consumer_queue)
            consumer_stats.append(
                {
                    "queue_size": consumer_queue.qsize(),
                    "dropped_events": self._consumer_dropped.get(q_id, 0),
                }
            )

        return {
            "consumer_count": len(self._consumers),
            "consumer_max_size": self._max_queue_size,
            "max_subscriptions": self._max_subscriptions,
            "consumers": consumer_stats,
            "total_published": self._total_published,
            "total_dropped": self._total_dropped,
            "closed": self._closed,
        }


class AsyncBackpressureBuffer:
    """Async-native backpressure buffer using asyncio.Queue.

    Provides the same buffering semantics as BackpressureBuffer but
    using asyncio.Queue for proper async/await support without GIL contention.

    Replaces threading.Lock-based BackpressureBuffer for use in async contexts.
    """

    def __init__(
        self,
        max_size: int | None = None,
        backoff_seconds: float = 0.1,
        config: StreamConfig | None = None,
    ) -> None:
        """Initialize the async backpressure buffer.

        Args:
            max_size: Maximum buffer size. Defaults to StreamConfig.buffer_size.
            backoff_seconds: Time to wait when buffer is full.
            config: Stream configuration for defaults.
        """
        cfg = config or StreamConfig.from_env()
        self._max_size = max_size if max_size is not None else cfg.buffer_size
        self._backoff_seconds = backoff_seconds
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_size)
        self._total_queued: int = 0
        self._total_dequeued: int = 0
        self._backpressure_events: int = 0

    @property
    def size(self) -> int:
        """Current buffer size (approximate in async context)."""
        return self._queue.qsize()

    @property
    def max_size(self) -> int:
        """Maximum buffer size."""
        return self._max_size

    @property
    def backpressure_events(self) -> int:
        """Number of times backpressure was applied."""
        return self._backpressure_events

    async def feed(self, chunk: str) -> None:
        """Add a chunk to the buffer with backpressure control.

        If the buffer is full, waits until space is available.

        Args:
            chunk: The chunk text to add.
        """
        while True:
            try:
                self._queue.put_nowait(chunk)
                self._total_queued += 1
                return
            except asyncio.QueueFull:
                self._backpressure_events += 1
                await asyncio.sleep(self._backoff_seconds)

    def feed_sync(self, chunk: str) -> bool:
        """Add a chunk to the buffer without waiting (sync fallback).

        Args:
            chunk: The chunk text to add.

        Returns:
            True if added, False if buffer is full.
        """
        try:
            self._queue.put_nowait(chunk)
            self._total_queued += 1
            return True
        except asyncio.QueueFull:
            self._backpressure_events += 1
            return False

    async def drain(self) -> list[str]:
        """Drain all buffered chunks.

        Returns:
            List of all buffered chunks.
        """
        chunks: list[str] = []
        while not self._queue.empty():
            try:
                chunk = self._queue.get_nowait()
                chunks.append(chunk)
                self._total_dequeued += 1
            except asyncio.QueueEmpty:
                break
        return chunks

    def drain_sync(self) -> list[str]:
        """Drain all buffered chunks synchronously.

        Returns:
            List of all buffered chunks.
        """
        chunks: list[str] = []
        while not self._queue.empty():
            try:
                chunk = self._queue.get_nowait()
                chunks.append(chunk)
                self._total_dequeued += 1
            except asyncio.QueueEmpty:
                break
        return chunks

    async def clear(self) -> None:
        """Clear all buffered chunks."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._total_dequeued += 1
            except asyncio.QueueEmpty:
                break

    def get_stats(self) -> dict[str, Any]:
        """Get buffer statistics.

        Returns:
            Dictionary with buffer stats.
        """
        return {
            "current_size": self._queue.qsize(),
            "max_size": self._max_size,
            "total_queued": self._total_queued,
            "total_dequeued": self._total_dequeued,
            "backpressure_events": self._backpressure_events,
        }
