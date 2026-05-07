from __future__ import annotations

import asyncio
from types import SimpleNamespace

from polaris.delivery.http.v2 import director as v2_director


def test_merge_director_status_prefers_workflow_snapshot() -> None:
    local_status = {
        "state": "IDLE",
        "workspace": "X:\\workspace",
        "metrics": {"tasks_completed": 0},
        "tasks": {"total": 0},
        "workers": {"total": 1, "busy": 0},
        "token_budget": {"remaining": 128},
    }
    workflow_status = {
        "state": "RUNNING",
        "workspace": "X:\\workspace",
        "metrics": {"tasks_completed": 1, "workflow_id": "wf-001"},
        "tasks": {"total": 2, "by_status": {"IN_PROGRESS": 1}},
        "workers": {"total": 2, "busy": 1},
        "token_budget": {"used": 32},
    }

    merged = v2_director._merge_director_status(local_status, workflow_status)

    assert merged["state"] == "RUNNING"
    assert merged["tasks"]["total"] == 2
    assert merged["metrics"]["workflow_id"] == "wf-001"
    assert merged["workers"]["total"] == 2
    assert merged["workers"]["busy"] == 1
    assert merged["token_budget"]["remaining"] == 128
    assert merged["token_budget"]["used"] == 32


def test_merge_director_status_keeps_local_when_workflow_unavailable() -> None:
    local_status = {
        "state": "IDLE",
        "workspace": "X:\\workspace",
        "metrics": {"tasks_completed": 0},
        "tasks": {"total": 0},
        "workers": {"total": 1, "busy": 0},
        "token_budget": {"remaining": 256},
    }

    merged = v2_director._merge_director_status(local_status, None)
    # Result should contain all original fields (may add source field)
    assert merged["state"] == local_status["state"]
    assert merged["workspace"] == local_status["workspace"]
    assert merged["metrics"] == local_status["metrics"]
    assert merged["tasks"] == local_status["tasks"]
    assert merged["workers"] == local_status["workers"]
    assert merged["token_budget"] == local_status["token_budget"]


def test_merge_director_status_uses_local_workers_when_workflow_workers_missing() -> None:
    local_status = {
        "state": "IDLE",
        "workspace": "X:\\workspace",
        "metrics": {"tasks_completed": 0},
        "tasks": {"total": 0},
        "workers": {"total": 3, "busy": 0},
        "token_budget": {"remaining": 300},
    }
    workflow_status = {
        "state": "PENDING",
        "workspace": "X:\\workspace",
        "metrics": {"workflow_id": "wf-002"},
        "tasks": {"total": 1},
        "workers": {},
        "token_budget": {},
    }

    merged = v2_director._merge_director_status(local_status, workflow_status)

    assert merged["state"] == "PENDING"
    assert merged["workers"]["total"] == 3
    assert merged["token_budget"]["remaining"] == 300


def test_merge_director_status_preserves_local_running_state() -> None:
    local_status = {
        "state": "RUNNING",
        "workspace": "X:\\workspace",
        "metrics": {"tasks_completed": 0},
        "tasks": {"total": 3},
        "workers": {"total": 2, "busy": 1},
        "token_budget": {"remaining": 300},
    }
    workflow_status = {
        "state": "PENDING",
        "workspace": "X:\\workspace",
        "metrics": {"workflow_id": "wf-003"},
        "tasks": {"total": 3},
        "workers": {"total": 1, "busy": 0},
        "token_budget": {},
    }

    merged = v2_director._merge_director_status(local_status, workflow_status)

    assert merged["state"] == "RUNNING"


def test_merge_director_status_keeps_local_tasks_when_workflow_rows_are_stale() -> None:
    local_status = {
        "running": True,
        "state": "RUNNING",
        "workspace": "X:\\workspace",
        "metrics": {"tasks_completed": 0},
        "tasks": {
            "total": 1,
            "by_status": {"IN_PROGRESS": 1},
            "task_rows": [{"id": "local-1", "status": "RUNNING"}],
        },
        "workers": {"total": 2, "busy": 1},
    }
    workflow_status = {
        "state": "PENDING",
        "workspace": "X:\\workspace",
        "metrics": {"workflow_id": "wf-stale-002"},
        "tasks": {"total": 1, "task_rows": []},
        "workers": {"total": 1, "busy": 0},
    }

    merged = v2_director._merge_director_status(local_status, workflow_status)

    assert merged["state"] == "RUNNING"
    assert merged["tasks"]["task_rows"][0]["id"] == "local-1"
    assert merged["metrics"]["workflow_id"] == "wf-stale-002"


