from __future__ import annotations

import json
import multiprocessing as mp
import operator
import os
import threading
import time
from collections import UserDict
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import Literal, cast

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    AppendIfGuardedSnapshotCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
    FactStreamQueryResultV1,
    GuardedFactAppendedV1,
    GuardedFactEventV1,
    GuardedFactSnapshotV1,
    QueryFactEventsV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.internal import (
    directed_effect_operation as deo_internal,
    service as task_runtime_service_internal,
)
from polaris.cells.runtime.task_runtime.internal.task_board import TaskBoardFileLockTimeoutError
from polaris.cells.runtime.task_runtime.public import (
    DIRECTED_EFFECT_OPERATION_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V2,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V3,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2,
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentCommandV1,
    BindRuntimeTaskToFactoryRunCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DeadLetterDirectedEffectOperationCommandV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectOperationResultV1,
    DirectedEffectOperationStateV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentReadinessProjectionV1,
    DirectedEffectParentReadinessResultV1,
    DirectedEffectParentReadinessStateCountV1,
    DirectedEffectRecoverySweepResultV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    GetDirectedEffectOperationQueryV1,
    GetDirectedEffectParentReadinessQueryV1,
    GetDirectedEffectParentRegistryQueryV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    ParentCorrelationV1,
    ReconcileAmbiguousDirectedEffectsCommandV1,
    SealDirectedEffectInventoryCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    abort_directed_effect_operation,
    admit_directed_effect_operation,
    admit_directed_effect_parent,
    claim_directed_effect,
    commit_directed_effect_receipt,
    dead_letter_directed_effect_operation,
    enroll_directed_effect_operation_stream,
    enroll_directed_effect_parent_registry_stream,
    finalize_directed_effect_inventory_admission,
    get_directed_effect_inventory,
    get_directed_effect_operation,
    get_directed_effect_parent_readiness,
    get_directed_effect_parent_registry,
    heartbeat_task_runtime_execution_attempt,
    mark_directed_effect_recovery_pending,
    reconcile_ambiguous_directed_effects,
    seal_directed_effect_inventory,
)
from polaris.kernelone.storage import resolve_storage_roots

_PARENT_CLOSED_EVENT_TYPE = "task_runtime.deo_parent_registry.v1.closed"


def _attempt(workspace: Path) -> TaskRuntimeExecutionAttemptIdentityV1:
    workspace_abs = str(workspace.resolve())
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace_abs,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="directed-effect-operation-test",
        )
    )
    service = TaskRuntimeService(workspace_abs)
    task_id = int(service.create_task_row(subject="directed effect operation")["id"])
    binding = service.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=workspace_abs,
            task_id=str(task_id),
            factory_run_id="deo-test-run",
        )
    )
    assert binding.ok is True
    claimed = service.claim_execution(
        task_id,
        worker_id="deo-test-worker",
        role_id="director",
        run_id="deo-test-run",
        external_task_id="DEO-1B",
        selection_source="test",
    )
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])


def _parent_command(identity: TaskRuntimeExecutionAttemptIdentityV1) -> AdmitDirectedEffectParentCommandV1:
    return AdmitDirectedEffectParentCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        correlation=ParentCorrelationV1(turn_id="turn-1", batch_id="batch-1"),
        admission_idempotency_key="parent-1",
        expected_version=0,
        expected_seq=1,
        actor="test-parent",
    )


def _operation_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    *,
    tool_call_id: str = "tool-1",
    effect_id: str = "effect-1",
    fingerprint: str = "fingerprint-1",
    expected_seq: int = 1,
) -> AdmitDirectedEffectOperationCommandV1:
    return AdmitDirectedEffectOperationCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=tool_call_id,
        effect_id=effect_id,
        expected_version=0,
        expected_seq=expected_seq,
        actor="test-child",
        intended_effect_fingerprint=fingerprint,
        policy_verdict_hash="policy-1",
        expected_receipt_binding_hash="receipt-1",
    )


def _seal_operation_commands(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    *commands: AdmitDirectedEffectOperationCommandV1,
) -> tuple[AdmitDirectedEffectOperationCommandV1, ...]:
    intents = tuple(
        DirectedEffectInventoryIntentV1(
            ordinal=ordinal,
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
        )
        for ordinal, command in enumerate(commands)
    )
    sealed = seal_directed_effect_inventory(
        SealDirectedEffectInventoryCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            intents=intents,
            expected_registry_version=1,
            expected_registry_seq=2,
        )
    )
    assert sealed.code == "inventory_sealed"
    assert sealed.projection is not None
    return tuple(
        replace(
            command,
            effect_id=member.effect_id,
            intended_effect_fingerprint=member.intended_effect_fingerprint,
            policy_verdict_hash=member.policy_verdict_hash,
            expected_receipt_binding_hash=member.expected_receipt_binding_hash,
        )
        for command, member in zip(commands, sealed.projection.members, strict=True)
    )


def _finalize_operation_inventory(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
) -> None:
    observed = get_directed_effect_inventory(
        GetDirectedEffectInventoryQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
        )
    )
    assert observed.ok is True
    assert observed.projection is not None
    projection = observed.projection
    finalized = finalize_directed_effect_inventory_admission(
        FinalizeDirectedEffectInventoryAdmissionCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            inventory_hash=projection.inventory_hash,
            expected_registry_version=projection.parent_registry_source_head_seq,
            expected_registry_seq=projection.parent_registry_source_head_seq + 1,
            expected_operation_head_seq=projection.operation_source_head_seq,
        )
    )
    assert finalized.code == "inventory_ready"


def _claim_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    *,
    tool_call_id: str = "tool-1",
    effect_id: str = "effect-1",
    fingerprint: str = "fingerprint-1",
    expected_version: int = 1,
    expected_seq: int = 2,
) -> ClaimDirectedEffectCommandV1:
    return ClaimDirectedEffectCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=tool_call_id,
        effect_id=effect_id,
        expected_version=expected_version,
        expected_seq=expected_seq,
        actor="test-child",
        intended_effect_fingerprint=fingerprint,
        policy_verdict_hash="policy-1",
        expected_receipt_binding_hash="receipt-1",
    )


def _started_operation(
    workspace: Path,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
    AdmitDirectedEffectOperationCommandV1,
]:
    identity = _attempt(workspace)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    (admit_command,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))
    assert admit_directed_effect_operation(admit_command).code == "admitted"
    _finalize_operation_inventory(identity, binding)
    claim_command = ClaimDirectedEffectCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=admit_command.tool_call_id,
        effect_id=admit_command.effect_id,
        expected_version=1,
        expected_seq=2,
        actor="test-child",
        intended_effect_fingerprint=admit_command.intended_effect_fingerprint,
        policy_verdict_hash=admit_command.policy_verdict_hash,
        expected_receipt_binding_hash=admit_command.expected_receipt_binding_hash,
    )
    assert claim_directed_effect(claim_command).code == "effect_claimed"
    return identity, binding, admit_command


def _aborted_operation(
    workspace: Path,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
    AdmitDirectedEffectOperationCommandV1,
]:
    identity = _attempt(workspace)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    (admit_command,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))
    assert admit_directed_effect_operation(admit_command).code == "admitted"
    _finalize_operation_inventory(identity, binding)
    aborted = abort_directed_effect_operation(
        AbortDirectedEffectOperationCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=admit_command.tool_call_id,
            effect_id=admit_command.effect_id,
            expected_version=1,
            expected_seq=2,
            actor="test-abort",
            intended_effect_fingerprint=admit_command.intended_effect_fingerprint,
            policy_verdict_hash=admit_command.policy_verdict_hash,
            expected_receipt_binding_hash=admit_command.expected_receipt_binding_hash,
            reason="physical effect was not started",
        )
    )
    assert aborted.code == "aborted"
    assert aborted.state == "ABORTED"
    return identity, binding, admit_command


def _dead_lettered_operation(
    workspace: Path,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
    AdmitDirectedEffectOperationCommandV1,
]:
    identity, binding, admit_command = _started_operation(workspace)
    common = {
        "workspace": identity.workspace,
        "task_id": identity.task_id,
        "execution_attempt": identity,
        "parent_binding": binding,
        "tool_call_id": admit_command.tool_call_id,
        "effect_id": admit_command.effect_id,
        "actor": "test-recovery",
        "intended_effect_fingerprint": admit_command.intended_effect_fingerprint,
        "policy_verdict_hash": admit_command.policy_verdict_hash,
        "expected_receipt_binding_hash": admit_command.expected_receipt_binding_hash,
    }
    pending = mark_directed_effect_recovery_pending(
        MarkDirectedEffectRecoveryPendingCommandV1(
            **common,
            expected_version=2,
            expected_seq=3,
            reason="physical result requires reconciliation",
            recovery_evidence_ref="recovery://director/settlement-matrix",
            recovery_evidence_hash="c" * 64,
        )
    )
    assert pending.code == "recovery_pending"
    dead_letter = dead_letter_directed_effect_operation(
        DeadLetterDirectedEffectOperationCommandV1(
            **common,
            expected_version=3,
            expected_seq=4,
            reason="physical result cannot be reconciled",
            resolution_evidence_ref="dead-letter://director/settlement-matrix",
            resolution_evidence_hash="d" * 64,
        )
    )
    assert dead_letter.code == "dead_lettered"
    assert dead_letter.state == "DEAD_LETTER"
    return identity, binding, admit_command


def _allow_expired_session_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        TaskRuntimeService,
        "_directed_effect_recovery_session_is_expired",
        staticmethod(lambda _session: True),
    )


def _enroll_parent(identity: TaskRuntimeExecutionAttemptIdentityV1) -> None:
    result = enroll_directed_effect_parent_registry_stream(
        EnrollDirectedEffectParentRegistryStreamCommandV1(execution_attempt=identity)
    )
    assert result.code == "parent_registry_stream_enrolled"
    assert result.receipt is not None
    assert result.evidence["receipt_authoritative"] is False


def _admit_parent(identity: TaskRuntimeExecutionAttemptIdentityV1) -> DirectedEffectParentBindingV1:
    result = admit_directed_effect_parent(_parent_command(identity))
    assert result.code == "parent_admitted"
    assert result.parent_binding is not None
    return result.parent_binding


