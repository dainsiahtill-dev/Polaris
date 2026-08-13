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




def test_task4_historical_ready_inventory_survives_parent_close_and_stale_replay_cas(
    tmp_path: Path,
) -> None:
    identity, binding, _sealed, command = _runtime_ready_candidate(tmp_path)
    ready = _runtime_finalize_inventory(command)
    assert ready.code == "inventory_ready"
    _runtime_close_parent(binding, previous_version=3)
    registry_after_close = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    observed = _runtime_get_inventory(_runtime_inventory_query(identity, binding))
    replay = _runtime_finalize_inventory(
        replace(
            command,
            expected_registry_version=99,
            expected_registry_seq=100,
            expected_operation_head_seq=99,
        )
    )

    assert observed.ok is True
    assert observed.code == "inventory_observed"
    assert observed.projection is not None
    assert observed.projection.inventory_ready is True
    assert observed.projection.parent_registry_source_head_seq == 4
    assert replay.ok is True
    assert replay.code == "inventory_ready_idempotent_replay"
    assert replay.projection == observed.projection
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_after_close
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize("transition", ("claim", "abort"))
def test_task4_ready_query_and_replay_validate_historical_admission_prefix_after_transition(
    tmp_path: Path,
    transition: str,
) -> None:
    identity, binding, sealed, command = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(command).code == "inventory_ready"
    member = sealed.members[0]
    common = {
        "workspace": identity.workspace,
        "task_id": identity.task_id,
        "execution_attempt": identity,
        "parent_binding": binding,
        "tool_call_id": member.tool_call_id,
        "effect_id": member.effect_id,
        "expected_version": 1,
        "expected_seq": 2,
        "actor": "task-runtime-inventory-test",
        "intended_effect_fingerprint": member.intended_effect_fingerprint,
        "policy_verdict_hash": member.policy_verdict_hash,
        "expected_receipt_binding_hash": member.expected_receipt_binding_hash,
    }
    if transition == "claim":
        transition_result = claim_directed_effect(ClaimDirectedEffectCommandV1(**common))
        assert transition_result.code == "effect_claimed"
    else:
        transition_result = abort_directed_effect_operation(
            AbortDirectedEffectOperationCommandV1(
                **common,
                reason="task4 historical-prefix abort",
            )
        )
        assert transition_result.code == "aborted"
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_after_transition = _runtime_events(identity.workspace, binding.operation_stream_token)

    observed = _runtime_get_inventory(_runtime_inventory_query(identity, binding))
    replay = _runtime_finalize_inventory(command)

    assert observed.ok is True
    assert observed.code == "inventory_observed"
    assert observed.projection is not None
    assert observed.projection.inventory_ready is True
    assert observed.projection.operation_source_head_seq == 2
    assert replay.ok is True
    assert replay.code == "inventory_ready_idempotent_replay"
    assert replay.projection == observed.projection
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_after_transition


