"""Focused two-phase execution-attempt settlement regression tests."""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty
from threading import Event, Thread
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
from polaris.cells.runtime.task_runtime.internal import (
    directed_effect_operation as deo_internal,
    service as service_module,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentCommandV1,
    BindRuntimeTaskToFactoryRunCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FenceExpiredFactoryRunSessionsCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ParentCorrelationV1,
    SealDirectedEffectInventoryCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    admit_directed_effect_operation,
    admit_directed_effect_parent,
    claim_directed_effect,
    commit_directed_effect_receipt,
    enroll_directed_effect_operation_stream,
    enroll_directed_effect_parent_registry_stream,
    finalize_directed_effect_inventory_admission,
    get_directed_effect_inventory,
    seal_directed_effect_inventory,
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
    binding = service.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(workspace),
            task_id=str(task_id),
            factory_run_id="settlement-run",
        )
    )
    assert binding.ok is True
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


def test_pending_terminal_intent_survives_parent_close_failure_and_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "pending-retry"
    service, task_id, identity = _claim_attempt(workspace)
    original = deo_internal.DirectedEffectOperationRepository.settle_parent_for_terminal_intent

    def fail_parent_close(*args: object, **kwargs: object) -> deo_internal.DirectedEffectSettlementPreBarrierVerdictV1:
        del args, kwargs
        return deo_internal.DirectedEffectSettlementPreBarrierVerdictV1(
            allowed=False,
            code="settlement_parent_close_failed",
            evidence={"reason": "synthetic_crash_after_terminal_intent"},
        )

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "settle_parent_for_terminal_intent",
        fail_parent_close,
    )
    blocked = _settle(service, identity, "completed")
    pending_session = service._read_session(task_id)

    assert blocked["code"] == "settlement_parent_close_failed"
    assert pending_session is not None
    assert pending_session.status == "active"
    pending = pending_session.metadata["pending_terminal_intent"]
    assert pending["schema_version"] == "task-runtime.pending-terminal-intent/1"
    assert pending["outcome"] == "completed"
    assert len(pending["identity_hash"]) == 64
    assert len(pending["summary_hash"]) == 64
    assert len(pending["metadata_hash"]) == 64

    heartbeat = service.heartbeat_execution_attempt(
        HeartbeatTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            lease_ttl_seconds=60,
            lock_timeout_seconds=0.5,
        )
    )
    assert heartbeat.code == "terminal_fence_pending"

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "settle_parent_for_terminal_intent",
        original,
    )
    recovered = _settle(service, identity, "completed")
    terminal_session = service._read_session(task_id)

    assert recovered["success"] is True
    assert terminal_session is not None
    assert terminal_session.status == "completed"
    assert terminal_session.metadata["pending_terminal_intent"] == pending
    assert (
        terminal_session.metadata["terminal_settlement_proof"]["terminal_intent_hash"]
        == pending["terminal_intent_hash"]
    )


