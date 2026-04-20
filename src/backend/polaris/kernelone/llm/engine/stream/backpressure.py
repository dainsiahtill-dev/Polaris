"""Polaris AI Platform - Backpressure Buffer

DEPRECATED: This module is deprecated. Use polaris.kernelone.stream.sse_streamer
instead for async-native backpressure control with asyncio.Queue.

Thread-safe buffer with backpressure control for streaming chunks.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import warnings
from typing import Any

from .config import StreamConfig, get_default_stream_config

logger = logging.getLogger(__name__)


class BackpressureBuffer:
    """Thread-safe buffer with backpressure control.

    DEPRECATED: This class uses threading.Lock which causes GIL contention
    in async contexts. Use AsyncBackpressureBuffer from
    polaris.kernelone.stream.sse_streamer instead.

    Manages chunk buffering with configurable size limits and automatic
    backpressure application when the buffer is full.

    Uses StreamConfig for default values (H-04 Fix).
    """

    def __init__(
        self,
        max_size: int | None = None,
        backoff_seconds: float = 0.1,
        config: StreamConfig | None = None,
    ) -> None:
        """Initialize the backpressure buffer.

        DEPRECATED: Use AsyncBackpressureBuffer from
        polaris.kernelone.stream.sse_streamer for async contexts.

        Args:
            max_size: Maximum number of chunks to buffer. Defaults to StreamConfig.buffer_size.
            backoff_seconds: Time to wait when buffer is full.
            config: Stream configuration for default values.
        """
        warnings.warn(
            "BackpressureBuffer is deprecated. "
            "Use polaris.kernelone.stream.sse_streamer.AsyncBackpressureBuffer instead. "
            "This class uses threading.Lock which causes GIL contention in async contexts.",
            DeprecationWarning,
            stacklevel=2,
        )
        cfg = config or get_default_stream_config()
        self._buffer: list[str] = []
        self._buffer_lock = threading.Lock()
        self._max_size = max_size if max_size is not None else cfg.buffer_size
        self._backoff_seconds = backoff_seconds
        self._total_queued = 0
        self._total_dequeued = 0
        self._backpressure_events = 0

    @property
    def size(self) -> int:
        """Current buffer size."""
        with self._buffer_lock:
            return len(self._buffer)

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
            # Check if buffer has space (use local lock for quick check)
            with self._buffer_lock:
                if len(self._buffer) < self._max_size:
                    self._buffer.append(chunk)
                    self._total_queued += 1
                    return
                # Buffer is full, will wait
                self._backpressure_events += 1

            # Wait outside the lock to allow other coroutines to proceed
            await asyncio.sleep(self._backoff_seconds)

    def feed_sync(self, chunk: str) -> bool:
        """Add a chunk to the buffer without backpressure (sync version).

        Args:
            chunk: The chunk text to add.

        Returns:
            True if added, False if buffer is full.
        """
        if len(self._buffer) >= self._max_size:
            logger.warning(
                "[backpressure-buffer] Buffer full, cannot add chunk (size=%d, max=%d)",
                len(self._buffer),
                self._max_size,
            )
            self._backpressure_events += 1
            return False
        self._buffer.append(chunk)
        self._total_queued += 1
        return True

    def drain(self) -> list[str]:
        """Drain all buffered chunks.

        Returns:
            List of all buffered chunks.
        """
        with self._buffer_lock:
            chunks = self._buffer
            self._total_dequeued += len(chunks)
            self._buffer = []
        return chunks

    def clear(self) -> None:
        """Clear all buffered chunks."""
        with self._buffer_lock:
            self._total_dequeued += len(self._buffer)
            self._buffer = []

    def get_stats(self) -> dict[str, Any]:
        """Get buffer statistics.

        Returns:
            Dictionary with buffer stats.
        """
        return {
            "current_size": len(self._buffer),
            "max_size": self._max_size,
            "total_queued": self._total_queued,
            "total_dequeued": self._total_dequeued,
            "backpressure_events": self._backpressure_events,
        }
