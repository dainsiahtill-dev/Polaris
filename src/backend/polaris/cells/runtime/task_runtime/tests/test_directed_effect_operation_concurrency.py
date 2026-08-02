from __future__ import annotations

import multiprocessing
from dataclasses import replace
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
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentBatchCommandV1,
    AdmitDirectedEffectParentCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ParentCorrelationV1,
    SealDirectedEffectInventoryCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    abort_directed_effect_operation,
    admit_directed_effect_operation,
    admit_directed_effect_parent,
    admit_directed_effect_parent_batch,
    claim_directed_effect,
    commit_directed_effect_receipt,
    enroll_directed_effect_operation_stream,
    enroll_directed_effect_parent_registry_stream,
    finalize_directed_effect_inventory_admission,
    get_directed_effect_inventory,
    heartbeat_task_runtime_execution_attempt,
    seal_directed_effect_inventory,
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


def _setup(
    workspace: str,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
    AdmitDirectedEffectOperationCommandV1,
]:
    identity = _setup_attempt(workspace)
    parent = admit_directed_effect_parent(_parent_command(identity))
    assert parent.parent_binding is not None
    binding = parent.parent_binding
    assert enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(execution_attempt=identity, parent_binding=binding)
    ).ok
    command = _command(identity, binding)
    sealed = seal_directed_effect_inventory(
        SealDirectedEffectInventoryCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            intents=(
                DirectedEffectInventoryIntentV1(
                    ordinal=0,
                    tool_call_id=command.tool_call_id,
                    normalized_tool_name="test_write",
                    effect_type="write",
                    execution_mode="write_serial",
                    intended_effect_fingerprint=deo_internal._hash_token(
                        {
                            "fingerprint": command.intended_effect_fingerprint,
                            "requested_effect_id": command.effect_id,
                        }
                    ),
                    policy_verdict_hash=deo_internal._hash_token({"policy_verdict": command.policy_verdict_hash}),
                    expected_receipt_binding_hash=deo_internal._hash_token(
                        {"receipt_binding": command.expected_receipt_binding_hash}
                    ),
                ),
            ),
            expected_registry_version=1,
            expected_registry_seq=2,
        )
    )
    assert sealed.code == "inventory_sealed"
    assert sealed.projection is not None
    member = sealed.projection.members[0]
    return (
        identity,
        binding,
        replace(
            command,
            effect_id=member.effect_id,
            intended_effect_fingerprint=member.intended_effect_fingerprint,
            policy_verdict_hash=member.policy_verdict_hash,
            expected_receipt_binding_hash=member.expected_receipt_binding_hash,
        ),
    )


def _setup_receipt_complete_parent(
    workspace: str,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
]:
    identity, binding, admission = _setup(workspace)
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
    assert (
        finalize_directed_effect_inventory_admission(
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
        ).code
        == "inventory_ready"
    )
    assert (
        claim_directed_effect(
            ClaimDirectedEffectCommandV1(
                workspace=identity.workspace,
                task_id=identity.task_id,
                execution_attempt=identity,
                parent_binding=binding,
                tool_call_id=admission.tool_call_id,
                effect_id=admission.effect_id,
                expected_version=1,
                expected_seq=2,
                actor="test",
                intended_effect_fingerprint=admission.intended_effect_fingerprint,
                policy_verdict_hash=admission.policy_verdict_hash,
                expected_receipt_binding_hash=admission.expected_receipt_binding_hash,
            )
        ).code
        == "effect_claimed"
    )
    assert (
        commit_directed_effect_receipt(
            CommitDirectedEffectReceiptCommandV1(
                workspace=identity.workspace,
                task_id=identity.task_id,
                execution_attempt=identity,
                parent_binding=binding,
                tool_call_id=admission.tool_call_id,
                effect_id=admission.effect_id,
                expected_version=2,
                expected_seq=3,
                actor="test",
                intended_effect_fingerprint=admission.intended_effect_fingerprint,
                policy_verdict_hash=admission.policy_verdict_hash,
                expected_receipt_binding_hash=admission.expected_receipt_binding_hash,
                receipt_ref="receipt://concurrency/batch-rollover",
                receipt_hash="7" * 64,
                receipt_binding_hash=admission.expected_receipt_binding_hash,
                receipt_outcome="succeeded",
            )
        ).code
        == "receipt_committed"
    )
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


