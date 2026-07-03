"""Weak-model edit_blocks compatibility (Blueprint B).

Covers the line-range affordance and payload normalization that let low-precision
local models (e.g. gemma-4-12B) land real edits without reproducing exact SEARCH text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import (
    _coerce_line_no,
    _handle_edit_blocks,
    _handle_edit_file,
    _handle_read_file,
    _handle_write_file,
    _has_search_replace_markers,
    _normalize_block_input,
    _suggest_similar_paths,
)
from polaris.kernelone.tool_execution.code_validator import validate_code_syntax

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


def test_file_marker_blocks_create_new_file_via_write_file_gate(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    blocks = """FILE: src/engine/simulation.ts
export interface Firefly {
  x: number;
  y: number;
  brightness: number;
}

export function fireflyCount(items: Firefly[]): number {
  return items.length;
}
"""

    result = _handle_edit_blocks(ex, blocks=blocks)

    assert result.get("ok") is True, result
    assert result.get("normalized_from_edit_blocks_file_marker") is True
    assert (tmp_path / "src" / "engine" / "simulation.ts").read_text(encoding="utf-8") == blocks.split("\n", 1)[1]


@pytest.mark.parametrize(
    ("header", "expected_file"),
    [
        ("file: src/engine/simulation.ts", "src/engine/simulation.ts"),
        ("FILE = src/engine/simulation.ts", "src/engine/simulation.ts"),
        ("path: src/engine/simulation.ts // whole module", "src/engine/simulation.ts"),
        ("FILE:\nsrc/engine/simulation.ts", "src/engine/simulation.ts"),
        ("src/engine/simulation.ts", "src/engine/simulation.ts"),
    ],
)
def test_file_marker_variants_create_new_file_via_write_file_gate(
    tmp_path: Path,
    header: str,
    expected_file: str,
) -> None:
    ex = _executor(tmp_path)
    body = """export interface Firefly {
  x: number;
  y: number;
  brightness: number;
}

export function createFirefly(): Firefly {
  return { x: 1, y: 2, brightness: 0.8 };
}
"""
    result = _handle_edit_blocks(ex, blocks=f"{header}\n{body}")

    assert result.get("ok") is True, result
    assert result.get("normalized_from_edit_blocks_file_marker") is True
    assert (tmp_path / expected_file).read_text(encoding="utf-8") == body


def test_default_file_with_whole_file_body_routes_to_write_file_gate(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    body = """export interface Flower {
  id: string;
  nectar: number;
}

export function flowerNectar(flower: Flower): number {
  return flower.nectar;
}
"""
    result = _handle_edit_blocks(ex, file="src/models/flower.ts", blocks=body)

    assert result.get("ok") is True, result
    assert result.get("normalized_from_edit_blocks_file_marker") is True
    assert (tmp_path / "src" / "models" / "flower.ts").read_text(encoding="utf-8") == body


def test_file_marker_blocks_replace_existing_file(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    replacement = """class HttpResponse:
    def __init__(self, content=b""):
        self.content = bytes(content)

    def serialize(self):
        return bytes(self.content)
"""
    blocks = f"FILE: response.py\n{replacement}"

    result = _handle_edit_blocks(ex, blocks=blocks)

    assert result.get("ok") is True, result
    assert (tmp_path / "response.py").read_text(encoding="utf-8") == replacement


def test_tiny_line_range_whole_file_replacement_from_file_start_is_normalized(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    replacement = """class HttpResponse:
    def __init__(self, content=b""):
        self.content = bytes(content)
        self.headers = {}

    def serialize(self):
        return bytes(self.content)


def add_header(response, name, value):
    response.headers[name] = value
    return response


def build_response(content):
    return HttpResponse(content)
"""
    result = _handle_edit_blocks(ex, file="response.py", start=1, end=1, replace=replacement)

    assert result.get("ok") is True, result
    assert (tmp_path / "response.py").read_text(encoding="utf-8") == replacement


def test_tiny_line_range_whole_file_replacement_from_middle_rejected(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    replacement = """class HttpResponse:
    def __init__(self, content=b""):
        self.content = bytes(content)
        self.headers = {}

    def serialize(self):
        return bytes(self.content)


