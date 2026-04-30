"""Tests for polaris.kernelone.tool_execution.output module.

Covers all 8 public functions with mocked file I/O and edge cases.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.tool_execution.constants import MAX_EVENT_CONTENT_LINES
from polaris.kernelone.tool_execution.output import (
    analyze_slice_content,
    annotate_rg_output,
    build_refs,
    compact_tool_output,
    count_tool_output_lines,
    persist_tool_raw_output,
    score_hit,
    suggest_radius,
)

# -----------------------------------------------------------------------------
# persist_tool_raw_output
# -----------------------------------------------------------------------------


class FakeState:
    """Minimal state object for testing persist_tool_raw_output."""

    def __init__(
        self, run_id: str = "run-123", workspace: str = "/ws", cache_root: str = "/cache", seq: int = 0
    ) -> None:
        self.current_run_id = run_id
        self.workspace_full = workspace
        self.cache_root_full = cache_root
        self._tool_output_seq = seq


@pytest.fixture
def fake_state() -> FakeState:
    return FakeState()


@patch("polaris.kernelone.tool_execution.output.resolve_run_dir")
@patch("polaris.kernelone.tool_execution.output.write_text_atomic")
@patch("os.makedirs")
def test_persist_tool_raw_output_all_streams(
    mock_makedirs: MagicMock, mock_write: MagicMock, mock_resolve: MagicMock, fake_state: FakeState
) -> None:
    mock_resolve.return_value = "/run_dir"
    paths = persist_tool_raw_output(
        fake_state,
        "read_file",
        stdout_text="stdout data",
        stderr_text="stderr data",
        error_text="error data",
    )
    assert paths["tool_stdout_path"].endswith(".stdout.log")
    assert paths["tool_stderr_path"].endswith(".stderr.log")
    assert paths["tool_error_path"].endswith(".error.log")
    assert fake_state._tool_output_seq == 1
    assert mock_write.call_count == 3
    mock_makedirs.assert_called_once()


@patch("polaris.kernelone.tool_execution.output.resolve_run_dir")
@patch("polaris.kernelone.tool_execution.output.write_text_atomic")
@patch("os.makedirs")
def test_persist_tool_raw_output_only_stdout(
    mock_makedirs: MagicMock, mock_write: MagicMock, mock_resolve: MagicMock, fake_state: FakeState
) -> None:
    mock_resolve.return_value = "/run_dir"
    paths = persist_tool_raw_output(fake_state, "read_file", stdout_text="hello")
    assert "tool_stdout_path" in paths
    assert "tool_stderr_path" not in paths
    assert "tool_error_path" not in paths
    assert mock_write.call_count == 1


@patch("polaris.kernelone.tool_execution.output.resolve_run_dir")
@patch("polaris.kernelone.tool_execution.output.write_text_atomic")
def test_persist_tool_raw_output_empty_returns_empty(
    mock_write: MagicMock, mock_resolve: MagicMock, fake_state: FakeState
) -> None:
    mock_resolve.return_value = "/run_dir"
    paths = persist_tool_raw_output(fake_state, "read_file")
    assert paths == {}
    assert mock_write.call_count == 0


@patch("polaris.kernelone.tool_execution.output.resolve_run_dir")
def test_persist_tool_raw_output_no_run_id(mock_resolve: MagicMock) -> None:
    state = FakeState(run_id="")
    paths = persist_tool_raw_output(state, "read_file", stdout_text="x")
    assert paths == {}
    mock_resolve.assert_not_called()


@patch("polaris.kernelone.tool_execution.output.resolve_run_dir")
def test_persist_tool_raw_output_resolve_returns_none(mock_resolve: MagicMock, fake_state: FakeState) -> None:
    mock_resolve.return_value = None
    paths = persist_tool_raw_output(fake_state, "read_file", stdout_text="x")
    assert paths == {}


@patch("polaris.kernelone.tool_execution.output.resolve_run_dir")
@patch("polaris.kernelone.tool_execution.output.write_text_atomic")
@patch("os.makedirs")
def test_persist_tool_raw_output_sequence_increments(
    mock_makedirs: MagicMock, mock_write: MagicMock, mock_resolve: MagicMock, fake_state: FakeState
) -> None:
    mock_resolve.return_value = "/run_dir"
    persist_tool_raw_output(fake_state, "tool_a", stdout_text="1")
    assert fake_state._tool_output_seq == 1
    persist_tool_raw_output(fake_state, "tool_b", stdout_text="2")
    assert fake_state._tool_output_seq == 2


@patch("polaris.kernelone.tool_execution.output.resolve_run_dir")
@patch("polaris.kernelone.tool_execution.output.write_text_atomic")
@patch("os.makedirs")
def test_persist_tool_raw_output_sanitizes_tool_name(
    mock_makedirs: MagicMock, mock_write: MagicMock, mock_resolve: MagicMock, fake_state: FakeState
) -> None:
    mock_resolve.return_value = "/run_dir"
    paths = persist_tool_raw_output(fake_state, "tool/name!@#", stdout_text="x")
    filename = os.path.basename(paths["tool_stdout_path"])
    assert "tool_name_" in filename


# -----------------------------------------------------------------------------
# build_refs
# -----------------------------------------------------------------------------


class FakeStateWithRefs:
    def __init__(self) -> None:
        self.current_task_id = "task-1"
        self.current_task_fingerprint = "fp-abc"
        self.current_run_id = "run-1"
        self.current_pm_iteration = 2
        self.current_director_iteration = 3


class FakeStateEmptyRefs:
    def __init__(self) -> None:
        self.current_task_id = ""
        self.current_task_fingerprint = ""
        self.current_run_id = ""
        self.current_pm_iteration = None
        self.current_director_iteration = None


def test_build_refs_full() -> None:
    state = FakeStateWithRefs()
    refs = build_refs(state, "execution")
    assert refs["task_id"] == "task-1"
    assert refs["task_fingerprint"] == "fp-abc"
    assert refs["run_id"] == "run-1"
    assert refs["pm_iteration"] == 2
    assert refs["director_iteration"] == 3
    assert refs["phase"] == "execution"


def test_build_refs_empty_coerced_to_none() -> None:
    state = FakeStateEmptyRefs()
    refs = build_refs(state, "planning")
    assert refs["task_id"] is None
    assert refs["task_fingerprint"] is None
    assert refs["run_id"] is None
    assert refs["pm_iteration"] is None
    assert refs["director_iteration"] is None


def test_build_refs_missing_attrs() -> None:
    state = object()
    refs = build_refs(state, "test")
    assert refs["task_id"] is None
    assert refs["run_id"] is None
    assert refs["phase"] == "test"


# -----------------------------------------------------------------------------
# compact_tool_output
# -----------------------------------------------------------------------------


def test_compact_tool_output_dict_no_truncation() -> None:
    output = {"tool": "read", "content": ["line1", "line2"]}
    compact, trunc = compact_tool_output("read", output)
    assert compact == output
    assert trunc["truncated"] is False


def test_compact_tool_output_list_content_truncated() -> None:
    output = {"tool": "read", "content": ["line"] * (MAX_EVENT_CONTENT_LINES + 5)}
    compact, trunc = compact_tool_output("read", output)
    assert len(compact["content"]) == MAX_EVENT_CONTENT_LINES
    assert trunc["truncated"] is True
    assert trunc["reason"] == "content_lines"
    assert trunc["original_lines"] == MAX_EVENT_CONTENT_LINES + 5


def test_compact_tool_output_non_dict() -> None:
    compact, trunc = compact_tool_output("read", "raw string")
    assert compact == {"raw": "raw string"}
    assert trunc["truncated"] is False


def test_compact_tool_output_tool_truncated_flag() -> None:
    output = {"tool": "read", "content": ["a"], "truncated": True}
    _compact, trunc = compact_tool_output("read", output)
    assert trunc["truncated"] is True
    assert trunc["reason"] == "tool_truncated"


def test_compact_tool_output_no_content_key() -> None:
    output = {"tool": "read", "data": "value"}
    compact, trunc = compact_tool_output("read", output)
    assert compact == output
    assert trunc["truncated"] is False


# -----------------------------------------------------------------------------
# score_hit
# -----------------------------------------------------------------------------


def test_score_hit_def_bonus() -> None:
    score = score_hit("def foo():", "/src/main.py", [])
    assert score == 5


def test_score_hit_export_bonus() -> None:
    score = score_hit("export function foo() {}", "/src/main.ts", [])
    # Matches both \bfunction\b (+5) and \bexport\s+function\b (+5)
    assert score == 10


def test_score_hit_pattern_match() -> None:
    score = score_hit("foo bar baz", "/src/main.py", ["foo", "baz"])
    assert score == 6  # 3 per pattern


def test_score_hit_loops_bonus() -> None:
    score = score_hit("code", "/src/loops/main.py", [])
    assert score == 2


def test_score_hit_test_penalty() -> None:
    score = score_hit("code", "/src/tests/main.py", [])
    assert score == -3


def test_score_hit_md_penalty() -> None:
    score = score_hit("code", "/docs/readme.md", [])
    # /docs/ (-3) and .md (-3)
    assert score == -6


def test_score_hit_empty_pattern_skipped() -> None:
    score = score_hit("foo", "/src/main.py", ["", "foo"])
    assert score == 3


def test_score_hit_regex_error_ignored() -> None:
    score = score_hit("foo", "/src/main.py", ["["])  # invalid regex
    assert score == 0


# -----------------------------------------------------------------------------
# annotate_rg_output
# -----------------------------------------------------------------------------


def test_annotate_rg_output_basic() -> None:
    output = {
        "pattern": "foo",
        "hits": [
            {"text": "def foo():", "file": "/src/a.py", "line": 1},
            {"text": "bar", "file": "/src/b.py", "line": 2},
        ],
    }
    annotate_rg_output(output)
    ranked = output["ranked_hits"]
    assert len(ranked) == 2
    assert ranked[0]["score"] >= ranked[1]["score"]
    assert "best_hit" in output


def test_annotate_rg_output_no_hits() -> None:
    output = {"pattern": "foo", "hits": []}
    annotate_rg_output(output)
    assert "ranked_hits" not in output


def test_annotate_rg_output_invalid_hit_skipped() -> None:
    output = {
        "pattern": "foo",
        "hits": [
            {"text": "def foo():", "file": "/src/a.py", "line": 1},
            "not a dict",
        ],
    }
    annotate_rg_output(output)
    assert len(output["ranked_hits"]) == 1


def test_annotate_rg_output_takes_top_three() -> None:
    hits = [{"text": f"line {i}", "file": f"/src/{i}.py", "line": i} for i in range(10)]
    output = {"pattern": "line", "hits": hits}
    annotate_rg_output(output)
    assert len(output["ranked_hits"]) == 3


def test_annotate_rg_output_empty_pattern() -> None:
    output = {
        "pattern": "",
        "hits": [{"text": "foo", "file": "/src/a.py", "line": 1}],
    }
    annotate_rg_output(output)
    assert output["ranked_hits"][0]["score"] == 0


# -----------------------------------------------------------------------------
# analyze_slice_content
# -----------------------------------------------------------------------------


def test_analyze_slice_content_has_def() -> None:
    content = [{"t": "def foo():"}, {"t": "    pass"}]
    result = analyze_slice_content(content)
    assert result["has_def"] is True
    assert result["has_end"] is False


def test_analyze_slice_content_has_end() -> None:
    content = [{"t": "    return 42"}, {"t": "}"}]
    result = analyze_slice_content(content)
    assert result["has_def"] is False
    assert result["has_end"] is True


def test_analyze_slice_content_export_function() -> None:
    content = [{"t": "export function foo() {"}, {"t": "}"}]
    result = analyze_slice_content(content)
    assert result["has_def"] is True
    assert result["has_end"] is True


def test_analyze_slice_content_empty() -> None:
    result = analyze_slice_content([])
    assert result == {"has_def": False, "has_end": False}


def test_analyze_slice_content_non_dict_items() -> None:
    content = [{"t": "def foo():"}, "not a dict", 42]
    result = analyze_slice_content(content)
    assert result["has_def"] is True


# -----------------------------------------------------------------------------
# suggest_radius
# -----------------------------------------------------------------------------


def test_suggest_radius_not_truncated() -> None:
    assert suggest_radius(False, {"has_def": False}, 80) is None


def test_suggest_radius_no_def() -> None:
    assert suggest_radius(True, {"has_def": False}, 80) == 140


def test_suggest_radius_has_def_no_end() -> None:
    assert suggest_radius(True, {"has_def": True, "has_end": False}, 80) == 120


def test_suggest_radius_has_def_and_end() -> None:
    assert suggest_radius(True, {"has_def": True, "has_end": True}, 80) is None


def test_suggest_radius_uses_max() -> None:
    assert suggest_radius(True, {"has_def": False}, 200) == 200


# -----------------------------------------------------------------------------
# count_tool_output_lines
# -----------------------------------------------------------------------------


def test_count_tool_output_lines_read_tools() -> None:
    output = {"tool": "repo_read_slice", "content": [{"t": "a"}, {"t": "b"}]}
    assert count_tool_output_lines(output) == 2


def test_count_tool_output_lines_cache_hit() -> None:
    output = {"tool": "repo_read_slice", "cache_hit": True, "content": [{"t": "a"}]}
    assert count_tool_output_lines(output) == 0


def test_count_tool_output_lines_non_dict() -> None:
    assert count_tool_output_lines("string") == 0


def test_count_tool_output_lines_unsupported_tool() -> None:
    output = {"tool": "write_file", "content": [{"t": "a"}]}
    assert count_tool_output_lines(output) == 0


def test_count_tool_output_lines_content_not_list() -> None:
    output = {"tool": "repo_read_slice", "content": "not a list"}
    assert count_tool_output_lines(output) == 0


def test_count_tool_output_lines_all_read_tools() -> None:
    for tool in ("repo_read_around", "repo_read_slice", "repo_read_head", "repo_read_tail"):
        output = {"tool": tool, "content": [{"t": "x"}] * 5}
        assert count_tool_output_lines(output) == 5