def _setup_unsealed_inventory(
    workspace: str,
    *,
    member_count: int = 1,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
    SealDirectedEffectInventoryCommandV1,
]:
    identity = _setup_attempt(workspace)
    parent = admit_directed_effect_parent(_parent_command(identity))
    assert parent.parent_binding is not None
    binding = parent.parent_binding
    assert enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(
            execution_attempt=identity,
            parent_binding=binding,
        )
    ).ok
    intents = tuple(
        DirectedEffectInventoryIntentV1(
            ordinal=ordinal,
            tool_call_id=f"tool-{ordinal}",
            normalized_tool_name="test_write",
            effect_type="write",
            execution_mode="write_serial",
            intended_effect_fingerprint=deo_internal._hash_token({"fingerprint": f"fingerprint-{ordinal}"}),
            policy_verdict_hash=deo_internal._hash_token({"policy_verdict": f"policy-{ordinal}"}),
            expected_receipt_binding_hash=deo_internal._hash_token({"receipt_binding": f"receipt-{ordinal}"}),
        )
        for ordinal in range(member_count)
    )
    return (
        identity,
        binding,
        SealDirectedEffectInventoryCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            intents=intents,
            expected_registry_version=1,
            expected_registry_seq=2,
        ),
    )


def _admission_for_member(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    member: Any,
    *,
    expected_seq: int,
) -> AdmitDirectedEffectOperationCommandV1:
    return AdmitDirectedEffectOperationCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        expected_version=0,
        expected_seq=expected_seq,
        actor="test",
        intended_effect_fingerprint=member.intended_effect_fingerprint,
        policy_verdict_hash=member.policy_verdict_hash,
        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
    )


def _install_first_prepare_barrier(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = Barrier(2)
    counter_lock = Lock()
    prepare_count = 0

    def synchronize_first_prepare(_snapshot: object) -> None:
        nonlocal prepare_count
        with counter_lock:
            prepare_count += 1
            synchronize = prepare_count <= 2
        if synchronize:
            barrier.wait(timeout=10)

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_prepare",
        staticmethod(synchronize_first_prepare),
    )