def _enroll_operation(identity: TaskRuntimeExecutionAttemptIdentityV1, binding: DirectedEffectParentBindingV1) -> None:
    result = enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(
            execution_attempt=identity,
            parent_binding=binding,
        )
    )
    assert result.code == "operation_stream_enrolled"
    assert result.parent_binding == binding
    assert result.receipt is not None
    assert result.evidence["receipt_authoritative"] is False


def _readiness_query(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
) -> GetDirectedEffectParentReadinessQueryV1:
    return GetDirectedEffectParentReadinessQueryV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
    )


def _append_operation_fact(
    command: AdmitDirectedEffectOperationCommandV1 | ClaimDirectedEffectCommandV1,
    binding: DirectedEffectParentBindingV1,
    *,
    kind: Literal["admit", "claim"],
    state: DirectedEffectOperationStateV1,
    previous_version: int,
    idempotency_key: str,
) -> str:
    repository = deo_internal.DirectedEffectOperationRepository
    operation = repository._derive_operation(command, binding)
    descriptor = repository._operation_descriptor(command, kind=kind)
    payload = repository._operation_event_canonical(
        operation=operation,
        state=state,
        previous_version=previous_version,
        descriptor=descriptor,
    )
    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=command.workspace,
            stream=binding.operation_stream_token,
            event_type=deo_internal._operation_event_type(state),
            payload=payload,
            source="test",
            idempotency_key=idempotency_key,
            expected_seq=command.expected_seq,
            durability="fsync",
            strict_integrity=True,
        )
    )
    return appended.event_id


def _file_bytes_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _close_parent(binding: DirectedEffectParentBindingV1) -> None:
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
            event_type=_PARENT_CLOSED_EVENT_TYPE,
            payload={
                "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
                "stable_registry_identity": binding.registry_identity.to_record(),
                "previous_version": registry_head,
                "version": registry_head + 1,
                "parent_sequence": binding.parent_sequence,
                "binding_id": binding.binding_id,
                "close_evidence_ref": "fact://test/close",
                "close_evidence_hash": "a" * 64,
                "actor": "test-close",
                "recorded_at": "2026-07-15T00:00:00+00:00",
            },
            source="test",
            idempotency_key=f"close-{binding.binding_id}",
            expected_seq=registry_head + 1,
            durability="fsync",
            strict_integrity=True,
        )
    )


def test_explicit_enrollment_order_fails_closed_without_implicit_maintenance(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    registry = get_directed_effect_parent_registry(
        GetDirectedEffectParentRegistryQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
        )
    )
    assert registry.code == "stream_lock_missing"
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    rejected = admit_directed_effect_operation(_operation_command(identity, binding))
    assert rejected.code == "stream_lock_missing"
    _enroll_operation(identity, binding)
    (command,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))
    assert admit_directed_effect_operation(command).code == "admitted"


def test_receipt_commit_is_guarded_durable_and_idempotent(tmp_path: Path) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    command = CommitDirectedEffectReceiptCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=admitted.tool_call_id,
        effect_id=admitted.effect_id,
        expected_version=2,
        expected_seq=3,
        actor="test-receipt",
        intended_effect_fingerprint=admitted.intended_effect_fingerprint,
        policy_verdict_hash=admitted.policy_verdict_hash,
        expected_receipt_binding_hash=admitted.expected_receipt_binding_hash,
        receipt_ref="receipt://director/effect-1",
        receipt_hash="4" * 64,
        receipt_binding_hash=admitted.expected_receipt_binding_hash,
        receipt_outcome="succeeded",
    )

    committed = commit_directed_effect_receipt(command)
    replayed = commit_directed_effect_receipt(command)
    observed = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
        )
    )
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events

    assert committed.code == "receipt_committed"
    assert committed.state == "RECEIPT_COMMITTED"
    assert committed.evidence["receipt_hash"] == "4" * 64
    assert replayed.code == "idempotent_replay"
    assert replayed.state == "RECEIPT_COMMITTED"
    assert replayed.evidence["receipt_ref"] == command.receipt_ref
    assert replayed.evidence["receipt_hash"] == command.receipt_hash
    assert replayed.evidence["receipt_binding_hash"] == command.receipt_binding_hash
    assert replayed.evidence["receipt_outcome"] == command.receipt_outcome
    assert observed.code == "found"
    assert observed.state == "RECEIPT_COMMITTED"
    payload = cast(dict[str, object], events[-1]["payload"])
    assert payload["schema_version"] == DIRECTED_EFFECT_OPERATION_SCHEMA_V3
    descriptor = cast(dict[str, object], payload["replay_descriptor"])
    assert descriptor["receipt_hash"] == "4" * 64
    assert descriptor["receipt_binding_hash"] == admitted.expected_receipt_binding_hash
    assert descriptor["receipt_outcome"] == "succeeded"


def test_recovery_is_finite_and_receipt_binding_mismatch_fails_closed(tmp_path: Path) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    common = {
        "workspace": identity.workspace,
        "task_id": identity.task_id,
        "execution_attempt": identity,
        "parent_binding": binding,
        "tool_call_id": admitted.tool_call_id,
        "effect_id": admitted.effect_id,
        "actor": "test-recovery",
        "intended_effect_fingerprint": admitted.intended_effect_fingerprint,
        "policy_verdict_hash": admitted.policy_verdict_hash,
        "expected_receipt_binding_hash": admitted.expected_receipt_binding_hash,
    }
    recovery = mark_directed_effect_recovery_pending(
        MarkDirectedEffectRecoveryPendingCommandV1(
            **common,
            expected_version=2,
            expected_seq=3,
            reason="physical result requires reconciliation",
            recovery_evidence_ref="recovery://director/effect-1",
            recovery_evidence_hash="5" * 64,
        )
    )
    mismatch = commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            **common,
            expected_version=3,
            expected_seq=4,
            receipt_ref="receipt://director/effect-1",
            receipt_hash="6" * 64,
            receipt_binding_hash="7" * 64,
            receipt_outcome="failed",
        )
    )
    dead_letter = dead_letter_directed_effect_operation(
        DeadLetterDirectedEffectOperationCommandV1(
            **common,
            expected_version=3,
            expected_seq=4,
            reason="receipt binding cannot be reconciled",
            resolution_evidence_ref="dead-letter://director/effect-1",
            resolution_evidence_hash="8" * 64,
        )
    )

    assert recovery.code == "recovery_pending"
    assert recovery.state == "RECOVERY_PENDING"
    assert mismatch.code == "receipt_binding_conflict"
    assert dead_letter.code == "dead_lettered"
    assert dead_letter.state == "DEAD_LETTER"


def test_startup_recovery_sweep_never_replays_effect_and_converges_to_dead_letter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    _allow_expired_session_recovery(monkeypatch)
    query = GetDirectedEffectOperationQueryV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=admitted.tool_call_id,
        effect_id=admitted.effect_id,
    )
    command = ReconcileAmbiguousDirectedEffectsCommandV1(
        workspace=identity.workspace,
        reason="factory settlement startup recovery",
        factory_run_id="deo-test-run",
    )

    first = reconcile_ambiguous_directed_effects(command)
    after_first = get_directed_effect_operation(query)
    second = reconcile_ambiguous_directed_effects(command)

    assert first.ok is True
    assert [(item.code, item.state) for item in first.items] == [
        ("recovery_pending", "RECOVERY_PENDING"),
        ("dead_lettered", "DEAD_LETTER"),
    ]
    assert after_first.state == "DEAD_LETTER"
    assert second.ok is True
    assert [(item.code, item.state) for item in second.items] == [
        ("recovery_pending", "RECOVERY_PENDING"),
        ("dead_lettered", "DEAD_LETTER"),
    ]
    assert [item.event_id for item in second.items] == [item.event_id for item in first.items]
    lease_path = (
        Path(resolve_storage_roots(identity.workspace).runtime_root) / "tasks" / "directed_effect_recovery.lease.json"
    )
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    assert lease["schema_version"] == "task-runtime.directed-effect-recovery-lease/1"
    assert lease["status"] == "released"
    assert lease["owner_pid"] == os.getpid()
    assert len(lease["owner_epoch"]) == 32
    assert len(lease["record_hash"]) == 64


def test_startup_recovery_fails_closed_on_corrupt_session(tmp_path: Path) -> None:
    identity, _binding, _admitted = _started_operation(tmp_path)
    session_path = (
        Path(resolve_storage_roots(identity.workspace).runtime_root) / "tasks" / f"task_{identity.task_id}.session.json"
    )
    session_path.write_text("{", encoding="utf-8")

    result = reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
        )
    )

    assert result.ok is False
    assert result.code == "partial_failure"
    assert result.scanned_session_count == 0
    assert [failure["code"] for failure in result.failures] == ["session_corrupt"]


def test_startup_recovery_distinguishes_repository_failure_from_session_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, _binding, _admitted = _started_operation(tmp_path)
    _allow_expired_session_recovery(monkeypatch)

    def fail_repository(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "reconcile_ambiguous_started_operations",
        fail_repository,
    )
    result = reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
        )
    )

    assert result.ok is False
    assert result.failures[0]["code"] == "recovery_repository_failure"
    assert result.failures[0]["task_id"] == identity.task_id
    assert result.failures[0]["session_id"] == identity.session_id


def test_startup_recovery_never_uses_stale_row_owner_for_new_locked_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, _binding, _admitted = _started_operation(tmp_path)
    service = TaskRuntimeService(identity.workspace)
    stale_projection = service.query_observable_task_rows_projection()
    current = service._read_session(identity.task_id)
    assert current is not None
    current.session_id = "tx-new-run-session"
    current.run_id = "new-factory-run"
    current.attempt += 1
    current.metadata["factory_run_id"] = "new-factory-run"
    assert service._write_session(current) is True

    monkeypatch.setattr(service, "query_observable_task_rows_projection", lambda: stale_projection)
    result = service.reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
        )
    )

    assert result.ok is True
    assert result.scanned_session_count == 0
    assert result.items == ()
    assert result.failures == ()


