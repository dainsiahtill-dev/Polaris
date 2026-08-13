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
    DIRECTED_EFFECT_OPERATION_SCHEMA_V4,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3,
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentBatchCommandV1,
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
    admit_directed_effect_parent_batch,
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


def _parent_batch_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    *,
    turn_id: str = "turn-2",
    batch_id: str = "batch-2",
    admission_idempotency_key: str = "parent-2",
) -> AdmitDirectedEffectParentBatchCommandV1:
    return AdmitDirectedEffectParentBatchCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        correlation=ParentCorrelationV1(turn_id=turn_id, batch_id=batch_id),
        admission_idempotency_key=admission_idempotency_key,
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


def _receipt_complete_operations(
    workspace: Path,
    *,
    count: int,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
    tuple[AdmitDirectedEffectOperationCommandV1, ...],
]:
    identity = _attempt(workspace)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    commands = _seal_operation_commands(
        identity,
        binding,
        *(
            _operation_command(
                identity,
                binding,
                tool_call_id=f"tool-{ordinal}",
                effect_id=f"effect-{ordinal}",
                fingerprint=f"fingerprint-{ordinal}",
                expected_seq=ordinal,
            )
            for ordinal in range(1, count + 1)
        ),
    )
    for command in commands:
        assert admit_directed_effect_operation(command).code == "admitted"
    _finalize_operation_inventory(identity, binding)
    next_expected_seq = len(commands) + 1
    for command in commands:
        claimed = claim_directed_effect(
            ClaimDirectedEffectCommandV1(
                workspace=identity.workspace,
                task_id=identity.task_id,
                execution_attempt=identity,
                parent_binding=binding,
                tool_call_id=command.tool_call_id,
                effect_id=command.effect_id,
                expected_version=1,
                expected_seq=next_expected_seq,
                actor="test-child",
                intended_effect_fingerprint=command.intended_effect_fingerprint,
                policy_verdict_hash=command.policy_verdict_hash,
                expected_receipt_binding_hash=command.expected_receipt_binding_hash,
            )
        )
        assert claimed.code == "effect_claimed"
        next_expected_seq += 1
    for ordinal, command in enumerate(commands, start=1):
        committed = commit_directed_effect_receipt(
            CommitDirectedEffectReceiptCommandV1(
                workspace=identity.workspace,
                task_id=identity.task_id,
                execution_attempt=identity,
                parent_binding=binding,
                tool_call_id=command.tool_call_id,
                effect_id=command.effect_id,
                expected_version=2,
                expected_seq=next_expected_seq,
                actor="test-receipt",
                intended_effect_fingerprint=command.intended_effect_fingerprint,
                policy_verdict_hash=command.policy_verdict_hash,
                expected_receipt_binding_hash=command.expected_receipt_binding_hash,
                receipt_ref=f"receipt://director/batch-rollover/{ordinal}",
                receipt_hash=str(ordinal) * 64,
                receipt_binding_hash=command.expected_receipt_binding_hash,
                receipt_outcome="succeeded",
            )
        )
        assert committed.code == "receipt_committed"
        next_expected_seq += 1
    return identity, binding, commands


def _commit_successful_receipt(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    admitted: AdmitDirectedEffectOperationCommandV1,
) -> None:
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
            receipt_ref="receipt://director/batch-rollover",
            receipt_hash="8" * 64,
            receipt_binding_hash=admitted.expected_receipt_binding_hash,
            receipt_outcome="succeeded",
        )
    )
    assert committed.code == "receipt_committed"


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