@pytest.mark.parametrize("transition", ("claim", "abort"))
def test_task5_claim_or_abort_before_inventory_ready_is_append_free(
    tmp_path: Path,
    transition: str,
) -> None:
    identity, binding, sealed, _finalize = _runtime_ready_candidate(tmp_path)
    member = sealed.members[0]
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    result = (
        claim_directed_effect(_runtime_claim_command(identity, binding, member))
        if transition == "claim"
        else abort_directed_effect_operation(_runtime_abort_command(identity, binding, member))
    )

    assert result.ok is False
    assert result.code == "inventory_not_ready"
    assert result.claim_grant is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task5_fresh_ready_claim_returns_exact_frozen_grant_and_replay_has_none(
    tmp_path: Path,
) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
    member = sealed.members[0]
    command = _runtime_claim_command(identity, binding, member)

    claimed = claim_directed_effect(command)

    assert claimed.ok is True
    assert claimed.code == "effect_claimed"
    assert claimed.operation is not None
    grant = claimed.claim_grant
    assert type(grant) is DirectedEffectClaimGrantV1
    assert grant.schema_version == DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1
    assert grant.execution_attempt == identity
    assert grant.parent_binding == binding
    assert grant.operation == claimed.operation
    assert grant.member == member
    assert grant.inventory_hash == sealed.inventory_hash
    assert grant.member.intended_effect_fingerprint == member.intended_effect_fingerprint
    assert grant.member.policy_verdict_hash == member.policy_verdict_hash
    assert grant.member.expected_receipt_binding_hash == member.expected_receipt_binding_hash
    assert grant.operation_version == claimed.version == 2
    assert grant.claim_event_id == claimed.evidence["event_id"]
    assert grant.claim_event_seq == claimed.evidence["appended_seq"] == 2
    assert grant.operation_source_head_seq == 2
    assert grant.parent_registry_source_head_seq == 3
    unsigned_record = dict(grant.to_record())
    assert unsigned_record.pop("grant_hash") == grant.grant_hash
    assert grant.grant_hash == _canonical_sha256(unsigned_record)
    detached_record = grant.to_record()
    detached_operation = cast(dict[str, object], detached_record["operation"])
    detached_operation["operation_id"] = "mutated-detached-record"
    assert grant.operation.operation_id == member.operation_id
    with pytest.raises(FrozenInstanceError):
        grant.grant_hash = "f" * 64  # type: ignore[misc]
    registry_before_replay = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before_replay = _runtime_events(identity.workspace, binding.operation_stream_token)

    replay = claim_directed_effect(command)

    assert replay.ok is True
    assert replay.code == "idempotent_replay"
    assert replay.claim_grant is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before_replay
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before_replay


def test_task11_complete_claim_chain_binds_command_cas_hashes_and_nested_grant(
    tmp_path: Path,
) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    member = sealed.members[0]
    registry_before_ready = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before_ready = _runtime_events(identity.workspace, binding.operation_stream_token)
    assert len(registry_before_ready) == 2
    assert len(operation_before_ready) == 1

    ready = _runtime_finalize_inventory(finalize)
    assert ready.code == "inventory_ready"
    registry_ready = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_ready = _runtime_events(identity.workspace, binding.operation_stream_token)
    assert len(registry_ready) == 3
    assert operation_ready == operation_before_ready

    command = _runtime_claim_command(identity, binding, member)
    assert command.workspace == identity.workspace
    assert command.task_id == identity.task_id
    assert command.execution_attempt is identity
    assert command.parent_binding is binding
    assert command.tool_call_id == member.tool_call_id
    assert command.effect_id == member.effect_id
    assert command.expected_version == 1
    assert command.expected_seq == 2
    assert command.intended_effect_fingerprint == member.intended_effect_fingerprint
    assert command.policy_verdict_hash == member.policy_verdict_hash
    assert command.expected_receipt_binding_hash == member.expected_receipt_binding_hash

    claimed = claim_directed_effect(command)
    grant = claimed.claim_grant
    assert claimed.code == "effect_claimed"
    assert type(grant) is DirectedEffectClaimGrantV1
    assert grant.execution_attempt == identity
    assert grant.parent_binding == binding
    assert grant.operation == claimed.operation
    assert grant.member == member
    assert grant.inventory_hash == sealed.inventory_hash
    assert grant.operation_version == claimed.version == command.expected_version + 1
    assert grant.claim_event_id == claimed.evidence["event_id"]
    assert grant.claim_event_seq == claimed.evidence["appended_seq"] == command.expected_seq
    assert grant.operation_source_head_seq == command.expected_seq
    assert grant.parent_registry_source_head_seq == len(registry_ready)
    grant_record = grant.to_record()
    assert set(grant_record) == {
        "schema_version",
        "execution_attempt",
        "parent_binding",
        "operation",
        "member",
        "inventory_hash",
        "operation_version",
        "claim_event_id",
        "claim_event_seq",
        "operation_source_head_seq",
        "parent_registry_source_head_seq",
        "grant_hash",
    }
    assert grant_record["execution_attempt"] == identity.to_record()
    assert grant_record["parent_binding"] == binding.to_record()
    assert grant_record["operation"] == grant.operation.to_record()
    assert grant_record["member"] == member.to_record()
    unsigned_grant_record = dict(grant_record)
    assert unsigned_grant_record.pop("grant_hash") == grant.grant_hash
    assert _canonical_sha256(unsigned_grant_record) == grant.grant_hash
    operation_claimed = _runtime_events(identity.workspace, binding.operation_stream_token)
    assert len(operation_claimed) == 2
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_ready

    replay = claim_directed_effect(command)
    assert replay.code == "idempotent_replay"
    assert replay.claim_grant is None
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_claimed
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_ready


