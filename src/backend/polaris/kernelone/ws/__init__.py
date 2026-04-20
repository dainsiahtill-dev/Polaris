"""KernelOne WebSocket session management subsystem.

Provides connection lifecycle management, session tracking, and WebSocket
protocol support for KernelOne agents and runtime services.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text operations use encoding="utf-8"
- Async-first: all I/O is async using asyncio
"""

from __future__ import annotations

from .ports import ConnectionPort, ConnectionState, SessionId, WsMessage, WsSessionPort
from .session_manager import InMemorySessionManager, WsSession

__all__ = [
    "ConnectionPort",
    "ConnectionState",
    "InMemorySessionManager",
    "SessionId",
    "WsMessage",
    "WsSession",
    "WsSessionPort",
]
