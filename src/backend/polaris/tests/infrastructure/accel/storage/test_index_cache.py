"""Tests for polaris.infrastructure.accel.storage.index_cache module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

# =============================================================================
# base_path_for_kind
# =============================================================================


def test_base_path_for_kind_valid(tmp_path: Path) -> None:
    for kind, filename in INDEX_FILE_NAMES.items():
        result = base_path_for_kind(tmp_path, kind)
        assert result == tmp_path / filename


def test_base_path_for_kind_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid kind"):
        base_path_for_kind(Path("/tmp"), "nonexistent")


# =============================================================================
# delta_path_for_base
# =============================================================================


def test_delta_path_for_base() -> None:
    base = Path("/tmp/index/symbols.jsonl")
    result = delta_path_for_base(base)
    assert result == Path("/tmp/index/symbols.delta.jsonl")


def test_delta_path_for_base_different_stem() -> None:
    base = Path("/tmp/index/references.jsonl")
    result = delta_path_for_base(base)
    assert result == Path("/tmp/index/references.delta.jsonl")


# =============================================================================
# load_jsonl_mmap
# =============================================================================


def test_load_jsonl_mmap_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"
    assert load_jsonl_mmap(missing) == []


def test_load_jsonl_mmap_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert load_jsonl_mmap(empty) == []


def test_load_jsonl_mmap_valid_rows(tmp_path: Path) -> None:
    path = tmp_path / "valid.jsonl"
    rows = [{"a": 1}, {"b": 2}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    result = load_jsonl_mmap(path)
    assert result == rows


def test_load_jsonl_mmap_skips_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    path.write_text('{"a": 1}\nnot json\n{"b": 2}\n', encoding="utf-8")
    result = load_jsonl_mmap(path)
    assert result == [{"a": 1}, {"b": 2}]


def test_load_jsonl_mmap_skips_non_dict(tmp_path: Path) -> None:
    path = tmp_path / "mixed_types.jsonl"
    path.write_text('{"a": 1}\n[1, 2, 3]\n{"b": 2}\n', encoding="utf-8")
    result = load_jsonl_mmap(path)
    assert result == [{"a": 1}, {"b": 2}]


def test_load_jsonl_mmap_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "blanks.jsonl"
    path.write_text('{"a": 1}\n\n\n{"b": 2}\n', encoding="utf-8")
    result = load_jsonl_mmap(path)
    assert result == [{"a": 1}, {"b": 2}]


# =============================================================================
# count_jsonl_lines
# =============================================================================


def test_count_jsonl_lines_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"
    assert count_jsonl_lines(missing) == 0


def test_count_jsonl_lines_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert count_jsonl_lines(empty) == 0


def test_count_jsonl_lines_with_data(tmp_path: Path) -> None:
    path = tmp_path / "data.jsonl"
    path.write_text('{"a": 1}\n{"b": 2}\n{"c": 3}\n', encoding="utf-8")
    assert count_jsonl_lines(path) == 3


def test_count_jsonl_lines_includes_invalid(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    path.write_text('{"a": 1}\nnot json\n\n', encoding="utf-8")
    assert count_jsonl_lines(path) == 3


# =============================================================================
# group_rows_by_key
# =============================================================================


def test_group_rows_by_key_basic() -> None:
    rows: list[dict[str, Any]] = [
        {"key": "a", "v": 1},
        {"key": "b", "v": 2},
        {"key": "a", "v": 3},
    ]
    result = group_rows_by_key(rows, "key")
    assert result == {"a": [{"key": "a", "v": 1}, {"key": "a", "v": 3}], "b": [{"key": "b", "v": 2}]}


def test_group_rows_by_key_skips_empty() -> None:
    rows: list[dict[str, Any]] = [
        {"key": "a", "v": 1},
        {"key": "", "v": 2},
        {"key": "a", "v": 3},
    ]
    result = group_rows_by_key(rows, "key")
    assert result == {"a": [{"key": "a", "v": 1}, {"key": "a", "v": 3}]}


def test_group_rows_by_key_missing_key() -> None:
    rows: list[dict[str, Any]] = [{"other": "x"}, {"key": "a"}]
    result = group_rows_by_key(rows, "key")
    assert result == {"a": [{"key": "a"}]}


# =============================================================================
# flatten_grouped_rows
# =============================================================================


def test_flatten_grouped_rows_sorted() -> None:
    grouped: dict[str, list[dict[str, Any]]] = {
        "b": [{"k": "b", "v": 1}],
        "a": [{"k": "a", "v": 2}, {"k": "a", "v": 3}],
    }
    result = flatten_grouped_rows(grouped)
    assert result == [{"k": "a", "v": 2}, {"k": "a", "v": 3}, {"k": "b", "v": 1}]


def test_flatten_grouped_rows_empty() -> None:
    assert flatten_grouped_rows({}) == []


# =============================================================================
# load_grouped_rows_with_delta
# =============================================================================


def test_load_grouped_rows_with_delta_base_only(tmp_path: Path) -> None:
    kind = "symbols"
    base = base_path_for_kind(tmp_path, kind)
    base.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"sym": "a", "v": 1}, {"sym": "b", "v": 2}]
    write_jsonl_atomic(base, rows)

    grouped, delta_count = load_grouped_rows_with_delta(tmp_path, kind, "sym")
    assert delta_count == 0
    assert "a" in grouped
    assert "b" in grouped


def test_load_grouped_rows_with_delta_set_override(tmp_path: Path) -> None:
    kind = "symbols"
    base = base_path_for_kind(tmp_path, kind)
    base.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"sym": "a", "v": 1}]
    write_jsonl_atomic(base, rows)

    append_delta_ops(tmp_path, kind, [{"op": "set", "key": "a", "rows": [{"sym": "a", "v": 99}]}])
    grouped, delta_count = load_grouped_rows_with_delta(tmp_path, kind, "sym")
    assert delta_count == 1
    assert grouped["a"] == [{"sym": "a", "v": 99}]


def test_load_grouped_rows_with_delta_delete(tmp_path: Path) -> None:
    kind = "symbols"
    base = base_path_for_kind(tmp_path, kind)
    base.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"sym": "a", "v": 1}, {"sym": "b", "v": 2}]
    write_jsonl_atomic(base, rows)

    append_delta_ops(tmp_path, kind, [{"op": "delete", "key": "a"}])
    grouped, delta_count = load_grouped_rows_with_delta(tmp_path, kind, "sym")
    assert delta_count == 1
    assert "a" not in grouped
    assert "b" in grouped


def test_load_grouped_rows_with_delta_no_base(tmp_path: Path) -> None:
    kind = "symbols"
    append_delta_ops(tmp_path, kind, [{"op": "set", "key": "x", "rows": [{"sym": "x", "v": 1}]}])
    grouped, delta_count = load_grouped_rows_with_delta(tmp_path, kind, "sym")
    assert delta_count == 1
    assert grouped["x"] == [{"sym": "x", "v": 1}]


def test_load_grouped_rows_with_delta_skips_empty_key(tmp_path: Path) -> None:
    kind = "symbols"
    append_delta_ops(tmp_path, kind, [{"op": "set", "key": "", "rows": [{"sym": "x"}]}])
    grouped, delta_count = load_grouped_rows_with_delta(tmp_path, kind, "sym")
    assert delta_count == 1
    assert "x" not in grouped


def test_load_grouped_rows_with_delta_skips_unknown_op(tmp_path: Path) -> None:
    kind = "symbols"
    base = base_path_for_kind(tmp_path, kind)
    base.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl_atomic(base, [{"sym": "a", "v": 1}])

    append_delta_ops(tmp_path, kind, [{"op": "unknown", "key": "a", "rows": []}])
    grouped, delta_count = load_grouped_rows_with_delta(tmp_path, kind, "sym")
    assert delta_count == 1
    assert grouped["a"] == [{"sym": "a", "v": 1}]


# =============================================================================
# load_index_rows
# =============================================================================


def test_load_index_rows(tmp_path: Path) -> None:
    kind = "symbols"
    base = base_path_for_kind(tmp_path, kind)
    base.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"sym": "b"}, {"sym": "a"}]
    write_jsonl_atomic(base, rows)

    result = load_index_rows(tmp_path, kind, "sym")
    assert result == [{"sym": "a"}, {"sym": "b"}]


# =============================================================================
# append_delta_ops
# =============================================================================


def test_append_delta_ops_returns_count(tmp_path: Path) -> None:
    kind = "symbols"
    ops = [{"op": "set", "key": "a", "rows": []}]
    assert append_delta_ops(tmp_path, kind, ops) == 1


def test_append_delta_ops_empty_list(tmp_path: Path) -> None:
    assert append_delta_ops(tmp_path, "symbols", []) == 0


def test_append_delta_ops_appends(tmp_path: Path) -> None:
    kind = "symbols"
    append_delta_ops(tmp_path, kind, [{"op": "set", "key": "a", "rows": []}])
    append_delta_ops(tmp_path, kind, [{"op": "set", "key": "b", "rows": []}])
    base = base_path_for_kind(tmp_path, kind)
    delta = delta_path_for_base(base)
    assert count_jsonl_lines(delta) == 2


# =============================================================================
# write_jsonl_atomic
# =============================================================================


def test_write_jsonl_atomic_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    write_jsonl_atomic(path, [{"a": 1}])
    assert path.exists()
    assert load_jsonl_mmap(path) == [{"a": 1}]


def test_write_jsonl_atomic_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    write_jsonl_atomic(path, [{"a": 1}])
    write_jsonl_atomic(path, [{"b": 2}])
    assert load_jsonl_mmap(path) == [{"b": 2}]


def test_write_jsonl_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "out.jsonl"
    write_jsonl_atomic(path, [{"x": 1}])
    assert path.exists()


# =============================================================================
# clear_delta_file
# =============================================================================


def test_clear_delta_file(tmp_path: Path) -> None:
    kind = "symbols"
    append_delta_ops(tmp_path, kind, [{"op": "set", "key": "a", "rows": []}])
    base = base_path_for_kind(tmp_path, kind)
    delta = delta_path_for_base(base)
    assert delta.exists()
    clear_delta_file(tmp_path, kind)
    assert delta.exists()
    assert load_jsonl_mmap(delta) == []
