"""Public boundary for `roles.session` cell.

This module provides the public API surface for roles.session cell.
- Contracts and Protocols are exported from public.contracts and public.service
- Implementation classes are re-exported from internal for backward compatibility
"""

# Re-export implementation classes from internal for backward compatibility
# These are NOT part of the public contract - use Protocol types instead
from polaris.cells.roles.session.internal.artifact_service import (
    RoleSessionArtifactService,
)
from polaris.cells.roles.session.internal.context_memory_service import (
    RoleSessionContextMemoryService,
)
from polaris.cells.roles.session.internal.conversation import (
    Conversation,
    ConversationMessage,
    get_session_local,
)
from polaris.cells.roles.session.internal.data_store import (
    PathSecurityError,
    RoleDataStore,
    RoleDataStoreError,
)
from polaris.cells.roles.session.internal.role_session_service import (
    RoleSessionService,
)
from polaris.cells.roles.session.internal.session_attachment import (
    SessionAttachment,
)
from polaris.cells.roles.session.public.contracts import (
    AttachmentMode,
    AttachRoleSessionCommandV1,
    CreateRoleSessionCommandV1,
    GetRoleSessionStateQueryV1,
    IRoleSessionContextMemoryService,
    IRoleSessionService,
    ReadRoleSessionArtifactQueryV1,
    ReadRoleSessionEpisodeQueryV1,
    RoleHostKind,
    RoleSessionContextQueryResultV1,
    RoleSessionError,
    RoleSessionLifecycleEventV1,
    RoleSessionResultV1,
    SearchRoleSessionMemoryQueryV1,
    SessionState,
    SessionType,
    UpdateRoleSessionCommandV1,
)


def init_db() -> None:
    """Initialize the session database."""
    from polaris.cells.roles.session.internal.conversation import init_db as _init_db

    return _init_db()


def get_db():
    """Get a database session (for dependency injection)."""
    from polaris.cells.roles.session.internal.conversation import get_db as _get_db

    return _get_db()


__all__ = [
    "AttachRoleSessionCommandV1",
    "AttachmentMode",
    "Conversation",
    "ConversationMessage",
    "CreateRoleSessionCommandV1",
    "GetRoleSessionStateQueryV1",
    "IRoleSessionContextMemoryService",
    "IRoleSessionService",
    "PathSecurityError",
    "ReadRoleSessionArtifactQueryV1",
    "ReadRoleSessionEpisodeQueryV1",
    "RoleDataStore",
    "RoleDataStoreError",
    "RoleHostKind",
    "RoleSessionArtifactService",
    "RoleSessionContextMemoryService",
    "RoleSessionContextQueryResultV1",
    "RoleSessionError",
    "RoleSessionLifecycleEventV1",
    "RoleSessionResultV1",
    "RoleSessionService",
    "SearchRoleSessionMemoryQueryV1",
    "SessionAttachment",
    "SessionState",
    "SessionType",
    "UpdateRoleSessionCommandV1",
    "get_db",
    "get_session_local",
    "init_db",
]
