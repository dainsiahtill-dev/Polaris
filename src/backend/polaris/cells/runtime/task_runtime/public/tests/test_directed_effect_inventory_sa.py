from __future__ import annotations

import ast
import hashlib
import inspect
import json
import operator
import textwrap
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from pathlib import Path
from threading import Barrier, Lock
from typing import Any, cast, get_args, get_type_hints

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
from polaris.cells.runtime.task_runtime import public as task_runtime_public
from polaris.cells.runtime.task_runtime.internal import directed_effect_operation as deo_internal
from polaris.cells.runtime.task_runtime.public import (
    DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentCommandV1,
    ClaimDirectedEffectCommandV1,
    DirectedEffectAuthorityFailureCodeV1,
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryCodeV1,
    DirectedEffectInventoryContingencyKindV1,
    DirectedEffectInventoryEffectTypeV1,
    DirectedEffectInventoryExecutionModeV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectInventoryProjectionV1,
    DirectedEffectInventoryResultV1,
    DirectedEffectOperationCodeV1,
    DirectedEffectOperationIdentityV1,
    DirectedEffectOperationResultV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    ParentCorrelationV1,
    SealDirectedEffectInventoryCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    abort_directed_effect_operation,
    admit_directed_effect_operation,
    admit_directed_effect_parent,
    claim_directed_effect,
    enroll_directed_effect_operation_stream,
    enroll_directed_effect_parent_registry_stream,
    seal_directed_effect_inventory,
)

_INTENT_CONTINGENCY_UNSET = object()
_PARENT_CLOSED_EVENT_TYPE = "task_runtime.deo_parent_registry.v1.closed"


def _attempt(workspace: Path) -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(workspace.resolve()),
        task_id=1,
        external_task_id="DEO-2A",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="worker-1",
        run_id="run-1",
        lease_expires_at="2026-07-16T12:00:00+00:00",
    )


def _parent_binding(identity: TaskRuntimeExecutionAttemptIdentityV1) -> DirectedEffectParentBindingV1:
    registry_identity = DirectedEffectParentRegistryIdentityV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        external_task_id=identity.external_task_id,
        session_id=identity.session_id,
        attempt=identity.attempt,
        role_id=identity.role_id,
        worker_id=identity.worker_id,
        run_id=identity.run_id,
    )
    return DirectedEffectParentBindingV1(
        schema_version=DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
        registry_identity=registry_identity,
        registry_stream_token="task-runtime-deo-parent-registry-1",
        registry_version=1,
        parent_sequence=1,
        binding_id="binding-1",
        operation_stream_token="task-runtime-deo-operation-1",
        binding_hash="binding-hash-1",
        admission_idempotency_key="parent-admission-1",
        correlation=ParentCorrelationV1(turn_id="turn-1", batch_id="batch-1"),
        actor="test-parent",
        source_event_id="parent-event-1",
        source_event_seq=1,
    )


def _intent(
    *,
    intended_effect_fingerprint: str = "1" * 64,
    policy_verdict_hash: str = "2" * 64,
    expected_receipt_binding_hash: str = "3" * 64,
    contingency_kind: object = _INTENT_CONTINGENCY_UNSET,
) -> DirectedEffectInventoryIntentV1:
    if contingency_kind is _INTENT_CONTINGENCY_UNSET:
        return DirectedEffectInventoryIntentV1(
            ordinal=0,
            tool_call_id="call-1",
            normalized_tool_name="write_file",
            effect_type="write",
            execution_mode="write_serial",
            intended_effect_fingerprint=intended_effect_fingerprint,
            policy_verdict_hash=policy_verdict_hash,
            expected_receipt_binding_hash=expected_receipt_binding_hash,
        )
    return DirectedEffectInventoryIntentV1(
        ordinal=0,
        tool_call_id="call-1",
        normalized_tool_name="write_file",
        effect_type="write",
        execution_mode="write_serial",
        intended_effect_fingerprint=intended_effect_fingerprint,
        policy_verdict_hash=policy_verdict_hash,
        expected_receipt_binding_hash=expected_receipt_binding_hash,
        contingency_kind=cast(DirectedEffectInventoryContingencyKindV1 | None, contingency_kind),
    )


def _seal_command(workspace: Path) -> SealDirectedEffectInventoryCommandV1:
    identity = _attempt(workspace)
    return SealDirectedEffectInventoryCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=_parent_binding(identity),
        intents=(_intent(contingency_kind=None),),
        expected_registry_version=1,
        expected_registry_seq=2,
        expected_operation_head_seq=0,
    )


def _intents(count: int) -> tuple[DirectedEffectInventoryIntentV1, ...]:
    template = _intent(contingency_kind=None)
    return tuple(
        replace(
            template,
            ordinal=ordinal,
            tool_call_id=f"call-{ordinal}",
        )
        for ordinal in range(count)
    )


def _member(
    *,
    ordinal: int = 0,
    tool_call_id: str = "call-1",
    effect_id: str = "effect-1",
    operation_id: str = "operation-1",
) -> DirectedEffectInventoryMemberV1:
    return DirectedEffectInventoryMemberV1(
        ordinal=ordinal,
        tool_call_id=tool_call_id,
        effect_id=effect_id,
        operation_id=operation_id,
        normalized_tool_name="write_file",
        effect_type="write",
        execution_mode="write_serial",
        intended_effect_fingerprint="1" * 64,
        policy_verdict_hash="2" * 64,
        expected_receipt_binding_hash="3" * 64,
    )


def _members(count: int) -> tuple[DirectedEffectInventoryMemberV1, ...]:
    return tuple(
        _member(
            ordinal=ordinal,
            tool_call_id=f"call-{ordinal}",
            effect_id=f"effect-{ordinal}",
            operation_id=f"operation-{ordinal}",
        )
        for ordinal in range(count)
    )


def _finalize_command(workspace: Path) -> FinalizeDirectedEffectInventoryAdmissionCommandV1:
    identity = _attempt(workspace)
    return FinalizeDirectedEffectInventoryAdmissionCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=_parent_binding(identity),
        inventory_hash="4" * 64,
        expected_registry_version=2,
        expected_registry_seq=3,
        expected_operation_head_seq=1,
    )


def _inventory_query(workspace: Path) -> GetDirectedEffectInventoryQueryV1:
    identity = _attempt(workspace)
    return GetDirectedEffectInventoryQueryV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=_parent_binding(identity),
    )


def _runtime_attempt(workspace: Path) -> TaskRuntimeExecutionAttemptIdentityV1:
    workspace_abs = str(workspace.resolve())
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace_abs,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="directed-effect-inventory-test",
        )
    )
    service = TaskRuntimeService(workspace_abs)
    task_id = int(service.create_task_row(subject="directed effect inventory")["id"])
    claimed = service.claim_execution(
        task_id,
        worker_id="inventory-test-worker",
        role_id="director",
        run_id="inventory-test-run",
        external_task_id="DEO-2A",
        selection_source="test",
    )
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])


def _runtime_parent_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> AdmitDirectedEffectParentCommandV1:
    return AdmitDirectedEffectParentCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        correlation=ParentCorrelationV1(turn_id="turn-1", batch_id="batch-1"),
        admission_idempotency_key="inventory-parent-1",
        expected_version=0,
        expected_seq=1,
        actor="test-parent",
    )


def _runtime_enroll_parent(identity: TaskRuntimeExecutionAttemptIdentityV1) -> None:
    result = enroll_directed_effect_parent_registry_stream(
        EnrollDirectedEffectParentRegistryStreamCommandV1(execution_attempt=identity)
    )
    assert result.code == "parent_registry_stream_enrolled"


def _runtime_admit_parent(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> DirectedEffectParentBindingV1:
    result = admit_directed_effect_parent(_runtime_parent_command(identity))
    assert result.code == "parent_admitted"
    assert result.parent_binding is not None
    return result.parent_binding


def _runtime_enroll_operation(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
) -> None:
    result = enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(
            execution_attempt=identity,
            parent_binding=binding,
        )
    )
    assert result.code == "operation_stream_enrolled"


def _runtime_parent(
    workspace: Path,
    *,
    enroll_operation: bool = True,
) -> tuple[TaskRuntimeExecutionAttemptIdentityV1, DirectedEffectParentBindingV1]:
    identity = _runtime_attempt(workspace)
    _runtime_enroll_parent(identity)
    binding = _runtime_admit_parent(identity)
    if enroll_operation:
        _runtime_enroll_operation(identity, binding)
    return identity, binding