def test_parent_batch_admission_rolls_over_receipt_complete_parent(tmp_path: Path) -> None:
    identity, first_binding, admitted = _started_operation(tmp_path)
    _commit_successful_receipt(identity, first_binding, admitted)

    second = admit_directed_effect_parent_batch(_parent_batch_command(identity))

    assert second.code == "parent_admitted", second
    assert second.parent_binding is not None
    assert second.parent_binding.parent_sequence == 2
    assert second.parent_binding.registry_version == 5
    assert second.parent_binding.source_event_seq == 5
    assert second.parent_binding.correlation == ParentCorrelationV1(
        turn_id="turn-2",
        batch_id="batch-2",
    )

    old_operation = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=first_binding,
            tool_call_id=admitted.tool_call_id,
            effect_id=admitted.effect_id,
        )
    )
    assert old_operation.code == "found"
    assert old_operation.state == "CLOSED_BY_PARENT"

    operation_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=first_binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    operation_close = cast(dict[str, object], operation_events[-1]["payload"])
    operation_descriptor = cast(dict[str, object], operation_close["replay_descriptor"])
    assert operation_close["schema_version"] == DIRECTED_EFFECT_OPERATION_SCHEMA_V4
    assert operation_descriptor["command"] == "close_by_parent"
    assert operation_descriptor["batch_rollover_hash"]
    assert operation_descriptor["terminal_intent_hash"] == ""
    assert operation_descriptor["settlement_outcome"] == ""

    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=first_binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events
    batch_close = cast(dict[str, object], registry_events[-2]["payload"])
    assert batch_close["schema_version"] == DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3
    assert batch_close["close_kind"] == "batch_rollover"
    assert batch_close["receipt_count"] == 1
    assert batch_close["failed_receipt_count"] == 0
    assert batch_close["dead_letter_count"] == 0


def test_parent_batch_admission_recovers_after_partial_child_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, first_binding, _commands = _receipt_complete_operations(tmp_path, count=2)
    crashed = False

    def crash_after_first_child_close(_result: object, close_index: int) -> None:
        nonlocal crashed
        if close_index == 1 and not crashed:
            crashed = True
            raise RuntimeError("simulated crash after first batch child close")

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_batch_rollover_child_close",
        staticmethod(crash_after_first_child_close),
    )

    with pytest.raises(RuntimeError, match="simulated crash after first batch child close"):
        admit_directed_effect_parent_batch(_parent_batch_command(identity))

    partial_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=first_binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert sum(event["event_type"].endswith(".closed_by_parent") for event in partial_events) == 1

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_batch_rollover_child_close",
        staticmethod(lambda _result, _close_index: None),
    )
    recovered = admit_directed_effect_parent_batch(_parent_batch_command(identity))

    assert recovered.code == "parent_admitted"
    assert recovered.parent_binding is not None
    assert recovered.parent_binding.parent_sequence == 2
    assert recovered.parent_binding.registry_version == 5
    operation_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=first_binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert sum(event["event_type"].endswith(".closed_by_parent") for event in operation_events) == 2
    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=first_binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events
    assert sum(event["event_type"] == "task_runtime.deo_parent_registry.v1.closed" for event in registry_events) == 1


def test_parent_batch_admission_replays_current_batch_without_closing_it(tmp_path: Path) -> None:
    identity, binding, _admitted = _started_operation(tmp_path)

    replay = admit_directed_effect_parent_batch(
        _parent_batch_command(
            identity,
            turn_id="turn-1",
            batch_id="batch-1",
            admission_idempotency_key="parent-1",
        )
    )

    assert replay.code == "parent_idempotent_replay"
    assert replay.parent_binding == binding
    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(registry_events) == 3
    assert all(
        cast(dict[str, object], event["payload"])["schema_version"] != DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3
        for event in registry_events
    )


def test_parent_batch_admission_blocks_unresolved_previous_effect(tmp_path: Path) -> None:
    identity, binding, _admitted = _started_operation(tmp_path)

    blocked = admit_directed_effect_parent_batch(_parent_batch_command(identity))

    assert blocked.ok is False
    assert blocked.code == "parent_open_conflict"
    assert blocked.evidence["reason"] == "batch_rollover_operation_unresolved"
    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(registry_events) == 3


