from __future__ import annotations

import multiprocessing
from pathlib import Path
from threading import Barrier, Event, Lock, Thread, get_ident
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
    GuardedFactSnapshotV1,
    QueryFactEventsV1,
    ReadGuardedFactSnapshotCommandV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
    read_guarded_fact_snapshot,
)
from polaris.cells.runtime.task_runtime.internal import directed_effect_operation as deo_internal
from polaris.cells.runtime.task_runtime.public import (
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentCommandV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ParentCorrelationV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    admit_directed_effect_operation,
    admit_directed_effect_parent,
    enroll_directed_effect_operation_stream,
    enroll_directed_effect_parent_registry_stream,
    heartbeat_task_runtime_execution_attempt,
    settle_task_runtime_execution_attempt,
)


def _setup_attempt(
    workspace: str,
    *,
    enroll_registry: bool = True,
) -> TaskRuntimeExecutionAttemptIdentityV1:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="deo-concurrency-test",
        )
    )
    service = TaskRuntimeService(workspace)
    task_id = int(service.create_task_row(subject="deo concurrency")["id"])
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(
        service.claim_execution(
            task_id,
            worker_id="worker",
            role_id="director",
            run_id="run",
            external_task_id="DEO-CONCURRENT",
            selection_source="test",
        )["execution_attempt"]
    )
    if enroll_registry:
        assert enroll_directed_effect_parent_registry_stream(
            EnrollDirectedEffectParentRegistryStreamCommandV1(execution_attempt=identity)
        ).ok
    return identity


def _parent_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> AdmitDirectedEffectParentCommandV1:
    return AdmitDirectedEffectParentCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        correlation=ParentCorrelationV1(turn_id="turn", batch_id="batch"),
        admission_idempotency_key="parent",
        expected_version=0,
        expected_seq=1,
    )


def _setup(workspace: str) -> tuple[TaskRuntimeExecutionAttemptIdentityV1, DirectedEffectParentBindingV1]:
    identity = _setup_attempt(workspace)
    parent = admit_directed_effect_parent(_parent_command(identity))
    assert parent.parent_binding is not None
    binding = parent.parent_binding
    assert enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(execution_attempt=identity, parent_binding=binding)
    ).ok
    return identity, binding


def _command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    *,
    suffix: str = "one",
) -> AdmitDirectedEffectOperationCommandV1:
    return AdmitDirectedEffectOperationCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=f"tool-{suffix}",
        effect_id=f"effect-{suffix}",
        expected_version=0,
        expected_seq=1,
        actor="test",
        intended_effect_fingerprint=f"fingerprint-{suffix}",
        policy_verdict_hash="policy",
        expected_receipt_binding_hash="receipt",
    )


def _process_admit(
    identity_record: dict[str, object],
    binding_record: dict[str, object],
    queue: multiprocessing.Queue[dict[str, Any]],
) -> None:
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(identity_record)
    binding = DirectedEffectParentBindingV1.from_record(binding_record)
    result = admit_directed_effect_operation(_command(identity, binding))
    queue.put({"code": result.code, "ok": result.ok})


def _process_parent_admission(
    identity_record: dict[str, object],
    started: Any,
    queue: multiprocessing.Queue[dict[str, Any]],
) -> None:
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(identity_record)
    started.set()
    result = admit_directed_effect_parent(_parent_command(identity))
    enrollment = None
    if result.ok and result.parent_binding is not None:
        enrollment = enroll_directed_effect_operation_stream(
            EnrollDirectedEffectOperationStreamCommandV1(
                execution_attempt=identity,
                parent_binding=result.parent_binding,
            )
        )
    queue.put(
        {
            "operation": "parent_admission",
            "success": result.ok,
            "code": result.code,
            "operation_stream_token": (
                result.parent_binding.operation_stream_token if result.parent_binding is not None else None
            ),
            "operation_enrollment_success": enrollment.ok if enrollment is not None else None,
            "operation_enrollment_code": enrollment.code if enrollment is not None else None,
            "operation_enrollment_receipt_authoritative": (
                enrollment.evidence.get("receipt_authoritative") if enrollment is not None else None
            ),
        }
    )


