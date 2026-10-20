"""Advertised empty range replacement survives existing normalization/consumer paths."""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_edit_blocks
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments


@pytest.mark.parametrize(
    "raw",
    [
        {"file": "owned.txt", "startLine": 2, "endLine": 2, "newText": ""},
        {"blocks": {"file": "owned.txt", "start": 2, "end": 2, "replace": ""}},
        {"blocks": '("owned.txt", 2, 2, "")'},
        {"edits": [{"file": "owned.txt", "start": 2, "end": 2, "replace": ""}]},
    ],
)
def test_explicit_empty_range_normalizes_without_becoming_missing(raw):
    normalized = normalize_tool_arguments("edit_blocks", raw)
    assert {key: normalized[key] for key in ("file", "start", "end", "replace")} == {
        "file": "owned.txt",
        "start": 2,
        "end": 2,
        "replace": "",
    }


@pytest.mark.parametrize("replacement,expected", [("", "same\nend\n"), ("changed", "same\nchanged\nend\n")])
def test_toolkit_uses_exact_offset_and_explicit_deletion(tmp_path, replacement, expected):
    (tmp_path / "owned.txt").write_text("same\nsame\nend\n", encoding="utf-8")
    executor = AgentAccelToolExecutor(workspace=str(tmp_path))
    result = _handle_edit_blocks(executor, file="owned.txt", start=2, end=2, replace=replacement)
    assert result.get("ok") is True, result
    assert (tmp_path / "owned.txt").read_text(encoding="utf-8") == expected


def test_missing_replacement_is_rejected_by_toolkit(tmp_path):
    (tmp_path / "owned.txt").write_text("one\ntwo\n", encoding="utf-8")
    result = _handle_edit_blocks(AgentAccelToolExecutor(workspace=str(tmp_path)), file="owned.txt", start=2, end=2)
    assert result.get("ok") is False
    assert (tmp_path / "owned.txt").read_text(encoding="utf-8") == "one\ntwo\n"


def test_json_ranges_keep_original_offsets_after_prior_line_count_change(tmp_path):
    import json

    (tmp_path / "owned.txt").write_text("same\nsame\nsame\nfour\n", encoding="utf-8")
    payload = json.dumps(
        [
            {"file": "owned.txt", "start": 1, "end": 1, "replace": "ONE\nadded\n"},
            {"file": "owned.txt", "start": 3, "end": 3, "replace": "THIRD\n"},
        ]
    )
    result = _handle_edit_blocks(AgentAccelToolExecutor(workspace=str(tmp_path)), blocks=payload)
    assert result.get("ok") is True, result
    assert (tmp_path / "owned.txt").read_text(encoding="utf-8") == "ONE\nadded\nsame\nTHIRD\nfour\n"