def test_parent_batch_admission_rolls_over_recovery_pending_and_unclaimed_siblings(
    tmp_path: Path,
) -> None:
    """R136: partial physical failure must not permanently block multi-batch admit.

    Live L1-01 residual: deferred-repair parent left RECOVERY_PENDING + INTENT_COMMITTED
    residuals, so the next director write batch failed with deo_parent_admission_failed.
    Batch rollover must terminalize those residuals (dead-letter + abort) and admit the
    successor parent.
    """

    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    recovery_cmd, unclaimed_cmd = _seal_operation_commands(
        identity,
        binding,
        _operation_command(
            identity,
            binding,
            tool_call_id="tool-recovery",
            effect_id="effect-recovery",
            fingerprint="fingerprint-recovery",
            expected_seq=1,
        ),
        _operation_command(
            identity,
            binding,
            tool_call_id="tool-unclaimed",
            effect_id="effect-unclaimed",
            fingerprint="fingerprint-unclaimed",
            expected_seq=2,
        ),
    )
    assert admit_directed_effect_operation(recovery_cmd).code == "admitted"
    assert admit_directed_effect_operation(unclaimed_cmd).code == "admitted"
    _finalize_operation_inventory(identity, binding)

    claimed = claim_directed_effect(
        ClaimDirectedEffectCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=recovery_cmd.tool_call_id,
            effect_id=recovery_cmd.effect_id,
            expected_version=1,
            expected_seq=3,
            actor="test-child",
            intended_effect_fingerprint=recovery_cmd.intended_effect_fingerprint,
            policy_verdict_hash=recovery_cmd.policy_verdict_hash,
            expected_receipt_binding_hash=recovery_cmd.expected_receipt_binding_hash,
        )
    )
    assert claimed.code == "effect_claimed"
    pending = mark_directed_effect_recovery_pending(
        MarkDirectedEffectRecoveryPendingCommandV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=recovery_cmd.tool_call_id,
            effect_id=recovery_cmd.effect_id,
            expected_version=2,
            expected_seq=4,
            actor="test-recovery",
            intended_effect_fingerprint=recovery_cmd.intended_effect_fingerprint,
            policy_verdict_hash=recovery_cmd.policy_verdict_hash,
            expected_receipt_binding_hash=recovery_cmd.expected_receipt_binding_hash,
            reason="physical executor returned a non-success result after fence consumption",
            recovery_evidence_ref="recovery://director/r136-partial-batch",
            recovery_evidence_hash="c" * 64,
        )
    )
    assert pending.code == "recovery_pending"
    assert pending.state == "RECOVERY_PENDING"

    second = admit_directed_effect_parent_batch(
        _parent_batch_command(
            identity,
            turn_id="turn-2",
            batch_id="batch-2",
            admission_idempotency_key="parent-2",
        )
    )

    assert second.ok is True, second
    assert second.code == "parent_admitted"
    assert second.parent_binding is not None
    assert second.parent_binding.parent_sequence == 2

    recovery_op = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=recovery_cmd.tool_call_id,
            effect_id=recovery_cmd.effect_id,
        )
    )
    unclaimed_op = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=unclaimed_cmd.tool_call_id,
            effect_id=unclaimed_cmd.effect_id,
        )
    )
    assert recovery_op.state == "DEAD_LETTER"
    assert unclaimed_op.state == "ABORTED"

    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events
    batch_close = cast(dict[str, object], registry_events[-2]["payload"])
    assert batch_close["schema_version"] == DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3
    assert batch_close["close_kind"] == "batch_rollover"
    assert batch_close["receipt_count"] == 0
    assert batch_close["dead_letter_count"] == 1
    assert batch_close["aborted_count"] == 1


def test_terminal_settlement_accepts_crash_after_batch_close_before_next_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, admitted = _started_operation(tmp_path)
    _commit_successful_receipt(identity, binding, admitted)

    def crash_after_close(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated crash after durable batch close")

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_batch_rollover_parent_close",
        staticmethod(crash_after_close),
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        admit_directed_effect_parent_batch(_parent_batch_command(identity))

    monkeypatch.undo()
    settled = TaskRuntimeService(identity.workspace).settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="terminal after durable batch close",
        )
    )

    assert settled["success"] is True, json.dumps(settled, ensure_ascii=False, sort_keys=True)
    registry_events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            strict_integrity=True,
        )
    ).events
    assert cast(dict[str, object], registry_events[-1]["payload"])["schema_version"] == (
        DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3
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