def add_header(response, name, value):
    response.headers[name] = value
    return response


def build_response(content):
    return HttpResponse(content)
"""
    result = _handle_edit_blocks(ex, file="response.py", start=2, end=2, replace=replacement)

    assert result.get("ok") is False
    assert result.get("error_type") == "line_range_whole_file_mismatch"
    assert "only 1 of" in result.get("error", "")
    assert (tmp_path / "response.py").read_text(encoding="utf-8") == SAMPLE


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
    assert result.get("error_type") == "prose_narration_in_edit_blocks"
    assert result.get("retryable") is True
    assert result.get("tool") == "edit_blocks"


def test_malformed_markers_keep_generic_error(tmp_path: Path) -> None:
    """Input WITH markers that still parses to zero blocks is not mislabeled as prose."""
    ex = _executor(tmp_path)
    result = _handle_edit_blocks(ex, blocks="<<<< SEARCH:response.py\nonly a search, no divider")
    assert result.get("ok") is False
    error = result.get("error", "")
    assert "prose/narration" not in error
    assert result.get("error_type") == "no_valid_edit_blocks"
    assert result.get("retryable") is True


def test_update_marker_blocks_are_normalized_and_applied(tmp_path: Path) -> None:
    """Factory-bench capture: qwen used conflict-style UPDATE markers."""
    ex = _executor(tmp_path)
    blocks = (
        "<<<<<<< UPDATE response.py\n"
        "    def serialize(self):\n"
        "        return self.content\n"
        "=======\n"
        "    def serialize(self):\n"
        "        return bytes(self.content)\n"
        ">>>>>>> UPDATE\n"
    )

    result = _handle_edit_blocks(ex, file="response.py", blocks=blocks)

    assert result.get("ok") is True, result
    assert "return bytes(self.content)" in (tmp_path / "response.py").read_text(encoding="utf-8")


def test_replace_marker_whole_file_wrapper_is_normalized(tmp_path: Path) -> None:
    """Factory-bench capture: qwen used REPLACE[:file] as a whole-file wrapper."""
    readme = tmp_path / "README.md"
    readme.write_text("# Old\n\nRun `python calculator.py`.\n", encoding="utf-8")
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    replacement = (
        "# 表达式计算器 (calculator)\n\n"
        "支持四则运算的数学表达式解析与求值工具。\n\n"
        "## 快速开始\n\n"
        "```bash\n"
        "python calculator.py\n"
        "```\n"
    )
    blocks = f"<<<<<<< REPLACE[:README.md]\n{replacement}>>>>>>> REPLACE\n"

    result = _handle_edit_blocks(ex, file="README.md", blocks=blocks)

    assert result.get("ok") is True, result
    text = readme.read_text(encoding="utf-8")
    assert "表达式计算器" in text
    assert "python calculator.py" in text


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


def test_read_file_not_found_suggests_typescript_source_for_js_import_path(tmp_path: Path) -> None:
    target = tmp_path / "src" / "models" / "Market.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export function createMarket() {}\n", encoding="utf-8")
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))

    result = _handle_read_file(ex, file="src/models/Market.js")

    assert result.get("ok") is False
    assert "Did you mean" in result.get("error", "")
    assert "src/models/Market.ts" in result.get("error", "")
    assert "src/models/Market.ts" in result.get("suggestion", "")


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


# ----- Phase-1 A4 slice: destructive-shrink gate -----


def _big_file_workspace(tmp_path: Path, lines: int = 300) -> AgentAccelToolExecutor:
    target = tmp_path / "pkg" / "big_module.py"
    target.parent.mkdir(parents=True)
    target.write_text("".join(f"line_{i} = {i}\n" for i in range(lines)), encoding="utf-8")
    return AgentAccelToolExecutor(workspace=str(tmp_path))


def test_line_range_destructive_shrink_rejected(tmp_path: Path) -> None:
    """phase1smoke5 regression: 1403 lines replaced by 17 must be refused."""
    ex = _big_file_workspace(tmp_path)
    result = _handle_edit_blocks(ex, file="pkg/big_module.py", start=1, end=250, replace="fixed = True\n")
    assert result.get("ok") is False
    assert result.get("error_type") == "destructive_shrink"
    assert result.get("retryable") is True
    assert "Narrow start/end" in result.get("suggestion", "")


def test_narrow_line_range_edit_still_works(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_edit_blocks(
        ex, file="pkg/big_module.py", start=10, end=12, replace="line_10 = 100\nline_11 = 110\nline_12 = 120\n"
    )
    assert result.get("ok") is True


def test_large_range_with_proportional_replacement_allowed(tmp_path: Path) -> None:
    """A genuine large refactor (similar size in/out) is not a shrink."""
    ex = _big_file_workspace(tmp_path)
    replacement = "".join(f"new_line_{i} = {i}\n" for i in range(120))
    result = _handle_edit_blocks(ex, file="pkg/big_module.py", start=1, end=150, replace=replacement)
    assert result.get("ok") is True


def test_write_file_overwrite_shrink_rejected(tmp_path: Path) -> None:
    """run20 regression: 539-line file overwritten by 32 lines must be refused."""
    ex = _big_file_workspace(tmp_path, lines=539)
    result = _handle_write_file(ex, file="pkg/big_module.py", content="tiny = 1\n" * 32)
    assert result.get("ok") is False
    assert result.get("error_type") == "destructive_shrink"
    assert "partial edit" in result.get("suggestion", "")
    assert "complete file body" in result.get("suggestion", "")


def test_write_file_rejects_source_narration_contamination(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_write_file(
        ex,
        file="pkg/main.ts",
        content="I'll address the quality repair issues immediately.\nexport const ready = true;\n",
    )

    assert result.get("ok") is False
    assert result.get("error_type") == "source_narration_contamination"
    assert result.get("retryable") is True
    assert not (tmp_path / "pkg" / "main.ts").exists()


def test_write_file_rejects_repair_directive_narration_contamination(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_write_file(
        ex,
        file="pkg/moonphase.ts",
        content=(
            "The repair directive is clear: create the missing module imported by src/index.ts.\n"
            "For moonphase.ts - should export moon phase related types/classes.\n"
        ),
    )

    assert result.get("ok") is False
    assert result.get("error_type") == "source_narration_contamination"
    assert result.get("retryable") is True
    assert not (tmp_path / "pkg" / "moonphase.ts").exists()


def test_write_file_rejects_quality_repair_mode_narration_contamination(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_write_file(
        ex,
        file="pkg/main.ts",
        content=(
            "The quality repair mode requires me to create the missing files. Let me analyze what's needed:\n"
            "1. `src/main.ts` - Missing target file\n"
        ),
    )

    assert result.get("ok") is False
    assert result.get("error_type") == "source_narration_contamination"
    assert result.get("retryable") is True
    assert not (tmp_path / "pkg" / "main.ts").exists()


def test_write_file_new_file_unaffected(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_write_file(ex, file="pkg/fresh.py", content="x = 1\n")
    assert result.get("ok") is True


def test_write_file_normalizes_fenced_package_json(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_write_file(
        ex,
        file="package.json",
        content='```json\n"name": "demo",\n"scripts": {"build": "tsc"}\n}\n```',
    )

    assert result.get("ok") is True
    payload = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert payload["name"] == "demo"
    assert payload["scripts"]["build"] == "tsc"
    assert result.get("normalized_patch_like_write") is True


def test_write_file_normalizes_package_json_with_trailing_fence(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_write_file(
        ex,
        file="package.json",
        content='{"name":"demo","scripts":{"build":"tsc"}}\n```',
    )

    assert result.get("ok") is True
    payload = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert payload["name"] == "demo"
    assert payload["scripts"]["build"] == "tsc"
    assert result.get("normalized_patch_like_write") is True


def test_write_file_rejects_incomplete_empty_package_json_fragment(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_write_file(ex, file="package.json", content="{  ")

    assert result.get("ok") is False
    assert "invalid JSON" in str(result.get("error") or "")
    assert not (tmp_path / "package.json").exists()


def test_write_file_normalizes_missing_json_key_opening_quote(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path)
    result = _handle_write_file(
        ex,
        file="package.json",
        content='name": "demo",\n"scripts": {"test": "node verify.js"}\n}',
    )

    assert result.get("ok") is True
    payload = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert payload["scripts"]["test"] == "node verify.js"


def test_write_file_small_existing_file_overwrite_allowed(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path, lines=20)
    result = _handle_write_file(ex, file="pkg/big_module.py", content="rewritten = True\n")
    assert result.get("ok") is True


# ----- Phase-1 W1.11: JSON-in-blocks normalization (phase1smoke6 live capture) -----


def test_json_array_line_range_inside_blocks_applies(tmp_path: Path) -> None:
    """The exact phase1smoke6 shape: a structured JSON edit inside `blocks`
    must be normalized and applied, not rejected as prose."""
    ex = _big_file_workspace(tmp_path, lines=30)
    payload = (
        '[{"start_line": 5, "end_line": 6, "file": "pkg/big_module.py", "replace": "line_4 = 400\\nline_5 = 500\\n"}]'
    )
    result = _handle_edit_blocks(ex, blocks=payload)
    assert result.get("ok") is True, result
    content = (tmp_path / "pkg" / "big_module.py").read_text(encoding="utf-8")
    assert "line_4 = 400" in content


def test_json_array_line_range_accepts_target_file_alias(tmp_path: Path) -> None:
    """Some LLMs use target_file instead of file inside JSON edit payloads."""
    ex = _big_file_workspace(tmp_path, lines=30)
    payload = '[{"start_line": 7, "end_line": 7, "target_file": "pkg/big_module.py", "replace": "line_6 = 600\\n"}]'

    result = _handle_edit_blocks(ex, blocks=payload)

    assert result.get("ok") is True, result
    content = (tmp_path / "pkg" / "big_module.py").read_text(encoding="utf-8")
    assert "line_6 = 600" in content


def test_json_object_uses_top_level_file_fallback(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path, lines=30)
    payload = '{"start": 3, "end": 3, "replace": "patched = True\\n"}'
    result = _handle_edit_blocks(ex, file="pkg/big_module.py", blocks=payload)
    assert result.get("ok") is True, result
    assert "patched = True" in (tmp_path / "pkg" / "big_module.py").read_text(encoding="utf-8")


def test_json_multi_edit_array_applies_all(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path, lines=40)
    payload = (
        '[{"start_line": 2, "end_line": 2, "file": "pkg/big_module.py", "replace": "first = 1\\n"},'
        ' {"start_line": 30, "end_line": 30, "file": "pkg/big_module.py", "replace": "second = 2\\n"}]'
    )
    result = _handle_edit_blocks(ex, blocks=payload)
    assert result.get("ok") is True, result
    content = (tmp_path / "pkg" / "big_module.py").read_text(encoding="utf-8")
    assert "first = 1" in content and "second = 2" in content


def test_json_target_path_new_code_whole_file_applies(tmp_path: Path) -> None:
    """Factory-bench capture: [{"targetPath": ..., "newCode": "..."}]."""
    (tmp_path / "calculator.py").write_text(
        '"""Old calculator."""\n\n'
        "class CalculatorError(Exception):\n"
        "    pass\n\n"
        "def subtract(a: float, b: float) -> float:\n"
        "    return a - b\n",
        encoding="utf-8",
    )
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    new_code = (
        '"""Calculator core module."""\n\n'
        "class CalculatorError(Exception):\n"
        "    pass\n\n"
        "def add(a: float, b: float) -> float:\n"
        "    return a + b\n\n"
        "def subtract(a: float, b: float) -> float:\n"
        "    return a - b\n\n"
        "def multiply(a: float, b: float) -> float:\n"
        "    return a * b\n\n"
        "def divide(a: float, b: float) -> float:\n"
        "    if b == 0:\n"
        "        raise CalculatorError('Division by zero')\n"
        "    return a / b\n"
    )
    payload = json.dumps([{"targetPath": "calculator.py", "newCode": new_code}])

    result = _handle_edit_blocks(ex, blocks=payload)

    assert result.get("ok") is True, result
    content = (tmp_path / "calculator.py").read_text(encoding="utf-8")
    assert "def multiply" in content
    assert "Old calculator" not in content


def test_existing_file_full_source_in_blocks_applies(tmp_path: Path) -> None:
    """Factory-bench capture: complete source was passed directly as blocks."""
    (tmp_path / "calculator.py").write_text(
        '"""Old calculator."""\n\nclass CalculatorError(Exception):\n    pass\n',
        encoding="utf-8",
    )
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    full_source = (
        '"""Core calculation engine with recursive descent parser."""\n\n'
        "from __future__ import annotations\n"
        "import re\n\n"
        "class Token:\n"
        "    def __init__(self, type_: str, value: str):\n"
        "        self.type = type_\n"
        "        self.value = value\n\n"
        "class ASTNode:\n"
        "    pass\n\n"
        "class BinaryOp(ASTNode):\n"
        "    def __init__(self, left: ASTNode, op: str, right: ASTNode):\n"
        "        self.left = left\n"
        "        self.op = op\n"
        "        self.right = right\n\n"
        "def tokenize(expression: str) -> list[Token]:\n"
        "    return [Token('NUM', item) for item in re.findall(r'\\d+', expression)]\n"
    )

    result = _handle_edit_blocks(ex, file="calculator.py", blocks=full_source)

    assert result.get("ok") is True, result
    content = (tmp_path / "calculator.py").read_text(encoding="utf-8")
    assert "class BinaryOp" in content
    assert "Old calculator" not in content


def test_json_edit_inherits_destructive_shrink_gate(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path, lines=300)
    payload = '[{"start_line": 1, "end_line": 250, "file": "pkg/big_module.py", "replace": "gutted = True\\n"}]'
    result = _handle_edit_blocks(ex, blocks=payload)
    assert result.get("ok") is False
    assert result.get("error_type") == "destructive_shrink"


def test_non_edit_json_still_falls_through_to_prose_guard(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path, lines=10)
    result = _handle_edit_blocks(ex, file="pkg/big_module.py", blocks='{"plan": "I will fix the bug"}')
    assert result.get("ok") is False
    assert result.get("error_type") != "destructive_shrink"


def test_prose_in_blocks_still_rejected(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path, lines=10)
    result = _handle_edit_blocks(
        ex, file="pkg/big_module.py", blocks="Adding the missing method to the class to fix the bug"
    )
    assert result.get("ok") is False
    assert "prose/narration" in result.get("error", "")


# ----- Factory-bench L1-01 live captures: nested blocks + new-file misuse -----


def test_nested_blocks_json_with_markers_unwraps_and_applies(tmp_path: Path) -> None:
    """[{"blocks": "<SEARCH/REPLACE payload>"}] must unwrap, not hit the prose guard."""
    ex = _big_file_workspace(tmp_path, lines=10)
    payload = '[{"blocks": "<<<< SEARCH:pkg/big_module.py\\nline_3 = 3\\n====\\nline_3 = 333\\n>>>> REPLACE\\n"}]'
    result = _handle_edit_blocks(ex, blocks=payload)
    assert result.get("ok") is True, result
    assert "line_3 = 333" in (tmp_path / "pkg" / "big_module.py").read_text(encoding="utf-8")


def test_nested_blocks_json_with_line_range_list_applies(tmp_path: Path) -> None:
    """Factory-bench L1-01 shape: [{"file": "...", "blocks": [{start,end,replace}]}]."""
    ex = _big_file_workspace(tmp_path, lines=10)
    payload = json.dumps(
        [
            {
                "file": "pkg/big_module.py",
                "blocks": [
                    {"filepath": "pkg/big_module.py", "start": 2, "end": 2, "replace": "line_2 = 222\n"},
                    {"start": 3, "end": 3, "replace": "line_3 = 333\n"},
                ],
            }
        ]
    )

    result = _handle_edit_blocks(ex, blocks=payload)

    assert result.get("ok") is True, result
    content = (tmp_path / "pkg" / "big_module.py").read_text(encoding="utf-8")
    assert "line_2 = 222" in content
    assert "line_3 = 333" in content


def test_new_file_via_edit_blocks_teaches_write_file(tmp_path: Path) -> None:
    """The L1-01 chain shape: whole-file code stuffed into blocks for a file
    that does not exist must teach write_file, not read_file."""
    ex = _big_file_workspace(tmp_path, lines=5)
    result = _handle_edit_blocks(
        ex,
        file="calculator.py",
        blocks='[{"blocks": "import sys\\nimport re\\ndef validate_input(expression):\\n    return expression\\n"}]',
    )
    assert result.get("ok") is False
    assert result.get("error_type") == "new_file_via_edit_blocks"
    assert "write_file" in result.get("error", "")
    assert result.get("retryable") is True


def test_prose_on_existing_file_keeps_prose_guard(tmp_path: Path) -> None:
    ex = _big_file_workspace(tmp_path, lines=5)
    result = _handle_edit_blocks(ex, file="pkg/big_module.py", blocks="I plan to refactor this module")
    assert result.get("ok") is False
    assert "prose/narration" in result.get("error", "")


def test_filename_plus_fence_teaches_write_file(tmp_path: Path) -> None:
    """factory-bench README capture: 'README.md ```markdown ...```' in blocks."""
    ex = _big_file_workspace(tmp_path, lines=5)
    (tmp_path / "README.md").write_text("# old\n", encoding="utf-8")
    result = _handle_edit_blocks(
        ex, blocks="README.md\n```markdown\n# CLI Calculator\n运行: python calculator.py\n```\n"
    )
    assert result.get("ok") is False
    assert result.get("error_type") == "whole_file_via_edit_blocks"
    assert '"file": "README.md"' in result.get("suggestion", "")


def test_yaml_label_prefixed_json_blocks_applies(tmp_path: Path) -> None:
    """L1-05 live shape #4: 'blocks: [{"path": ..., "start": ...}]'."""
    ex = _big_file_workspace(tmp_path, lines=20)
    payload = 'blocks: [ { "path": "pkg/big_module.py", "start": 2, "end": 2, "replace": "fixed = 2\\n" } ]'
    result = _handle_edit_blocks(ex, blocks=payload)
    assert result.get("ok") is True, result
    assert "fixed = 2" in (tmp_path / "pkg" / "big_module.py").read_text(encoding="utf-8")


