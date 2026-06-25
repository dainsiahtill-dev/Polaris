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


_DISCONNECT_RUNTIME_ERROR_MARKERS: tuple[str, ...] = (
    "websocket is not connected",
    'need to call "accept" first',
    'cannot call "receive" once a disconnect message has been received',
    'cannot call "send" once a close message has been sent',
    "unexpected asgi message 'websocket.send'",
    "after sending 'websocket.close'",
)


def is_websocket_disconnect_runtime_error(exc: BaseException) -> bool:
    """Return True when Starlette reports a closed WS as a RuntimeError.

    Starlette can surface normal client disconnect races as RuntimeError rather
    than WebSocketDisconnect when receive/send tasks resolve during shutdown.
    These should be audited as disconnects, not backend errors.
    """
    message = str(exc or "").lower()
    return any(marker in message for marker in _DISCONNECT_RUNTIME_ERROR_MARKERS)


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
    "status.resident": "status.resident",
    "status.instances": "status.instances",
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
    "is_websocket_disconnect_runtime_error",
]
