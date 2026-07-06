"""PM dispatch taskboard-mainline convergence tests."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.cli.pm.engine.taskboard import (
    _build_taskboard_runtime,
    _finalize_taskboard_runtime_entry,
    _select_taskboard_ready_batch,
    _taskboard_runtime_claim_failures,
    _taskboard_runtime_transition_failures,
)


def _task(task_id: str, *, depends_on: list[str] | None = None) -> dict[str, object]:
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "goal": f"Deliver {task_id}",
        "priority": 1,
        "dependencies": list(depends_on or []),
        "target_files": [f"src/{task_id.lower()}.py"],
    }


def test_pm_taskboard_mainline_uses_task_runtime_rows(tmp_path: Path) -> None:
    runtime = _build_taskboard_runtime(
        workspace_full=str(tmp_path),
        run_id="run-1",
        director_tasks=[_task("TASK-1"), _task("TASK-2", depends_on=["TASK-1"])],
        max_workers=2,
    )

    assert "task_runtime" in runtime
    assert "board" not in runtime
    assert "module" not in runtime
    assert runtime["pm_id_to_board_id"]

    first_batch = _select_taskboard_ready_batch(runtime, max_workers=2)

    assert len(first_batch) == 1
    assert first_batch[0]["task"]["id"] == "TASK-1"
    assert first_batch[0]["task_runtime_session_id"]

    first_row_id = int(first_batch[0]["board_id"])
    transition = _finalize_taskboard_runtime_entry(
        runtime,
        board_id=first_row_id,
        session_id=str(first_batch[0]["task_runtime_session_id"]),
        pm_status="done",
        metadata={"test_projection": True},
        result_summary="completed by test",
    )

    assert transition["success"] is True

    second_batch = _select_taskboard_ready_batch(
        runtime,
        max_workers=2,
        dispatched_board_ids={first_row_id},
    )

    assert len(second_batch) == 1
    assert second_batch[0]["task"]["id"] == "TASK-2"
    assert second_batch[0]["task_runtime_session_id"]


def test_pm_taskboard_mainline_suspends_needs_continue(tmp_path: Path) -> None:
    runtime = _build_taskboard_runtime(
        workspace_full=str(tmp_path),
        run_id="run-continue",
        director_tasks=[_task("TASK-1")],
        max_workers=1,
    )
    batch = _select_taskboard_ready_batch(runtime, max_workers=1)
    row_id = int(batch[0]["board_id"])

    transition = _finalize_taskboard_runtime_entry(
        runtime,
        board_id=row_id,
        session_id=str(batch[0]["task_runtime_session_id"]),
        pm_status="needs_continue",
        metadata={"last_pm_status": "needs_continue"},
        result_summary="needs another Director round",
    )

    assert transition["success"] is True
    task_runtime = runtime["task_runtime"]
    row = task_runtime.get_task(row_id)
    assert row is not None
    assert row["status"] in {"blocked", "pending"}
    metadata = row.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata["last_pm_status"] == "needs_continue"


def test_pm_taskboard_mainline_records_claim_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _build_taskboard_runtime(
        workspace_full=str(tmp_path),
        run_id="run-claim-failure",
        director_tasks=[_task("TASK-1")],
        max_workers=1,
    )

    def fake_claim_execution(self, task_id, **kwargs):
        del self, kwargs
        return {
            "success": False,
            "reason": "lease_conflict",
            "task": {"id": task_id},
        }

    monkeypatch.setattr(TaskRuntimeService, "claim_execution", fake_claim_execution)

    batch = _select_taskboard_ready_batch(runtime, max_workers=1)

    assert batch == []
    assert _taskboard_runtime_claim_failures(runtime) == [
        {
            "success": False,
            "board_id": 1,
            "worker_id": "director-worker-1",
            "reason": "lease_conflict",
            "claim_result": {
                "success": False,
                "reason": "lease_conflict",
                "task": {"id": 1},
            },
        }
    ]


def test_pm_taskboard_mainline_records_claim_execution_event_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _build_taskboard_runtime(
        workspace_full=str(tmp_path),
        run_id="run-claim-event-failure",
        director_tasks=[_task("TASK-1")],
        max_workers=1,
    )

    def fake_claim_execution(self, task_id, **kwargs):
        del self, kwargs
        return {
            "success": True,
            "reason": "claimed",
            "task": {"id": task_id},
            "session": {"session_id": "session-1"},
            "execution_event": {
                "ok": False,
                "event_type": "claimed",
                "error_code": "fact_stream_unavailable",
            },
        }

    monkeypatch.setattr(TaskRuntimeService, "claim_execution", fake_claim_execution)

    batch = _select_taskboard_ready_batch(runtime, max_workers=1)

    assert batch == []
    assert _taskboard_runtime_claim_failures(runtime) == [
        {
            "success": False,
            "board_id": 1,
            "worker_id": "director-worker-1",
            "reason": "task_runtime_claim_execution_event_append_failed",
            "claim_result": {
                "success": False,
                "reason": "task_runtime_claim_execution_event_append_failed",
                "task": {"id": 1},
                "session": {"session_id": "session-1"},
                "execution_event": {
                    "ok": False,
                    "event_type": "claimed",
                    "error_code": "fact_stream_unavailable",
                },
            },
        }
    ]


def test_pm_taskboard_mainline_records_transition_failure(tmp_path: Path) -> None:
    runtime = _build_taskboard_runtime(
        workspace_full=str(tmp_path),
        run_id="run-transition-failure",
        director_tasks=[_task("TASK-1")],
        max_workers=1,
    )
    batch = _select_taskboard_ready_batch(runtime, max_workers=1)
    row_id = int(batch[0]["board_id"])

    transition = _finalize_taskboard_runtime_entry(
        runtime,
        board_id=row_id,
        session_id="wrong-session",
        pm_status="done",
        metadata={"last_pm_status": "done"},
        result_summary="completed by stale worker",
    )

    assert transition["success"] is False
    failures = _taskboard_runtime_transition_failures(runtime)
    assert failures == [
        {
            "success": False,
            "board_id": row_id,
            "pm_status": "done",
            "reason": transition["reason"],
            "transition_result": transition["transition_result"],
        }
    ]
    assert failures[0]["transition_result"]["success"] is False


def test_pm_taskboard_mainline_blocks_create_execution_event_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_create_task_row = TaskRuntimeService.create_task_row

    def fake_create_task_row(self, **kwargs):
        row = original_create_task_row(self, **kwargs)
        row["execution_event"] = {
            "ok": False,
            "event_type": "created",
            "error_code": "fact_stream_unavailable",
        }
        return row

    monkeypatch.setattr(TaskRuntimeService, "create_task_row", fake_create_task_row)

    runtime = _build_taskboard_runtime(
        workspace_full=str(tmp_path),
        run_id="run-create-event-failure",
        director_tasks=[_task("TASK-1")],
        max_workers=1,
    )

    assert _select_taskboard_ready_batch(runtime, max_workers=1) == []
    assert runtime["board_id_to_task"] == {}
    assert _taskboard_runtime_transition_failures(runtime) == [
        {
            "success": False,
            "board_id": 1,
            "pm_status": "created",
            "reason": "task_runtime_create_execution_event_append_failed",
            "transition_result": {
                "ok": False,
                "event_type": "created",
                "error_code": "fact_stream_unavailable",
            },
        }
    ]


def test_pm_taskboard_mainline_blocks_dependency_update_execution_event_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_update_task_row = TaskRuntimeService.update_task_row

    def fake_update_task_row(self, task_id, **kwargs):
        row = original_update_task_row(self, task_id, **kwargs)
        if isinstance(row, dict):
            row["execution_event"] = {
                "ok": False,
                "event_type": "updated",
                "error_code": "fact_stream_unavailable",
            }
        return row

    monkeypatch.setattr(TaskRuntimeService, "update_task_row", fake_update_task_row)

    runtime = _build_taskboard_runtime(
        workspace_full=str(tmp_path),
        run_id="run-dependency-event-failure",
        director_tasks=[_task("TASK-1")],
        max_workers=1,
    )

    assert _select_taskboard_ready_batch(runtime, max_workers=1) == []
    assert runtime["board_id_to_task"] == {}
    assert _taskboard_runtime_transition_failures(runtime) == [
        {
            "success": False,
            "board_id": 1,
            "pm_status": "dependency_update",
            "reason": "task_runtime_dependency_update_execution_event_append_failed",
            "transition_result": {
                "ok": False,
                "event_type": "updated",
                "error_code": "fact_stream_unavailable",
            },
        }
    ]
