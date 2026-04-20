"""Public service contracts for `roles.session` cell.

This module provides the public API surface (Protocols and contracts) for roles.session cell.
Implementation classes are NOT exported from this module - use the internal module directly.
"""

from __future__ import annotations

# Re-export concrete data store for cross-cell dependency injection (kernel -> session)
from polaris.cells.roles.session.internal.data_store import RoleDataStore

# Re-export public protocols and contracts only
from polaris.cells.roles.session.public.contracts import (
    AttachmentMode,
    IRoleSessionContextMemoryService,
    IRoleSessionService,
    RoleHostKind,
    SessionState,
    SessionType,
)

__all__ = [
    "AttachmentMode",
    "IRoleSessionContextMemoryService",
    "IRoleSessionService",
    "RoleDataStore",
    "RoleHostKind",
    "SessionState",
    "SessionType",
]