def test_session_factory_owner_ignores_role_run_id_and_volatile_lease_projection(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    service = TaskRuntimeService(identity.workspace)
    current = service._read_session(identity.task_id)
    assert current is not None
    current.run_id = "director-child-run"
    current.metadata.pop("factory_run_id", None)
    matching_row = {
        "session_id": current.session_id,
        "claim_attempt": current.attempt,
        # Factory ownership belongs to the stable execution attempt.  A
        # TaskBoard/fact projection may legitimately lag a session heartbeat,
        # so its volatile lease deadline must not erase that ownership.
        "lease_expires_at": "2026-07-20T00:00:00+00:00",
        "metadata": {"factory_run_id": "legacy-factory-run"},
    }
    stale_session_row = {**matching_row, "session_id": "tx-stale-session"}
    stale_attempt_row = {**matching_row, "claim_attempt": current.attempt + 1}

    assert service._session_factory_run_id(current, matching_row) == "legacy-factory-run"
    assert service._session_factory_run_id(current, stale_session_row) == ""
    assert service._session_factory_run_id(current, stale_attempt_row) == ""


def test_startup_recovery_command_rejects_caller_supplied_authority_without_mutation(
    tmp_path: Path,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    query = GetDirectedEffectOperationQueryV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=admitted.tool_call_id,
        effect_id=admitted.effect_id,
    )

    with pytest.raises(TypeError):
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            owner_epoch="foreign-process-epoch",
            owner_pid=os.getpid() + 1,
        )

    assert get_directed_effect_operation(query).state == "EFFECT_STARTED"


def test_startup_recovery_deadline_is_rechecked_after_repository_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    _allow_expired_session_recovery(monkeypatch)
    original_get = deo_internal.DirectedEffectOperationRepository.get

    def delayed_get(
        repository: deo_internal.DirectedEffectOperationRepository,
        query: GetDirectedEffectOperationQueryV1,
    ) -> DirectedEffectOperationResultV1:
        time.sleep(0.08)
        return original_get(repository, query)

    monkeypatch.setattr(deo_internal.DirectedEffectOperationRepository, "get", delayed_get)
    result = reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
            deadline_seconds=0.05,
        )
    )

    assert result.ok is False
    assert any(failure["code"] == "recovery_deadline_exceeded" for failure in result.failures)
    query = GetDirectedEffectOperationQueryV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=admitted.tool_call_id,
        effect_id=admitted.effect_id,
    )
    monkeypatch.setattr(deo_internal.DirectedEffectOperationRepository, "get", original_get)
    assert get_directed_effect_operation(query).state == "EFFECT_STARTED"


def test_startup_recovery_deadline_is_rechecked_after_recovery_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, _binding, _admitted = _started_operation(tmp_path)
    _allow_expired_session_recovery(monkeypatch)
    command = ReconcileAmbiguousDirectedEffectsCommandV1(
        workspace=identity.workspace,
        reason="factory settlement startup recovery",
        factory_run_id="deo-test-run",
    )
    assert reconcile_ambiguous_directed_effects(command).ok is True
    original_projection = deo_internal.DirectedEffectOperationRepository._recovery_fact_projection_results

    def delayed_projection(*args: object, **kwargs: object) -> object:
        time.sleep(0.08)
        return original_projection(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_recovery_fact_projection_results",
        delayed_projection,
    )
    result = reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
            deadline_seconds=0.05,
        )
    )

    assert result.ok is False
    assert any(failure["code"] == "recovery_deadline_exceeded" for failure in result.failures)
    assert any(item.state == "DEAD_LETTER" for item in result.items)


def test_startup_recovery_deadline_is_rechecked_after_dead_letter_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    _allow_expired_session_recovery(monkeypatch)
    original_mutate = deo_internal.DirectedEffectOperationRepository._mutate

    def delayed_dead_letter_mutation(
        repository: deo_internal.DirectedEffectOperationRepository,
        command: object,
        **kwargs: object,
    ) -> DirectedEffectOperationResultV1:
        if kwargs.get("kind") == "dead_letter":
            time.sleep(0.30)
        return original_mutate(repository, command, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_mutate",
        delayed_dead_letter_mutation,
    )
    result = reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
            deadline_seconds=0.20,
        )
    )

    assert result.ok is False
    assert any(failure["code"] == "recovery_deadline_exceeded" for failure in result.failures)
    observed = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
        )
    )
    assert observed.state == "DEAD_LETTER"


def test_startup_recovery_deadline_covers_empty_workspace_task_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TaskRuntimeService(str(tmp_path.resolve()))
    original_projection = TaskRuntimeService.query_observable_task_rows_projection

    def delayed_projection(runtime: TaskRuntimeService) -> object:
        time.sleep(0.08)
        return original_projection(runtime)

    monkeypatch.setattr(TaskRuntimeService, "query_observable_task_rows_projection", delayed_projection)
    result = service.reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=str(tmp_path.resolve()),
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
            deadline_seconds=0.05,
        )
    )

    assert result.ok is False
    assert result.scanned_session_count == 0
    assert result.failures[0]["code"] == "recovery_deadline_exceeded"
    assert result.failures[0]["stage"] == "after_task_projection"
    lease_path = Path(resolve_storage_roots(tmp_path).runtime_root) / "tasks" / "directed_effect_recovery.lease.json"
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    assert lease["status"] == "released"


def test_startup_recovery_deadline_is_rechecked_after_lease_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def delayed_hook(*, lease_id: str, owner_epoch: str) -> None:
        assert lease_id
        assert owner_epoch
        time.sleep(0.08)

    monkeypatch.setattr(
        TaskRuntimeService,
        "_after_directed_effect_recovery_lease_acquired",
        staticmethod(delayed_hook),
    )
    result = reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=str(tmp_path.resolve()),
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
            deadline_seconds=0.05,
        )
    )

    assert result.ok is False
    assert result.failures[0]["code"] == "recovery_deadline_exceeded"
    assert result.failures[0]["stage"] == "after_recovery_lease_hook"


def test_startup_recovery_deadline_expiry_during_lease_lock_wait_is_attributed_to_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TaskRuntimeService(str(tmp_path.resolve()))
    clock = {"now": 0.0}

    class DeadlineExpiredLock:
        def __enter__(self) -> None:
            clock["now"] = 2.0
            raise TaskBoardFileLockTimeoutError("synthetic lease lock timeout")

        def __exit__(self, *args: object) -> bool:
            del args
            return False

    def timed_out_file_lock(_path: Path, *, timeout_seconds: float | None = None) -> DeadlineExpiredLock:
        assert timeout_seconds == pytest.approx(1.0)
        return DeadlineExpiredLock()

    monkeypatch.setattr(task_runtime_service_internal.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(service._board, "_file_lock", timed_out_file_lock)
    result = service.reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=str(tmp_path.resolve()),
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
            deadline_seconds=1.0,
            lock_timeout_seconds=4.0,
        )
    )

    assert result.ok is False
    assert result.failures[0]["code"] == "recovery_deadline_exceeded"
    assert result.failures[0]["stage"] == "after_recovery_lease_lock_wait"


def test_startup_recovery_deadline_expiry_during_session_lock_wait_is_attributed_to_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, _binding, _admitted = _started_operation(tmp_path)
    service = TaskRuntimeService(identity.workspace)
    clock = {"now": 0.0}

    class DeadlineExpiredSessionLock:
        def acquire(self, *, timeout: float) -> bool:
            assert timeout == pytest.approx(1.0)
            clock["now"] = 2.0
            return False

        def release(self) -> None:
            raise AssertionError("an unacquired session lock must not be released")

    monkeypatch.setattr(task_runtime_service_internal.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(service, "_get_session_lock", lambda _task_id: DeadlineExpiredSessionLock())
    result = service._reconcile_directed_effect_recovery_task(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
            deadline_seconds=1.0,
            lock_timeout_seconds=4.0,
        ),
        task_id=identity.task_id,
        task_row={},
        owner_epoch="owner-epoch",
        deadline_monotonic=1.0,
        scanned_operation_count=0,
    )

    assert result.stop_sweep is True
    assert result.failures[0]["code"] == "recovery_deadline_exceeded"
    assert result.failures[0]["stage"] == "after_session_lock_wait"


def test_startup_recovery_deadline_expiry_during_session_file_lock_wait_is_attributed_to_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, _binding, _admitted = _started_operation(tmp_path)
    service = TaskRuntimeService(identity.workspace)
    clock = {"now": 0.0}
    released = False

    class AcquiredSessionLock:
        def acquire(self, *, timeout: float) -> bool:
            assert timeout == pytest.approx(1.0)
            return True

        def release(self) -> None:
            nonlocal released
            released = True

    class DeadlineExpiredFileLock:
        def __enter__(self) -> None:
            clock["now"] = 2.0
            raise TaskBoardFileLockTimeoutError("synthetic session file lock timeout")

        def __exit__(self, *args: object) -> bool:
            del args
            return False

    def timed_out_file_lock(_path: Path, *, timeout_seconds: float | None = None) -> DeadlineExpiredFileLock:
        assert timeout_seconds == pytest.approx(1.0)
        return DeadlineExpiredFileLock()

    monkeypatch.setattr(task_runtime_service_internal.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(service, "_get_session_lock", lambda _task_id: AcquiredSessionLock())
    monkeypatch.setattr(service._board, "_file_lock", timed_out_file_lock)
    result = service._reconcile_directed_effect_recovery_task(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
            deadline_seconds=1.0,
            lock_timeout_seconds=4.0,
        ),
        task_id=identity.task_id,
        task_row={},
        owner_epoch="owner-epoch",
        deadline_monotonic=1.0,
        scanned_operation_count=0,
    )

    assert released is True
    assert result.stop_sweep is True
    assert result.failures[0]["code"] == "recovery_deadline_exceeded"
    assert result.failures[0]["stage"] == "after_session_file_lock_wait"