# ----- Phase-1 A5: pre-write syntax gate -----


def test_write_file_attaches_js_syntax_diagnostic(tmp_path: Path) -> None:
    """L2-09 live regression: a `;` where `,` belongs in an object literal
    must come back as a syntax diagnostic without landing invalid JS."""
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    bad_js = "const head = {\n  x: 1,\n  y: 2;\n};\n"
    result = _handle_write_file(ex, file="game.js", content=bad_js)
    assert result.get("ok") is False
    assert "Code syntax validation failed" in result.get("error", "")
    assert "game.js" in result.get("error", "")
    assert "Unexpected" in result.get("error", "")
    assert not (tmp_path / "game.js").exists()


def test_write_file_clean_python_passes_syntax(tmp_path: Path) -> None:
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    result = _handle_write_file(ex, file="calc.py", content="def add(a, b):\n    return a + b\n")
    assert result.get("ok") is True
    assert result.get("syntax_check") == "passed"


def test_write_file_sanitizes_jsdoc_glob_before_ts_syntax_gate(tmp_path: Path) -> None:
    """Glob examples like src/**/*.ts contain */ and must not close JSDoc."""

    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    content = """/**
 * Verifies source_target_coverage: src/**/*.ts is covered.
 */

export function main(): string {
  return "flight";
}
"""

    result = _handle_write_file(ex, file="src/verify.ts", content=content)

    assert result.get("ok") is True, result
    assert result.get("block_comment_glob_sanitized") is True
    written = (tmp_path / "src" / "verify.ts").read_text(encoding="utf-8")
    assert "src/** /*.ts" in written
    assert "src/**/*.ts" not in written
    assert validate_code_syntax(written, "src/verify.ts").is_valid