def _runtime_seal_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    *,
    intents: tuple[DirectedEffectInventoryIntentV1, ...] | None = None,
    expected_registry_version: int = 1,
    expected_registry_seq: int = 2,
) -> SealDirectedEffectInventoryCommandV1:
    return SealDirectedEffectInventoryCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        intents=intents or (_intent(contingency_kind=None),),
        expected_registry_version=expected_registry_version,
        expected_registry_seq=expected_registry_seq,
        expected_operation_head_seq=0,
    )


def _runtime_sealed_parent(
    workspace: Path,
    *,
    intents: tuple[DirectedEffectInventoryIntentV1, ...] | None = None,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
    DirectedEffectInventoryProjectionV1,
]:
    identity, binding = _runtime_parent(workspace)
    sealed = seal_directed_effect_inventory(_runtime_seal_command(identity, binding, intents=intents))
    assert sealed.code == "inventory_sealed"
    assert sealed.projection is not None
    return identity, binding, sealed.projection


def _runtime_admission_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    member: DirectedEffectInventoryMemberV1,
    *,
    expected_seq: int = 1,
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
        actor="task-runtime-inventory-test",
        intended_effect_fingerprint=member.intended_effect_fingerprint,
        policy_verdict_hash=member.policy_verdict_hash,
        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
    )


def _runtime_claim_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    member: DirectedEffectInventoryMemberV1,
    *,
    expected_seq: int = 2,
) -> ClaimDirectedEffectCommandV1:
    return ClaimDirectedEffectCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        expected_version=1,
        expected_seq=expected_seq,
        actor="task-runtime-inventory-test",
        intended_effect_fingerprint=member.intended_effect_fingerprint,
        policy_verdict_hash=member.policy_verdict_hash,
        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
    )


def _runtime_abort_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    member: DirectedEffectInventoryMemberV1,
    *,
    expected_seq: int = 2,
) -> AbortDirectedEffectOperationCommandV1:
    return AbortDirectedEffectOperationCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        expected_version=1,
        expected_seq=expected_seq,
        actor="task-runtime-inventory-test",
        intended_effect_fingerprint=member.intended_effect_fingerprint,
        policy_verdict_hash=member.policy_verdict_hash,
        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
        reason="task5 inventory abort",
    )


def _runtime_finalize_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    projection: DirectedEffectInventoryProjectionV1,
    *,
    expected_operation_head_seq: int,
) -> FinalizeDirectedEffectInventoryAdmissionCommandV1:
    return FinalizeDirectedEffectInventoryAdmissionCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        inventory_hash=projection.inventory_hash,
        expected_registry_version=2,
        expected_registry_seq=3,
        expected_operation_head_seq=expected_operation_head_seq,
    )


def _runtime_ready_candidate(
    workspace: Path,
    *,
    intents: tuple[DirectedEffectInventoryIntentV1, ...] | None = None,
) -> tuple[
    TaskRuntimeExecutionAttemptIdentityV1,
    DirectedEffectParentBindingV1,
    DirectedEffectInventoryProjectionV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
]:
    identity, binding, sealed = _runtime_sealed_parent(workspace, intents=intents)
    for expected_seq, member in enumerate(sealed.members, start=1):
        admitted = admit_directed_effect_operation(
            _runtime_admission_command(
                identity,
                binding,
                member,
                expected_seq=expected_seq,
            )
        )
        assert admitted.code == "admitted"
    command = _runtime_finalize_command(
        identity,
        binding,
        sealed,
        expected_operation_head_seq=len(sealed.members),
    )
    return identity, binding, sealed, command


def _runtime_inventory_query(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
) -> GetDirectedEffectInventoryQueryV1:
    return GetDirectedEffectInventoryQueryV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
    )


def _task4_public_call(name: str, command_or_query: object) -> DirectedEffectInventoryResultV1:
    public_call = getattr(task_runtime_public, name, None)
    assert callable(public_call), f"{name} must be publicly callable"
    result = public_call(command_or_query)
    assert type(result) is DirectedEffectInventoryResultV1
    return result


def _runtime_finalize_inventory(
    command: FinalizeDirectedEffectInventoryAdmissionCommandV1,
) -> DirectedEffectInventoryResultV1:
    return _task4_public_call("finalize_directed_effect_inventory_admission", command)


def _runtime_get_inventory(
    query: GetDirectedEffectInventoryQueryV1,
) -> DirectedEffectInventoryResultV1:
    return _task4_public_call("get_directed_effect_inventory", query)


def _append_operation_transition_for_inventory_test(
    command: (
        AbortDirectedEffectOperationCommandV1 | AdmitDirectedEffectOperationCommandV1 | ClaimDirectedEffectCommandV1
    ),
    *,
    state: str,
    kind: str,
    previous_version: int,
) -> DirectedEffectOperationIdentityV1:
    repository = deo_internal.DirectedEffectOperationRepository()
    operation = repository._derive_operation(command, command.parent_binding)
    descriptor = repository._operation_descriptor(command, kind=kind)
    payload = repository._operation_event_canonical(
        operation=operation,
        state=state,
        previous_version=previous_version,
        descriptor=descriptor,
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=command.workspace,
            stream=command.parent_binding.operation_stream_token,
            event_type=deo_internal._operation_event_type(state),
            payload=payload,
            source="test",
            idempotency_key=f"task4-direct-{kind}-{operation.operation_id}-{command.expected_seq}",
            expected_seq=command.expected_seq,
            durability="fsync",
            strict_integrity=True,
        )
    )
    return operation


def _direct_ready_payload(
    binding: DirectedEffectParentBindingV1,
    sealed: DirectedEffectInventoryProjectionV1,
    *,
    actor: str = "roles.kernel",
    operation_source_head_seq: int | None = None,
) -> dict[str, object]:
    ordered_operation_ids = tuple(member.operation_id for member in sealed.members)
    operation_head = len(ordered_operation_ids) if operation_source_head_seq is None else operation_source_head_seq
    return {
        "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
        "stable_registry_identity": binding.registry_identity.to_record(),
        "previous_version": 2,
        "version": 3,
        "parent_sequence": binding.parent_sequence,
        "binding_id": binding.binding_id,
        "inventory_hash": sealed.inventory_hash,
        "ordered_operation_ids": list(ordered_operation_ids),
        "admission_set_hash": _test_admission_set_hash(
            binding_id=binding.binding_id,
            inventory_hash=sealed.inventory_hash,
            ordered_operation_ids=ordered_operation_ids,
            operation_source_head_seq=operation_head,
        ),
        "operation_source_head_seq": operation_head,
        "actor": actor,
        "recorded_at": "2026-07-16T00:00:00+00:00",
    }


def _test_admission_set_hash(
    *,
    binding_id: str,
    inventory_hash: str,
    ordered_operation_ids: tuple[str, ...],
    operation_source_head_seq: int,
) -> str:
    return _canonical_sha256(
        {
            "schema_version": "task-runtime.directed-effect-inventory-admission-set/1",
            "binding_id": binding_id,
            "inventory_hash": inventory_hash,
            "ordered_operation_ids": list(ordered_operation_ids),
            "operation_source_head_seq": operation_source_head_seq,
        }
    )


def _append_direct_ready_fact(
    binding: DirectedEffectParentBindingV1,
    payload: Mapping[str, object],
    *,
    idempotency_key: str,
) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=binding.workspace,
            stream=binding.registry_stream_token,
            event_type=deo_internal._PARENT_INVENTORY_READY_EVENT_TYPE,
            payload=payload,
            source="test",
            idempotency_key=idempotency_key,
            expected_seq=3,
            durability="fsync",
            strict_integrity=True,
        )
    )


def _runtime_events(workspace: str, stream: str) -> tuple[Mapping[str, Any], ...]:
    return query_fact_events(
        QueryFactEventsV1(
            workspace=workspace,
            stream=stream,
            strict_integrity=True,
        )
    ).events


def _runtime_close_parent(
    binding: DirectedEffectParentBindingV1,
    *,
    previous_version: int | None = None,
) -> None:
    previous = binding.registry_version if previous_version is None else previous_version
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=binding.workspace,
            stream=binding.registry_stream_token,
            event_type=_PARENT_CLOSED_EVENT_TYPE,
            payload={
                "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
                "stable_registry_identity": binding.registry_identity.to_record(),
                "previous_version": previous,
                "version": previous + 1,
                "parent_sequence": binding.parent_sequence,
                "binding_id": binding.binding_id,
                "close_evidence_ref": "fact://test/inventory-close",
                "close_evidence_hash": "a" * 64,
                "actor": "test-close",
                "recorded_at": "2026-07-16T00:00:00+00:00",
            },
            source="test",
            idempotency_key=f"inventory-close-{binding.binding_id}-{previous}",
            expected_seq=previous + 1,
            durability="fsync",
            strict_integrity=True,
        )
    )