def test_heartbeat_racing_terminal_intent_linearizes_after_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heartbeat cannot renew across the durable terminal-intent fence."""

    workspace = tmp_path / "heartbeat-terminal-intent-race"
    service, task_id, identity = _claim_attempt(workspace)
    _enroll_parent_registry(identity)
    intent_written = Event()
    release_settlement = Event()
    heartbeat_started = Event()
    heartbeat_done = Event()
    observed: dict[str, Any] = {}

    def pause_after_intent(*_args: object) -> None:
        intent_written.set()
        assert release_settlement.wait(timeout=10)

    def run_heartbeat() -> None:
        heartbeat_started.set()
        observed["heartbeat"] = TaskRuntimeService(str(workspace)).heartbeat_execution_attempt(
            HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                lease_ttl_seconds=60,
                lock_timeout_seconds=5.0,
            )
        )
        heartbeat_done.set()

    monkeypatch.setattr(service, "_after_terminal_intent_write", pause_after_intent)
    settlement_thread = Thread(
        target=lambda: observed.setdefault("settlement", _settle(service, identity, "completed", timeout_seconds=5.0))
    )
    settlement_thread.start()
    assert intent_written.wait(timeout=10)
    heartbeat_thread = Thread(target=run_heartbeat)
    heartbeat_thread.start()
    assert heartbeat_started.wait(timeout=10)
    assert heartbeat_done.wait(timeout=0.1) is False

    release_settlement.set()
    settlement_thread.join(timeout=10)
    heartbeat_thread.join(timeout=10)

    assert not settlement_thread.is_alive()
    assert not heartbeat_thread.is_alive()
    assert observed["settlement"]["success"] is True
    assert observed["settlement"]["code"] == "settled"
    assert observed["heartbeat"].success is False
    assert observed["heartbeat"].code == "lease_version_mismatch"
    persisted = service._read_session(task_id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.metadata["pending_terminal_intent"]
    assert persisted.metadata["terminal_settlement_proof"]
    assert _terminal_fact_count(workspace, task_id) == 1


def test_heartbeat_winner_invalidates_stale_settlement_without_terminal_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed renewal wins the lock and invalidates the old settlement lease."""

    workspace = tmp_path / "heartbeat-wins-settlement-race"
    service, task_id, stale_identity = _claim_attempt(workspace)
    _enroll_parent_registry(stale_identity)
    heartbeat_persisted = Event()
    release_heartbeat = Event()
    settlement_started = Event()
    settlement_done = Event()
    observed: dict[str, Any] = {}
    original_heartbeat = service._heartbeat_execution_attempt_locked

    def pause_after_heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> Any:
        verdict = original_heartbeat(command)
        assert verdict.success is True
        heartbeat_persisted.set()
        assert release_heartbeat.wait(timeout=10)
        return verdict

    def run_settlement() -> None:
        settlement_started.set()
        observed["settlement"] = _settle(
            TaskRuntimeService(str(workspace)),
            stale_identity,
            "completed",
            timeout_seconds=5.0,
        )
        settlement_done.set()

    monkeypatch.setattr(service, "_heartbeat_execution_attempt_locked", pause_after_heartbeat)
    heartbeat_thread = Thread(
        target=lambda: observed.setdefault(
            "heartbeat",
            service.heartbeat_execution_attempt(
                HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                    workspace=stale_identity.workspace,
                    identity=stale_identity,
                    lease_ttl_seconds=60,
                    lock_timeout_seconds=5.0,
                )
            ),
        )
    )
    heartbeat_thread.start()
    assert heartbeat_persisted.wait(timeout=10)
    settlement_thread = Thread(target=run_settlement)
    settlement_thread.start()
    assert settlement_started.wait(timeout=10)
    assert settlement_done.wait(timeout=0.1) is False

    release_heartbeat.set()
    heartbeat_thread.join(timeout=10)
    settlement_thread.join(timeout=10)

    assert not heartbeat_thread.is_alive()
    assert not settlement_thread.is_alive()
    assert observed["heartbeat"].success is True
    assert observed["heartbeat"].code == "heartbeat_renewed"
    assert observed["settlement"]["success"] is False
    assert observed["settlement"]["code"] == "lease_version_mismatch"
    persisted = service._read_session(task_id)
    assert persisted is not None
    assert persisted.status == "active"
    assert persisted.lease_expires_at == observed["heartbeat"].renewed_identity.lease_expires_at
    assert "pending_terminal_intent" not in persisted.metadata
    assert "terminal_settlement_proof" not in persisted.metadata
    assert _registry_events(stale_identity) == ()
    assert _terminal_fact_count(workspace, task_id) == 0