class _FakeDirectorService:
    def __init__(self, *, status: dict, local_tasks: list[dict]) -> None:
        self._status = status
        self._local_tasks = local_tasks
        self.list_calls = 0

    async def get_status(self) -> dict:
        return self._status

    async def list_tasks(self, status=None):
        del status
        self.list_calls += 1
        return self._local_tasks


def _build_fake_request() -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(ramdisk_root=""),
            ),
        ),
    )


def test_list_tasks_defaults_to_workflow_source(monkeypatch) -> None:
    workflow_tasks: list[dict[str, object]] = [
        {
            "id": "pm-1",
            "subject": "Workflow Task",
            "description": "from workflow",
            "status": "RUNNING",
            "priority": "MEDIUM",
            "claimed_by": None,
            "result": None,
            "metadata": {},
        }
    ]
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "RUNNING"},
        local_tasks=[],
    )

    # Mock RuntimeProjectionService.build_async for new implementation
    from polaris.cells.runtime.projection.internal.runtime_projection_service import RuntimeProjection

    async def _fake_build_async(workspace, cache_root=None, state=None):
        return RuntimeProjection(
            pm_local={},
            director_local={},
            workflow_archive={"tasks": workflow_tasks},
            engine_fallback=None,
        )

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _fake_build_async)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            service=service,
        )
    )

    assert [item.id for item in payload] == ["pm-1"]
    assert service.list_calls == 0


def test_list_tasks_workflow_uses_projection_task_rows(monkeypatch) -> None:
    workflow_tasks: list[dict[str, object]] = [
        {
            "id": "pm-projected-1",
            "subject": "Projected workflow task",
            "description": "from projection.task_rows",
            "status": "RUNNING",
            "priority": "MEDIUM",
            "claimed_by": None,
            "result": None,
            "metadata": {"pm_task_id": "PM-1"},
        }
    ]
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "RUNNING"},
        local_tasks=[],
    )

    from polaris.cells.runtime.projection.internal.runtime_projection_service import RuntimeProjection

    async def _fake_build_async(workspace, cache_root=None, state=None):
        return RuntimeProjection(
            pm_local={},
            director_local={},
            workflow_archive={"status": {"tasks": {"task_rows": workflow_tasks}}},
            engine_fallback=None,
            task_rows=workflow_tasks,
        )

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _fake_build_async)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            source="workflow",
            service=service,
        )
    )

    assert [item.id for item in payload] == ["pm-projected-1"]
    assert payload[0].metadata["pm_task_id"] == "PM-1"
    assert service.list_calls == 0


def test_list_tasks_returns_director_task_pool_contract_fields(monkeypatch) -> None:
    workflow_tasks: list[dict[str, object]] = [
        {
            "id": "runtime-1",
            "subject": "Implement backend contract",
            "description": "Normalize Director task pool output",
            "status": "in_progress",
            "priority": "HIGH",
            "claimed_by": "director-worker-1",
            "current_file": "src/backend/polaris/delivery/http/v2/director.py",
            "metadata": {
                "pm_task_id": "PM-42",
                "goal": "Expose task pool details",
                "acceptance_criteria": [
                    {"description": "shows status buckets"},
                    {"title": "shows task details"},
                ],
                "target_files": ["src/backend/polaris/delivery/http/v2/director.py"],
                "dependencies": ["PM-41"],
                "runtime_execution": {"worker_id": "director-worker-1"},
            },
            "result": None,
        }
    ]
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "RUNNING"},
        local_tasks=[],
    )

    from polaris.cells.runtime.projection.internal.runtime_projection_service import RuntimeProjection

    async def _fake_build_async(workspace, cache_root=None, state=None):
        return RuntimeProjection(
            pm_local={},
            director_local={},
            workflow_archive={"status": {"tasks": {"task_rows": workflow_tasks}}},
            engine_fallback=None,
            task_rows=workflow_tasks,
        )

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _fake_build_async)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            source="workflow",
            service=service,
        )
    )

    assert len(payload) == 1
    task = payload[0]
    assert task.status == "RUNNING"
    assert task.goal == "Expose task pool details"
    assert task.acceptance == ["shows status buckets", "shows task details"]
    assert task.target_files == ["src/backend/polaris/delivery/http/v2/director.py"]
    assert task.dependencies == ["PM-41"]
    assert task.current_file == "src/backend/polaris/delivery/http/v2/director.py"
    assert task.worker == "director-worker-1"
    assert task.claimed_by == "director-worker-1"
    assert task.pm_task_id == "PM-42"