def test_task5_fresh_abort_after_ready_succeeds_without_grant(tmp_path: Path) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)

    aborted = abort_directed_effect_operation(_runtime_abort_command(identity, binding, sealed.members[0]))

    assert aborted.ok is True
    assert aborted.code == "aborted"
    assert aborted.claim_grant is None
    assert len(_runtime_events(identity.workspace, binding.operation_stream_token)) == 2
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before


@pytest.mark.parametrize(
    ("corruption", "transition"),
    (
        ("forged_ready", "claim"),
        ("forged_prefix", "abort"),
        ("changed_inventory", "claim"),
        ("changed_member", "abort"),
        ("missing_member", "claim"),
    ),
)
def test_task5_corrupt_ready_or_admission_prefix_fails_before_transition_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
    transition: str,
) -> None:
    intents = _intents(2) if corruption == "missing_member" else None
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path, intents=intents)
    if corruption == "forged_ready":
        payload = _direct_ready_payload(binding, sealed, actor="forged-ready-actor")
        _append_direct_ready_fact(
            binding,
            payload,
            idempotency_key="task5-forged-ready",
        )
    elif corruption == "forged_prefix":
        operation_events = _runtime_events(identity.workspace, binding.operation_stream_token)
        assert len(operation_events) == 1
        assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
        real_read_snapshot = deo_internal.read_guarded_fact_snapshot

        def read_snapshot_with_forged_prefix(command: Any) -> Any:
            snapshot = real_read_snapshot(command)
            target_facts = list(snapshot.target_records())
            replay_descriptor = cast(
                dict[str, object],
                target_facts[0]["payload"]["replay_descriptor"],
            )
            replay_descriptor["policy_verdict_hash"] = "f" * 64
            return replace(snapshot, target_facts=tuple(target_facts))

        monkeypatch.setattr(
            deo_internal,
            "read_guarded_fact_snapshot",
            read_snapshot_with_forged_prefix,
        )
    else:
        assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
        real_read_snapshot = deo_internal.read_guarded_fact_snapshot

        def read_snapshot_with_inventory_corruption(command: Any) -> Any:
            snapshot = real_read_snapshot(command)
            target_facts = list(snapshot.target_records())
            guard_facts = list(snapshot.guard_records())
            if corruption == "missing_member":
                target_facts.pop(1)
            else:
                seal_payload = cast(dict[str, object], guard_facts[1]["payload"])
                if corruption == "changed_inventory":
                    observed_hash = cast(str, seal_payload["inventory_hash"])
                    seal_payload["inventory_hash"] = "f" * 64 if observed_hash != "f" * 64 else "e" * 64
                else:
                    members = cast(list[dict[str, object]], seal_payload["members"])
                    members[0]["policy_verdict_hash"] = "f" * 64
            return replace(
                snapshot,
                target_facts=tuple(target_facts),
                guard_facts=tuple(guard_facts),
            )

        monkeypatch.setattr(
            deo_internal,
            "read_guarded_fact_snapshot",
            read_snapshot_with_inventory_corruption,
        )
    member = sealed.members[0]
    expected_seq = len(sealed.members) + 1
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    result = (
        claim_directed_effect(_runtime_claim_command(identity, binding, member, expected_seq=expected_seq))
        if transition == "claim"
        else abort_directed_effect_operation(
            _runtime_abort_command(identity, binding, member, expected_seq=expected_seq)
        )
    )

    assert result.ok is False
    assert result.code == "strict_stream_corruption"
    assert result.claim_grant is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task5_ambiguous_post_durable_claim_reconciliation_returns_exact_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
    real_append = deo_internal.append_if_guarded_snapshot
    append_calls = 0

    def durable_claim_then_acknowledgement_loss(guarded_command: Any) -> Any:
        nonlocal append_calls
        append_calls += 1
        if append_calls == 1:
            real_append(guarded_command)
            raise FactStreamError(
                "simulated claim acknowledgement loss",
                code="append_write_failed",
                details={"phase": "after_durable_claim"},
            )
        return real_append(guarded_command)

    monkeypatch.setattr(
        deo_internal,
        "append_if_guarded_snapshot",
        durable_claim_then_acknowledgement_loss,
    )

    reconciled = claim_directed_effect(_runtime_claim_command(identity, binding, sealed.members[0]))

    assert reconciled.ok is True
    assert reconciled.code == "effect_claimed"
    assert type(reconciled.claim_grant) is DirectedEffectClaimGrantV1
    assert reconciled.claim_grant.operation == reconciled.operation
    assert reconciled.evidence["reconciled_after_guarded_error"] is True
    assert reconciled.evidence["fact_stream_code"] == "append_write_failed"
    assert reconciled.evidence["fact_stream_details"] == {"phase": "after_durable_claim"}
    assert append_calls == 2
    assert len(_runtime_events(identity.workspace, binding.operation_stream_token)) == 2


