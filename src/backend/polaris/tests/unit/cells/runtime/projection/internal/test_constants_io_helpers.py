"""Tests for polaris.cells.runtime.projection.internal.constants and io_helpers.

Covers constant re-exports, pure helper functions, and lazy-load proxies.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.runtime.projection.internal.constants import DEFAULT_WORKSPACE
from polaris.cells.runtime.projection.internal.io_helpers import (
    get_git_status,
    get_lancedb_status,
)


class TestDefaultWorkspace:
    """Tests for DEFAULT_WORKSPACE constant."""

    def test_is_string(self) -> None:
        assert isinstance(DEFAULT_WORKSPACE, str)

    def test_not_empty(self) -> None:
        assert DEFAULT_WORKSPACE


class TestGetGitStatus:
    """Tests for get_git_status."""

    def test_detects_git_present(self, tmp_path: Any) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        result = get_git_status(str(tmp_path))
        assert result["present"] is True
        assert result["root"] == str(tmp_path)

    def test_detects_git_file(self, tmp_path: Any) -> None:
        git_file = tmp_path / ".git"
        git_file.write_text("gitdir: /somewhere", encoding="utf-8")
        result = get_git_status(str(tmp_path))
        assert result["present"] is True

    def test_no_git(self, tmp_path: Any) -> None:
        result = get_git_status(str(tmp_path))
        assert result["present"] is False
        assert result["root"] == ""

    def test_empty_workspace(self) -> None:
        result = get_git_status("")
        assert result["present"] is False

    def test_returns_dict(self, tmp_path: Any) -> None:
        result = get_git_status(str(tmp_path))
        assert isinstance(result, dict)
        assert "present" in result
        assert "root" in result


class TestGetLancedbStatus:
    """Tests for get_lancedb_status."""

    def test_returns_dict(self) -> None:
        result = get_lancedb_status()
        assert isinstance(result, dict)
        assert "ok" in result
        assert "python" in result

    def test_structure(self) -> None:
        result = get_lancedb_status()
        assert "error" in result
        if result["ok"]:
            assert "version" in result
        else:
            assert result["error"] is not None or result["error"] == ""

    def test_python_path_set(self) -> None:
        result = get_lancedb_status()
        assert isinstance(result["python"], str)
        assert result["python"]

    @patch("polaris.cells.runtime.projection.internal.io_helpers.sys.executable", "/fake/python")
    def test_uses_sys_executable(self) -> None:
        result = get_lancedb_status()
        assert result["python"] == "/fake/python"


class TestReadFileHead:
    """Tests for read_file_head (re-export from file_io)."""

    @patch("polaris.cells.runtime.projection.internal.file_io.read_file_head")
    def test_proxy_called(self, mock_head: MagicMock) -> None:
        from polaris.cells.runtime.projection.internal.io_helpers import read_file_head

        mock_head.return_value = "head content"
        result = read_file_head("/path", 10)
        mock_head.assert_called_once_with("/path", 10)
        assert result == "head content"


class TestReadFileTail:
    """Tests for read_file_tail (re-export from file_io)."""

    @patch("polaris.cells.runtime.projection.internal.file_io.read_file_tail")
    def test_proxy_called(self, mock_tail: MagicMock) -> None:
        from polaris.cells.runtime.projection.internal.io_helpers import read_file_tail

        mock_tail.return_value = "tail content"
        result = read_file_tail("/path", 10)
        mock_tail.assert_called_once_with("/path", 10)
        assert result == "tail content"


class TestReadJson:
    """Tests for read_json (re-export from file_io)."""

    @patch("polaris.cells.runtime.projection.internal.file_io.read_json")
    def test_proxy_called(self, mock_read: MagicMock) -> None:
        from polaris.cells.runtime.projection.internal.io_helpers import read_json

        mock_read.return_value = {"key": "value"}
        result = read_json("/path")
        mock_read.assert_called_once_with("/path")
        assert result == {"key": "value"}


class TestFormatMtime:
    """Tests for format_mtime (re-export from file_io)."""

    @patch("polaris.cells.runtime.projection.internal.file_io.format_mtime")
    def test_proxy_called(self, mock_fmt: MagicMock) -> None:
        from polaris.cells.runtime.projection.internal.io_helpers import format_mtime

        mock_fmt.return_value = "2024-01-01"
        result = format_mtime(1700000000.0)
        mock_fmt.assert_called_once_with(1700000000.0)
        assert result == "2024-01-01"


class TestResolveArtifactPath:
    """Tests for resolve_artifact_path lazy proxy."""

    @patch("polaris.cells.runtime.artifact_store.public.service.resolve_artifact_path")
    def test_lazy_proxy(self, mock_resolve: MagicMock) -> None:
        from polaris.cells.runtime.projection.internal.io_helpers import resolve_artifact_path

        mock_resolve.return_value = "/resolved/path"
        result = resolve_artifact_path("/ws", "/cache", "rel")
        mock_resolve.assert_called_once_with("/ws", "/cache", "rel")
        assert result == "/resolved/path"


class TestSelectLatestArtifact:
    """Tests for select_latest_artifact lazy proxy."""

    @patch("polaris.cells.runtime.artifact_store.public.service.select_latest_artifact")
    def test_lazy_proxy(self, mock_select: MagicMock) -> None:
        from polaris.cells.runtime.projection.internal.io_helpers import select_latest_artifact

        mock_select.return_value = {"data": True}
        result = select_latest_artifact("/ws", "/cache", "rel")
        mock_select.assert_called_once_with("/ws", "/cache", "rel")
        assert result == {"data": True}


class TestBuildCacheRoot:
    """Tests for build_cache_root (re-export)."""

    @patch("polaris.cells.runtime.projection.internal.io_helpers.build_cache_root")
    def test_proxy(self, mock_build: MagicMock) -> None:
        from polaris.cells.runtime.projection.internal.io_helpers import build_cache_root

        mock_build.return_value = "/cache/root"
        result = build_cache_root("/ram", "/ws")
        mock_build.assert_called_once_with("/ram", "/ws")
        assert result == "/cache/root"


class TestReadIncremental:
    """Tests for read_incremental (re-export from file_io)."""

    @patch("polaris.cells.runtime.projection.internal.file_io.read_incremental")
    def test_proxy_called(self, mock_read: MagicMock) -> None:
        from polaris.cells.runtime.projection.internal.io_helpers import read_incremental

        mock_read.return_value = "incremental"
        result = read_incremental("/path", 0)
        mock_read.assert_called_once_with("/path", 0)
        assert result == "incremental"
