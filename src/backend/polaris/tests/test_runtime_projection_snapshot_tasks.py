from __future__ import annotations

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