@pytest.mark.parametrize("proof_failure", ("receipt", "canonical_replay"))
def test_task5_claim_receipt_or_canonical_proof_failure_never_returns_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proof_failure: str,
) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
    if proof_failure == "receipt":

        def tamper_receipt(receipt: Any) -> Any:
            return replace(receipt, event_id=f"{receipt.event_id}-tampered")

        monkeypatch.setattr(
            deo_internal.DirectedEffectOperationRepository,
            "_after_guarded_commit",
            staticmethod(tamper_receipt),
        )
    else:
        real_append = deo_internal.append_if_guarded_snapshot
        append_calls = 0

        def append_then_fail_exact_replay(guarded_command: Any) -> Any:
            nonlocal append_calls
            append_calls += 1
            if append_calls == 1:
                return real_append(guarded_command)
            raise FactStreamError(
                "simulated claim canonical replay failure",
                code="append_write_failed",
                details={"phase": "claim_exact_replay"},
            )

        monkeypatch.setattr(
            deo_internal,
            "append_if_guarded_snapshot",
            append_then_fail_exact_replay,
        )

    rejected = claim_directed_effect(_runtime_claim_command(identity, binding, sealed.members[0]))

    assert rejected.ok is False
    assert rejected.code == "guarded_receipt_mismatch"
    assert rejected.claim_grant is None
    assert len(_runtime_events(identity.workspace, binding.operation_stream_token)) == 2