def test_edit_blocks_sanitizes_jsdoc_glob_before_ts_syntax_gate(tmp_path: Path) -> None:
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    target = tmp_path / "src" / "verify.ts"
    target.parent.mkdir(parents=True)
    target.write_text(
        """/**
 * Verifies source_target_coverage.
 */

export function main(): string {
  return "flight";
}
""",
        encoding="utf-8",
    )
    blocks = """<<<< SEARCH:src/verify.ts
 * Verifies source_target_coverage.
====
 * Verifies source_target_coverage: src/**/*.ts is covered.
>>>> REPLACE
"""

    result = _handle_edit_blocks(ex, blocks=blocks)

    assert result.get("ok") is True, result
    written = target.read_text(encoding="utf-8")
    assert "src/** /*.ts" in written
    assert "src/**/*.ts" not in written
    assert validate_code_syntax(written, "src/verify.ts").is_valid


def test_write_file_does_not_sanitize_glob_outside_jsdoc(tmp_path: Path) -> None:
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    content = """/* normal comment */
export const pattern = "src/**/*.ts";
"""

    result = _handle_write_file(ex, file="src/pattern.ts", content=content)

    assert result.get("ok") is True, result
    assert result.get("block_comment_glob_sanitized") is not True
    written = (tmp_path / "src" / "pattern.ts").read_text(encoding="utf-8")
    assert 'export const pattern = "src/**/*.ts";' in written