def _expected_runtime_inventory(
    binding: DirectedEffectParentBindingV1,
    intent: DirectedEffectInventoryIntentV1,
) -> tuple[dict[str, object], str]:
    effect_id = (
        "deo_effect_v1_"
        + _canonical_sha256(
            {
                "schema_version": DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
                "parent_binding_id": binding.binding_id,
                "tool_call_id": intent.tool_call_id,
                "intended_effect_fingerprint": intent.intended_effect_fingerprint,
            }
        )[:48]
    )
    operation_id = (
        "deo_v1_"
        + _canonical_sha256(
            {
                "binding_id": binding.binding_id,
                "tool_call_id": intent.tool_call_id,
                "effect_id": effect_id,
            }
        )[:48]
    )
    member_record: dict[str, object] = {
        "schema_version": DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
        "ordinal": intent.ordinal,
        "tool_call_id": intent.tool_call_id,
        "effect_id": effect_id,
        "operation_id": operation_id,
        "normalized_tool_name": intent.normalized_tool_name,
        "effect_type": intent.effect_type,
        "execution_mode": intent.execution_mode,
        "intended_effect_fingerprint": intent.intended_effect_fingerprint,
        "policy_verdict_hash": intent.policy_verdict_hash,
        "expected_receipt_binding_hash": intent.expected_receipt_binding_hash,
        "contingency_kind": intent.contingency_kind,
    }
    inventory_hash = _canonical_sha256(
        {
            "schema_version": "task-runtime.directed-effect-inventory/1",
            "parent_binding_id": binding.binding_id,
            "members": [member_record],
        }
    )
    return member_record, inventory_hash


def _direct_seal_payload(
    binding: DirectedEffectParentBindingV1,
    intent: DirectedEffectInventoryIntentV1,
    *,
    previous_version: int = 1,
    version: int = 2,
) -> dict[str, object]:
    member_record, inventory_hash = _expected_runtime_inventory(binding, intent)
    return {
        "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
        "stable_registry_identity": binding.registry_identity.to_record(),
        "previous_version": previous_version,
        "version": version,
        "parent_sequence": binding.parent_sequence,
        "binding_id": binding.binding_id,
        "members": [member_record],
        "member_count": 1,
        "inventory_hash": inventory_hash,
        "actor": "roles.kernel",
        "recorded_at": "2026-07-16T00:00:00+00:00",
    }


def _append_direct_seal_fact(
    binding: DirectedEffectParentBindingV1,
    payload: Mapping[str, object],
    *,
    expected_seq: int,
    idempotency_key: str,
) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=binding.workspace,
            stream=binding.registry_stream_token,
            event_type="task_runtime.directed_effect_parent_registry.v1.parent_inventory_sealed",
            payload=payload,
            source="test",
            idempotency_key=idempotency_key,
            expected_seq=expected_seq,
            durability="fsync",
            strict_integrity=True,
        )
    )


def _inventory_projection(
    workspace: Path,
    *,
    ready: bool = False,
) -> DirectedEffectInventoryProjectionV1:
    identity = _attempt(workspace)
    members = _members(2)
    return DirectedEffectInventoryProjectionV1(
        schema_version=DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding_id="binding-1",
        members=members,
        inventory_hash="4" * 64,
        sealed_event_id="inventory-sealed-2",
        sealed_event_seq=2,
        parent_registry_source_head_seq=3 if ready else 2,
        operation_source_head_seq=2 if ready else 1,
        inventory_ready=ready,
        ready_event_id="inventory-ready-3" if ready else None,
        ready_event_seq=3 if ready else None,
        admitted_count=2 if ready else 1,
        missing_operation_ids=() if ready else (members[1].operation_id,),
        unexpected_operation_ids=(),
    )


def _operation(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    member: DirectedEffectInventoryMemberV1,
) -> DirectedEffectOperationIdentityV1:
    return DirectedEffectOperationIdentityV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt_id=binding.registry_identity.execution_attempt_id,
        parent_binding_id=binding.binding_id,
        parent_sequence=binding.parent_sequence,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        operation_id=member.operation_id,
        operation_stream_token=binding.operation_stream_token,
    )


def _canonical_sha256(record: Mapping[str, object]) -> str:
    encoded = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _grant_unsigned_record(
    *,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    parent_binding: DirectedEffectParentBindingV1,
    operation: DirectedEffectOperationIdentityV1,
    member: DirectedEffectInventoryMemberV1,
    inventory_hash: str = "4" * 64,
    operation_version: int = 2,
    claim_event_id: str = "claim-event-2",
    claim_event_seq: int = 2,
    operation_source_head_seq: int = 2,
    parent_registry_source_head_seq: int = 3,
    schema_version: str = DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "execution_attempt": execution_attempt.to_record(),
        "parent_binding": parent_binding.to_record(),
        "operation": operation.to_record(),
        "member": member.to_record(),
        "inventory_hash": inventory_hash,
        "operation_version": operation_version,
        "claim_event_id": claim_event_id,
        "claim_event_seq": claim_event_seq,
        "operation_source_head_seq": operation_source_head_seq,
        "parent_registry_source_head_seq": parent_registry_source_head_seq,
    }


def _grant(workspace: Path) -> DirectedEffectClaimGrantV1:
    identity = _attempt(workspace)
    binding = _parent_binding(identity)
    member = _member()
    operation = _operation(identity, binding, member)
    unsigned_record = _grant_unsigned_record(
        execution_attempt=identity,
        parent_binding=binding,
        operation=operation,
        member=member,
    )
    return DirectedEffectClaimGrantV1(
        schema_version=DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
        execution_attempt=identity,
        parent_binding=binding,
        operation=operation,
        member=member,
        inventory_hash="4" * 64,
        operation_version=2,
        claim_event_id="claim-event-2",
        claim_event_seq=2,
        operation_source_head_seq=2,
        parent_registry_source_head_seq=3,
        grant_hash=_canonical_sha256(unsigned_record),
    )


def _grant_hash_after(
    grant: DirectedEffectClaimGrantV1,
    **changes: object,
) -> str:
    return _canonical_sha256(
        _grant_unsigned_record(
            schema_version=cast(str, changes.get("schema_version", grant.schema_version)),
            execution_attempt=cast(
                TaskRuntimeExecutionAttemptIdentityV1,
                changes.get("execution_attempt", grant.execution_attempt),
            ),
            parent_binding=cast(
                DirectedEffectParentBindingV1,
                changes.get("parent_binding", grant.parent_binding),
            ),
            operation=cast(
                DirectedEffectOperationIdentityV1,
                changes.get("operation", grant.operation),
            ),
            member=cast(
                DirectedEffectInventoryMemberV1,
                changes.get("member", grant.member),
            ),
            inventory_hash=cast(str, changes.get("inventory_hash", grant.inventory_hash)),
            operation_version=cast(int, changes.get("operation_version", grant.operation_version)),
            claim_event_id=cast(str, changes.get("claim_event_id", grant.claim_event_id)),
            claim_event_seq=cast(int, changes.get("claim_event_seq", grant.claim_event_seq)),
            operation_source_head_seq=cast(
                int,
                changes.get("operation_source_head_seq", grant.operation_source_head_seq),
            ),
            parent_registry_source_head_seq=cast(
                int,
                changes.get(
                    "parent_registry_source_head_seq",
                    grant.parent_registry_source_head_seq,
                ),
            ),
        )
    )






def test_inventory_intent_has_exact_record_and_is_frozen() -> None:
    intent = _intent(contingency_kind=None)

    assert intent.to_record() == {
        "schema_version": "task-runtime.directed-effect-inventory-intent/1",
        "ordinal": 0,
        "tool_call_id": "call-1",
        "normalized_tool_name": "write_file",
        "effect_type": "write",
        "execution_mode": "write_serial",
        "intended_effect_fingerprint": "1" * 64,
        "policy_verdict_hash": "2" * 64,
        "expected_receipt_binding_hash": "3" * 64,
        "contingency_kind": None,
    }
    with pytest.raises(FrozenInstanceError):
        intent.ordinal = 1  # type: ignore[misc]


def test_inventory_intent_defaults_contingency_kind_to_none() -> None:
    intent = _intent()

    assert intent.contingency_kind is None
    assert intent.to_record()["contingency_kind"] is None