def _process_settlement(
    identity_record: dict[str, object],
    started: Any,
    queue: multiprocessing.Queue[dict[str, Any]],
) -> None:
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(identity_record)
    started.set()
    result = settle_task_runtime_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="parent admission race settlement",
            lock_timeout_seconds=10.0,
        )
    )
    queue.put(
        {
            "operation": "settlement",
            "success": result["success"],
            "code": result["code"],
        }
    )


def _close_parent(binding: DirectedEffectParentBindingV1, *, idempotency_key: str) -> None:
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
                "close_evidence_ref": "fact://test/close",
                "close_evidence_hash": "a" * 64,
                "actor": "test",
                "recorded_at": "2026-07-15T00:00:00+00:00",
            },
            source="test",
            idempotency_key=idempotency_key,
            expected_seq=binding.registry_version + 1,
            durability="fsync",
            strict_integrity=True,
        )
    )


def _parent_registry_events(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> tuple[dict[str, Any], ...]:
    registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(identity)
    stream = deo_internal._registry_stream_token(registry_identity)
    return query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=stream,
            strict_integrity=True,
        )
    ).events


def _terminal_execution_events(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> tuple[dict[str, Any], ...]:
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream="task_runtime.execution",
        )
    ).events
    return tuple(
        event
        for event in events
        if event.get("event_type") in {"completed", "failed", "suspended"}
        and event.get("payload", {}).get("task_id") == str(identity.task_id)
    )


def test_real_thread_workflow_has_one_durable_event(tmp_path: Path) -> None:
    identity, binding = _setup(str(tmp_path.resolve()))
    observed: list[str] = []
    threads = [
        Thread(target=lambda: observed.append(admit_directed_effect_operation(_command(identity, binding)).code))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert len(observed) == 2
    assert set(observed) <= {"admitted", "idempotent_replay"}
    assert "admitted" in observed
    events = query_fact_events(
        QueryFactEventsV1(workspace=identity.workspace, stream=binding.operation_stream_token, strict_integrity=True)
    ).events
    assert len(events) == 1


def test_real_process_workflow_has_one_durable_event(tmp_path: Path) -> None:
    identity, binding = _setup(str(tmp_path.resolve()))
    context = multiprocessing.get_context("fork")
    queue: multiprocessing.Queue[dict[str, Any]] = context.Queue()
    processes = [
        context.Process(target=_process_admit, args=(identity.to_record(), binding.to_record(), queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    observed = sorted(queue.get(timeout=5)["code"] for _ in processes)
    assert set(observed) <= {"admitted", "idempotent_replay"}
    assert "admitted" in observed
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(events) == 1


def test_close_between_prepare_and_commit_causes_guard_drift_without_child_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding = _setup(str(tmp_path.resolve()))
    closed = False

    def close_once(snapshot: object) -> None:
        nonlocal closed
        del snapshot
        if closed:
            return
        closed = True
        _close_parent(binding, idempotency_key="close-race")

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_prepare",
        staticmethod(close_once),
    )
    result = admit_directed_effect_operation(_command(identity, binding))
    assert result.code == "parent_closed"
    events = query_fact_events(
        QueryFactEventsV1(workspace=identity.workspace, stream=binding.operation_stream_token, strict_integrity=True)
    ).events
    assert events == ()


def test_two_prepared_same_semantic_calls_share_one_conservative_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding = _setup(str(tmp_path.resolve()))
    prepared_barrier = Barrier(2)
    observed_threads: set[int] = set()
    observed_lock = Lock()

    def wait_after_first_prepare(snapshot: object) -> None:
        del snapshot
        thread_id = get_ident()
        with observed_lock:
            first_prepare = thread_id not in observed_threads
            observed_threads.add(thread_id)
        if first_prepare:
            prepared_barrier.wait(timeout=15)

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_prepare",
        staticmethod(wait_after_first_prepare),
    )
    results: list[Any] = []
    threads = [
        Thread(target=lambda: results.append(admit_directed_effect_operation(_command(identity, binding))))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()

    assert len(results) == 2
    assert {result.code for result in results} == {"admitted"}
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(events) == 1
    event = events[0]
    for result in results:
        assert result.evidence["authoritative_append"] is False
        assert result.evidence["authoritative_effect_receipt"] is True
        assert result.evidence["append_disposition"] == "committed_or_exact_replay"
        assert result.evidence["event_id"] == event["event_id"]
        assert result.evidence["appended_seq"] == event["seq"]
        assert result.state == "INTENT_COMMITTED"
        assert result.version == 1
        assert result.snapshot is not None
        assert result.snapshot.state == result.state
        assert result.snapshot.version == result.version
        assert result.snapshot.last_event_id == event["event_id"]


def test_guard_drift_then_lease_rotation_blocks_reprepare_and_child_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding = _setup(str(tmp_path.resolve()))
    closed = False

    def close_once(snapshot: object) -> None:
        nonlocal closed
        del snapshot
        if closed:
            return
        closed = True
        _close_parent(binding, idempotency_key="close-before-settlement")

    def rotate_lease_before_reprepare(exc: FactStreamError, attempt_number: int) -> None:
        assert exc.code == "guard_snapshot_drift"
        assert attempt_number == 1
        heartbeat = heartbeat_task_runtime_execution_attempt(
            HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                lease_ttl_seconds=120,
                context_summary="rotate lease at guarded reprepare boundary",
                lock_timeout_seconds=5.0,
            )
        )
        assert heartbeat.success is True
        assert heartbeat.code == "heartbeat_renewed"

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_prepare",
        staticmethod(close_once),
    )
    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_drift",
        staticmethod(rotate_lease_before_reprepare),
    )
    result = admit_directed_effect_operation(_command(identity, binding))

    assert result.code == "lease_version_mismatch"
    assert result.evidence["guarded_attempt"] == 1
    assert result.evidence["guarded_authority_phase"] == "reprepare"
    assert result.evidence["drift_codes"] == ("guard_snapshot_drift",)
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert events == ()