def test_expired_reclaim_racing_settlement_cannot_supersede_terminal_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale-owner reclaim that becomes eligible mid-settlement loses cleanly."""

    workspace = tmp_path / "reclaim-settlement-race"
    service, task_id, original_identity = _claim_attempt(workspace)
    _enroll_parent_registry(original_identity)
    before_expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)
    after_expiry = datetime(2030, 1, 3, tzinfo=timezone.utc)
    session = service._read_session(task_id)
    assert session is not None
    session.lease_expires_at = datetime(2030, 1, 2, tzinfo=timezone.utc).isoformat()
    assert service._write_session(session) is True
    identity = service._execution_attempt_identity_from_session(session)
    intent_written = Event()
    release_settlement = Event()
    reclaim_started = Event()
    reclaim_done = Event()
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        service_module,
        "utc_now",
        lambda: after_expiry if intent_written.is_set() else before_expiry,
    )

    def pause_after_intent(*_args: object) -> None:
        intent_written.set()
        assert release_settlement.wait(timeout=10)

    def run_reclaim() -> None:
        reclaim_started.set()
        observed["reclaim"] = TaskRuntimeService(str(workspace)).fence_expired_factory_run_sessions(
            FenceExpiredFactoryRunSessionsCommandV1(
                workspace=identity.workspace,
                factory_run_id=identity.run_id,
                reason="concurrent stale-owner reclaim",
            )
        )
        reclaim_done.set()

    monkeypatch.setattr(service, "_after_terminal_intent_write", pause_after_intent)
    settlement_thread = Thread(
        target=lambda: observed.setdefault("settlement", _settle(service, identity, "completed", timeout_seconds=5.0))
    )
    settlement_thread.start()
    assert intent_written.wait(timeout=10)
    reclaim_thread = Thread(target=run_reclaim)
    reclaim_thread.start()
    assert reclaim_started.wait(timeout=10)
    assert reclaim_done.wait(timeout=0.1) is False

    release_settlement.set()
    settlement_thread.join(timeout=10)
    reclaim_thread.join(timeout=10)

    assert not settlement_thread.is_alive()
    assert not reclaim_thread.is_alive()
    assert observed["settlement"]["success"] is True
    assert observed["settlement"]["code"] == "settled"
    assert observed["reclaim"].ok is True
    assert observed["reclaim"].code == "no_expired_sessions"
    assert observed["reclaim"].fenced_session_ids == ()
    persisted = service._read_session(task_id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert "factory_stale_session_fence" not in persisted.metadata
    assert _terminal_fact_count(workspace, task_id) == 1


def test_expired_reclaim_winner_fences_stale_settlement_without_split_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed stale-owner fence revokes the old settlement authority."""

    workspace = tmp_path / "reclaim-wins-settlement-race"
    service, task_id, stale_identity = _claim_attempt(workspace)
    _enroll_parent_registry(stale_identity)
    expired_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    observed_at = datetime(2030, 1, 2, tzinfo=timezone.utc)
    session = service._read_session(task_id)
    assert session is not None
    session.lease_expires_at = expired_at.isoformat()
    assert service._write_session(session) is True
    stale_identity = service._execution_attempt_identity_from_session(session)
    fence_persisted = Event()
    release_fence = Event()
    settlement_started = Event()
    settlement_done = Event()
    observed: dict[str, Any] = {}
    original_write = service._write_session_locked

    monkeypatch.setattr(service_module, "utc_now", lambda: observed_at)

    def pause_after_fence_write(candidate: Any) -> bool:
        written = original_write(candidate)
        if written and "factory_stale_session_fence" in candidate.metadata:
            fence_persisted.set()
            assert release_fence.wait(timeout=10)
        return written

    def run_settlement() -> None:
        settlement_started.set()
        observed["settlement"] = _settle(
            TaskRuntimeService(str(workspace)),
            stale_identity,
            "completed",
            timeout_seconds=5.0,
        )
        settlement_done.set()

    monkeypatch.setattr(service, "_write_session_locked", pause_after_fence_write)
    reclaim_thread = Thread(
        target=lambda: observed.setdefault(
            "reclaim",
            service.fence_expired_factory_run_sessions(
                FenceExpiredFactoryRunSessionsCommandV1(
                    workspace=stale_identity.workspace,
                    factory_run_id=stale_identity.run_id,
                    reason="reclaim wins race",
                )
            ),
        )
    )
    reclaim_thread.start()
    assert fence_persisted.wait(timeout=10)
    settlement_thread = Thread(target=run_settlement)
    settlement_thread.start()
    assert settlement_started.wait(timeout=10)
    assert settlement_done.wait(timeout=0.1) is False

    release_fence.set()
    reclaim_thread.join(timeout=10)
    settlement_thread.join(timeout=10)

    assert not reclaim_thread.is_alive()
    assert not settlement_thread.is_alive()
    assert observed["reclaim"].ok is True
    assert observed["reclaim"].code == "expired_sessions_fenced"
    assert observed["reclaim"].fenced_session_ids == (stale_identity.session_id,)
    assert observed["settlement"]["success"] is False
    assert observed["settlement"]["code"] in {"lease_version_mismatch", "session_not_active"}
    persisted = service._read_session(task_id)
    assert persisted is not None
    assert persisted.status == "suspended"
    assert persisted.metadata["factory_stale_session_fence"]["factory_run_id"] == stale_identity.run_id
    assert "pending_terminal_intent" not in persisted.metadata
    assert "terminal_settlement_proof" not in persisted.metadata
    assert _registry_events(stale_identity) == ()
    assert _terminal_fact_count(workspace, task_id) == 0


