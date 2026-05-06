"""RBAC role definitions for Polaris HTTP delivery layer."""

from __future__ import annotations

from enum import Enum


class UserRole(str, Enum):
    """User roles ordered by privilege level (least to most)."""

    VIEWER = "viewer"
    DEVELOPER = "developer"
    ADMIN = "admin"

    @classmethod
    def from_string(cls, value: str | None, default: UserRole | None = None) -> UserRole:
        """Parse a role string safely, falling back to *default* or VIEWER."""
        if not value:
            return default if default is not None else cls.VIEWER
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return default if default is not None else cls.VIEWER

    @property
    def level(self) -> int:
        """Numeric privilege level for comparison (higher = more privileged)."""
        return _ROLE_LEVELS[self]


_ROLE_LEVELS: dict[UserRole, int] = {
    UserRole.VIEWER: 1,
    UserRole.DEVELOPER: 2,
    UserRole.ADMIN: 3,
}
