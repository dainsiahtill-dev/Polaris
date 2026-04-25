"""Tests for polaris.kernelone.db.policy."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.kernelone.db.errors import DatabasePathError, DatabasePolicyError
from polaris.kernelone.db.policy import (
    _expand_path,
    _is_within,
    is_managed_storage_path,
    managed_storage_roots,
    resolve_lancedb_path,
    resolve_sqlite_path,
)


class TestExpandPath:
    def test_expands_tilde(self) -> None:
        result = _expand_path("~/test")
        assert result == os.path.abspath(os.path.expanduser("~/test"))

    def test_expands_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_VAR", "/tmp/foo")
        result = _expand_path("$TEST_VAR/bar")
        assert result == os.path.abspath("/tmp/foo/bar")

    def test_normalizes_relative(self) -> None:
        result = _expand_path("./test")
        assert result == os.path.abspath("./test")


class TestIsWithin:
    def test_child_within_parent(self) -> None:
        assert _is_within("/tmp", "/tmp/foo") is True

    def test_same_path(self) -> None:
        assert _is_within("/tmp", "/tmp") is True

    def test_outside_parent(self) -> None:
        assert _is_within("/tmp", "/var") is False

    def test_invalid_paths(self) -> None:
        assert _is_within("", "") is False


class TestManagedStorageRoots:
    def test_returns_three_roots(self, tmp_path: Path) -> None:
        roots = managed_storage_roots(str(tmp_path))
        assert len(roots) == 3
        assert all(os.path.isabs(r) for r in roots)


class TestIsManagedStoragePath:
    def test_path_inside_runtime(self, tmp_path: Path) -> None:
        roots = managed_storage_roots(str(tmp_path))
        runtime_root = roots[0]
        test_file = os.path.join(runtime_root, "test.db")
        assert is_managed_storage_path(str(tmp_path), test_file) is True

    def test_path_outside(self, tmp_path: Path) -> None:
        assert is_managed_storage_path(str(tmp_path), "/outside/test.db") is False


class TestResolveSqlitePath:
    def test_empty_uses_default(self, tmp_path: Path) -> None:
        result = resolve_sqlite_path(str(tmp_path), "", allow_unmanaged_absolute=True, ensure_parent=False)
        assert "default.sqlite" in result

    def test_memory_path_passthrough(self, tmp_path: Path) -> None:
        result = resolve_sqlite_path(str(tmp_path), ":memory:", allow_unmanaged_absolute=True, ensure_parent=False)
        assert result == ":memory:"

    def test_file_uri_passthrough(self, tmp_path: Path) -> None:
        result = resolve_sqlite_path(str(tmp_path), "file:test.db", allow_unmanaged_absolute=True, ensure_parent=False)
        assert result == "file:test.db"

    def test_relative_path_resolves(self, tmp_path: Path) -> None:
        result = resolve_sqlite_path(str(tmp_path), "test.db", allow_unmanaged_absolute=True, ensure_parent=True)
        assert os.path.isabs(result)
        assert Path(result).parent.exists()

    def test_unmanaged_absolute_denied(self, tmp_path: Path) -> None:
        with pytest.raises(DatabasePolicyError, match="outside managed storage"):
            resolve_sqlite_path(str(tmp_path), "/outside/test.db", allow_unmanaged_absolute=False, ensure_parent=False)

    def test_unmanaged_absolute_allowed(self, tmp_path: Path) -> None:
        result = resolve_sqlite_path(
            str(tmp_path), "/outside/test.db", allow_unmanaged_absolute=True, ensure_parent=False
        )
        assert result == os.path.abspath("/outside/test.db")

    def test_ensure_parent_creates_dirs(self, tmp_path: Path) -> None:
        db_path = os.path.join(str(tmp_path), "sub", "dir", "test.db")
        result = resolve_sqlite_path(str(tmp_path), db_path, allow_unmanaged_absolute=True, ensure_parent=True)
        assert Path(result).parent.exists()

    def test_invalid_path_raises(self, tmp_path: Path) -> None:
        with (
            patch(
                "polaris.kernelone.db.policy.normalize_logical_rel_path",
                side_effect=ValueError("invalid"),
            ),
            pytest.raises(DatabasePathError, match="invalid sqlite path"),
        ):
            resolve_sqlite_path(str(tmp_path), "bad/path", allow_unmanaged_absolute=False, ensure_parent=False)


class TestResolveLancedbPath:
    def test_empty_uses_default(self, tmp_path: Path) -> None:
        result = resolve_lancedb_path(str(tmp_path), "", allow_unmanaged_absolute=True, ensure_exists=False)
        assert "lancedb" in result

    def test_relative_path_resolves(self, tmp_path: Path) -> None:
        result = resolve_lancedb_path(str(tmp_path), "mydb", allow_unmanaged_absolute=True, ensure_exists=False)
        assert os.path.isabs(result)

    def test_unmanaged_absolute_denied(self, tmp_path: Path) -> None:
        with pytest.raises(DatabasePolicyError, match="outside managed storage"):
            resolve_lancedb_path(str(tmp_path), "/outside/lancedb", allow_unmanaged_absolute=False, ensure_exists=False)

    def test_ensure_exists_creates_dir(self, tmp_path: Path) -> None:
        db_path = os.path.join(str(tmp_path), "sub", "lancedb")
        result = resolve_lancedb_path(str(tmp_path), db_path, allow_unmanaged_absolute=True, ensure_exists=True)
        assert Path(result).exists()

    def test_env_expansion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANCEDB_PATH", str(tmp_path / "env_lancedb"))
        result = resolve_lancedb_path(
            str(tmp_path), "$LANCEDB_PATH", allow_unmanaged_absolute=True, ensure_exists=False
        )
        assert "env_lancedb" in result
