"""Tests for polaris.cells.workspace.integrity.internal.fs_utils."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from polaris.cells.workspace.integrity.internal.fs_utils import (
    get_abs_path,
    normalize_rel_path,
    workspace_has_docs,
    workspace_status_path,
)


class TestWorkspaceStatusPath:
    def test_empty_workspace(self) -> None:
        assert workspace_status_path("") == ""

    def test_valid_workspace(self) -> None:
        result = workspace_status_path("/tmp/ws")
        assert "workspace_status.json" in result


class TestGetAbsPath:
    def test_absolute_path(self) -> None:
        assert get_abs_path("/ws", "/absolute/path") == "/absolute/path"

    def test_relative_path(self) -> None:
        result = get_abs_path("/ws", "relative/path")
        assert "ws" in result
        assert "relative" in result
        assert "path" in result


class TestNormalizeRelPath:
    def test_backslash_to_slash(self) -> None:
        assert normalize_rel_path("foo\\bar") == "foo/bar"

    def test_leading_slash_stripped(self) -> None:
        assert normalize_rel_path("/foo/bar") == "foo/bar"

    def test_empty_string(self) -> None:
        assert normalize_rel_path("") == "."


class TestWorkspaceHasDocs:
    def test_empty_workspace(self) -> None:
        assert workspace_has_docs("") is False

    def test_with_docs_directory(self) -> None:
        with patch("os.path.isdir", return_value=True):
            assert workspace_has_docs("/tmp/ws") is True

    def test_without_docs_directory(self) -> None:
        with patch("os.path.isdir", return_value=False):
            assert workspace_has_docs("/tmp/ws") is False
