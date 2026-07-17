"""Contract tests for the DEO-2B public Director policy port."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import fields, is_dataclass, replace
from typing import get_type_hints

import polaris.cells.director.runtime.public as director_public
import polaris.cells.director.runtime.public.directed_effect_contracts as director_contracts
import polaris.cells.director.runtime.public.directed_effect_policy_contracts as policy
import pytest
from polaris.cells.runtime.task_runtime.public import (
    DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectOperationIdentityV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    ParentCorrelationV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)

_HASH = "b" * 64
_OTHER_HASH = "c" * 64
_ARGUMENTS = (("path", "src/a.py"),)
_ARGUMENTS_HASH = director_contracts.hash_directed_effect_arguments(_ARGUMENTS)


def _target_state_evidence(
    *,
    target_path: str = "src/a.py",
    exists: bool = True,
    before_content_hash: str = _HASH,
    minimal_content_evidence: tuple[tuple[str, object], ...] = (("prefix", "content"),),
    agents_policy_hash: str = _HASH,
    is_no_file_state: bool = False,
) -> policy.DirectorEffectTargetStateEvidenceV1:
    return policy.DirectorEffectTargetStateEvidenceV1(
        target_path=target_path,
        exists=exists,
        before_content_hash=before_content_hash,
        minimal_content_evidence=minimal_content_evidence,
        agents_policy_hash=agents_policy_hash,
        target_state_hash=policy.hash_directed_effect_target_state_components(
            target_path=target_path,
            exists=exists,
            before_content_hash=before_content_hash,
            minimal_content_evidence=minimal_content_evidence,
            agents_policy_hash=agents_policy_hash,
            is_no_file_state=is_no_file_state,
        ),
        is_no_file_state=is_no_file_state,
    )


def _subject(
    *,
    inventory_ordinal: int = 1,
) -> policy.DirectorEffectPolicyOperationSubjectV1:
    return policy.DirectorEffectPolicyOperationSubjectV1(
        workspace="/workspace",
        turn_id="turn-1",
        batch_id="batch-1",
        tool_call_id="call-1",
        inventory_ordinal=inventory_ordinal,
        normalized_tool_name="write_file",
        normalized_arguments=(("path", "src/a.py"),),
        effect_type="write",
        execution_mode="write_serial",
        prospective_operation_hash=_HASH,
    )


def _member(
    *,
    effect_id: str = "effect-1",
    operation_id: str = "operation-1",
) -> DirectedEffectInventoryMemberV1:
    return DirectedEffectInventoryMemberV1(
        ordinal=1,
        tool_call_id="call-1",
        effect_id=effect_id,
        operation_id=operation_id,
        normalized_tool_name="write_file",
        effect_type="write",
        execution_mode="write_serial",
        intended_effect_fingerprint=_HASH,
        policy_verdict_hash=_HASH,
        expected_receipt_binding_hash=_HASH,
    )


def _forged_member(
    member: DirectedEffectInventoryMemberV1,
    **changes: object,
) -> DirectedEffectInventoryMemberV1:
    forged = object.__new__(DirectedEffectInventoryMemberV1)
    for field in fields(member):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(member, field.name)))
    return forged


def _snapshot() -> policy.DirectorEffectPolicySnapshotResultV1:
    subject = _subject()
    target = _target_state_evidence()
    evidence_hash = policy.hash_directed_effect_policy_snapshot_evidence(
        status="allowed",
        allowed=True,
        error_code=None,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=target,
        normalized_operation_hash=_HASH,
    )
    return policy.DirectorEffectPolicySnapshotResultV1(
        status="allowed",
        allowed=True,
        error_code=None,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=target,
        target_state_hash=target.target_state_hash,
        normalized_operation_hash=_HASH,
        evidence_hash=evidence_hash,
    )


def _bound_snapshot(
    member: DirectedEffectInventoryMemberV1,
    *,
    authorization_evidence_hash: str | None = None,
    member_binding_hash: str | None = None,
) -> policy.DirectorEffectPolicyBoundSnapshotV1:
    snapshot = _snapshot()
    authorization_evidence_hash = (
        authorization_evidence_hash
        or _authorization_evidence(
            _claim_grant(member),
            snapshot,
        ).authorization_hash
    )
    return policy.DirectorEffectPolicyBoundSnapshotV1(
        snapshot=snapshot,
        authorization_evidence_hash=authorization_evidence_hash,
        member=member,
        member_binding_hash=member_binding_hash
        or policy.hash_directed_effect_policy_member_binding(
            snapshot.evidence_hash,
            authorization_evidence_hash,
            member,
        ),
    )


def _revalidation_evidence_hash(
    *,
    status: policy.DirectorEffectPolicySnapshotStatusV1 = "allowed",
    allowed: bool = True,
    error_code: director_contracts.DirectedEffectErrorCodeV1 | None = None,
    current_policy_version: str = "v1",
    current_policy_hash: str = _HASH,
    current_target_state_evidence: policy.DirectorEffectTargetStateEvidenceV1 | None = None,
    current_normalized_operation_hash: str = _HASH,
    target_observation_performed: bool = True,
) -> str:
    return policy.hash_directed_effect_policy_revalidation_evidence(
        status=status,
        allowed=allowed,
        error_code=error_code,
        current_policy_version=current_policy_version,
        current_policy_hash=current_policy_hash,
        current_target_state_evidence=current_target_state_evidence or _target_state_evidence(),
        current_normalized_operation_hash=current_normalized_operation_hash,
        target_observation_performed=target_observation_performed,
    )


def _claim_grant(member: DirectedEffectInventoryMemberV1) -> DirectedEffectClaimGrantV1:
    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=1,
        external_task_id="task-1",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="worker-1",
        run_id="run-1",
        lease_expires_at="2026-07-17T12:00:00+00:00",
    )
    binding = DirectedEffectParentBindingV1(
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


def _authorization_evidence(
    grant: DirectedEffectClaimGrantV1,
    snapshot: policy.DirectorEffectPolicySnapshotResultV1 | None = None,
    *,
    arguments_hash: str = _ARGUMENTS_HASH,
) -> director_contracts.DirectorEffectAuthorizationEvidenceV1:
    snapshot = snapshot or _snapshot()
    values = {
        "workspace": grant.execution_attempt.workspace,
        "execution_attempt_id": grant.parent_binding.registry_identity.execution_attempt_id,
        "turn_id": grant.parent_binding.correlation.turn_id,
        "batch_id": grant.parent_binding.correlation.batch_id,
        "tool_call_id": grant.member.tool_call_id,
        "normalized_tool_name": grant.member.normalized_tool_name,
        "arguments_hash": arguments_hash,
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
        "bound_policy_snapshot_hash": snapshot.evidence_hash,
        "target_state_hash": snapshot.target_state_hash,
        "normalized_operation_hash": snapshot.normalized_operation_hash,
        "policy_version": snapshot.policy_version,
        "policy_hash": snapshot.policy_hash,
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


def test_policy_contracts_are_frozen_and_reject_mutable_payloads() -> None:
    """The policy boundary is immutable and has no mutable wire payloads."""
    contract_types = (
        policy.DirectorEffectPolicyOperationSubjectV1,
        policy.DirectorEffectTargetStateEvidenceV1,
        policy.DirectorEffectPolicySnapshotRequestV1,
        policy.DirectorEffectPolicySnapshotResultV1,
        policy.DirectorEffectPolicyBoundSnapshotV1,
        policy.DirectorEffectPolicyMemberBindingRequestV1,
        policy.DirectorEffectPolicyMemberBindingResultV1,
        policy.DirectorEffectPolicyRevalidationRequestV1,
        policy.DirectorEffectPolicyRevalidationResultV1,
    )
    assert all(is_dataclass(contract_type) for contract_type in contract_types)
    assert all(hasattr(contract_type, "__slots__") for contract_type in contract_types)
    with pytest.raises(TypeError):
        policy.DirectorEffectPolicyOperationSubjectV1(
            workspace="/workspace",
            turn_id="turn-1",
            batch_id="batch-1",
            tool_call_id="call-1",
            inventory_ordinal=1,
            normalized_tool_name="write_file",
            normalized_arguments={"path": "src/a.py"},
            effect_type="write",
            execution_mode="write_serial",
            prospective_operation_hash=_HASH,
        )
    with pytest.raises(TypeError, match="mutable mappings"):
        policy.DirectorEffectPolicyOperationSubjectV1(
            workspace="/workspace",
            turn_id="turn-1",
            batch_id="batch-1",
            tool_call_id="call-1",
            inventory_ordinal=1,
            normalized_tool_name="write_file",
            normalized_arguments=(("options", ({"overwrite": True},)),),
            effect_type="write",
            execution_mode="write_serial",
            prospective_operation_hash=_HASH,
        )


def test_policy_result_invariants_distinguish_capability_from_denial() -> None:
    """A denied policy result cannot retain executable authorization."""
    allowed = _snapshot()
    assert allowed.allowed is True
    with pytest.raises(ValueError, match="allowed"):
        replace(allowed, status="denied", error_code="deo_director_policy_denied")


def test_no_file_target_state_is_explicit_and_workspace_policy_bound() -> None:
    """Command-only policy checks have a dedicated, immutable no-file form."""
    no_file = _target_state_evidence(
        target_path="",
        exists=False,
        before_content_hash="0" * 64,
        minimal_content_evidence=(),
        is_no_file_state=True,
    )
    assert no_file.is_no_file_state is True
    with pytest.raises(ValueError, match="no-file"):
        policy.DirectorEffectTargetStateEvidenceV1(
            target_path="src/a.py",
            exists=False,
            before_content_hash="0" * 64,
            minimal_content_evidence=(),
            agents_policy_hash=_HASH,
            target_state_hash=_HASH,
            is_no_file_state=True,
        )


def test_policy_baseline_evidence_derives_aggregate_and_binds_snapshot_hash() -> None:
    """The aggregate is derived evidence, while the snapshot hash binds its full baseline."""
    baseline = _target_state_evidence()
    assert baseline.target_state_hash == policy.hash_directed_effect_target_state_evidence(baseline)
    with pytest.raises(ValueError, match="target_state_hash"):
        replace(baseline, target_state_hash=_OTHER_HASH)

    snapshot = _snapshot()
    changed_baseline = _target_state_evidence(agents_policy_hash=_OTHER_HASH)
    with pytest.raises(ValueError, match="evidence_hash"):
        replace(
            snapshot,
            baseline_target_state_evidence=changed_baseline,
            target_state_hash=changed_baseline.target_state_hash,
        )


_SUBJECT_FIELDS = (
    "workspace",
    "turn_id",
    "batch_id",
    "tool_call_id",
    "inventory_ordinal",
    "normalized_tool_name",
    "normalized_arguments",
    "effect_type",
    "execution_mode",
    "prospective_operation_hash",
)


def _forged_subject(
    subject: policy.DirectorEffectPolicyOperationSubjectV1,
    field_name: str,
) -> policy.DirectorEffectPolicyOperationSubjectV1:
    replacements: dict[str, object] = {
        "workspace": "/other",
        "turn_id": "turn-2",
        "batch_id": "batch-2",
        "tool_call_id": "call-2",
        "inventory_ordinal": 0,
        "normalized_tool_name": "edit_file",
        "normalized_arguments": (("path", "src/b.py"),),
        "effect_type": "async",
        "execution_mode": "async_receipt",
        "prospective_operation_hash": _OTHER_HASH,
    }
    forged = object.__new__(policy.DirectorEffectPolicyOperationSubjectV1)
    for field in fields(subject):
        object.__setattr__(
            forged,
            field.name,
            replacements[field_name] if field.name == field_name else getattr(subject, field.name),
        )
    return forged


@pytest.mark.parametrize("field_name", _SUBJECT_FIELDS)
def test_snapshot_hash_binds_every_retained_subject_field(field_name: str) -> None:
    """Every pre-seal identity field is explicit snapshot evidence."""
    snapshot = _snapshot()
    forged_subject = _forged_subject(snapshot.subject, field_name)
    changed_hash = policy.hash_directed_effect_policy_snapshot_evidence(
        status=snapshot.status,
        allowed=snapshot.allowed,
        error_code=snapshot.error_code,
        policy_version=snapshot.policy_version,
        policy_hash=snapshot.policy_hash,
        subject=forged_subject,
        baseline_target_state_evidence=snapshot.baseline_target_state_evidence,
        normalized_operation_hash=snapshot.normalized_operation_hash,
    )

    assert changed_hash != snapshot.evidence_hash
    with pytest.raises(ValueError):
        replace(snapshot, subject=forged_subject)


def test_operation_subject_accepts_zero_ordinal_and_rejects_negative() -> None:
    """TaskRuntime inventory ordinals are zero-based non-negative integers."""
    assert _subject(inventory_ordinal=0).inventory_ordinal == 0
    with pytest.raises(ValueError, match="non-negative"):
        _subject(inventory_ordinal=-1)


def test_policy_protocol_has_only_typed_contract_returns() -> None:
    """The inversion port exposes typed results, never generic mappings."""
    methods = ("snapshot", "bind_member", "revalidate")
    for method_name in methods:
        method = getattr(policy.DirectorEffectPolicySnapshotPortV1, method_name)
        annotation = get_type_hints(method)["return"]
        assert "Mapping" not in str(annotation)
    assert inspect.iscoroutinefunction(policy.DirectorEffectPolicySnapshotPortV1.snapshot)
    assert not inspect.iscoroutinefunction(policy.DirectorEffectPolicySnapshotPortV1.bind_member)
    assert inspect.iscoroutinefunction(policy.DirectorEffectPolicySnapshotPortV1.revalidate)
    assert _subject().inventory_ordinal == 1
    assert director_public.hash_directed_effect_policy_member_binding is (
        policy.hash_directed_effect_policy_member_binding
    )
    assert director_public.hash_directed_effect_policy_revalidation_evidence is (
        policy.hash_directed_effect_policy_revalidation_evidence
    )


def test_policy_contracts_instantiate_snapshot_binding_and_revalidation() -> None:
    """The complete policy boundary is constructible with exact frozen values."""
    subject = _subject()
    target = _target_state_evidence()
    snapshot_request = policy.DirectorEffectPolicySnapshotRequestV1(
        subject=subject,
        workspace=subject.workspace,
        normalized_tool_name=subject.normalized_tool_name,
        normalized_arguments=subject.normalized_arguments,
        job_token_restriction_evidence=(("token_id", "job-1"),),
        expected_policy_version="v1",
        canonical_command="",
        path_scope_evidence=(("allowed", True),),
        command_scope_evidence=(),
        target_state_evidence=target,
    )
    member = _member()
    snapshot = _snapshot()
    authorization = _authorization_evidence(_claim_grant(member), snapshot)
    binding_request = policy.DirectorEffectPolicyMemberBindingRequestV1(
        snapshot=snapshot,
        authorization_evidence=authorization,
        member=member,
    )
    bound = _bound_snapshot(member)
    binding_result = policy.DirectorEffectPolicyMemberBindingResultV1(
        status="allowed",
        error_code=None,
        member=member,
        member_binding_hash=bound.member_binding_hash,
        bound_snapshot=bound,
    )
    revalidation_request = policy.DirectorEffectPolicyRevalidationRequestV1(
        bound_snapshot=bound,
        workspace="/workspace",
        actual_normalized_tool_name="write_file",
        actual_normalized_arguments=_ARGUMENTS,
        actual_arguments_hash=_ARGUMENTS_HASH,
        authorization_evidence=_authorization_evidence(_claim_grant(member), bound.snapshot),
        member=member,
        operation_id=member.operation_id,
        claim_grant=_claim_grant(member),
        current_job_token_restriction_evidence=(("token_id", "job-1"),),
    )
    revalidation_result = policy.DirectorEffectPolicyRevalidationResultV1(
        status="allowed",
        allowed=True,
        error_code=None,
        current_policy_version="v1",
        current_policy_hash=_HASH,
        current_target_state_evidence=target,
        current_target_state_hash=target.target_state_hash,
        current_normalized_operation_hash=_HASH,
        target_observation_performed=True,
        current_evidence_hash=_revalidation_evidence_hash(current_target_state_evidence=target),
    )

    assert snapshot_request.subject is subject
    assert binding_request.member is member
    assert binding_result.bound_snapshot is bound
    assert revalidation_request.claim_grant.member == member
    assert revalidation_result.allowed is True


def test_allowed_member_binding_rejects_member_identity_drift() -> None:
    """An allowed result cannot substitute a different member after binding."""
    member = _member()
    drifted = replace(member, effect_id="effect-drift")

    with pytest.raises(ValueError, match="member identity"):
        policy.DirectorEffectPolicyMemberBindingResultV1(
            status="allowed",
            error_code=None,
            member=drifted,
            member_binding_hash=_HASH,
            bound_snapshot=_bound_snapshot(member),
        )


def test_allowed_member_binding_rejects_binding_hash_drift() -> None:
    """An allowed result cannot alter the hash sealed into its bound snapshot."""
    member = _member()

    with pytest.raises(ValueError, match="member_binding_hash"):
        policy.DirectorEffectPolicyMemberBindingResultV1(
            status="allowed",
            error_code=None,
            member=member,
            member_binding_hash=_OTHER_HASH,
            bound_snapshot=_bound_snapshot(member),
        )


def test_bound_snapshot_constructor_binds_complete_member_identity() -> None:
    """A typed bound snapshot cannot substitute either the member or its hash."""
    member = _member()
    snapshot = _snapshot()
    authorization_hash = _authorization_evidence(_claim_grant(member), snapshot).authorization_hash
    binding_hash = policy.hash_directed_effect_policy_member_binding(
        snapshot.evidence_hash,
        authorization_hash,
        member,
    )

    assert (
        policy.DirectorEffectPolicyBoundSnapshotV1(
            snapshot=snapshot,
            authorization_evidence_hash=authorization_hash,
            member=member,
            member_binding_hash=binding_hash,
        ).member_binding_hash
        == binding_hash
    )
    with pytest.raises(ValueError, match="member_binding_hash"):
        policy.DirectorEffectPolicyBoundSnapshotV1(
            snapshot=snapshot,
            authorization_evidence_hash=authorization_hash,
            member=member,
            member_binding_hash=_OTHER_HASH,
        )
    with pytest.raises(ValueError, match="member_binding_hash"):
        policy.DirectorEffectPolicyBoundSnapshotV1(
            snapshot=snapshot,
            authorization_evidence_hash=authorization_hash,
            member=replace(member, effect_id="effect-substituted"),
            member_binding_hash=binding_hash,
        )
    with pytest.raises(ValueError, match="member_binding_hash"):
        policy.DirectorEffectPolicyBoundSnapshotV1(
            snapshot=snapshot,
            authorization_evidence_hash=_OTHER_HASH,
            member=member,
            member_binding_hash=binding_hash,
        )


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("ordinal", 2),
        ("tool_call_id", "call-2"),
        ("effect_id", "effect-2"),
        ("operation_id", "operation-2"),
        ("normalized_tool_name", "edit_file"),
        ("effect_type", "async"),
        ("execution_mode", "async_receipt"),
        ("intended_effect_fingerprint", _OTHER_HASH),
        ("policy_verdict_hash", _OTHER_HASH),
        ("expected_receipt_binding_hash", _OTHER_HASH),
    ),
)
def test_bound_snapshot_constructor_rejects_each_member_field_substitution(
    field_name: str,
    forged_value: object,
) -> None:
    """The public member-binding hash commits every sealed member field."""
    member = _member()
    bound = _bound_snapshot(member)

    with pytest.raises(ValueError, match="member_binding_hash"):
        policy.DirectorEffectPolicyBoundSnapshotV1(
            snapshot=bound.snapshot,
            authorization_evidence_hash=bound.authorization_evidence_hash,
            member=_forged_member(member, **{field_name: forged_value}),
            member_binding_hash=bound.member_binding_hash,
        )


def test_revalidation_result_constructor_binds_complete_current_evidence() -> None:
    """Every current verdict field is committed by the public revalidation hash."""
    target = _target_state_evidence()
    evidence_hash = _revalidation_evidence_hash(current_target_state_evidence=target)
    result = policy.DirectorEffectPolicyRevalidationResultV1(
        status="allowed",
        allowed=True,
        error_code=None,
        current_policy_version="v1",
        current_policy_hash=_HASH,
        current_target_state_evidence=target,
        current_target_state_hash=target.target_state_hash,
        current_normalized_operation_hash=_HASH,
        target_observation_performed=True,
        current_evidence_hash=evidence_hash,
    )
    assert result.current_evidence_hash == evidence_hash
    assert result.target_observation_performed is True
    with pytest.raises(ValueError, match="current_evidence_hash"):
        policy.DirectorEffectPolicyRevalidationResultV1(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version="v2",
            current_policy_hash=_HASH,
            current_target_state_evidence=target,
            current_target_state_hash=target.target_state_hash,
            current_normalized_operation_hash=_HASH,
            target_observation_performed=True,
            current_evidence_hash=evidence_hash,
        )

    denied_hash = _revalidation_evidence_hash(
        status="denied",
        allowed=False,
        error_code="deo_authorization_evidence_drift",
        current_target_state_evidence=target,
        target_observation_performed=False,
    )
    denied = policy.DirectorEffectPolicyRevalidationResultV1(
        status="denied",
        allowed=False,
        error_code="deo_authorization_evidence_drift",
        current_policy_version="v1",
        current_policy_hash=_HASH,
        current_target_state_evidence=target,
        current_target_state_hash=target.target_state_hash,
        current_normalized_operation_hash=_HASH,
        target_observation_performed=False,
        current_evidence_hash=denied_hash,
    )
    assert denied.target_observation_performed is False
    with pytest.raises(ValueError, match="current_evidence_hash"):
        policy.DirectorEffectPolicyRevalidationResultV1(
            status="denied",
            allowed=False,
            error_code="deo_authorization_evidence_drift",
            current_policy_version="v1",
            current_policy_hash=_HASH,
            current_target_state_evidence=target,
            current_target_state_hash=target.target_state_hash,
            current_normalized_operation_hash=_HASH,
            target_observation_performed=True,
            current_evidence_hash=denied_hash,
        )
    with pytest.raises(ValueError, match="fresh target observation"):
        policy.DirectorEffectPolicyRevalidationResultV1(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version="v1",
            current_policy_hash=_HASH,
            current_target_state_evidence=target,
            current_target_state_hash=target.target_state_hash,
            current_normalized_operation_hash=_HASH,
            target_observation_performed=False,
            current_evidence_hash=evidence_hash,
        )
    changed_target = _target_state_evidence(agents_policy_hash=_OTHER_HASH)
    with pytest.raises(ValueError, match="current_evidence_hash"):
        policy.DirectorEffectPolicyRevalidationResultV1(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version="v1",
            current_policy_hash=_HASH,
            current_target_state_evidence=changed_target,
            current_target_state_hash=changed_target.target_state_hash,
            current_normalized_operation_hash=_HASH,
            target_observation_performed=True,
            current_evidence_hash=evidence_hash,
        )


@pytest.mark.parametrize(
    ("effect_type", "execution_mode"),
    (
        pytest.param("delete", "write_serial", id="unknown-effect"),
        pytest.param("write", "unknown", id="unknown-mode"),
        pytest.param("write", "async_receipt", id="write-async-mismatch"),
        pytest.param("async", "write_serial", id="async-write-mismatch"),
    ),
)
def test_operation_subject_reuses_task_runtime_effect_mode_authority(
    effect_type: object,
    execution_mode: object,
) -> None:
    """Only TaskRuntime-authorized effect/mode values and pairs are accepted."""
    with pytest.raises(ValueError, match="supported pair"):
        policy.DirectorEffectPolicyOperationSubjectV1(
            workspace="/workspace",
            turn_id="turn-1",
            batch_id="batch-1",
            tool_call_id="call-1",
            inventory_ordinal=1,
            normalized_tool_name="write_file",
            normalized_arguments=(("path", "src/a.py"),),
            effect_type=effect_type,
            execution_mode=execution_mode,
            prospective_operation_hash=_HASH,
        )


@pytest.mark.parametrize(
    "case",
    (
        "workspace",
        "tool",
        "arguments",
        "batch_evidence",
        "attempt",
        "parent",
        "member",
        "operation",
    ),
)
def test_policy_revalidation_rejects_outer_and_nested_identity_drift(
    case: str,
) -> None:
    """Policy revalidation closes every outer and nested identity mismatch."""
    member = _member()
    bound = _bound_snapshot(member)
    grant = _claim_grant(member)
    evidence = _authorization_evidence(grant)
    values: dict[str, object] = {
        "bound_snapshot": bound,
        "workspace": "/workspace",
        "actual_normalized_tool_name": "write_file",
        "actual_normalized_arguments": _ARGUMENTS,
        "actual_arguments_hash": _ARGUMENTS_HASH,
        "authorization_evidence": evidence,
        "member": member,
        "operation_id": member.operation_id,
        "claim_grant": grant,
        "current_job_token_restriction_evidence": (("token_id", "job-1"),),
    }
    if case == "workspace":
        values["workspace"] = "/other"
    elif case == "tool":
        values["actual_normalized_tool_name"] = "execute_command"
    elif case == "arguments":
        values["actual_arguments_hash"] = _OTHER_HASH
    elif case == "batch_evidence":
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
        values["member"] = replace(member, effect_id="effect-drift")
    else:
        values["operation_id"] = "operation-drift"

    with pytest.raises(ValueError, match="mismatch"):
        policy.DirectorEffectPolicyRevalidationRequestV1(**values)


def test_policy_tokens_and_integer_fields_reject_implicit_coercion() -> None:
    """Policy contracts reject non-string tokens and bool integer impostors."""
    with pytest.raises(TypeError, match="workspace"):
        policy.DirectorEffectPolicyOperationSubjectV1(
            workspace=7,
            turn_id="turn-1",
            batch_id="batch-1",
            tool_call_id="call-1",
            inventory_ordinal=1,
            normalized_tool_name="write_file",
            normalized_arguments=(),
            effect_type="write",
            execution_mode="write_serial",
            prospective_operation_hash=_HASH,
        )
    with pytest.raises(ValueError, match="inventory_ordinal"):
        policy.DirectorEffectPolicyOperationSubjectV1(
            workspace="/workspace",
            turn_id="turn-1",
            batch_id="batch-1",
            tool_call_id="call-1",
            inventory_ordinal=True,
            normalized_tool_name="write_file",
            normalized_arguments=(),
            effect_type="write",
            execution_mode="write_serial",
            prospective_operation_hash=_HASH,
        )


def test_policy_revalidation_rejects_payload_only_drift() -> None:
    """Actual normalized arguments must produce the supplied and authorized hash."""
    member = _member()
    grant = _claim_grant(member)
    original_arguments = (("path", "src/a.py"),)
    original_hash = director_contracts.hash_directed_effect_arguments(original_arguments)
    evidence = _authorization_evidence(grant, arguments_hash=original_hash)

    with pytest.raises(ValueError, match=r"arguments_hash.*payload"):
        policy.DirectorEffectPolicyRevalidationRequestV1(
            bound_snapshot=_bound_snapshot(member),
            workspace="/workspace",
            actual_normalized_tool_name="write_file",
            actual_normalized_arguments=(("path", "src/drift.py"),),
            actual_arguments_hash=original_hash,
            authorization_evidence=evidence,
            member=member,
            operation_id=member.operation_id,
            claim_grant=grant,
            current_job_token_restriction_evidence=(("token_id", "job-1"),),
        )


def test_canonical_arguments_hash_rejects_duplicate_keys() -> None:
    """Canonicalization fails closed when two values claim the same argument key."""
    with pytest.raises(ValueError, match="duplicate"):
        director_contracts.hash_directed_effect_arguments(
            (("path", "src/a.py"), ("path", "src/b.py")),
        )


@pytest.mark.parametrize("invalid_bool", (0, 1, "true"))
def test_policy_bool_fields_require_exact_bool(invalid_bool: object) -> None:
    """Every policy bool field rejects numeric and textual substitutes."""
    target_values: dict[str, object] = {
        "target_path": "src/a.py",
        "exists": True,
        "before_content_hash": _HASH,
        "minimal_content_evidence": (),
        "agents_policy_hash": _HASH,
        "target_state_hash": _HASH,
        "is_no_file_state": False,
    }
    for field_name in ("exists", "is_no_file_state"):
        invalid_target = dict(target_values)
        invalid_target[field_name] = invalid_bool
        with pytest.raises(TypeError, match=field_name):
            policy.DirectorEffectTargetStateEvidenceV1(**invalid_target)

    with pytest.raises(TypeError, match="allowed"):
        policy.DirectorEffectPolicySnapshotResultV1(
            status="allowed",
            allowed=invalid_bool,
            error_code=None,
            policy_version="v1",
            policy_hash=_HASH,
            subject=_subject(),
            baseline_target_state_evidence=_target_state_evidence(),
            target_state_hash=_target_state_evidence().target_state_hash,
            normalized_operation_hash=_HASH,
            evidence_hash=_HASH,
        )

    with pytest.raises(TypeError, match="allowed"):
        policy.DirectorEffectPolicyRevalidationResultV1(
            status="allowed",
            allowed=invalid_bool,
            error_code=None,
            current_policy_version="v1",
            current_policy_hash=_HASH,
            current_target_state_evidence=_target_state_evidence(),
            current_target_state_hash=_target_state_evidence().target_state_hash,
            current_normalized_operation_hash=_HASH,
            target_observation_performed=True,
            current_evidence_hash=_HASH,
        )

    with pytest.raises(TypeError, match="target_observation_performed"):
        policy.DirectorEffectPolicyRevalidationResultV1(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version="v1",
            current_policy_hash=_HASH,
            current_target_state_evidence=_target_state_evidence(),
            current_target_state_hash=_target_state_evidence().target_state_hash,
            current_normalized_operation_hash=_HASH,
            target_observation_performed=invalid_bool,
            current_evidence_hash=_HASH,
        )
