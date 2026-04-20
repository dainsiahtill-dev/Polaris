"""KernelOne authentication context subsystem.

Provides AuthContext and SimpleAuthContext for propagating identity
and authorization information through the KernelOne runtime.

Design constraints:
- KernelOne-only: no Polaris business logic
- Identity is opaque to KernelOne: tokens, permissions, and session
  management are the responsibility of the application layer
- This module only carries auth metadata through the runtime stack
- No bare except: all errors caught with specific exception types

Usage::

    ctx = SimpleAuthContext(
        principal="director.agent",
        auth_token="hp_...",
        scopes=["fs:read", "llm:call"],
    )
    async with ctx.bind():
        # Auth metadata propagates via contextvars
        auth = AuthContext.current()
        assert auth.principal == "director.agent"
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Optional

from polaris.kernelone.utils.time_utils import utc_now as _utc_now

# -----------------------------------------------------------------------------
# Context variable storage
# -----------------------------------------------------------------------------

_current_auth: contextvars.ContextVar[SimpleAuthContext | None] = contextvars.ContextVar(
    "_kernelone_auth", default=None
)


# -----------------------------------------------------------------------------
# AuthContext protocol
# -----------------------------------------------------------------------------


class AuthContext:
    """Abstract auth context protocol.

    Implement this interface to provide auth context for custom identity
    systems (e.g. JWT validation, OAuth token introspection, LDAP).
    """

    @property
    def principal(self) -> str:
        raise NotImplementedError

    @property
    def scopes(self) -> frozenset[str]:
        raise NotImplementedError

    @property
    def expires_at(self) -> datetime | None:
        raise NotImplementedError

    @property
    def metadata(self) -> dict[str, Any]:
        raise NotImplementedError

    def has_scope(self, scope: str) -> bool:
        raise NotImplementedError

    def is_expired(self) -> bool:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Session-based auth methods (sensible defaults, no NotImplementedError)
    # -------------------------------------------------------------------------

    def validate_session(self, session_id: str) -> bool:
        """Check if session is valid and not expired.

        Args:
            session_id: The session identifier to validate.

        Returns:
            True if session exists and has not expired, False otherwise.
        """
        # Default: no session store available, return False for unknown sessions
        return False

    def get_current_user(self) -> dict[str, Any] | None:
        """Return user info from validated session.

        Returns:
            User dict with keys (id, username, roles, metadata) or None if
            no authenticated user in current context.
        """
        return None

    def check_permission(self, permission: str) -> bool:
        """Check if current user has specific permission.

        Args:
            permission: Permission string to check.

        Returns:
            True if user has the permission, False otherwise.
        """
        # Deny by default when no auth context
        return False

    def get_user_roles(self) -> list[str]:
        """Get roles for current user.

        Returns:
            List of role strings, empty list if no authenticated user.
        """
        return []

    def has_permission(self, permission: str) -> bool:
        """Check if current user has specific permission (alias for check_permission).

        Args:
            permission: Permission string to check.

        Returns:
            True if user has the permission, False otherwise.
        """
        return self.check_permission(permission)

    def get_auth_token(self) -> str:
        """Get the auth token for the current session.

        Returns:
            Auth token string, empty string if not authenticated.
        """
        return ""

    def refresh_token(self) -> str | None:
        """Refresh the auth token if possible.

        Returns:
            New token string if refresh succeeded, None otherwise.
        """
        return None

    @classmethod
    def current(cls) -> AuthContext | None:
        """Get the currently bound auth context, or None."""
        return _current_auth.get()  # type: ignore[return-value]

    def bind(self) -> contextvars.Token:
        """Bind this context to the current async execution scope."""
        raise NotImplementedError

    @classmethod
    def unbind(cls, token: contextvars.Token) -> None:
        """Unbind and restore previous context."""
        _current_auth.reset(token)


# -----------------------------------------------------------------------------
# Simple implementation (non-frozen dataclass with explicit freeze)
# -----------------------------------------------------------------------------


@dataclass
class SimpleAuthContext:
    """Auth context with simple in-memory scope checking.

    Uses a non-frozen dataclass with a _frozen flag to enforce immutability
    after __post_init__. This avoids frozen-dataclass field-ordering issues
    while still providing true immutability post-construction.

    For multi-node deployments, replace with a distributed auth context
    that validates tokens against your auth server.
    """

    principal: str
    auth_token: str = ""
    scopes: frozenset[str] = field(default_factory=frozenset)
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    _frozen: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.auth_token) > 4096:
            raise ValueError("auth_token must not exceed 4096 characters")
        # Freeze after construction to enforce immutability
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(f"SimpleAuthContext is immutable: cannot set '{name}'")
        object.__setattr__(self, name, value)

    @property
    def is_anonymous(self) -> bool:
        return not self.principal or self.principal == "anonymous"

    def has_scope(self, scope: str) -> bool:
        if self.is_anonymous:
            return False
        return scope in self.scopes or "*" in self.scopes

    def has_any_scope(self, required: list[str]) -> bool:
        return any(self.has_scope(s) for s in required)

    def has_all_scopes(self, required: list[str]) -> bool:
        return all(self.has_scope(s) for s in required)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return _utc_now() > self.expires_at

    # -------------------------------------------------------------------------
    # Session-based auth methods
    # -------------------------------------------------------------------------

    def validate_session(self, session_id: str) -> bool:
        """Check if session is valid and not expired.

        Args:
            session_id: The session identifier to validate.

        Returns:
            True if session exists, auth_token matches, and has not expired.
        """
        if self.is_anonymous:
            return False
        # Simple validation: session_id must match auth_token if set
        if self.auth_token and session_id != self.auth_token:
            return False
        return not self.is_expired()

    def get_current_user(self) -> dict[str, Any] | None:
        """Return user info from the current auth context.

        Returns:
            User dict with keys (id, username, roles, scopes, metadata)
            or None if anonymous.
        """
        if self.is_anonymous:
            return None
        return {
            "id": self.principal,
            "username": self.principal,
            "roles": self.get_user_roles(),
            "scopes": sorted(self.scopes),
            "metadata": dict(self.metadata),
        }

    def check_permission(self, permission: str) -> bool:
        """Check if current user has specific permission.

        Args:
            permission: Permission string to check.

        Returns:
            True if user has the permission via scope or is admin.
        """
        if self.is_anonymous:
            return False
        return self.has_scope(permission)

    def get_user_roles(self) -> list[str]:
        """Get roles for current user.

        Returns:
            List of role strings extracted from scopes or metadata,
            empty list if anonymous or no roles defined.
        """
        if self.is_anonymous:
            return []
        # Extract roles from metadata first
        roles = self.metadata.get("roles", [])
        if isinstance(roles, list) and roles:
            return roles
        # Extract roles from scopes by taking the prefix before ':'
        # e.g., "kernelone:fs:read" -> "kernelone"
        role_prefixes: set[str] = set()
        for scope in self.scopes:
            if ":" in scope:
                role_prefixes.add(scope.split(":")[0])
        return sorted(role_prefixes)

    def get_auth_token(self) -> str:
        """Get the auth token for the current session."""
        return self.auth_token

    def refresh_token(self) -> str | None:
        """Refresh the auth token if possible.

        SimpleAuthContext does not support token refresh.
        """
        return None

    def with_scopes(self, *additional: str) -> SimpleAuthContext:
        """Return a new context with additional scopes."""
        return SimpleAuthContext(
            principal=self.principal,
            auth_token=self.auth_token,
            scopes=self.scopes | frozenset(additional),
            expires_at=self.expires_at,
            created_at=self.created_at,
            metadata=dict(self.metadata),
        )

    def without_scopes(self, *remove: str) -> SimpleAuthContext:
        """Return a new context with specified scopes removed."""
        return SimpleAuthContext(
            principal=self.principal,
            auth_token=self.auth_token,
            scopes=self.scopes - frozenset(remove),
            expires_at=self.expires_at,
            created_at=self.created_at,
            metadata=dict(self.metadata),
        )

    def bind(self) -> contextvars.Token:
        return _current_auth.set(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal": self.principal,
            "scopes": sorted(self.scopes),
            "expires_at": (self.expires_at.isoformat() if self.expires_at else None),
            "is_expired": self.is_expired(),
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }


# -----------------------------------------------------------------------------
# Scope definitions (KernelOne-level only)
# -----------------------------------------------------------------------------

KERNELONE_SCOPES = frozenset(
    [
        "kernelone:fs:read",
        "kernelone:fs:write",
        "kernelone:fs:delete",
        "kernelone:db:query",
        "kernelone:db:write",
        "kernelone:llm:call",
        "kernelone:subprocess:run",
        "kernelone:events:publish",
        "kernelone:audit:write",
        "kernelone:effect:declare",
        "kernelone:scheduler:manage",
    ]
)


class AnonymousAuthContext(SimpleAuthContext):
    """Pre-configured anonymous auth context.

    Used when no authentication is available. Denies all non-public operations.
    """

    def __init__(self) -> None:
        super().__init__(
            principal="anonymous",
            auth_token="",
            scopes=frozenset(),
            expires_at=None,
            created_at=_utc_now(),
            metadata={},
        )


def current() -> SimpleAuthContext | None:
    """Shorthand for AuthContext.current()."""
    return _current_auth.get()


__all__ = [
    "KERNELONE_SCOPES",
    "AnonymousAuthContext",
    "AuthContext",
    "SimpleAuthContext",
    "current",
]