def test_stale_fence_cannot_supersede_pending_terminal_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "pending-stale-fence"
    service, task_id, identity = _claim_attempt(workspace)

    def fail_parent_close(*args: object, **kwargs: object) -> deo_internal.DirectedEffectSettlementPreBarrierVerdictV1:
        del args, kwargs
        return deo_internal.DirectedEffectSettlementPreBarrierVerdictV1(
            allowed=False,
            code="settlement_parent_close_failed",
            evidence={"reason": "synthetic_crash_after_terminal_intent"},
        )

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "settle_parent_for_terminal_intent",
        fail_parent_close,
    )
    assert _settle(service, identity, "failed")["code"] == "settlement_parent_close_failed"
    pending_session = service._read_session(task_id)
    assert pending_session is not None
    pending_session.lease_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert service._write_session(pending_session) is True

    fenced = service.fence_expired_factory_run_sessions(
        FenceExpiredFactoryRunSessionsCommandV1(
            workspace=identity.workspace,
            factory_run_id=identity.run_id,
            reason="must preserve terminal intent",
        )
    )
    observed = service._read_session(task_id)

    assert fenced.ok is False
    assert fenced.code == "session_fence_failed"
    assert fenced.conflicts[0]["code"] == "terminal_fence_pending"
    assert observed is not None
    assert observed.status == "active"
    assert observed.metadata["pending_terminal_intent"]["outcome"] == "failed"


