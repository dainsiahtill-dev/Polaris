from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.cells.runtime.task_runtime.internal.execution_session import (
    TaskExecutionSession,
    build_task_execution_claim_result,
    build_task_runtime_execution_event_payload,
    build_task_runtime_metadata,
    is_terminal_session_status,
    is_terminal_task_row_status,
    project_task_row_runtime_state,
    task_row_status_counts,
    terminal_session_timestamp,
    terminal_task_status_value_for_session_status,
)


def _valid_session_payload() -> dict[str, object]:
    return {
        "session_id": "tx-1",
        "task_id": 7,
        "role_id": "director",
        "worker_id": "worker-1",
        "run_id": "",
        "status": "active",
        "claimed_at": "2026-01-01T00:00:00+00:00",
        "last_heartbeat_at": "2026-01-01T00:00:00+00:00",
        "lease_expires_at": "2026-01-01T00:02:00+00:00",
    }


@pytest.mark.parametrize(
    "field_name",
    ["session_id", "task_id", "role_id", "worker_id", "status", "lease_expires_at"],
)
def test_from_dict_requires_core_session_fields(field_name: str) -> None:
    payload = _valid_session_payload()
    payload.pop(field_name)

    with pytest.raises(ValueError, match=field_name):
        TaskExecutionSession.from_dict(payload)


@pytest.mark.parametrize("task_id", [0, -1, "not-an-int"])
def test_from_dict_rejects_invalid_task_id(task_id: object) -> None:
    payload = _valid_session_payload()
    payload["task_id"] = task_id

    with pytest.raises(ValueError, match="task_id"):
        TaskExecutionSession.from_dict(payload)


@pytest.mark.parametrize("status", ["", "running", "done"])
def test_from_dict_rejects_invalid_status(status: str) -> None:
    payload = _valid_session_payload()
    payload["status"] = status

    with pytest.raises(ValueError, match="status"):
        TaskExecutionSession.from_dict(payload)


@pytest.mark.parametrize("status", ["active", "completed", "failed", "suspended"])
def test_from_dict_accepts_known_statuses(status: str) -> None:
    payload = _valid_session_payload()
    payload["status"] = status

    session = TaskExecutionSession.from_dict(payload)

    assert session.session_id == "tx-1"
    assert session.task_id == 7
    assert session.status == status
    assert session.run_id == ""