def test_three_guarded_snapshot_drifts_exhaust_without_child_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding = _setup(str(tmp_path.resolve()))
    drift_codes = (
        "target_snapshot_drift",
        "guard_snapshot_drift",
        "target_snapshot_drift",
    )
    decoy_stream_pairs = (
        ("execution.control_plane", "factory.settlement"),
        ("resident.cycle.events", "roles.kernel.turn_outcomes"),
        ("task_market.events", "taskboard.terminal.events"),
    )
    drift_marker_type = "test.task_runtime.deo_guarded_drift_marker"
    for attempt_number, (_, guard_stream) in enumerate(decoy_stream_pairs, start=1):
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=identity.workspace,
                stream=guard_stream,
                event_type=drift_marker_type,
                payload={"attempt": attempt_number, "phase": "guard_baseline"},
                source="test",
                idempotency_key=f"guard-baseline-{attempt_number}",
                expected_seq=1,
                durability="fsync",
                strict_integrity=True,
            )
        )
    prepared_heads: list[tuple[int, int]] = []
    observed_drifts: list[tuple[int, str]] = []

    def force_next_drift(snapshot: GuardedFactSnapshotV1) -> None:
        assert snapshot.target_stream == binding.operation_stream_token
        assert snapshot.guard_stream == binding.registry_stream_token
        prepared_heads.append(
            (
                snapshot.proof.target_head_seq,
                snapshot.proof.guard_head_seq,
            )
        )
        attempt_index = len(prepared_heads) - 1
        drift_code = drift_codes[attempt_index]
        decoy_target, decoy_guard = decoy_stream_pairs[attempt_index]
        decoy_snapshot = read_guarded_fact_snapshot(
            ReadGuardedFactSnapshotCommandV1(
                workspace=identity.workspace,
                target_stream=decoy_target,
                guard_stream=decoy_guard,
            )
        )
        assert decoy_snapshot.proof.target_head_seq == 0
        assert decoy_snapshot.proof.guard_head_seq == 1
        drift_stream = decoy_target if drift_code == "target_snapshot_drift" else decoy_guard
        drift_head = (
            decoy_snapshot.proof.target_head_seq
            if drift_stream == decoy_target
            else decoy_snapshot.proof.guard_head_seq
        )
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=identity.workspace,
                stream=drift_stream,
                event_type=drift_marker_type,
                payload={"attempt": attempt_index + 1, "phase": drift_code},
                source="test",
                idempotency_key=f"drift-{attempt_index + 1}",
                expected_seq=drift_head + 1,
                durability="fsync",
                strict_integrity=True,
            )
        )
        # The production commit already owns this proof object. Redirect it to
        # one complete public proof made stale by the real append above.
        for field_name in (
            "workspace",
            "target_stream",
            "guard_stream",
            "target_storage_path",
            "guard_storage_path",
            "target_head_seq",
            "guard_head_seq",
            "strict_format_revision",
            "target_facts_digest",
            "guard_facts_digest",
            "continuity_digest",
        ):
            object.__setattr__(
                snapshot.proof,
                field_name,
                getattr(decoy_snapshot.proof, field_name),
            )

    def observe_drift(exc: FactStreamError, attempt_number: int) -> None:
        observed_drifts.append((attempt_number, exc.code))

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_prepare",
        staticmethod(force_next_drift),
    )
    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_drift",
        staticmethod(observe_drift),
    )
    result = admit_directed_effect_operation(_command(identity, binding))

    assert result.code == "guarded_reprepare_exhausted"
    assert result.operation is not None
    assert result.evidence == {
        "attempts_total": 3,
        "reprepare_count": 2,
        "drift_codes": drift_codes,
        "target_head_seq": prepared_heads[-1][0],
        "guard_head_seq": prepared_heads[-1][1],
        "operation_identity": result.operation.to_record(),
        "parent_binding_id": binding.binding_id,
    }
    assert prepared_heads == [(0, 1), (0, 1), (0, 1)]
    assert observed_drifts == list(enumerate(drift_codes, start=1))
    registry_events = _parent_registry_events(identity)
    assert len(registry_events) == 1
    assert registry_events[0]["event_type"] == ("task_runtime.directed_effect_parent_registry.v1.parent_admitted")
    decoy_events = tuple(
        event
        for stream_pair in decoy_stream_pairs
        for stream in stream_pair
        for event in query_fact_events(
            QueryFactEventsV1(
                workspace=identity.workspace,
                stream=stream,
                strict_integrity=True,
            )
        ).events
    )
    assert len(decoy_events) == 6
    assert {event["event_type"] for event in decoy_events} == {drift_marker_type}
    child_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert child_events == ()