def test_startup_recovery_deadline_is_rechecked_after_missing_session_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    deadline_reached = False

    def delayed_missing_read(
        _service: TaskRuntimeService,
        _task_id: int,
    ) -> None:
        return None

    def expire_after_read(*, task_id: int, session_id: str) -> None:
        nonlocal deadline_reached
        assert task_id == identity.task_id
        assert session_id == ""
        deadline_reached = True

    def deadline_status(_deadline_monotonic: float) -> bool:
        return deadline_reached

    monkeypatch.setattr(
        TaskRuntimeService,
        "_read_directed_effect_recovery_session_locked",
        delayed_missing_read,
    )
    monkeypatch.setattr(
        TaskRuntimeService,
        "_after_directed_effect_recovery_session_read",
        staticmethod(expire_after_read),
    )
    monkeypatch.setattr(
        TaskRuntimeService,
        "_directed_effect_recovery_deadline_reached",
        staticmethod(deadline_status),
    )
    result = reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
        )
    )

    assert result.ok is False
    assert result.scanned_session_count == 0
    assert result.failures[0]["code"] == "recovery_deadline_exceeded"
    assert result.failures[0]["stage"] == "after_session_read"
    monkeypatch.undo()
    observed = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
        )
    )
    assert observed.state == "EFFECT_STARTED"


def test_startup_recovery_deadline_stops_before_session_read_after_file_lock_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, _binding, _admitted = _started_operation(tmp_path)
    service = TaskRuntimeService(identity.workspace)
    read_called = False
    deadline_reached = False

    def expire_after_file_lock(*, task_id: int) -> None:
        nonlocal deadline_reached
        assert task_id == identity.task_id
        deadline_reached = True

    def deadline_status(_deadline_monotonic: float) -> bool:
        return deadline_reached

    def forbidden_read(
        _task_id: int,
    ) -> None:
        nonlocal read_called
        read_called = True
        return None

    monkeypatch.setattr(service, "_read_directed_effect_recovery_session_locked", forbidden_read)
    monkeypatch.setattr(
        TaskRuntimeService,
        "_after_directed_effect_recovery_session_file_lock_acquired",
        staticmethod(expire_after_file_lock),
    )
    monkeypatch.setattr(
        TaskRuntimeService,
        "_directed_effect_recovery_deadline_reached",
        staticmethod(deadline_status),
    )
    result = service.reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
            factory_run_id="deo-test-run",
        )
    )

    assert read_called is False
    assert result.ok is False
    assert result.failures[0]["code"] == "recovery_deadline_exceeded"
    assert result.failures[0]["stage"] == "before_session_read"


def test_startup_recovery_holds_session_file_lock_against_cross_process_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    context = mp.get_context("fork")
    recovery_read = context.Event()
    release_recovery = context.Event()
    heartbeat_finished = context.Event()
    queue: mp.Queue[dict[str, object]] = context.Queue()

    def pause_after_session_read(*, task_id: int, session_id: str) -> None:
        assert task_id == identity.task_id
        assert session_id == identity.session_id
        recovery_read.set()
        if not release_recovery.wait(timeout=10.0):
            raise RuntimeError("timed out waiting to release recovery session lock")

    def run_recovery() -> None:
        result = reconcile_ambiguous_directed_effects(
            ReconcileAmbiguousDirectedEffectsCommandV1(
                workspace=identity.workspace,
                reason="factory settlement startup recovery",
                factory_run_id="deo-test-run",
                deadline_seconds=8.0,
                lock_timeout_seconds=4.0,
            )
        )
        queue.put(
            {
                "operation": "recovery",
                "ok": result.ok,
                "code": result.code,
                "failure_codes": tuple(failure["code"] for failure in result.failures),
            }
        )

    def run_heartbeat() -> None:
        result = heartbeat_task_runtime_execution_attempt(
            HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                lease_ttl_seconds=120,
                lock_timeout_seconds=4.0,
                context_summary="cross-process recovery fence",
            )
        )
        queue.put(
            {
                "operation": "heartbeat",
                "success": result.success,
                "code": result.code,
                "renewed_identity": (
                    result.renewed_identity.to_record() if result.renewed_identity is not None else None
                ),
            }
        )
        heartbeat_finished.set()

    monkeypatch.setattr(
        TaskRuntimeService,
        "_after_directed_effect_recovery_session_read",
        staticmethod(pause_after_session_read),
    )
    recovery_process = context.Process(target=run_recovery)
    heartbeat_process = context.Process(target=run_heartbeat)
    recovery_process.start()
    assert recovery_read.wait(timeout=8.0)
    heartbeat_process.start()
    assert heartbeat_finished.wait(timeout=0.25) is False

    release_recovery.set()
    recovery_process.join(timeout=12.0)
    heartbeat_process.join(timeout=12.0)
    assert recovery_process.exitcode == 0
    assert heartbeat_process.exitcode == 0
    observed = {item["operation"]: item for item in (queue.get(timeout=3), queue.get(timeout=3))}
    queue.close()
    queue.join_thread()

    assert observed["recovery"] == {
        "operation": "recovery",
        "ok": False,
        "code": "partial_failure",
        "failure_codes": ("recovery_active_session_unexpired",),
    }
    assert observed["heartbeat"]["operation"] == "heartbeat"
    assert observed["heartbeat"]["success"] is True
    renewed_record = observed["heartbeat"]["renewed_identity"]
    assert isinstance(renewed_record, Mapping)
    renewed_identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(renewed_record)
    before_receipt = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=renewed_identity.workspace,
            task_id=renewed_identity.task_id,
            execution_attempt=renewed_identity,
            parent_binding=binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
        )
    )
    assert before_receipt.state == "EFFECT_STARTED"
    committed = commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            workspace=renewed_identity.workspace,
            task_id=renewed_identity.task_id,
            execution_attempt=renewed_identity,
            parent_binding=binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
            expected_version=2,
            expected_seq=3,
            actor="test-receipt-after-live-recovery",
            intended_effect_fingerprint=admitted.intended_effect_fingerprint,
            policy_verdict_hash=admitted.policy_verdict_hash,
            expected_receipt_binding_hash=admitted.expected_receipt_binding_hash,
            receipt_ref="receipt://director/live-effect",
            receipt_hash="a" * 64,
            receipt_binding_hash=admitted.expected_receipt_binding_hash,
            receipt_outcome="succeeded",
        )
    )
    assert committed.code == "receipt_committed"


def test_startup_recovery_fails_before_mutation_when_factory_authority_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    monkeypatch.setattr(TaskRuntimeService, "_session_factory_run_id", staticmethod(lambda *_args: ""))

    result = reconcile_ambiguous_directed_effects(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=identity.workspace,
            reason="factory settlement startup recovery",
        )
    )

    assert result.ok is False
    assert result.failures[0]["code"] == "recovery_factory_run_id_missing"
    observed = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
        )
    )
    assert observed.state == "EFFECT_STARTED"


def test_startup_recovery_uses_one_cross_process_maintenance_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _identity, _binding, _admitted = _started_operation(tmp_path)
    _allow_expired_session_recovery(monkeypatch)
    acquired = threading.Event()
    release = threading.Event()
    first_results: list[DirectedEffectRecoverySweepResultV1] = []

    def pause_after_acquire(*, lease_id: str, owner_epoch: str) -> None:
        assert len(lease_id) == 32
        assert len(owner_epoch) == 32
        acquired.set()
        assert release.wait(timeout=2.0)

    monkeypatch.setattr(
        TaskRuntimeService,
        "_after_directed_effect_recovery_lease_acquired",
        staticmethod(pause_after_acquire),
    )
    command = ReconcileAmbiguousDirectedEffectsCommandV1(
        workspace=str(tmp_path.resolve()),
        reason="factory settlement startup recovery",
        factory_run_id="deo-test-run",
        deadline_seconds=1.5,
        lock_timeout_seconds=0.05,
    )
    worker = threading.Thread(
        target=lambda: first_results.append(reconcile_ambiguous_directed_effects(command)),
        daemon=True,
    )
    worker.start()
    assert acquired.wait(timeout=1.0)

    competing = reconcile_ambiguous_directed_effects(command)
    release.set()
    worker.join(timeout=2.0)

    assert competing.ok is False
    assert competing.failures[0]["code"] == "recovery_lease_lock_timeout"
    assert len(first_results) == 1
    assert first_results[0].ok is True


@pytest.mark.parametrize("outcome", ("completed", "failed", "suspended"))
def test_settlement_closes_receipt_parent_before_terminal_session(tmp_path: Path, outcome: str) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    receipt_outcome = "succeeded" if outcome == "completed" else "failed"
    committed = commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
            expected_version=2,
            expected_seq=3,
            actor="test-receipt",
            intended_effect_fingerprint=admitted.intended_effect_fingerprint,
            policy_verdict_hash=admitted.policy_verdict_hash,
            expected_receipt_binding_hash=admitted.expected_receipt_binding_hash,
            receipt_ref="receipt://director/settlement",
            receipt_hash="9" * 64,
            receipt_binding_hash=admitted.expected_receipt_binding_hash,
            receipt_outcome=receipt_outcome,  # type: ignore[arg-type]
        )
    )
    assert committed.code == "receipt_committed"

    settled = TaskRuntimeService(identity.workspace).settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome=outcome,  # type: ignore[arg-type]
            summary=f"settled-{outcome}",
        )
    )
    operation_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events

    assert settled["success"] is True, json.dumps(settled, ensure_ascii=False, sort_keys=True)
    assert settled["session"]["status"] == outcome
    operation_payload = cast(dict[str, object], operation_events[-1]["payload"])
    assert operation_payload["state"] == "CLOSED_BY_PARENT"
    close_payload = cast(dict[str, object], registry_events[-1]["payload"])
    assert close_payload["schema_version"] == DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2
    assert close_payload["settlement_outcome"] == outcome
    assert (
        close_payload["terminal_intent_hash"]
        == settled["session"]["metadata"]["terminal_settlement_proof"]["terminal_intent_hash"]
    )


def test_completed_settlement_rejects_failed_receipt_without_closing_parent(tmp_path: Path) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    assert (
        commit_directed_effect_receipt(
            CommitDirectedEffectReceiptCommandV1(
                workspace=identity.workspace,
                task_id=identity.task_id,
                execution_attempt=identity,
                parent_binding=binding,
                tool_call_id=admitted.tool_call_id,
                effect_id=admitted.effect_id,
                expected_version=2,
                expected_seq=3,
                actor="test-receipt",
                intended_effect_fingerprint=admitted.intended_effect_fingerprint,
                policy_verdict_hash=admitted.policy_verdict_hash,
                expected_receipt_binding_hash=admitted.expected_receipt_binding_hash,
                receipt_ref="receipt://director/failed",
                receipt_hash="a" * 64,
                receipt_binding_hash=admitted.expected_receipt_binding_hash,
                receipt_outcome="failed",
            )
        ).code
        == "receipt_committed"
    )

    blocked = TaskRuntimeService(identity.workspace).settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="must reject failed effect",
        )
    )
    session = TaskRuntimeService(identity.workspace)._read_session(identity.task_id)

    assert blocked["success"] is False
    assert blocked["code"] == "settlement_effect_outcome_conflict"
    assert session is not None
    assert session.status == "active"