def test_list_tasks_normalizes_director_task_pool_statuses_and_filter(monkeypatch) -> None:
    workflow_tasks: list[dict[str, object]] = [
        {"id": "pending-1", "subject": "Ready", "status": "READY", "priority": "MEDIUM", "metadata": {}},
        {"id": "claimed-1", "subject": "Claimed", "status": "claimed", "priority": "MEDIUM", "metadata": {}},
        {"id": "running-1", "subject": "Running", "status": "IN_PROGRESS", "priority": "MEDIUM", "metadata": {}},
        {
            "id": "blocked-1",
            "subject": "Blocked",
            "status": "blocked",
            "priority": "MEDIUM",
            "blocked_by": ["pending-1"],
            "metadata": {},
        },
        {
            "id": "failed-1",
            "subject": "Failed",
            "status": "timeout",
            "priority": "MEDIUM",
            "error_message": "command timed out",
            "metadata": {},
        },
        {"id": "completed-1", "subject": "Done", "status": "completed", "priority": "MEDIUM", "metadata": {}},
    ]
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "RUNNING"},
        local_tasks=[],
    )

    from polaris.cells.runtime.projection.internal.runtime_projection_service import RuntimeProjection

    async def _fake_build_async(workspace, cache_root=None, state=None):
        return RuntimeProjection(
            pm_local={},
            director_local={},
            workflow_archive={"status": {"tasks": {"task_rows": workflow_tasks}}},
            engine_fallback=None,
            task_rows=workflow_tasks,
        )

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _fake_build_async)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            source="workflow",
            service=service,
        )
    )
    assert [task.status for task in payload] == [
        "PENDING",
        "CLAIMED",
        "RUNNING",
        "BLOCKED",
        "FAILED",
        "COMPLETED",
    ]
    assert payload[3].dependencies == ["pending-1"]
    assert payload[4].error == "command timed out"

    running_payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            status="running",
            source="workflow",
            service=service,
        )
    )
    assert [task.id for task in running_payload] == ["running-1"]


def test_list_tasks_auto_falls_back_to_snapshot_pm_contract_rows(monkeypatch) -> None:
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "IDLE"},
        local_tasks=[],
    )

    from polaris.cells.runtime.projection.internal.runtime_projection_service import RuntimeProjection

    async def _fake_build_async(workspace, cache_root=None, state=None):
        projection = RuntimeProjection(
            pm_local={},
            director_local={"running": False, "state": "IDLE"},
            workflow_archive=None,
            task_rows=[],
        )
        projection.snapshot = {
            "tasks": [
                {
                    "id": "PM-1",
                    "title": "Contract task",
                    "goal": "Verify contract fallback",
                    "status": "todo",
                }
            ]
        }
        return projection

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _fake_build_async)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            source="auto",
            service=service,
        )
    )

    assert [item.id for item in payload] == ["PM-1"]
    assert payload[0].status == "PENDING"
    assert payload[0].metadata["pm_task_id"] == "PM-1"


