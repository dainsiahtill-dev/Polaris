"""Reopening a row must preserve its fulfilled session settlement."""

from pathlib import Path

from polaris.cells.runtime.task_runtime.tests.test_execution_attempt_settlement import _claim_attempt, _settle


def test_completed_row_rework_keeps_closed_session_and_allows_failure_abort(tmp_path: Path) -> None:
    service, task_id, identity = _claim_attempt(tmp_path)
    settled = _settle(service, identity, "completed")
    assert settled["success"] is True
    before = service._read_session(task_id)
    assert before is not None
    proof = dict(before.metadata["terminal_settlement_proof"])

    reopened = service.reopen_task_row(task_id, reason="factory_quality_same_task_local_rework_authorized")
    assert reopened is not None
    session = service._read_session(task_id)
    assert session is not None

    assert session.status == "completed"
    assert session.metadata["terminal_settlement_proof"] == proof
    assert service._directed_effect_inactive_pre_barrier_locked(session).allowed is True
    cancelled = service.cancel_task_row_for_factory_abort(task_id, factory_run_id="settlement-run")
    assert cancelled is not None and cancelled["status"] == "cancelled"


def test_reopen_recovers_only_authorized_legacy_rework_session_status(tmp_path: Path) -> None:
    service, task_id, identity = _claim_attempt(tmp_path)
    settled = _settle(service, identity, "completed")
    assert settled["success"] is True
    service.update_task_row(
        task_id,
        metadata={
            "factory_local_rework": {
                "action_kind": "publish_owner_rework",
                "factory_run_id": "settlement-run",
                "external_task_id": identity.external_task_id,
            }
        },
    )
    session = service._read_session(task_id)
    assert session is not None
    session.mark_suspended(reason="factory_quality_same_task_local_rework_authorized", resumable=True)
    assert service._write_session(session, allow_terminal_downgrade=True) is True
    assert service._directed_effect_inactive_pre_barrier_locked(session).allowed is False

    reopened = service.reopen_task_row(task_id, reason="native legacy rework recovery")

    assert reopened is not None and reopened.get("ok") is not False
    current = service._read_session(task_id)
    assert current is not None and current.status == "completed"
    assert current.metadata["terminal_settlement_proof"] == settled["session"]["metadata"]["terminal_settlement_proof"]
    assert service._directed_effect_inactive_pre_barrier_locked(current).allowed is True


def test_reopened_completed_task_claims_a_fresh_execution_identity(tmp_path: Path) -> None:
    service, task_id, identity = _claim_attempt(tmp_path)
    assert _settle(service, identity, "completed")["success"] is True
    assert service.reopen_task_row(task_id, reason="same-owner repair") is not None

    claimed = service.claim_execution(
        task_id,
        worker_id="settlement-worker",
        role_id="chief_engineer",
        run_id="settlement-run",
        external_task_id="settlement-task",
        selection_source="rework-test",
    )

    assert claimed["success"] is True
    assert claimed["session"]["session_id"] != identity.session_id
    assert "pending_terminal_intent" not in claimed["session"]["metadata"]
