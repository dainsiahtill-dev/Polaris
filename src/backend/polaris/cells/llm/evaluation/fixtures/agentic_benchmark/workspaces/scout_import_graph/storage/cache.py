"""Lightweight token cache for the storage layer.

Also consumes the shared ``SessionContext`` DTO from ``core.contracts`` to
provide a fast read path in front of ``database.py``.
"""

from __future__ import annotations

from core.contracts import SessionContext

_CACHE: dict[str, SessionContext] = {}


def cache_session(session: SessionContext) -> None:
    """Cache the shared ``SessionContext`` DTO by user id."""
    _CACHE[session.user_id] = session


def peek_session(user_id: str) -> SessionContext | None:
    """Return a cached ``SessionContext`` for ``user_id`` if present."""
    return _CACHE.get(user_id)
