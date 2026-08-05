"""GR2A focused tests for strict resident Goal/Attempt persistence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from polaris.cells.resident.autonomy.internal.goal_attempt_ledger import transition_goal_state
from polaris.cells.resident.autonomy.public import (
    ObserveResidentGoalAttemptCommandV1,
    QueryResidentGoalExecutionV1,
    ResidentGoalAttemptStatusV1,
    ResidentGoalExecutionStatusV1,
    ResidentGoalLifecycleErrorV1,
    ResidentGoalStateV1,
    SettleResidentGoalAttemptCommandV1,
    StartResidentGoalAttemptCommandV1,
    observe_resident_goal_attempt,
    query_resident_goal_execution,
    settle_resident_goal_attempt,
    start_resident_goal_attempt,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ResidentGoalStateV1.PENDING, ResidentGoalStateV1.APPROVED),
        (ResidentGoalStateV1.PENDING, ResidentGoalStateV1.REJECTED),
        (ResidentGoalStateV1.APPROVED, ResidentGoalStateV1.MATERIALIZED),
        (ResidentGoalStateV1.MATERIALIZED, ResidentGoalStateV1.ARCHIVED),
        (ResidentGoalStateV1.REJECTED, ResidentGoalStateV1.ARCHIVED),
    ],
)
def test_goal_transition_truth_table_allows_only_declared_edges(
    current: ResidentGoalStateV1,
    target: ResidentGoalStateV1,
) -> None:
    assert transition_goal_state(current, target, current_revision=3, expected_revision=3) == (
        target,
        4,
        True,
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ResidentGoalStateV1.PENDING, ResidentGoalStateV1.MATERIALIZED),
        (ResidentGoalStateV1.APPROVED, ResidentGoalStateV1.REJECTED),
        (ResidentGoalStateV1.REJECTED, ResidentGoalStateV1.APPROVED),
        (ResidentGoalStateV1.ARCHIVED, ResidentGoalStateV1.PENDING),
    ],
)
def test_goal_transition_truth_table_rejects_other_edges(
    current: ResidentGoalStateV1,
    target: ResidentGoalStateV1,
) -> None:
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        transition_goal_state(current, target, current_revision=3, expected_revision=3)
    assert exc_info.value.error_code == "invalid_goal_transition"


def test_goal_same_state_idempotent_and_revision_conflict_fails() -> None:
    assert transition_goal_state(
        ResidentGoalStateV1.APPROVED,
        ResidentGoalStateV1.APPROVED,
        current_revision=7,
        expected_revision=7,
    ) == (ResidentGoalStateV1.APPROVED, 7, False)
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        transition_goal_state(
            ResidentGoalStateV1.APPROVED,
            ResidentGoalStateV1.APPROVED,
            current_revision=7,
            expected_revision=6,
        )
    assert exc_info.value.error_code == "goal_revision_conflict"


def test_unknown_goal_state_fails_closed() -> None:
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        transition_goal_state("mystery", ResidentGoalStateV1.APPROVED, current_revision=0, expected_revision=0)  # type: ignore[arg-type]
    assert exc_info.value.error_code == "unknown_persisted_goal_state"


def test_start_idempotency_replay_and_semantic_conflict(tmp_path: Path) -> None:
    command = StartResidentGoalAttemptCommandV1(
        workspace=str(tmp_path),
        goal_id="goal-1",
        idempotency_key="start-1",
        run_id="run-1",
        expected_revision=0,
        max_attempts=2,
        no_progress_limit=2,
    )
    first = start_resident_goal_attempt(command)
    replay = start_resident_goal_attempt(command)
    assert replay == first
    assert first.status is ResidentGoalAttemptStatusV1.ACTIVE
    assert first.execution_status is ResidentGoalExecutionStatusV1.ACTIVE

    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        start_resident_goal_attempt(
            StartResidentGoalAttemptCommandV1(
                workspace=str(tmp_path),
                goal_id="goal-1",
                idempotency_key="start-1",
                run_id="run-1",
                expected_revision=0,
                max_attempts=3,
                no_progress_limit=2,
            )
        )
    assert exc_info.value.error_code == "goal_attempt_idempotency_conflict"


def test_attempt_revision_conflict(tmp_path: Path) -> None:
    start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-cas",
            idempotency_key="start-cas",
            run_id="run-cas",
            expected_revision=0,
        )
    )
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        observe_resident_goal_attempt(
            ObserveResidentGoalAttemptCommandV1(
                workspace=str(tmp_path),
                goal_id="goal-cas",
                attempt_id="goal-cas-attempt-1",
                idempotency_key="observe-cas",
                expected_revision=0,
                progress_fingerprint="fp-a",
            )
        )
    assert exc_info.value.error_code == "goal_revision_conflict"


def test_no_progress_streak_resets_and_blocks(tmp_path: Path) -> None:
    started = start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-progress",
            idempotency_key="start-progress",
            run_id="run-progress",
            expected_revision=0,
            no_progress_limit=2,
        )
    )
    revision = started.revision
    observations = []
    for index, fingerprint in enumerate(("fp-a", "fp-a", "fp-b", "fp-b", "fp-b"), start=1):
        receipt = observe_resident_goal_attempt(
            ObserveResidentGoalAttemptCommandV1(
                workspace=str(tmp_path),
                goal_id="goal-progress",
                attempt_id=started.attempt_id,
                idempotency_key=f"observe-{index}",
                expected_revision=revision,
                progress_fingerprint=fingerprint,
            )
        )
        observations.append(receipt)
        revision = receipt.revision

    assert [item.no_progress_streak for item in observations] == [0, 1, 0, 1, 2]
    assert observations[-1].status is ResidentGoalAttemptStatusV1.BLOCKED_NO_PROGRESS
    assert observations[-1].execution_status is ResidentGoalExecutionStatusV1.BLOCKED_NO_PROGRESS


def test_retry_budget_exhaustion_and_restart_recovery(tmp_path: Path) -> None:
    first = start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-retry",
            idempotency_key="start-1",
            run_id="run-retry-1",
            expected_revision=0,
            max_attempts=2,
        )
    )
    failed_first = settle_resident_goal_attempt(
        SettleResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-retry",
            attempt_id=first.attempt_id,
            idempotency_key="settle-1",
            expected_revision=first.revision,
            status=ResidentGoalAttemptStatusV1.FAILED,
        )
    )
    assert failed_first.execution_status is ResidentGoalExecutionStatusV1.RETRY_ELIGIBLE

    second = start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-retry",
            idempotency_key="start-2",
            run_id="run-retry-2",
            expected_revision=failed_first.revision,
            max_attempts=2,
        )
    )
    exhausted = settle_resident_goal_attempt(
        SettleResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-retry",
            attempt_id=second.attempt_id,
            idempotency_key="settle-2",
            expected_revision=second.revision,
            status=ResidentGoalAttemptStatusV1.FAILED,
        )
    )
    assert exhausted.execution_status is ResidentGoalExecutionStatusV1.EXHAUSTED

    recovered = query_resident_goal_execution(
        QueryResidentGoalExecutionV1(workspace=str(tmp_path), goal_id="goal-retry")
    )
    assert recovered.revision == exhausted.revision
    assert recovered.status is ResidentGoalExecutionStatusV1.EXHAUSTED
    assert recovered.attempt_count == 2


def test_success_waits_for_outcome_binding(tmp_path: Path) -> None:
    started = start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-success",
            idempotency_key="start-success",
            run_id="run-success",
            expected_revision=0,
        )
    )
    settled = settle_resident_goal_attempt(
        SettleResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-success",
            attempt_id=started.attempt_id,
            idempotency_key="settle-success",
            expected_revision=started.revision,
            status=ResidentGoalAttemptStatusV1.SUCCEEDED,
        )
    )
    assert settled.execution_status is ResidentGoalExecutionStatusV1.AWAITING_OUTCOME_BINDING


def test_query_missing_goal_is_zero_write(tmp_path: Path) -> None:
    before = tuple(tmp_path.rglob("*"))
    result = query_resident_goal_execution(
        QueryResidentGoalExecutionV1(workspace=str(tmp_path), goal_id="goal-missing")
    )
    after = tuple(tmp_path.rglob("*"))
    assert result.status is ResidentGoalExecutionStatusV1.READY
    assert result.revision == 0
    assert before == after == ()


def test_corrupt_stream_fails_closed(tmp_path: Path) -> None:
    stream = tmp_path / ".polaris/meta/resident/goals/goal-corrupt/attempts.v1.jsonl"
    stream.parent.mkdir(parents=True)
    stream.write_bytes(b"{not-json}\n")
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        query_resident_goal_execution(QueryResidentGoalExecutionV1(workspace=str(tmp_path), goal_id="goal-corrupt"))
    assert exc_info.value.error_code == "goal_attempt_stream_corrupt"


def test_completed_verified_is_reserved() -> None:
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        SettleResidentGoalAttemptCommandV1(
            workspace="/tmp/workspace",
            goal_id="goal-reserved",
            attempt_id="attempt-reserved",
            idempotency_key="settle-reserved",
            expected_revision=1,
            status="COMPLETED_VERIFIED",  # type: ignore[arg-type]
        )
    assert exc_info.value.error_code == "completed_verified_reserved"


def test_attempt_identity_carries_ordinal_run_and_start_evidence(tmp_path: Path) -> None:
    receipt = start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-identity",
            idempotency_key="start-identity",
            run_id="run-identity",
            expected_revision=0,
            evidence_refs=("run:receipt", "goal:evidence"),
        )
    )
    assert receipt.attempt_number == 1
    assert receipt.attempt_id == "goal-identity-attempt-1"
    assert receipt.workspace == str(tmp_path.resolve())
    assert receipt.run_id == "run-identity"
    assert receipt.evidence_refs == ("goal:evidence", "run:receipt")


def test_single_active_attempt_and_terminal_immutability(tmp_path: Path) -> None:
    started = start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-terminal",
            idempotency_key="start-terminal",
            run_id="run-terminal",
            expected_revision=0,
        )
    )
    with pytest.raises(ResidentGoalLifecycleErrorV1) as active_exc:
        start_resident_goal_attempt(
            StartResidentGoalAttemptCommandV1(
                workspace=str(tmp_path),
                goal_id="goal-terminal",
                idempotency_key="second-active",
                run_id="run-other",
                expected_revision=started.revision,
            )
        )
    assert active_exc.value.error_code == "invalid_goal_execution_transition"

    terminal = settle_resident_goal_attempt(
        SettleResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-terminal",
            attempt_id=started.attempt_id,
            idempotency_key="cancel-terminal",
            expected_revision=started.revision,
            status=ResidentGoalAttemptStatusV1.CANCELLED,
        )
    )
    with pytest.raises(ResidentGoalLifecycleErrorV1) as terminal_exc:
        start_resident_goal_attempt(
            StartResidentGoalAttemptCommandV1(
                workspace=str(tmp_path),
                goal_id="goal-terminal",
                idempotency_key="after-terminal",
                run_id="run-after-terminal",
                expected_revision=terminal.revision,
            )
        )
    assert terminal_exc.value.error_code == "invalid_goal_execution_transition"


def test_same_goal_id_isolated_by_workspace(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(left),
            goal_id="goal-shared",
            idempotency_key="start-left",
            run_id="run-left",
            expected_revision=0,
        )
    )
    right_projection = query_resident_goal_execution(
        QueryResidentGoalExecutionV1(workspace=str(right), goal_id="goal-shared")
    )
    assert right_projection.status is ResidentGoalExecutionStatusV1.READY
    assert right_projection.revision == 0


def test_query_ignores_legacy_pm_run_and_progress_claims(tmp_path: Path) -> None:
    legacy_path = tmp_path / ".polaris/meta/resident/goals.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "goal_id": "goal-legacy",
                        "title": "completed verified success",
                        "task_progress": 100,
                        "materialization_artifacts": {"pm_run": {"status": "completed"}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    projection = query_resident_goal_execution(
        QueryResidentGoalExecutionV1(workspace=str(tmp_path), goal_id="goal-legacy")
    )
    assert projection.status is ResidentGoalExecutionStatusV1.READY
    assert projection.attempt_count == 0


def test_valid_hash_with_unknown_attempt_status_fails_closed(tmp_path: Path) -> None:
    start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-status-corrupt",
            idempotency_key="start-status-corrupt",
            run_id="run-status-corrupt",
            expected_revision=0,
        )
    )
    stream = tmp_path / ".polaris/meta/resident/goals/goal-status-corrupt/attempts.v1.jsonl"
    record = json.loads(stream.read_text(encoding="utf-8"))
    record["receipt"]["status"] = "MYSTERY"
    record.pop("record_hash")
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record["record_hash"] = hashlib.sha256(canonical).hexdigest()
    stream.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        query_resident_goal_execution(
            QueryResidentGoalExecutionV1(workspace=str(tmp_path), goal_id="goal-status-corrupt")
        )
    assert exc_info.value.error_code == "goal_attempt_stream_corrupt"


def test_valid_hash_with_cross_workspace_receipt_fails_closed(tmp_path: Path) -> None:
    start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id="goal-workspace-corrupt",
            idempotency_key="start-workspace-corrupt",
            run_id="run-workspace-corrupt",
            expected_revision=0,
        )
    )
    stream = tmp_path / ".polaris/meta/resident/goals/goal-workspace-corrupt/attempts.v1.jsonl"
    record = json.loads(stream.read_text(encoding="utf-8"))
    record["receipt"]["workspace"] = str((tmp_path / "forged").resolve())
    record.pop("record_hash")
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record["record_hash"] = hashlib.sha256(canonical).hexdigest()
    stream.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        query_resident_goal_execution(
            QueryResidentGoalExecutionV1(workspace=str(tmp_path), goal_id="goal-workspace-corrupt")
        )
    assert exc_info.value.error_code == "goal_attempt_stream_corrupt"


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_field",
        "missing_field",
        "wrong_int_primitive",
        "wrong_refs_primitive",
    ],
)
def test_valid_hash_with_invalid_nested_receipt_schema_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    goal_id = f"goal-nested-{mutation}"
    start_resident_goal_attempt(
        StartResidentGoalAttemptCommandV1(
            workspace=str(tmp_path),
            goal_id=goal_id,
            idempotency_key=f"start-{mutation}",
            run_id=f"run-{mutation}",
            expected_revision=0,
        )
    )
    stream = tmp_path / f".polaris/meta/resident/goals/{goal_id}/attempts.v1.jsonl"
    record = json.loads(stream.read_text(encoding="utf-8"))
    receipt = record["receipt"]
    if mutation == "extra_field":
        receipt["forged_authority"] = True
    elif mutation == "missing_field":
        receipt.pop("run_id")
    elif mutation == "wrong_int_primitive":
        receipt["attempt_number"] = True
    else:
        receipt["evidence_refs"] = "not-a-list"
    record.pop("record_hash")
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record["record_hash"] = hashlib.sha256(canonical).hexdigest()
    stream.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        query_resident_goal_execution(QueryResidentGoalExecutionV1(workspace=str(tmp_path), goal_id=goal_id))
    assert exc_info.value.error_code == "goal_attempt_stream_corrupt"
