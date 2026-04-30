"""Tests for `polaris.cells.policy.workspace_guard.internal.guard_service`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.policy.workspace_guard.internal.guard_service import (
    GuardCheckResult,
    WorkspaceGuardService,
)


class TestWorkspaceGuardServiceInit:
    """Test suite for WorkspaceGuardService initialization."""

    def test_init_default(self) -> None:
        """Service initializes with default None self-upgrade mode."""
        service = WorkspaceGuardService()
        assert service._self_upgrade_mode is None

    def test_init_with_self_upgrade_mode_true(self) -> None:
        """Service initializes with explicit True self-upgrade mode."""
        service = WorkspaceGuardService(self_upgrade_mode=True)
        assert service._self_upgrade_mode is True

    def test_init_with_self_upgrade_mode_false(self) -> None:
        """Service initializes with explicit False self-upgrade mode."""
        service = WorkspaceGuardService(self_upgrade_mode=False)
        assert service._self_upgrade_mode is False

    def test_init_with_self_upgrade_mode_string(self) -> None:
        """Service accepts string values for self-upgrade mode."""
        service = WorkspaceGuardService(self_upgrade_mode="1")
        assert service._self_upgrade_mode == "1"


class TestMetaProjectRoot:
    """Test suite for meta_project_root property."""

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.get_meta_project_root"
    )
    def test_meta_project_root_returns_path(self, mock_get_root: MagicMock) -> None:
        """Property delegates to get_meta_project_root and returns Path."""
        expected = Path("/fake/polaris/root")
        mock_get_root.return_value = expected
        service = WorkspaceGuardService()
        result = service.meta_project_root
        assert result == expected
        mock_get_root.assert_called_once()


class TestCheckWriteAllowed:
    """Test suite for check_write_allowed method."""

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.ensure_workspace_target_allowed"
    )
    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.resolve_workspace_target"
    )
    def test_check_write_allowed_valid_path(
        self,
        mock_resolve: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        """Allowed path returns GuardCheckResult with allowed=True."""
        resolved = Path("/workspace/project")
        mock_ensure.return_value = resolved
        mock_resolve.return_value = resolved
        service = WorkspaceGuardService()
        result = service.check_write_allowed("/workspace/project")
        assert result.allowed is True
        assert result.reason == "allowed"
        assert result.resolved_path == resolved
        mock_ensure.assert_called_once_with(
            "/workspace/project",
            self_upgrade_mode=None,
        )

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.ensure_workspace_target_allowed"
    )
    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.resolve_workspace_target"
    )
    def test_check_write_allowed_meta_project_blocked(
        self,
        mock_resolve: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        """Blocked path returns GuardCheckResult with allowed=False."""
        resolved = Path("/polaris/meta")
        mock_ensure.side_effect = ValueError("meta-project guard violation")
        mock_resolve.return_value = resolved
        service = WorkspaceGuardService()
        result = service.check_write_allowed("/polaris/meta")
        assert result.allowed is False
        assert "meta-project guard violation" in result.reason
        assert result.resolved_path == resolved

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.ensure_workspace_target_allowed"
    )
    def test_check_write_allowed_with_self_upgrade_mode(
        self,
        mock_ensure: MagicMock,
    ) -> None:
        """Self-upgrade mode is forwarded to ensure_workspace_target_allowed."""
        resolved = Path("/polaris/meta")
        mock_ensure.return_value = resolved
        service = WorkspaceGuardService(self_upgrade_mode=True)
        service.check_write_allowed("/polaris/meta")
        mock_ensure.assert_called_once_with(
            "/polaris/meta",
            self_upgrade_mode=True,
        )

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.ensure_workspace_target_allowed"
    )
    def test_check_write_allowed_path_object_input(
        self,
        mock_ensure: MagicMock,
    ) -> None:
        """Method accepts Path objects as input."""
        path_input = Path("/workspace/project")
        mock_ensure.return_value = path_input
        service = WorkspaceGuardService()
        result = service.check_write_allowed(path_input)
        assert result.allowed is True
        mock_ensure.assert_called_once_with(
            path_input,
            self_upgrade_mode=None,
        )

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.ensure_workspace_target_allowed"
    )
    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.resolve_workspace_target"
    )
    def test_check_write_allowed_nonexistent_path(
        self,
        mock_resolve: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        """Nonexistent path still resolves and checks."""
        resolved = Path("/nonexistent/path")
        mock_ensure.return_value = resolved
        mock_resolve.return_value = resolved
        service = WorkspaceGuardService()
        result = service.check_write_allowed("/nonexistent/path")
        assert result.allowed is True
        assert result.resolved_path == resolved


class TestCheckArchiveWriteAllowed:
    """Test suite for check_archive_write_allowed method."""

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.resolve_workspace_target"
    )
    def test_check_archive_write_allowed_no_history_keyword_delegates(
        self,
        mock_resolve: MagicMock,
    ) -> None:
        """Path without 'history' keyword delegates to check_write_allowed."""
        resolved = Path("/workspace") / "other" / "run-001"
        mock_resolve.return_value = resolved
        with patch.object(
            WorkspaceGuardService,
            "check_write_allowed",
            return_value=GuardCheckResult(
                allowed=True,
                reason="allowed",
                resolved_path=resolved,
            ),
        ) as mock_check:
            service = WorkspaceGuardService()
            result = service.check_archive_write_allowed(str(resolved))
            assert result.allowed is True
            mock_check.assert_called_once()

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.resolve_workspace_target"
    )
    def test_check_archive_write_allowed_outside_history_namespace_blocked(
        self,
        mock_resolve: MagicMock,
    ) -> None:
        """Path with 'history' but outside workspace/history namespace is blocked."""
        resolved = Path("/workspace") / "history-bad" / "run-001"
        mock_resolve.return_value = resolved
        service = WorkspaceGuardService()
        result = service.check_archive_write_allowed(str(resolved))
        assert result.allowed is False
        assert "outside the allowed workspace/history/* namespace" in result.reason
        assert result.resolved_path == resolved

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.resolve_workspace_target"
    )
    def test_check_archive_write_allowed_nested_history_blocked(
        self,
        mock_resolve: MagicMock,
    ) -> None:
        """Deeply nested history path outside workspace/history is blocked."""
        resolved = Path("/workspace") / "deep" / "history" / "data"
        mock_resolve.return_value = resolved
        service = WorkspaceGuardService()
        result = service.check_archive_write_allowed(str(resolved))
        assert result.allowed is False
        assert "outside the allowed workspace/history/* namespace" in result.reason

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.resolve_workspace_target"
    )
    def test_check_archive_write_allowed_with_history_in_name_blocked(
        self,
        mock_resolve: MagicMock,
    ) -> None:
        """Path containing 'history' keyword but not in workspace/history blocked."""
        resolved = Path("/my-history-folder") / "data"
        mock_resolve.return_value = resolved
        service = WorkspaceGuardService()
        result = service.check_archive_write_allowed(str(resolved))
        assert result.allowed is False
        assert "outside the allowed workspace/history/* namespace" in result.reason

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.ensure_workspace_target_allowed"
    )
    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.resolve_workspace_target"
    )
    def test_check_archive_write_allowed_forwards_self_upgrade_mode(
        self,
        mock_resolve: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        """Self-upgrade mode is forwarded via check_write_allowed delegation."""
        resolved = Path("/workspace") / "other" / "run-001"
        mock_ensure.return_value = resolved
        mock_resolve.return_value = resolved
        service = WorkspaceGuardService(self_upgrade_mode=True)
        result = service.check_archive_write_allowed(str(resolved))
        assert result.allowed is True
        assert result.reason == "allowed"
        mock_ensure.assert_called_once_with(
            str(resolved),
            self_upgrade_mode=True,
        )


class TestIsMetaProjectWorkspace:
    """Test suite for is_meta_project_workspace method."""

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.is_meta_project_target"
    )
    def test_is_meta_project_workspace_true(self, mock_is_meta: MagicMock) -> None:
        """Returns True when workspace is the meta-project."""
        mock_is_meta.return_value = True
        service = WorkspaceGuardService()
        result = service.is_meta_project_workspace("/polaris")
        assert result is True
        mock_is_meta.assert_called_once_with("/polaris")

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.is_meta_project_target"
    )
    def test_is_meta_project_workspace_false(self, mock_is_meta: MagicMock) -> None:
        """Returns False when workspace is not the meta-project."""
        mock_is_meta.return_value = False
        service = WorkspaceGuardService()
        result = service.is_meta_project_workspace("/other/project")
        assert result is False

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.is_meta_project_target"
    )
    def test_is_meta_project_workspace_path_object(
        self,
        mock_is_meta: MagicMock,
    ) -> None:
        """Method accepts Path objects."""
        mock_is_meta.return_value = False
        service = WorkspaceGuardService()
        path_input = Path("/other/project")
        result = service.is_meta_project_workspace(path_input)
        assert result is False
        mock_is_meta.assert_called_once_with(path_input)


class TestGetGuardMessage:
    """Test suite for get_guard_message method."""

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.build_workspace_guard_message"
    )
    def test_get_guard_message_returns_string(
        self,
        mock_build: MagicMock,
    ) -> None:
        """Returns human-readable guard violation message."""
        expected_msg = "Guard violation: /workspace/target"
        mock_build.return_value = expected_msg
        service = WorkspaceGuardService()
        result = service.get_guard_message("/workspace/target")
        assert result == expected_msg
        mock_build.assert_called_once_with("/workspace/target")

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.build_workspace_guard_message"
    )
    def test_get_guard_message_with_path_object(
        self,
        mock_build: MagicMock,
    ) -> None:
        """Method accepts Path objects."""
        path_input = Path("/workspace/target")
        mock_build.return_value = "Guard violation"
        service = WorkspaceGuardService()
        service.get_guard_message(path_input)
        mock_build.assert_called_once_with(path_input)

    @patch(
        "polaris.cells.policy.workspace_guard.internal.guard_service.build_workspace_guard_message"
    )
    def test_get_guard_message_contains_env_var_reference(
        self,
        mock_build: MagicMock,
    ) -> None:
        """Message references self-upgrade environment variable."""
        mock_build.return_value = (
            "Enable self_upgrade_mode=true or set KERNELONE_SELF_UPGRADE_MODE=1"
        )
        service = WorkspaceGuardService()
        result = service.get_guard_message("/target")
        assert "KERNELONE_SELF_UPGRADE_MODE" in result


class TestGuardCheckResult:
    """Test suite for GuardCheckResult dataclass."""

    def test_guard_check_result_fields(self) -> None:
        """Dataclass stores allowed, reason, and resolved_path."""
        path = Path("/workspace/test")
        result = GuardCheckResult(
            allowed=True,
            reason="allowed",
            resolved_path=path,
        )
        assert result.allowed is True
        assert result.reason == "allowed"
        assert result.resolved_path == path

    def test_guard_check_result_immutable(self) -> None:
        """Frozen dataclass prevents mutation."""
        result = GuardCheckResult(
            allowed=False,
            reason="blocked",
            resolved_path=Path("/test"),
        )
        with pytest.raises(AttributeError):
            result.allowed = True  # type: ignore[misc]

    def test_guard_check_result_hashable(self) -> None:
        """Frozen dataclass can be used in sets and as dict keys."""
        result = GuardCheckResult(
            allowed=True,
            reason="allowed",
            resolved_path=Path("/test"),
        )
        assert hash(result) is not None
        assert result in {result}


class TestModuleExports:
    """Test suite for module-level exports."""

    def test_all_exports_defined(self) -> None:
        """__all__ contains expected public symbols."""
        from polaris.cells.policy.workspace_guard.internal import guard_service

        expected = [
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
        for name in expected:
            assert name in guard_service.__all__

    def test_self_upgrade_mode_env_constant(self) -> None:
        """SELF_UPGRADE_MODE_ENV has expected value."""
        from polaris.cells.policy.workspace_guard.internal.guard_service import (
            SELF_UPGRADE_MODE_ENV,
        )

        assert SELF_UPGRADE_MODE_ENV == "KERNELONE_SELF_UPGRADE_MODE"
