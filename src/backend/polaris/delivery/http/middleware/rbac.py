"""RBAC middleware and dependency factory for FastAPI routes.

Role authorization is based only on server-bound authentication context.
Client-supplied role headers are not identity facts and are ignored.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

from fastapi import HTTPException, Request
from polaris.delivery.http.auth.roles import UserRole
from polaris.kernelone.auth_context import SimpleAuthContext
from starlette.requests import Request as StarletteRequest
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_AUTHENTICATED_ROLE = UserRole.VIEWER


def _role_from_value(value: object) -> UserRole | None:
    """Parse a role only when it exactly matches a known server role."""
    if isinstance(value, UserRole):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    for role in UserRole:
        if role.value == normalized:
            return role
    return None


def _role_values(raw_roles: object) -> tuple[object, ...]:
    """Normalize trusted metadata role claims into an iterable tuple."""
    if raw_roles is None:
        return ()
    if isinstance(raw_roles, str):
        return (raw_roles,)
    if isinstance(raw_roles, Sequence) and not isinstance(raw_roles, (bytes, bytearray)):
        return tuple(cast(Sequence[object], raw_roles))
    return (raw_roles,)


def role_from_auth_context(auth_context: SimpleAuthContext | None) -> UserRole:
    """Resolve the effective role from server-bound auth metadata.

    The desktop bearer token currently authenticates the local backend client
    but does not prove admin or developer role membership. Missing or malformed
    role metadata therefore falls back to viewer.
    """
    if auth_context is None or auth_context.is_anonymous:
        return DEFAULT_AUTHENTICATED_ROLE

    metadata = auth_context.metadata
    raw_roles = _role_values(metadata.get("roles"))
    if not raw_roles:
        raw_roles = _role_values(metadata.get("role"))

    roles = [role for value in raw_roles if (role := _role_from_value(value)) is not None]
    if not roles:
        return DEFAULT_AUTHENTICATED_ROLE
    return max(roles, key=lambda role: role.level)


def extract_role_from_request(request: Request) -> UserRole:
    """Return the server-bound role for a request.

    This compatibility helper intentionally ignores ``X-User-Role`` and other
    client headers. It only reads the auth context attached by trusted server
    dependencies, then mirrors the result onto ``request.state.user_role`` for
    downstream observability.
    """
    auth_context = getattr(request.state, "auth_context", None)
    role = role_from_auth_context(auth_context if isinstance(auth_context, SimpleAuthContext) else None)
    request.state.user_role = role
    return role


class RBACMiddleware:
    """ASGI middleware that initializes a least-privileged role projection.

    Usage (FastAPI):
        app.add_middleware(RBACMiddleware)
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = StarletteRequest(scope, receive)
        request.state.user_role = DEFAULT_AUTHENTICATED_ROLE

        await self.app(scope, receive, send)


def require_role(allowed: Sequence[UserRole]) -> Callable[[Request], None]:
    """Dependency factory that enforces route-level role membership.

    Usage:
        @router.post(
            "/admin-only",
            dependencies=[Depends(require_role([UserRole.ADMIN]))],
        )
    """
    allowed_set = set(allowed)

    def checker(request: Request) -> None:
        auth_context = getattr(request.state, "auth_context", None)
        if not isinstance(auth_context, SimpleAuthContext):
            from polaris.delivery.http.dependencies import require_auth

            require_auth(request)

        role = extract_role_from_request(request)
        if role not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=f"role '{role.value}' not authorized for this resource",
            )

    return checker
