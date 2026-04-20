"""Polaris AI Platform - Stream Module

Public exports for stream executor components.

This module provides unified streaming LLM invocation capability:
- StreamExecutor: Main streaming executor class
- StreamConfig: Immutable configuration
- StreamState: State machine enum
- LLMStreamResult: Result validation dataclass (StreamResult is deprecated alias)
- BackpressureBuffer: Thread-safe buffer with backpressure control
"""

from __future__ import annotations

from polaris.kernelone.llm.providers import get_provider_manager
from polaris.kernelone.llm.shared_contracts import StreamEventType
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

from .backpressure import BackpressureBuffer

# Core exports
from .config import (
    _MAX_PENDING_TOOL_CALLS,
    _STREAM_TIMEOUT,
    _TOKEN_TIMEOUT,
    MAX_BUFFER_SIZE,
    LLMStreamResult,
    StreamConfig,
    StreamResult,  # Backward compatibility alias
    StreamState,
    get_stream_timeout,
    reset_stream_timeout,
    set_stream_timeout,
    validate_stream_result,
)
from .event_streamer import EventStreamer, SerializationFormat, infer_channel
from .executor import (
    StreamExecutor,
    _stream_with_overall_timeout,
    stream_to_response,
)
from .result_tracker import _StreamResultTracker

# Re-export utility functions from tool_accumulator for backward compatibility
from .tool_accumulator import (
    _debug_compact_payload,
    _debug_tool_arguments,
    _normalize_arguments,
    _provider_supports_structured_stream,
    _safe_text_length,
    _tool_accumulator_key,
    _ToolCallAccumulator,
)

__all__ = [
    "MAX_BUFFER_SIZE",
    "_MAX_PENDING_TOOL_CALLS",
    "_STREAM_TIMEOUT",
    "_TOKEN_TIMEOUT",
    "BackpressureBuffer",
    "EventStreamer",
    "LLMStreamResult",
    "SerializationFormat",
    "StreamConfig",
    # Core classes
    "StreamEventType",
    "StreamExecutor",
    "StreamResult",  # Backward compatibility alias
    "StreamState",
    # Result tracker (internal but exported for backward compatibility)
    "_StreamResultTracker",
    # Tool accumulator (internal but exported for backward compatibility)
    "_ToolCallAccumulator",
    "_debug_compact_payload",
    "_debug_tool_arguments",
    "_normalize_arguments",
    "_provider_supports_structured_stream",
    "_safe_text_length",
    "_stream_with_overall_timeout",
    "_tool_accumulator_key",
    "emit_debug_event",
    "get_provider_manager",
    "get_stream_timeout",
    "infer_channel",
    "reset_stream_timeout",
    "set_stream_timeout",
    "stream_to_response",
    "validate_stream_result",
]