@pytest.mark.parametrize("winner", ("parent_admission", "settlement"))
def test_real_thread_parent_admission_and_settlement_linearize_without_split_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winner: str,
) -> None:
    identity = _setup_attempt(str(tmp_path.resolve()))
    winner_locked = Event()
    release_winner = Event()
    loser_started = Event()

    def hold_winner(operation: str, observed_identity: object) -> None:
        del observed_identity
        if operation != winner:
            return
        winner_locked.set()
        assert release_winner.wait(timeout=15)

    monkeypatch.setattr(
        TaskRuntimeService,
        "_after_directed_effect_linearization_lock",
        staticmethod(hold_winner),
    )
    results: dict[str, Any] = {}

    def admit_parent() -> None:
        parent = admit_directed_effect_parent(_parent_command(identity))
        results["parent_admission"] = parent
        if parent.ok and parent.parent_binding is not None:
            results["operation_enrollment"] = enroll_directed_effect_operation_stream(
                EnrollDirectedEffectOperationStreamCommandV1(
                    execution_attempt=identity,
                    parent_binding=parent.parent_binding,
                )
            )

    def settle() -> None:
        results["settlement"] = settle_task_runtime_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                outcome="completed",
                summary="thread parent admission race",
                lock_timeout_seconds=10.0,
            )
        )

    operations = {"parent_admission": admit_parent, "settlement": settle}
    loser = "settlement" if winner == "parent_admission" else "parent_admission"
    winner_thread = Thread(target=operations[winner])

    def run_loser() -> None:
        loser_started.set()
        operations[loser]()

    loser_thread = Thread(target=run_loser)
    winner_thread.start()
    assert winner_locked.wait(timeout=15)
    loser_thread.start()
    assert loser_started.wait(timeout=5)
    release_winner.set()
    for thread in (winner_thread, loser_thread):
        thread.join(timeout=20)
        assert not thread.is_alive()

    parent = results["parent_admission"]
    settlement = results["settlement"]
    service = TaskRuntimeService(identity.workspace)
    session = service._read_session(identity.task_id)
    assert session is not None
    row = service.get_task(identity.task_id)
    assert row is not None
    if winner == "parent_admission":
        assert parent.code == "parent_admitted"
        enrollment = results["operation_enrollment"]
        assert enrollment.ok is True
        assert enrollment.code == "operation_stream_enrolled"
        assert enrollment.parent_binding == parent.parent_binding
        assert enrollment.evidence["receipt_authoritative"] is False
        assert settlement["success"] is False
        assert settlement["code"] == "settlement_parent_close_required"
        assert session.status == "active"
        assert row["status"] == "in_progress"
        assert len(_parent_registry_events(identity)) == 1
        assert _terminal_execution_events(identity) == ()
        assert parent.parent_binding is not None
        assert (
            query_fact_events(
                QueryFactEventsV1(
                    workspace=identity.workspace,
                    stream=parent.parent_binding.operation_stream_token,
                    strict_integrity=True,
                )
            ).events
            == ()
        )
    else:
        assert settlement["success"] is True
        assert settlement["code"] == "settled"
        assert parent.code in {"lease_version_mismatch", "session_not_active"}
        assert "operation_enrollment" not in results
        assert session.status == "completed"
        assert row["status"] == "completed"
        assert _parent_registry_events(identity) == ()
        assert len(_terminal_execution_events(identity)) == 1


