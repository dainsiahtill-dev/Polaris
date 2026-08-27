from __future__ import annotations

from pathlib import Path

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
        "# Fantasy Restaurant\n\n"
        "The CLI computes observable queue decisions rather than a static placeholder.\n",
    )

    assert error is None


def test_rejects_actual_placeholder_prose_in_readme(tmp_path: Path) -> None:
    error = _validate_readme(
        tmp_path,
        "# Fantasy Restaurant\n\nThis section is a placeholder until queue behavior is implemented.\n",
    )

    assert error is not None
    assert "generic/placeholder content detected" in error
