"""Tests for polaris.infrastructure.accel.storage.cache module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polaris.infrastructure.accel.storage.cache import (
    ensure_project_dirs,
    project_hash,
    project_paths,
    read_json,
    write_json,
    write_jsonl,
)


class TestProjectHash:
    def test_project_hash_consistent(self):
        path = Path("/tmp/myproject")
        h1 = project_hash(path)
        h2 = project_hash(path)
        assert h1 == h2
        assert len(h1) == 16

    def test_project_hash_different_paths(self):
        h1 = project_hash(Path("/tmp/project_a"))
        h2 = project_hash(Path("/tmp/project_b"))
        assert h1 != h2

    def test_project_hash_case_insensitive(self):
        h1 = project_hash(Path("/tmp/MyProject"))
        h2 = project_hash(Path("/tmp/myproject"))
        assert h1 == h2

    def test_project_hash_backslash_normalized(self):
        h1 = project_hash(Path("/tmp/my/project"))
        h2 = project_hash(Path("\\tmp\\my\\project"))
        assert h1 == h2

    def test_project_hash_length(self):
        h = project_hash(Path("/tmp/test"))
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


class TestProjectPaths:
    def test_project_paths_structure(self):
        accel_home = Path("/accel")
        project_dir = Path("/tmp/myproject")
        paths = project_paths(accel_home, project_dir)

        assert "base" in paths
        assert "index" in paths
        assert "index_units" in paths
        assert "context" in paths
        assert "verify" in paths
        assert "telemetry" in paths
        assert "state" in paths

    def test_project_paths_base_contains_hash(self):
        accel_home = Path("/accel")
        project_dir = Path("/tmp/myproject")
        paths = project_paths(accel_home, project_dir)
        p_hash = project_hash(project_dir)

        assert paths["base"] == accel_home / "projects" / p_hash

    def test_project_paths_index_under_base(self):
        accel_home = Path("/accel")
        project_dir = Path("/tmp/myproject")
        paths = project_paths(accel_home, project_dir)

        assert paths["index"].parent == paths["base"]

    def test_project_paths_all_under_base(self):
        accel_home = Path("/accel")
        project_dir = Path("/tmp/myproject")
        paths = project_paths(accel_home, project_dir)

        for key, value in paths.items():
            if key != "base":
                assert str(value).startswith(str(paths["base"]))


class TestEnsureProjectDirs:
    def test_ensure_project_dirs_creates_all(self):
        paths = {
            "base": Path("/tmp/test/base"),
            "index": Path("/tmp/test/base/index"),
            "context": Path("/tmp/test/base/context"),
        }
        with patch.object(Path, "mkdir") as mock_mkdir:
            ensure_project_dirs(paths)

        assert mock_mkdir.call_count == 2  # base is skipped
        mock_mkdir.assert_any_call(parents=True, exist_ok=True)

    def test_ensure_project_dirs_skips_base(self):
        paths = {
            "base": Path("/tmp/test/base"),
        }
        with patch.object(Path, "mkdir") as mock_mkdir:
            ensure_project_dirs(paths)

        mock_mkdir.assert_not_called()

    def test_ensure_project_dirs_empty_dict(self):
        with patch.object(Path, "mkdir") as mock_mkdir:
            ensure_project_dirs({})

        mock_mkdir.assert_not_called()


class TestWriteJson:
    def test_write_json_creates_file(self):
        path = Path("/tmp/test.json")
        data = {"key": "value"}
        with patch.object(Path, "write_text") as mock_write:
            write_json(path, data)

        mock_write.assert_called_once()
        written = mock_write.call_args[0][0]
        assert json.loads(written) == data
        assert mock_write.call_args[1]["encoding"] == "utf-8"

    def test_write_json_includes_newline(self):
        path = Path("/tmp/test.json")
        data = {"key": "value"}
        with patch.object(Path, "write_text") as mock_write:
            write_json(path, data)

        written = mock_write.call_args[0][0]
        assert written.endswith("\n")

    def test_write_json_unicode_preserved(self):
        path = Path("/tmp/test.json")
        data = {"key": "中文"}
        with patch.object(Path, "write_text") as mock_write:
            write_json(path, data)

        written = mock_write.call_args[0][0]
        assert "中文" in written


class TestReadJson:
    def test_read_json_existing_file(self):
        path = Path("/tmp/test.json")
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value='{"key": "value"}'):
                result = read_json(path)

        assert result == {"key": "value"}

    def test_read_json_missing_file_returns_empty(self):
        path = Path("/tmp/test.json")
        with patch.object(Path, "exists", return_value=False):
            result = read_json(path)

        assert result == {}

    def test_read_json_invalid_json_returns_empty(self):
        path = Path("/tmp/test.json")
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value="not json"):
                result = read_json(path)

        assert result == {}

    def test_read_json_non_dict_returns_empty(self):
        path = Path("/tmp/test.json")
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value="[1, 2, 3]"):
                result = read_json(path)

        assert result == {}

    def test_read_json_os_error_returns_empty(self):
        path = Path("/tmp/test.json")
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
                result = read_json(path)

        assert result == {}


class TestWriteJsonl:
    def test_write_jsonl_empty_list(self):
        path = Path("/tmp/test.jsonl")
        with patch.object(Path, "write_text") as mock_write:
            write_jsonl(path, [])

        mock_write.assert_called_once_with("", encoding="utf-8")

    def test_write_jsonl_single_row(self):
        path = Path("/tmp/test.jsonl")
        rows = [{"key": "value"}]
        with patch.object(Path, "write_text") as mock_write:
            write_jsonl(path, rows)

        written = mock_write.call_args[0][0]
        assert written == '{"key": "value"}\n'

    def test_write_jsonl_multiple_rows(self):
        path = Path("/tmp/test.jsonl")
        rows = [{"a": 1}, {"b": 2}]
        with patch.object(Path, "write_text") as mock_write:
            write_jsonl(path, rows)

        written = mock_write.call_args[0][0]
        lines = written.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[1]) == {"b": 2}

    def test_write_jsonl_unicode_preserved(self):
        path = Path("/tmp/test.jsonl")
        rows = [{"key": "中文"}]
        with patch.object(Path, "write_text") as mock_write:
            write_jsonl(path, rows)

        written = mock_write.call_args[0][0]
        assert "中文" in written