def _run_two_threads(first: Any, second: Any) -> tuple[Any, Any]:
    results: list[Any] = []
    failures: list[tuple[BaseException, Any]] = []
    result_lock = Lock()

    def invoke(call: Any) -> None:
        try:
            result = call()
        except BaseException as exc:  # noqa: BLE001 - worker failures must reach pytest.
            with result_lock:
                failures.append((exc, exc.__traceback__))
        else:
            with result_lock:
                results.append(result)

    threads = [Thread(target=invoke, args=(call,)) for call in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    if failures:
        exception, traceback = failures[0]
        raise exception.with_traceback(traceback)
    for thread in threads:
        assert not thread.is_alive()
    assert len(results) == 2
    return results[0], results[1]


def _process_admit(
    command: AdmitDirectedEffectOperationCommandV1,
    queue: multiprocessing.Queue[dict[str, Any]],
) -> None:
    result = admit_directed_effect_operation(command)
    queue.put({"code": result.code, "ok": result.ok})


def _process_claim(
    command: ClaimDirectedEffectCommandV1,
    barrier: Any,
    queue: multiprocessing.Queue[dict[str, Any]],
) -> None:
    barrier.wait(timeout=15)
    result = claim_directed_effect(command)
    queue.put(
        {
            "code": result.code,
            "ok": result.ok,
            "has_claim_grant": result.claim_grant is not None,
        }
    )


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
    registry_head = len(
        query_fact_events(
            QueryFactEventsV1(
                workspace=binding.workspace,
                stream=binding.registry_stream_token,
                strict_integrity=True,
            )
        ).events
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=binding.workspace,
            stream=binding.registry_stream_token,
            event_type="task_runtime.deo_parent_registry.v1.closed",
            payload={
                "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
                "stable_registry_identity": binding.registry_identity.to_record(),
                "previous_version": registry_head,
                "version": registry_head + 1,
                "parent_sequence": binding.parent_sequence,
                "binding_id": binding.binding_id,
                "close_evidence_ref": "fact://test/close",
                "close_evidence_hash": "a" * 64,
                "actor": "test",
                "recorded_at": "2026-07-15T00:00:00+00:00",
            },
            source="test",
            idempotency_key=idempotency_key,
            expected_seq=registry_head + 1,
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


def _stream_events(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    stream: str,
) -> tuple[dict[str, Any], ...]:
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


def test_run_two_threads_reraises_worker_base_exception_with_original_traceback() -> None:
    class ThreadFailure(BaseException):
        pass

    failure = ThreadFailure("worker failed")

    def fail_with_original_frame() -> None:
        raise failure

    with pytest.raises(ThreadFailure) as caught:
        _run_two_threads(fail_with_original_frame, lambda: None)

    assert caught.value is failure
    traceback_names: list[str] = []
    traceback = caught.value.__traceback__
    while traceback is not None:
        traceback_names.append(traceback.tb_frame.f_code.co_name)
        traceback = traceback.tb_next
    assert "fail_with_original_frame" in traceback_names


def test_real_thread_workflow_has_one_durable_event(tmp_path: Path) -> None:
    identity, binding, command = _setup(str(tmp_path.resolve()))
    observed: list[str] = []
    threads = [Thread(target=lambda: observed.append(admit_directed_effect_operation(command).code)) for _ in range(2)]
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
    identity, binding, command = _setup(str(tmp_path.resolve()))
    context = multiprocessing.get_context("fork")
    queue: multiprocessing.Queue[dict[str, Any]] = context.Queue()
    processes = [context.Process(target=_process_admit, args=(command, queue)) for _ in range(2)]
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


def test_task5_real_process_exact_claim_race_has_one_grant_single_winner(tmp_path: Path) -> None:
    identity, binding, admission = _setup(str(tmp_path.resolve()))
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
    assert (
        finalize_directed_effect_inventory_admission(
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
        ).code
        == "inventory_ready"
    )
    command = ClaimDirectedEffectCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=admission.tool_call_id,
        effect_id=admission.effect_id,
        expected_version=1,
        expected_seq=2,
        actor=admission.actor,
        intended_effect_fingerprint=admission.intended_effect_fingerprint,
        policy_verdict_hash=admission.policy_verdict_hash,
        expected_receipt_binding_hash=admission.expected_receipt_binding_hash,
    )
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    queue: multiprocessing.Queue[dict[str, Any]] = context.Queue()
    processes = [context.Process(target=_process_claim, args=(command, barrier, queue)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    results = tuple(queue.get(timeout=5) for _ in processes)

    codes = tuple(result["code"] for result in results)
    operation_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(operation_events) == 2
    assert codes.count("effect_claimed") == 1
    assert codes.count("idempotent_replay") == 1
    fresh = next(result for result in results if result["code"] == "effect_claimed")
    replay = next(result for result in results if result["code"] == "idempotent_replay")
    assert fresh["has_claim_grant"] is True
    assert replay["has_claim_grant"] is False


def test_task6_concurrent_exact_seals_have_one_fresh_and_one_typed_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, command = _setup_unsealed_inventory(str(tmp_path.resolve()))
    _install_first_prepare_barrier(monkeypatch)

    results = _run_two_threads(
        lambda: seal_directed_effect_inventory(command),
        lambda: seal_directed_effect_inventory(command),
    )

    codes = tuple(result.code for result in results)
    registry_events = _stream_events(identity, binding.registry_stream_token)
    operation_events = _stream_events(identity, binding.operation_stream_token)
    assert codes.count("inventory_sealed") == 1
    assert codes.count("inventory_seal_idempotent_replay") == 1
    assert len(registry_events) == 2
    assert registry_events[-1]["event_type"] == deo_internal._PARENT_INVENTORY_SEALED_EVENT_TYPE
    assert operation_events == ()


def test_task6_concurrent_different_seals_have_one_canonical_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, first = _setup_unsealed_inventory(str(tmp_path.resolve()))
    changed_intent = replace(
        first.intents[0],
        policy_verdict_hash=deo_internal._hash_token({"policy_verdict": "different"}),
    )
    second = replace(first, intents=(changed_intent,))
    _install_first_prepare_barrier(monkeypatch)

    results = _run_two_threads(
        lambda: seal_directed_effect_inventory(first),
        lambda: seal_directed_effect_inventory(second),
    )

    fresh = [result for result in results if result.code == "inventory_sealed"]
    conflicts = [result for result in results if result.code == "inventory_seal_conflict"]
    assert len(fresh) == 1
    assert len(conflicts) == 1
    assert fresh[0].projection is not None
    registry_events = _stream_events(identity, binding.registry_stream_token)
    assert len(registry_events) == 2
    assert registry_events[-1]["payload"]["inventory_hash"] == fresh[0].projection.inventory_hash
    assert _stream_events(identity, binding.operation_stream_token) == ()


def test_task6_final_admission_racing_finalize_never_publishes_partial_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, seal_command = _setup_unsealed_inventory(
        str(tmp_path.resolve()),
        member_count=2,
    )
    sealed = seal_directed_effect_inventory(seal_command)
    assert sealed.projection is not None
    first_member, final_member = sealed.projection.members
    assert (
        admit_directed_effect_operation(_admission_for_member(identity, binding, first_member, expected_seq=1)).code
        == "admitted"
    )
    final_admission = _admission_for_member(identity, binding, final_member, expected_seq=2)
    finalize = FinalizeDirectedEffectInventoryAdmissionCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        inventory_hash=sealed.projection.inventory_hash,
        expected_registry_version=2,
        expected_registry_seq=3,
        expected_operation_head_seq=2,
    )
    real_read_snapshot = deo_internal.read_guarded_fact_snapshot
    barrier = Barrier(2)
    counter_lock = Lock()
    read_count = 0

    def synchronized_snapshot(command: Any) -> Any:
        nonlocal read_count
        snapshot = real_read_snapshot(command)
        with counter_lock:
            read_count += 1
            synchronize = read_count <= 2
        if synchronize:
            barrier.wait(timeout=10)
        return snapshot

    monkeypatch.setattr(deo_internal, "read_guarded_fact_snapshot", synchronized_snapshot)

    results = _run_two_threads(
        lambda: admit_directed_effect_operation(final_admission),
        lambda: finalize_directed_effect_inventory_admission(finalize),
    )

    assert {result.code for result in results} == {"admitted", "inventory_admission_incomplete"}
    assert len(_stream_events(identity, binding.registry_stream_token)) == 2
    assert len(_stream_events(identity, binding.operation_stream_token)) == 2
    stale = finalize_directed_effect_inventory_admission(replace(finalize, expected_operation_head_seq=1))
    assert stale.code == "stream_expected_seq_conflict"
    assert len(_stream_events(identity, binding.registry_stream_token)) == 2

    ready = finalize_directed_effect_inventory_admission(finalize)

    assert ready.code == "inventory_ready"
    assert ready.projection is not None
    assert ready.projection.inventory_ready is True
    assert len(_stream_events(identity, binding.registry_stream_token)) == 3
    assert len(_stream_events(identity, binding.operation_stream_token)) == 2


def test_r145_inventory_finalize_survives_same_owner_lease_renew_after_admits(
    tmp_path: Path,
) -> None:
    """r144 residual: concurrent same-owner heartbeat after multi-member admit
    must not block inventory_ready (deo_inventory_ready_failed root cause).
    """

    identity, binding, seal_command = _setup_unsealed_inventory(
        str(tmp_path.resolve()),
        member_count=4,
    )
    sealed = seal_directed_effect_inventory(seal_command)
    assert sealed.projection is not None
    for index, member in enumerate(sealed.projection.members, start=1):
        admitted = admit_directed_effect_operation(
            _admission_for_member(identity, binding, member, expected_seq=index)
        )
        assert admitted.code == "admitted"

    # Concurrent same-owner heartbeat advances lease_expires_at while finalize
    # still holds the pre-heartbeat execution_attempt identity (live r144 pattern).
    heartbeat = heartbeat_task_runtime_execution_attempt(
        HeartbeatTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            lease_ttl_seconds=120,
            context_summary="director_loop_same_owner_renew_during_deo_prepare",
            lock_timeout_seconds=5.0,
        )
    )
    assert heartbeat.success is True
    assert heartbeat.renewed_identity is not None
    assert heartbeat.renewed_identity.lease_expires_at != identity.lease_expires_at

    finalize = FinalizeDirectedEffectInventoryAdmissionCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        inventory_hash=sealed.projection.inventory_hash,
        expected_registry_version=2,
        expected_registry_seq=3,
        expected_operation_head_seq=4,
    )
    ready = finalize_directed_effect_inventory_admission(finalize)
    assert ready.code == "inventory_ready", ready.evidence
    assert ready.projection is not None
    assert ready.projection.inventory_ready is True
    assert ready.projection.admitted_count == 4
    assert len(_stream_events(identity, binding.registry_stream_token)) == 3


