"""Internal guard service for `policy.workspace_guard` cell.

Internal implementation of workspace write guard checks.
Exposed through public/service.py which re-exports this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.cells.policy.workspace_guard.service import (
    SELF_UPGRADE_MODE_ENV,
    build_workspace_guard_message,
    ensure_workspace_target_allowed,
    get_meta_project_root,
    is_meta_project_target,
    resolve_workspace_target,
    self_upgrade_mode_enabled,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class GuardCheckResult:
    """Result of a workspace guard check."""

    allowed: bool
    reason: str
    resolved_path: Path


class WorkspaceGuardService:
    """Internal service for workspace guard operations.

    Provides the core guard logic used by public/service.py.
    """

    def __init__(
        self,
        *,
        self_upgrade_mode: Any = None,
    ) -> None:
        self._self_upgrade_mode = self_upgrade_mode

    @property
    def meta_project_root(self) -> Path:
        """Return the Polaris meta-project root."""
        return get_meta_project_root()

    def check_write_allowed(self, path: str | Path) -> GuardCheckResult:
        """Check if a write to the given path is allowed.

        Args:
            path: Target workspace path

        Returns:
            GuardCheckResult with allowed status and reason
        """
        try:
            resolved = ensure_workspace_target_allowed(
                path,
                self_upgrade_mode=self._self_upgrade_mode,
            )
            return GuardCheckResult(
                allowed=True,
                reason="allowed",
                resolved_path=resolved,
            )
        except ValueError as exc:
            return GuardCheckResult(
                allowed=False,
                reason=str(exc),
                resolved_path=resolve_workspace_target(path),
            )

    def check_archive_write_allowed(self, path: str | Path) -> GuardCheckResult:
        """Check if an archive write to the given path is allowed.

        Archive writes have additional constraints on history paths.

        Args:
            path: Target archive path

        Returns:
            GuardCheckResult with allowed status and reason
        """
        resolved = resolve_workspace_target(path)
        resolved_str = str(resolved)

        if "history" in resolved_str and "workspace/history" not in resolved_str:
            return GuardCheckResult(
                allowed=False,
                reason=(f"Archive path '{resolved}' is outside the allowed workspace/history/* namespace"),
                resolved_path=resolved,
            )

        return self.check_write_allowed(path)

    def is_meta_project_workspace(self, workspace: str | Path) -> bool:
        """Check if the given workspace is the Polaris meta-project.

        Args:
            workspace: Workspace path to check

        Returns:
            True if workspace is the meta-project root
        """
        return is_meta_project_target(workspace)

    def get_guard_message(self, path: str | Path) -> str:
        """Build a human-readable guard violation message.

        Args:
            path: Path that triggered the violation

        Returns:
            Human-readable error message
        """
        return build_workspace_guard_message(path)


__all__ = [
    "SELF_UPGRADE_MODE_ENV",
    "GuardCheckResult",
    "WorkspaceGuardService",
    "build_workspace_guard_message",
    "ensure_workspace_target_allowed",
    "get_meta_project_root",
    "is_meta_project_target",
    "resolve_workspace_target",
    "self_upgrade_mode_enabled",
]