@pytest.mark.parametrize(
    ("effect_type", "execution_mode"),
    (
        ("write", "write_serial"),
        ("async", "async_receipt"),
    ),
)
@pytest.mark.parametrize("contingency_kind", (None, "forward", "rollback"))
def test_inventory_intent_preserves_each_legal_effect_mode_and_contingency_pair(
    effect_type: DirectedEffectInventoryEffectTypeV1,
    execution_mode: DirectedEffectInventoryExecutionModeV1,
    contingency_kind: DirectedEffectInventoryContingencyKindV1 | None,
) -> None:
    intent = replace(
        _intent(contingency_kind=None),
        effect_type=effect_type,
        execution_mode=execution_mode,
        contingency_kind=contingency_kind,
    )

    assert intent.to_record()["effect_type"] == effect_type
    assert intent.to_record()["execution_mode"] == execution_mode
    assert intent.to_record()["contingency_kind"] == contingency_kind


def test_inventory_intent_from_record_round_trips_exact_record() -> None:
    intent = _intent(contingency_kind="rollback")

    assert DirectedEffectInventoryIntentV1.from_record(intent.to_record()) == intent


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_inventory_intent_from_record_rejects_field_drift(mutation: str) -> None:
    record = _intent(contingency_kind=None).to_record()
    if mutation == "missing":
        del record["tool_call_id"]
    else:
        record["unexpected"] = "field"

    with pytest.raises(ValueError):
        DirectedEffectInventoryIntentV1.from_record(record)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("ordinal", True),
        ("ordinal", "0"),
        ("contingency_kind", 1),
    ),
)
def test_inventory_intent_from_record_rejects_invalid_field_types(field_name: str, value: object) -> None:
    record = _intent(contingency_kind=None).to_record()
    record[field_name] = value

    with pytest.raises(TypeError):
        DirectedEffectInventoryIntentV1.from_record(record)


def test_inventory_intent_rejects_non_string_contingency_type() -> None:
    with pytest.raises(TypeError):
        replace(
            _intent(contingency_kind=None),
            contingency_kind=cast(Any, 1),
        )


@pytest.mark.parametrize(
    "violation",
    (
        "negative_ordinal",
        "empty_call_id",
        "blank_call_id",
        "empty_tool_name",
        "blank_tool_name",
        "write_async_receipt",
        "async_write_serial",
        "unknown_contingency",
    ),
)
def test_inventory_intent_rejects_locked_invariant_violations(violation: str) -> None:
    intent = _intent(contingency_kind=None)

    with pytest.raises(ValueError):
        if violation == "negative_ordinal":
            replace(intent, ordinal=-1)
        elif violation == "empty_call_id":
            replace(intent, tool_call_id="")
        elif violation == "blank_call_id":
            replace(intent, tool_call_id="   ")
        elif violation == "empty_tool_name":
            replace(intent, normalized_tool_name="")
        elif violation == "blank_tool_name":
            replace(intent, normalized_tool_name="   ")
        elif violation == "write_async_receipt":
            replace(intent, effect_type="write", execution_mode="async_receipt")
        elif violation == "async_write_serial":
            replace(intent, effect_type="async", execution_mode="write_serial")
        else:
            replace(
                intent,
                contingency_kind=cast(DirectedEffectInventoryContingencyKindV1, "unknown"),
            )


@pytest.mark.parametrize(
    (
        "intended_effect_fingerprint",
        "policy_verdict_hash",
        "expected_receipt_binding_hash",
    ),
    (
        ("A" * 64, "2" * 64, "3" * 64),
        ("1" * 64, "A" * 64, "3" * 64),
        ("1" * 64, "2" * 64, "A" * 64),
        ("1" * 63, "2" * 64, "3" * 64),
        ("1" * 64, "g" * 64, "3" * 64),
    ),
)
def test_inventory_intent_rejects_any_non_lowercase_hex_digest(
    intended_effect_fingerprint: str,
    policy_verdict_hash: str,
    expected_receipt_binding_hash: str,
) -> None:
    with pytest.raises(ValueError):
        _intent(
            intended_effect_fingerprint=intended_effect_fingerprint,
            policy_verdict_hash=policy_verdict_hash,
            expected_receipt_binding_hash=expected_receipt_binding_hash,
            contingency_kind=None,
        )


def test_seal_inventory_command_accepts_matching_attempt_parent_and_one_intent(tmp_path: Path) -> None:
    command = _seal_command(tmp_path)

    assert command.workspace == str(tmp_path.resolve())
    assert command.task_id == command.execution_attempt.task_id
    assert command.parent_binding.registry_identity == DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
        command.execution_attempt
    )
    assert command.intents == (_intent(contingency_kind=None),)
    assert command.expected_registry_version == 1
    assert command.expected_registry_seq == 2
    assert command.expected_operation_head_seq == 0


def test_seal_inventory_command_rejects_nonzero_operation_head(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        replace(_seal_command(tmp_path), expected_operation_head_seq=1)


def test_seal_inventory_command_rejects_registry_seq_not_following_version(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        replace(
            _seal_command(tmp_path),
            expected_registry_version=100,
            expected_registry_seq=2,
        )


def test_seal_inventory_command_rejects_non_tuple_intents(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        replace(
            _seal_command(tmp_path),
            intents=cast(tuple[DirectedEffectInventoryIntentV1, ...], list(_intents(1))),
        )


@pytest.mark.parametrize("count", (0, 65))
def test_seal_inventory_command_rejects_inventory_size_outside_bounds(tmp_path: Path, count: int) -> None:
    with pytest.raises(ValueError):
        replace(_seal_command(tmp_path), intents=_intents(count))


def test_seal_inventory_command_rejects_non_contiguous_ordinals(tmp_path: Path) -> None:
    intents = _intents(2)

    with pytest.raises(ValueError):
        replace(
            _seal_command(tmp_path),
            intents=(intents[0], replace(intents[1], ordinal=2)),
        )


def test_seal_inventory_command_rejects_duplicate_tool_call_ids(tmp_path: Path) -> None:
    intents = _intents(2)

    with pytest.raises(ValueError):
        replace(
            _seal_command(tmp_path),
            intents=(intents[0], replace(intents[1], tool_call_id=intents[0].tool_call_id)),
        )


@pytest.mark.parametrize(
    "violation",
    (
        "zero_registry_version",
        "bool_registry_version",
        "low_registry_seq",
        "bool_registry_seq",
        "bool_operation_head",
    ),
)
def test_seal_inventory_command_rejects_locked_sequence_boundaries(
    tmp_path: Path,
    violation: str,
) -> None:
    command = _seal_command(tmp_path)

    with pytest.raises(ValueError):
        if violation == "zero_registry_version":
            replace(command, expected_registry_version=0)
        elif violation == "bool_registry_version":
            replace(command, expected_registry_version=True)
        elif violation == "low_registry_seq":
            replace(command, expected_registry_seq=1)
        elif violation == "bool_registry_seq":
            replace(command, expected_registry_seq=True)
        else:
            replace(command, expected_operation_head_seq=True)


@pytest.mark.parametrize(
    "mismatch",
    (
        "attempt_workspace",
        "parent_workspace",
        "attempt_task",
        "parent_task",
        "parent_registry_identity",
    ),
)
def test_seal_inventory_command_rejects_attempt_parent_identity_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    command = _seal_command(tmp_path)
    other_workspace = str((tmp_path / "other-workspace").resolve())
    with pytest.raises(ValueError):
        if mismatch == "attempt_workspace":
            replace(
                command,
                execution_attempt=replace(command.execution_attempt, workspace=other_workspace),
            )
        elif mismatch == "parent_workspace":
            replace(
                command,
                parent_binding=replace(
                    command.parent_binding,
                    registry_identity=replace(
                        command.parent_binding.registry_identity,
                        workspace=other_workspace,
                    ),
                ),
            )
        elif mismatch == "attempt_task":
            replace(
                command,
                execution_attempt=replace(command.execution_attempt, task_id=command.task_id + 1),
            )
        elif mismatch == "parent_task":
            replace(
                command,
                parent_binding=replace(
                    command.parent_binding,
                    registry_identity=replace(
                        command.parent_binding.registry_identity,
                        task_id=command.task_id + 1,
                    ),
                ),
            )
        else:
            replace(
                command,
                parent_binding=replace(
                    command.parent_binding,
                    registry_identity=replace(
                        command.parent_binding.registry_identity,
                        session_id="different-session",
                    ),
                ),
            )


def test_inventory_member_has_exact_record_roundtrip_and_is_frozen() -> None:
    member = _member(effect_id=" effect-1 ", operation_id=" operation-1 ")
    expected_record = {
        "schema_version": DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
        "ordinal": 0,
        "tool_call_id": "call-1",
        "effect_id": "effect-1",
        "operation_id": "operation-1",
        "normalized_tool_name": "write_file",
        "effect_type": "write",
        "execution_mode": "write_serial",
        "intended_effect_fingerprint": "1" * 64,
        "policy_verdict_hash": "2" * 64,
        "expected_receipt_binding_hash": "3" * 64,
        "contingency_kind": None,
    }

    assert member.to_record() == expected_record
    assert DirectedEffectInventoryMemberV1.from_record(expected_record) == member
    with pytest.raises(FrozenInstanceError):
        member.operation_id = "other-operation"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("tool_call_id", ""),
        ("tool_call_id", "   "),
        ("effect_id", ""),
        ("effect_id", "   "),
        ("operation_id", ""),
        ("operation_id", "   "),
    ),
)
def test_inventory_member_rejects_empty_identity_tokens(field_name: str, value: str) -> None:
    with pytest.raises(ValueError):
        replace(_member(), **{field_name: value})


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_inventory_member_from_record_rejects_field_drift(mutation: str) -> None:
    record = _member().to_record()
    if mutation == "missing":
        del record["operation_id"]
    else:
        record["unexpected"] = "field"

    with pytest.raises(ValueError):
        DirectedEffectInventoryMemberV1.from_record(record)


def test_finalize_inventory_admission_accepts_exact_identity_and_boundaries(tmp_path: Path) -> None:
    command = _finalize_command(tmp_path)

    assert command.workspace == str(tmp_path.resolve())
    assert command.task_id == command.execution_attempt.task_id
    assert command.parent_binding.registry_identity == DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
        command.execution_attempt
    )
    assert command.inventory_hash == "4" * 64
    assert command.expected_registry_version == 2
    assert command.expected_registry_seq == 3
    assert command.expected_operation_head_seq == 1
    assert command.actor == "roles.kernel"


