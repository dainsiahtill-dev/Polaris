"""Polaris AI Platform - Stream Module

Public exports for stream executor components.

This module provides unified streaming LLM invocation capability:
- StreamExecutor: Main streaming executor class
- StreamConfig: Immutable configuration
- StreamState: State machine enum
- LLMStreamResult: Result validation dataclass
"""

from __future__ import annotations

from polaris.kernelone.llm.engine.contracts import StreamEventType
from polaris.kernelone.llm.providers import get_provider_manager
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

from .config import (
    LLMStreamResult,
    StreamConfig,
    StreamState,
    validate_stream_result,
)
from .event_streamer import EventStreamer, SerializationFormat, infer_channel
from .executor import (
    StreamExecutor,
    _stream_with_overall_timeout,
    stream_to_response,
)

__all__ = [
    "EventStreamer",
    "LLMStreamResult",
    "SerializationFormat",
    "StreamConfig",
    # Core classes
    "StreamEventType",
    "StreamExecutor",
    "StreamState",
    "_stream_with_overall_timeout",
    "emit_debug_event",
    "get_provider_manager",
    "infer_channel",
    "stream_to_response",
    "validate_stream_result",
]
