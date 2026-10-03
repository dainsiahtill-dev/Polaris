"""Planning input must not turn workspace inventory into owner write targets."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import (
    _apply_workspace_quality_repairs,
)
from polaris.cells.roles.adapters.public import service as adapters


def _executor(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace=root,
        _workspace_quality_repair_target_files=lambda: ["package.json", "src/foreign.ts"],
        _workspace_quality_repair_diagnostic_target_files=lambda _errors: ["src/foreign.ts"],
        _workspace_quality_repair_changed_files=lambda: ["package.json", "src/foreign.ts"],
        _workspace_quality_repair_blueprint_evidence=lambda **_kwargs: ("", ""),
    )


def _install_readonly_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    def observe(_adapter: object, *, task: dict, **_kwargs: object) -> tuple:
        return [], {
            "received_target_files": list(task["target_files"]),
            "received_metadata_target_files": list(task["metadata"]["target_files"]),
        }

    monkeypatch.setattr(adapters, "run_director_materialization_quality_repair_schedule", observe)
    monkeypatch.setattr(adapters, "run_director_post_execution_repair_schedule", lambda *_args, **_kwargs: ([], None))


def test_original_owner_targets_reach_schedule_without_inventory_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "package.json").write_text('{"name":"fixture"}', encoding="utf-8")
    _install_readonly_schedule(monkeypatch)
    owner = {
        "id": "TASK-2",
        "target_files": ["index.html", "src/entry/browser.ts"],
        "metadata": {"allowed_write_paths": ["index.html", "src/entry/browser.ts"]},
    }
    before = deepcopy(owner)
    _, summary = _apply_workspace_quality_repairs(
        _executor(tmp_path), run_id="factory-1", artifact_quality_errors=[], task_id="TASK-2", repair_task=owner
    )
    assert summary["received_target_files"] == ["index.html", "src/entry/browser.ts"]
    assert summary["received_metadata_target_files"] == ["index.html", "src/entry/browser.ts"]
    assert owner == before


@pytest.mark.parametrize("metadata_targets", [[], ["package.json"]])
def test_explicit_empty_owner_targets_do_not_fall_back_to_workspace_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata_targets: list[str]
) -> None:
    _install_readonly_schedule(monkeypatch)
    with pytest.raises(ValueError, match="workspace_quality_repair_owner_targets_missing"):
        _apply_workspace_quality_repairs(
            _executor(tmp_path),
            run_id="factory-1",
            artifact_quality_errors=[],
            task_id="TASK-2",
            repair_task={
                "id": "TASK-2",
                "target_files": [],
                "metadata": {"target_files": metadata_targets, "allowed_write_paths": []},
            },
        )