def test_finalize_inventory_admission_rejects_workspace_whitespace(tmp_path: Path) -> None:
    command = _finalize_command(tmp_path)

    with pytest.raises(ValueError):
        replace(command, workspace=f"  {command.workspace}  ")


@pytest.mark.parametrize(
    "violation",
    (
        "inventory_hash",
        "registry_version",
        "registry_seq",
        "operation_head",
        "actor",
    ),
)
def test_finalize_inventory_admission_rejects_locked_hash_sequence_and_actor(
    tmp_path: Path,
    violation: str,
) -> None:
    command = _finalize_command(tmp_path)

    with pytest.raises(ValueError):
        if violation == "inventory_hash":
            replace(command, inventory_hash="A" * 64)
        elif violation == "registry_version":
            replace(command, expected_registry_version=1)
        elif violation == "registry_seq":
            replace(command, expected_registry_seq=2)
        elif violation == "operation_head":
            replace(command, expected_operation_head_seq=0)
        else:
            replace(command, actor="director")


@pytest.mark.parametrize(
    "mismatch",
    (
        "workspace",
        "task_id",
        "attempt_workspace",
        "attempt_identity",
        "parent_workspace",
        "parent_identity",
    ),
)
def test_finalize_inventory_admission_rejects_any_identity_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    command = _finalize_command(tmp_path)
    other_workspace = str((tmp_path / "other-workspace").resolve())

    with pytest.raises(ValueError):
        if mismatch == "workspace":
            replace(command, workspace=f"{command.workspace}/..")
        elif mismatch == "task_id":
            replace(command, task_id=command.task_id + 1)
        elif mismatch == "attempt_workspace":
            replace(
                command,
                execution_attempt=replace(command.execution_attempt, workspace=other_workspace),
            )
        elif mismatch == "attempt_identity":
            replace(
                command,
                execution_attempt=replace(command.execution_attempt, session_id="other-session"),
            )
        elif mismatch == "parent_workspace":
            replace(
                command,
                parent_binding=replace(
                    command.parent_binding,
                    registry_identity=replace(
                        command.parent_binding.registry_identity,
                        workspace=other_workspace,
                    ),
                ),
            )
        else:
            replace(
                command,
                parent_binding=replace(
                    command.parent_binding,
                    registry_identity=replace(
                        command.parent_binding.registry_identity,
                        worker_id="other-worker",
                    ),
                ),
            )


def test_get_inventory_query_accepts_only_exact_attempt_parent_identity(tmp_path: Path) -> None:
    query = _inventory_query(tmp_path)

    assert query.workspace == str(tmp_path.resolve())
    assert query.task_id == query.execution_attempt.task_id
    assert query.parent_binding.registry_identity == DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
        query.execution_attempt
    )


def test_get_inventory_query_rejects_workspace_whitespace(tmp_path: Path) -> None:
    query = _inventory_query(tmp_path)

    with pytest.raises(ValueError):
        replace(query, workspace=f"  {query.workspace}  ")


@pytest.mark.parametrize(
    "mismatch",
    ("workspace", "task_id", "attempt_identity", "parent_identity"),
)
def test_get_inventory_query_rejects_locked_identity_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    query = _inventory_query(tmp_path)

    with pytest.raises(ValueError):
        if mismatch == "workspace":
            replace(query, workspace=f"{query.workspace}/..")
        elif mismatch == "task_id":
            replace(query, task_id=query.task_id + 1)
        elif mismatch == "attempt_identity":
            replace(
                query,
                execution_attempt=replace(query.execution_attempt, run_id="other-run"),
            )
        else:
            replace(
                query,
                parent_binding=replace(
                    query.parent_binding,
                    registry_identity=replace(
                        query.parent_binding.registry_identity,
                        role_id="other-role",
                    ),
                ),
            )


def test_seal_inventory_public_entry_rejects_wrong_command_type() -> None:
    with pytest.raises(TypeError):
        seal_directed_effect_inventory(cast(Any, object()))


def test_seal_inventory_runtime_requires_explicit_operation_stream_enrollment(
    tmp_path: Path,
) -> None:
    identity, binding = _runtime_parent(tmp_path, enroll_operation=False)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)

    rejected = seal_directed_effect_inventory(_runtime_seal_command(identity, binding))

    assert rejected.ok is False
    assert rejected.code == "stream_lock_missing"
    assert rejected.projection is None
    assert rejected.evidence["stream_kind"] == "inventory_guarded_pair"
    assert rejected.evidence["target_stream_kind"] == "parent_registry"
    assert rejected.evidence["target_stream_token"] == binding.registry_stream_token
    assert rejected.evidence["guard_stream_kind"] == "operation"
    assert rejected.evidence["guard_stream_token"] == binding.operation_stream_token
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    with pytest.raises(FactStreamError) as still_missing:
        _runtime_events(identity.workspace, binding.operation_stream_token)
    assert still_missing.value.code == "stream_lock_missing"


def test_seal_inventory_runtime_persists_exact_single_seal_and_zero_operation_facts(
    tmp_path: Path,
) -> None:
    identity, binding = _runtime_parent(tmp_path)
    intent = _intent(contingency_kind=None)
    command = _runtime_seal_command(identity, binding, intents=(intent,))
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)

    result = seal_directed_effect_inventory(command)

    expected_member, expected_inventory_hash = _expected_runtime_inventory(binding, intent)
    assert result.ok is True
    assert result.code == "inventory_sealed"
    assert result.projection is not None
    assert result.projection.inventory_ready is False
    assert result.projection.admitted_count == 0
    assert result.projection.members[0].to_record() == expected_member
    assert result.projection.missing_operation_ids == (expected_member["operation_id"],)
    assert result.projection.unexpected_operation_ids == ()
    assert result.projection.inventory_hash == expected_inventory_hash

    registry_after = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_events = _runtime_events(identity.workspace, binding.operation_stream_token)
    assert len(registry_after) == len(registry_before) + 1
    assert operation_events == ()
    sealed_event = registry_after[-1]
    assert sealed_event["event_type"] == ("task_runtime.directed_effect_parent_registry.v1.parent_inventory_sealed")
    payload = cast(Mapping[str, Any], sealed_event["payload"])
    assert set(payload) == {
        "schema_version",
        "stable_registry_identity",
        "previous_version",
        "version",
        "parent_sequence",
        "binding_id",
        "members",
        "member_count",
        "inventory_hash",
        "actor",
        "recorded_at",
    }
    assert payload["schema_version"] == DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1
    assert payload["stable_registry_identity"] == binding.registry_identity.to_record()
    assert payload["previous_version"] == 1
    assert payload["version"] == 2
    assert payload["parent_sequence"] == binding.parent_sequence
    assert payload["binding_id"] == binding.binding_id
    assert payload["members"] == [expected_member]
    assert payload["member_count"] == 1
    assert payload["inventory_hash"] == expected_inventory_hash
    assert payload["actor"] == "roles.kernel"
    recorded_at = datetime.fromisoformat(cast(str, payload["recorded_at"]))
    assert recorded_at.tzinfo is not None
    assert recorded_at.utcoffset() is not None