def test_task6_ready_claim_racing_abort_has_one_terminal_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, seal_command = _setup_unsealed_inventory(str(tmp_path.resolve()))
    sealed = seal_directed_effect_inventory(seal_command)
    assert sealed.projection is not None
    member = sealed.projection.members[0]
    assert (
        admit_directed_effect_operation(_admission_for_member(identity, binding, member, expected_seq=1)).code
        == "admitted"
    )
    finalize = FinalizeDirectedEffectInventoryAdmissionCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        inventory_hash=sealed.projection.inventory_hash,
        expected_registry_version=2,
        expected_registry_seq=3,
        expected_operation_head_seq=1,
    )
    assert finalize_directed_effect_inventory_admission(finalize).code == "inventory_ready"
    claim = ClaimDirectedEffectCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        expected_version=1,
        expected_seq=2,
        actor="test",
        intended_effect_fingerprint=member.intended_effect_fingerprint,
        policy_verdict_hash=member.policy_verdict_hash,
        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
    )
    abort = AbortDirectedEffectOperationCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        expected_version=1,
        expected_seq=2,
        actor="test",
        intended_effect_fingerprint=member.intended_effect_fingerprint,
        policy_verdict_hash=member.policy_verdict_hash,
        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
        reason="task6 claim-abort race",
    )
    _install_first_prepare_barrier(monkeypatch)

    results = _run_two_threads(
        lambda: claim_directed_effect(claim),
        lambda: abort_directed_effect_operation(abort),
    )

    fresh = [result for result in results if result.code in {"effect_claimed", "aborted"}]
    losers = [result for result in results if not result.ok]
    assert len(fresh) == 1
    assert len(losers) == 1
    assert losers[0].code in {
        "operation_version_conflict",
        "illegal_transition",
        "stream_expected_seq_conflict",
    }
    assert losers[0].claim_grant is None
    if fresh[0].code == "effect_claimed":
        assert fresh[0].claim_grant is not None
    else:
        assert fresh[0].claim_grant is None
    operation_events = _stream_events(identity, binding.operation_stream_token)
    assert len(operation_events) == 2
    assert operation_events[-1]["payload"]["state"] in {"EFFECT_STARTED", "ABORTED"}
    assert len(_stream_events(identity, binding.registry_stream_token)) == 3


