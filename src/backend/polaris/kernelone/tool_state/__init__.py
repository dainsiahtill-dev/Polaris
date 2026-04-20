"""Tool state management for KernelOne.

This module provides:
- ToolState: Complete state with sub-state tracking
- ToolCallTracker: Track multiple tool calls in flight
- ToolStateStatus: Core state enum
- ToolErrorKind: Error classification
- ToolLoopSafetyPolicy: Safety policy for tool loops
- TranscriptLog: Tool execution transcript management
- compact_result_payload: Result compaction utilities
"""

# Re-export compaction utilities
from polaris.kernelone.tool_state.compaction import (
    compact_result_payload,
    compact_value,
    promote_read_file_content,
    trim_text,
)

# Re-export safety policy
from polaris.kernelone.tool_state.safety import (
    _DEFAULT_CONTEXT_WINDOW_TOKENS,
    _MAX_READ_FILE_CONTENT_CHARS,
    _MAX_RESULT_DEPTH,
    _MAX_RESULT_ERROR_CHARS,
    _MAX_RESULT_LIST_ITEMS,
    _MAX_RESULT_OBJECT_KEYS,
    # Configuration constants
    _MAX_RESULT_STRING_CHARS,
    _READ_FILE_PROMOTION_HEADROOM_RATIO,
    ToolLoopSafetyPolicy,
)
from polaris.kernelone.tool_state.state_machine import (
    InvalidToolStateTransitionError,
    ToolErrorKind,
    ToolPendingSubState,
    ToolRunningSubState,
    # Core class
    ToolState,
    # State types
    ToolStateStatus,
    # Factory
    create_tool_state,
)

# Re-export transcript management
from polaris.kernelone.tool_state.transcript import (
    ToolCallEntry,
    ToolResultEntry,
    ToolTranscriptEntry,
    TranscriptLog,
)

__all__ = [
    "_DEFAULT_CONTEXT_WINDOW_TOKENS",
    "_MAX_READ_FILE_CONTENT_CHARS",
    "_MAX_RESULT_DEPTH",
    "_MAX_RESULT_ERROR_CHARS",
    "_MAX_RESULT_LIST_ITEMS",
    "_MAX_RESULT_OBJECT_KEYS",
    # Configuration constants
    "_MAX_RESULT_STRING_CHARS",
    "_READ_FILE_PROMOTION_HEADROOM_RATIO",
    "InvalidToolStateTransitionError",
    "ToolCallEntry",
    "ToolErrorKind",
    # Safety policy
    "ToolLoopSafetyPolicy",
    "ToolPendingSubState",
    "ToolResultEntry",
    "ToolRunningSubState",
    # Core class
    "ToolState",
    # State types
    "ToolStateStatus",
    # Transcript management
    "ToolTranscriptEntry",
    "TranscriptLog",
    # Compaction utilities
    "compact_result_payload",
    "compact_value",
    # Factory
    "create_tool_state",
    "promote_read_file_content",
    "trim_text",
]
