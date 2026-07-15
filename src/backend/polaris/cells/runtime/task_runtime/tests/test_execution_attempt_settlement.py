"""Focused two-phase execution-attempt settlement regression tests."""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
    QueryFactEventsV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.internal import directed_effect_operation as deo_internal
from polaris.cells.runtime.task_runtime.public.contracts import (
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    AdmitDirectedEffectParentCommandV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FenceExpiredFactoryRunSessionsCommandV1,
    ParentCorrelationV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    admit_directed_effect_parent,
    enroll_directed_effect_parent_registry_stream,
)


def _bootstrap_task_runtime_fact_stream(workspace: Path) -> None:
    """Establish the FactStream authority required by TaskRuntime event I/O."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="task-runtime-execution-attempt-settlement-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )


def _claim_attempt(workspace: Path) -> tuple[TaskRuntimeService, int, TaskRuntimeExecutionAttemptIdentityV1]:
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_task_runtime_fact_stream(workspace)
    service = TaskRuntimeService(str(workspace))
    task_id = int(service.create_task_row(subject="two-phase settlement")["id"])
    claim = service.claim_execution(
        task_id,
        worker_id="settlement-worker",
        role_id="chief_engineer",
        run_id="settlement-run",
        external_task_id="settlement-task",
        selection_source="two-phase-settlement-test",
    )
    assert claim["success"] is True
    return service, task_id, TaskRuntimeExecutionAttemptIdentityV1(**dict(claim["execution_attempt"]))


def _parent_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> AdmitDirectedEffectParentCommandV1:
    return AdmitDirectedEffectParentCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        correlation=ParentCorrelationV1(turn_id="settlement-turn", batch_id="settlement-batch"),
        admission_idempotency_key="settlement-parent",
        expected_version=0,
        expected_seq=1,
        actor="settlement-test",
    )


def _enroll_parent_registry(identity: TaskRuntimeExecutionAttemptIdentityV1) -> None:
    result = enroll_directed_effect_parent_registry_stream(
        EnrollDirectedEffectParentRegistryStreamCommandV1(execution_attempt=identity)
    )
    assert result.ok is True


def _admit_parent(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> DirectedEffectParentBindingV1:
    result = admit_directed_effect_parent(_parent_command(identity))
    assert result.code == "parent_admitted"
    assert result.parent_binding is not None
    return result.parent_binding


def _registry_stream(identity: TaskRuntimeExecutionAttemptIdentityV1) -> str:
    registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(identity)
    return deo_internal._registry_stream_token(registry_identity)


def _registry_events(identity: TaskRuntimeExecutionAttemptIdentityV1) -> tuple[dict[str, Any], ...]:
    return query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=_registry_stream(identity),
            strict_integrity=True,
        )
    ).events


def _close_parent(binding: DirectedEffectParentBindingV1) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=binding.workspace,
            stream=binding.registry_stream_token,
            event_type="task_runtime.deo_parent_registry.v1.closed",
            payload={
                "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
                "stable_registry_identity": binding.registry_identity.to_record(),
                "previous_version": binding.registry_version,
                "version": binding.registry_version + 1,
                "parent_sequence": binding.parent_sequence,
                "binding_id": binding.binding_id,
                "close_evidence_ref": "fact://test/legacy-close",
                "close_evidence_hash": "c" * 64,
                "actor": "settlement-test",
                "recorded_at": "2026-07-15T00:00:00+00:00",
            },
            source="test",
            idempotency_key=f"legacy-close-{binding.binding_id}",
            expected_seq=binding.registry_version + 1,
            durability="fsync",
            strict_integrity=True,
        )
    )


def _settle(
    service: TaskRuntimeService,
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    *,
    timeout_seconds: float = 0.5,
) -> dict[str, Any]:
    return service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome=outcome,
            summary=f"settlement-{outcome}",
            lock_timeout_seconds=timeout_seconds,
        )
    )


def _hold_session_lock(workspace: str, task_id: int, ready_path: str, hold_seconds: float) -> None:
    """Hold the real cross-process session lock for the bounded-timeout case."""

    service = TaskRuntimeService(workspace)
    with service._board._file_lock(service._session_file_lock_path(task_id)):
        Path(ready_path).write_text("locked\n", encoding="utf-8")
        time.sleep(hold_seconds)


def _spawn_settlement_contender(
    workspace: str,
    identity_record: dict[str, Any],
    outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    start_event: Any,
    result_queue: Any,
) -> None:
    """Settle one claimed identity from an isolated spawned interpreter."""

    if not start_event.wait(timeout=10):
        result_queue.put({"success": False, "code": "start_timeout"})
        return
    service = TaskRuntimeService(workspace)
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(identity_record)
    result_queue.put(_settle(service, identity, outcome, timeout_seconds=2.0))


def _completed_fact_count(workspace: Path) -> int:
    return len(
        query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream="task_runtime.execution",
                event_type="completed",
            )
        ).events
    )


def _terminal_fact_count(workspace: Path, task_id: int) -> int:
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
        )
    ).events
    return sum(
        1
        for event in events
        if event.get("event_type") in {"completed", "failed", "suspended"}
        and event.get("payload", {}).get("task_id") == str(task_id)
    )


@pytest.mark.parametrize(
    "outcomes",
    (
        ("completed", "failed"),
        ("completed", "suspended"),
    ),
    ids=("complete-vs-fail", "complete-vs-suspend"),
)
def test_spawned_settlement_contenders_commit_exactly_one_terminal_winner(
    tmp_path: Path,
    outcomes: tuple[TaskRuntimeExecutionAttemptSettlementOutcomeV1, TaskRuntimeExecutionAttemptSettlementOutcomeV1],
) -> None:
    """Separate processes cannot split one claimed terminal settlement."""

    workspace = tmp_path / "workspace"
    service, task_id, identity = _claim_attempt(workspace)
    package_parent = str(Path(__file__).resolve().parents[6])
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)
    context = mp.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue(maxsize=2)
    processes = [
        context.Process(
            target=_spawn_settlement_contender,
            args=(str(workspace), identity.to_record(), outcome, start_event, result_queue),
        )
        for outcome in outcomes
    ]

    try:
        for process in processes:
            process.start()
        start_event.set()
        try:
            results = [result_queue.get(timeout=15) for _ in processes]
        except Empty:
            pytest.fail("spawn settlement contenders did not report within the timeout")
    finally:
        start_event.set()
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()

    assert all(process.exitcode == 0 for process in processes)
    winners = [result for result in results if result["success"] is True]
    losers = [result for result in results if result["success"] is False]
    assert len(winners) == 1
    assert winners[0]["code"] == "settled"
    assert len(losers) == 1
    assert losers[0]["code"] in {"terminal_outcome_conflict", "session_not_active"}
    assert winners[0]["projection_receipt"]["terminal_transition_id"]
    session = service._read_session(task_id)
    assert session is not None
    assert session.status in outcomes
    assert winners[0]["outcome"] == session.status
    row = service.get_task(task_id)
    assert row is not None
    assert row["status"] == ("pending" if session.status == "suspended" else session.status)
    assert _terminal_fact_count(workspace, task_id) == 1


def test_settlement_completed_winner_and_conflicting_outcome_loser(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    service, task_id, identity = _claim_attempt(workspace)

    winner = _settle(service, identity, "completed")
    loser = _settle(service, identity, "failed")

    assert winner["success"] is True
    assert winner["code"] == "settled"
    assert winner["session"]["status"] == "completed"
    assert winner["projection_receipt"]["terminal_transition_id"]
    assert loser["success"] is False
    assert loser["code"] == "terminal_outcome_conflict"
    assert _completed_fact_count(workspace) == 1
    session = service._read_session(task_id)
    assert session is not None
    assert session.status == "completed"


def test_settlement_same_outcome_returns_typed_idempotent_success(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    service, _task_id, identity = _claim_attempt(workspace)

    first = _settle(service, identity, "completed")
    replay = _settle(service, identity, "completed")

    assert first["success"] is True
    assert first["code"] == "settled"
    assert replay["success"] is True
    assert replay["code"] == "settlement_idempotent"
    assert replay["idempotent"] is True
    assert (
        replay["projection_receipt"]["terminal_transition_id"] == first["projection_receipt"]["terminal_transition_id"]
    )
    assert _completed_fact_count(workspace) == 1


def test_settlement_projection_failure_is_recoverable_without_rewriting_winner(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    service, task_id, identity = _claim_attempt(workspace)
    original_update = service._board.update
    failed_once = False

    def _fail_first_projection(*args: Any, **kwargs: Any) -> Any:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("synthetic projection write failure")
        return original_update(*args, **kwargs)

    service._board.update = _fail_first_projection  # type: ignore[method-assign]
    failed = _settle(service, identity, "completed")
    service._board.update = original_update  # type: ignore[method-assign]
    recovered = _settle(service, identity, "completed")

    assert failed["success"] is False
    assert failed["code"] == "row_projection_failed"
    session = service._read_session(task_id)
    assert session is not None
    assert session.status == "completed"
    assert recovered["success"] is True
    assert recovered["code"] == "settled"
    assert _completed_fact_count(workspace) == 1


def test_settlement_session_lock_timeout_is_bounded(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    service, _task_id, identity = _claim_attempt(workspace)
    package_parent = str(Path(__file__).resolve().parents[6])
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)
    ready_path = workspace / "session-lock-ready.txt"
    holder = mp.get_context("spawn").Process(
        target=_hold_session_lock,
        args=(str(workspace), identity.task_id, str(ready_path), 0.6),
    )
    holder.start()
    try:
        deadline = time.monotonic() + 2.0
        while not ready_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.read_text(encoding="utf-8") == "locked\n"
        started_at = time.monotonic()
        blocked = _settle(service, identity, "completed", timeout_seconds=0.1)
        elapsed = time.monotonic() - started_at
        assert blocked["success"] is False
        assert blocked["code"] == "file_lock_timeout"
        assert blocked["evidence"]["lock_scope"] == "cooperative_session_file"
        assert 0.08 <= elapsed < 1.0
    finally:
        holder.join(timeout=2.0)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=1.0)
    assert holder.exitcode == 0


@pytest.mark.parametrize("enroll_registry", (False, True), ids=("unenrolled", "strict-empty"))
def test_empty_parent_registry_allows_settlement_and_later_admission_cannot_append(
    tmp_path: Path,
    enroll_registry: bool,
) -> None:
    workspace = tmp_path / "workspace"
    service, _task_id, identity = _claim_attempt(workspace)
    if enroll_registry:
        _enroll_parent_registry(identity)

    settled = _settle(service, identity, "completed")
    rejected = admit_directed_effect_parent(_parent_command(identity))

    assert settled["success"] is True
    assert settled["code"] == "settled"
    assert rejected.code in {"lease_version_mismatch", "session_not_active"}
    if enroll_registry:
        assert _registry_events(identity) == ()


@pytest.mark.parametrize(
    ("registry_state", "expected_code"),
    (
        ("OPEN", "settlement_parent_close_required"),
        ("CLOSED", "settlement_parent_close_proof_required"),
        ("corrupt", "settlement_parent_registry_invalid"),
        ("unknown_event", "settlement_parent_registry_invalid"),
        ("read_error", "settlement_parent_registry_unavailable"),
    ),
)
def test_settlement_registry_pre_barrier_fails_closed_without_terminal_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_state: str,
    expected_code: str,
) -> None:
    workspace = tmp_path / registry_state
    service, task_id, identity = _claim_attempt(workspace)
    _enroll_parent_registry(identity)
    if registry_state in {"OPEN", "CLOSED"}:
        binding = _admit_parent(identity)
        if registry_state == "CLOSED":
            _close_parent(binding)
    elif registry_state in {"corrupt", "unknown_event"}:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=identity.workspace,
                stream=_registry_stream(identity),
                event_type=(
                    "task_runtime.directed_effect_parent_registry.v1.parent_admitted"
                    if registry_state == "corrupt"
                    else "task_runtime.directed_effect_parent_registry.v1.unknown"
                ),
                payload={
                    "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
                    "recorded_at": "2026-07-15T00:00:00+00:00",
                },
                source="test",
                idempotency_key=f"invalid-registry-{registry_state}",
                expected_seq=1,
                durability="fsync",
                strict_integrity=True,
            )
        )
    else:

        def fail_registry_read(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise FactStreamError(
                "synthetic strict registry read failure",
                code="query_failed",
                details={"stage": "strict_registry_read"},
            )

        monkeypatch.setattr(deo_internal, "query_fact_events", fail_registry_read)

    session_before = service._read_session(task_id)
    row_before = service.get_task(task_id)
    assert session_before is not None
    assert row_before is not None
    terminal_facts_before = _terminal_fact_count(workspace, task_id)

    blocked = _settle(service, identity, "completed")

    session_after = service._read_session(task_id)
    row_after = service.get_task(task_id)
    assert blocked["success"] is False
    assert blocked["code"] == expected_code
    assert blocked["evidence"]["directed_effect_pre_barrier"]["registry_state"]
    assert session_after is not None
    assert session_after.to_dict() == session_before.to_dict()
    assert row_after == row_before
    assert _terminal_fact_count(workspace, task_id) == terminal_facts_before


@pytest.mark.parametrize(
    "writer",
    (
        "canonical_settle",
        "stale_fencing",
        "bulk_cancellation",
        "rework_failure",
        "dedupe_cancellation",
        "role_adapter_failure",
        "reopen_suspension",
    ),
)
def test_every_active_to_inactive_writer_blocks_on_open_parent_without_writes(
    tmp_path: Path,
    writer: str,
) -> None:
    workspace = tmp_path / writer
    service, task_id, identity = _claim_attempt(workspace)
    _enroll_parent_registry(identity)
    _admit_parent(identity)
    if writer == "stale_fencing":
        expired_session = service._read_session(task_id)
        assert expired_session is not None
        expired_session.lease_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        assert service._write_session(expired_session) is True

    session_before = service._read_session(task_id)
    row_before = service.get_task(task_id)
    assert session_before is not None
    assert row_before is not None
    terminal_facts_before = _terminal_fact_count(workspace, task_id)

    if writer == "canonical_settle":
        result: Any = _settle(service, identity, "completed")
        observed_code = result["code"]
    elif writer == "stale_fencing":
        result = service.fence_expired_factory_run_sessions(
            FenceExpiredFactoryRunSessionsCommandV1(
                workspace=identity.workspace,
                factory_run_id=identity.run_id,
                reason="stale fencing pre-barrier",
            )
        )
        assert result.ok is False
        observed_code = result.conflicts[0]["code"]
    elif writer == "bulk_cancellation":
        result = service.suspend_active_executions_for_run(
            identity.run_id,
            reason="bulk cancellation pre-barrier",
        )
        observed_code = result["failed"][0]["code"]
    elif writer == "rework_failure":
        result = service.fail_task_row_after_rework_exhausted(
            task_id,
            reason="rework failure pre-barrier",
        )
        assert result is not None
        observed_code = result["code"]
    elif writer == "dedupe_cancellation":
        result = service.cancel_task_row_for_deduplication(
            task_id,
            primary_task_id=task_id + 100,
            reason="dedupe pre-barrier",
        )
        assert result is not None
        observed_code = result["code"]
    elif writer == "role_adapter_failure":
        result = service.fail_task_row_from_role_adapter(
            task_id,
            reason="role adapter pre-barrier",
            role_id="chief_engineer",
        )
        assert result is not None
        observed_code = result["code"]
    else:
        result = service.reopen_task_row(task_id, reason="reopen pre-barrier")
        assert result is not None
        observed_code = result["code"]

    session_after = service._read_session(task_id)
    row_after = service.get_task(task_id)
    assert observed_code == "settlement_parent_close_required"
    assert session_after is not None
    assert session_after.to_dict() == session_before.to_dict()
    assert row_after == row_before
    assert _terminal_fact_count(workspace, task_id) == terminal_facts_before
    assert len(_registry_events(identity)) == 1