def test_write_file_clean_rust_allows_arrows_and_generics(tmp_path: Path) -> None:
    """Rust pre-write validation must not treat `->` or generics as bracket errors."""

    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    content = (
        "use std::collections::HashMap;\n\n"
        "pub fn labels(items: Vec<String>) -> HashMap<String, String> {\n"
        "    let mut map = HashMap::<String, String>::new();\n"
        "    if items.len() > 0 {\n"
        '        map.insert("count".to_string(), items.len().to_string());\n'
        "    }\n"
        "    map\n"
        "}\n"
    )

    result = _handle_write_file(ex, file="src/engine/mapper.rs", content=content)

    assert result.get("ok") is True
    assert "Code syntax validation failed" not in result.get("error", "")
    assert (tmp_path / "src" / "engine" / "mapper.rs").read_text(encoding="utf-8") == content


def test_write_file_broken_python_blocked_by_pre_write_guard(tmp_path: Path) -> None:
    """Division of labor: .py is blocked BEFORE the write by PreWriteGuard
    (ok=False + validation_errors); the post-write gate covers languages the
    pre-write guard does not parse (.js — the L2-09 vector)."""
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    result = _handle_write_file(ex, file="bad.py", content="def add(a, b:\n    return\n")
    assert result.get("ok") is False
    assert result.get("validation_errors")
    assert not (tmp_path / "bad.py").exists()


