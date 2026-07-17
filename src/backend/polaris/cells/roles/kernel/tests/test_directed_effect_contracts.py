"""Contract tests for DEO-2B roles.kernel public values and ports."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import fields, is_dataclass, replace
from typing import get_args, get_type_hints

import polaris.cells.director.runtime.public.directed_effect_contracts as director_contracts
import polaris.cells.director.runtime.public.directed_effect_policy_contracts as policy_contracts
import polaris.cells.roles.kernel.public as kernel_public
import polaris.cells.roles.kernel.public.directed_effect_contracts as contracts
import pytest
from polaris.cells.runtime.task_runtime.public import (
    DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectInventoryProjectionV1,
    DirectedEffectOperationIdentityV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    ParentCorrelationV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)

_HASH = "d" * 64
_OTHER_HASH = "e" * 64


def _attempt(*, run_id: str = "run-1") -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=1,
        external_task_id="task-1",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="worker-1",
        run_id=run_id,
        lease_expires_at="2026-07-17T12:00:00+00:00",
    )


def _binding(attempt: TaskRuntimeExecutionAttemptIdentityV1) -> DirectedEffectParentBindingV1:
    return DirectedEffectParentBindingV1(
        schema_version=DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
        registry_identity=DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempt),
        registry_stream_token="registry-stream-1",
        registry_version=1,
        parent_sequence=1,
        binding_id="binding-1",
        operation_stream_token="operation-stream-1",
        binding_hash=_HASH,
        admission_idempotency_key="parent-admission-1",
        correlation=ParentCorrelationV1(turn_id="turn-1", batch_id="batch-1"),
        actor="roles.kernel",
        source_event_id="parent-event-1",
        source_event_seq=1,
    )


def _member(
    ordinal: int,
    *,
    tool_call_id: str | None = None,
    effect_id: str | None = None,
    operation_id: str | None = None,
) -> DirectedEffectInventoryMemberV1:
    suffix = str(ordinal)
    return DirectedEffectInventoryMemberV1(
        ordinal=ordinal,
        tool_call_id=tool_call_id or f"call-{suffix}",
        effect_id=effect_id or f"effect-{suffix}",
        operation_id=operation_id or f"operation-{suffix}",
        normalized_tool_name="write_file",
        effect_type="write",
        execution_mode="write_serial",
        intended_effect_fingerprint=_HASH,
        policy_verdict_hash=_HASH,
        expected_receipt_binding_hash=_HASH,
    )


def _inventory(
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    members: tuple[DirectedEffectInventoryMemberV1, ...],
) -> DirectedEffectInventoryProjectionV1:
    return DirectedEffectInventoryProjectionV1(
        schema_version=DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
        workspace=attempt.workspace,
        task_id=attempt.task_id,
        execution_attempt=attempt,
        parent_binding_id="binding-1",
        members=members,
        inventory_hash=_HASH,
        sealed_event_id="inventory-sealed-1",
        sealed_event_seq=2,
        parent_registry_source_head_seq=3,
        operation_source_head_seq=3,
        inventory_ready=True,
        ready_event_id="inventory-ready-1",
        ready_event_seq=3,
        admitted_count=len(members),
        missing_operation_ids=(),
        unexpected_operation_ids=(),
    )


def _evidence(
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    member: DirectedEffectInventoryMemberV1,
    snapshot: policy_contracts.DirectorEffectPolicySnapshotResultV1 | None = None,
) -> director_contracts.DirectorEffectAuthorizationEvidenceV1:
    values = {
        "workspace": attempt.workspace,
        "execution_attempt_id": DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
            attempt
        ).execution_attempt_id,
        "turn_id": "turn-1",
        "batch_id": "batch-1",
        "tool_call_id": member.tool_call_id,
        "normalized_tool_name": member.normalized_tool_name,
        "arguments_hash": _HASH,
        "tool_spec_hash": _HASH,
        "role_policy_id": "director",
        "role_policy_hash": _HASH,
        "canonical_allow_list_hash": _HASH,
        "capability_scope": ("src/",),
        "capability_scope_hash": _HASH,
        "job_token_id": "job-1",
        "job_token_evidence_hash": _HASH,
        "execution_envelope_hash": _HASH,
        "allowed_command_hash": _HASH,
        "mutation_guard_mode": "strict",
        "bound_policy_snapshot_hash": snapshot.evidence_hash if snapshot is not None else _HASH,
        "target_state_hash": snapshot.target_state_hash if snapshot is not None else _HASH,
        "normalized_operation_hash": snapshot.normalized_operation_hash if snapshot is not None else _HASH,
        "policy_version": snapshot.policy_version if snapshot is not None else "v1",
        "policy_hash": snapshot.policy_hash if snapshot is not None else _HASH,
    }
    return director_contracts.DirectorEffectAuthorizationEvidenceV1(
        **values,
        authorization_hash=director_contracts.hash_director_effect_authorization_evidence(**values),
    )


def _forged_authorization_evidence(
    evidence: director_contracts.DirectorEffectAuthorizationEvidenceV1,
    **changes: object,
) -> director_contracts.DirectorEffectAuthorizationEvidenceV1:
    forged = object.__new__(director_contracts.DirectorEffectAuthorizationEvidenceV1)
    for field in fields(evidence):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(evidence, field.name)))
    return forged


def _prepared_member(
    member: DirectedEffectInventoryMemberV1,
    *,
    stream_head: int,
) -> contracts.DirectedEffectPreparedMemberV1:
    return contracts.DirectedEffectPreparedMemberV1(
        member=member,
        admitted_operation_version=1,
        latest_operation_stream_head=stream_head,
    )


def _prepared_batch(
    *,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
    inventory: DirectedEffectInventoryProjectionV1 | None = None,
    prepared_members: tuple[contracts.DirectedEffectPreparedMemberV1, ...] | None = None,
    call_id_index: tuple[tuple[str, int], ...] | None = None,
    evidence_by_call_id: tuple[tuple[str, director_contracts.DirectorEffectAuthorizationEvidenceV1], ...] | None = None,
) -> contracts.PreparedDirectedEffectBatchV1:
    attempt = execution_attempt or _attempt()
    members = (_member(0), _member(1))
    ready_inventory = inventory or _inventory(attempt, members)
    prepared = prepared_members or tuple(
        _prepared_member(member, stream_head=index + 2) for index, member in enumerate(ready_inventory.members)
    )
    index = call_id_index or tuple(
        (prepared_member.member.tool_call_id, position) for position, prepared_member in enumerate(prepared)
    )
    evidence = evidence_by_call_id or tuple(
        (
            prepared_member.member.tool_call_id,
            _evidence(attempt, prepared_member.member),
        )
        for prepared_member in prepared
    )
    return contracts.PreparedDirectedEffectBatchV1(
        execution_attempt=attempt,
        parent_binding=_binding(attempt),
        inventory=ready_inventory,
        prepared_members=prepared,
        call_id_index=index,
        latest_parent_registry_head=ready_inventory.parent_registry_source_head_seq,
        latest_operation_stream_head=ready_inventory.operation_source_head_seq,
        authorization_evidence_by_call_id=evidence,
    )


def _claim_grant(
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    member: DirectedEffectInventoryMemberV1,
) -> DirectedEffectClaimGrantV1:
    operation = DirectedEffectOperationIdentityV1(
        workspace=attempt.workspace,
        task_id=attempt.task_id,
        execution_attempt_id=binding.registry_identity.execution_attempt_id,
        parent_binding_id=binding.binding_id,
        parent_sequence=binding.parent_sequence,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        operation_id=member.operation_id,
        operation_stream_token=binding.operation_stream_token,
    )
    unsigned_record = {
        "schema_version": DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
        "execution_attempt": attempt.to_record(),
        "parent_binding": binding.to_record(),
        "operation": operation.to_record(),
        "member": member.to_record(),
        "inventory_hash": _HASH,
        "operation_version": 2,
        "claim_event_id": "claim-event-1",
        "claim_event_seq": 3,
        "operation_source_head_seq": 3,
        "parent_registry_source_head_seq": 3,
    }
    grant_hash = hashlib.sha256(
        json.dumps(
            unsigned_record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return DirectedEffectClaimGrantV1(
        schema_version=DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
        execution_attempt=attempt,
        parent_binding=binding,
        operation=operation,
        member=member,
        inventory_hash=_HASH,
        operation_version=2,
        claim_event_id="claim-event-1",
        claim_event_seq=3,
        operation_source_head_seq=3,
        parent_registry_source_head_seq=3,
        grant_hash=grant_hash,
    )


def _tamper_grant(
    grant: DirectedEffectClaimGrantV1,
    **changes: object,
) -> DirectedEffectClaimGrantV1:
    tampered = object.__new__(DirectedEffectClaimGrantV1)
    for field in fields(grant):
        object.__setattr__(
            tampered,
            field.name,
            changes.get(field.name, getattr(grant, field.name)),
        )
    return tampered


def _policy_subject() -> policy_contracts.DirectorEffectPolicyOperationSubjectV1:
    return policy_contracts.DirectorEffectPolicyOperationSubjectV1(
        workspace="/workspace",
        turn_id="turn-1",
        batch_id="batch-1",
        tool_call_id="call-0",
        inventory_ordinal=0,
        normalized_tool_name="write_file",
        normalized_arguments=(("path", "src/a.py"),),
        effect_type="write",
        execution_mode="write_serial",
        prospective_operation_hash=_HASH,
    )


def _policy_result(
    subject: policy_contracts.DirectorEffectPolicyOperationSubjectV1 | None = None,
) -> policy_contracts.DirectorEffectPolicySnapshotResultV1:
    subject = subject or _policy_subject()
    baseline_hash = policy_contracts.hash_directed_effect_target_state_components(
        target_path="src/a.py",
        exists=True,
        before_content_hash=_HASH,
        minimal_content_evidence=(),
        agents_policy_hash=_HASH,
        is_no_file_state=False,
    )
    baseline = policy_contracts.DirectorEffectTargetStateEvidenceV1(
        target_path="src/a.py",
        exists=True,
        before_content_hash=_HASH,
        minimal_content_evidence=(),
        agents_policy_hash=_HASH,
        target_state_hash=baseline_hash,
        is_no_file_state=False,
    )
    evidence_hash = policy_contracts.hash_directed_effect_policy_snapshot_evidence(
        status="allowed",
        allowed=True,
        error_code=None,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=baseline,
        normalized_operation_hash=_HASH,
    )
    return policy_contracts.DirectorEffectPolicySnapshotResultV1(
        status="allowed",
        allowed=True,
        error_code=None,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=baseline,
        target_state_hash=baseline.target_state_hash,
        normalized_operation_hash=_HASH,
        evidence_hash=evidence_hash,
    )


class _PolicyPort:
    async def snapshot(
        self,
        request: policy_contracts.DirectorEffectPolicySnapshotRequestV1,
    ) -> policy_contracts.DirectorEffectPolicySnapshotResultV1:
        return _policy_result(request.subject)

    def bind_member(
        self,
        request: policy_contracts.DirectorEffectPolicyMemberBindingRequestV1,
    ) -> policy_contracts.DirectorEffectPolicyMemberBindingResultV1:
        member_binding_hash = policy_contracts.hash_directed_effect_policy_member_binding(
            request.snapshot.evidence_hash,
            request.authorization_evidence.authorization_hash,
            request.member,
        )
        bound = policy_contracts.DirectorEffectPolicyBoundSnapshotV1(
            snapshot=request.snapshot,
            authorization_evidence_hash=request.authorization_evidence.authorization_hash,
            member=request.member,
            member_binding_hash=member_binding_hash,
        )
        return policy_contracts.DirectorEffectPolicyMemberBindingResultV1(
            status="allowed",
            error_code=None,
            member=request.member,
            member_binding_hash=member_binding_hash,
            bound_snapshot=bound,
        )

    async def revalidate(
        self,
        request: policy_contracts.DirectorEffectPolicyRevalidationRequestV1,
    ) -> policy_contracts.DirectorEffectPolicyRevalidationResultV1:
        snapshot = request.bound_snapshot.snapshot
        evidence_hash = policy_contracts.hash_directed_effect_policy_revalidation_evidence(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version=snapshot.policy_version,
            current_policy_hash=snapshot.policy_hash,
            current_target_state_evidence=snapshot.baseline_target_state_evidence,
            current_normalized_operation_hash=snapshot.normalized_operation_hash,
            target_observation_performed=True,
        )
        return policy_contracts.DirectorEffectPolicyRevalidationResultV1(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version=snapshot.policy_version,
            current_policy_hash=snapshot.policy_hash,
            current_target_state_evidence=snapshot.baseline_target_state_evidence,
            current_target_state_hash=snapshot.target_state_hash,
            current_normalized_operation_hash=snapshot.normalized_operation_hash,
            target_observation_performed=True,
            current_evidence_hash=evidence_hash,
        )


def test_policy_fixture_retains_a1_baseline_and_canonical_member_hash() -> None:
    """Kernel protocol fixtures consume the amended public evidence contracts."""
    snapshot = _policy_result()
    member = _member(0)
    authorization = _evidence(_attempt(), member, snapshot)
    result = _PolicyPort().bind_member(
        policy_contracts.DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            member=member,
        )
    )

    assert snapshot.subject.inventory_ordinal == 0
    assert snapshot.baseline_target_state_evidence.target_state_hash == snapshot.target_state_hash
    assert result.member_binding_hash == policy_contracts.hash_directed_effect_policy_member_binding(
        snapshot.evidence_hash,
        authorization.authorization_hash,
        member,
    )
    assert result.bound_snapshot is not None


class _FenceAdmin:
    def register(
        self,
        context: contracts.DirectedEffectExecutionContextV1,
    ) -> contracts.DirectedEffectFenceRegistrationResultV1:
        return contracts.DirectedEffectFenceRegistrationResultV1(
            ok=True,
            status="registered",
            context_id=context.context_id,
            error_code=None,
        )

    def release_batch(
        self,
        batch_id: str,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> contracts.DirectedEffectFenceReleaseResultV1:
        return contracts.DirectedEffectFenceReleaseResultV1(
            ok=True,
            status="released",
            batch_id=batch_id,
            released_count=1,
            error_code=None,
        )


class _FenceConsume:
    def consume(
        self,
        context: contracts.DirectedEffectExecutionContextV1,
    ) -> contracts.DirectedEffectFenceConsumeResultV1:
        return contracts.DirectedEffectFenceConsumeResultV1(
            ok=True,
            status="consumed",
            context_id=context.context_id,
            error_code=None,
        )


class _MutationPort:
    async def execute_mutation(
        self,
        context: contracts.DirectedEffectExecutionContextV1,
        normalized_tool_name: str,
        normalized_arguments: director_contracts.DirectedEffectImmutableItemsV1,
    ) -> contracts.DirectedEffectMutationPortResultV1:
        return contracts.DirectedEffectMutationPortResultV1(
            ok=True,
            status="executed",
            tool_result=contracts.DirectedEffectToolResultV1(payload=(("ok", True),)),
            error_code=None,
        )


def test_all_deo_results_are_frozen_typed_and_closed() -> None:
    """Kernel-facing DEO results retain closed status and immutable state."""
    contract_types = (
        contracts.DirectedEffectExecutionContextV1,
        contracts.DirectedEffectPreparedMemberV1,
        contracts.PreparedDirectedEffectBatchV1,
        contracts.DirectedEffectLifecycleResultV1,
        contracts.DirectedEffectAttemptValidationResultV1,
        contracts.DirectedEffectAttemptHeartbeatResultV1,
        contracts.DirectedEffectFenceRegistrationResultV1,
        contracts.DirectedEffectFenceConsumeResultV1,
        contracts.DirectedEffectFenceReleaseResultV1,
        contracts.DirectedEffectToolResultV1,
        contracts.DirectedEffectMutationPortResultV1,
        contracts.DirectedEffectFencePortsV1,
        contracts.DirectedEffectRuntimeDependenciesV1,
    )
    assert all(is_dataclass(contract_type) for contract_type in contract_types)
    assert all(hasattr(contract_type, "__slots__") for contract_type in contract_types)
    assert set(get_args(contracts.DirectedEffectLifecycleStatusV1)) == {
        "ready",
        "not_applicable",
        "denied",
    }
    assert set(get_args(contracts.DirectedEffectFenceReleaseStatusV1)) == {
        "released",
        "absent",
        "denied",
    }
    assert set(get_args(contracts.DirectedEffectAttemptValidationStatusV1)) == {"valid", "denied"}
    assert set(get_args(contracts.DirectedEffectAttemptHeartbeatStatusV1)) == {"fresh", "denied"}
    assert set(get_args(contracts.DirectedEffectFenceRegistrationStatusV1)) == {"registered", "denied"}
    assert set(get_args(contracts.DirectedEffectFenceConsumeStatusV1)) == {"consumed", "denied"}
    assert set(get_args(contracts.DirectedEffectMutationStatusV1)) == {"executed", "denied", "failed"}


def test_fence_protocols_are_narrowed_by_capability() -> None:
    """Adapter consumers can consume only; kernel administration stays separate."""
    admin_methods = set(dir(contracts.DirectedEffectFenceAdminPortV1))
    consume_methods = set(dir(contracts.DirectedEffectFenceConsumePortV1))
    assert "register" in admin_methods
    assert "release_batch" in admin_methods
    assert "consume" not in admin_methods
    assert "consume" in consume_methods
    assert "register" not in consume_methods
    assert "release_batch" not in consume_methods


def test_kernel_protocols_return_typed_results_not_mappings() -> None:
    """Every DEO port return annotation remains a closed public contract."""
    methods = (
        contracts.DirectedEffectFenceAdminPortV1.register,
        contracts.DirectedEffectFenceAdminPortV1.release_batch,
        contracts.DirectedEffectFenceConsumePortV1.consume,
        contracts.DirectedEffectMutationPortV1.execute_mutation,
    )
    for method in methods:
        return_type = get_type_hints(method)["return"]
        assert "Mapping" not in str(return_type)
    assert inspect.iscoroutinefunction(contracts.DirectedEffectMutationPortV1.execute_mutation)


def test_mutation_and_cleanup_results_have_non_conflicting_states() -> None:
    """Only executed mutation results carry a tool result; absent cleanup succeeds."""
    absent = contracts.DirectedEffectFenceReleaseResultV1(
        ok=True,
        status="absent",
        batch_id="batch-1",
        released_count=0,
        error_code=None,
    )
    assert absent.ok is True
    with pytest.raises(ValueError, match="tool_result"):
        contracts.DirectedEffectMutationPortResultV1(
            ok=False,
            status="denied",
            tool_result=contracts.DirectedEffectToolResultV1(payload=(("x", "y"),)),
            error_code="deo_claim_failed",
        )


def test_all_kernel_contracts_have_concrete_valid_constructions() -> None:
    """Every kernel public value participates in one valid concrete graph."""
    batch = _prepared_batch()
    attempt = batch.execution_attempt
    member = batch.prepared_members[0].member
    evidence = batch.authorization_evidence_by_call_id[0][1]
    context = contracts.DirectedEffectExecutionContextV1(
        context_id="context-1",
        batch_id="batch-1",
        creator_pid=1,
        tool_call_id=member.tool_call_id,
        normalized_tool_name=member.normalized_tool_name,
        arguments_hash=_HASH,
        authorization_evidence=evidence,
        claim_grant=_claim_grant(attempt, batch.parent_binding, member),
    )
    lifecycle = contracts.DirectedEffectLifecycleResultV1(
        status="ready",
        prepared_batch=batch,
        error_code=None,
        upstream_evidence=(("code", "inventory_ready"),),
    )
    validation = contracts.DirectedEffectAttemptValidationResultV1(
        status="valid",
        execution_attempt=attempt,
        error_code=None,
    )
    heartbeat = contracts.DirectedEffectAttemptHeartbeatResultV1(
        status="fresh",
        execution_attempt=attempt,
        error_code=None,
    )
    registration = contracts.DirectedEffectFenceRegistrationResultV1(
        ok=True,
        status="registered",
        context_id=context.context_id,
        error_code=None,
    )
    consumption = contracts.DirectedEffectFenceConsumeResultV1(
        ok=True,
        status="consumed",
        context_id=context.context_id,
        error_code=None,
    )
    release = contracts.DirectedEffectFenceReleaseResultV1(
        ok=True,
        status="released",
        batch_id=context.batch_id,
        released_count=1,
        error_code=None,
    )
    tool_result = contracts.DirectedEffectToolResultV1(payload=(("ok", True),))
    mutation = contracts.DirectedEffectMutationPortResultV1(
        ok=True,
        status="executed",
        tool_result=tool_result,
        error_code=None,
    )
    fence_ports = contracts.DirectedEffectFencePortsV1(
        admin=_FenceAdmin(),
        consume=_FenceConsume(),
    )
    dependencies = contracts.DirectedEffectRuntimeDependenciesV1(
        policy_snapshot_port=_PolicyPort(),
        fence_admin_port=fence_ports.admin,
        mutation_port=_MutationPort(),
    )

    assert lifecycle.prepared_batch is batch
    assert validation.execution_attempt is attempt
    assert heartbeat.execution_attempt is attempt
    assert registration.ok and consumption.ok and release.ok and mutation.ok
    assert dependencies.fence_admin_port is fence_ports.admin


@pytest.mark.parametrize(
    "case",
    ("missing", "extra", "out_of_order", "identity_drift"),
)
def test_prepared_batch_rejects_inventory_member_and_order_drift(case: str) -> None:
    """Prepared members must exactly preserve the complete ready inventory sequence."""
    attempt = _attempt()
    inventory_members = (_member(0), _member(1))
    inventory = _inventory(attempt, inventory_members)
    prepared = tuple(_prepared_member(member, stream_head=index + 2) for index, member in enumerate(inventory_members))
    if case == "missing":
        drifted = prepared[:1]
    elif case == "extra":
        drifted = (*prepared, _prepared_member(_member(2), stream_head=4))
    elif case == "out_of_order":
        drifted = tuple(reversed(prepared))
    else:
        drifted = (
            replace(prepared[0], member=replace(prepared[0].member, effect_id="effect-drift")),
            prepared[1],
        )

    with pytest.raises(ValueError, match="inventory members"):
        _prepared_batch(
            execution_attempt=attempt,
            inventory=inventory,
            prepared_members=drifted,
        )


def test_prepared_batch_rejects_call_index_and_evidence_identity_drift() -> None:
    """Call indexes and authorization evidence bind the exact member sequence."""
    batch = _prepared_batch()
    first_call_id, first_evidence = batch.authorization_evidence_by_call_id[0]
    drifted_evidence = _forged_authorization_evidence(first_evidence, tool_call_id="call-drift")

    with pytest.raises(ValueError, match="authorization evidence"):
        _prepared_batch(
            execution_attempt=batch.execution_attempt,
            inventory=batch.inventory,
            prepared_members=batch.prepared_members,
            evidence_by_call_id=(
                (first_call_id, drifted_evidence),
                batch.authorization_evidence_by_call_id[1],
            ),
        )
    with pytest.raises(ValueError, match="call_id_index"):
        _prepared_batch(
            execution_attempt=batch.execution_attempt,
            inventory=batch.inventory,
            prepared_members=batch.prepared_members,
            call_id_index=(("call-1", 0), ("call-0", 1)),
        )


def test_prepared_batch_rejects_inventory_attempt_and_head_drift() -> None:
    """Ready inventory identity and returned heads remain exact batch bindings."""
    attempt = _attempt()
    other_attempt = _attempt(run_id="run-drift")
    inventory = _inventory(other_attempt, (_member(0), _member(1)))

    with pytest.raises(ValueError, match="execution attempt"):
        _prepared_batch(execution_attempt=attempt, inventory=inventory)

    batch = _prepared_batch()
    with pytest.raises(ValueError, match="source heads"):
        replace(
            batch,
            latest_operation_stream_head=batch.latest_operation_stream_head + 1,
        )
    with pytest.raises(ValueError, match="parent binding"):
        replace(
            batch,
            parent_binding=replace(batch.parent_binding, binding_id="binding-drift"),
        )


def test_nested_mutable_tool_result_payload_is_rejected() -> None:
    """Tool results reject mutable values at any nested depth."""
    with pytest.raises(TypeError, match="mutable mappings"):
        contracts.DirectedEffectToolResultV1(
            payload=(("result", ({"path": "src/a.py"},)),),
        )


@pytest.mark.parametrize(
    "case",
    (
        "batch",
        "call",
        "tool",
        "arguments",
        "evidence",
        "attempt",
        "parent",
        "member",
        "operation",
    ),
)
def test_execution_context_rejects_outer_and_nested_identity_drift(case: str) -> None:
    """Fence contexts reject all outer, evidence, and nested grant drift."""
    batch = _prepared_batch()
    member = batch.prepared_members[0].member
    evidence = batch.authorization_evidence_by_call_id[0][1]
    grant = _claim_grant(batch.execution_attempt, batch.parent_binding, member)
    values: dict[str, object] = {
        "context_id": "context-1",
        "batch_id": "batch-1",
        "creator_pid": 1,
        "tool_call_id": member.tool_call_id,
        "normalized_tool_name": member.normalized_tool_name,
        "arguments_hash": _HASH,
        "authorization_evidence": evidence,
        "claim_grant": grant,
    }
    if case == "batch":
        values["batch_id"] = "batch-drift"
    elif case == "call":
        values["tool_call_id"] = "call-drift"
    elif case == "tool":
        values["normalized_tool_name"] = "execute_command"
    elif case == "arguments":
        values["arguments_hash"] = _OTHER_HASH
    elif case == "evidence":
        values["authorization_evidence"] = _forged_authorization_evidence(evidence, batch_id="batch-drift")
    elif case == "attempt":
        values["claim_grant"] = _tamper_grant(
            grant,
            execution_attempt=replace(grant.execution_attempt, run_id="run-drift"),
        )
    elif case == "parent":
        values["claim_grant"] = _tamper_grant(
            grant,
            parent_binding=replace(grant.parent_binding, binding_id="binding-drift"),
        )
    elif case == "member":
        values["claim_grant"] = _tamper_grant(
            grant,
            member=replace(grant.member, effect_id="effect-drift"),
        )
    else:
        values["claim_grant"] = _tamper_grant(
            grant,
            operation=replace(grant.operation, operation_id="operation-drift"),
        )

    with pytest.raises(ValueError, match="mismatch"):
        contracts.DirectedEffectExecutionContextV1(**values)


def test_kernel_tokens_integers_and_status_alias_exports_are_strict() -> None:
    """Kernel public boundaries reject coercion and export all stable statuses."""
    batch = _prepared_batch()
    member = batch.prepared_members[0].member
    evidence = batch.authorization_evidence_by_call_id[0][1]
    grant = _claim_grant(batch.execution_attempt, batch.parent_binding, member)
    with pytest.raises(TypeError, match="context_id"):
        contracts.DirectedEffectExecutionContextV1(
            context_id=7,
            batch_id="batch-1",
            creator_pid=1,
            tool_call_id=member.tool_call_id,
            normalized_tool_name=member.normalized_tool_name,
            arguments_hash=_HASH,
            authorization_evidence=evidence,
            claim_grant=grant,
        )
    with pytest.raises(ValueError, match="creator_pid"):
        contracts.DirectedEffectExecutionContextV1(
            context_id="context-1",
            batch_id="batch-1",
            creator_pid=True,
            tool_call_id=member.tool_call_id,
            normalized_tool_name=member.normalized_tool_name,
            arguments_hash=_HASH,
            authorization_evidence=evidence,
            claim_grant=grant,
        )
    with pytest.raises(ValueError, match="admitted_operation_version"):
        contracts.DirectedEffectPreparedMemberV1(
            member=member,
            admitted_operation_version=True,
            latest_operation_stream_head=1,
        )
    with pytest.raises(ValueError, match="released_count"):
        contracts.DirectedEffectFenceReleaseResultV1(
            ok=True,
            status="released",
            batch_id="batch-1",
            released_count=True,
            error_code=None,
        )

    aliases = (
        "DirectedEffectLifecycleStatusV1",
        "DirectedEffectAttemptValidationStatusV1",
        "DirectedEffectAttemptHeartbeatStatusV1",
        "DirectedEffectFenceRegistrationStatusV1",
        "DirectedEffectFenceConsumeStatusV1",
        "DirectedEffectFenceReleaseStatusV1",
        "DirectedEffectMutationStatusV1",
    )
    for name in aliases:
        assert getattr(kernel_public, name) is getattr(contracts, name)


@pytest.mark.parametrize("invalid_ok", (0, 1, "true"))
def test_kernel_result_ok_fields_require_exact_bool(invalid_ok: object) -> None:
    """Fence and mutation verdicts reject numeric and textual bool substitutes."""
    with pytest.raises(TypeError, match="ok"):
        contracts.DirectedEffectFenceRegistrationResultV1(
            ok=invalid_ok,
            status="registered",
            context_id="context-1",
            error_code=None,
        )
    with pytest.raises(TypeError, match="ok"):
        contracts.DirectedEffectFenceConsumeResultV1(
            ok=invalid_ok,
            status="consumed",
            context_id="context-1",
            error_code=None,
        )
    with pytest.raises(TypeError, match="ok"):
        contracts.DirectedEffectFenceReleaseResultV1(
            ok=invalid_ok,
            status="released",
            batch_id="batch-1",
            released_count=1,
            error_code=None,
        )
    with pytest.raises(TypeError, match="ok"):
        contracts.DirectedEffectMutationPortResultV1(
            ok=invalid_ok,
            status="executed",
            tool_result=contracts.DirectedEffectToolResultV1(payload=(("path", "src/a.py"),)),
            error_code=None,
        )


def test_kernel_root_public_exports_tool_result_value_alias() -> None:
    """The stable tool-result value alias is available from the kernel root boundary."""
    assert "DirectedEffectToolResultValueV1" in kernel_public.__all__
    assert kernel_public.DirectedEffectToolResultValueV1 is contracts.DirectedEffectToolResultValueV1