@pytest.mark.parametrize(
    ("operation_state", "outcome", "expected_success", "expected_code"),
    (
        ("ABORTED", "completed", True, "settled"),
        ("ABORTED", "failed", True, "settled"),
        ("ABORTED", "suspended", True, "settled"),
        ("DEAD_LETTER", "completed", False, "settlement_effect_outcome_conflict"),
        ("DEAD_LETTER", "failed", True, "settled"),
        ("DEAD_LETTER", "suspended", True, "settled"),
    ),
)
def test_terminal_operation_settlement_matrix_preserves_fact_order_and_outcome_policy(
    tmp_path: Path,
    operation_state: Literal["ABORTED", "DEAD_LETTER"],
    outcome: Literal["completed", "failed", "suspended"],
    expected_success: bool,
    expected_code: str,
) -> None:
    setup = _aborted_operation if operation_state == "ABORTED" else _dead_lettered_operation
    identity, binding, _admitted = setup(tmp_path / f"{operation_state.lower()}-{outcome}")
    service = TaskRuntimeService(identity.workspace)
    execution_facts_before = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream="task_runtime.execution",
        )
    ).events

    settled = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome=outcome,
            summary=f"terminal-matrix-{operation_state.lower()}-{outcome}",
        )
    )
    operation_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events
    execution_facts_after = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream="task_runtime.execution",
        )
    ).events
    session = service._read_session(identity.task_id)
    parent_closes = tuple(event for event in registry_events if event["event_type"] == _PARENT_CLOSED_EVENT_TYPE)
    operation_states = tuple(cast(dict[str, object], event["payload"]).get("state") for event in operation_events)

    assert settled["success"] is expected_success
    assert settled["code"] == expected_code
    assert tuple(event["seq"] for event in operation_events) == tuple(range(1, len(operation_events) + 1))
    assert operation_states.count(operation_state) == 1
    assert "CLOSED_BY_PARENT" not in operation_states
    assert session is not None

    if not expected_success:
        assert session.status == "active"
        assert "pending_terminal_intent" not in session.metadata
        assert len(parent_closes) == 0
        assert execution_facts_after == execution_facts_before
        return

    assert session.status == outcome
    assert len(parent_closes) == 1
    close_payload = cast(dict[str, object], parent_closes[0]["payload"])
    assert close_payload["settlement_outcome"] == outcome
    assert close_payload["operation_source_head_seq"] == operation_events[-1]["seq"]
    pending_intent = cast(dict[str, object], session.metadata["pending_terminal_intent"])
    settlement_proof = cast(dict[str, object], session.metadata["terminal_settlement_proof"])
    assert pending_intent["outcome"] == outcome
    assert settlement_proof["settlement_outcome"] == outcome
    assert settlement_proof["terminal_intent_hash"] == pending_intent["terminal_intent_hash"]
    assert len(execution_facts_after) == len(execution_facts_before) + 1
    terminal_fact = cast(dict[str, object], execution_facts_after[-1]["payload"])
    assert terminal_fact["task_id"] == str(identity.task_id)
    assert terminal_fact["event_type"] == outcome
    assert terminal_fact["session_id"] == identity.session_id


@pytest.mark.parametrize("crash_stage", ("intent", "child", "parent", "terminal"))
def test_settlement_replays_each_durable_crash_boundary_without_duplicate_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path / crash_stage)
    committed = commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
            expected_version=2,
            expected_seq=3,
            actor="test-crash-receipt",
            intended_effect_fingerprint=admitted.intended_effect_fingerprint,
            policy_verdict_hash=admitted.policy_verdict_hash,
            expected_receipt_binding_hash=admitted.expected_receipt_binding_hash,
            receipt_ref="receipt://director/crash-boundary",
            receipt_hash="b" * 64,
            receipt_binding_hash=admitted.expected_receipt_binding_hash,
            receipt_outcome="succeeded",
        )
    )
    assert committed.code == "receipt_committed"
    service = TaskRuntimeService(identity.workspace)
    command = SettleTaskRuntimeExecutionAttemptCommandV1(
        workspace=identity.workspace,
        identity=identity,
        outcome="completed",
        summary="crash-boundary-replay",
    )

    def crash(*args: object) -> None:
        del args
        raise RuntimeError(f"synthetic_{crash_stage}_crash")

    if crash_stage == "intent":
        monkeypatch.setattr(service, "_after_terminal_intent_write", crash)
    elif crash_stage == "child":
        monkeypatch.setattr(
            deo_internal.DirectedEffectOperationRepository,
            "_after_settlement_child_close",
            staticmethod(crash),
        )
    elif crash_stage == "parent":
        monkeypatch.setattr(
            deo_internal.DirectedEffectOperationRepository,
            "_after_settlement_parent_close",
            staticmethod(crash),
        )
    else:
        monkeypatch.setattr(service, "_after_terminal_session_write", crash)

    with pytest.raises(RuntimeError, match=f"synthetic_{crash_stage}_crash"):
        service.settle_execution_attempt(command)

    if crash_stage == "intent":
        monkeypatch.setattr(service, "_after_terminal_intent_write", lambda *_args: None)
    elif crash_stage == "child":
        monkeypatch.setattr(
            deo_internal.DirectedEffectOperationRepository,
            "_after_settlement_child_close",
            staticmethod(lambda *_args: None),
        )
    elif crash_stage == "parent":
        monkeypatch.setattr(
            deo_internal.DirectedEffectOperationRepository,
            "_after_settlement_parent_close",
            staticmethod(lambda *_args: None),
        )
    else:
        monkeypatch.setattr(service, "_after_terminal_session_write", lambda *_args: None)

    after_crash = service._read_session(identity.task_id)
    assert after_crash is not None
    assert after_crash.status == ("completed" if crash_stage == "terminal" else "active")
    assert after_crash.metadata["pending_terminal_intent"]["outcome"] == "completed"

    replayed = service.settle_execution_attempt(command)
    operation_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events
    terminal = service._read_session(identity.task_id)
    child_close_count = sum(
        1 for event in operation_events if cast(dict[str, object], event["payload"]).get("state") == "CLOSED_BY_PARENT"
    )
    receipt_count = sum(
        1 for event in operation_events if cast(dict[str, object], event["payload"]).get("state") == "RECEIPT_COMMITTED"
    )
    parent_close_count = sum(1 for event in registry_events if event["event_type"] == _PARENT_CLOSED_EVENT_TYPE)

    assert replayed["success"] is True
    assert terminal is not None
    assert terminal.status == "completed"
    assert receipt_count == 1
    assert child_close_count == 1
    assert parent_close_count == 1


_BINDING_MISMATCH_CASES = (
    ("schema_version", "parent_binding_conflict"),
    ("registry_identity.schema_version", "execution_attempt_mismatch"),
    ("registry_identity.workspace", "workspace_mismatch"),
    ("registry_identity.task_id", "task_mismatch"),
    ("registry_identity.external_task_id", "execution_attempt_mismatch"),
    ("registry_identity.session_id", "execution_attempt_mismatch"),
    ("registry_identity.attempt", "execution_attempt_mismatch"),
    ("registry_identity.role_id", "execution_attempt_mismatch"),
    ("registry_identity.worker_id", "execution_attempt_mismatch"),
    ("registry_identity.run_id", "execution_attempt_mismatch"),
    ("registry_stream_token", "parent_binding_conflict"),
    ("registry_version", "parent_binding_version_conflict"),
    ("parent_sequence", "parent_binding_version_conflict"),
    ("binding_id", "parent_binding_not_found"),
    ("operation_stream_token", "parent_binding_conflict"),
    ("binding_hash", "parent_binding_hash_mismatch"),
    ("admission_idempotency_key", "parent_admission_idempotency_conflict"),
    ("correlation.schema_version", "parent_binding_conflict"),
    ("correlation.turn_id", "turn_mismatch"),
    ("correlation.batch_id", "batch_mismatch"),
    ("actor", "parent_binding_conflict"),
    ("source_event_id", "parent_binding_event_conflict"),
    ("source_event_seq", "parent_binding_version_conflict"),
)


def _forge_binding_field(
    binding: DirectedEffectParentBindingV1,
    field_path: str,
) -> DirectedEffectParentBindingV1:
    forged = replace(binding)
    owner: object = forged
    field_name = field_path
    if field_path.startswith("registry_identity."):
        owner = replace(binding.registry_identity)
        field_name = field_path.removeprefix("registry_identity.")
        object.__setattr__(forged, "registry_identity", owner)
    elif field_path.startswith("correlation."):
        owner = replace(binding.correlation)
        field_name = field_path.removeprefix("correlation.")
        object.__setattr__(forged, "correlation", owner)
    current = getattr(owner, field_name)
    forged_value = current + 1 if isinstance(current, int) else f"{current}-forged"
    object.__setattr__(owner, field_name, forged_value)
    return forged


@pytest.mark.parametrize(("field_path", "expected_code"), _BINDING_MISMATCH_CASES)
def test_operation_enrollment_rejects_complete_binding_mismatch_before_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_path: str,
    expected_code: str,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    maintenance_calls: list[object] = []

    def observe_operation_enrollment(command: object) -> None:
        maintenance_calls.append(command)
        raise AssertionError("binding mismatch reached the operation enrollment port")

    monkeypatch.setattr(
        deo_internal,
        "enroll_fact_stream_streams",
        observe_operation_enrollment,
    )
    forged = _forge_binding_field(binding, field_path)
    rejected = enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(execution_attempt=identity, parent_binding=forged)
    )
    assert rejected.ok is False
    assert rejected.code == expected_code
    assert rejected.receipt is None
    assert maintenance_calls == []


