from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
