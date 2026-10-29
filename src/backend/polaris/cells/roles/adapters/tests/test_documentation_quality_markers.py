"""Documentation names source markers; it is not an unfinished implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.public.service import create_role_adapter

_R31_RULES = (
    "| 反占位 | 不允许 TODO / FIXME / stub / 未实现占位 |",
    "- **零占位**：最终 diff 必须不含 `TODO` / `FIXME` / `stub` / `not implemented`。",
)


def _validate(tmp_path: Path, relative: str, content: str) -> str | None:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    adapter: Any = create_role_adapter("director", str(tmp_path))
    result: str | None = adapter._execution.validate_generated_output(
        {"subject": "blueprint", "description": "blueprint"}, [relative]
    )
    return result


@pytest.mark.parametrize("rule", _R31_RULES)
def test_actual_director_gate_accepts_r31_anti_placeholder_documentation(tmp_path: Path, rule: str) -> None:
    error = _validate(tmp_path, "docs/blueprint.md", "# blueprint\n\n" + rule + "\n")
    assert error is None, error


@pytest.mark.parametrize("suffix", [".markdown", ".rst", ".txt"])
def test_documentation_mentions_and_counterexamples_are_not_executed_source(tmp_path: Path, suffix: str) -> None:
    content = (
        "blueprint quality policy: no stub implementation is accepted.\n\n"
        "Rejected code example, retained here for explanation:\n"
        "```python\nraise NotImplementedError()\n# stub\n```\n"
        'The "placeholder" term describes an anti-pattern, not a delivered implementation.\n'
    )
    assert _validate(tmp_path, "docs/blueprint" + suffix, content) is None


@pytest.mark.parametrize(
    "relative,content",
    [
        ("src/blueprint.py", "# blueprint\ndef run():\n    raise NotImplementedError()\n"),
        ("src/blueprint.ts", "// blueprint\nexport const result = stub;\n"),
        ("tests/blueprint.test.ts", "// blueprint\nexport function result() { return placeholder; }\n"),
    ],
)
def test_source_keywords_defer_to_ce_verification_and_independent_qa(
    tmp_path: Path, relative: str, content: str
) -> None:
    error = _validate(tmp_path, relative, content)
    assert error is None


@pytest.mark.parametrize(
    "content,expected",
    [
        ("  \n", "unreadable"),
        ("# blueprint\n<<<<<<< SEARCH\nunfinished\n=======\n", "generic/placeholder"),
    ],
)
def test_documentation_required_readability_and_residue_checks_remain_failed(
    tmp_path: Path, content: str, expected: str
) -> None:
    error = _validate(tmp_path, "docs/blueprint.md", content)
    assert error is not None and expected in error


def test_complete_html_can_explain_real_placeholder_attribute(tmp_path: Path) -> None:
    content = (
        "<!doctype html>\n<html><body>\n"
        '<!-- "placeholder" is an input hint; the blueprint preview is implemented below. -->\n'
        '<input id="source" placeholder="Enter blueprint text">\n'
        '<output id="preview"></output>\n'
        "<script>\n"
        'const source = document.getElementById("source");\n'
        'const preview = document.getElementById("preview");\n'
        'source.addEventListener("input", () => { preview.textContent = source.value; });\n'
        "</script>\n</body></html>\n"
    )
    assert _validate(tmp_path, "index.html", content) is None


def test_complete_html_accepts_plain_placeholder_explanation_comment(tmp_path: Path) -> None:
    content = (
        "<!doctype html>\n<html><body>\n"
        "<!-- The placeholder attribute is documented here. -->\n"
        '<label id="preview">blueprint preview</label>\n'
        '<input id="source" placeholder="Enter blueprint text">\n'
        "<script>\n"
        'const source = document.getElementById("source");\n'
        'const preview = document.getElementById("preview");\n'
        'source.addEventListener("input", () => { preview.textContent = source.value; });\n'
        "</script>\n</body></html>\n"
    )
    assert _validate(tmp_path, "index.html", content) is None


@pytest.mark.parametrize(
    "content",
    [
        "# Track TODO from upstream.\ndef stub():\n    return 42\n",
        "# Generated Project Scaffold\nVALUE = 42\n",
    ],
)
def test_functioning_source_is_not_rejected_by_keyword_heuristics(tmp_path: Path, content: str) -> None:
    assert _validate(tmp_path, "answer.py", content) is None
