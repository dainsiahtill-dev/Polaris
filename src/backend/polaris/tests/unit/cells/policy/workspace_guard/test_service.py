"""Tests for polaris.cells.policy.workspace_guard.service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from polaris.cells.policy.workspace_guard.service import (
    SELF_UPGRADE_MODE_ENV,
    build_workspace_guard_message,
    ensure_workspace_target_allowed,
    get_meta_project_root,
    is_meta_project_target,
    resolve_workspace_target,
    self_upgrade_mode_enabled,
)


class TestCoerceBool:
    def test_true_values(self) -> None:
        assert self_upgrade_mode_enabled(True) is True
        assert self_upgrade_mode_enabled("1") is True
        assert self_upgrade_mode_enabled("true") is True
        assert self_upgrade_mode_enabled("yes") is True
        assert self_upgrade_mode_enabled("on") is True

    def test_false_values(self) -> None:
        assert self_upgrade_mode_enabled(False) is False
        assert self_upgrade_mode_enabled(None) is False
        assert self_upgrade_mode_enabled("0") is False
        assert self_upgrade_mode_enabled("false") is False
        assert self_upgrade_mode_enabled("") is False

    def test_env_var(self) -> None:
        with patch.dict("os.environ", {SELF_UPGRADE_MODE_ENV: "1"}):
            assert self_upgrade_mode_enabled() is True
        with patch.dict("os.environ", {SELF_UPGRADE_MODE_ENV: "0"}, clear=False):
            assert self_upgrade_mode_enabled() is False


class TestGetMetaProjectRoot:
    def test_returns_path(self) -> None:
        result = get_meta_project_root()
        assert isinstance(result, Path)
        assert result.is_absolute()


class TestResolveWorkspaceTarget:
    def test_resolves_string(self) -> None:
        result = resolve_workspace_target("/tmp/test")
        assert isinstance(result, Path)
        assert result.is_absolute()

    def test_resolves_path(self) -> None:
        result = resolve_workspace_target(Path("/tmp/test"))
        assert isinstance(result, Path)


class TestIsMetaProjectTarget:
    def test_meta_project_root_is_target(self) -> None:
        root = get_meta_project_root()
        assert is_meta_project_target(root) is True

    def test_child_of_meta_project(self) -> None:
        root = get_meta_project_root()
        child = root / "src"
        assert is_meta_project_target(child) is True

    def test_unrelated_path(self) -> None:
        assert is_meta_project_target("/tmp/unrelated") is False


class TestBuildWorkspaceGuardMessage:
    def test_contains_path_info(self) -> None:
        msg = build_workspace_guard_message("/tmp/test")
        assert "target workspace" in msg
        assert "Polaris meta-project root" in msg
        assert SELF_UPGRADE_MODE_ENV in msg


class TestEnsureWorkspaceTargetAllowed:
    def test_unrelated_path_allowed(self) -> None:
        result = ensure_workspace_target_allowed("/tmp/unrelated")
        assert isinstance(result, Path)

    def test_meta_project_blocked(self) -> None:
        root = get_meta_project_root()
        with pytest.raises(ValueError, match="meta-project"):
            ensure_workspace_target_allowed(root)

    def test_meta_project_allowed_in_upgrade_mode(self) -> None:
        root = get_meta_project_root()
        result = ensure_workspace_target_allowed(root, self_upgrade_mode=True)
        assert result == root.resolve()
