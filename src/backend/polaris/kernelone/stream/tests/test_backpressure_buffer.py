"""Tests for KernelOne async stream backpressure buffering."""

from __future__ import annotations

import importlib

import pytest
from polaris.kernelone.llm.engine.stream.config import StreamConfig
from polaris.kernelone.stream.backpressure_buffer import AsyncBackpressureBuffer


@pytest.fixture
def buffer() -> AsyncBackpressureBuffer:
    config = StreamConfig(buffer_size=100)
    return AsyncBackpressureBuffer(max_size=5, backoff_seconds=0.01, config=config)


@pytest.mark.asyncio
async def test_feed_and_drain(buffer: AsyncBackpressureBuffer) -> None:
    await buffer.feed("chunk1")
    await buffer.feed("chunk2")

    assert buffer.size == 2
    assert await buffer.drain() == ["chunk1", "chunk2"]
    assert buffer.size == 0


def test_feed_sync(buffer: AsyncBackpressureBuffer) -> None:
    assert buffer.feed_sync("chunk1") is True
    assert buffer.feed_sync("chunk2") is True
    assert buffer.size == 2


def test_feed_sync_full_buffer(buffer: AsyncBackpressureBuffer) -> None:
    for i in range(5):
        assert buffer.feed_sync(f"chunk{i}") is True

    assert buffer.feed_sync("overflow") is False
    assert buffer.backpressure_events == 1


def test_drain_sync(buffer: AsyncBackpressureBuffer) -> None:
    buffer.feed_sync("chunk1")
    buffer.feed_sync("chunk2")

    assert buffer.drain_sync() == ["chunk1", "chunk2"]
    assert buffer.size == 0


@pytest.mark.asyncio
async def test_clear(buffer: AsyncBackpressureBuffer) -> None:
    await buffer.feed("chunk1")
    await buffer.feed("chunk2")

    await buffer.clear()

    assert buffer.size == 0


def test_get_stats(buffer: AsyncBackpressureBuffer) -> None:
    buffer.feed_sync("chunk1")

    stats = buffer.get_stats()

    assert stats["current_size"] == 1
    assert stats["max_size"] == 5
    assert stats["total_queued"] == 1
    assert stats["total_dequeued"] == 0
    assert stats["backpressure_events"] == 0


def test_legacy_http_event_stream_module_is_not_importable() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("polaris.kernelone.stream.sse_streamer")