@pytest.mark.parametrize("transition", ("claim", "abort"))
def test_task5_ready_unexpected_aggregate_blocks_transition_without_append(
    tmp_path: Path,
    transition: str,
) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
    member = sealed.members[0]
    unexpected_command = replace(
        _runtime_admission_command(identity, binding, member, expected_seq=2),
        tool_call_id="task5-unexpected-call",
        effect_id="task5-unexpected-effect",
    )
    unexpected = _append_operation_transition_for_inventory_test(
        unexpected_command,
        state="INTENT_COMMITTED",
        kind="admit",
        previous_version=0,
    )
    observed = _runtime_get_inventory(_runtime_inventory_query(identity, binding))
    assert observed.ok is False
    assert observed.code == "inventory_admission_unexpected"
    assert observed.evidence["unexpected_operation_ids"] == (unexpected.operation_id,)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    result = (
        claim_directed_effect(_runtime_claim_command(identity, binding, member, expected_seq=3))
        if transition == "claim"
        else abort_directed_effect_operation(_runtime_abort_command(identity, binding, member, expected_seq=3))
    )

    assert result.ok is False
    assert result.code == "inventory_admission_unexpected"
    assert result.claim_grant is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize("transition", ("claim", "abort"))
def test_task5_claim_or_abort_without_inventory_seal_is_append_free(
    tmp_path: Path,
    transition: str,
) -> None:
    identity, binding = _runtime_parent(tmp_path)
    member_record, _inventory_hash = _expected_runtime_inventory(
        binding,
        _intent(contingency_kind=None),
    )
    member = DirectedEffectInventoryMemberV1.from_record(member_record)
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    result = (
        claim_directed_effect(_runtime_claim_command(identity, binding, member, expected_seq=1))
        if transition == "claim"
        else abort_directed_effect_operation(_runtime_abort_command(identity, binding, member, expected_seq=1))
    )

    assert result.ok is False
    assert result.code == "inventory_not_sealed"
    assert result.claim_grant is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


@pytest.mark.parametrize(
    ("caller_drift", "expected_code"),
    (
        ("unknown_identity", "inventory_member_not_found"),
        ("intended_effect_fingerprint", "inventory_member_conflict"),
        ("policy_verdict_hash", "inventory_member_conflict"),
        ("expected_receipt_binding_hash", "inventory_member_conflict"),
    ),
)
def test_task5_ready_claim_rejects_unknown_identity_or_semantic_hash_drift_without_append(
    tmp_path: Path,
    caller_drift: str,
    expected_code: str,
) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
    command = _runtime_claim_command(identity, binding, sealed.members[0])
    command = (
        replace(command, effect_id="task5-unknown-effect")
        if caller_drift == "unknown_identity"
        else replace(command, **{caller_drift: "f" * 64})
    )
    registry_before = _runtime_events(identity.workspace, binding.registry_stream_token)
    operation_before = _runtime_events(identity.workspace, binding.operation_stream_token)

    rejected = claim_directed_effect(command)

    assert rejected.ok is False
    assert rejected.code == expected_code
    assert rejected.claim_grant is None
    assert _runtime_events(identity.workspace, binding.registry_stream_token) == registry_before
    assert _runtime_events(identity.workspace, binding.operation_stream_token) == operation_before


def test_task5_claim_retry_reuses_one_ownership_key_and_returns_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
    real_append = deo_internal.append_if_guarded_snapshot
    idempotency_keys: list[str] = []

    def drift_once_then_append(guarded_command: Any) -> Any:
        idempotency_keys.append(guarded_command.idempotency_key)
        if len(idempotency_keys) == 1:
            raise FactStreamError(
                "simulated single claim target drift",
                code="target_snapshot_drift",
                details={"phase": "claim_commit"},
            )
        return real_append(guarded_command)

    monkeypatch.setattr(
        deo_internal,
        "append_if_guarded_snapshot",
        drift_once_then_append,
    )

    claimed = claim_directed_effect(_runtime_claim_command(identity, binding, sealed.members[0]))

    assert claimed.ok is True
    assert claimed.code == "effect_claimed"
    assert type(claimed.claim_grant) is DirectedEffectClaimGrantV1
    assert claimed.evidence["guarded_attempt"] == 2
    assert len(idempotency_keys) == 3
    assert len(set(idempotency_keys)) == 1
    assert len(_runtime_events(identity.workspace, binding.operation_stream_token)) == 2


