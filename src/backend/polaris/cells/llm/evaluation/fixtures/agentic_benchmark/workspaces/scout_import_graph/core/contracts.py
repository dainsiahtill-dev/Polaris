"""Core shared contracts for the service mesh fixture.

This module defines the single shared DTO ``SessionContext`` that is imported
and consumed across the auth, api, and storage layers. It is the canonical
data-transfer object for a request-scoped session.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SessionContext:
    """Request-scoped session data shared across all service layers.

    Attributes:
        user_id: Stable identifier of the authenticated principal.
        token: Opaque bearer token minted by the auth service.
        scopes: Permission scopes granted to this session.
        tenant: Logical tenant the session belongs to.
    """

    user_id: str
    token: str
    scopes: tuple[str, ...] = field(default_factory=tuple)
    tenant: str = "default"

    def has_scope(self, scope: str) -> bool:
        """Return True if this session was granted ``scope``."""
        return scope in self.scopes


@dataclass(frozen=True)
class ServiceError:
    """Uniform error envelope returned by every service boundary."""

    code: str
    message: str
