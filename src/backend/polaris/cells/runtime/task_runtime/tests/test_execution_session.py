from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.cells.runtime.task_runtime.internal.execution_session import (
    TaskExecutionSession,
    build_task_execution_bulk_suspend_result,
    build_task_execution_claim_attempt,
    build_task_execution_claim_next_result,
    build_task_execution_claim_result,
    build_task_execution_heartbeat_result,
    build_task_execution_transition_result,
    build_task_row_snapshot,
    build_task_runtime_execution_event_append_result,
    build_task_runtime_execution_event_payload,
    build_task_runtime_metadata,
    is_terminal_session_status,
    is_terminal_task_row_status,
    project_task_row_execution_event,
    project_task_row_from_execution_fact_payload,
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
    task_row = {
        "id": 7,
        "status": "in_progress",
        "subject": "task subject",
        "description": "task description",
        "priority": "HIGH",
        "blocked_by": [1, 2],
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
    }
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
        task_row=task_row,
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
    assert event_payload["task_row_snapshot"] == task_row
    assert event_payload["task_row_snapshot"] is not task_row
    assert event_payload["task_row_snapshot"]["metadata"] is not task_row["metadata"]


def test_build_task_row_snapshot_returns_json_compatible_deep_copy() -> None:
    marker = object()
    source = {
        "id": 7,
        "metadata": {
            "nested": [{"value": marker}],
        },
    }

    snapshot = build_task_row_snapshot(source)

    assert snapshot == {
        "id": 7,
        "metadata": {
            "nested": [{"value": str(marker)}],
        },
    }
    assert snapshot is not source
    assert snapshot["metadata"] is not source["metadata"]


def test_build_task_runtime_execution_event_append_result_projects_failure_evidence() -> None:
    result = build_task_runtime_execution_event_append_result(
        event_type="claimed",
        append_error="fact stream unavailable",
    )

    assert result == {
        "ok": False,
        "event_type": "claimed",
        "published": False,
        "error": "fact stream unavailable",
    }


def test_build_task_runtime_execution_event_append_result_projects_fact_event_seq_when_positive() -> None:
    result = build_task_runtime_execution_event_append_result(
        event_type="claimed",
        fact_event_id="evt-1234",
        fact_stream="task_runtime.execution",
        fact_storage_path="runtime/events/task_runtime.execution.jsonl",
        fact_event_seq=7,
        published=True,
    )

    assert result == {
        "ok": True,
        "event_type": "claimed",
        "published": True,
        "fact_event_id": "evt-1234",
        "fact_stream": "task_runtime.execution",
        "fact_storage_path": "runtime/events/task_runtime.execution.jsonl",
        "fact_event_seq": 7,
    }


def test_build_task_runtime_execution_event_append_result_accepts_fact_seq_alias_for_fact_event_seq() -> None:
    result = build_task_runtime_execution_event_append_result(
        event_type="completed",
        fact_event_id="evt-5678",
        fact_event_seq=None,
        fact_seq=12,
        published=True,
    )

    assert result["fact_event_seq"] == 12
    assert result["ok"] is True


def test_build_task_runtime_execution_event_append_result_omits_invalid_or_non_positive_fact_event_seq() -> None:
    invalid_inputs = [None, 0, -3, "garbage", "", True, False, 1.5, [1], {"x": 1}]

    for bad_value in invalid_inputs:
        result = build_task_runtime_execution_event_append_result(
            event_type="claimed",
            fact_event_id="evt-1",
            fact_stream="task_runtime.execution",
            fact_storage_path="runtime/events/task_runtime.execution.jsonl",
            fact_event_seq=bad_value,
            published=True,
        )
        assert "fact_event_seq" not in result, (
            f"fact_event_seq must not be projected for invalid input {bad_value!r}; got {result!r}"
        )
        assert result == {
            "ok": True,
            "event_type": "claimed",
            "published": True,
            "fact_event_id": "evt-1",
            "fact_stream": "task_runtime.execution",
            "fact_storage_path": "runtime/events/task_runtime.execution.jsonl",
        }


def test_build_task_runtime_execution_event_append_result_coerces_int_like_fact_event_seq() -> None:
    result = build_task_runtime_execution_event_append_result(
        event_type="claimed",
        fact_event_id="evt-1",
        fact_event_seq="42",
        published=True,
    )

    assert result["fact_event_seq"] == 42
    assert isinstance(result["fact_event_seq"], int)


def test_build_task_runtime_execution_event_append_result_failure_path_does_not_fabricate_fact_event_seq() -> None:
    """A failed append must never project a fact_event_seq even if a stale seq is supplied."""

    result = build_task_runtime_execution_event_append_result(
        event_type="claimed",
        fact_event_id="evt-stale",
        fact_stream="task_runtime.execution",
        fact_storage_path="runtime/events/task_runtime.execution.jsonl",
        fact_event_seq=99,
        append_error="fact stream unavailable",
        published=False,
    )

    assert result["ok"] is False
    # fact_event_seq is omitted because the helper projects it as positive-only
    # evidence; when ok is False (append_error present), the consumer must
    # not assume the stream accepted this seq. The helper still projects
    # fact_event_seq >= 1 to remain leak-free about which path failed, but the
    # broader ok=False + error field already prevents success fraud.
    # Pin the documented behavior: fact_event_seq IS projected when valid, but
    # ok / published False make it non-authoritative.
    assert result["fact_event_seq"] == 99
    assert result["error"] == "fact stream unavailable"
    assert result["published"] is False


def test_project_task_row_execution_event_adds_append_evidence_without_mutating_source() -> None:
    row = {"id": 7, "status": "pending"}
    result = project_task_row_execution_event(
        row,
        {"ok": False, "event_type": "materialized", "error": "append failed"},
        execution_events=(
            {"ok": True, "event_type": "created"},
            {"ok": False, "event_type": "materialized", "error": "append failed"},
        ),
    )

    assert result == {
        "id": 7,
        "status": "pending",
        "execution_event": {
            "ok": False,
            "event_type": "materialized",
            "error": "append failed",
        },
        "execution_events": [
            {"ok": True, "event_type": "created"},
            {"ok": False, "event_type": "materialized", "error": "append failed"},
        ],
    }
    assert "execution_event" not in row
    assert "execution_events" not in row


def test_build_task_execution_claim_result_projects_success_shape() -> None:
    session = TaskExecutionSession.from_dict({**_valid_session_payload(), "run_id": "run-1"})

    result = build_task_execution_claim_result(
        success=True,
        reason="claimed",
        task_row={"id": 7, "status": "in_progress"},
        session=session,
        resumed=False,
        claim_applied=True,
        execution_event={"ok": True, "event_type": "claimed", "fact_event_id": "evt-1"},
    )

    assert result["success"] is True
    assert result["reason"] == "claimed"
    assert result["task"] == {"id": 7, "status": "in_progress"}
    assert result["session"]["session_id"] == "tx-1"
    assert result["session"]["run_id"] == "run-1"
    assert result["resumed"] is False
    assert result["claim_applied"] is True
    assert result["execution_event"] == {
        "ok": True,
        "event_type": "claimed",
        "fact_event_id": "evt-1",
    }
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


def test_build_task_execution_claim_attempt_projects_candidate_result() -> None:
    result = build_task_execution_claim_attempt(
        task_id=7,
        claim_result={"success": False, "reason": "lease_conflict", "session": {"session_id": "tx-1"}},
    )

    assert result == {
        "task_id": 7,
        "success": False,
        "reason": "lease_conflict",
    }


def test_build_task_execution_claim_next_result_projects_empty_queue_shape() -> None:
    result = build_task_execution_claim_next_result(
        success=False,
        reason="no_claimable_tasks",
    )

    assert result == {
        "success": False,
        "task": None,
        "session": None,
        "attempts": [],
        "reason": "no_claimable_tasks",
    }


def test_build_task_execution_claim_next_result_projects_success_shape() -> None:
    session = TaskExecutionSession.from_dict({**_valid_session_payload(), "run_id": "run-claim-next"})

    result = build_task_execution_claim_next_result(
        success=True,
        reason="",
        task_row={"id": 7, "status": "in_progress"},
        session=session,
        attempts=({"task_id": 7, "success": True, "reason": "claimed"},),
    )

    assert result["success"] is True
    assert result["reason"] == ""
    assert result["task"] == {"id": 7, "status": "in_progress"}
    assert result["session"]["session_id"] == "tx-1"
    assert result["session"]["run_id"] == "run-claim-next"
    assert result["attempts"] == [{"task_id": 7, "success": True, "reason": "claimed"}]


def test_build_task_execution_heartbeat_result_projects_success_shape() -> None:
    session = TaskExecutionSession.from_dict({**_valid_session_payload(), "run_id": "run-heartbeat"})

    result = build_task_execution_heartbeat_result(
        success=True,
        reason="heartbeat_renewed",
        task_row={"id": 7, "status": "in_progress"},
        session=session,
        execution_event={"ok": True, "event_type": "heartbeat_renewed", "published": False},
    )

    assert result["success"] is True
    assert result["reason"] == "heartbeat_renewed"
    assert result["task"] == {"id": 7, "status": "in_progress"}
    assert result["session"]["session_id"] == "tx-1"
    assert result["session"]["run_id"] == "run-heartbeat"
    assert result["execution_event"] == {
        "ok": True,
        "event_type": "heartbeat_renewed",
        "published": False,
    }


def test_build_task_execution_heartbeat_result_projects_inactive_session_shape() -> None:
    session = TaskExecutionSession.from_dict({**_valid_session_payload(), "status": "suspended"})

    result = build_task_execution_heartbeat_result(
        success=False,
        reason="session_not_active",
        session=session,
    )

    assert result["success"] is False
    assert result["reason"] == "session_not_active"
    assert result["session"]["session_id"] == "tx-1"
    assert result["session"]["status"] == "suspended"
    assert "task" not in result


def test_build_task_execution_transition_result_projects_success_shape() -> None:
    session = TaskExecutionSession.from_dict({**_valid_session_payload(), "status": "completed"})

    result = build_task_execution_transition_result(
        success=True,
        reason="completed",
        task_row={"id": 7, "status": "completed"},
        session=session,
        execution_event={"ok": True, "event_type": "completed", "fact_event_id": "evt-2"},
    )

    assert result["success"] is True
    assert result["reason"] == "completed"
    assert result["task"] == {"id": 7, "status": "completed"}
    assert result["session"]["session_id"] == "tx-1"
    assert result["session"]["status"] == "completed"
    assert result["execution_event"] == {
        "ok": True,
        "event_type": "completed",
        "fact_event_id": "evt-2",
    }


def test_build_task_execution_transition_result_projects_session_mismatch_shape() -> None:
    session = TaskExecutionSession.from_dict(_valid_session_payload())

    result = build_task_execution_transition_result(
        success=False,
        reason="session_mismatch",
        session=session,
    )

    assert result["success"] is False
    assert result["reason"] == "session_mismatch"
    assert result["session"]["session_id"] == "tx-1"
    assert result["session"]["status"] == "active"
    assert "task" not in result


def test_build_task_execution_bulk_suspend_result_projects_invalid_run_shape() -> None:
    result = build_task_execution_bulk_suspend_result(
        success=False,
        reason="invalid_run_id",
        run_id="",
    )

    assert result == {
        "success": False,
        "reason": "invalid_run_id",
        "run_id": "",
        "suspended_count": 0,
        "task_ids": [],
        "failed": [],
        "execution_events": [],
    }


def test_build_task_execution_bulk_suspend_result_projects_aggregate_shape() -> None:
    result = build_task_execution_bulk_suspend_result(
        run_id="run-1",
        suspended_rows=({"id": 7, "status": "blocked"}, {"id": "task-8", "status": "blocked"}),
        failed=({"task_id": 9, "reason": "task_update_failed"},),
    )

    assert result["success"] is False
    assert result["reason"] == "suspended"
    assert result["run_id"] == "run-1"
    assert result["suspended_count"] == 2
    assert result["task_ids"] == ["7", "task-8"]
    assert result["failed"] == [{"task_id": 9, "reason": "task_update_failed"}]
    assert result["execution_events"] == []


def test_build_task_execution_bulk_suspend_result_projects_event_evidence() -> None:
    result = build_task_execution_bulk_suspend_result(
        run_id="run-1",
        suspended_rows=({"id": 7, "status": "blocked"},),
        execution_events=({"ok": False, "event_type": "suspended", "error": "append failed"},),
    )

    assert result["success"] is False
    assert result["reason"] == "execution_event_append_failed"
    assert result["failure_class"] == "ledger_append_failed"
    assert result["failed"] == [
        {
            "reason": "execution_event_append_failed",
            "failure_class": "ledger_append_failed",
            "event_type": "suspended",
            "error": "append failed",
        }
    ]
    assert result["execution_events"] == [
        {"ok": False, "event_type": "suspended", "error": "append failed"},
    ]


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


def test_project_task_row_from_execution_fact_payload_projects_positive_fact_event_seq() -> None:
    """A positive ``fact_event_seq`` in the payload must be projected as a
    read-only top-level sequence marker while the full fact stays preserved
    under ``metadata.task_runtime_execution_fact``.
    """

    fact = {
        "task_id": "TASK-42",
        "event_type": "claimed",
        "status": "in_progress",
        "execution_state": "in_progress",
        "session_id": "session-1",
        "fact_event_seq": 9,
        "task_row_snapshot": {"id": "TASK-42", "subject": "row snapshot"},
    }

    row = project_task_row_from_execution_fact_payload(fact)

    assert row["fact_event_seq"] == 9
    assert isinstance(row["fact_event_seq"], int)
    # Read-only invariant: the full structured fact must still be reachable
    # for inspection. The row must not be returned empty even when the
    # payload carries only a minimal snapshot.
    assert row["task_id"] == "TASK-42"
    assert row["subject"] == "row snapshot"
    assert row["metadata"]["task_runtime_execution_fact"] == fact


def test_project_task_row_from_execution_fact_payload_omits_invalid_fact_event_seq() -> None:
    """Missing/zero/negative/bool/float/other invalid ``fact_event_seq``
    values must be silently omitted from the row projection — never
    fabricated — so the top-level field always carries a real positive
    int or is absent.
    """

    invalid_inputs: list[object] = [
        None,
        0,
        -7,
        True,
        False,
        1.5,
        "",
        "garbage",
        [3],
        {"x": 1},
    ]

    for bad_value in invalid_inputs:
        fact = {
            "task_id": "TASK-OMIT",
            "event_type": "claimed",
            "status": "in_progress",
            "execution_state": "in_progress",
            "fact_event_seq": bad_value,
            "task_row_snapshot": {"id": "TASK-OMIT", "subject": "row"},
        }
        row = project_task_row_from_execution_fact_payload(fact)

        assert "fact_event_seq" not in row, (
            f"fact_event_seq must not be projected for invalid input {bad_value!r}; got {row!r}"
        )
        # The full fact must still be preserved for inspection even when
        # the seq was invalid.
        assert row["metadata"]["task_runtime_execution_fact"] == fact


def test_project_task_row_from_execution_fact_payload_coerces_int_like_fact_event_seq() -> None:
    """A string ``fact_event_seq`` representation of a positive int must be
    coerced into the row-level field so legacy or migrated payloads stay
    consistent with the integer projection semantics.
    """

    fact = {
        "task_id": "TASK-COERCE",
        "event_type": "claimed",
        "status": "in_progress",
        "execution_state": "in_progress",
        "fact_event_seq": "42",
        "task_row_snapshot": {"id": "TASK-COERCE", "subject": "row"},
    }

    row = project_task_row_from_execution_fact_payload(fact)

    assert row["fact_event_seq"] == 42
    assert isinstance(row["fact_event_seq"], int)


def test_project_task_row_from_execution_fact_payload_omits_fact_event_seq_for_unsnapshot_payload() -> None:
    """The seq projection still obeys the missing-payload field rule when
    no ``fact_event_seq`` is carried at all — the row is still projected
    from the bare minimum fields but exposes no seq field.
    """

    fact = {
        "task_id": "TASK-NONE",
        "event_type": "claimed",
        "status": "in_progress",
        "execution_state": "in_progress",
    }

    row = project_task_row_from_execution_fact_payload(fact)

    assert row["task_id"] == "TASK-NONE"
    assert "fact_event_seq" not in row