def test_terminal_intent_exact_replay_binds_utf8_summary_and_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "terminal-intent-replay"
    service, _task_id, identity = _claim_attempt(workspace)
    summary = "完成：魔法终端验证 ✓"
    metadata = {"验收": ["构建", "入口"], "score": 2}
    command = SettleTaskRuntimeExecutionAttemptCommandV1(
        workspace=identity.workspace,
        identity=identity,
        outcome="completed",
        summary=summary,
        metadata=metadata,
        lock_timeout_seconds=0.5,
    )

    settled = service.settle_execution_attempt(command)
    replayed = service.settle_execution_attempt(command)
    changed_summary = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary=f"{summary}!",
            metadata=metadata,
            lock_timeout_seconds=0.5,
        )
    )
    changed_metadata = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary=summary,
            metadata={**metadata, "score": 3},
            lock_timeout_seconds=0.5,
        )
    )

    pending = settled["session"]["metadata"]["pending_terminal_intent"]
    expected_metadata_hash = hashlib.sha256(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert settled["success"] is True
    assert replayed["code"] == "settlement_idempotent"
    assert changed_summary["code"] == "settlement_terminal_intent_conflict"
    assert changed_metadata["code"] == "settlement_terminal_intent_conflict"
    assert pending["summary_hash"] == hashlib.sha256(summary.encode("utf-8")).hexdigest()
    assert pending["metadata_hash"] == expected_metadata_hash


@pytest.mark.parametrize("tampered_status", ("active", "suspended"))
def test_fulfilled_terminal_intent_requires_exact_session_outcome(
    tmp_path: Path,
    tampered_status: str,
) -> None:
    workspace = tmp_path / f"fulfilled-intent-{tampered_status}"
    service, task_id, identity = _claim_attempt(workspace)
    settled = _settle(service, identity, "completed")
    assert settled["success"] is True
    session = service._read_session(task_id)
    assert session is not None
    session.status = tampered_status

    verdict = service._directed_effect_inactive_pre_barrier_locked(session)

    assert verdict.allowed is False
    assert verdict.code == "settlement_terminal_intent_conflict"
    assert verdict.evidence["reason"] == "terminal_session_outcome_mismatch"


def test_terminal_reopen_requires_exact_fulfilled_intent_proof(tmp_path: Path) -> None:
    workspace = tmp_path / "terminal-reopen-proof"
    service, task_id, identity = _claim_attempt(workspace)
    settled = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="proof-bound terminal",
            lock_timeout_seconds=0.5,
        )
    )
    assert settled["success"] is True
    session_path = Path(service._kernel_fs.resolve_path(service._session_logical_path(task_id)))
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["metadata"]["terminal_settlement_proof"]["terminal_intent_hash"] = "0" * 64
    session_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    reopened = service.reopen_task_row(task_id, reason="must reject tampered settlement proof")

    assert reopened is not None
    assert reopened["success"] is False
    assert reopened["code"] == "settlement_terminal_intent_conflict"
    assert reopened["evidence"]["reason"] == "terminal_settlement_proof_binding_invalid"
    observed = service._read_session(task_id)
    assert observed is not None
    assert observed.status == "completed"


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


def _director_materialization_attempt(
    workspace: Path,
) -> tuple[
    TaskRuntimeService,
    int,
    int,
    TaskRuntimeExecutionAttemptIdentityV1,
    Any,
    Any,
]:
    """Create one failed-capability candidate and a directly blocked child."""

    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_task_runtime_fact_stream(workspace)
    (workspace / "main.go").write_text("package main\n", encoding="utf-8")
    service = TaskRuntimeService(str(workspace))
    parent = service.create_task_row(
        subject="materialized Director parent",
        metadata={
            "target_files": ["main.go"],
            "scope_paths": ["main.go"],
            "adapter_result": {
                "write_tool_evidence": True,
                "new_files": ["main.go"],
                "modified_files": [],
            },
        },
    )
    parent_id = int(parent["id"])
    child = service.create_task_row(subject="downstream repair task", blocked_by=[parent_id])
    child_id = int(child["id"])
    claim = service.claim_execution(
        parent_id,
        worker_id="director",
        role_id="director",
        run_id="director-materialization-run",
        external_task_id="TASK-1",
        selection_source="failed-materialization-test",
    )
    assert claim["success"] is True
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(dict(claim["execution_attempt"]))
    task = service._board.get(parent_id)
    session = service._read_session(parent_id)
    assert task is not None
    assert session is not None
    return service, parent_id, child_id, identity, task, session


def _materialization_settlement_proof(identity: TaskRuntimeExecutionAttemptIdentityV1) -> dict[str, Any]:
    """Return a proof shaped like a closed, all-success DEO parent registry."""

    stable_identity = identity.to_record()
    stable_identity.pop("lease_expires_at")
    return {
        "registry_state": "CLOSED_WITH_OUTCOME_PROOF",
        "settlement_outcome": "failed",
        "receipt_count": 1,
        "failed_receipt_count": 0,
        "dead_letter_count": 0,
        "aborted_count": 0,
        "stable_registry_identity": stable_identity,
        "close_evidence_ref": "settlement://materialized-failure",
        "close_evidence_hash": "a" * 64,
        "receipt_summary_hash": "b" * 64,
    }


