"""Weak-model edit_blocks compatibility (Blueprint B).

Covers the line-range affordance and payload normalization that let low-precision
local models (e.g. gemma-4-12B) land real edits without reproducing exact SEARCH text.
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import (
    _coerce_line_no,
    _handle_edit_blocks,
    _handle_read_file,
    _has_search_replace_markers,
    _normalize_block_input,
    _suggest_similar_paths,
)

SAMPLE = """class HttpResponse:
    def __init__(self, content=b""):
        self.content = content

    def serialize(self):
        return self.content
"""


def _executor(tmp_path: Path) -> AgentAccelToolExecutor:
    (tmp_path / "response.py").write_text(SAMPLE, encoding="utf-8")
    return AgentAccelToolExecutor(workspace=str(tmp_path))


# ----- pure helpers -----


def test_normalize_strips_code_fence() -> None:
    text = "```python\n<<<< SEARCH:f.py\na\n====\nb\n>>>> REPLACE\n```"
    out = _normalize_block_input(text)
    assert not out.lstrip().startswith("```")
    assert "<<<< SEARCH" in out


def test_normalize_unescapes_when_no_real_newlines() -> None:
    out = _normalize_block_input("<<<< SEARCH:f.py\\na\\n====\\nb\\n>>>> REPLACE")
    assert "\n" in out and "\\n" not in out


def test_normalize_preserves_real_newlines() -> None:
    text = "line1\nliteral backslash-n stays: \\n inside\nline3"
    assert _normalize_block_input(text) == text


def test_has_search_replace_markers() -> None:
    assert _has_search_replace_markers("<<<< SEARCH:f.py\na\n====\nb\n>>>> REPLACE")
    assert _has_search_replace_markers("<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE")
    assert not _has_search_replace_markers("just some prose about the fix")


def test_coerce_line_no() -> None:
    assert _coerce_line_no("278") == 278
    assert _coerce_line_no(5) == 5
    assert _coerce_line_no("x") is None
    assert _coerce_line_no(None) is None


# ----- line-range affordance (end to end) -----


def test_line_range_edit_applies(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    result = _handle_edit_blocks(
        ex,
        file="response.py",
        start=2,
        end=3,
        replace='    def __init__(self, content=b""):\n        self.content = bytes(content)\n',
    )
    assert result.get("ok") is True, result
    text = (tmp_path / "response.py").read_text(encoding="utf-8")
    assert "self.content = bytes(content)" in text
    # untouched lines remain
    assert "def serialize(self):" in text


def test_line_range_edit_via_new_text_alias(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    result = _handle_edit_blocks(
        ex, file="response.py", start_line=6, end_line=6, new_text="        return bytes(self.content)\n"
    )
    assert result.get("ok") is True, result
    assert "return bytes(self.content)" in (tmp_path / "response.py").read_text(encoding="utf-8")


def test_line_range_empty_replacement_rejected(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    result = _handle_edit_blocks(ex, file="response.py", start=2, end=3, replace="   ")
    assert result.get("ok") is False
    assert "no replacement code" in result.get("error", "").lower()


def test_line_range_invalid_range_rejected(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    result = _handle_edit_blocks(ex, file="response.py", start=99, end=120, replace="x = 1\n")
    assert result.get("ok") is False
    assert "invalid line range" in result.get("error", "").lower()


def test_line_range_missing_file_rejected(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    result = _handle_edit_blocks(ex, file="nope.py", start=1, end=1, replace="x = 1\n")
    assert result.get("ok") is False
    assert "not found" in result.get("error", "").lower()


# ----- regression: classic SEARCH/REPLACE still works -----


def test_classic_search_replace_still_works(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    blocks = (
        "<<<< SEARCH:response.py\n        return self.content\n====\n        return bytes(self.content)\n>>>> REPLACE\n"
    )
    result = _handle_edit_blocks(ex, file="response.py", blocks=blocks)
    assert result.get("ok") is True, result
    assert "return bytes(self.content)" in (tmp_path / "response.py").read_text(encoding="utf-8")


# ----- Phase 2: prose-blocks teaching error -----


def test_prose_blocks_returns_teaching_error(tmp_path: Path) -> None:
    """Narration passed as 'blocks' (live-captured Qwen3.6 shape) must teach both forms."""
    ex = _executor(tmp_path)
    prose = "Let me first read the main entry point to understand the codebase structure, then apply a fix."
    result = _handle_edit_blocks(ex, blocks=prose)
    assert result.get("ok") is False
    error = result.get("error", "")
    assert "prose/narration" in error
    assert "Let me first read" in error  # echoes what the model sent
    suggestion = result.get("suggestion", "")
    assert '"start"' in suggestion and '"replace"' in suggestion  # line-range form
    assert "<<<< SEARCH" in suggestion and ">>>> REPLACE" in suggestion  # block form


def test_malformed_markers_keep_generic_error(tmp_path: Path) -> None:
    """Input WITH markers that still parses to zero blocks is not mislabeled as prose."""
    ex = _executor(tmp_path)
    result = _handle_edit_blocks(ex, blocks="<<<< SEARCH:response.py\nonly a search, no divider")
    assert result.get("ok") is False
    error = result.get("error", "")
    assert "prose/narration" not in error


# ----- Phase 2: did-you-mean path candidates -----


def _nested_workspace(tmp_path: Path) -> AgentAccelToolExecutor:
    target = tmp_path / "django" / "core" / "checks" / "model_checks.py"
    target.parent.mkdir(parents=True)
    target.write_text("E028 = 'check'\n", encoding="utf-8")
    other = tmp_path / "docs" / "model_checks.py"
    other.parent.mkdir(parents=True)
    other.write_text("# unrelated\n", encoding="utf-8")
    return AgentAccelToolExecutor(workspace=str(tmp_path))


def test_read_file_not_found_suggests_candidates(tmp_path: Path) -> None:
    ex = _nested_workspace(tmp_path)
    result = _handle_read_file(ex, file="src/django/core/checks/model_checks.py")
    assert result.get("ok") is False
    error = result.get("error", "")
    assert "Did you mean" in error
    assert "django/core/checks/model_checks.py" in error


def test_suggest_similar_paths_ranks_by_trailing_overlap(tmp_path: Path) -> None:
    ex = _nested_workspace(tmp_path)
    candidates = _suggest_similar_paths(ex, "src/django/core/checks/model_checks.py")
    assert candidates[0] == "django/core/checks/model_checks.py"
    # Relevance gate (run20): basename-only matches no longer qualify for a
    # multi-component request — they redirect weak models into unrelated files.
    assert "docs/model_checks.py" not in candidates


def test_not_found_without_candidates_keeps_exploration_advice(tmp_path: Path) -> None:
    ex = _nested_workspace(tmp_path)
    result = _handle_read_file(ex, file="totally/unknown_module_xyz.py")
    assert result.get("ok") is False
    assert "Did you mean" not in result.get("error", "")
    assert 'repo_rg("unknown_module_xyz")' in result.get("suggestion", "")


def test_multi_component_request_with_basename_only_matches_gets_no_redirect(tmp_path: Path) -> None:
    """run20 regression (django-15213 shape): src/main.py must NOT be redirected
    to a deep unrelated main.py — that suggestion became the final wrong patch."""
    deep = tmp_path / "django" / "contrib" / "admin" / "views" / "main.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("# admin changelist\n", encoding="utf-8")
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    result = _handle_read_file(ex, file="src/main.py")
    assert result.get("ok") is False
    assert "Did you mean" not in result.get("error", "")
    assert 'repo_rg("main")' in result.get("suggestion", "")


def test_bare_name_request_only_suggests_shallow_candidates(tmp_path: Path) -> None:
    """run20 regression (README.md shape): a bare conventional name must not be
    redirected into a deep unrelated subtree; a shallow real match still works."""
    deep = tmp_path / "docs" / "theme" / "static" / "fontawesome" / "README.md"
    deep.parent.mkdir(parents=True)
    deep.write_text("# vendored\n", encoding="utf-8")
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    no_redirect = _handle_read_file(ex, file="README.md")
    assert "Did you mean" not in no_redirect.get("error", "")

    shallow = tmp_path / "docs" / "README.md"
    shallow.write_text("# real docs index\n", encoding="utf-8")
    suggested = _handle_read_file(ex, file="README.md")
    assert "Did you mean: docs/README.md?" in suggested.get("error", "")
    assert "fontawesome" not in suggested.get("error", "")


def test_mistyped_directory_still_rescued_when_one_component_matches(tmp_path: Path) -> None:
    """A genuinely-corroborated candidate (>= 1 directory component beyond the
    basename) keeps the did-you-mean rescue path alive."""
    target = tmp_path / "app" / "src" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('x')\n", encoding="utf-8")
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    result = _handle_read_file(ex, file="src/main.py")
    assert "Did you mean: app/src/main.py?" in result.get("error", "")


def test_edit_blocks_not_found_target_suggests_candidates(tmp_path: Path) -> None:
    ex = _nested_workspace(tmp_path)
    blocks = "<<<< SEARCH:src/django/core/checks/model_checks.py\nE028 = 'check'\n====\nE028 = 'fixed'\n>>>> REPLACE\n"
    result = _handle_edit_blocks(ex, file="src/django/core/checks/model_checks.py", blocks=blocks)
    assert result.get("ok") is False
    assert "Did you mean" in result.get("error", "")


# ----- Wave-4: hallucinated foreign absolute paths -----


def test_foreign_absolute_path_returns_teaching_error_with_candidates(tmp_path: Path) -> None:
    """Live capture (Qwen3.6, run20): reads of '/Users/joey/workspace/polaris/...' on a
    Linux host burned the failure budget and collateral-blocked correct-path reads."""
    ex = _nested_workspace(tmp_path)
    result = _handle_read_file(ex, file="/Users/joey/workspace/polaris/django/core/checks/model_checks.py")
    assert result.get("ok") is False
    error = result.get("error", "")
    assert "WORKSPACE-RELATIVE" in error
    assert "django/core/checks/model_checks.py" in error  # did-you-mean from basename


def test_foreign_absolute_path_classified_recoverable() -> None:
    from polaris.kernelone.tool_execution.error_classifier import ToolErrorClassifier

    classifier = ToolErrorClassifier()
    pattern = classifier.classify("read_file", "UNSUPPORTED_PATH_PREFIX: /Users/joey/workspace/x.py")
    assert pattern.error_type == "not_found"  # argument-recoverable -> no tool-level budget block


def test_absolute_path_inside_workspace_still_works(tmp_path: Path) -> None:
    ex = _nested_workspace(tmp_path)
    result = _handle_read_file(ex, file=str(tmp_path / "django" / "core" / "checks" / "model_checks.py"))
    assert result.get("ok") is True, result


def test_edit_blocks_foreign_absolute_path_teaches(tmp_path: Path) -> None:
    ex = _nested_workspace(tmp_path)
    blocks = "<<<< SEARCH\nE028 = 'check'\n====\nE028 = 'fixed'\n>>>> REPLACE\n"
    result = _handle_edit_blocks(ex, file="/Users/joey/repo/django/core/checks/model_checks.py", blocks=blocks)
    assert result.get("ok") is False
    assert "WORKSPACE-RELATIVE" in result.get("error", "")
