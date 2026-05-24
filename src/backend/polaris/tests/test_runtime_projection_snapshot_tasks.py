from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.cells.runtime.projection.public.service import (
    RuntimeProjection,
    build_snapshot_payload_from_projection,
)


def test_snapshot_prefers_runtime_task_rows() -> None:
    projection = RuntimeProjection(
        pm_local={},
        director_local={
            "running": True,
            "status": {
                "tasks": {
                    "task_rows": [
                        {"id": "local-1", "subject": "legacy local", "status": "RUNNING"},
                    ]
                }
            },
        },
        workflow_archive={"tasks": [{"id": "wf-1", "subject": "workflow", "status": "PENDING"}]},
        engine_fallback=None,
    )

    runtime_rows = [
        {"id": "task-1", "subject": "runtime canonical task", "status": "in_progress"},
        {"id": "task-2", "subject": "runtime pending task", "status": "pending"},
    ]
    with patch(
        "polaris.cells.runtime.projection.internal.runtime_projection_service.load_runtime_task_rows",
        return_value=runtime_rows,
    ):
        snapshot = build_snapshot_payload_from_projection(
            projection=projection,
            workspace="C:/Temp/runtime-ws",
        )

    assert snapshot["tasks"] == runtime_rows


def test_snapshot_projects_workflow_director_completion_over_stale_pm_state() -> None:
    projection = RuntimeProjection(
        pm_local={},
        director_local={"running": False, "state": "IDLE"},
        director_merged={
            "running": False,
            "source": "workflow",
            "status": {
                "state": "COMPLETED",
                "tasks": {
                    "total": 2,
                    "by_status": {"COMPLETED": 2, "FAILED": 0, "IN_PROGRESS": 0},
                },
            },
        },
        workflow_archive={
            "source": "workflow",
            "status": {
                "state": "COMPLETED",
                "tasks": {
                    "total": 2,
                    "by_status": {"COMPLETED": 2, "FAILED": 0, "IN_PROGRESS": 0},
                },
            },
        },
        task_rows=[
            {"id": "PM-1", "subject": "done 1", "status": "COMPLETED", "metadata": {"pm_task_id": "PM-1"}},
            {"id": "PM-2", "subject": "done 2", "status": "COMPLETED", "metadata": {"pm_task_id": "PM-2"}},
        ],
    )

    with (
        patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.load_runtime_task_rows",
            return_value=[],
        ),
        patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.read_json",
            return_value={"completed_task_count": 0},
        ),
    ):
        snapshot = build_snapshot_payload_from_projection(
            projection=projection,
            workspace="C:/Temp/runtime-ws",
            cache_root=Path("C:/Temp/runtime-root"),
        )

    assert snapshot["director"]["source"] == "workflow"
    assert snapshot["pm_state"]["last_director_status"] == "COMPLETED"
    assert snapshot["pm_state"]["completed_task_count"] == 2
    assert snapshot["snapshot_compat"]["workflow_completed_tasks"] == 2


def test_snapshot_projects_director_result_when_workflow_projection_is_unavailable() -> None:
    projection = RuntimeProjection(
        pm_local={},
        director_local={"running": False, "state": "IDLE"},
        workflow_archive=None,
        task_rows=[],
    )

    with (
        patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.load_runtime_task_rows",
            return_value=[],
        ),
        patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.read_json",
            side_effect=[
                {},
                {"completed_task_count": 0, "last_director_status": "IDLE"},
                {"status": "success", "successes": 3},
            ],
        ),
    ):
        snapshot = build_snapshot_payload_from_projection(
            projection=projection,
            workspace="C:/Temp/runtime-ws",
            cache_root=None,
        )

    assert snapshot["pm_state"]["last_director_status"] == "success"
    assert snapshot["pm_state"]["completed_task_count"] == 3


def test_snapshot_recovers_runtime_artifacts_from_pm_status_contract_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    canonical_root = tmp_path / "canonical-root"
    pm_runtime_root = tmp_path / "pm-runtime" / ".polaris" / "projects" / "workspace-fallback" / "runtime"
    contract_path = pm_runtime_root / "contracts" / "pm_tasks.contract.json"
    pm_state_path = pm_runtime_root / "state" / "pm.state.json"
    director_result_path = pm_runtime_root / "results" / "director.result.json"
    plan_path = pm_runtime_root / "contracts" / "plan.md"

    contract_path.parent.mkdir(parents=True)
    pm_state_path.parent.mkdir(parents=True)
    director_result_path.parent.mkdir(parents=True)
    contract_payload = {
        "run_id": "pm-fallback",
        "tasks": [{"id": "PM-1", "title": "Recovered task", "status": "pending"}],
    }
    contract_path.write_text(json.dumps(contract_payload), encoding="utf-8")
    pm_state_path.write_text(
        json.dumps({"completed_task_count": 0, "last_director_status": "IDLE"}),
        encoding="utf-8",
    )
    director_result_path.write_text(json.dumps({"status": "success", "successes": 1}), encoding="utf-8")
    plan_path.write_text("# Plan\n\nRecovered from PM runtime root.\n", encoding="utf-8")

    projection = RuntimeProjection(
        pm_local={"contract_path": str(contract_path), "running": False},
        director_local={"running": False, "state": "IDLE"},
        workflow_archive=None,
        task_rows=[],
    )

    with patch(
        "polaris.cells.runtime.projection.internal.runtime_projection_service.load_runtime_task_rows",
        return_value=[],
    ):
        snapshot = build_snapshot_payload_from_projection(
            projection=projection,
            workspace=str(workspace),
            cache_root=canonical_root,
        )

    assert snapshot["run_id"] == "pm-fallback"
    assert snapshot["tasks"] == [{"id": "PM-1", "title": "Recovered task", "status": "pending"}]
    assert snapshot["pm_state"]["last_director_status"] == "success"
    assert snapshot["pm_state"]["completed_task_count"] == 1
    assert "Recovered from PM runtime root" in snapshot["plan_text"]


@pytest.mark.asyncio
async def test_pm_local_status_preserves_pm_service_artifact_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.cells.runtime.projection.internal import runtime_projection_service as service

    class FakePMService:
        def get_status(self) -> dict[str, object]:
            return {
                "running": False,
                "pid": None,
                "mode": "run_once",
                "started_at": 123.0,
                "contract_path": "C:/runtime/.polaris/projects/ws/runtime/contracts/pm_tasks.contract.json",
                "terminal": True,
                "ok": False,
                "exit_code": 7,
                "error": "PM_ITERATION_FAILED",
            }

    class FakeContainer:
        async def resolve_async(self, _service_type: object) -> FakePMService:
            return FakePMService()

    async def fake_get_container() -> FakeContainer:
        return FakeContainer()

    monkeypatch.setattr("polaris.infrastructure.di.container.get_container", fake_get_container)

    status = await service.get_pm_local_status()

    assert status["running"] is False
    assert status["mode"] == "run_once"
    assert status["contract_path"].endswith("pm_tasks.contract.json")
    assert status["terminal"] is True
    assert status["ok"] is False
    assert status["exit_code"] == 7
    assert status["error"] == "PM_ITERATION_FAILED"
