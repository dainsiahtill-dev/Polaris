"""Runtime WebSocket endpoint modules.

This package contains the refactored components of the runtime WebSocket endpoint:
- models: Type definitions and constants
- helpers: Facade for utility functions
- channel_utils: Channel classification and path resolution
- signature_utils: Signature computation and tracking
- protocol_utils: v2 Protocol helpers
- json_utils: JSON parsing utilities
- stream: Stream sending functions
- protocol: v2 protocol handlers
- websocket_core: Core WebSocket endpoint
- websocket_loop: Main loop implementation
- client_message: Client message handling
"""

from polaris.delivery.ws.endpoints.models import (
    JOURNAL_CHANNELS,
    LEGACY_LLM_CHANNELS,
    V2_CHANNEL_TO_SUBJECT,
    WebSocketSendError,
)
from polaris.delivery.ws.endpoints.websocket_core import (
    STREAM_CHANNELS,
    runtime_websocket,
)

__all__ = [
    "JOURNAL_CHANNELS",
    "LEGACY_LLM_CHANNELS",
    "STREAM_CHANNELS",
    "V2_CHANNEL_TO_SUBJECT",
    # Models
    "WebSocketSendError",
    # Core
    "runtime_websocket",
]
