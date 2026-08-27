from __future__ import annotations

from polaris.cells.chief_engineer.blueprint.public.service._portfolio import (
    _project_delegated_cpp_header_companions,
)


def _artifact(path: str, *, role: str = "source") -> dict[str, str]:
    return {
        "obligation_id": f"artifact-{path}",
        "path": path,
        "semantic_role": role,
        "applicability": "required",
        "owner_task_id": "TASK-1",
    }


def test_projects_missing_cpp_header_from_stable_delegated_layout() -> None:
    """Exact L3-24 r88: CE must close its delegated public-header topology."""

    artifacts = (
        _artifact("src/cipher.cpp"),
        _artifact("include/invisible_ink/cipher.hpp"),
        _artifact("src/cli_options.cpp"),
        _artifact("include/invisible_ink/cli_options.hpp"),
        _artifact("src/diary.cpp"),
        _artifact("include/invisible_ink/diary.hpp"),
        _artifact("src/diary_render.cpp"),
        _artifact("src/diary_cli.cpp", role="entrypoint"),
    )

    projected = _project_delegated_cpp_header_companions(
        artifacts,
        delegated_public_header_task_ids=frozenset({"TASK-1"}),
    )

    by_path = {row["path"]: row for row in projected}
    assert "include/invisible_ink/diary_render.hpp" in by_path
    assert by_path["include/invisible_ink/diary_render.hpp"]["owner_task_id"] == "TASK-1"
    assert by_path["include/invisible_ink/diary_render.hpp"]["semantic_role"] == "source"
    assert "include/invisible_ink/diary_cli.hpp" not in by_path


def test_does_not_guess_cpp_header_when_layout_is_ambiguous() -> None:
    artifacts = (
        _artifact("src/cipher.cpp"),
        _artifact("include/invisible_ink/cipher.hpp"),
        _artifact("src/diary.cpp"),
        _artifact("public/diary.h"),
        _artifact("src/diary_render.cpp"),
    )

    projected = _project_delegated_cpp_header_companions(
        artifacts,
        delegated_public_header_task_ids=frozenset({"TASK-1"}),
    )

    assert projected == artifacts
