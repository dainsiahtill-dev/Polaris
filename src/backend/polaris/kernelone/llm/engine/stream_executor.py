"""Polaris AI Platform - Stream Executor Facade

This file serves as a backward-compatible import redirect.
All implementations have been refactored into the stream/ subdirectory.

Usage:
    # Old imports (still work via this facade):
    from polaris.kernelone.llm.engine.stream_executor import StreamExecutor
    from polaris.kernelone.llm.engine.stream_executor import StreamConfig, StreamState

    # New imports (recommended):
    from polaris.kernelone.llm.engine.stream import StreamExecutor, StreamConfig
"""

from __future__ import annotations

# Redirect all imports to the stream module
from polaris.kernelone.llm.engine.stream import (
    _MAX_PENDING_TOOL_CALLS,
    _STREAM_TIMEOUT,
    _TOKEN_TIMEOUT,
    MAX_BUFFER_SIZE,
    BackpressureBuffer,
    StreamConfig,
    StreamEventType,
    # Core classes
    StreamExecutor,
    StreamResult,
    StreamState,
    _debug_compact_payload,
    _debug_tool_arguments,
    _normalize_arguments,
    _provider_supports_structured_stream,
    _safe_text_length,
    _stream_with_overall_timeout,
    # Internal classes (exported for backward compatibility)
    _StreamResultTracker,
    _tool_accumulator_key,
    _ToolCallAccumulator,
    # Test entry points
    emit_debug_event,
    get_provider_manager,
    # Backward compatibility globals
    get_stream_timeout,
    reset_stream_timeout,
    set_stream_timeout,
    # Utility functions
    stream_to_response,
    # Validation functions
    validate_stream_result,
)

__all__ = [
    "MAX_BUFFER_SIZE",
    "_MAX_PENDING_TOOL_CALLS",
    "_STREAM_TIMEOUT",
    "_TOKEN_TIMEOUT",
    "BackpressureBuffer",
    "StreamConfig",
    "StreamEventType",
    "StreamExecutor",
    "StreamResult",
    "StreamState",
    "_StreamResultTracker",
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
    "reset_stream_timeout",
    "set_stream_timeout",
    "stream_to_response",
    "validate_stream_result",
]
