"""In-memory session store for the storage layer.

Persists the shared ``SessionContext`` DTO imported from ``core.contracts``.
"""

from __future__ import annotations

from core.contracts import SessionContext

_SESSIONS: dict[str, SessionContext] = {}


def save_session(session: SessionContext) -> None:
    """Persist the shared ``SessionContext`` DTO keyed by token."""
    _SESSIONS[session.token] = session


def load_session(token: str) -> SessionContext | None:
    """Load a previously persisted ``SessionContext`` by token."""
    return _SESSIONS.get(token)
