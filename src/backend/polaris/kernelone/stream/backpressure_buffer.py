"""Async backpressure buffer for KernelOne stream primitives."""

from __future__ import annotations

import asyncio
from typing import Any

from polaris.kernelone.llm.engine.stream.config import StreamConfig


class AsyncBackpressureBuffer:
    """Async-native backpressure buffer using ``asyncio.Queue``."""

    def __init__(
        self,
        max_size: int | None = None,
        backoff_seconds: float = 0.1,
        config: StreamConfig | None = None,
    ) -> None:
        cfg = config or StreamConfig.from_env()
        self._max_size = max_size if max_size is not None else cfg.buffer_size
        self._backoff_seconds = backoff_seconds
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_size)
        self._total_queued = 0
        self._total_dequeued = 0
        self._backpressure_events = 0

    @property
    def size(self) -> int:
        """Current buffer size."""
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
        """Add a chunk, waiting until capacity is available."""
        while True:
            try:
                self._queue.put_nowait(chunk)
                self._total_queued += 1
                return
            except asyncio.QueueFull:
                self._backpressure_events += 1
                await asyncio.sleep(self._backoff_seconds)

    def feed_sync(self, chunk: str) -> bool:
        """Add a chunk without waiting."""
        try:
            self._queue.put_nowait(chunk)
            self._total_queued += 1
            return True
        except asyncio.QueueFull:
            self._backpressure_events += 1
            return False

    async def drain(self) -> list[str]:
        """Drain all buffered chunks."""
        return self.drain_sync()

    def drain_sync(self) -> list[str]:
        """Drain all buffered chunks synchronously."""
        chunks: list[str] = []
        while not self._queue.empty():
            try:
                chunk = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            chunks.append(chunk)
            self._total_dequeued += 1
        return chunks

    async def clear(self) -> None:
        """Clear all buffered chunks."""
        self.drain_sync()

    def get_stats(self) -> dict[str, Any]:
        """Return buffer statistics."""
        return {
            "current_size": self._queue.qsize(),
            "max_size": self._max_size,
            "total_queued": self._total_queued,
            "total_dequeued": self._total_dequeued,
            "backpressure_events": self._backpressure_events,
        }
