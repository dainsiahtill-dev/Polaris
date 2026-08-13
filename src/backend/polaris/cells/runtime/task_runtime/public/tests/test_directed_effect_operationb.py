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
