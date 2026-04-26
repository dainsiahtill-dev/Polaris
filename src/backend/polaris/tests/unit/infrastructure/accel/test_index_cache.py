"""Tests for polaris.infrastructure.accel.storage.index_cache module."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from polaris.infrastructure.accel.storage.index_cache import (
    INDEX_FILE_NAMES,
    append_delta_ops,
    base_path_for_kind,
    clear_delta_file,
    count_jsonl_lines,
    delta_path_for_base,
    flatten_grouped_rows,
    group_rows_by_key,
    load_grouped_rows_with_delta,
    load_index_rows,
    load_jsonl_mmap,
    write_jsonl_atomic,
)


class TestIndexFileNames:
    """Tests for INDEX_FILE_NAMES constant."""

    def test_contains_expected_kinds(self) -> None:
        """Should contain all expected index kinds."""
        expected = {"symbols", "references", "dependencies", "test_ownership"}
        assert set(INDEX_FILE_NAMES.keys()) == expected

    def test_file_extensions(self) -> None:
        """All entries should have .jsonl extension."""
        for filename in INDEX_FILE_NAMES.values():
            assert filename.endswith(".jsonl")


class TestBasePathForKind:
    """Tests for base_path_for_kind function."""

    def test_valid_kind(self, tmp_path: Path) -> None:
        """Should return correct path for valid kind."""
        result = base_path_for_kind(tmp_path, "symbols")
        assert result == tmp_path / "symbols.jsonl"

    def test_all_valid_kinds(self, tmp_path: Path) -> None:
        """Should work for all valid kinds."""
        for kind in INDEX_FILE_NAMES:
            result = base_path_for_kind(tmp_path, kind)
            assert result.parent == tmp_path
            assert result.name == INDEX_FILE_NAMES[kind]

    def test_invalid_kind(self, tmp_path: Path) -> None:
        """Should raise ValueError for invalid kind."""
        with pytest.raises(ValueError, match="Invalid kind"):
            base_path_for_kind(tmp_path, "invalid")


class TestDeltaPathForBase:
    """Tests for delta_path_for_base function."""

    def test_creates_delta_path(self) -> None:
        """Should return path with .delta.jsonl extension."""
        base = Path("/data/symbols.jsonl")
        result = delta_path_for_base(base)
        assert result == Path("/data/symbols.delta.jsonl")

    def test_preserves_stem(self) -> None:
        """Should preserve original stem in name."""
        base = Path("/data/my-data.jsonl")
        result = delta_path_for_base(base)
        assert result.stem == "my-data.delta"


class TestLoadJsonlMmap:
    """Tests for load_jsonl_mmap function."""

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file should return empty list."""
        result = load_jsonl_mmap(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file should return empty list."""
        file_path = tmp_path / "empty.jsonl"
        file_path.write_text("", encoding="utf-8")
        result = load_jsonl_mmap(file_path)
        assert result == []

    def test_valid_jsonl(self, tmp_path: Path) -> None:
        """Should parse valid JSONL file."""
        file_path = tmp_path / "data.jsonl"
        file_path.write_text(
            json.dumps({"key": "value1"}) + "\n" + json.dumps({"key": "value2"}) + "\n", encoding="utf-8"
        )
        result = load_jsonl_mmap(file_path)
        assert len(result) == 2
        assert result[0]["key"] == "value1"
        assert result[1]["key"] == "value2"

    def test_skips_invalid_lines(self, tmp_path: Path) -> None:
        """Should skip invalid JSON lines."""
        file_path = tmp_path / "data.jsonl"
        file_path.write_text(
            json.dumps({"key": "value1"}) + "\n" + "not valid json\n" + json.dumps({"key": "value2"}) + "\n",
            encoding="utf-8",
        )
        result = load_jsonl_mmap(file_path)
        assert len(result) == 2

    def test_skips_empty_lines(self, tmp_path: Path) -> None:
        """Should skip empty lines."""
        file_path = tmp_path / "data.jsonl"
        file_path.write_text(
            json.dumps({"key": "value1"}) + "\n\n\n" + json.dumps({"key": "value2"}) + "\n", encoding="utf-8"
        )
        result = load_jsonl_mmap(file_path)
        assert len(result) == 2


class TestCountJsonlLines:
    """Tests for count_jsonl_lines function."""

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file should return 0."""
        result = count_jsonl_lines(tmp_path / "nonexistent.jsonl")
        assert result == 0

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file should return 0."""
        file_path = tmp_path / "empty.jsonl"
        file_path.write_text("", encoding="utf-8")
        result = count_jsonl_lines(file_path)
        assert result == 0

    def test_counts_lines(self, tmp_path: Path) -> None:
        """Should count all lines including those with content."""
        file_path = tmp_path / "data.jsonl"
        file_path.write_text(
            json.dumps({"key": "value1"})
            + "\n"
            + json.dumps({"key": "value2"})
            + "\n"
            + json.dumps({"key": "value3"})
            + "\n",
            encoding="utf-8",
        )
        result = count_jsonl_lines(file_path)
        assert result == 3


class TestGroupRowsByKey:
    """Tests for group_rows_by_key function."""

    def test_empty_rows(self) -> None:
        """Empty rows should return empty dict."""
        result = group_rows_by_key([], "key")
        assert result == {}

    def test_groups_rows(self) -> None:
        """Should group rows by key field."""
        rows = [
            {"key": "a", "data": 1},
            {"key": "b", "data": 2},
            {"key": "a", "data": 3},
        ]
        result = group_rows_by_key(rows, "key")
        assert len(result) == 2
        assert len(result["a"]) == 2
        assert len(result["b"]) == 1

    def test_skips_empty_keys(self) -> None:
        """Should skip rows with empty key."""
        rows = [
            {"key": "a", "data": 1},
            {"data": 2},  # No key
            {"key": "", "data": 3},
        ]
        result = group_rows_by_key(rows, "key")
        assert len(result) == 1
        assert "a" in result

    def test_preserves_row_order(self) -> None:
        """Should preserve order of rows within group."""
        rows = [
            {"key": "a", "order": 1},
            {"key": "b", "order": 2},
            {"key": "a", "order": 3},
        ]
        result = group_rows_by_key(rows, "key")
        assert result["a"][0]["order"] == 1
        assert result["a"][1]["order"] == 3


class TestFlattenGroupedRows:
    """Tests for flatten_grouped_rows function."""

    def test_empty_groups(self) -> None:
        """Empty groups should return empty list."""
        result = flatten_grouped_rows({})
        assert result == []

    def test_flattens_sorted(self) -> None:
        """Should flatten groups in sorted key order."""
        groups = {
            "b": [{"key": "b", "v": 1}],
            "a": [{"key": "a", "v": 2}],
            "c": [{"key": "c", "v": 3}],
        }
        result = flatten_grouped_rows(groups)
        assert result[0]["key"] == "a"
        assert result[1]["key"] == "b"
        assert result[2]["key"] == "c"

    def test_preserves_within_group_order(self) -> None:
        """Should preserve order within each group."""
        groups = {
            "a": [{"order": 1}, {"order": 2}],
        }
        result = flatten_grouped_rows(groups)
        assert result[0]["order"] == 1
        assert result[1]["order"] == 2


class TestLoadGroupedRowsWithDelta:
    """Tests for load_grouped_rows_with_delta function."""

    def test_loads_base_only(self, tmp_path: Path) -> None:
        """Should load base file without delta."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)
        base_file = index_dir / "symbols.jsonl"
        base_file.write_text(
            json.dumps({"symbol": "a", "def": 1}) + "\n" + json.dumps({"symbol": "b", "def": 2}) + "\n",
            encoding="utf-8",
        )
        grouped, delta_count = load_grouped_rows_with_delta(
            index_dir=index_dir,
            kind="symbols",
            key_field="symbol",
        )
        assert "a" in grouped
        assert "b" in grouped
        assert delta_count == 0

    def test_applies_delta_delete(self, tmp_path: Path) -> None:
        """Should apply delta delete operations."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)
        base_file = index_dir / "symbols.jsonl"
        base_file.write_text(
            json.dumps({"symbol": "a", "def": 1}) + "\n" + json.dumps({"symbol": "b", "def": 2}) + "\n",
            encoding="utf-8",
        )
        delta_file = index_dir / "symbols.delta.jsonl"
        delta_file.write_text(json.dumps({"op": "delete", "key": "a"}) + "\n", encoding="utf-8")
        grouped, delta_count = load_grouped_rows_with_delta(
            index_dir=index_dir,
            kind="symbols",
            key_field="symbol",
        )
        assert "a" not in grouped
        assert "b" in grouped
        assert delta_count == 1

    def test_applies_delta_set(self, tmp_path: Path) -> None:
        """Should apply delta set operations."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)
        base_file = index_dir / "symbols.jsonl"
        base_file.write_text(json.dumps({"symbol": "a", "def": 1}) + "\n", encoding="utf-8")
        delta_file = index_dir / "symbols.delta.jsonl"
        delta_file.write_text(
            json.dumps({"op": "set", "key": "a", "rows": [{"symbol": "a", "def": 10}]}) + "\n", encoding="utf-8"
        )
        grouped, _ = load_grouped_rows_with_delta(
            index_dir=index_dir,
            kind="symbols",
            key_field="symbol",
        )
        assert grouped["a"][0]["def"] == 10


class TestLoadIndexRows:
    """Tests for load_index_rows function."""

    def test_loads_flat_rows(self, tmp_path: Path) -> None:
        """Should load and flatten index rows."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)
        base_file = index_dir / "symbols.jsonl"
        base_file.write_text(
            json.dumps({"symbol": "a", "def": 1}) + "\n" + json.dumps({"symbol": "b", "def": 2}) + "\n",
            encoding="utf-8",
        )
        result = load_index_rows(
            index_dir=index_dir,
            kind="symbols",
            key_field="symbol",
        )
        assert len(result) == 2
        # Should be sorted by key
        assert result[0]["symbol"] == "a"
        assert result[1]["symbol"] == "b"


class TestAppendDeltaOps:
    """Tests for append_delta_ops function."""

    def test_appends_ops(self, tmp_path: Path) -> None:
        """Should append operations to delta file."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)
        ops = [
            {"op": "delete", "key": "a"},
            {"op": "set", "key": "b", "rows": [{"key": "b", "v": 2}]},
        ]
        count = append_delta_ops(index_dir, "symbols", ops)
        assert count == 2
        delta_file = index_dir / "symbols.delta.jsonl"
        assert delta_file.exists()

    def test_empty_ops(self, tmp_path: Path) -> None:
        """Should return 0 for empty ops list."""
        count = append_delta_ops(tmp_path, "symbols", [])
        assert count == 0

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Should create parent directories."""
        index_dir = tmp_path / "subdir" / "index"
        count = append_delta_ops(index_dir, "symbols", [{"op": "delete", "key": "a"}])
        assert count == 1
        assert index_dir.exists()


class TestWriteJsonlAtomic:
    """Tests for write_jsonl_atomic function."""

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_writes_rows(self, tmp_path: Path) -> None:
        """Should write JSONL rows atomically."""
        file_path = tmp_path / "data.jsonl"
        rows = [
            {"key": "a", "value": 1},
            {"key": "b", "value": 2},
        ]
        write_jsonl_atomic(file_path, rows)
        assert file_path.exists()
        lines = file_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Should create parent directories."""
        file_path = tmp_path / "subdir" / "nested" / "data.jsonl"
        write_jsonl_atomic(file_path, [{"key": "value"}])
        assert file_path.exists()

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_overwrites_existing(self, tmp_path: Path) -> None:
        """Should overwrite existing file."""
        file_path = tmp_path / "data.jsonl"
        file_path.write_text("old content", encoding="utf-8")
        write_jsonl_atomic(file_path, [{"key": "new"}])
        content = file_path.read_text(encoding="utf-8")
        assert "old" not in content
        assert '"key": "new"' in content

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_empty_rows(self, tmp_path: Path) -> None:
        """Should handle empty rows list."""
        file_path = tmp_path / "data.jsonl"
        write_jsonl_atomic(file_path, [])
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == ""


class TestClearDeltaFile:
    """Tests for clear_delta_file function."""

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_clears_delta(self, tmp_path: Path) -> None:
        """Should clear delta file content."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)
        base_file = index_dir / "symbols.jsonl"
        base_file.write_text(json.dumps({"symbol": "a", "def": 1}) + "\n", encoding="utf-8")
        delta_file = index_dir / "symbols.delta.jsonl"
        delta_file.write_text(json.dumps({"op": "delete", "key": "a"}) + "\n", encoding="utf-8")
        clear_delta_file(index_dir, "symbols")
        # Should truncate delta file
        assert delta_file.exists()
        assert delta_file.read_text(encoding="utf-8") == ""

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_creates_delta_if_missing(self, tmp_path: Path) -> None:
        """Should create delta file if it doesn't exist."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)
        base_file = index_dir / "symbols.jsonl"
        base_file.write_text(json.dumps({"symbol": "a", "def": 1}) + "\n", encoding="utf-8")
        clear_delta_file(index_dir, "symbols")
        delta_file = index_dir / "symbols.delta.jsonl"
        assert delta_file.exists()