def test_task5_grant_builder_is_fenced_to_confirmed_fresh_claim_path() -> None:
    repository_type = deo_internal.DirectedEffectOperationRepository
    class_tree = ast.parse(textwrap.dedent(inspect.getsource(repository_type)))
    class_node = cast(ast.ClassDef, class_tree.body[0])
    claim_grant_callers: list[str] = []
    for node in class_node.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if (
                child.func.attr == "_claim_grant"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "self"
            ):
                claim_grant_callers.append(node.name)

    assert claim_grant_callers == ["_confirmed_mutation_result"]
    assert "_replay_result" not in claim_grant_callers


def test_task5_real_barrier_exact_claim_race_has_one_grant_single_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, binding, sealed, finalize = _runtime_ready_candidate(tmp_path)
    assert _runtime_finalize_inventory(finalize).code == "inventory_ready"
    command = _runtime_claim_command(identity, binding, sealed.members[0])
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
        results = tuple(executor.map(claim_directed_effect, (command, command)))

    codes = tuple(result.code for result in results)
    assert len(_runtime_events(identity.workspace, binding.operation_stream_token)) == 2
    assert len(_runtime_events(identity.workspace, binding.registry_stream_token)) == 3
    assert codes.count("effect_claimed") == 1
    assert codes.count("idempotent_replay") == 1
    fresh = next(result for result in results if result.code == "effect_claimed")
    replay = next(result for result in results if result.code == "idempotent_replay")
    assert type(fresh.claim_grant) is DirectedEffectClaimGrantV1
    assert fresh.claim_grant.operation == fresh.operation
    assert replay.claim_grant is None


_INVENTORY_SUCCESS_CODES = (
    "inventory_sealed",
    "inventory_seal_idempotent_replay",
    "inventory_ready",
    "inventory_ready_idempotent_replay",
    "inventory_observed",
)

_INVENTORY_FAILURE_CODES = (
    "inventory_not_sealed",
    "inventory_seal_conflict",
    "inventory_requires_empty_operation_stream",
    "inventory_member_not_found",
    "inventory_member_conflict",
    "inventory_admission_incomplete",
    "inventory_admission_unexpected",
    "inventory_not_ready",
)

_AUTHORITY_SUCCESS_CODES = (
    "parent_admitted",
    "parent_idempotent_replay",
    "parent_registry_found",
    "admitted",
    "effect_claimed",
    "aborted",
    "receipt_committed",
    "recovery_pending",
    "dead_lettered",
    "closed_by_parent",
    "found",
    "idempotent_replay",
)


def test_inventory_result_accepts_each_success_code_with_projection(
    tmp_path: Path,
    code: str,
) -> None:
    projection = _inventory_projection(tmp_path, ready=code.startswith("inventory_ready"))
    result = DirectedEffectInventoryResultV1(
        ok=True,
        code=cast(Any, code),
        projection=projection,
        evidence={"source": "test"},
    )

    assert result.ok is True
    assert result.code == code
    assert result.projection is projection


@pytest.mark.parametrize("code", _INVENTORY_FAILURE_CODES)
def test_inventory_result_accepts_each_failure_code_without_projection(code: str) -> None:
    result = DirectedEffectInventoryResultV1(
        ok=False,
        code=cast(Any, code),
        evidence={"source": "test"},
    )

    assert result.ok is False
    assert result.projection is None


def test_inventory_result_rejects_projection_success_pairing_drift(tmp_path: Path) -> None:
    projection = _inventory_projection(tmp_path)

    with pytest.raises(ValueError):
        DirectedEffectInventoryResultV1(ok=True, code="inventory_sealed")
    with pytest.raises(ValueError):
        DirectedEffectInventoryResultV1(
            ok=False,
            code="inventory_not_ready",
            projection=projection,
        )