@pytest.mark.parametrize("winner", ("parent_admission", "settlement"))
def test_real_process_parent_admission_and_settlement_linearize_without_split_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winner: str,
) -> None:
    identity = _setup_attempt(str(tmp_path.resolve()))
    context = multiprocessing.get_context("fork")
    winner_locked = context.Event()
    release_winner = context.Event()

    def hold_winner(operation: str, observed_identity: object) -> None:
        del observed_identity
        if operation != winner:
            return
        winner_locked.set()
        if not release_winner.wait(timeout=20):
            raise RuntimeError("timed out waiting to release DEO linearization winner")

    monkeypatch.setattr(
        TaskRuntimeService,
        "_after_directed_effect_linearization_lock",
        staticmethod(hold_winner),
    )
    queue: multiprocessing.Queue[dict[str, Any]] = context.Queue()
    started = {
        "parent_admission": context.Event(),
        "settlement": context.Event(),
    }
    processes = {
        "parent_admission": context.Process(
            target=_process_parent_admission,
            args=(
                identity.to_record(),
                started["parent_admission"],
                queue,
            ),
        ),
        "settlement": context.Process(
            target=_process_settlement,
            args=(
                identity.to_record(),
                started["settlement"],
                queue,
            ),
        ),
    }
    loser = "settlement" if winner == "parent_admission" else "parent_admission"
    processes[winner].start()
    assert started[winner].wait(timeout=10)
    assert winner_locked.wait(timeout=15)
    processes[loser].start()
    assert started[loser].wait(timeout=10)
    release_winner.set()
    for process in processes.values():
        process.join(timeout=25)
        assert process.exitcode == 0
    observed = {item["operation"]: item for item in (queue.get(timeout=5), queue.get(timeout=5))}
    queue.close()
    queue.join_thread()

    service = TaskRuntimeService(identity.workspace)
    session = service._read_session(identity.task_id)
    assert session is not None
    row = service.get_task(identity.task_id)
    assert row is not None
    if winner == "parent_admission":
        assert observed["parent_admission"]["code"] == "parent_admitted"
        assert observed["parent_admission"]["operation_enrollment_success"] is True
        assert observed["parent_admission"]["operation_enrollment_code"] == "operation_stream_enrolled"
        assert observed["parent_admission"]["operation_enrollment_receipt_authoritative"] is False
        assert observed["settlement"] == {
            "operation": "settlement",
            "success": False,
            "code": "settlement_parent_close_required",
        }
        assert session.status == "active"
        assert row["status"] == "in_progress"
        assert len(_parent_registry_events(identity)) == 1
        assert _terminal_execution_events(identity) == ()
        operation_stream = observed["parent_admission"]["operation_stream_token"]
        assert isinstance(operation_stream, str)
        assert (
            query_fact_events(
                QueryFactEventsV1(
                    workspace=identity.workspace,
                    stream=operation_stream,
                    strict_integrity=True,
                )
            ).events
            == ()
        )
    else:
        assert observed["settlement"]["code"] == "settled"
        assert observed["settlement"]["success"] is True
        assert observed["parent_admission"]["code"] in {
            "lease_version_mismatch",
            "session_not_active",
        }
        assert observed["parent_admission"]["operation_enrollment_success"] is None
        assert observed["parent_admission"]["operation_enrollment_code"] is None
        assert session.status == "completed"
        assert row["status"] == "completed"
        assert _parent_registry_events(identity) == ()
        assert len(_terminal_execution_events(identity)) == 1