def test_write_file_unknown_extension_no_check(tmp_path: Path) -> None:
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    result = _handle_write_file(ex, file="notes.md", content="# hello\n")
    assert result.get("ok") is True
    assert "syntax_check" not in result


def test_write_file_truncated_html_suggests_append(tmp_path: Path) -> None:
    """L2-11 r6: rewrites at the same output limit truncate at the same place
    forever — the post-write suggestion must steer to append_to_file."""
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    truncated = "<html>\n<body>\n<script>\nconst editor = 1;\n"
    result = _handle_write_file(ex, file="index.html", content=truncated)
    assert result.get("ok") is True
    assert result.get("syntax_check") == "failed"
    assert "append_to_file" in result.get("suggestion", "")
    assert "do NOT rewrite" in result.get("suggestion", "")


def test_write_file_plain_js_error_keeps_narrow_edit_suggestion(tmp_path: Path) -> None:
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))
    bad_js = "const head = {\n  x: 1,\n  y: 2;\n};\n"
    result = _handle_write_file(ex, file="game.js", content=bad_js)
    assert result.get("ok") is False
    assert result.get("error_type") == "syntax"
    assert result.get("retryable") is True
    assert result.get("loop_break") is False
    assert "Code syntax validation failed" in result.get("error", "")
    assert "write_file" in result.get("suggestion", "")
    assert "complete corrected UTF-8 file body" in result.get("suggestion", "")
    assert "edit_blocks" in result.get("suggestion", "")
    assert "append_to_file" not in result.get("suggestion", "")
    excerpt = result.get("syntax_error_excerpt")
    assert isinstance(excerpt, list)
    assert excerpt
    assert excerpt[0]["line"] == 3
    assert excerpt[0]["message"].startswith("SyntaxError:")
    context_lines = excerpt[0]["context"]
    assert any(line["line"] == 3 and line["is_error_line"] for line in context_lines)
    assert any(line["text"] == "  y: 2;" for line in context_lines)


