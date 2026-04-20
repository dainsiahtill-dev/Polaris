"""In-memory WebSocket session manager for KernelOne ws/ subsystem.

Provides session tracking, message routing, and broadcast capabilities.
Suitable for single-process scenarios. For multi-process deployments,
replace with RedisSessionManager backed by a Redis pub/sub adapter.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text operations use encoding="utf-8"
- Async-first: all operations are async
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.utils.time_utils import utc_now as _utc_now

from .ports import ConnectionState, SessionId, WsMessage, WsSession, WsSessionPort

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class InMemorySessionManager(WsSessionPort):
    """In-process WebSocket session tracker.

    Stores session metadata in memory. Does not hold open connections —
    use a WebSocket library (Starlette, FastAPI, aiohttp) for that.
    This manager tracks which sessions exist and provides broadcast routing.

    Usage::

        manager = InMemorySessionManager()

        # Create a session when a client connects
        session = await manager.create_session(
            session_id="sess-abc123",
            metadata={"user_id": "user-1"},
        )

        # Update when a message arrives
        await manager.update_session(
            "sess-abc123",
            message_count=session.message_count + 1,
            last_message_at=_utc_now(),
        )

        # Broadcast to all sessions
        await manager.broadcast("server event", metadata={"type": "ping"})

        # Remove when disconnected
        await manager.remove_session("sess-abc123")
    """

    def __init__(self) -> None:
        self._sessions: dict[SessionId, WsSession] = {}
        self._handlers: dict[str, list[Callable[[WsMessage], Any]]] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        session_id: SessionId,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> WsSession:
        if not session_id:
            raise ValueError("session_id must be non-empty")
        async with self._lock:
            if session_id in self._sessions:
                raise ValueError(f"session {session_id!r} already exists")
            session = WsSession(
                session_id=session_id,
                state=ConnectionState.CONNECTING,
                created_at=_utc_now(),
                metadata=dict(metadata) if metadata else {},
            )
            self._sessions[session_id] = session
            logger.debug("Session created: %s", session_id)
            return session

    async def get_session(self, session_id: SessionId) -> WsSession | None:
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[WsSession]:
        return list(self._sessions.values())

    async def update_session(
        self,
        session_id: SessionId,
        **updates: Any,
    ) -> WsSession | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if "state" in updates:
                session.state = ConnectionState(updates["state"])
            if "message_count" in updates:
                session.message_count = int(updates["message_count"])  # type: ignore[assignment]
            if "last_message_at" in updates:
                session.last_message_at = updates["last_message_at"]
            if "peer_addr" in updates:
                session.peer_addr = str(updates["peer_addr"])  # type: ignore[assignment]
            if "metadata" in updates:
                session.metadata.update(dict(updates["metadata"]))  # type: ignore[union-attr]
            return session

    async def remove_session(self, session_id: SessionId) -> bool:
        async with self._lock:
            removed = session_id in self._sessions
            if removed:
                self._sessions.pop(session_id, None)
                logger.debug("Session removed: %s", session_id)
            return removed

    async def broadcast(
        self,
        message: str,
        *,
        session_ids: list[SessionId] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        targets = session_ids or [sid for sid, s in self._sessions.items() if s.state == ConnectionState.OPEN]
        sent = 0
        for sid in targets:
            session = self._sessions.get(sid)
            if session is not None and session.state == ConnectionState.OPEN:
                # Dispatch to registered type handlers
                await self._dispatch_to_handlers(
                    WsMessage(
                        session_id=sid,
                        type="broadcast",
                        payload=message,
                        metadata=dict(metadata) if metadata else {},
                    )
                )
                sent += 1
        logger.debug("Broadcast sent to %d sessions (targeted: %s)", sent, session_ids is not None)
        return sent

    def register_handler(
        self,
        event_type: str,
        handler: Callable[[WsMessage], Any],
    ) -> None:
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def _dispatch_to_handlers(self, message: WsMessage) -> None:
        handlers = self._handlers.get(message.type, [])
        for handler in handlers:
            try:
                result = handler(message)
                if asyncio.iscoroutine(result):
                    await result
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "Ws handler for type=%s raised: %s",
                    message.type,
                    exc,
                )

    async def dispatch_message(
        self,
        session_id: SessionId,
        payload: str,
        msg_type: str = "text",
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Dispatch a received message to registered handlers.

        Call this when a WebSocket message is received from a client.
        """
        if session_id not in self._sessions:
            logger.warning("Message for unknown session: %s", session_id)
            return
        message = WsMessage(
            session_id=session_id,
            type=msg_type,
            payload=payload,
            metadata=dict(metadata) if metadata else {},
        )
        # Update last_message_at
        await self.update_session(session_id, last_message_at=_utc_now())
        await self._dispatch_to_handlers(message)


__all__ = ["InMemorySessionManager"]