def _commit_one_succeeded_directed_effect(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> None:
    """Commit one real DEO success receipt so settlement builds live proof."""

    _enroll_parent_registry(identity)
    binding = _admit_parent(identity)
    enrolled = enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(
            execution_attempt=identity,
            parent_binding=binding,
        )
    )
    assert enrolled.ok is True
    intended_effect_fingerprint = deo_internal._hash_token({"fingerprint": "materialized-main-go"})
    policy_verdict_hash = deo_internal._hash_token({"policy": "allow-test-write"})
    expected_receipt_binding_hash = deo_internal._hash_token({"receipt": "main-go"})
    sealed = seal_directed_effect_inventory(
        SealDirectedEffectInventoryCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            intents=(
                DirectedEffectInventoryIntentV1(
                    ordinal=0,
                    tool_call_id="write-main-go",
                    normalized_tool_name="write_file",
                    effect_type="write",
                    execution_mode="write_serial",
                    intended_effect_fingerprint=intended_effect_fingerprint,
                    policy_verdict_hash=policy_verdict_hash,
                    expected_receipt_binding_hash=expected_receipt_binding_hash,
                ),
            ),
            expected_registry_version=1,
            expected_registry_seq=2,
        )
    )
    assert sealed.code == "inventory_sealed"
    assert sealed.projection is not None
    member = sealed.projection.members[0]
    admission = AdmitDirectedEffectOperationCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        expected_version=0,
        expected_seq=1,
        actor="settlement-materialization-test",
        intended_effect_fingerprint=member.intended_effect_fingerprint,
        policy_verdict_hash=member.policy_verdict_hash,
        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
    )
    assert admit_directed_effect_operation(admission).code == "admitted"
    inventory = get_directed_effect_inventory(
        GetDirectedEffectInventoryQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
        )
    )
    assert inventory.projection is not None
    finalized = finalize_directed_effect_inventory_admission(
        FinalizeDirectedEffectInventoryAdmissionCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            inventory_hash=inventory.projection.inventory_hash,
            expected_registry_version=2,
            expected_registry_seq=3,
            expected_operation_head_seq=1,
        )
    )
    assert finalized.code == "inventory_ready"
    claimed = claim_directed_effect(
        ClaimDirectedEffectCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=member.tool_call_id,
            effect_id=member.effect_id,
            expected_version=1,
            expected_seq=2,
            actor="settlement-materialization-test",
            intended_effect_fingerprint=member.intended_effect_fingerprint,
            policy_verdict_hash=member.policy_verdict_hash,
            expected_receipt_binding_hash=member.expected_receipt_binding_hash,
        )
    )
    assert claimed.code == "effect_claimed"
    committed = commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=member.tool_call_id,
            effect_id=member.effect_id,
            expected_version=2,
            expected_seq=3,
            actor="settlement-materialization-test",
            intended_effect_fingerprint=member.intended_effect_fingerprint,
            policy_verdict_hash=member.policy_verdict_hash,
            expected_receipt_binding_hash=member.expected_receipt_binding_hash,
            receipt_ref="receipt://materialized/main.go",
            receipt_hash="c" * 64,
            receipt_binding_hash=member.expected_receipt_binding_hash,
            receipt_outcome="succeeded",
        )
    )
    assert committed.code == "receipt_committed"


def test_failed_director_materialization_requires_closed_successful_effect_proof(tmp_path: Path) -> None:
    """A real declared file plus bound all-success receipts releases capability."""

    service, _parent_id, _child_id, identity, task, session = _director_materialization_attempt(tmp_path)
    session.metadata["terminal_settlement_proof"] = _materialization_settlement_proof(identity)
    command = SettleTaskRuntimeExecutionAttemptCommandV1(
        workspace=identity.workspace,
        identity=identity,
        outcome="failed",
        summary="quality gate rejected a repairable file",
    )

    decision = service._failed_materialization_dependency_satisfaction(
        command=command,
        task=task,
        session=session,
    )

    assert decision is not None
    evidence = dict(decision.evidence)
    assert evidence["kind"] == "failed_director_materialization"
    assert evidence["materialized_paths"] == ["main.go"]
    assert evidence["receipt_count"] == 1
    assert len(evidence["evidence_hash"]) == 64


