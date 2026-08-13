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






def test_operation_result_rejects_mismatched_claim_grant(
    tmp_path: Path,
    mismatch: str,
) -> None:
    grant = _grant(tmp_path)
    operation = grant.operation
    state = "EFFECT_STARTED"
    version = grant.operation_version
    if mismatch == "operation":
        operation = replace(operation, operation_id="other-operation")
    elif mismatch == "state":
        state = "INTENT_COMMITTED"
    else:
        version += 1

    with pytest.raises(ValueError):
        DirectedEffectOperationResultV1(
            ok=True,
            code="effect_claimed",
            operation=operation,
            state=cast(Any, state),
            version=version,
            claim_grant=grant,
        )


@pytest.mark.parametrize(
    ("code", "state", "version", "idempotent"),
    (
        ("admitted", "INTENT_COMMITTED", 1, False),
        ("aborted", "ABORTED", 2, False),
        ("idempotent_replay", "EFFECT_STARTED", 2, True),
    ),
)
def test_operation_result_non_claim_and_replay_codes_never_carry_grant(
    tmp_path: Path,
    code: str,
    state: str,
    version: int,
    idempotent: bool,
) -> None:
    grant = _grant(tmp_path)

    with pytest.raises(ValueError):
        DirectedEffectOperationResultV1(
            ok=True,
            code=cast(Any, code),
            operation=grant.operation,
            state=cast(Any, state),
            version=version,
            idempotent=idempotent,
            claim_grant=grant,
        )


def test_operation_result_idempotent_replay_has_no_grant(tmp_path: Path) -> None:
    grant = _grant(tmp_path)
    replay = DirectedEffectOperationResultV1(
        ok=True,
        code="idempotent_replay",
        operation=grant.operation,
        state="EFFECT_STARTED",
        version=grant.operation_version,
        idempotent=True,
    )

    assert replay.claim_grant is None


def test_task4_admission_before_seal_fails_without_any_stream_append(tmp_path: Path) -> None:
    identity, binding = _runtime_parent(tmp_path)
    intent = _intent(contingency_kind=None)
    member_record, _inventory_hash = _expected_runtime_inventory(binding, intent)
    member = DirectedEffectInventoryMemberV1.from_record(member_record)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = admit_directed_effect_operation(_runtime_admission_command(identity, binding, member))

    assert rejected.ok is False
    assert rejected.code == "inventory_not_sealed"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize("unknown_field", ("tool_call_id", "effect_id", "operation_id"))
