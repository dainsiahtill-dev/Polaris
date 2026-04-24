"""Tests for polaris.infrastructure.accel.storage.cache module."""

from __future__ import annotations

import json
from pathlib import Path

from polaris.infrastructure.accel.storage.cache import (
    ensure_project_dirs,
    project_hash,
    project_paths,
    read_json,
    write_json,
    write_jsonl,
)


class TestProjectHash:
    """Tests for project_hash function."""

    def test_same_path_same_hash(self, tmp_path: Path) -> None:
        """Same path should produce same hash."""
        hash1 = project_hash(tmp_path)
        hash2 = project_hash(tmp_path)
        assert hash1 == hash2

    def test_different_paths_different_hashes(self, tmp_path: Path) -> None:
        """Different paths should produce different hashes."""
        path1 = tmp_path / "project1"
        path2 = tmp_path / "project2"
        path1.mkdir()
        path2.mkdir()
        hash1 = project_hash(path1)
        hash2 = project_hash(path2)
        assert hash1 != hash2

    def test_normalizes_case(self, tmp_path: Path) -> None:
        """Should normalize case differences."""
        result1 = project_hash(tmp_path)
        result2 = project_hash(str(tmp_path).upper())
        # Verify both return valid hashes
        assert len(result1) == 16
        assert len(result2) == 16

    def test_backslash_normalization(self, tmp_path: Path) -> None:
        """Should normalize backslashes."""
        result1 = project_hash(tmp_path)
        result2 = project_hash(str(tmp_path).replace("/", "\\"))
        # Verify both return valid hashes
        assert len(result1) == 16
        assert len(result2) == 16

    def test_returns_16_chars(self, tmp_path: Path) -> None:
        """Should return 16 character hash."""
        result = project_hash(tmp_path)
        assert len(result) == 16

    def test_hex_chars_only(self, tmp_path: Path) -> None:
        """Should return only hex characters."""
        result = project_hash(tmp_path)
        assert all(c in "0123456789abcdef" for c in result)


class TestProjectPaths:
    """Tests for project_paths function."""

    def test_returns_required_keys(self, tmp_path: Path) -> None:
        """Should return all required path keys."""
        accel_home = tmp_path / "accel_home"
        accel_home.mkdir(parents=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir(parents=True)
        result = project_paths(accel_home, project_dir)
        expected_keys = {"base", "index", "index_units", "context", "verify", "telemetry", "state"}
        assert set(result.keys()) == expected_keys

    def test_base_under_accel_home(self, tmp_path: Path) -> None:
        """Base path should be under accel_home."""
        accel_home = tmp_path / "accel_home"
        project_dir = tmp_path / "project"
        result = project_paths(accel_home, project_dir)
        assert result["base"].parent.parent == accel_home

    def test_nested_directories(self, tmp_path: Path) -> None:
        """Should create correct nested structure."""
        accel_home = tmp_path / "accel_home"
        project_dir = tmp_path / "project"
        result = project_paths(accel_home, project_dir)
        assert result["index"].parent == result["base"]
        assert result["context"].parent == result["base"]


class TestEnsureProjectDirs:
    """Tests for ensure_project_dirs function."""

    def test_creates_directories(self, tmp_path: Path) -> None:
        """Should create all directories."""
        paths = {
            "base": tmp_path / "base",
            "index": tmp_path / "index",
            "context": tmp_path / "context",
        }
        ensure_project_dirs(paths)
        assert paths["index"].exists()
        assert paths["context"].exists()

    def test_skips_base_key(self, tmp_path: Path) -> None:
        """Should skip 'base' key (only creates subdirs)."""
        paths = {
            "base": tmp_path / "base",
            "index": tmp_path / "base" / "index",
        }
        ensure_project_dirs(paths)
        # Should create subdirectories but not 'base' itself
        assert paths["index"].exists()

    def test_creates_nested_dirs(self, tmp_path: Path) -> None:
        """Should create nested directories."""
        paths = {
            "nested": tmp_path / "a" / "b" / "c",
        }
        ensure_project_dirs(paths)
        assert paths["nested"].exists()

    def test_handles_empty_paths(self, tmp_path: Path) -> None:
        """Should handle empty paths dict."""
        ensure_project_dirs({})
        # Should not raise


class TestWriteJson:
    """Tests for write_json function."""

    def test_writes_json(self, tmp_path: Path) -> None:
        """Should write JSON data to file."""
        file_path = tmp_path / "data.json"
        data = {"key": "value", "number": 42}
        write_json(file_path, data)
        assert file_path.exists()
        content = json.loads(file_path.read_text(encoding="utf-8"))
        assert content == data

    def test_formatted_output(self, tmp_path: Path) -> None:
        """Should write formatted JSON with indentation."""
        file_path = tmp_path / "data.json"
        write_json(file_path, {"key": "value"})
        content = file_path.read_text(encoding="utf-8")
        # Should have indentation (2 spaces)
        assert "  " in content

    def test_utf8_encoding(self, tmp_path: Path) -> None:
        """Should write UTF-8 encoded content."""
        file_path = tmp_path / "data.json"
        data = {"unicode": "你好世界"}
        write_json(file_path, data)
        content = file_path.read_text(encoding="utf-8")
        assert "你好世界" in content


class TestReadJson:
    """Tests for read_json function."""

    def test_reads_json(self, tmp_path: Path) -> None:
        """Should read JSON data from file."""
        file_path = tmp_path / "data.json"
        file_path.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        result = read_json(file_path)
        assert result == {"key": "value"}

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file should return empty dict."""
        result = read_json(tmp_path / "nonexistent.json")
        assert result == {}

    def test_invalid_json(self, tmp_path: Path) -> None:
        """Invalid JSON should return empty dict."""
        file_path = tmp_path / "data.json"
        file_path.write_text("not valid json {", encoding="utf-8")
        result = read_json(file_path)
        assert result == {}

    def test_non_dict_json(self, tmp_path: Path) -> None:
        """Non-dict JSON should return empty dict."""
        file_path = tmp_path / "data.json"
        file_path.write_text("[1, 2, 3]", encoding="utf-8")
        result = read_json(file_path)
        assert result == {}


class TestWriteJsonl:
    """Tests for write_jsonl function."""

    def test_writes_jsonl(self, tmp_path: Path) -> None:
        """Should write JSONL format (one JSON per line)."""
        file_path = tmp_path / "data.jsonl"
        rows = [{"key": "value1"}, {"key": "value2"}]
        write_jsonl(file_path, rows)
        assert file_path.exists()
        lines = file_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_empty_rows(self, tmp_path: Path) -> None:
        """Should handle empty rows list."""
        file_path = tmp_path / "data.jsonl"
        write_jsonl(file_path, [])
        assert file_path.exists()
        # Should write nothing or just a newline
        content = file_path.read_text(encoding="utf-8")
        assert content == ""

    def test_utf8_encoding(self, tmp_path: Path) -> None:
        """Should write UTF-8 encoded content."""
        file_path = tmp_path / "data.jsonl"
        rows = [{"unicode": "你好世界"}]
        write_jsonl(file_path, rows)
        content = file_path.read_text(encoding="utf-8")
        assert "你好世界" in content

    def test_newline_at_end(self, tmp_path: Path) -> None:
        """Should end with newline if there are rows."""
        file_path = tmp_path / "data.jsonl"
        rows = [{"key": "value"}]
        write_jsonl(file_path, rows)
        content = file_path.read_text(encoding="utf-8")
        assert content.endswith("\n")