def test_seal_inventory_runtime_exact_retry_precedes_stale_cas_and_does_not_append(
    tmp_path: Path,
) -> None:
    identity, binding = _runtime_parent(tmp_path)
    command = _runtime_seal_command(identity, binding)
    assert seal_directed_effect_inventory(command).code == "inventory_sealed"
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)

    replay = seal_directed_effect_inventory(
        replace(
            command,
            expected_registry_version=99,
            expected_registry_seq=100,
        )
    )

    assert replay.ok is True
    assert replay.code == "inventory_seal_idempotent_replay"
    assert replay.projection is not None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before


def test_seal_inventory_runtime_changed_semantics_conflict_without_append(tmp_path: Path) -> None:
    identity, binding = _runtime_parent(tmp_path)
    command = _runtime_seal_command(identity, binding)
    assert seal_directed_effect_inventory(command).code == "inventory_sealed"
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    changed_intent = replace(command.intents[0], policy_verdict_hash="5" * 64)

    conflict = seal_directed_effect_inventory(
        replace(
            command,
            intents=(changed_intent,),
            expected_registry_version=2,
            expected_registry_seq=3,
        )
    )

    assert conflict.ok is False
    assert conflict.code == "inventory_seal_conflict"
    assert conflict.projection is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before


def test_seal_inventory_runtime_stale_registry_cas_fails_before_first_seal(tmp_path: Path) -> None:
    identity, binding = _runtime_parent(tmp_path)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)

    stale = seal_directed_effect_inventory(
        _runtime_seal_command(
            identity,
            binding,
            expected_registry_version=2,
            expected_registry_seq=3,
        )
    )

    assert stale.ok is False
    assert stale.code == "parent_registry_version_conflict"
    assert stale.projection is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before


def test_seal_inventory_runtime_requires_empty_operation_stream_without_registry_append(
    tmp_path: Path,
) -> None:
    identity, binding = _runtime_parent(tmp_path)
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            event_type="test.directed_effect_operation.v1.nonempty",
            payload={"test_marker": "operation-head-one"},
            source="test",
            idempotency_key="inventory-nonempty-operation-stream",
            expected_seq=1,
            durability="fsync",
            strict_integrity=True,
        )
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)

    rejected = seal_directed_effect_inventory(_runtime_seal_command(identity, binding))

    assert rejected.ok is False
    assert rejected.code == "inventory_requires_empty_operation_stream"
    assert rejected.projection is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert len(_runtime_events(identity.workspace, binding.operation_stream_token)) == 1


def test_seal_inventory_runtime_rejects_closed_parent_at_current_registry_cas(tmp_path: Path) -> None:
    identity, binding = _runtime_parent(tmp_path)
    _runtime_close_parent(binding)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)

    rejected = seal_directed_effect_inventory(
        _runtime_seal_command(
            identity,
            binding,
            expected_registry_version=2,
            expected_registry_seq=3,
        )
    )

    assert rejected.ok is False
    assert rejected.code == "parent_closed"
    assert rejected.projection is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before


@pytest.mark.parametrize(
    "corruption",
    (
        "extra_payload_field",
        "member_count_drift",
        "wrong_derived_effect_id",
        "inventory_hash_mismatch",
        "naive_recorded_at",
    ),
)
def test_seal_inventory_corrupted_seal_fact_is_rejected_by_strict_parser(
    tmp_path: Path,
    corruption: str,
) -> None:
    identity, binding = _runtime_parent(tmp_path)
    intent = _intent(contingency_kind=None)
    payload = _direct_seal_payload(binding, intent)
    if corruption == "extra_payload_field":
        payload["unexpected"] = "field"
    elif corruption == "member_count_drift":
        payload["member_count"] = 2
    elif corruption == "wrong_derived_effect_id":
        member = dict(cast(list[Mapping[str, object]], payload["members"])[0])
        expected_effect_id = cast(str, member["effect_id"])
        wrong_effect_id = "deo_effect_v1_" + ("f" if expected_effect_id[-1] != "f" else "e") * 48
        member["effect_id"] = wrong_effect_id
        member["operation_id"] = (
            "deo_v1_"
            + _canonical_sha256(
                {
                    "binding_id": binding.binding_id,
                    "tool_call_id": member["tool_call_id"],
                    "effect_id": wrong_effect_id,
                }
            )[:48]
        )
        payload["members"] = [member]
        payload["inventory_hash"] = _canonical_sha256(
            {
                "schema_version": "task-runtime.directed-effect-inventory/1",
                "parent_binding_id": binding.binding_id,
                "members": [member],
            }
        )
    elif corruption == "inventory_hash_mismatch":
        observed_hash = cast(str, payload["inventory_hash"])
        payload["inventory_hash"] = "f" * 64 if observed_hash != "f" * 64 else "e" * 64
    else:
        payload["recorded_at"] = "2026-07-16T00:00:00"

    _append_direct_seal_fact(
        binding,
        payload,
        expected_seq=2,
        idempotency_key=f"corrupted-seal-{corruption}",
    )
    persisted = _runtime_events(identity.workspace, binding.registry_stream_token)
    assert len(persisted) == 2
    assert persisted[-1]["payload"] == payload

    result = seal_directed_effect_inventory(_runtime_seal_command(identity, binding))

    assert result.ok is False
    assert result.code == "strict_stream_corruption"
    assert result.projection is None


def test_seal_inventory_duplicate_seal_fact_is_strict_corruption(tmp_path: Path) -> None:
    identity, binding = _runtime_parent(tmp_path)
    intent = _intent(contingency_kind=None)
    _append_direct_seal_fact(
        binding,
        _direct_seal_payload(binding, intent),
        expected_seq=2,
        idempotency_key="duplicate-seal-first",
    )
    _append_direct_seal_fact(
        binding,
        _direct_seal_payload(
            binding,
            intent,
            previous_version=2,
            version=3,
        ),
        expected_seq=3,
        idempotency_key="duplicate-seal-second",
    )
    assert len(_runtime_events(identity.workspace, binding.registry_stream_token)) == 3

    result = seal_directed_effect_inventory(
        _runtime_seal_command(
            identity,
            binding,
            expected_registry_version=3,
            expected_registry_seq=4,
        )
    )

    assert result.ok is False
    assert result.code == "strict_stream_corruption"
    assert result.evidence.get("reason") == "duplicate_parent_inventory_seal"


@pytest.mark.parametrize(
    "receipt_field",
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
def test_seal_inventory_rejects_tampered_guarded_receipt_after_real_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_field: str,
) -> None:
    identity, binding = _runtime_parent(tmp_path)

    def tamper_receipt(receipt: Any) -> Any:
        if receipt_field == "event_id":
            value = f"{receipt.event_id}-tampered"
        elif receipt_field == "workspace":
            value = str((tmp_path / "other-workspace").resolve())
        elif receipt_field == "stream":
            value = f"{receipt.stream}-tampered"
        elif receipt_field == "storage_path":
            value = f"{receipt.storage_path}.tampered"
        elif receipt_field == "appended_at":
            value = "2099-01-01T00:00:00+00:00"
        elif receipt_field == "appended_seq":
            value = receipt.appended_seq + 1
        else:
            value = "f" * 64 if receipt.semantic_digest != "f" * 64 else "e" * 64
        return replace(receipt, **{receipt_field: value})

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(tamper_receipt),
    )

    result = seal_directed_effect_inventory(_runtime_seal_command(identity, binding))

    assert result.ok is False
    assert result.code == "guarded_receipt_mismatch"
    assert len(_runtime_events(identity.workspace, binding.registry_stream_token)) == 2


def test_seal_inventory_receipt_confirmation_requires_canonical_exact_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding = _runtime_parent(tmp_path)
    real_append = deo_internal.append_if_guarded_snapshot
    append_calls = 0

    def append_then_fail_exact_replay(command: Any) -> Any:
        nonlocal append_calls
        append_calls += 1
        if append_calls == 1:
            return real_append(command)
        raise FactStreamError(
            "simulated canonical receipt replay failure",
            code="append_write_failed",
            details={"phase": "confirm_exact_replay"},
        )

    monkeypatch.setattr(deo_internal, "append_if_guarded_snapshot", append_then_fail_exact_replay)

    result = seal_directed_effect_inventory(_runtime_seal_command(identity, binding))

    assert result.ok is False
    assert result.code == "guarded_receipt_mismatch"
    assert append_calls == 2


