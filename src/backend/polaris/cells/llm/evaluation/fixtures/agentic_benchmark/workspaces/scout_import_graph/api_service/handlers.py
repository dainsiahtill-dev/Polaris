"""HTTP-style request handlers for the api service.

Imports both auth-service modules and the shared ``SessionContext`` DTO from
``core.contracts``. This is the highest-level consumer of the shared DTO.
"""

from __future__ import annotations

from auth_service.permissions import is_admin
from auth_service.tokens import build_session
from core.contracts import ServiceError, SessionContext
from storage.database import save_session


def login(user_id: str, secret: str) -> SessionContext:
    """Authenticate and persist a new session, returning the shared DTO."""
    session = build_session(user_id, secret, scopes=("read", "admin:write"))
    save_session(session)
    return session


def delete_resource(session: SessionContext, resource_id: str) -> dict[str, str] | ServiceError:
    """Admin-only handler that consults the auth service for the shared DTO."""
    if not is_admin(session):
        return ServiceError(code="forbidden", message="admin scope required")
    return {"deleted": resource_id, "by": session.user_id}
