"""Dispatch cannot turn prose filenames into obligations or write grants."""

from __future__ import annotations

from copy import deepcopy

import pytest
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import _pm_task_rows_from_payload


@pytest.mark.parametrize(
    "step",
    [
        "实现浏览器入口，不强制 src/legacy.ts",
        "Do not create src/legacy.ts",
        "Optional implementation: src/legacy.ts",
        "Read the sibling src/legacy.ts; it belongs to another task",
        "Create src/legacy.ts",
    ],
)
@pytest.mark.parametrize("scope", [["index.html"], []])
def test_prose_paths_do_not_augment_structured_obligations_or_scope(step: str, scope: list[str]) -> None:
    row = {"id": "TASK-2", "target_files": ["index.html"], "scope_paths": scope, "steps": [step]}
    original = deepcopy(row)
    assert _pm_task_rows_from_payload({"tasks": [row]}) == [original]
    assert row == original


def test_real_structured_targets_are_retained_even_when_absent_from_prose() -> None:
    row = {
        "id": "TASK-2",
        "target_files": ["index.html", "src/required.ts"],
        "scope_paths": ["index.html", "src/required.ts"],
        "goal": "Implement the browser entrypoint",
    }
    assert _pm_task_rows_from_payload([row]) == [row]


def test_empty_structured_targets_do_not_get_invented_from_prose() -> None:
    row = {"id": "TASK-2", "target_files": [], "scope_paths": [], "steps": ["Create src/legacy.ts"]}
    assert _pm_task_rows_from_payload([row]) == [row]