# ----- EOF (no trailing newline) line-range edits -----


def test_line_range_edit_last_line_no_trailing_newline(tmp_path: Path) -> None:
    """Finding 1 regression: editing the final line of a file whose last line has
    no trailing newline must NOT silently drop the synthesized block.

    Before the fix the divider ``====`` was glued onto the final content line
    (``return self.content====``) so the parser's full-line divider regex never
    matched, the block parsed to nothing, and the edit silently failed.
    """
    # File whose LAST line has no trailing newline.
    source = "class C:\n    def value(self):\n        return self.content"
    (tmp_path / "eof.py").write_text(source, encoding="utf-8")
    assert not source.endswith("\n")
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))

    result = _handle_edit_blocks(
        ex,
        file="eof.py",
        start=3,
        end=3,
        replace="        return self.content or b''",
    )

    assert result.get("ok") is True, result
    assert result.get("files_modified") == 1, result
    text = (tmp_path / "eof.py").read_text(encoding="utf-8")
    # Edit applied exactly to the final line.
    assert text == "class C:\n    def value(self):\n        return self.content or b''"
    # Delimiter newline was NOT written to disk (EOF shape preserved).
    assert not text.endswith("\n")


# ----- edit_file single-bound line edits -----


def test_edit_file_start_line_without_end_edits_only_that_line(tmp_path: Path) -> None:
    """Finding 2 regression: edit_file(start_line=N) with no end_line must edit
    ONLY line N, not delete every line from N to EOF.

    The tool prompt documents start_line/end_line as optional, so single-bound
    calls are expected. Defaulting end to total_lines truncated the file tail.
    """
    body = "".join(f"line {n}\n" for n in range(1, 201))  # 200 lines
    (tmp_path / "big.txt").write_text(body, encoding="utf-8")
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))

    result = _handle_edit_file(ex, file="big.txt", start_line=10, content="x=1\n")

    assert result.get("ok") is True, result
    text = (tmp_path / "big.txt").read_text(encoding="utf-8")
    out_lines = text.splitlines()
    # The file did NOT shrink: still ~200 lines (tail preserved).
    assert len(out_lines) == 200, len(out_lines)
    assert out_lines[9] == "x=1"  # line 10 (0-indexed 9) replaced
    assert out_lines[8] == "line 9"  # line 9 untouched
    assert out_lines[10] == "line 11"  # line 11 untouched
    assert out_lines[-1] == "line 200"  # EOF preserved


