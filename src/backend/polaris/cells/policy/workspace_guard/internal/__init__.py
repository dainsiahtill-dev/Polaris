"""Internal implementations for `policy.workspace_guard` cell."""

from polaris.cells.policy.workspace_guard.internal.guard_service import (
    GuardCheckResult,
    WorkspaceGuardService,
)

__all__ = [
    "GuardCheckResult",
    "WorkspaceGuardService",
]
