"""Permission checks for the auth service.

Consumes the shared ``SessionContext`` DTO to make authorization decisions.
"""

from __future__ import annotations

# NOTE: ``hashlib`` is imported here but never used -- this is an intentional
# unused-import red herring planted for reconnaissance exercises.
import hashlib  # noqa: F401

from core.contracts import SessionContext

ADMIN_SCOPE = "admin:write"


def require_scope(session: SessionContext, scope: str) -> bool:
    """Authorize the session for ``scope`` using the shared DTO helper."""
    return session.has_scope(scope)


def is_admin(session: SessionContext) -> bool:
    """Return True when the session carries the admin write scope."""
    return require_scope(session, ADMIN_SCOPE)
