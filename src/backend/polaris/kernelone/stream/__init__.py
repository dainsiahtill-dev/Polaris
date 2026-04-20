"""KernelOne stream subsystem.

Provides async streaming primitives for LLM responses, agent events,
and other async data flows. Aligned with master_types.py StreamEvent types.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text operations use encoding="utf-8"
- Async-first: all iteration is async using AsyncIterator/AsyncGenerator
"""

from __future__ import annotations

from .ports import (
    AsyncByteStream,
    AsyncTextStream,
    StreamAdapter,
    StreamChunk as KStreamChunk,
    StreamObserver,
    observe_stream,
    stream_from_async_gen,
    stream_from_sync_gen,
)
from .sse_streamer import AsyncBackpressureBuffer, EventStreamer, SSEEvent

__all__ = [
    "AsyncBackpressureBuffer",
    "AsyncByteStream",
    "AsyncTextStream",
    "EventStreamer",
    "KStreamChunk",
    "SSEEvent",
    "StreamAdapter",
    "StreamObserver",
    "observe_stream",
    "stream_from_async_gen",
    "stream_from_sync_gen",
]
