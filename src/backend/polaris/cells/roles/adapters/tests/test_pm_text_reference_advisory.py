"""The actual PM consumer preserves typed ownership across reference hints."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.orchestration.pm_planning.tests.test_text_reference_scope_advisory import _planning_tasks
from polaris.cells.roles.adapters.public import PMAdapter


def _adapter(workspace: Path) -> PMAdapter:
    constructor: Callable[..., PMAdapter] = PMAdapter
    return constructor(workspace=str(workspace))


@pytest.mark.parametrize("original_root_scope", [False, True])
@pytest.mark.parametrize(
    "prose",
    [
        "Document module topology and reference `config/runtime.json` and `config/compiler.json`; "
        "implementation belongs to the downstream task.",
        "Create a design guide describing `config/runtime.json` and `config/compiler.json` configuration; "
        "the guide owns documentation only.",
    ],
)
def test_actual_pm_adapter_unblocks_reference_without_rewriting_declared_artifacts(
    tmp_path: Path, original_root_scope: bool, prose: str
) -> None:
    tasks = _planning_tasks(prose)
    if original_root_scope:
        tasks[0]["scope_paths"] = ["."]
    before = deepcopy(tasks)
    normalized, quality = _adapter(tmp_path)._evaluate_contract_quality(tasks)
    assert quality["ok"] is True, quality
    assert quality["score"] == 100
    assert any("referenced paths" in warning for warning in quality["warnings"])
    assert tasks == before
    for actual, original in zip(normalized, before, strict=True):
        assert actual["id"] == original["id"]
        assert actual["target_files"] == original["target_files"]
        assert actual["verification_commands"] == original["verification_commands"]
    assert normalized[0]["target_files"] == ["docs/design.md", "docs/example.json"]


def test_actual_adapter_keeps_readonly_write_artifact_collision_rejection(tmp_path: Path) -> None:
    tasks = _planning_tasks("Document module topology and reference `config/runtime.json`.")
    tasks[1]["context_files"] = ["src/module.py"]
    before = deepcopy(tasks)
    _, quality = _adapter(tmp_path)._evaluate_contract_quality(tasks)
    assert quality["ok"] is False
    assert any(
        diagnostic["code"] == "artifact_readonly_write_collision" for diagnostic in quality["contract_diagnostics"]
    )
    assert tasks == before