def test_v2_writer_and_historical_v1_exact_replay_are_schema_neutral(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    second = _operation_command(
        identity,
        binding,
        tool_call_id="tool-v1",
        effect_id="effect-v1",
        expected_seq=2,
    )
    command, second = _seal_operation_commands(identity, binding, command, second)
    written = admit_directed_effect_operation(command)
    assert written.code == "admitted"
    assert written.evidence["authoritative_append"] is False
    assert written.evidence["authoritative_effect_receipt"] is True
    assert written.evidence["append_disposition"] == "committed_or_exact_replay"
    assert written.snapshot is not None
    assert written.snapshot.state == "INTENT_COMMITTED"
    assert written.snapshot.version == 1
    payload = query_fact_events(
        QueryFactEventsV1(workspace=identity.workspace, stream=binding.operation_stream_token, strict_integrity=True)
    ).events[0]["payload"]
    assert payload["schema_version"] == DIRECTED_EFFECT_OPERATION_SCHEMA_V2
    assert "recorded_at" not in payload
    assert (
        admit_directed_effect_operation(replace(command, expected_version=99, expected_seq=99)).code
        == "idempotent_replay"
    )

    operation = deo_internal.DirectedEffectOperationRepository._derive_operation(second, binding)
    descriptor = deo_internal.DirectedEffectOperationRepository._operation_descriptor(second, kind="admit")
    historical = deo_internal.DirectedEffectOperationRepository._operation_event_canonical(
        operation=operation,
        state="INTENT_COMMITTED",
        previous_version=0,
        descriptor=descriptor,
    )
    historical["schema_version"] = DIRECTED_EFFECT_OPERATION_SCHEMA_V1
    historical["recorded_at"] = "2026-07-15T00:00:00+00:00"
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            event_type="task_runtime.directed_effect_operation.v1.intent_committed",
            payload=historical,
            source="test",
            idempotency_key="historical-v1",
            expected_seq=2,
            durability="fsync",
            strict_integrity=True,
        )
    )
    replay = admit_directed_effect_operation(second)
    assert replay.code == "idempotent_replay"
    assert replay.evidence["committed_seq"] == 2
    assert replay.evidence["authoritative_effect_receipt"] is False


def test_semantic_conflict_and_replay_after_parent_close(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    new_command = _operation_command(identity, binding, tool_call_id="new-tool", effect_id="new-effect")
    command, new_command = _seal_operation_commands(identity, binding, command, new_command)
    assert admit_directed_effect_operation(command).code == "admitted"
    assert (
        admit_directed_effect_operation(replace(command, intended_effect_fingerprint="changed")).code
        == "inventory_member_conflict"
    )
    _close_parent(binding)
    assert admit_directed_effect_operation(command).code == "idempotent_replay"
    assert admit_directed_effect_operation(new_command).code == "parent_closed"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("unknown_schema", "strict_stream_unknown_schema"),
        ("missing_field", "strict_stream_corruption"),
        ("extra_field", "strict_stream_corruption"),
        ("v1_naive_timestamp", "strict_stream_corruption"),
    ],
)
def test_operation_parser_fails_closed_for_exact_v1_v2_shapes(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    workspace = tmp_path / mutation
    workspace.mkdir()
    identity = _attempt(workspace)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    operation = deo_internal.DirectedEffectOperationRepository._derive_operation(command, binding)
    descriptor = deo_internal.DirectedEffectOperationRepository._operation_descriptor(command, kind="admit")
    payload = deo_internal.DirectedEffectOperationRepository._operation_event_canonical(
        operation=operation,
        state="INTENT_COMMITTED",
        previous_version=0,
        descriptor=descriptor,
    )
    if mutation == "unknown_schema":
        payload["schema_version"] = "task-runtime.directed-effect-operation/99"
    elif mutation == "missing_field":
        del payload["state"]
    elif mutation == "extra_field":
        payload["recorded_at"] = "2026-07-15T00:00:00+00:00"
    else:
        payload["schema_version"] = DIRECTED_EFFECT_OPERATION_SCHEMA_V1
        payload["recorded_at"] = "2026-07-15T00:00:00"
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            event_type="task_runtime.directed_effect_operation.v1.intent_committed",
            payload=payload,
            source="test",
            idempotency_key=f"invalid-{mutation}",
            expected_seq=1,
            durability="fsync",
            strict_integrity=True,
        )
    )
    result = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=command.tool_call_id,
            effect_id=command.effect_id,
        )
    )
    assert result.code == expected_code


def test_targeted_reducer_preserves_precise_identity_mismatch_after_prior_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    command = _operation_command(identity, binding)
    repository = deo_internal.DirectedEffectOperationRepository()
    target = repository._derive_operation(command, binding)
    forged = replace(target, workspace=f"{target.workspace}-forged")
    admit_descriptor = repository._operation_descriptor(command, kind="admit")
    claim_descriptor = repository._operation_descriptor(_claim_command(identity, binding), kind="claim")
    transitions = {
        1: deo_internal._CommittedTransition(
            operation=target,
            state="INTENT_COMMITTED",
            previous_version=0,
            version=1,
            descriptor=admit_descriptor,
            normalized=repository._normalized_transition(
                operation=target,
                state="INTENT_COMMITTED",
                descriptor=admit_descriptor,
            ),
            canonical_event={},
            event_id="event-1",
            seq=1,
        ),
        2: deo_internal._CommittedTransition(
            operation=forged,
            state="EFFECT_STARTED",
            previous_version=1,
            version=2,
            descriptor=claim_descriptor,
            normalized=repository._normalized_transition(
                operation=forged,
                state="EFFECT_STARTED",
                descriptor=claim_descriptor,
            ),
            canonical_event={},
            event_id="event-2",
            seq=2,
        ),
    }

    def parse_transition(
        record: Mapping[str, object],
        observed_binding: DirectedEffectParentBindingV1,
    ) -> deo_internal._CommittedTransition:
        assert observed_binding == binding
        return transitions[cast(int, record["seq"])]

    monkeypatch.setattr(repository, "_parse_operation_transition", parse_transition)
    result = repository._reduce_operation(
        deo_internal._StreamRead(events=({"seq": 1}, {"seq": 2}), head_seq=2),
        target,
        binding,
    )

    assert isinstance(result, DirectedEffectOperationResultV1)
    assert result.code == "workspace_mismatch"


def test_snapshot_projection_is_in_memory_only(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    (command,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))
    admitted = admit_directed_effect_operation(command)
    assert admitted.snapshot is not None
    assert admitted.snapshot.state == "INTENT_COMMITTED"
    query = GetDirectedEffectOperationQueryV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=command.tool_call_id,
        effect_id=command.effect_id,
    )
    assert get_directed_effect_operation(query).snapshot is not None
    runtime_root = Path(resolve_storage_roots(identity.workspace).runtime_root)
    assert not (runtime_root / "task_runtime" / "directed_effect_operation_v1").exists()


def test_non_drift_error_after_real_commit_reconciles_strict_durable_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    (command,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))

    def fail_after_real_commit(receipt: object) -> None:
        del receipt
        heartbeat = heartbeat_task_runtime_execution_attempt(
            HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                lease_ttl_seconds=120,
                context_summary="invalidate original identity after durable append",
                lock_timeout_seconds=5.0,
            )
        )
        assert heartbeat.success is True
        raise FactStreamError(
            "simulated acknowledgement loss after fsync",
            code="append_write_failed",
            details={"boundary": "after_fsync"},
        )

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(fail_after_real_commit),
    )
    result = admit_directed_effect_operation(command)

    assert result.code == "admitted"
    assert result.evidence["reconciled_after_guarded_error"] is True
    assert result.evidence["fact_stream_code"] == "append_write_failed"
    assert result.evidence["authoritative_append"] is False
    assert result.evidence["authoritative_effect_receipt"] is True
    assert result.evidence["append_disposition"] == "committed_or_exact_replay"
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(events) == 1
    assert result.evidence["event_id"] == events[0]["event_id"]
    assert result.evidence["appended_seq"] == events[0]["seq"]


def test_non_drift_error_without_durable_event_returns_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    (command,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))

    def fail_before_commit(snapshot: object) -> None:
        del snapshot
        raise FactStreamError(
            "simulated failure before guarded append",
            code="append_write_failed",
            details={"boundary": "before_append"},
        )

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_prepare",
        staticmethod(fail_before_commit),
    )
    result = admit_directed_effect_operation(command)

    assert result.code == "stream_append_failed"
    assert result.evidence["reconciled_after_guarded_error"] is True
    assert result.evidence["fact_stream_code"] == "append_write_failed"
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert events == ()


def test_guarded_confirmation_fails_closed_on_receipt_or_semantic_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    (command,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))
    captured: list[GuardedFactAppendedV1] = []

    def capture_receipt(receipt: GuardedFactAppendedV1) -> None:
        captured.append(receipt)

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(capture_receipt),
    )
    assert admit_directed_effect_operation(command).code == "admitted"
    assert len(captured) == 1

    repository = deo_internal.DirectedEffectOperationRepository()
    operation = repository._derive_operation(command, binding)
    descriptor = repository._operation_descriptor(command, kind="admit")
    canonical = repository._operation_event_canonical(
        operation=operation,
        state="INTENT_COMMITTED",
        previous_version=0,
        descriptor=descriptor,
    )
    normalized = repository._normalized_transition(
        operation=operation,
        state="INTENT_COMMITTED",
        descriptor=descriptor,
    )
    prepared = repository._prepare_guarded_snapshot(command, binding.registry_identity)
    assert isinstance(prepared, GuardedFactSnapshotV1)
    idempotency_key = deo_internal._hash_token(normalized.to_record())
    guarded_command = AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=prepared.proof,
        event=GuardedFactEventV1(
            event_type="task_runtime.directed_effect_operation.v1.intent_committed",
            source="runtime.task_runtime",
            payload=canonical,
            aggregate_id=str(command.task_id),
            correlation_id=idempotency_key,
        ),
        idempotency_key=idempotency_key,
    )
    receipt_mismatch = repository._confirm_guarded_append(
        command=command,
        operation=operation,
        kind="admit",
        target="INTENT_COMMITTED",
        canonical_event=canonical,
        normalized=normalized,
        expected_previous_version=0,
        guarded_attempt=1,
        receipt=replace(captured[0], event_id="forged-event-id"),
        guarded_command=guarded_command,
    )
    semantic_mismatch = repository._confirm_guarded_append(
        command=command,
        operation=operation,
        kind="admit",
        target="INTENT_COMMITTED",
        canonical_event={**canonical, "parent_binding_id": "forged-binding"},
        normalized=normalized,
        expected_previous_version=0,
        guarded_attempt=1,
        receipt=captured[0],
        guarded_command=guarded_command,
    )

    assert receipt_mismatch.code == "guarded_receipt_mismatch"
    assert receipt_mismatch.evidence["reason"] == "receipt_identity_mismatch"
    assert semantic_mismatch.code == "guarded_receipt_mismatch"
    assert semantic_mismatch.evidence["reason"] == "canonical_transition_not_unique"