def test_real_deo_failed_materialization_settlement_releases_only_dependency(
    tmp_path: Path,
) -> None:
    """Live DEO receipts make the child ready without changing parent failure."""

    service, parent_id, child_id, identity, _task, _session = _director_materialization_attempt(tmp_path)
    _commit_one_succeeded_directed_effect(identity)
    command = SettleTaskRuntimeExecutionAttemptCommandV1(
        workspace=identity.workspace,
        identity=identity,
        outcome="failed",
        summary="semantic quality failed after a committed write",
    )

    result = service.settle_execution_attempt(command)

    assert result["success"] is True
    assert result["code"] == "settled"
    assert result["dependency_satisfaction"]["receipt_count"] == 1
    assert result["dependency_satisfaction"]["materialized_paths"] == ["main.go"]
    parent = service.get_task(parent_id)
    child = service.get_task(child_id)
    assert parent is not None
    assert parent["status"] == "failed"
    assert child is not None
    assert child["status"] == "pending"
    assert child["blocked_by"] == []


@pytest.mark.parametrize(
    ("proof_patch", "adapter_patch"),
    (
        ({"failed_receipt_count": 1}, {}),
        ({"dead_letter_count": 1}, {}),
        ({"receipt_count": 0}, {}),
        ({"registry_state": "OPEN"}, {}),
        ({}, {"write_tool_evidence": False}),
        ({}, {"new_files": ["unrelated.go"]}),
    ),
)
def test_failed_director_materialization_dependency_release_fails_closed(
    tmp_path: Path,
    proof_patch: dict[str, Any],
    adapter_patch: dict[str, Any],
) -> None:
    """Missing, failed, dead-lettered, or out-of-scope evidence never releases."""

    service, _parent_id, _child_id, identity, task, session = _director_materialization_attempt(tmp_path)
    proof = _materialization_settlement_proof(identity)
    proof.update(proof_patch)
    session.metadata["terminal_settlement_proof"] = proof
    task.metadata["adapter_result"].update(adapter_patch)
    command = SettleTaskRuntimeExecutionAttemptCommandV1(
        workspace=identity.workspace,
        identity=identity,
        outcome="failed",
        summary="unqualified materialization",
    )

    assert (
        service._failed_materialization_dependency_satisfaction(
            command=command,
            task=task,
            session=session,
        )
        is None
    )


def test_failed_materialization_stays_failed_but_releases_and_replays_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settlement preserves FAILED while its durable capability unblocks the child."""

    service, parent_id, child_id, identity, _task, _session = _director_materialization_attempt(tmp_path)
    evidence = {
        "schema_version": service_module._DEPENDENCY_SATISFACTION_SCHEMA_V1,
        "kind": "failed_director_materialization",
        "task_id": parent_id,
        "terminal_transition_id": "",
        "materialized_paths": ["main.go"],
    }

    def _decision(*, command: Any, task: Any, session: Any) -> Any:
        del command, task
        projected = dict(evidence)
        projected["terminal_transition_id"] = session.terminal_transition_id
        projected["evidence_hash"] = service_module._canonical_sha256(projected)
        return service_module._DependencySatisfactionDecision(evidence=projected)

    monkeypatch.setattr(service, "_failed_materialization_dependency_satisfaction", _decision)
    command = SettleTaskRuntimeExecutionAttemptCommandV1(
        workspace=identity.workspace,
        identity=identity,
        outcome="failed",
        summary="quality failed after committed writes",
    )

    first = service.settle_execution_attempt(command)

    assert first["success"] is True
    assert first["code"] == "settled"
    assert service.get_task(parent_id)["status"] == "failed"
    child = service.get_task(child_id)
    assert child is not None
    assert child["status"] == "pending"
    assert child["blocked_by"] == []
    assert first["dependency_events"][0]["event_type"] == "dependencies_unblocked"
    assert first["dependency_events"][0]["ok"] is True

    replay = service.settle_execution_attempt(command)

    assert replay["success"] is True
    assert replay["code"] == "settlement_idempotent"
    assert replay["dependency_events"] == []
    assert replay["dependency_satisfaction"]["kind"] == "failed_director_materialization"
