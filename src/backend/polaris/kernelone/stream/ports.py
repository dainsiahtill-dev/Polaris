"""Stream port definitions for KernelOne stream/ subsystem.

Defines async streaming contracts and utility adapters. Aligned with
master_types.py StreamEvent/StreamChunk/StreamDone/StreamError types.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Generator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from polaris.kernelone.contracts.technical import (
    StreamChunk as MasterStreamChunk,
)
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Stream wrappers aligned with master_types.py
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamChunk:
    """A single chunk in an async text stream.

    Aligned with master_types.py StreamChunk but scoped to text-only
    for simplicity. Use binary wrappers for byte streams.
    """

    data: str
    sequence: int
    is_final: bool = False
    timestamp: datetime = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_master(self) -> MasterStreamChunk:
        """Convert to master_types.StreamChunk."""
        return MasterStreamChunk(
            data=self.data,
            sequence=self.sequence,
            is_final=self.is_final,
        )

    @classmethod
    def from_master(cls, master: MasterStreamChunk) -> StreamChunk:
        return cls(data=master.data, sequence=master.sequence, is_final=master.is_final)


# -----------------------------------------------------------------------------
# Async Iterator Types
# -----------------------------------------------------------------------------

AsyncTextStream = AsyncIterator[StreamChunk]
AsyncByteStream = AsyncIterator[bytes]


# -----------------------------------------------------------------------------
# StreamObserver
# ----------------------------------------------------------------------------_


class StreamObserver:
    """Observer that receives stream chunks via callbacks.

    Usage::

        observer = StreamObserver()

        async def on_chunk(chunk: StreamChunk) -> None:
            print(chunk.data, end="", flush=True)

        observer.on_chunk(on_chunk)
        await observe_stream(stream, observer)
    """

    def __init__(self) -> None:
        self._chunk_handlers: list[Callable[[StreamChunk], Any]] = []
        self._done_handlers: list[Callable[[], Any]] = []
        self._error_handlers: list[Callable[[Exception], Any]] = []
        self._received: list[StreamChunk] = []
        self._final_received: StreamChunk | None = None
        self._errored: bool = False

    def on_chunk(self, handler: Callable[[StreamChunk], Any]) -> None:
        """Register a handler called for each StreamChunk."""
        self._chunk_handlers.append(handler)

    def on_done(self, handler: Callable[[], Any]) -> None:
        """Register a handler called when the stream completes."""
        self._done_handlers.append(handler)

    def on_error(self, handler: Callable[[Exception], Any]) -> None:
        """Register a handler called when the stream errors."""
        self._error_handlers.append(handler)

    @property
    def chunks(self) -> list[StreamChunk]:
        """All chunks received so far."""
        return list(self._received)

    @property
    def final_chunk(self) -> StreamChunk | None:
        """The final chunk (is_final=True), if received."""
        return self._final_received

    @property
    def errored(self) -> bool:
        return self._errored

    def _fire_chunk(self, chunk: StreamChunk) -> None:
        self._received.append(chunk)
        if chunk.is_final:
            self._final_received = chunk
        for handler in self._chunk_handlers:
            try:
                result = handler(chunk)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except (RuntimeError, ValueError) as exc:
                logger.warning("StreamObserver chunk handler raised: %s", exc)

    def _fire_done(self) -> None:
        for handler in self._done_handlers:
            try:
                result = handler()
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except (RuntimeError, ValueError) as exc:
                logger.warning("StreamObserver done handler raised: %s", exc)

    def _fire_error(self, exc: Exception) -> None:
        self._errored = True
        for handler in self._error_handlers:
            try:
                result = handler(exc)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except (RuntimeError, ValueError) as e:
                logger.warning("StreamObserver error handler raised: %s", e)


# -----------------------------------------------------------------------------
# StreamAdapter
# -----------------------------------------------------------------------------


class StreamAdapter(AsyncIterator[StreamChunk]):
    """Base adapter wrapping a raw async iterator with sequence numbering.

    Subclasses implement the raw source; this class handles:
    - Sequence numbering
    - Final chunk detection
    - Error wrapping
    """

    def __init__(self, source: AsyncIterator[Any]) -> None:
        self._source = source
        self._sequence: int = 0

    async def __anext__(self) -> StreamChunk:
        try:
            raw = await self._source.__anext__()
            chunk = self._to_chunk(raw, is_final=False)
            self._sequence += 1
            return chunk
        except StopAsyncIteration:
            chunk = self._to_chunk(self._final_value(), is_final=True)
            raise StopAsyncIteration from None
        except (RuntimeError, ValueError) as exc:
            raise StopAsyncIteration from exc

    def _to_chunk(self, raw: Any, *, is_final: bool) -> StreamChunk:
        """Convert raw source value to StreamChunk. Override for custom mapping."""
        if isinstance(raw, StreamChunk):
            return raw
        if isinstance(raw, MasterStreamChunk):
            return StreamChunk.from_master(raw)
        if isinstance(raw, str):
            return StreamChunk(data=raw, sequence=self._sequence, is_final=is_final)
        return StreamChunk(data=str(raw), sequence=self._sequence, is_final=is_final)

    def _final_value(self) -> str:
        return ""


class TextStreamAdapter(StreamAdapter):
    """Adapter for string-yielding async iterators (e.g. LLM text streams)."""

    def __init__(self, source: AsyncIterator[str]) -> None:
        super().__init__(source)


class ByteStreamAdapter(StreamAdapter):
    """Adapter for bytes-yielding async iterators (e.g. file download streams)."""

    def __init__(self, source: AsyncIterator[bytes]) -> None:
        super().__init__(source)

    def _to_chunk(self, raw: Any, *, is_final: bool) -> StreamChunk:
        if isinstance(raw, bytes):
            import base64

            return StreamChunk(
                data=base64.b64encode(raw).decode("ascii"),
                sequence=self._sequence,
                is_final=is_final,
                metadata={"encoding": "base64"},
            )
        return super()._to_chunk(raw, is_final=is_final)


# -----------------------------------------------------------------------------
# Stream utilities
# -----------------------------------------------------------------------------


async def observe_stream(
    stream: AsyncIterator[StreamChunk],
    observer: StreamObserver,
) -> None:
    """Consume a stream, forwarding chunks/errors/done to an observer.

    Usage::

        observer = StreamObserver()
        observer.on_chunk(lambda c: print(c.data, end=""))
        await observe_stream(stream, observer)
    """
    try:
        async for chunk in stream:
            observer._fire_chunk(chunk)
            if chunk.is_final:
                observer._fire_done()
                return
    except (RuntimeError, ValueError) as exc:
        observer._fire_error(exc)
        observer._fire_done()


async def stream_from_async_gen(
    gen: Callable[[], AsyncGenerator[str, None]],
) -> AsyncGenerator[StreamChunk, None]:
    """Build a StreamChunk iterator from an async generator factory.

    Wraps an async generator in a try-finally structure to ensure proper
    cleanup when the generator exits early (break, exception, or cancellation).

    Uses explicit __anext__ iteration instead of async for to ensure
    synchronous cleanup when the generator exits.

    Usage::

        async def llm_stream():
            async for text in llm_client.stream():
                yield text

        stream = stream_from_async_gen(llm_stream)
        async for chunk in stream:
            print(chunk.data, end="")

    Note:
        The underlying async generator is closed via its aclose() method
        when iteration exits, whether through normal completion, break,
        or exception.
    """
    seq = 0
    async_gen = gen()
    try:
        while True:
            try:
                text = await async_gen.__anext__()
                yield StreamChunk(data=text, sequence=seq, is_final=False)
                seq += 1
            except StopAsyncIteration:
                yield StreamChunk(data="", sequence=seq, is_final=True)
                break
    except (RuntimeError, ValueError) as exc:
        yield StreamChunk(
            data=f"[stream error: {exc}]",
            sequence=seq,
            is_final=True,
            metadata={"error": str(exc)},
        )
    finally:
        # Ensure the async generator is properly closed.
        # This handles early break, exception, and cancellation cases.
        # Using __anext__ instead of async for ensures this runs synchronously.
        if hasattr(async_gen, "aclose") and callable(async_gen.aclose):
            with contextlib.suppress(asyncio.CancelledError):
                try:
                    # Add timeout to prevent async_generator_athrow blocking for 128s
                    # when the underlying async generator is waiting on network I/O
                    await asyncio.wait_for(async_gen.aclose(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("[stream] async_gen.aclose() timed out after 5s")
                except StopAsyncIteration:
                    pass  # Normal: generator already closed


def stream_from_sync_gen(
    gen: Generator[str, None, None],
) -> AsyncGenerator[StreamChunk, None]:
    """Build a StreamChunk iterator from a synchronous string generator.

    Wraps a sync generator in an async iterator for compatibility with
    the async stream API. Ensures the sync generator is properly closed
    via gen.close() when the async iteration exits.

    Uses explicit next() iteration instead of for loop to ensure
    synchronous cleanup when the generator exits.

    Usage::

        def files_lines():
            for line in open("file.txt", encoding="utf-8"):
                yield line

        stream = stream_from_sync_gen(files_lines())
        async for chunk in stream:
            print(chunk.data, end="")

    Note:
        The sync generator is closed via gen.close() when iteration exits,
        whether through normal completion, break, or exception.
    """
    seq = 0

    async def _aiter() -> AsyncGenerator[StreamChunk, None]:
        nonlocal seq
        try:
            while True:
                try:
                    text = next(gen)
                    yield StreamChunk(data=text, sequence=seq, is_final=False)
                    seq += 1
                except StopIteration:
                    yield StreamChunk(data="", sequence=seq, is_final=True)
                    break
        except (RuntimeError, ValueError) as exc:
            yield StreamChunk(
                data=f"[stream error: {exc}]",
                sequence=seq,
                is_final=True,
                metadata={"error": str(exc)},
            )
        finally:
            # Ensure the sync generator is properly closed.
            # This handles early break, exception, and cancellation cases.
            # gen.close() causes a GeneratorExit to be thrown into the
            # generator, allowing it to clean up any resources.
            gen.close()

    return _aiter()