@pytest.mark.parametrize(
    "tampered_field",
    (
        "event_id",
        "workspace",
        "stream",
        "storage_path",
        "appended_at",
        "appended_seq",
        "semantic_digest",
    ),
)
def test_public_guarded_confirmation_rejects_each_tampered_receipt_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered_field: str,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    (command,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))

    def tamper(receipt: GuardedFactAppendedV1) -> GuardedFactAppendedV1:
        if tampered_field == "event_id":
            return replace(receipt, event_id="forged-event-id")
        if tampered_field == "workspace":
            return replace(receipt, workspace=str((tmp_path / "forged-workspace").resolve()))
        if tampered_field == "stream":
            return replace(receipt, stream="forged-stream")
        if tampered_field == "storage_path":
            return replace(receipt, storage_path="/forged/storage/path.jsonl")
        if tampered_field == "appended_at":
            return replace(receipt, appended_at="2099-01-01T00:00:00+00:00")
        if tampered_field == "appended_seq":
            return replace(receipt, appended_seq=receipt.appended_seq + 1)
        if tampered_field == "semantic_digest":
            return replace(receipt, semantic_digest="b" * 64)
        raise AssertionError(f"unsupported tampered field: {tampered_field}")

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(tamper),
    )
    result = admit_directed_effect_operation(command)

    assert result.code == "guarded_receipt_mismatch"
    assert result.evidence["reason"] == "receipt_identity_mismatch"
    assert result.evidence["receipt_drift_fields"] == (tampered_field,)
    assert "authoritative_effect_receipt" not in result.evidence
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(events) == 1


def test_parent_readiness_observes_empty_enrolled_stream_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    service = TaskRuntimeService(identity.workspace)
    session_before = service._read_session(identity.task_id)
    assert session_before is not None
    before_registry = query_fact_events(
        QueryFactEventsV1(workspace=identity.workspace, stream=binding.registry_stream_token, strict_integrity=True)
    ).events
    before_operations = query_fact_events(
        QueryFactEventsV1(workspace=identity.workspace, stream=binding.operation_stream_token, strict_integrity=True)
    ).events
    workspace_root = Path(identity.workspace)
    runtime_root = Path(resolve_storage_roots(identity.workspace).runtime_root)
    workspace_files_before = _file_bytes_snapshot(workspace_root)
    runtime_files_before = _file_bytes_snapshot(runtime_root)
    receipts_before = {path: content for path, content in runtime_files_before.items() if "receipt" in path.lower()}
    projections_before = {
        path: content for path, content in runtime_files_before.items() if "projection" in path.lower()
    }
    mutation_calls: list[str] = []

    def forbidden_mutation(*args: object, **kwargs: object) -> None:
        del args, kwargs
        mutation_calls.append("called")
        raise AssertionError("readiness observation reached a mutation port")

    monkeypatch.setattr(deo_internal, "append_fact_event", forbidden_mutation)
    monkeypatch.setattr(deo_internal, "append_if_guarded_snapshot", forbidden_mutation)
    monkeypatch.setattr(deo_internal, "enroll_fact_stream_streams", forbidden_mutation)
    monkeypatch.setattr(TaskRuntimeService, "_write_session", forbidden_mutation)
    monkeypatch.setattr(TaskRuntimeService, "_write_session_locked", forbidden_mutation)
    monkeypatch.setattr(TaskRuntimeService, "_record_session_write_receipt", forbidden_mutation)

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is True
    assert result.code == "readiness_observed"
    assert result.projection is not None
    assert result.projection.enforcement == "not_enabled"
    assert result.projection.operation_count == 0
    assert result.projection.operation_source_head_seq == 0
    assert tuple((item.state, item.count) for item in result.projection.state_counts) == (
        ("INTENT_COMMITTED", 0),
        ("EFFECT_STARTED", 0),
        ("RECOVERY_PENDING", 0),
        ("RECEIPT_COMMITTED", 0),
        ("CLOSED_BY_PARENT", 0),
        ("ABORTED", 0),
        ("DEAD_LETTER", 0),
    )
    assert mutation_calls == []
    assert (
        query_fact_events(
            QueryFactEventsV1(workspace=identity.workspace, stream=binding.registry_stream_token, strict_integrity=True)
        ).events
        == before_registry
    )
    assert (
        query_fact_events(
            QueryFactEventsV1(
                workspace=identity.workspace, stream=binding.operation_stream_token, strict_integrity=True
            )
        ).events
        == before_operations
    )
    session_after = service._read_session(identity.task_id)
    assert session_after is not None
    assert session_after.to_dict() == session_before.to_dict()
    workspace_files_after = _file_bytes_snapshot(workspace_root)
    runtime_files_after = _file_bytes_snapshot(runtime_root)
    assert workspace_files_after == workspace_files_before
    assert runtime_files_after == runtime_files_before
    assert {path: content for path, content in runtime_files_after.items() if "receipt" in path.lower()} == (
        receipts_before
    )
    assert {path: content for path, content in runtime_files_after.items() if "projection" in path.lower()} == (
        projections_before
    )
    assert not (runtime_root / "task_runtime" / "directed_effect_operation_v1").exists()


def test_parent_readiness_rejects_wrong_query_type() -> None:
    wrong_query = cast(GetDirectedEffectParentReadinessQueryV1, object())

    with pytest.raises(TypeError, match="query must be GetDirectedEffectParentReadinessQueryV1"):
        get_directed_effect_parent_readiness(wrong_query)