def test_receipt_commit_racing_settlement_never_closes_unreceipted_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, admission = _setup(str(tmp_path.resolve()))
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
    assert (
        finalize_directed_effect_inventory_admission(
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
        ).code
        == "inventory_ready"
    )
    claim = claim_directed_effect(
        ClaimDirectedEffectCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=admission.tool_call_id,
            effect_id=admission.effect_id,
            expected_version=1,
            expected_seq=2,
            actor="test",
            intended_effect_fingerprint=admission.intended_effect_fingerprint,
            policy_verdict_hash=admission.policy_verdict_hash,
            expected_receipt_binding_hash=admission.expected_receipt_binding_hash,
        )
    )
    assert claim.code == "effect_claimed"
    receipt = CommitDirectedEffectReceiptCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=admission.tool_call_id,
        effect_id=admission.effect_id,
        expected_version=2,
        expected_seq=3,
        actor="test",
        intended_effect_fingerprint=admission.intended_effect_fingerprint,
        policy_verdict_hash=admission.policy_verdict_hash,
        expected_receipt_binding_hash=admission.expected_receipt_binding_hash,
        receipt_ref="receipt://race/effect",
        receipt_hash="4" * 64,
        receipt_binding_hash=admission.expected_receipt_binding_hash,
        receipt_outcome="succeeded",
    )
    prepared = Event()
    release = Event()

    def pause_receipt(snapshot: object) -> None:
        del snapshot
        prepared.set()
        assert release.wait(timeout=15)

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_prepare",
        staticmethod(pause_receipt),
    )
    observed: dict[str, Any] = {}
    receipt_thread = Thread(target=lambda: observed.setdefault("receipt", commit_directed_effect_receipt(receipt)))
    receipt_thread.start()
    assert prepared.wait(timeout=15)

    blocked = settle_task_runtime_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="receipt race must remain open",
            lock_timeout_seconds=10.0,
        )
    )

    assert blocked["success"] is False
    assert blocked["code"] == "settlement_directed_effect_unresolved"
    release.set()
    receipt_thread.join(timeout=20)
    assert not receipt_thread.is_alive()
    assert observed["receipt"].code == "receipt_committed"

    settled = settle_task_runtime_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="receipt race must remain open",
            lock_timeout_seconds=10.0,
        )
    )

    assert settled["success"] is True
    assert settled["code"] == "settled"
    assert [event["payload"]["state"] for event in _stream_events(identity, binding.operation_stream_token)] == [
        "INTENT_COMMITTED",
        "EFFECT_STARTED",
        "RECEIPT_COMMITTED",
        "CLOSED_BY_PARENT",
    ]
    assert len(_terminal_execution_events(identity)) == 1


