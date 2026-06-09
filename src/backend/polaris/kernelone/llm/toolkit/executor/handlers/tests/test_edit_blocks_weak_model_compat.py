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
    _has_search_replace_markers,
    _normalize_block_input,
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