def test_parent_readiness_reduces_multiple_operations_with_fixed_state_counts(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    first = _operation_command(identity, binding)
    second = _operation_command(
        identity,
        binding,
        tool_call_id="tool-2",
        effect_id="effect-2",
        expected_seq=2,
    )
    first, second = _seal_operation_commands(identity, binding, first, second)
    assert admit_directed_effect_operation(first).code == "admitted"
    assert admit_directed_effect_operation(second).code == "admitted"
    _finalize_operation_inventory(identity, binding)
    assert (
        claim_directed_effect(
            ClaimDirectedEffectCommandV1(
                workspace=identity.workspace,
                task_id=identity.task_id,
                execution_attempt=identity,
                parent_binding=binding,
                tool_call_id=second.tool_call_id,
                effect_id=second.effect_id,
                expected_version=1,
                expected_seq=3,
                actor="test-child",
                intended_effect_fingerprint=second.intended_effect_fingerprint,
                policy_verdict_hash=second.policy_verdict_hash,
                expected_receipt_binding_hash=second.expected_receipt_binding_hash,
            )
        ).code
        == "effect_claimed"
    )

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is True
    assert result.projection is not None
    assert result.projection.operation_count == 2
    assert result.projection.operation_source_head_seq == 3
    assert {item.state: item.count for item in result.projection.state_counts} == {
        "INTENT_COMMITTED": 1,
        "EFFECT_STARTED": 1,
        "RECOVERY_PENDING": 0,
        "RECEIPT_COMMITTED": 0,
        "CLOSED_BY_PARENT": 0,
        "ABORTED": 0,
        "DEAD_LETTER": 0,
    }


def test_parent_readiness_observes_closed_historical_parent_while_attempt_remains_active(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    _close_parent(binding)
    service = TaskRuntimeService(identity.workspace)
    session_before = service._read_session(identity.task_id)
    assert session_before is not None
    assert session_before.status == "active"

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is True
    assert result.code == "readiness_observed"
    assert result.projection is not None
    assert result.projection.enforcement == "not_enabled"
    assert result.projection.parent_binding_id == binding.binding_id
    assert result.projection.parent_registry_source_head_seq == 2
    assert result.projection.operation_count == 0
    session_after = service._read_session(identity.task_id)
    assert session_after is not None
    assert session_after.status == "active"
    assert session_after.to_dict() == session_before.to_dict()


@pytest.mark.parametrize(
    ("query_factory", "expected_code"),
    (
        (
            lambda identity, binding: _readiness_query(identity, replace(binding, binding_id="missing-binding")),
            "parent_binding_not_found",
        ),
        (
            lambda identity, binding: _readiness_query(
                identity, replace(binding, operation_stream_token="mismatched-stream")
            ),
            "parent_binding_conflict",
        ),
    ),
)
def test_parent_readiness_fails_closed_for_stale_or_invalid_bindings(
    tmp_path: Path,
    query_factory: Callable[
        [TaskRuntimeExecutionAttemptIdentityV1, DirectedEffectParentBindingV1],
        GetDirectedEffectParentReadinessQueryV1,
    ],
    expected_code: str,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)

    result = get_directed_effect_parent_readiness(query_factory(identity, binding))

    assert result.ok is False
    assert result.code == expected_code
    assert result.projection is None


def test_parent_readiness_fails_closed_for_stale_attempt(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    renewed = heartbeat_task_runtime_execution_attempt(
        HeartbeatTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            lease_ttl_seconds=120,
            context_summary="make readiness query identity stale",
            lock_timeout_seconds=5.0,
        )
    )
    assert renewed.success is True

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "lease_version_mismatch"
    assert result.projection is None


def test_parent_readiness_fails_closed_when_operation_stream_is_unenrolled(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "stream_lock_missing"
    assert result.projection is None


def test_parent_readiness_fails_closed_for_persisted_illegal_transition(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    event_id = _append_operation_fact(
        _claim_command(identity, binding, expected_version=0, expected_seq=1),
        binding,
        kind="claim",
        state="EFFECT_STARTED",
        previous_version=0,
        idempotency_key="illegal-initial-claim",
    )

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "strict_stream_corruption"
    assert result.evidence == {
        "reason": "illegal_persisted_transition",
        "event_id": event_id,
    }
    assert result.projection is None


def test_parent_readiness_fails_closed_for_persisted_version_discontinuity(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = replace(_operation_command(identity, binding), expected_version=1)
    event_id = _append_operation_fact(
        command,
        binding,
        kind="admit",
        state="INTENT_COMMITTED",
        previous_version=1,
        idempotency_key="non-monotonic-initial-version",
    )

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "strict_stream_corruption"
    assert result.evidence == {
        "reason": "non_monotonic_operation_version",
        "event_id": event_id,
    }
    assert result.projection is None


def test_parent_readiness_fails_closed_for_persisted_semantic_drift(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    admission = _operation_command(identity, binding)
    _append_operation_fact(
        admission,
        binding,
        kind="admit",
        state="INTENT_COMMITTED",
        previous_version=0,
        idempotency_key="semantic-baseline",
    )
    drifted_claim = _claim_command(identity, binding, fingerprint="fingerprint-drifted")
    drift_event_id = _append_operation_fact(
        drifted_claim,
        binding,
        kind="claim",
        state="EFFECT_STARTED",
        previous_version=1,
        idempotency_key="semantic-drift",
    )

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "deo_semantic_drift"
    assert result.evidence == {
        "event_id": drift_event_id,
        "observed": ("fingerprint-drifted", "policy-1", "receipt-1"),
        "expected": ("fingerprint-1", "policy-1", "receipt-1"),
    }
    assert result.projection is None


def test_parent_readiness_fails_closed_for_corrupt_or_ambiguous_operation_facts(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    (first,) = _seal_operation_commands(identity, binding, _operation_command(identity, binding))
    assert admit_directed_effect_operation(first).code == "admitted"
    first_operation = deo_internal.DirectedEffectOperationRepository._derive_operation(first, binding)
    claim = ClaimDirectedEffectCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=first.tool_call_id,
        effect_id=first.effect_id,
        expected_version=1,
        expected_seq=2,
        actor="test-child",
        intended_effect_fingerprint=first.intended_effect_fingerprint,
        policy_verdict_hash=first.policy_verdict_hash,
        expected_receipt_binding_hash=first.expected_receipt_binding_hash,
    )
    forged_operation = replace(first_operation, tool_call_id="other-tool", effect_id="other-effect")
    expected_operation = replace(
        forged_operation,
        operation_id=deo_internal._operation_id(
            binding_id=binding.binding_id,
            tool_call_id=forged_operation.tool_call_id,
            effect_id=forged_operation.effect_id,
        ),
    )
    descriptor = deo_internal.DirectedEffectOperationRepository._operation_descriptor(claim, kind="claim")
    payload = deo_internal.DirectedEffectOperationRepository._operation_event_canonical(
        operation=forged_operation,
        state="EFFECT_STARTED",
        previous_version=1,
        descriptor=descriptor,
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            event_type="task_runtime.directed_effect_operation.v1.effect_started",
            payload=payload,
            source="test",
            idempotency_key="ambiguous-operation",
            expected_seq=2,
            durability="fsync",
            strict_integrity=True,
        )
    )

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "operation_identity_conflict"
    assert result.evidence == {
        "reason": "persisted_operation_identity_not_canonical",
        "expected_operation": expected_operation.to_record(),
        "observed_operation": forged_operation.to_record(),
    }
    assert result.projection is None


def test_parent_readiness_contract_is_immutable_and_has_no_authority_fields() -> None:
    values = ["original"]
    evidence: dict[str, object] = {"nested": {"values": values}}
    result = DirectedEffectParentReadinessResultV1(
        ok=False,
        code="session_not_active",
        evidence=evidence,
    )
    values.append("mutated")

    assert result.evidence == {"nested": {"values": ("original",)}}
    with pytest.raises(TypeError):
        operator.setitem(result.evidence, "new", True)
    nested = result.evidence["nested"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        operator.setitem(nested, "new", True)
    nested_values = nested["values"]
    assert isinstance(nested_values, tuple)
    with pytest.raises(TypeError):
        operator.setitem(nested_values, 0, "mutated")
    with pytest.raises(FrozenInstanceError):
        result.ok = True  # type: ignore[misc]
    forbidden = ("ready", "eligible", "authorized", "receipt", "close", "terminal")
    for contract in (
        DirectedEffectParentReadinessProjectionV1,
        DirectedEffectParentReadinessResultV1,
        DirectedEffectParentReadinessStateCountV1,
    ):
        assert not any(token in field.name.lower() for field in fields(contract) for token in forbidden)


def test_parent_readiness_evidence_rejects_cycles_with_stable_boundary_error() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    custom_cyclic: UserDict[str, object] = UserDict()
    custom_cyclic["self"] = custom_cyclic

    for evidence in (cyclic, custom_cyclic):
        with pytest.raises(ValueError, match="readiness evidence must not contain cycles"):
            DirectedEffectParentReadinessResultV1(
                ok=False,
                code="strict_stream_corruption",
                evidence=evidence,
            )

    frozen_set = DirectedEffectParentReadinessResultV1(
        ok=False,
        code="strict_stream_corruption",
        evidence={"diagnostic_labels": {"registry", "stream"}},
    )
    assert frozen_set.evidence["diagnostic_labels"] == frozenset({"registry", "stream"})


def test_parent_readiness_failure_preserves_nested_diagnostic_evidence() -> None:
    result = DirectedEffectParentReadinessResultV1(
        ok=False,
        code="strict_stream_corruption",
        evidence={
            "receipt_error": {
                "terminal_reason": "strict stream diagnostic only",
                "details": ["torn-tail", "no-projection"],
            }
        },
    )

    assert result.ok is False
    assert result.projection is None
    assert result.evidence == {
        "receipt_error": {
            "terminal_reason": "strict stream diagnostic only",
            "details": ("torn-tail", "no-projection"),
        }
    }


def test_parent_readiness_success_rejects_non_diagnostic_evidence_schema(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    observed = get_directed_effect_parent_readiness(_readiness_query(identity, binding))
    assert observed.projection is not None
    assert set(observed.evidence) == {
        "parent_registry_source_head_seq",
        "operation_source_head_seq",
    }

    forbidden_success_keys = (
        "readiness_verdict",
        "permission_granted",
        "authority_granted",
        "authorization_status",
        "authoritative_verdict",
        "settle_allowed",
        "settling_status",
        "settlement_status",
    )
    for forbidden_key in forbidden_success_keys:
        with pytest.raises(ValueError, match="successful readiness evidence must match diagnostic schema"):
            DirectedEffectParentReadinessResultV1(
                ok=True,
                code="readiness_observed",
                projection=observed.projection,
                evidence={forbidden_key: True},
            )


def test_parent_readiness_maps_corrupt_operation_stream_without_projection(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    operation = deo_internal.DirectedEffectOperationRepository._derive_operation(command, binding)
    descriptor = deo_internal.DirectedEffectOperationRepository._operation_descriptor(command, kind="admit")
    payload = deo_internal.DirectedEffectOperationRepository._operation_event_canonical(
        operation=operation,
        state="INTENT_COMMITTED",
        previous_version=0,
        descriptor=descriptor,
    )
    payload["schema_version"] = "task-runtime.directed-effect-operation/invalid"
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            event_type="task_runtime.directed_effect_operation.v1.intent_committed",
            payload=payload,
            source="test",
            idempotency_key="corrupt-readiness-operation",
            expected_seq=1,
            durability="fsync",
            strict_integrity=True,
        )
    )

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "strict_stream_unknown_schema"
    assert result.evidence == {"observed_schema_version": "task-runtime.directed-effect-operation/invalid"}
    assert result.projection is None


def test_parent_readiness_propagates_unknown_storage_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)

    def unexpected_storage_failure(query: QueryFactEventsV1) -> FactStreamQueryResultV1:
        del query
        raise RuntimeError("unexpected storage failure")

    monkeypatch.setattr(deo_internal, "query_fact_events", unexpected_storage_failure)

    with pytest.raises(RuntimeError, match="unexpected storage failure"):
        get_directed_effect_parent_readiness(_readiness_query(identity, binding))


def test_parent_readiness_fails_closed_for_paginated_head_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    _append_operation_fact(
        _operation_command(identity, binding),
        binding,
        kind="admit",
        state="INTENT_COMMITTED",
        previous_version=0,
        idempotency_key="pagination-baseline",
    )
    real_query = deo_internal.query_fact_events

    def ambiguous_page(query: QueryFactEventsV1) -> FactStreamQueryResultV1:
        observed = real_query(query)
        if query.stream == binding.operation_stream_token:
            return replace(observed, total=observed.total + 1)
        return observed

    monkeypatch.setattr(deo_internal, "query_fact_events", ambiguous_page)

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "strict_stream_corruption"
    assert result.evidence == {
        "stream_kind": "operation",
        "reason": "strict_stream_page_or_head_mismatch",
        "event_total": 2,
        "event_count": 1,
        "head_seq": 1,
    }
    assert result.projection is None


def test_parent_readiness_fails_closed_above_bounded_operation_stream(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    for sequence in range(1, deo_internal._MAX_OPERATION_EVENTS + 2):
        _append_operation_fact(
            _operation_command(
                identity,
                binding,
                tool_call_id=f"tool-overload-{sequence}",
                effect_id=f"effect-overload-{sequence}",
                expected_seq=sequence,
            ),
            binding,
            kind="admit",
            state="INTENT_COMMITTED",
            previous_version=0,
            idempotency_key=f"overload-{sequence}",
        )

    result = get_directed_effect_parent_readiness(_readiness_query(identity, binding))

    assert result.ok is False
    assert result.code == "strict_stream_overload"
    assert result.evidence == {
        "stream_kind": "operation",
        "event_total": deo_internal._MAX_OPERATION_EVENTS + 1,
        "max_events": deo_internal._MAX_OPERATION_EVENTS,
    }
    assert result.projection is None