def test_registry_enrollment_racing_settlement_never_appends_parent_fact(
    tmp_path: Path,
) -> None:
    identity = _setup_attempt(str(tmp_path.resolve()), enroll_registry=False)
    start = Barrier(2)
    results: dict[str, Any] = {}

    def enroll() -> None:
        start.wait(timeout=10)
        results["enrollment"] = enroll_directed_effect_parent_registry_stream(
            EnrollDirectedEffectParentRegistryStreamCommandV1(execution_attempt=identity)
        )

    def settle() -> None:
        start.wait(timeout=10)
        results["settlement"] = settle_task_runtime_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                outcome="completed",
                summary="enrollment race settlement",
            )
        )

    threads = (Thread(target=enroll), Thread(target=settle))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()

    enrollment = results["enrollment"]
    assert (enrollment.ok, enrollment.code) in {
        (True, "parent_registry_stream_enrolled"),
        (False, "lease_version_mismatch"),
        (False, "session_not_active"),
    }
    assert results["settlement"]["success"] is True
    assert results["settlement"]["code"] == "settled"
    rejected = admit_directed_effect_parent(_parent_command(identity))
    assert rejected.code in {"lease_version_mismatch", "session_not_active"}
    if enrollment.ok:
        assert _parent_registry_events(identity) == ()
    else:
        with pytest.raises(FactStreamError) as exc_info:
            _parent_registry_events(identity)
        assert exc_info.value.code == "stream_lock_missing"


def test_parent_admission_does_not_reenter_public_attempt_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _setup_attempt(str(tmp_path.resolve()))

    def recursive_validation_forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("parent admission must use caller-held locked validation")

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "validate_attempt",
        recursive_validation_forbidden,
    )
    monkeypatch.setattr(
        TaskRuntimeService,
        "validate_execution_attempt",
        recursive_validation_forbidden,
    )
    result = admit_directed_effect_parent(_parent_command(identity))
    assert result.code == "parent_admitted"
    assert len(_parent_registry_events(identity)) == 1