def test_seal_inventory_ambiguous_reconcile_requires_canonical_receipt_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding = _runtime_parent(tmp_path)
    real_append = deo_internal.append_if_guarded_snapshot
    append_calls = 0

    def append_then_fail_replay(command: Any) -> Any:
        nonlocal append_calls
        append_calls += 1
        if append_calls == 1:
            return real_append(command)
        raise FactStreamError(
            "simulated ambiguous replay proof failure",
            code="append_write_failed",
            details={"phase": "reconcile_exact_replay"},
        )

    def fail_after_real_commit(receipt: Any) -> None:
        del receipt
        raise FactStreamError(
            "simulated acknowledgement loss after durable append",
            code="append_write_failed",
            details={"phase": "after_commit"},
        )

    monkeypatch.setattr(deo_internal, "append_if_guarded_snapshot", append_then_fail_replay)
    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(fail_after_real_commit),
    )

    result = seal_directed_effect_inventory(_runtime_seal_command(identity, binding))

    assert result.ok is False
    assert result.code == "guarded_receipt_mismatch"
    assert append_calls == 2


def test_seal_inventory_rejects_post_append_guard_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding = _runtime_parent(tmp_path)

    def corrupt_guard_after_commit(receipt: Any) -> Any:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=identity.workspace,
                stream=binding.operation_stream_token,
                event_type="test.directed_effect_operation.v1.unknown_after_seal",
                payload={"test_marker": "post-append-guard-corruption"},
                source="test",
                idempotency_key="post-append-guard-corruption",
                expected_seq=1,
                durability="fsync",
                strict_integrity=True,
            )
        )
        return receipt

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(corrupt_guard_after_commit),
    )

    result = seal_directed_effect_inventory(_runtime_seal_command(identity, binding))

    assert result.ok is False
    assert result.code in {"strict_stream_unknown_schema", "strict_stream_corruption"}
    assert result.code != "inventory_sealed"


def test_seal_inventory_reprepare_exhaustion_leaves_registry_without_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding = _runtime_parent(tmp_path)
    append_calls = 0

    def always_drift(command: Any) -> None:
        nonlocal append_calls
        del command
        append_calls += 1
        raise FactStreamError(
            "simulated target snapshot drift",
            code="target_snapshot_drift",
            details={"phase": "commit"},
        )

    monkeypatch.setattr(deo_internal, "append_if_guarded_snapshot", always_drift)

    result = seal_directed_effect_inventory(_runtime_seal_command(identity, binding))

    assert result.ok is False
    assert result.code == "guarded_reprepare_exhausted"
    assert append_calls == 3
    assert len(_runtime_events(identity.workspace, binding.registry_stream_token)) == 1


def test_inventory_projection_accepts_partial_admission_and_is_frozen(tmp_path: Path) -> None:
    projection = _inventory_projection(tmp_path)

    assert projection.schema_version == DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1
    assert projection.members == _members(2)
    assert projection.inventory_ready is False
    assert projection.ready_event_id is None
    assert projection.ready_event_seq is None
    assert projection.admitted_count == 1
    assert projection.missing_operation_ids == ("operation-1",)
    assert projection.unexpected_operation_ids == ()
    with pytest.raises(FrozenInstanceError):
        projection.inventory_ready = True  # type: ignore[misc]


def test_inventory_projection_accepts_complete_ready_admission(tmp_path: Path) -> None:
    projection = _inventory_projection(tmp_path, ready=True)

    assert projection.inventory_ready is True
    assert projection.ready_event_id == "inventory-ready-3"
    assert projection.ready_event_seq == 3
    assert projection.admitted_count == len(projection.members)
    assert projection.missing_operation_ids == ()
    assert projection.unexpected_operation_ids == ()


@pytest.mark.parametrize(
    "mismatch",
    ("workspace", "task_id", "attempt_workspace", "attempt_task"),
)
def test_inventory_projection_rejects_workspace_task_identity_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    projection = _inventory_projection(tmp_path)
    other_workspace = str((tmp_path / "other-workspace").resolve())

    with pytest.raises(ValueError):
        if mismatch == "workspace":
            replace(projection, workspace=other_workspace)
        elif mismatch == "task_id":
            replace(projection, task_id=projection.task_id + 1)
        elif mismatch == "attempt_workspace":
            replace(
                projection,
                execution_attempt=replace(projection.execution_attempt, workspace=other_workspace),
            )
        else:
            replace(
                projection,
                execution_attempt=replace(
                    projection.execution_attempt,
                    task_id=projection.task_id + 1,
                ),
            )


@pytest.mark.parametrize(
    "violation",
    (
        "non_tuple",
        "empty",
        "oversized",
        "wrong_member_type",
        "ordinal_gap",
        "duplicate_tool_call_id",
        "duplicate_effect_id",
        "duplicate_operation_id",
    ),
)
def test_inventory_projection_rejects_invalid_member_inventory(
    tmp_path: Path,
    violation: str,
) -> None:
    projection = _inventory_projection(tmp_path)
    members = projection.members

    with pytest.raises((TypeError, ValueError)):
        if violation == "non_tuple":
            replace(
                projection,
                members=cast(tuple[DirectedEffectInventoryMemberV1, ...], list(members)),
            )
        elif violation == "empty":
            replace(projection, members=())
        elif violation == "oversized":
            replace(projection, members=_members(65))
        elif violation == "wrong_member_type":
            replace(
                projection,
                members=cast(tuple[DirectedEffectInventoryMemberV1, ...], (_intent(),)),
            )
        elif violation == "ordinal_gap":
            replace(projection, members=(members[0], replace(members[1], ordinal=2)))
        elif violation == "duplicate_tool_call_id":
            replace(
                projection,
                members=(members[0], replace(members[1], tool_call_id=members[0].tool_call_id)),
            )
        elif violation == "duplicate_effect_id":
            replace(
                projection,
                members=(members[0], replace(members[1], effect_id=members[0].effect_id)),
            )
        else:
            replace(
                projection,
                members=(members[0], replace(members[1], operation_id=members[0].operation_id)),
            )


