from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.cells.runtime.artifact_store.internal import artifacts
from polaris.cells.runtime.projection.public import service as projection_service


def test_workflow_runtime_status_uses_task_runtime_observable_rows(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()

    class _ProjectionOnlyTaskRuntime:
        def __init__(self, workspace_arg: str) -> None:
            assert workspace_arg == str(workspace)

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "TASK-1",
                    "status": "failed",
                    "metadata": {"status_source": "task_runtime.execution_fact"},
                }
            ]

        def list_task_rows(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("artifact_store must read observable task rows")

    monkeypatch.setattr(artifacts, "TaskRuntimeService", _ProjectionOnlyTaskRuntime)

    result = artifacts.get_workflow_runtime_status(str(workspace), str(cache_root))

    assert result["tasks"] == [
        {
            "id": "TASK-1",
            "status": "failed",
            "metadata": {"status_source": "task_runtime.execution_fact"},
        }
    ]
    assert result["task_count"] == 1


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