def test_list_tasks_auto_falls_back_to_local_when_workflow_empty(monkeypatch) -> None:
    local_tasks: list[dict[str, object]] = [
        {
            "id": "local-1",
            "subject": "Local Task",
            "description": "from local queue",
            "status": "PENDING",
            "priority": "MEDIUM",
            "claimed_by": None,
            "result": None,
            "metadata": {},
        }
    ]
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "RUNNING"},
        local_tasks=local_tasks,
    )

    # Mock RuntimeProjectionService.build_async for new implementation
    from polaris.cells.runtime.projection.internal.runtime_projection_service import RuntimeProjection

    async def _fake_build_async(workspace, cache_root=None, state=None):
        # Return empty workflow to trigger local fallback
        return RuntimeProjection(
            pm_local={},
            director_local={
                "running": True,
                "active_tasks": len(local_tasks),
                "task_rows": local_tasks,
            },
            workflow_archive={"tasks": []},  # Empty workflow
            engine_fallback=None,
        )

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _fake_build_async)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            source="auto",
            service=service,
        )
    )

    assert [item.id for item in payload] == ["local-1"]
    # New implementation uses projection data directly, doesn't call service
    assert service.list_calls == 0


def test_list_tasks_auto_prefers_workflow_rows(monkeypatch) -> None:
    workflow_tasks: list[dict[str, object]] = [
        {
            "id": "wf-1",
            "subject": "Workflow Task",
            "description": "from workflow queue",
            "status": "RUNNING",
            "priority": "MEDIUM",
            "claimed_by": None,
            "result": None,
            "metadata": {},
        }
    ]
    local_tasks: list[dict[str, object]] = [
        {
            "id": "local-1",
            "subject": "Local Task",
            "description": "from local queue",
            "status": "PENDING",
            "priority": "MEDIUM",
            "claimed_by": None,
            "result": None,
            "metadata": {},
        }
    ]
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "RUNNING"},
        local_tasks=local_tasks,
    )

    # Mock RuntimeProjectionService.build_async for new implementation
    from polaris.cells.runtime.projection.internal.runtime_projection_service import RuntimeProjection

    async def _fake_build_async(workspace, cache_root=None, state=None):
        # Return workflow tasks to test workflow preference
        return RuntimeProjection(
            pm_local={},
            director_local={
                "running": True,
                "active_tasks": len(local_tasks),
                "task_rows": local_tasks,
            },
            workflow_archive={"tasks": workflow_tasks},  # Has workflow tasks
            engine_fallback=None,
        )

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _fake_build_async)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            source="auto",
            service=service,
        )
    )

    assert [item.id for item in payload] == ["wf-1"]


def test_list_tasks_auto_keeps_local_terminal_rows(monkeypatch) -> None:
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "IDLE"},
        local_tasks=[],
    )

    from polaris.cells.runtime.projection.internal.runtime_projection_service import RuntimeProjection

    async def _fake_build_async(workspace, cache_root=None, state=None):
        return RuntimeProjection(
            pm_local={},
            director_local={
                "running": False,
                "state": "COMPLETED",
                "status": {
                    "state": "COMPLETED",
                    "tasks": {
                        "active": 0,
                        "task_rows": [
                            {
                                "id": "local-completed-1",
                                "subject": "Completed task",
                                "description": "from local terminal snapshot",
                                "status": "COMPLETED",
                                "priority": "MEDIUM",
                                "claimed_by": "director-worker",
                                "result": {"summary": "done"},
                                "metadata": {"pm_task_id": "PM-1"},
                            }
                        ],
                    },
                },
            },
            workflow_archive={"tasks": []},
            engine_fallback=None,
        )

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _fake_build_async)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            source="auto",
            service=service,
        )
    )

    assert [item.id for item in payload] == ["local-completed-1"]


def test_list_tasks_local_source_skips_projection(monkeypatch) -> None:
    local_tasks: list[dict[str, object]] = [
        {
            "id": "local-1",
            "subject": "Local Task",
            "description": "from local queue",
            "status": "PENDING",
            "priority": "MEDIUM",
            "claimed_by": None,
            "result": None,
            "metadata": {},
        }
    ]
    service = _FakeDirectorService(
        status={"workspace": "X:\\workspace", "state": "RUNNING"},
        local_tasks=local_tasks,
    )

    async def _boom(*, workspace, cache_root=None, state=None):
        raise AssertionError("projection should not be called for source=local")

    monkeypatch.setattr(v2_director.RuntimeProjectionService, "build_async", _boom)

    payload = asyncio.run(
        v2_director.list_tasks(
            request=_build_fake_request(),
            source="local",
            service=service,
        )
    )

    assert [item.id for item in payload] == ["local-1"]
    assert service.list_calls == 1
