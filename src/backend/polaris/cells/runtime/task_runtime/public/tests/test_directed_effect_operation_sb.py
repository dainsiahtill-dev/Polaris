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