# ----- Finding 3: atomic multi-file commit -----


def test_edit_blocks_multifile_aborts_atomically_on_second_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Finding 3 regression: two-file edit where the 2nd file fails to stage must
    leave the 1st file UNCHANGED on disk (no partial apply) and return ok:false.

    Previously Phase 2 wrote+renamed each file in turn, so a later-file failure
    committed earlier files while still reporting failure (partial apply).
    """
    from polaris.kernelone.llm.toolkit.executor.handlers import filesystem as fs

    (tmp_path / "a.txt").write_text("alpha-original\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta-original\n", encoding="utf-8")
    ex = AgentAccelToolExecutor(workspace=str(tmp_path))

    blocks = (
        "<<<< SEARCH:a.txt\nalpha-original\n====\nalpha-NEW\n>>>> REPLACE\n"
        "<<<< SEARCH:b.txt\nbeta-original\n====\nbeta-NEW\n>>>> REPLACE\n"
    )

    real_stage = fs._stage_temp_verify
    calls: list[str] = []

    def flaky_stage(target_path: str, content: str, *, encoding: str = "utf-8"):
        calls.append(target_path)
        if target_path.endswith("b.txt"):
            return {"ok": False, "error": "forced verify failure for b.txt"}
        return real_stage(target_path, content, encoding=encoding)

    monkeypatch.setattr(fs, "_stage_temp_verify", flaky_stage)

    result = _handle_edit_blocks(ex, blocks=blocks)

    assert result.get("ok") is False, result
    assert result.get("files_modified") == 0, result
    # The first (otherwise-valid) file must NOT have been committed.
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "alpha-original\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "beta-original\n"
    # No leftover temp files in the workspace.
    leftover = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftover == [], leftover