def test_close_between_prepare_and_commit_causes_guard_drift_without_child_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, command = _setup(str(tmp_path.resolve()))
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
    result = admit_directed_effect_operation(command)
    assert result.code == "parent_closed"
    events = query_fact_events(
        QueryFactEventsV1(workspace=identity.workspace, stream=binding.operation_stream_token, strict_integrity=True)
    ).events
    assert events == ()


def test_two_prepared_same_semantic_calls_share_one_conservative_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, command = _setup(str(tmp_path.resolve()))
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
    threads = [Thread(target=lambda: results.append(admit_directed_effect_operation(command))) for _ in range(2)]
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


def test_guard_drift_then_same_owner_lease_rotation_does_not_block_authority_reprepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R145: same-owner heartbeat during guarded reprepare is not authority steal.

    Parent close still fails the admit for business reasons; the point is that
    concurrent lease renew must not be the blocking reason.
    """

    identity, binding, command = _setup(str(tmp_path.resolve()))
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
    result = admit_directed_effect_operation(command)

    # Same-owner lease renew is accepted; parent close remains the business failure.
    assert result.code != "lease_version_mismatch"
    assert result.ok is False
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
    identity, binding, command = _setup(str(tmp_path.resolve()))
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
    result = admit_directed_effect_operation(command)

    assert result.code == "guarded_reprepare_exhausted"
    assert result.operation is not None
    assert result.evidence == {
        "attempts_total": 3,
        "reprepare_count": 2,
        "drift_codes": drift_codes,
        "target_head_seq": prepared_heads[-1][0],
        "guard_head_seq": binding.source_event_seq,
        "operation_identity": result.operation.to_record(),
        "parent_binding_id": binding.binding_id,
    }
    assert prepared_heads == [(0, 2), (0, 2), (0, 2)]
    assert observed_drifts == list(enumerate(drift_codes, start=1))
    registry_events = _parent_registry_events(identity)
    assert len(registry_events) == 2
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


@pytest.mark.parametrize("winner", ("parent_batch_admission", "settlement"))
def test_real_thread_parent_batch_rollover_and_settlement_linearize_without_split_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winner: str,
) -> None:
    identity, first_binding = _setup_receipt_complete_parent(str(tmp_path.resolve()))
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

    def admit_parent_batch() -> None:
        results["parent_batch_admission"] = admit_directed_effect_parent_batch(
            AdmitDirectedEffectParentBatchCommandV1(
                workspace=identity.workspace,
                task_id=identity.task_id,
                execution_attempt=identity,
                correlation=ParentCorrelationV1(turn_id="turn-2", batch_id="batch-2"),
                admission_idempotency_key="parent-2",
                actor="test",
            )
        )

    def settle() -> None:
        results["settlement"] = settle_task_runtime_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                outcome="completed",
                summary="thread parent batch rollover race",
                lock_timeout_seconds=10.0,
            )
        )

    operations = {
        "parent_batch_admission": admit_parent_batch,
        "settlement": settle,
    }
    loser = "settlement" if winner == "parent_batch_admission" else "parent_batch_admission"
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

    batch_admission = results["parent_batch_admission"]
    settlement = results["settlement"]
    service = TaskRuntimeService(identity.workspace)
    session = service._read_session(identity.task_id)
    assert session is not None
    row = service.get_task(identity.task_id)
    assert row is not None
    registry_events = _parent_registry_events(identity)
    first_operation_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=first_binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events

    if winner == "parent_batch_admission":
        assert batch_admission.code == "parent_admitted"
        assert batch_admission.parent_binding is not None
        assert batch_admission.parent_binding.parent_sequence == 2
        assert batch_admission.parent_binding.registry_version == 5
        assert settlement["success"] is False
        assert settlement["code"] == "settlement_parent_close_required"
        assert session.status == "active"
        assert row["status"] == "in_progress"
        assert len(registry_events) == 5
        assert registry_events[-2]["event_type"] == "task_runtime.deo_parent_registry.v1.closed"
        assert registry_events[-1]["event_type"] == "task_runtime.directed_effect_parent_registry.v1.parent_admitted"
        assert first_operation_events[-1]["event_type"] == "task_runtime.directed_effect_operation.v1.closed_by_parent"
        assert _terminal_execution_events(identity) == ()
    else:
        assert settlement["success"] is True
        assert settlement["code"] == "settled"
        assert batch_admission.code in {"lease_version_mismatch", "session_not_active"}
        assert batch_admission.parent_binding is None
        assert session.status == "completed"
        assert row["status"] == "completed"
        assert len(registry_events) == 4
        assert registry_events[-1]["event_type"] == "task_runtime.deo_parent_registry.v1.closed"
        assert first_operation_events[-1]["event_type"] == "task_runtime.directed_effect_operation.v1.closed_by_parent"
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
