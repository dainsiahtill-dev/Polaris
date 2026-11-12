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
        "# Fantasy Restaurant\n\nThe CLI computes observable queue decisions rather than a static placeholder.\n",
    )

    assert error is None


def test_placeholder_prose_defers_to_ce_verification_and_qa(tmp_path: Path) -> None:
    """Current contract: the Director output gate does not judge placeholder prose.

    ``DirectorPatchExecutor.validate_generated_output`` owns path safety,
    readability and unapplied patch-protocol residue.  Whether a sentence says
    "placeholder" is a semantic question: the CE verification recipe and
    independent QA own the "is this finished?" verdict, so the keyword must
    not fail materialization on its own.
    """

    error = _validate_readme(
        tmp_path,
        "# Fantasy Restaurant\n\nThis section is a placeholder until queue behavior is implemented.\n",
    )

    assert error is None


def test_unreadable_output_is_still_rejected(tmp_path: Path) -> None:
    """The readability half of the same gate stays fail-closed."""

    error = _validate_readme(tmp_path, "   \n")

    assert error is not None
    assert "unreadable" in error


def test_patch_residue_is_still_rejected(tmp_path: Path) -> None:
    """The unapplied-patch-protocol half of the same gate stays fail-closed."""

    error = _validate_readme(
        tmp_path,
        "# Fantasy Restaurant\n<<<<<<< SEARCH\nunfinished\n=======\n",
    )

    assert error is not None
    assert "generic/placeholder content detected" in error


def test_blueprint_can_prohibit_named_unfinished_implementations(tmp_path: Path) -> None:
    content = "# Garden blueprint\n\n禁止 `NotImplemented` 占位实现；garden 行为函数必须返回真实计算结果。\n"
    (tmp_path / "blueprint.md").write_text(content, encoding="utf-8")
    error = DirectorPatchExecutor(str(tmp_path)).validate_generated_output(
        {"subject": "Garden simulation blueprint", "description": "Document real garden behavior"},
        ["blueprint.md"],
    )
    assert error is None


def test_executable_notimplemented_body_defers_to_ce_verification(tmp_path: Path) -> None:
    """Current contract: a ``NotImplementedError`` body is a CE/QA verdict.

    The retired keyword scan used to reject the file here.  The Director
    output gate now only proves path/readability/residue, and the semantic
    "is this implementation real?" question belongs to the CE verification
    recipe and independent QA.
    """
    (tmp_path / "garden.py").write_text("def garden_tick():\n    raise NotImplementedError()\n", encoding="utf-8")
    error = DirectorPatchExecutor(str(tmp_path)).validate_generated_output(
        {"subject": "Garden simulation", "description": "Implement real garden behavior"}, ["garden.py"]
    )
    assert error is None