@pytest.mark.parametrize(
    ("session_status", "task_status"),
    [
        ("completed", "completed"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
    ],
)
def test_terminal_session_status_projects_task_status(session_status: str, task_status: str) -> None:
    assert terminal_task_status_value_for_session_status(session_status) == task_status
    assert is_terminal_session_status(session_status) is True


@pytest.mark.parametrize("session_status", ["active", "suspended", "", None])
def test_non_terminal_session_status_has_no_task_status_projection(session_status: object) -> None:
    assert terminal_task_status_value_for_session_status(session_status) == ""
    assert is_terminal_session_status(session_status) is False


@pytest.mark.parametrize("task_status", ["completed", "failed", "cancelled"])
def test_terminal_task_row_status_projection(task_status: str) -> None:
    assert is_terminal_task_row_status(task_status) is True
    assert is_terminal_task_row_status(task_status.upper()) is True


@pytest.mark.parametrize("task_status", ["pending", "in_progress", "blocked", "active", "", None])
def test_non_terminal_task_row_status_projection(task_status: object) -> None:
    assert is_terminal_task_row_status(task_status) is False


def test_terminal_session_timestamp_uses_terminal_projection_priority() -> None:
    payload = _valid_session_payload()
    payload.update(
        {
            "status": "failed",
            "claimed_at": "2026-01-01T00:00:00+00:00",
            "last_heartbeat_at": "2026-01-01T00:01:00+00:00",
            "lease_expires_at": "2026-01-01T00:02:00+00:00",
            "released_at": "2026-01-01T00:03:00+00:00",
        }
    )
    session = TaskExecutionSession.from_dict(payload)

    assert terminal_session_timestamp(session) == 1767225780.0


def test_terminal_session_timestamp_returns_none_without_valid_projection_timestamp() -> None:
    payload = _valid_session_payload()
    payload.update(
        {
            "claimed_at": "not-a-date",
            "last_heartbeat_at": "not-a-date",
            "lease_expires_at": "not-a-date",
            "released_at": "not-a-date",
        }
    )
    session = TaskExecutionSession.from_dict(payload)

    assert terminal_session_timestamp(session) is None


def test_task_row_status_counts_projects_runtime_stats() -> None:
    rows = [
        {"id": 1, "status": "pending"},
        {"id": 2, "status": "pending", "blocked_by": [1]},
        {"id": 3, "status": "in_progress"},
        {"id": 4, "status": "completed"},
        {"id": 5, "status": "failed"},
        {"id": 6, "status": "blocked"},
        {"id": 7, "status": "cancelled"},
        {"id": 8, "status": "unknown"},
    ]

    assert task_row_status_counts(rows) == {
        "total": 8,
        "ready": 1,
        "pending": 2,
        "in_progress": 1,
        "completed": 1,
        "failed": 1,
        "blocked": 1,
        "cancelled": 1,
    }


def test_build_task_runtime_metadata_projects_session_state() -> None:
    payload = _valid_session_payload()
    payload.update(
        {
            "external_task_id": "external-task-7",
            "context_summary": "context summary",
            "last_error": "last error",
            "last_result_summary": "done",
            "attempt": 3,
            "resume_count": 2,
            "run_id": "run-123",
        }
    )
    session = TaskExecutionSession.from_dict(payload)

    metadata = build_task_runtime_metadata(
        session=session,
        effective_status="IN_PROGRESS",
        resume_state="RESUMABLE",
        extra_metadata={"external_task_id": "", "preserved": "value"},
    )

    runtime_execution = metadata["runtime_execution"]
    assert runtime_execution["session_id"] == "tx-1"
    assert runtime_execution["effective_status"] == "in_progress"
    assert runtime_execution["resume_state"] == "resumable"
    assert runtime_execution["resume_available"] is True
    assert metadata["claimed_by"] == "worker-1"
    assert metadata["last_claimed_by"] == "worker-1"
    assert metadata["claim_attempt"] == 3
    assert metadata["resume_count"] == 2
    assert metadata["workflow_run_id"] == "run-123"
    assert metadata["external_task_id"] == "external-task-7"
    assert metadata["last_execution_error"] == "last error"
    assert metadata["last_execution_summary"] == "done"
    assert metadata["last_context_summary"] == "context summary"
    assert metadata["preserved"] == "value"


def test_build_task_runtime_execution_event_payload_projects_runtime_state() -> None:
    payload = _valid_session_payload()
    payload.update(
        {
            "run_id": "run-123",
            "last_error": "ignored when summary exists",
            "last_result_summary": "done",
            "attempt": 2,
            "resume_count": 1,
        }
    )
    session = TaskExecutionSession.from_dict(payload)
    event_payload = build_task_runtime_execution_event_payload(
        event_type="CLAIMED",
        workspace="/tmp/workspace",
        task_row={
            "id": 7,
            "status": "in_progress",
            "subject": "task subject",
            "claimed_by": "worker-1",
            "last_claimed_by": "worker-1",
            "metadata": {
                "runtime_execution": {
                    "effective_status": "in_progress",
                    "resume_state": "resumed",
                    "resume_available": False,
                },
                "factory_run_id": "factory-1",
                "factory_bench_session_id": "bench-1",
                "factory_bench_project_id": "L1-01",
            },
        },
        session=session,
        details={"source": "unit"},
        timestamp="2026-01-01T00:00:00+00:00",
    )

    assert event_payload["event_type"] == "claimed"
    assert event_payload["workspace"] == "/tmp/workspace"
    assert event_payload["task_id"] == "7"
    assert event_payload["execution_state"] == "in_progress"
    assert event_payload["session_id"] == "tx-1"
    assert event_payload["run_id"] == "run-123"
    assert event_payload["attempt"] == 2
    assert event_payload["resume_count"] == 1
    assert event_payload["resume_state"] == "resumed"
    assert event_payload["last_result_summary"] == "done"
    assert event_payload["details"] == {"source": "unit"}
    assert event_payload["timestamp"] == "2026-01-01T00:00:00+00:00"
    assert event_payload["factory_run_id"] == "factory-1"
    assert event_payload["factory_bench_session_id"] == "bench-1"
    assert event_payload["factory_bench_project_id"] == "L1-01"


def test_build_task_execution_claim_result_projects_success_shape() -> None:
    session = TaskExecutionSession.from_dict({**_valid_session_payload(), "run_id": "run-1"})

    result = build_task_execution_claim_result(
        success=True,
        reason="claimed",
        task_row={"id": 7, "status": "in_progress"},
        session=session,
        resumed=False,
        claim_applied=True,
    )

    assert result["success"] is True
    assert result["reason"] == "claimed"
    assert result["task"] == {"id": 7, "status": "in_progress"}
    assert result["session"]["session_id"] == "tx-1"
    assert result["session"]["run_id"] == "run-1"
    assert result["resumed"] is False
    assert result["claim_applied"] is True
    assert "reconcile_error" not in result


def test_build_task_execution_claim_result_projects_terminal_reject_shape() -> None:
    session = TaskExecutionSession.from_dict({**_valid_session_payload(), "status": "failed"})

    result = build_task_execution_claim_result(
        success=False,
        reason="task_terminal",
        task_row={"id": 7, "status": "failed"},
        session=session,
        reconciled_from_terminal_session=False,
        reconcile_error="terminal_row_conflict",
    )

    assert result["success"] is False
    assert result["reason"] == "task_terminal"
    assert result["task"]["status"] == "failed"
    assert result["session"]["status"] == "failed"
    assert result["reconciled_from_terminal_session"] is False
    assert result["reconcile_error"] == "terminal_row_conflict"
    assert "claim_applied" not in result


def test_project_task_row_runtime_state_uses_active_session_projection() -> None:
    payload = _valid_session_payload()
    payload.update({"run_id": "run-active", "resume_count": 1})
    session = TaskExecutionSession.from_dict(payload)

    projected = project_task_row_runtime_state(
        {"id": 7, "status": "pending", "metadata": {"claimed_by": "stale-worker"}},
        task_status_value="pending",
        session=session,
        now=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )

    assert projected["raw_status"] == "pending"
    assert projected["status"] == "in_progress"
    assert projected["claimed_by"] == "worker-1"
    assert projected["last_claimed_by"] == "worker-1"
    assert projected["resume_state"] == "resumed"
    assert projected["resume_available"] is False
    assert projected["workflow_run_id"] == "run-active"
    assert projected["session_id"] == "tx-1"
    assert projected["claim_attempt"] == 1
    runtime_execution = projected["metadata"]["runtime_execution"]
    assert runtime_execution["effective_status"] == "in_progress"
    assert runtime_execution["resume_state"] == "resumed"
    assert runtime_execution["raw_status"] == "pending"


def test_project_task_row_runtime_state_marks_superseded_terminal_session() -> None:
    payload = _valid_session_payload()
    payload.update({"status": "failed", "run_id": "run-failed", "last_error": "old failure"})
    session = TaskExecutionSession.from_dict(payload)

    projected = project_task_row_runtime_state(
        {"id": 7, "status": "pending", "metadata": {}},
        task_status_value="pending",
        session=session,
        terminal_session_superseded=True,
    )

    assert projected["status"] == "pending"
    assert projected["claimed_by"] == ""
    assert projected["workflow_run_id"] == "run-failed"
    assert projected["last_error"] == "old failure"
    runtime_execution = projected["metadata"]["runtime_execution"]
    assert runtime_execution["effective_status"] == "pending"
    assert runtime_execution["superseded_terminal_session_status"] == "failed"
    assert runtime_execution["session_projection_authority"] == "row_reset_after_terminal_session"
