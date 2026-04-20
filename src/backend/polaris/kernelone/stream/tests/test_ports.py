"""Tests for polaris.kernelone.stream.ports module.

Covers:
- stream_from_async_gen: normal iteration, early break, exception exit
- stream_from_sync_gen: normal iteration, early break, exception exit
- StreamChunk, StreamObserver, and other stream utilities
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.stream.ports import (
    StreamChunk,
    StreamObserver,
    stream_from_async_gen,
    stream_from_sync_gen,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator


class TestStreamChunk:
    """Tests for StreamChunk dataclass."""

    def test_stream_chunk_creation(self) -> None:
        """Verify StreamChunk can be created with required fields."""
        chunk = StreamChunk(data="hello", sequence=0)
        assert chunk.data == "hello"
        assert chunk.sequence == 0
        assert chunk.is_final is False
        assert chunk.metadata == {}

    def test_stream_chunk_with_metadata(self) -> None:
        """Verify StreamChunk accepts metadata."""
        chunk = StreamChunk(
            data="test",
            sequence=1,
            is_final=True,
            metadata={"key": "value"},
        )
        assert chunk.metadata == {"key": "value"}
        assert chunk.is_final is True


class TestStreamObserver:
    """Tests for StreamObserver callback handler."""

    def test_observer_initial_state(self) -> None:
        """Verify observer starts with empty state."""
        observer = StreamObserver()
        assert observer.chunks == []
        assert observer.final_chunk is None
        assert observer.errored is False

    def test_observer_on_chunk_callback(self) -> None:
        """Verify chunk callbacks are invoked."""
        observer = StreamObserver()
        callback = MagicMock()
        observer.on_chunk(callback)

        chunk = StreamChunk(data="test", sequence=0)
        observer._fire_chunk(chunk)

        callback.assert_called_once_with(chunk)
        assert observer.chunks == [chunk]

    def test_observer_on_done_callback(self) -> None:
        """Verify done callbacks are invoked."""
        observer = StreamObserver()
        callback = MagicMock()
        observer.on_done(callback)

        observer._fire_done()
        callback.assert_called_once()

    def test_observer_on_error_callback(self) -> None:
        """Verify error callbacks are invoked with exception."""
        observer = StreamObserver()
        callback = MagicMock()
        observer.on_error(callback)

        exc = ValueError("test error")
        observer._fire_error(exc)

        callback.assert_called_once_with(exc)
        assert observer.errored is True


# -----------------------------------------------------------------------------
# Tests for stream_from_async_gen
# -----------------------------------------------------------------------------


class TestStreamFromAsyncGen:
    """Tests for stream_from_async_gen generator wrapper."""

    @pytest.mark.asyncio
    async def test_normal_iteration(self) -> None:
        """Verify normal iteration completes all items."""

        async def gen() -> AsyncGenerator[str, None]:
            yield "a"
            yield "b"
            yield "c"

        stream = stream_from_async_gen(gen)
        chunks = [chunk async for chunk in stream]

        assert len(chunks) == 4  # 3 data + 1 final
        assert chunks[0].data == "a"
        assert chunks[1].data == "b"
        assert chunks[2].data == "c"
        assert chunks[3].data == ""
        assert chunks[3].is_final is True

    @pytest.mark.asyncio
    async def test_early_break(self) -> None:
        """Verify early break triggers cleanup when generator is closed.

        Note: Python does not automatically close async generators when
        iteration exits early. Our finally block schedules cleanup via
        aclose(), which runs when the generator is garbage collected
        or explicitly closed. We verify cleanup by explicitly closing
        the generator.
        """
        cleanup_called = False

        async def gen() -> AsyncGenerator[str, None]:
            nonlocal cleanup_called
            try:
                yield "a"
                yield "b"
                yield "c"
            finally:
                cleanup_called = True

        stream: AsyncGenerator[StreamChunk, None] = stream_from_async_gen(gen)
        chunks = []
        async with contextlib.aclosing(stream) as s:
            async for chunk in s:
                chunks.append(chunk)
                if len(chunks) >= 2:
                    break

        assert len(chunks) == 2
        assert cleanup_called is True

    @pytest.mark.asyncio
    async def test_exception_exit(self) -> None:
        """Verify exception path yields error chunk and cleans up."""

        async def gen() -> AsyncGenerator[str, None]:
            yield "a"
            raise RuntimeError("test error")

        stream = stream_from_async_gen(gen)
        chunks = [chunk async for chunk in stream]

        # Should get the error chunk
        error_chunk = chunks[-1]
        assert error_chunk.is_final is True
        # Error message format is "[stream error: {exc}]" where exc is str(exc)
        assert "test error" in error_chunk.data
        assert "error" in error_chunk.metadata

    @pytest.mark.asyncio
    async def test_async_gen_with_proper_finally(self) -> None:
        """Verify async generator with finally block is properly cleaned up."""
        cleanup_called = False

        async def gen() -> AsyncGenerator[str, None]:
            nonlocal cleanup_called
            try:
                yield "a"
                yield "b"
            finally:
                cleanup_called = True

        stream: AsyncGenerator[StreamChunk, None] = stream_from_async_gen(gen)
        chunks = []
        async with contextlib.aclosing(stream) as s:
            async for chunk in s:
                chunks.append(chunk)
                if len(chunks) >= 1:
                    break

        assert len(chunks) == 1
        assert cleanup_called is True


# -----------------------------------------------------------------------------
# Tests for stream_from_sync_gen
# -----------------------------------------------------------------------------


class TestStreamFromSyncGen:
    """Tests for stream_from_sync_gen generator wrapper."""

    @pytest.mark.asyncio
    async def test_normal_iteration(self) -> None:
        """Verify normal iteration completes all items."""

        def gen() -> Generator[str, None, None]:
            yield "a"
            yield "b"
            yield "c"

        stream = stream_from_sync_gen(gen())
        chunks = [chunk async for chunk in stream]

        assert len(chunks) == 4  # 3 data + 1 final
        assert chunks[0].data == "a"
        assert chunks[1].data == "b"
        assert chunks[2].data == "c"
        assert chunks[3].data == ""
        assert chunks[3].is_final is True

    @pytest.mark.asyncio
    async def test_early_break(self) -> None:
        """Verify early break exits and closes generator when closed.

        Note: Python does not automatically close async generators when
        iteration exits early. Our finally block schedules cleanup via
        close(), which runs when the generator is garbage collected
        or explicitly closed. We verify cleanup by explicitly closing
        the generator.
        """
        closed = False

        def gen() -> Generator[str, None, None]:
            nonlocal closed
            try:
                yield from ["a", "b", "c"]
            finally:
                closed = True

        stream: AsyncGenerator[StreamChunk, None] = stream_from_sync_gen(gen())
        chunks = []
        async with contextlib.aclosing(stream) as s:
            async for chunk in s:
                chunks.append(chunk)
                if len(chunks) >= 2:
                    break

        assert len(chunks) == 2
        assert closed is True

    @pytest.mark.asyncio
    async def test_exception_exit(self) -> None:
        """Verify exception path yields error chunk and cleans up."""
        closed = False

        def gen() -> Generator[str, None, None]:
            nonlocal closed
            try:
                yield "a"
                raise RuntimeError("test error")
            finally:
                closed = True

        stream = stream_from_sync_gen(gen())
        chunks = [chunk async for chunk in stream]

        # Should get the error chunk
        error_chunk = chunks[-1]
        assert error_chunk.is_final is True
        assert "test error" in error_chunk.data
        assert closed is True

    @pytest.mark.asyncio
    async def test_sync_gen_with_cleanup_in_yield(self) -> None:
        """Verify generator with cleanup in yield handles early break."""

        class SyncGenWithCleanup:
            def __init__(self) -> None:
                self.cleaned_up = False
                self._gen = self._generate()

            def _generate(self):
                try:
                    yield "a"
                    yield "b"
                finally:
                    self.cleaned_up = True

            def __iter__(self):
                return self._gen

            def close(self) -> None:
                self._gen.close()
                self.cleaned_up = True

        sync_gen = SyncGenWithCleanup()
        stream: AsyncGenerator[StreamChunk, None] = stream_from_sync_gen(
            cast("Generator[str, None, None]", sync_gen._gen)
        )
        chunks = []
        async with contextlib.aclosing(stream) as s:
            async for chunk in s:
                chunks.append(chunk)
                if len(chunks) >= 1:
                    break

        assert len(chunks) == 1
        assert sync_gen.cleaned_up is True


# -----------------------------------------------------------------------------
# Integration tests
# -----------------------------------------------------------------------------


class TestStreamIntegration:
    """Integration tests for stream utilities."""

    @pytest.mark.asyncio
    async def test_stream_to_observer(self) -> None:
        """Verify stream can be consumed by observer."""
        observer = StreamObserver()

        async def gen() -> AsyncGenerator[str, None]:
            for x in ["hello", "world"]:
                yield x

        stream = stream_from_async_gen(gen)

        async for chunk in stream:
            observer._fire_chunk(chunk)

        # 2 data chunks + 1 final chunk
        assert len(observer.chunks) == 3
        assert observer.final_chunk is not None
        assert observer.final_chunk.is_final is True

    @pytest.mark.asyncio
    async def test_mixed_sync_async_stream(self) -> None:
        """Verify sync and async streams can be chained."""

        def sync_gen() -> Generator[str, None, None]:
            yield "1"
            yield "2"

        async def async_gen() -> AsyncGenerator[str, None]:
            yield "3"
            yield "4"

        sync_stream = stream_from_sync_gen(sync_gen())
        async_stream = stream_from_async_gen(async_gen)

        combined = []
        async for chunk in sync_stream:
            combined.append(chunk.data)
        async for chunk in async_stream:
            combined.append(chunk.data)

        # Filter out empty final chunks
        data = [x for x in combined if x]
        assert data == ["1", "2", "3", "4"]
