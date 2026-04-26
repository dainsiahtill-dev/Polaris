from __future__ import annotations

from pathlib import Path

from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService
from polaris.cells.resident.autonomy.internal.resident_runtime_service import (
    get_resident_service,
    reset_resident_services,
)
from polaris.kernelone.storage.io_paths import build_cache_root


def test_resident_pm_bridge_stages_and_promotes_governed_goal(tmp_path: Path) -> None:
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    cache_root = build_cache_root("", str(workspace))
    artifacts = ArtifactService(workspace=str(workspace), cache_root=cache_root)
    artifacts.write_task_contract(
        {
            "focus": "baseline",
            "overall_goal": "Existing PM contract",
            "metadata": {"source": "baseline"},
            "tasks": [{"id": "baseline-1", "title": "Keep baseline"}],
        }
    )
    artifacts.write_plan("# Existing Plan\n\nKeep this plan block.\n")
    artifacts.write_pm_state({"last_director_status": "idle"})

    service = get_resident_service(str(workspace))
    goal = service.create_goal_proposal(
        {
            "goal_type": "maintenance",
            "title": "Harden resident bridge",
            "motivation": "Move governed goals into PM runtime with rollback safety.",
            "source": "manual",
            "scope": ["src/backend/app/resident", "docs/resident"],
            "budget": {"max_tasks": 2, "max_parallel_tasks": 1},
            "evidence_refs": ["docs/resident/resident-engineering-rfc.md"],
        }
    )
    approved = service.approve_goal(goal.goal_id, note="approved for bridge test")
    assert approved is not None

    staged = service.stage_goal(
        goal.goal_id,
        promote_to_pm_runtime=True,
        ramdisk_root="",
    )

    assert staged is not None
    assert staged["promoted_to_pm_runtime"] is True
    assert staged["artifacts"]["resident_contract_path"]
    assert staged["artifacts"]["pm_contract_path"]
    assert staged["artifacts"]["backup_manifest_path"]

    resident_contract = artifacts.read_json("RESIDENT_GOAL_CONTRACT")
    pm_contract = artifacts.read_task_contract()
    pm_state = artifacts.read_pm_state()
    plan_text = artifacts.read_plan()

    assert resident_contract is not None
    assert resident_contract["metadata"]["resident_goal_id"] == goal.goal_id
    assert pm_contract is not None
    assert pm_contract["metadata"]["resident_goal_id"] == goal.goal_id
    assert pm_state is not None
    assert pm_state["resident_goal_id"] == goal.goal_id
    assert f"RESIDENT_GOAL:{goal.goal_id}:BEGIN" in plan_text
    assert "Existing Plan" in plan_text

    saved_goal = next(item for item in service.list_goals() if item.goal_id == goal.goal_id)
    assert saved_goal.materialization_artifacts["pm_contract_path"] == staged["artifacts"]["pm_contract_path"]
    assert saved_goal.materialization_artifacts["promotion"]["promoted_at"] == staged["promotion"]["promoted_at"]

    reset_resident_services()
