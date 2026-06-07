"""Public service exports for `policy.workspace_guard` cell."""

from __future__ import annotations

from polaris.cells.policy.workspace_guard.public.contracts import (
    WorkspaceArchiveWriteGuardQueryV1,
    WorkspaceGuardBatchDecisionV1,
    WorkspaceGuardDecisionV1,
    WorkspaceGuardError,
    WorkspaceGuardPathDecisionV1,
    WorkspaceGuardViolationEventV1,
    WorkspaceWriteGuardBatchQueryV1,
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


def check_workspace_write_guard_batch(query: WorkspaceWriteGuardBatchQueryV1) -> WorkspaceGuardBatchDecisionV1:
    """Evaluate multiple workspace write guard paths through one public query."""
    if not isinstance(query, WorkspaceWriteGuardBatchQueryV1):
        raise TypeError("query must be a WorkspaceWriteGuardBatchQueryV1")

    checked_paths = tuple(dict.fromkeys(query.paths))
    path_decisions: list[WorkspaceGuardPathDecisionV1] = []
    for path in checked_paths:
        try:
            ensure_workspace_target_allowed(path)
        except ValueError as exc:
            reason = str(exc)
            path_decisions.append(
                WorkspaceGuardPathDecisionV1(
                    path=path,
                    operation=query.operation,
                    allowed=False,
                    reason=reason,
                )
            )
            return WorkspaceGuardBatchDecisionV1(
                allowed=False,
                reason=reason,
                checked_paths=checked_paths,
                denied_path=path,
                path_decisions=tuple(path_decisions),
            )
        path_decisions.append(
            WorkspaceGuardPathDecisionV1(
                path=path,
                operation=query.operation,
                allowed=True,
                reason="workspace target allowed",
            )
        )
    return WorkspaceGuardBatchDecisionV1(
        allowed=True,
        reason="workspace target allowed",
        checked_paths=checked_paths,
        denied_path="",
        path_decisions=tuple(path_decisions),
    )


__all__ = [
    "SELF_UPGRADE_MODE_ENV",
    "WorkspaceArchiveWriteGuardQueryV1",
    "WorkspaceGuardBatchDecisionV1",
    "WorkspaceGuardDecisionV1",
    "WorkspaceGuardError",
    "WorkspaceGuardPathDecisionV1",
    "WorkspaceGuardViolationEventV1",
    "WorkspaceWriteGuardBatchQueryV1",
    "WorkspaceWriteGuardQueryV1",
    "build_workspace_guard_message",
    "check_workspace_write_guard",
    "check_workspace_write_guard_batch",
    "ensure_workspace_target_allowed",
    "get_meta_project_root",
    "is_meta_project_target",
    "resolve_workspace_target",
    "self_upgrade_mode_enabled",
]
