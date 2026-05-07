"""Request/response models and type definitions for runtime WebSocket endpoint.

This module contains:
- WebSocketSendError: Custom exception for WebSocket send errors
- Type aliases and constants for channel configuration
"""

from __future__ import annotations


class WebSocketSendError(Exception):
    """WebSocket send error with categorization."""

    def __init__(
        self,
        error_type: str,
        message: str,
        original_error: Exception | None = None,
    ) -> None:
        """Initialize WebSocketSendError.

        Args:
            error_type: Category of the error (e.g., 'serialization_error', 'connection_reset')
            message: Human-readable error message
            original_error: The underlying exception that caused this error
        """
        self.error_type = error_type
        self.message = message
        self.original_error = original_error
        super().__init__(message)


# =============================================================================
# Channel Configuration Constants
# =============================================================================

LEGACY_LLM_CHANNELS: set[str] = {"pm_llm", "director_llm"}
JOURNAL_CHANNELS: set[str] = {"system", "process", "llm"}

# v2 Protocol Channel Mapping (logical channel -> JetStream subject)
# Maps logical channel names to JetStream subjects.
# Format: hp.runtime.<workspace_key>.<category>.<channel>
V2_CHANNEL_TO_SUBJECT: dict[str, str] = {
    "log.system": "log.system",
    "log.process": "log.process",
    "log.llm": "log.llm",
    "event.file_edit": "event.file_edit",
    "event.task_trace": "event.task_trace",
    "status.snapshot": "status.snapshot",
}

RUNTIME_EVENT_SCHEMA_VERSION = "runtime.v2"
RUNTIME_EVENT_PROTOCOL_VERSION = 2


__all__ = [
    "JOURNAL_CHANNELS",
    "LEGACY_LLM_CHANNELS",
    "RUNTIME_EVENT_PROTOCOL_VERSION",
    "RUNTIME_EVENT_SCHEMA_VERSION",
    "V2_CHANNEL_TO_SUBJECT",
    "WebSocketSendError",
]
