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

__all__ = [
    "SELF_UPGRADE_MODE_ENV",
    "WorkspaceArchiveWriteGuardQueryV1",
    "WorkspaceGuardDecisionV1",
    "WorkspaceGuardError",
    "WorkspaceGuardViolationEventV1",
    "WorkspaceWriteGuardQueryV1",
    "build_workspace_guard_message",
    "ensure_workspace_target_allowed",
    "get_meta_project_root",
    "is_meta_project_target",
    "resolve_workspace_target",
    "self_upgrade_mode_enabled",
]