def test_task4_admission_requires_exact_sealed_member_identity_without_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unknown_field: str,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    member = sealed.members[0]
    command = _runtime_admission_command(identity, binding, member)
    if unknown_field == "tool_call_id":
        command = replace(command, tool_call_id="unknown-call")
    elif unknown_field == "effect_id":
        command = replace(command, effect_id="unknown-effect")
    else:
        original_derive = deo_internal.DirectedEffectOperationRepository._derive_operation

        def derive_unknown_operation(
            requested: object,
            durable_binding: DirectedEffectParentBindingV1,
        ) -> DirectedEffectOperationIdentityV1:
            operation = original_derive(requested, durable_binding)
            return replace(operation, operation_id=f"{operation.operation_id}-unknown")

        monkeypatch.setattr(
            deo_internal.DirectedEffectOperationRepository,
            "_derive_operation",
            staticmethod(derive_unknown_operation),
        )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = admit_directed_effect_operation(command)

    assert rejected.ok is False
    assert rejected.code == "inventory_member_not_found"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize(
    "semantic_field",
    (
        "intended_effect_fingerprint",
        "policy_verdict_hash",
        "expected_receipt_binding_hash",
    ),
)
def test_task4_admission_rejects_each_sealed_semantic_hash_mismatch_without_append(
    tmp_path: Path,
    semantic_field: str,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    command = replace(
        _runtime_admission_command(identity, binding, sealed.members[0]),
        **{semantic_field: "f" * 64},
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = admit_directed_effect_operation(command)

    assert rejected.ok is False
    assert rejected.code == "inventory_member_conflict"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_partial_admission_query_and_finalize_fail_closed_without_append(
    tmp_path: Path,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path, intents=_intents(2))
    first = admit_directed_effect_operation(_runtime_admission_command(identity, binding, sealed.members[0]))
    assert first.code == "admitted"

    observed = _runtime_get_inventory(_runtime_inventory_query(identity, binding))

    assert observed.ok is True
    assert observed.code == "inventory_observed"
    assert observed.projection is not None
    assert observed.projection.inventory_ready is False
    assert observed.projection.admitted_count == 1
    assert observed.projection.missing_operation_ids == (sealed.members[1].operation_id,)
    assert observed.projection.unexpected_operation_ids == ()
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_finalize_inventory(
        _runtime_finalize_command(
            identity,
            binding,
            sealed,
            expected_operation_head_seq=1,
        )
    )

    assert rejected.ok is False
    assert rejected.code == "inventory_admission_incomplete"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize("duplicate_call_identity", (False, True))
def test_task4_finalize_rejects_unexpected_or_duplicate_call_admission_without_append(
    tmp_path: Path,
    duplicate_call_identity: bool,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    member = sealed.members[0]
    assert admit_directed_effect_operation(_runtime_admission_command(identity, binding, member)).code == "admitted"
    unexpected = replace(
        _runtime_admission_command(identity, binding, member, expected_seq=2),
        tool_call_id=member.tool_call_id if duplicate_call_identity else "unexpected-call",
        effect_id="unexpected-effect",
    )
    _append_operation_transition_for_inventory_test(
        unexpected,
        state="INTENT_COMMITTED",
        kind="admit",
        previous_version=0,
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_finalize_inventory(
        _runtime_finalize_command(
            identity,
            binding,
            sealed,
            expected_operation_head_seq=2,
        )
    )

    assert rejected.ok is False
    assert rejected.code == "inventory_admission_unexpected"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_finalize_rejects_member_in_wrong_operation_state_without_append(
    tmp_path: Path,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    member = sealed.members[0]
    assert admit_directed_effect_operation(_runtime_admission_command(identity, binding, member)).code == "admitted"
    claim = ClaimDirectedEffectCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        expected_version=1,
        expected_seq=2,
        actor="task-runtime-inventory-test",
        intended_effect_fingerprint=member.intended_effect_fingerprint,
        policy_verdict_hash=member.policy_verdict_hash,
        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
    )
    _append_operation_transition_for_inventory_test(
        claim,
        state="EFFECT_STARTED",
        kind="claim",
        previous_version=1,
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_finalize_inventory(
        _runtime_finalize_command(
            identity,
            binding,
            sealed,
            expected_operation_head_seq=2,
        )
    )

    assert rejected.ok is False
    assert rejected.code == "inventory_admission_incomplete"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_finalize_rejects_stale_operation_head_without_append(tmp_path: Path) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    assert (
        admit_directed_effect_operation(_runtime_admission_command(identity, binding, sealed.members[0])).code
        == "admitted"
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_finalize_inventory(
        _runtime_finalize_command(
            identity,
            binding,
            sealed,
            expected_operation_head_seq=2,
        )
    )

    assert rejected.ok is False
    assert rejected.code == "stream_expected_seq_conflict"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_finalize_writes_exact_ready_proof_and_exact_replay_is_append_free(
    tmp_path: Path,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path, intents=_intents(2))
    for expected_seq, member in enumerate(sealed.members, start=1):
        assert (
            admit_directed_effect_operation(
                _runtime_admission_command(
                    identity,
                    binding,
                    member,
                    expected_seq=expected_seq,
                )
            ).code
            == "admitted"
        )
    command = _runtime_finalize_command(
        identity,
        binding,
        sealed,
        expected_operation_head_seq=2,
    )
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    ready = _runtime_finalize_inventory(command)

    assert ready.ok is True
    assert ready.code == "inventory_ready"
    assert ready.projection is not None
    assert ready.projection.inventory_ready is True
    assert ready.projection.members == sealed.members
    assert ready.projection.admitted_count == 2
    assert ready.projection.missing_operation_ids == ()
    assert ready.projection.unexpected_operation_ids == ()
    assert ready.projection.operation_source_head_seq == 2
    registry_after_ready = _runtime_events(identity.workspace, binding.registry_stream_token)
    assert len(registry_after_ready) == 3
    ready_fact = registry_after_ready[-1]
    assert ready_fact["event_type"] == deo_internal._PARENT_INVENTORY_READY_EVENT_TYPE
    ready_payload = ready_fact["payload"]
    assert isinstance(ready_payload, Mapping)
    assert ready_payload["inventory_hash"] == sealed.inventory_hash
    assert ready_payload["ordered_operation_ids"] == [member.operation_id for member in sealed.members]
    assert ready_payload["operation_source_head_seq"] == 2
    assert isinstance(ready_payload["admission_set_hash"], str)
    assert len(ready_payload["admission_set_hash"]) == 64
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before

    replay = _runtime_finalize_inventory(command)

    assert replay.ok is True
    assert replay.code == "inventory_ready_idempotent_replay"
    assert replay.projection == ready.projection
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_after_ready
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_query_after_ready_rejects_new_unexpected_admission_without_mutation(
    tmp_path: Path,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    member = sealed.members[0]
    assert admit_directed_effect_operation(_runtime_admission_command(identity, binding, member)).code == "admitted"
    assert (
        _runtime_finalize_inventory(
            _runtime_finalize_command(
                identity,
                binding,
                sealed,
                expected_operation_head_seq=1,
            )
        ).code
        == "inventory_ready"
    )
    unexpected = replace(
        _runtime_admission_command(identity, binding, member, expected_seq=2),
        tool_call_id="post-ready-unexpected-call",
        effect_id="post-ready-unexpected-effect",
    )
    unexpected_operation = _append_operation_transition_for_inventory_test(
        unexpected,
        state="INTENT_COMMITTED",
        kind="admit",
        previous_version=0,
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_get_inventory(_runtime_inventory_query(identity, binding))

    assert rejected.ok is False
    assert rejected.code == "inventory_admission_unexpected"
    assert rejected.projection is None
    assert rejected.evidence["reason"] == "unexpected_operation_after_inventory_ready"
    assert rejected.evidence["unexpected_operation_ids"] == (unexpected_operation.operation_id,)
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize(
    ("tamper", "payload_changes", "expected_reason"),
    (
        (
            "actor",
            {"actor": "not-roles-kernel"},
            "parent_inventory_ready_actor_invalid",
        ),
        (
            "operation_head",
            {"operation_source_head_seq": 2},
            "parent_inventory_ready_operation_head_not_exact_admission_set",
        ),
    ),
)
def test_task4_query_rejects_tampered_ready_actor_or_operation_head_without_mutation(
    tmp_path: Path,
    tamper: str,
    payload_changes: Mapping[str, object],
    expected_reason: str,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    assert (
        admit_directed_effect_operation(_runtime_admission_command(identity, binding, sealed.members[0])).code
        == "admitted"
    )
    operation_head = cast(int | None, payload_changes.get("operation_source_head_seq"))
    payload = _direct_ready_payload(
        binding,
        sealed,
        actor=cast(str, payload_changes.get("actor", "roles.kernel")),
        operation_source_head_seq=operation_head,
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            event_type=deo_internal._PARENT_INVENTORY_READY_EVENT_TYPE,
            payload=payload,
            source="test",
            idempotency_key=f"task4-tampered-ready-{tamper}",
            expected_seq=3,
            durability="fsync",
            strict_integrity=True,
        )
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_get_inventory(_runtime_inventory_query(identity, binding))

    assert rejected.ok is False
    assert rejected.code == "strict_stream_corruption"
    assert rejected.projection is None
    assert rejected.evidence["reason"] == expected_reason
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_query_strictly_rejects_duplicate_ready_fact_without_append(tmp_path: Path) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    assert (
        admit_directed_effect_operation(_runtime_admission_command(identity, binding, sealed.members[0])).code
        == "admitted"
    )
    assert (
        _runtime_finalize_inventory(
            _runtime_finalize_command(
                identity,
                binding,
                sealed,
                expected_operation_head_seq=1,
            )
        ).code
        == "inventory_ready"
    )
    ready_fact = _runtime_events(identity.workspace, binding.registry_stream_token)[-1]
    duplicate_payload = dict(cast(Mapping[str, object], ready_fact["payload"]))
    duplicate_payload["previous_version"] = 3
    duplicate_payload["version"] = 4
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.registry_stream_token,
            event_type=deo_internal._PARENT_INVENTORY_READY_EVENT_TYPE,
            payload=duplicate_payload,
            source="test",
            idempotency_key="task4-duplicate-ready-corruption",
            expected_seq=4,
            durability="fsync",
            strict_integrity=True,
        )
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_get_inventory(_runtime_inventory_query(identity, binding))

    assert rejected.ok is False
    assert rejected.code == "strict_stream_corruption"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize(
    ("public_name", "expected_type_name"),
    (
        (
            "finalize_directed_effect_inventory_admission",
            "FinalizeDirectedEffectInventoryAdmissionCommandV1",
        ),
        ("get_directed_effect_inventory", "GetDirectedEffectInventoryQueryV1"),
    ),
)
def test_task4_public_finalize_and_query_require_exact_contract_types(
    public_name: str,
    expected_type_name: str,
) -> None:
    assert public_name in task_runtime_public.__all__
    public_call = getattr(task_runtime_public, public_name, None)
    assert callable(public_call), f"{public_name} must be publicly callable"

    with pytest.raises(TypeError, match=expected_type_name):
        public_call(object())


@pytest.mark.parametrize("stage", ("seal", "finalize"))
def test_task4_concurrent_exact_inventory_mutation_converges_to_fresh_and_typed_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    if stage == "seal":
        identity, binding = _runtime_parent(tmp_path)
        command: object = _runtime_seal_command(identity, binding)
        invoke = seal_directed_effect_inventory
        expected_codes = {"inventory_sealed", "inventory_seal_idempotent_replay"}
        registry_events_before = 1
    else:
        identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
        invoke = _runtime_finalize_inventory
        expected_codes = {"inventory_ready", "inventory_ready_idempotent_replay"}
        registry_events_before = 2
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

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(invoke, (command, command)))

    assert {result.code for result in results} == expected_codes
    assert all(result.ok for result in results)
    assert all(result.code != "idempotency_semantic_conflict" for result in results)
    assert len(_runtime_events(identity.workspace, binding.registry_stream_token)) == registry_events_before + 1
    expected_operation_events = 0 if stage == "seal" else 1
    assert len(_runtime_events(identity.workspace, binding.operation_stream_token)) == expected_operation_events


@pytest.mark.parametrize("stage", ("seal", "finalize"))
def test_task4_fixed_timestamp_concurrent_exact_mutation_is_one_fresh_and_one_typed_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    if stage == "seal":
        identity, binding = _runtime_parent(tmp_path)
        command: object = _runtime_seal_command(identity, binding)
        invoke = seal_directed_effect_inventory
        fresh_code = "inventory_sealed"
        replay_code = "inventory_seal_idempotent_replay"
        registry_events_before = 1
    else:
        identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
        invoke = _runtime_finalize_inventory
        fresh_code = "inventory_ready"
        replay_code = "inventory_ready_idempotent_replay"
        registry_events_before = 2
    fixed_timestamp = "2026-07-16T12:34:56.123456+00:00"
    fixed_instant = datetime.fromisoformat(fixed_timestamp)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> FixedDateTime:
            assert tz is not None
            return cls.fromtimestamp(fixed_instant.timestamp(), cast(Any, tz))

    monkeypatch.setattr(deo_internal, "datetime", FixedDateTime)
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

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(invoke, (command, command)))

    codes = tuple(result.code for result in results)
    assert all(code != "guarded_receipt_mismatch" for code in codes)
    assert all(code != "idempotency_semantic_conflict" for code in codes)
    registry_events = _runtime_events(identity.workspace, binding.registry_stream_token)
    assert len(registry_events) == registry_events_before + 1
    assert registry_events[-1]["payload"]["recorded_at"] == fixed_timestamp
    expected_operation_events = 0 if stage == "seal" else 1
    assert len(_runtime_events(identity.workspace, binding.operation_stream_token)) == expected_operation_events
    assert codes.count(fresh_code) == 1
    assert codes.count(replay_code) == 1


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
def test_task4_finalize_rejects_each_tampered_public_guarded_receipt_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_field: str,
) -> None:
    identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

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

    rejected = _runtime_finalize_inventory(command)

    assert rejected.ok is False
    assert rejected.code == "guarded_receipt_mismatch"
    assert len(_runtime_events(identity.workspace, binding.registry_stream_token)) == 3
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_finalize_receipt_confirmation_requires_canonical_exact_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
    real_append = deo_internal.append_if_guarded_snapshot
    append_calls = 0

    def append_then_fail_exact_replay(guarded_command: Any) -> Any:
        nonlocal append_calls
        append_calls += 1
        if append_calls == 1:
            return real_append(guarded_command)
        raise FactStreamError(
            "simulated finalize exact replay failure",
            code="append_write_failed",
            details={"phase": "finalize_confirm_exact_replay"},
        )

    monkeypatch.setattr(deo_internal, "append_if_guarded_snapshot", append_then_fail_exact_replay)

    rejected = _runtime_finalize_inventory(command)

    assert rejected.ok is False
    assert rejected.code == "guarded_receipt_mismatch"
    assert rejected.evidence["reason"] == "public_exact_replay_receipt_failed"
    assert append_calls == 2
    assert len(_runtime_events(identity.workspace, binding.registry_stream_token)) == 3


def test_task4_finalize_reconciles_ambiguous_error_after_durable_ready_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
    real_append = deo_internal.append_if_guarded_snapshot
    append_calls = 0

    def durable_append_then_acknowledgement_loss(guarded_command: Any) -> Any:
        nonlocal append_calls
        append_calls += 1
        if append_calls == 1:
            real_append(guarded_command)
            raise FactStreamError(
                "simulated acknowledgement loss after durable ready",
                code="append_write_failed",
                details={"phase": "after_durable_ready"},
            )
        return real_append(guarded_command)

    monkeypatch.setattr(
        deo_internal,
        "append_if_guarded_snapshot",
        durable_append_then_acknowledgement_loss,
    )

    reconciled = _runtime_finalize_inventory(command)

    assert reconciled.ok is True
    assert reconciled.code == "inventory_ready"
    assert reconciled.evidence["reconciled_after_guarded_error"] is True
    assert reconciled.evidence["fact_stream_code"] == "append_write_failed"
    assert reconciled.evidence["fact_stream_details"] == {"phase": "after_durable_ready"}
    assert append_calls == 2
    assert len(_runtime_events(identity.workspace, binding.registry_stream_token)) == 3


@pytest.mark.parametrize(
    ("fact_stream_code", "expected_code"),
    (
        ("append_write_failed", "stream_append_failed"),
        ("unmapped_append_failure", "fact_stream_unknown_failure"),
        # R149: KernelOne flock deadline must not collapse to opaque unknown.
        ("lock_acquisition_timeout", "stream_lock_timeout"),
        ("file_lock_timeout", "stream_lock_timeout"),
        ("lock_timeout", "stream_lock_timeout"),
    ),
)
def test_task4_finalize_preserves_no_durable_append_failure_taxonomy_without_ready_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fact_stream_code: str,
    expected_code: str,
) -> None:
    identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    def fail_without_durable_append(_guarded_command: Any) -> None:
        raise FactStreamError(
            "simulated no-durable finalize append failure",
            code=fact_stream_code,
            details={"phase": "before_durable_ready"},
        )

    monkeypatch.setattr(deo_internal, "append_if_guarded_snapshot", fail_without_durable_append)

    rejected = _runtime_finalize_inventory(command)

    assert rejected.ok is False
    assert rejected.code == expected_code
    assert rejected.evidence["fact_stream_code"] == fact_stream_code
    assert rejected.evidence["fact_stream_details"] == {"phase": "before_durable_ready"}
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize("drift_code", ("target_snapshot_drift", "guard_snapshot_drift"))
def test_task4_finalize_exhausts_target_or_guard_drift_without_ready_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_code: str,
) -> None:
    identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)
    append_calls = 0

    def always_drift(_guarded_command: Any) -> None:
        nonlocal append_calls
        append_calls += 1
        raise FactStreamError(
            "simulated finalize guarded drift",
            code=drift_code,
            details={"phase": "commit"},
        )

    monkeypatch.setattr(deo_internal, "append_if_guarded_snapshot", always_drift)

    rejected = _runtime_finalize_inventory(command)

    assert rejected.ok is False
    assert rejected.code == "guarded_reprepare_exhausted"
    assert rejected.evidence["drift_codes"] == (drift_code, drift_code, drift_code)
    assert append_calls == 3
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_finalize_revalidates_authority_after_guarded_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)
    original_validate = deo_internal.DirectedEffectOperationRepository.validate_attempt
    authority_revoked = False
    append_calls = 0

    def validate_attempt(
        repository: Any,
        workspace: str,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectOperationResultV1 | None:
        if authority_revoked:
            return DirectedEffectOperationResultV1(ok=False, code="session_not_active")
        return original_validate(repository, workspace, execution_attempt)

    def drift_once(_guarded_command: Any) -> None:
        nonlocal append_calls
        append_calls += 1
        raise FactStreamError(
            "simulated drift before authority revocation",
            code="target_snapshot_drift",
            details={"phase": "commit"},
        )

    def revoke_after_drift(_exc: FactStreamError, _attempt_number: int) -> None:
        nonlocal authority_revoked
        authority_revoked = True

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "validate_attempt",
        validate_attempt,
    )
    monkeypatch.setattr(deo_internal, "append_if_guarded_snapshot", drift_once)
    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_drift",
        staticmethod(revoke_after_drift),
    )

    rejected = _runtime_finalize_inventory(command)

    assert rejected.ok is False
    assert rejected.code == "session_not_active"
    assert rejected.evidence["guarded_authority_phase"] == "reprepare"
    assert rejected.evidence["drift_codes"] == ("target_snapshot_drift",)
    assert append_calls == 1
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize(
    ("tamper", "expected_code", "expected_reason"),
    (
        ("extra_field", "strict_stream_corruption", "parent_inventory_ready_payload_fields_invalid"),
        ("missing_field", "strict_stream_corruption", "parent_inventory_ready_payload_fields_invalid"),
        ("stable_identity", "execution_attempt_mismatch", None),
        ("previous_version", "strict_stream_corruption", "parent_registry_version_not_monotonic"),
        ("version", "strict_stream_corruption", "parent_registry_version_not_monotonic"),
        ("record_seq", "strict_stream_corruption", "parent_registry_version_not_monotonic"),
        ("inventory_hash", "strict_stream_corruption", "parent_inventory_ready_inventory_hash_mismatch"),
        ("operation_ids", "strict_stream_corruption", "parent_inventory_ready_operation_ids_invalid"),
        ("operation_order", "strict_stream_corruption", "parent_inventory_ready_operation_ids_invalid"),
        (
            "admission_hash",
            "strict_stream_corruption",
            "parent_inventory_ready_admission_set_hash_mismatch",
        ),
        ("naive_timestamp", "strict_stream_corruption", "parent_inventory_ready_recorded_at_invalid"),
    ),
)
def test_task4_query_fails_closed_for_ready_fact_tamper_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
    expected_code: str,
    expected_reason: str | None,
) -> None:
    identity, binding, sealed, _command = _runtime_ready_candidate(tmp_path, intents=_intents(2))
    payload = _direct_ready_payload(binding, sealed)
    if tamper == "extra_field":
        payload["unexpected"] = "field"
    elif tamper == "missing_field":
        payload.pop("actor")
    elif tamper == "stable_identity":
        stable_identity = dict(cast(Mapping[str, object], payload["stable_registry_identity"]))
        stable_identity["run_id"] = "tampered-run"
        payload["stable_registry_identity"] = stable_identity
    elif tamper == "previous_version":
        payload["previous_version"] = 1
    elif tamper == "version":
        payload["version"] = 4
    elif tamper == "inventory_hash":
        tampered_hash = "f" * 64 if sealed.inventory_hash != "f" * 64 else "e" * 64
        payload["inventory_hash"] = tampered_hash
        payload["admission_set_hash"] = _test_admission_set_hash(
            binding_id=binding.binding_id,
            inventory_hash=tampered_hash,
            ordered_operation_ids=tuple(member.operation_id for member in sealed.members),
            operation_source_head_seq=2,
        )
    elif tamper in {"operation_ids", "operation_order"}:
        observed_ids = tuple(member.operation_id for member in sealed.members)
        tampered_ids = (
            ("unexpected-operation-id", *observed_ids[1:])
            if tamper == "operation_ids"
            else tuple(reversed(observed_ids))
        )
        payload["ordered_operation_ids"] = list(tampered_ids)
        payload["admission_set_hash"] = _test_admission_set_hash(
            binding_id=binding.binding_id,
            inventory_hash=sealed.inventory_hash,
            ordered_operation_ids=tampered_ids,
            operation_source_head_seq=2,
        )
    elif tamper == "admission_hash":
        observed_hash = cast(str, payload["admission_set_hash"])
        payload["admission_set_hash"] = "f" * 64 if observed_hash != "f" * 64 else "e" * 64
    elif tamper == "naive_timestamp":
        payload["recorded_at"] = "2026-07-16T00:00:00"
    _append_direct_ready_fact(
        binding,
        payload,
        idempotency_key=f"task4-ready-tamper-matrix-{tamper}",
    )
    if tamper == "record_seq":
        real_read_snapshot = deo_internal.read_guarded_fact_snapshot

        def read_snapshot_with_forged_ready_seq(command: Any) -> Any:
            snapshot = real_read_snapshot(command)
            target_facts = list(snapshot.target_records())
            target_facts[-1]["seq"] = 4
            return replace(snapshot, target_facts=tuple(target_facts))

        monkeypatch.setattr(
            deo_internal,
            "read_guarded_fact_snapshot",
            read_snapshot_with_forged_ready_seq,
        )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_get_inventory(_runtime_inventory_query(identity, binding))

    assert rejected.ok is False
    assert rejected.code == expected_code
    assert rejected.projection is None
    if expected_reason is not None:
        assert rejected.evidence["reason"] == expected_reason
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task4_query_rejects_ready_fact_bound_to_forged_operation_prefix(
    tmp_path: Path,
) -> None:
    identity, binding, sealed = _runtime_sealed_parent(tmp_path)
    forged_admission = replace(
        _runtime_admission_command(identity, binding, sealed.members[0]),
        policy_verdict_hash="f" * 64,
    )
    _append_operation_transition_for_inventory_test(
        forged_admission,
        state="INTENT_COMMITTED",
        kind="admit",
        previous_version=0,
    )
    _append_direct_ready_fact(
        binding,
        _direct_ready_payload(binding, sealed),
        idempotency_key="task4-ready-forged-operation-prefix",
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = _runtime_get_inventory(_runtime_inventory_query(identity, binding))

    assert rejected.ok is False
    assert rejected.code == "strict_stream_corruption"
    assert rejected.projection is None
    assert rejected.evidence["reason"] == "ready_operation_prefix_invalid"
    assert rejected.evidence["prefix_failure_code"] == "inventory_member_conflict"
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


