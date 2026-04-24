r"""Unified event type constants for KernelOne runtime events.

This module provides a single source of truth for all event type strings
to prevent fragmentation between "tool_call" (underscore) and "tool.call" (dot).

Emit Path Architecture (P1-030):
    There are 4 distinct emit paths in KernelOne, intentionally separated:

    1. emit_event() [io_events.py]
       - Purpose: JSONL persistent logging to files
       - Use case: Audit trail, durable storage
       - Layer: KernelFileSystem / JSONL

    2. MessageBus.publish() [message_bus.py]
       - Purpose: Async pub/sub actor communication
       - Use case: Actor messaging, cross-component async notifications
       - Layer: Infrastructure / Actor model

    3. EventRegistry.emit() [typed/registry.py]
       - Purpose: Typed event subscriptions with wildcard matching
       - Use case: Real-time event subscriptions, typed handlers
       - Layer: KernelOne / Typed events

    4. UEPEventPublisher.publish_stream_event() [uep_publisher.py]
       - Purpose: Unified Event Pipeline - dual-write to EventRegistry + MessageBus
       - Use case: Runtime events, stream chunks, LLM lifecycle
       - Layer: Integration layer (TypedEventBusAdapter bridge)

    Coordination:
    - TypedEventBusAdapter.emit_to_both() bridges EventRegistry and MessageBus
    - UEPEventPublisher uses TypedEventBusAdapter for dual-write
    - All paths should use constants from this module for event type strings

CRITICAL: All text I/O must use UTF-8 encoding explicitly.

Example:
    >>> from polaris.kernelone.events.constants import EVENT_TYPE_TOOL_CALL
    >>> event_type = EVENT_TYPE_TOOL_CALL  # "tool_call"
"""

from __future__ import annotations

# =============================================================================
# Unified Event Type Constants (Single Source of Truth)
# =============================================================================

# Tool lifecycle events
EVENT_TYPE_TOOL_CALL = "tool_call"
"""Tool call event type (underscore notation for Python convention consistency)."""

EVENT_TYPE_TOOL_RESULT = "tool_result"
"""Tool result event type (underscore notation for Python convention consistency)."""

EVENT_TYPE_TOOL_ERROR = "tool_error"
"""Tool error event type."""

EVENT_TYPE_TOOL_START = "tool_start"
"""Tool execution start event type."""

EVENT_TYPE_TOOL_END = "tool_end"
"""Tool execution end event type."""

# Message/Content events
EVENT_TYPE_CONTENT_CHUNK = "content_chunk"
"""Content chunk event type (streaming output)."""

EVENT_TYPE_THINKING_CHUNK = "thinking_chunk"
"""Thinking/thought chunk event type (internal reasoning)."""

EVENT_TYPE_COMPLETE = "complete"
"""Completion event type (end of response)."""

# LLM events
EVENT_TYPE_LLM_START = "llm_start"
"""LLM invocation start event type."""

EVENT_TYPE_LLM_END = "llm_end"
"""LLM invocation end event type."""

EVENT_TYPE_LLM_ERROR = "llm_error"
"""LLM invocation error event type."""

# LLM call lifecycle events (roles/kernel internal events compatibility)
# These are used by cells/roles/kernel/internal/events.py and related subsystems
EVENT_TYPE_LLM_CALL_START = "llm_call_start"
"""LLM call start event type (roles/kernel compatibility alias)."""

EVENT_TYPE_LLM_CALL_END = "llm_call_end"
"""LLM call end event type (roles/kernel compatibility alias)."""

EVENT_TYPE_LLM_RETRY = "llm_retry"
"""LLM call retry event type."""

# LLM realtime observer event types
# These are used by LLMRealtimeEventBridge implementations
EVENT_TYPE_LLM_WAITING = "llm_waiting"
"""LLM waiting for response event type."""

EVENT_TYPE_LLM_COMPLETED = "llm_completed"
"""LLM response completed event type."""

EVENT_TYPE_LLM_FAILED = "llm_failed"
"""LLM invocation failed event type."""

# Session events
EVENT_TYPE_SESSION_START = "session_start"
"""Session start event type."""

EVENT_TYPE_SESSION_END = "session_end"
"""Session end event type."""

# Task events
EVENT_TYPE_TASK_CREATED = "task.created"
"""Task created event type (dot notation for task hierarchy)."""

EVENT_TYPE_TASK_UPDATED = "task.updated"
"""Task updated event type (dot notation for task hierarchy)."""

EVENT_TYPE_TASK_COMPLETED = "task.completed"
"""Task completed event type (dot notation for task hierarchy)."""

EVENT_TYPE_TASK_FAILED = "task.failed"
"""Task failed event type (dot notation for task hierarchy)."""

# Audit events
EVENT_TYPE_FINGERPRINT = "fingerprint"
"""Fingerprint event type for execution traces."""

EVENT_TYPE_STATE_SNAPSHOT = "state.snapshot"
"""State snapshot event type (dot notation for state hierarchy)."""

EVENT_TYPE_ERROR = "error"
"""Generic error event type."""


__all__ = [
    "EVENT_TYPE_COMPLETE",
    # Content events
    "EVENT_TYPE_CONTENT_CHUNK",
    "EVENT_TYPE_ERROR",
    # Audit events
    "EVENT_TYPE_FINGERPRINT",
    "EVENT_TYPE_LLM_CALL_END",
    # LLM call lifecycle events (roles/kernel compatibility)
    "EVENT_TYPE_LLM_CALL_START",
    "EVENT_TYPE_LLM_COMPLETED",
    "EVENT_TYPE_LLM_END",
    "EVENT_TYPE_LLM_ERROR",
    "EVENT_TYPE_LLM_FAILED",
    "EVENT_TYPE_LLM_RETRY",
    # LLM events
    "EVENT_TYPE_LLM_START",
    # LLM realtime observer event types
    "EVENT_TYPE_LLM_WAITING",
    "EVENT_TYPE_SESSION_END",
    # Session events
    "EVENT_TYPE_SESSION_START",
    "EVENT_TYPE_STATE_SNAPSHOT",
    "EVENT_TYPE_TASK_COMPLETED",
    # Task events (dot notation for hierarchy)
    "EVENT_TYPE_TASK_CREATED",
    "EVENT_TYPE_TASK_FAILED",
    "EVENT_TYPE_TASK_UPDATED",
    "EVENT_TYPE_THINKING_CHUNK",
    # Core tool event types
    "EVENT_TYPE_TOOL_CALL",
    "EVENT_TYPE_TOOL_END",
    "EVENT_TYPE_TOOL_ERROR",
    "EVENT_TYPE_TOOL_RESULT",
    "EVENT_TYPE_TOOL_START",
]
