"""Drained-owner rehydration must retain CE's strict task-local completion slice."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.chief_engineer.blueprint import public as ce_public
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import (
    _task_completion_projection_from_repair_task,
    _workspace_quality_frozen_ce_owner_task,
)


@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("prior_top", ["absent", "none", "stale"])
def test_rehydrated_owner_preserves_strict_ce_completion_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allowed: bool, prior_top: str
) -> None:
    """Copying only job token/whole contract lost the artifact registration slice."""
    token = {
        "run_id": "run-1",
        "token_id": "job-1",
        "blueprint_hash": "a" * 64,
        "target_files": ["src/code.py"],
        "allowed_write_paths": ["src/code.py"],
    }
    blueprint = {
        "task_id": "TASK-1",
        "blueprint_id": "ce-1",
        "status": "generated",
        "handoff_ready": True,
        "blueprint_hash": "a" * 64,
        "target_files": ["src/code.py"],
        "job_token": token,
        "capability_token": token,
        "project_completion_contract": {"project_id": "project-1"},
    }
    projection = {
        "schema_version": "polaris.task_completion_projection.v1",
        "task_id": "TASK-1",
        "run_id": "run-1",
        "project_id": "project-1",
        "project_contract_hash": "b" * 64,
        "projection_hash": "c" * 64,
        "owned_artifacts": [{"obligation_id": "artifact.code", "owner_task_id": "TASK-1", "path": "src/code.py"}],
    }

    def strict_owner(workspace: str, payload: dict, *, require_strict: bool = False) -> dict:
        assert workspace == str(tmp_path)
        assert payload["metadata"]["blueprint_id"] == "ce-1"
        return {"allowed": allowed and require_strict, "task_completion_projection": projection}

    monkeypatch.setattr(ce_public, "validate_director_handoff_from_payload", strict_owner)
    executor = SimpleNamespace(
        workspace=tmp_path,
        _load_chief_engineer_review_payload=lambda **_kwargs: {
            "blueprints": [
                {
                    "task_id": "TASK-1",
                    "status": "generated",
                    "handoff_ready": True,
                    "blueprint_id": "ce-1",
                    "blueprint_path": "runtime/blueprints/ce-1.json",
                }
            ]
        },
        _read_json_artifact_payload=lambda _path: blueprint,
    )
    canonical = {"id": "TASK-1", "target_files": ["package.toml"]}
    if prior_top != "absent":
        canonical["task_completion_projection"] = (
            None
            if prior_top == "none"
            else {"task_id": "TASK-1", "run_id": "older-run", "owned_artifacts": [{"path": "foreign.py"}]}
        )
    actual = _workspace_quality_frozen_ce_owner_task(
        executor, run_id="run-1", task_id="TASK-1", canonical_task=canonical
    )
    if allowed:
        assert actual["metadata"]["task_completion_projection"] == projection
        assert _task_completion_projection_from_repair_task(actual) == projection
    else:
        assert actual == canonical
