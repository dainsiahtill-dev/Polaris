"""Public service exports for `policy.workspace_guard` cell."""

from __future__ import annotations

from polaris.cells.policy.workspace_guard.public.contracts import (
    WorkspaceArchiveWriteGuardQueryV1,
    WorkspaceGuardDecisionV1,
    WorkspaceGuardError,
    WorkspaceGuardViolationEventV1,
    WorkspaceWriteGuardQueryV1,
)
from polaris.cells.policy.workspace_guard.service import (
    SELF_UPGRADE_MODE_ENV,
    build_workspace_guard_message,
    ensure_workspace_target_allowed,
    get_meta_project_root,
    is_meta_project_target,
    resolve_workspace_target,
    self_upgrade_mode_enabled,
)


def check_workspace_write_guard(query: WorkspaceWriteGuardQueryV1) -> WorkspaceGuardDecisionV1:
    """Evaluate a workspace write guard query through the public contract."""
    if not isinstance(query, WorkspaceWriteGuardQueryV1):
        raise TypeError("query must be a WorkspaceWriteGuardQueryV1")
    try:
        ensure_workspace_target_allowed(query.path)
    except ValueError as exc:
        return WorkspaceGuardDecisionV1(allowed=False, reason=str(exc))
    return WorkspaceGuardDecisionV1(allowed=True, reason="workspace target allowed")


__all__ = [
    "SELF_UPGRADE_MODE_ENV",
    "WorkspaceArchiveWriteGuardQueryV1",
    "WorkspaceGuardDecisionV1",
    "WorkspaceGuardError",
    "WorkspaceGuardViolationEventV1",
    "WorkspaceWriteGuardQueryV1",
    "build_workspace_guard_message",
    "check_workspace_write_guard",
    "ensure_workspace_target_allowed",
    "get_meta_project_root",
    "is_meta_project_target",
    "resolve_workspace_target",
    "self_upgrade_mode_enabled",
]
