"""WebSocket port definitions for KernelOne ws/ subsystem.

These ports define the technical contracts for WebSocket session management,
independent of any specific transport or business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

# -----------------------------------------------------------------------------
# Identity & Types
# -----------------------------------------------------------------------------

SessionId = str


class ConnectionState(str, Enum):
    """Lifecycle state of a WebSocket connection."""

    CONNECTING = "connecting"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


@dataclass(frozen=True)
class WsMessage:
    """Immutable message sent over a WebSocket connection.

    All messages are JSON-serializable. Binary data is base64-encoded.
    """

    session_id: SessionId
    type: str  # e.g. "text", "binary", "ping", "pong", "error"
    payload: str  # text content or base64-encoded binary
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "type": self.type,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "metadata": dict(self.metadata),
        }


@dataclass
class WsSession:
    """Active WebSocket session state tracked by SessionManager."""

    session_id: SessionId
    state: ConnectionState = ConnectionState.CONNECTING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_message_at: datetime | None = None
    message_count: int = 0
    peer_addr: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "last_message_at": (self.last_message_at.isoformat() if self.last_message_at else None),
            "message_count": self.message_count,
            "peer_addr": self.peer_addr,
            "metadata": dict(self.metadata),
        }


# -----------------------------------------------------------------------------
# Ports (Protocols)
# -----------------------------------------------------------------------------


class ConnectionPort(Protocol):
    """Abstract interface for a single WebSocket connection.

    Implementations: AsgiConnectionAdapter (for FastAPI/Starlette),
    AiohttpConnectionAdapter, RawWebSocketAdapter.
    """

    @property
    def session_id(self) -> SessionId:
        """Unique identifier for this connection."""
        ...

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        ...

    async def send_text(self, text: str, *, metadata: dict[str, Any] | None = None) -> None:
        """Send a text message over the connection."""
        ...

    async def send_bytes(self, data: bytes, *, metadata: dict[str, Any] | None = None) -> None:
        """Send a binary message over the connection."""
        ...

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the connection with the given code and reason."""
        ...

    async def accept(self, subprotocol: str | None = None) -> None:
        """Accept the incoming connection upgrade."""
        ...


class WsSessionPort(Protocol):
    """Abstract interface for WebSocket session lifecycle management.

    Implementations: InMemorySessionManager (development),
    RedisSessionManager (production multi-process).
    """

    async def create_session(
        self,
        session_id: SessionId,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> WsSession:
        """Create and register a new session."""
        ...

    async def get_session(self, session_id: SessionId) -> WsSession | None:
        """Retrieve an existing session by ID."""
        ...

    async def list_sessions(self) -> list[WsSession]:
        """List all active sessions."""
        ...

    async def update_session(
        self,
        session_id: SessionId,
        **updates: Any,
    ) -> WsSession | None:
        """Update mutable session fields (state, message_count, etc.)."""
        ...

    async def remove_session(self, session_id: SessionId) -> bool:
        """Remove a session. Returns True if the session existed."""
        ...

    async def broadcast(
        self,
        message: str,
        *,
        session_ids: list[SessionId] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Broadcast a message.

        Args:
            message: Text content to send.
            session_ids: If provided, only send to these sessions.
                         If None, broadcast to all OPEN sessions.
            metadata: Optional metadata attached to each WsMessage.

        Returns:
            Number of sessions the message was sent to.
        """
        ...

    def register_handler(
        self,
        event_type: str,
        handler: Callable[[WsMessage], Any],
    ) -> None:
        """Register a handler for a specific message type.

        Args:
            event_type: Message type string (e.g. "chat", "tool_result").
            handler: Async callable receiving the WsMessage.
        """
        ...