def test_inventory_result_evidence_is_recursively_detached_and_immutable() -> None:
    values = ["original"]
    labels = {"sealed", "observed"}
    evidence: dict[str, object] = {
        "nested": {"values": values, "labels": labels},
    }
    result = DirectedEffectInventoryResultV1(
        ok=False,
        code="inventory_not_ready",
        evidence=evidence,
    )
    values.append("mutated")
    labels.add("mutated")

    assert result.evidence == {
        "nested": {
            "values": ("original",),
            "labels": frozenset({"sealed", "observed"}),
        }
    }
    with pytest.raises(TypeError):
        operator.setitem(cast(dict[str, Any], result.evidence), "new", True)
    nested = result.evidence["nested"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        operator.setitem(cast(dict[str, Any], nested), "new", True)
    with pytest.raises(FrozenInstanceError):
        result.ok = True  # type: ignore[misc]


def test_inventory_result_evidence_rejects_cycles_and_non_data_values() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError):
        DirectedEffectInventoryResultV1(
            ok=False,
            code="inventory_not_ready",
            evidence=cyclic,
        )
    with pytest.raises(TypeError):
        DirectedEffectInventoryResultV1(
            ok=False,
            code="inventory_not_ready",
            evidence=cast(Mapping[str, Any], {1: "invalid-key"}),
        )
    with pytest.raises(TypeError):
        DirectedEffectInventoryResultV1(
            ok=False,
            code="inventory_not_ready",
            evidence={"invalid_value": object()},
        )


def test_inventory_result_code_type_hint_is_exact_public_alias() -> None:
    assert get_type_hints(DirectedEffectInventoryResultV1)["code"] == DirectedEffectInventoryCodeV1


def test_inventory_and_operation_code_aliases_share_one_authority_failure_set() -> None:
    failure_codes = _literal_union_values(DirectedEffectAuthorityFailureCodeV1)
    inventory_codes = _literal_union_values(DirectedEffectInventoryCodeV1)
    operation_codes = _literal_union_values(DirectedEffectOperationCodeV1)

    assert inventory_codes == set(_INVENTORY_SUCCESS_CODES) | failure_codes
    assert operation_codes == set(_AUTHORITY_SUCCESS_CODES) | failure_codes
    assert set(_INVENTORY_FAILURE_CODES).issubset(failure_codes)
    assert set(_AUTHORITY_SUCCESS_CODES).isdisjoint(inventory_codes)
    assert "effect_claimed" not in inventory_codes


def test_inventory_result_accepts_representative_shared_authority_failure() -> None:
    result = DirectedEffectInventoryResultV1(
        ok=False,
        code="session_not_active",
    )

    assert result.code == "session_not_active"
    assert result.projection is None


@pytest.mark.parametrize("code", ("unknown_inventory_code", "effect_claimed"))
def test_inventory_result_rejects_unknown_and_operation_success_codes(code: str) -> None:
    with pytest.raises(ValueError):
        DirectedEffectInventoryResultV1(ok=False, code=cast(Any, code))


@pytest.mark.parametrize(
    "export_name",
    (
        "DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1",
        "DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1",
        "DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1",
        "DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1",
        "DirectedEffectAuthorityFailureCodeV1",
        "DirectedEffectClaimGrantV1",
        "DirectedEffectInventoryContingencyKindV1",
        "DirectedEffectInventoryCodeV1",
        "DirectedEffectInventoryEffectTypeV1",
        "DirectedEffectInventoryExecutionModeV1",
        "DirectedEffectInventoryIntentV1",
        "DirectedEffectInventoryMemberV1",
        "DirectedEffectInventoryProjectionV1",
        "DirectedEffectInventoryResultV1",
        "FinalizeDirectedEffectInventoryAdmissionCommandV1",
        "GetDirectedEffectInventoryQueryV1",
        "SealDirectedEffectInventoryCommandV1",
        "seal_directed_effect_inventory",
    ),
)
def test_inventory_contract_is_publicly_exported(export_name: str) -> None:
    assert export_name in task_runtime_public.__all__
    assert getattr(task_runtime_public, export_name) is not None
