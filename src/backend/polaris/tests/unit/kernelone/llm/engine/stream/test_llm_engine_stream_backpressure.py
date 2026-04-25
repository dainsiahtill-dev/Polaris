"""Tests for polaris.kernelone.llm.engine.stream.backpressure."""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.llm.engine.stream.backpressure import BackpressureBuffer
from polaris.kernelone.llm.engine.stream.config import StreamConfig


class TestBackpressureBufferInit:
    def test_deprecation_warning(self) -> None:
        with pytest.warns(DeprecationWarning, match="deprecated"):
            BackpressureBuffer()

    def test_default_size_from_config(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            assert buf.max_size > 0

    def test_custom_max_size(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer(max_size=50)
            assert buf.max_size == 50

    def test_custom_config(self) -> None:
        with pytest.warns(DeprecationWarning):
            cfg = StreamConfig(buffer_size=200)
            buf = BackpressureBuffer(config=cfg)
            assert buf.max_size == 200

    def test_custom_backoff(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer(backoff_seconds=0.5)
            assert buf._backoff_seconds == 0.5


class TestBackpressureBufferSize:
    def test_empty(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            assert buf.size == 0

    def test_after_feed_sync(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            buf.feed_sync("chunk1")
            assert buf.size == 1


class TestBackpressureBufferFeedSync:
    def test_adds_chunk(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            result = buf.feed_sync("hello")
            assert result is True
            assert buf.size == 1

    def test_rejects_when_full(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer(max_size=1)
            buf.feed_sync("first")
            result = buf.feed_sync("second")
            assert result is False
            assert buf.size == 1

    def test_counts_backpressure_events(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer(max_size=1)
            buf.feed_sync("first")
            buf.feed_sync("second")  # Rejected
            assert buf.backpressure_events == 1


@pytest.mark.asyncio
class TestBackpressureBufferFeed:
    async def test_adds_chunk(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            await buf.feed("hello")
            assert buf.size == 1

    async def test_waits_when_full(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer(max_size=1, backoff_seconds=0.01)
            await buf.feed("first")

            # Second feed should wait then succeed after drain
            async def delayed_drain():
                await asyncio.sleep(0.02)
                buf.drain()

            task = asyncio.create_task(delayed_drain())
            await buf.feed("second")
            await task
            assert buf.size == 1


class TestBackpressureBufferDrain:
    def test_returns_all_chunks(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            buf.feed_sync("a")
            buf.feed_sync("b")
            chunks = buf.drain()
            assert chunks == ["a", "b"]
            assert buf.size == 0

    def test_counts_dequeued(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            buf.feed_sync("a")
            buf.drain()
            stats = buf.get_stats()
            assert stats["total_dequeued"] == 1


class TestBackpressureBufferClear:
    def test_clears_all(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            buf.feed_sync("a")
            buf.feed_sync("b")
            buf.clear()
            assert buf.size == 0

    def test_counts_cleared_as_dequeued(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            buf.feed_sync("a")
            buf.clear()
            stats = buf.get_stats()
            assert stats["total_dequeued"] == 1


class TestBackpressureBufferGetStats:
    def test_stats(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer(max_size=10)
            buf.feed_sync("a")
            buf.feed_sync("b")
            stats = buf.get_stats()
            assert stats["current_size"] == 2
            assert stats["max_size"] == 10
            assert stats["total_queued"] == 2
            assert stats["total_dequeued"] == 0
            assert stats["backpressure_events"] == 0

    def test_stats_after_drain(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer()
            buf.feed_sync("a")
            buf.drain()
            stats = buf.get_stats()
            assert stats["current_size"] == 0
            assert stats["total_queued"] == 1
            assert stats["total_dequeued"] == 1

    def test_stats_after_rejection(self) -> None:
        with pytest.warns(DeprecationWarning):
            buf = BackpressureBuffer(max_size=1)
            buf.feed_sync("a")
            buf.feed_sync("b")  # Rejected
            stats = buf.get_stats()
            assert stats["backpressure_events"] == 1