@pytest.mark.parametrize("inventory_hash", ("A" * 64, "4" * 63, "g" * 64))
def test_inventory_projection_rejects_noncanonical_inventory_digest(
    tmp_path: Path,
    inventory_hash: str,
) -> None:
    with pytest.raises(ValueError):
        replace(_inventory_projection(tmp_path), inventory_hash=inventory_hash)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("sealed_event_id", ""),
        ("sealed_event_id", 1),
        ("sealed_event_seq", 0),
        ("sealed_event_seq", True),
        ("parent_registry_source_head_seq", -1),
        ("parent_registry_source_head_seq", True),
        ("operation_source_head_seq", -1),
        ("operation_source_head_seq", True),
    ),
)
def test_inventory_projection_rejects_invalid_event_and_head_boundaries(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(_inventory_projection(tmp_path), **{field_name: value})


@pytest.mark.parametrize(
    "violation",
    (
        "wrong_admitted_count",
        "missing_non_member",
        "missing_non_tuple",
        "unexpected_duplicate",
        "unexpected_member_overlap",
        "unexpected_non_tuple",
    ),
)
def test_inventory_projection_rejects_invalid_admission_accounting(
    tmp_path: Path,
    violation: str,
) -> None:
    projection = _inventory_projection(tmp_path)

    with pytest.raises((TypeError, ValueError)):
        if violation == "wrong_admitted_count":
            replace(projection, admitted_count=0)
        elif violation == "missing_non_member":
            replace(projection, missing_operation_ids=("not-a-member",))
        elif violation == "missing_non_tuple":
            replace(
                projection,
                missing_operation_ids=cast(tuple[str, ...], ["operation-1"]),
            )
        elif violation == "unexpected_duplicate":
            replace(
                projection,
                unexpected_operation_ids=("unexpected-1", "unexpected-1"),
            )
        elif violation == "unexpected_member_overlap":
            replace(projection, unexpected_operation_ids=("operation-0",))
        else:
            replace(
                projection,
                unexpected_operation_ids=cast(tuple[str, ...], ["unexpected-1"]),
            )


@pytest.mark.parametrize(
    "case",
    ("partial_admitted", "ready_admitted", "nonready_unexpected"),
)
def test_inventory_projection_rejects_operation_head_lower_bound(
    tmp_path: Path,
    case: str,
) -> None:
    partial = _inventory_projection(tmp_path)
    ready = _inventory_projection(tmp_path, ready=True)

    with pytest.raises(ValueError):
        if case == "partial_admitted":
            replace(partial, operation_source_head_seq=0)
        elif case == "ready_admitted":
            replace(ready, operation_source_head_seq=1)
        else:
            replace(
                partial,
                operation_source_head_seq=1,
                unexpected_operation_ids=("unexpected-1",),
            )


@pytest.mark.parametrize(
    "violation",
    (
        "ready_missing_event_id",
        "ready_missing_event_seq",
        "ready_with_missing",
        "ready_with_unexpected",
        "nonready_with_event_id",
        "nonready_with_event_seq",
    ),
)
def test_inventory_projection_rejects_invalid_ready_pairing(
    tmp_path: Path,
    violation: str,
) -> None:
    partial = _inventory_projection(tmp_path)
    ready = _inventory_projection(tmp_path, ready=True)

    with pytest.raises(ValueError):
        if violation == "ready_missing_event_id":
            replace(ready, ready_event_id=None)
        elif violation == "ready_missing_event_seq":
            replace(ready, ready_event_seq=None)
        elif violation == "ready_with_missing":
            replace(
                ready,
                admitted_count=1,
                missing_operation_ids=("operation-1",),
            )
        elif violation == "ready_with_unexpected":
            replace(ready, unexpected_operation_ids=("unexpected-1",))
        elif violation == "nonready_with_event_id":
            replace(partial, ready_event_id="inventory-ready-3")
        else:
            replace(partial, ready_event_seq=3)


def test_claim_grant_has_exact_record_hash_and_is_frozen(tmp_path: Path) -> None:
    grant = _grant(tmp_path)
    unsigned_record = _grant_unsigned_record(
        execution_attempt=grant.execution_attempt,
        parent_binding=grant.parent_binding,
        operation=grant.operation,
        member=grant.member,
    )
    expected_hash = _canonical_sha256(unsigned_record)

    assert grant.grant_hash == expected_hash
    assert grant.to_record() == {**unsigned_record, "grant_hash": expected_hash}
    with pytest.raises(FrozenInstanceError):
        grant.operation_version = 3  # type: ignore[misc]


@pytest.mark.parametrize("grant_hash", ("A" * 64, "f" * 63, "g" * 64, "0" * 64))
def test_claim_grant_rejects_noncanonical_or_wrong_hash(
    tmp_path: Path,
    grant_hash: str,
) -> None:
    with pytest.raises(ValueError):
        replace(_grant(tmp_path), grant_hash=grant_hash)


@pytest.mark.parametrize(
    "changed_field",
    (
        "execution_attempt",
        "parent_binding",
        "operation",
        "member",
        "inventory_hash",
        "operation_version",
        "claim_event_id",
        "claim_and_operation_seq",
        "parent_registry_source_head_seq",
    ),
)
def test_claim_grant_hash_binds_each_unsigned_record_component(
    tmp_path: Path,
    changed_field: str,
) -> None:
    grant = _grant(tmp_path)

    with pytest.raises(ValueError):
        if changed_field == "execution_attempt":
            replace(
                grant,
                execution_attempt=replace(
                    grant.execution_attempt,
                    lease_expires_at="2026-07-16T13:00:00+00:00",
                ),
            )
        elif changed_field == "parent_binding":
            replace(grant, parent_binding=replace(grant.parent_binding, actor="other-actor"))
        elif changed_field == "operation":
            replace(
                grant,
                operation=replace(grant.operation, operation_id="operation-2"),
                member=replace(grant.member, operation_id="operation-2"),
            )
        elif changed_field == "member":
            replace(grant, member=replace(grant.member, normalized_tool_name="edit_file"))
        elif changed_field == "inventory_hash":
            replace(grant, inventory_hash="5" * 64)
        elif changed_field == "operation_version":
            replace(grant, operation_version=3)
        elif changed_field == "claim_event_id":
            replace(grant, claim_event_id="claim-event-3")
        elif changed_field == "claim_and_operation_seq":
            replace(grant, claim_event_seq=3, operation_source_head_seq=3)
        else:
            replace(grant, parent_registry_source_head_seq=4)


@pytest.mark.parametrize(
    "field_name",
    ("execution_attempt", "parent_binding", "operation", "member"),
)
def test_claim_grant_requires_exact_nested_contract_types(
    tmp_path: Path,
    field_name: str,
) -> None:
    grant = _grant(tmp_path)

    with pytest.raises(TypeError):
        replace(grant, **{field_name: cast(Any, object())})


@pytest.mark.parametrize(
    "mismatch",
    (
        "attempt_binding",
        "operation_workspace",
        "operation_task",
        "operation_attempt",
        "operation_binding",
        "operation_parent_sequence",
        "operation_stream",
        "operation_call",
        "operation_effect",
        "operation_id",
    ),
)
def test_claim_grant_rejects_cross_identity_with_valid_recomputed_hash(
    tmp_path: Path,
    mismatch: str,
) -> None:
    grant = _grant(tmp_path)
    changes: dict[str, object]
    if mismatch == "attempt_binding":
        changes = {
            "execution_attempt": replace(grant.execution_attempt, session_id="other-session"),
        }
    else:
        operation_changes: dict[str, object]
        if mismatch == "operation_workspace":
            operation_changes = {"workspace": str((tmp_path / "other").resolve())}
        elif mismatch == "operation_task":
            operation_changes = {"task_id": grant.operation.task_id + 1}
        elif mismatch == "operation_attempt":
            operation_changes = {"execution_attempt_id": "other-session:1"}
        elif mismatch == "operation_binding":
            operation_changes = {"parent_binding_id": "other-binding"}
        elif mismatch == "operation_parent_sequence":
            operation_changes = {"parent_sequence": grant.operation.parent_sequence + 1}
        elif mismatch == "operation_stream":
            operation_changes = {"operation_stream_token": "other-operation-stream"}
        elif mismatch == "operation_call":
            operation_changes = {"tool_call_id": "other-call"}
        elif mismatch == "operation_effect":
            operation_changes = {"effect_id": "other-effect"}
        else:
            operation_changes = {"operation_id": "other-operation"}
        changes = {"operation": replace(grant.operation, **operation_changes)}

    with pytest.raises(ValueError):
        replace(grant, **changes, grant_hash=_grant_hash_after(grant, **changes))


@pytest.mark.parametrize(
    "violation",
    (
        "schema",
        "inventory_hash",
        "operation_version",
        "claim_event_id",
        "claim_event_seq",
        "claim_operation_seq_mismatch",
        "parent_registry_head",
    ),
)
def test_claim_grant_rejects_locked_schema_digest_version_and_heads(
    tmp_path: Path,
    violation: str,
) -> None:
    grant = _grant(tmp_path)

    with pytest.raises((TypeError, ValueError)):
        if violation == "schema":
            schema = "task-runtime.directed-effect-claim-grant/2"
            replace(
                grant,
                schema_version=schema,
                grant_hash=_grant_hash_after(grant, schema_version=schema),
            )
        elif violation == "inventory_hash":
            replace(grant, inventory_hash="A" * 64)
        elif violation == "operation_version":
            replace(grant, operation_version=1)
        elif violation == "claim_event_id":
            replace(grant, claim_event_id="")
        elif violation == "claim_event_seq":
            replace(grant, claim_event_seq=0, operation_source_head_seq=0)
        elif violation == "claim_operation_seq_mismatch":
            replace(grant, operation_source_head_seq=3)
        else:
            replace(grant, parent_registry_source_head_seq=0)


@pytest.mark.parametrize(
    ("registry_head_offset", "accepted"),
    ((0, False), (1, False), (2, True)),
)
def test_claim_grant_registry_ready_head_boundary(
    tmp_path: Path,
    registry_head_offset: int,
    accepted: bool,
) -> None:
    grant = _grant(tmp_path)
    registry_head = grant.parent_binding.source_event_seq + registry_head_offset
    changes = {"parent_registry_source_head_seq": registry_head}
    grant_hash = _grant_hash_after(grant, **changes)

    if accepted:
        observed = replace(grant, **changes, grant_hash=grant_hash)
        assert observed.parent_registry_source_head_seq == grant.parent_binding.source_event_seq + 2
    else:
        with pytest.raises(ValueError):
            replace(grant, **changes, grant_hash=grant_hash)


def test_task5_operation_result_effect_claimed_requires_valid_grant(tmp_path: Path) -> None:
    grant = _grant(tmp_path)
    with_grant = DirectedEffectOperationResultV1(
        ok=True,
        code="effect_claimed",
        operation=grant.operation,
        state="EFFECT_STARTED",
        version=grant.operation_version,
        claim_grant=grant,
    )

    assert with_grant.claim_grant is grant
    with pytest.raises(ValueError):
        DirectedEffectOperationResultV1(
            ok=True,
            code="effect_claimed",
            operation=grant.operation,
            state="EFFECT_STARTED",
            version=grant.operation_version,
        )


@pytest.mark.parametrize("mismatch", ("operation", "state", "version"))
