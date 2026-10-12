from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor


def _validate_readme(tmp_path: Path, content: str) -> str | None:
    readme = tmp_path / "README.md"
    readme.write_text(content, encoding="utf-8")
    return DirectorPatchExecutor(str(tmp_path)).validate_generated_output(
        {
            "subject": "Fantasy restaurant queue CLI",
            "description": "Document real restaurant queue behavior",
        },
        ["README.md"],
    )


def test_allows_negated_placeholder_prose_in_readme(tmp_path: Path) -> None:
    """README anti-scaffold claims are evidence, not unfinished content."""

    error = _validate_readme(
        tmp_path,
        "# Fantasy Restaurant\n\nThe CLI computes observable queue decisions rather than a static placeholder.\n",
    )

    assert error is None


def test_rejects_actual_placeholder_prose_in_readme(tmp_path: Path) -> None:
    error = _validate_readme(
        tmp_path,
        "# Fantasy Restaurant\n\nThis section is a placeholder until queue behavior is implemented.\n",
    )

    assert error is not None
    assert "generic/placeholder content detected" in error


@pytest.mark.parametrize("word", ["NotImplemented", "stub", "placeholder"])
def test_blueprint_can_prohibit_named_unfinished_implementations(tmp_path: Path, word: str) -> None:
    content = f"# Garden blueprint\n\n禁止 `{word}` 占位实现；garden 行为函数必须返回真实计算结果。\n"
    (tmp_path / "blueprint.md").write_text(content, encoding="utf-8")
    error = DirectorPatchExecutor(str(tmp_path)).validate_generated_output(
        {"subject": "Garden simulation blueprint", "description": "Document real garden behavior"},
        ["blueprint.md"],
    )
    assert error is None


def test_executable_notimplemented_body_still_requires_real_implementation(tmp_path: Path) -> None:
    (tmp_path / "garden.py").write_text("def garden_tick():\n    raise NotImplementedError()\n", encoding="utf-8")
    error = DirectorPatchExecutor(str(tmp_path)).validate_generated_output(
        {"subject": "Garden simulation", "description": "Implement real garden behavior"}, ["garden.py"]
    )
    assert error is not None
