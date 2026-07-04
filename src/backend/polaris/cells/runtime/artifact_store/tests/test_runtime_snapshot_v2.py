from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.runtime.artifact_store.internal import artifacts
from polaris.cells.runtime.projection.public import service as projection_service


def test_runtime_snapshot_v2_uses_workflow_task_projection(monkeypatch) -> None:
    state = SimpleNamespace(
        settings=SimpleNamespace(
            workspace="/tmp/polaris-runtime-v2-test",
            ramdisk_root="",
        )
    )
    monkeypatch.setattr(
        artifacts,
        "build_snapshot",
        lambda *_args, **_kwargs: {
            "run_id": "run-1",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Contract task",
                    "status": "pending",
                }
            ],
        },
    )
    monkeypatch.setattr(
        artifacts,
        "_build_pm_status",
        lambda _state: {
            "running": True,
            "workflow": {
                "running": True,
                "workflow_id": "workflow-1",
                "director_runtime_snapshot": {
                    "stage": "director_started",
                    "tasks": {
                        "TASK-1": {
                            "task_id": "TASK-1",
                            "state": "running",
                            "summary": "Director is materializing target files",
                            "metadata": {
                                "task_title": "Runtime task",
                                "current_file": "src/index.ts",
                                "changed_files": ["src/index.ts"],
                            },
                        }
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        projection_service,
        "build_director_runtime_status",
        lambda *_args, **_kwargs: {"running": False, "workers": []},
    )
    monkeypatch.setattr(
        artifacts,
        "_build_engine_status_v2",
        lambda *_args, **_kwargs: {"phase": "implementation"},
    )

    snapshot = artifacts.build_runtime_snapshot_v2(state)

    assert snapshot["tasks"][0]["id"] == "TASK-1"
    assert snapshot["tasks"][0]["title"] == "Contract task"
    assert snapshot["tasks"][0]["state"] == "in_progress"
    assert snapshot["summary"] == {
        "total": 1,
        "completed": 0,
        "failed": 0,
        "blocked": 0,
    }
