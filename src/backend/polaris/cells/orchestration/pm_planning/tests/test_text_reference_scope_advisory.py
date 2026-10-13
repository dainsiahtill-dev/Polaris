"""Prose references are planning hints; declared effects retain authority."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.public.service import evaluate_pm_task_quality
from polaris.cells.orchestration.pm_planning.tests.test_verification_operand_readiness import _command, _task


def _planning_tasks(prose: str) -> list[dict[str, Any]]:
    planning = _task("PLAN", ["docs/design.md", "docs/example.json"], [], [])
    planning.update(
        title="Create architecture guide and module contracts",
        goal=prose,
        phase="planning",
        assigned_to="ChiefEngineer",
        acceptance=["verify docs/design.md exists; at least 2 module interfaces have concrete command expectations."],
    )
    implementation = _task(
        "IMPLEMENT",
        ["src/module.py", "config/runtime.json", "config/compiler.json"],
        ["PLAN"],
        [_command(["npm", "ci"], modality="environment_prep"), _command(["npm", "test"])],
    )
    return [planning, implementation]


@pytest.mark.parametrize(
    "prose",
    [
        "Document module topology and reference `config/runtime.json` and `config/compiler.json`; "
        "implementation belongs to the downstream task.",
        "Create a design guide describing `config/runtime.json` and `config/compiler.json` configuration; "
        "the guide owns documentation only.",
    ],
)
def test_planning_references_are_unblocked_advisory_with_original_fields(tmp_path: Path, prose: str) -> None:
    tasks = _planning_tasks(prose)
    before = deepcopy(tasks)
    quality = evaluate_pm_task_quality({"tasks": tasks}, workspace_full=str(tmp_path))
    assert quality["ok"] is True, quality
    assert quality["score"] == 100
    assert not any("described scope paths missing" in issue for issue in quality["critical_issues"])
    assert any("referenced paths" in warning for warning in quality["warnings"])
    assert tasks == before
    assert tasks[0]["target_files"] == ["docs/design.md", "docs/example.json"]
    assert tasks[0]["verification_commands"] == []
    assert tasks[1]["target_files"] == before[1]["target_files"]
    assert tasks[1]["verification_commands"] == before[1]["verification_commands"]


def test_imperative_prose_is_not_a_structured_write_grant(tmp_path: Path) -> None:
    tasks = [
        _task("WRITER", ["src/module.py"], [], [_command(["npm", "test"])]),
        _task("OWNER", ["src/other.py"], ["WRITER"], []),
    ]
    tasks[0]["goal"] = "Implement `src/other.py` and describe its integration with this module."
    before = deepcopy(tasks)
    quality = evaluate_pm_task_quality({"tasks": tasks}, workspace_full=str(tmp_path))
    assert quality["ok"] is True, quality
    assert quality["score"] == 100
    assert any("referenced paths" in warning for warning in quality["warnings"])
    assert tasks == before
    assert "src/other.py" not in tasks[0]["scope_paths"]
    assert "src/other.py" not in tasks[0]["target_files"]


@pytest.mark.parametrize("target", ["../outside.py", "/outside/module.py"])
def test_structured_foreign_scope_still_rejected(tmp_path: Path, target: str) -> None:
    tasks = [_task("WRITER", [target], [], [_command(["npm", "test"])])]
    before = deepcopy(tasks)
    quality = evaluate_pm_task_quality({"tasks": tasks}, workspace_full=str(tmp_path))
    assert quality["ok"] is False
    assert any("scope" in issue for issue in quality["critical_issues"])
    assert tasks == before
