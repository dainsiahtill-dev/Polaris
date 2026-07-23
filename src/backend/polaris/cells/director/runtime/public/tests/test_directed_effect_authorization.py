"""Contract tests for DEO-2B Director authorization values."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from dataclasses import fields, is_dataclass, replace
from typing import Literal, TypedDict, cast, get_args

import polaris.cells.director.runtime.public as director_public
import polaris.cells.director.runtime.public.directed_effect_contracts as contracts
import polaris.cells.director.runtime.public.directed_effect_policy_contracts as policy_contracts
import polaris.cells.director.runtime.public.directed_effect_service as service_module
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

_HASH = "a" * 64
_OTHER_HASH = "b" * 64
_ARGUMENTS = (("path", "src/a.py"),)
_ARGUMENTS_HASH = contracts.hash_directed_effect_arguments(_ARGUMENTS)
_AUTHORITY_FIELDS = (
    "workspace",
    "execution_attempt_id",
    "turn_id",
    "batch_id",
    "tool_call_id",
    "normalized_tool_name",
    "arguments_hash",
    "tool_spec_hash",
    "role_policy_id",
    "role_policy_hash",
    "canonical_allow_list_hash",
    "capability_scope",
    "capability_scope_hash",
    "job_token_id",
    "job_token_evidence_hash",
    "execution_envelope_hash",
    "allowed_command_hash",
    "mutation_guard_mode",
    "bound_policy_snapshot_hash",
    "target_state_hash",
    "normalized_operation_hash",
    "policy_version",
    "policy_hash",
)
_EXPECTED_ERROR_CODES = {
    "deo_tool_normalization_failed",
    "deo_tool_not_allowed",
    "deo_path_scope_denied",
    "deo_command_scope_denied",
    "deo_mutation_guard_denied",
    "deo_job_token_invalid",
    "deo_director_policy_denied",
    "deo_authorization_evidence_drift",
    "deo_target_state_drift",
    "deo_policy_version_drift",
    "deo_operation_hash_mismatch",
    "deo_execution_attempt_missing",
    "deo_execution_attempt_invalid",
    "deo_execution_attempt_heartbeat_failed",
    "deo_inventory_invalid",
    "deo_parent_stream_enrollment_failed",
    "deo_parent_admission_failed",
    "deo_operation_stream_enrollment_failed",
    "deo_inventory_seal_failed",
    "deo_member_admission_failed",
    "deo_inventory_ready_failed",
    "deo_claim_failed",
    "deo_execution_attempt_mismatch",
    "deo_parent_binding_mismatch",
    "deo_operation_identity_mismatch",
    "deo_member_identity_mismatch",
    "deo_inventory_hash_mismatch",
    "deo_claim_event_mismatch",
    "deo_operation_head_mismatch",
    "deo_parent_registry_head_mismatch",
    "deo_grant_hash_invalid",
    "deo_fence_capacity_exceeded",
    "deo_fence_pid_mismatch",
    "deo_context_not_registered",
    "deo_context_identity_mismatch",
    "deo_context_replayed",
    "deo_context_reconstructed",
    "deo_context_release_failed",
    "deo_physical_execution_failed",
    "deo_tool_classification_mismatch",
    "deo_malformed_nested_grant",
    "deo_bound_snapshot_member_mismatch",
    "deo_authorization_hash_drift",
    "deo_authorization_binding_drift",
    "deo_public_policy_evidence_drift",
    "deo_capability_scope_drift",
    "deo_job_token_evidence_drift",
    "deo_current_policy_evidence_unavailable",
}


class _AuthorizationEvidenceValues(TypedDict):
    workspace: str
    execution_attempt_id: str
    turn_id: str
    batch_id: str
    tool_call_id: str
    normalized_tool_name: str
    arguments_hash: str
    tool_spec_hash: str
    role_policy_id: str
    role_policy_hash: str
    canonical_allow_list_hash: str
    capability_scope: tuple[str, ...]
    capability_scope_hash: str
    job_token_id: str
    job_token_evidence_hash: str
    execution_envelope_hash: str
    allowed_command_hash: str
    mutation_guard_mode: Literal["strict"]
    bound_policy_snapshot_hash: str
    target_state_hash: str
    normalized_operation_hash: str
    policy_version: str
    policy_hash: str


def _evidence_values() -> _AuthorizationEvidenceValues:
    return {
        "workspace": "/workspace",
        "execution_attempt_id": "session-1:1",
        "turn_id": "turn-1",
        "batch_id": "batch-1",
        "tool_call_id": "call-1",
        "normalized_tool_name": "write_file",
        "arguments_hash": _ARGUMENTS_HASH,
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
        "bound_policy_snapshot_hash": _HASH,
        "target_state_hash": _HASH,
        "normalized_operation_hash": _HASH,
        "policy_version": "v1",
        "policy_hash": _HASH,
    }


def _authorization_hash(values: _AuthorizationEvidenceValues) -> str:
    return contracts.hash_director_effect_authorization_evidence(
        workspace=values["workspace"],
        execution_attempt_id=values["execution_attempt_id"],
        turn_id=values["turn_id"],
        batch_id=values["batch_id"],
        tool_call_id=values["tool_call_id"],
        normalized_tool_name=values["normalized_tool_name"],
        arguments_hash=values["arguments_hash"],
        tool_spec_hash=values["tool_spec_hash"],
        role_policy_id=values["role_policy_id"],
        role_policy_hash=values["role_policy_hash"],
        canonical_allow_list_hash=values["canonical_allow_list_hash"],
        capability_scope=values["capability_scope"],
        capability_scope_hash=values["capability_scope_hash"],
        job_token_id=values["job_token_id"],
        job_token_evidence_hash=values["job_token_evidence_hash"],
        execution_envelope_hash=values["execution_envelope_hash"],
        allowed_command_hash=values["allowed_command_hash"],
        mutation_guard_mode=values["mutation_guard_mode"],
        bound_policy_snapshot_hash=values["bound_policy_snapshot_hash"],
        target_state_hash=values["target_state_hash"],
        normalized_operation_hash=values["normalized_operation_hash"],
        policy_version=values["policy_version"],
        policy_hash=values["policy_hash"],
    )


def _evidence_from_values(values: _AuthorizationEvidenceValues) -> contracts.DirectorEffectAuthorizationEvidenceV1:
    return contracts.DirectorEffectAuthorizationEvidenceV1(
        **values,
        authorization_hash=_authorization_hash(values),
    )


def _evidence() -> contracts.DirectorEffectAuthorizationEvidenceV1:
    return _evidence_from_values(_evidence_values())


def _forged_evidence(
    evidence: contracts.DirectorEffectAuthorizationEvidenceV1 | None = None,
    **changes: object,
) -> contracts.DirectorEffectAuthorizationEvidenceV1:
    baseline = evidence or _evidence()
    forged = object.__new__(contracts.DirectorEffectAuthorizationEvidenceV1)
    for field in fields(baseline):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(baseline, field.name)))
    return forged


def _classification(
    *,
    applicability: contracts.DirectedEffectApplicabilityV1 = "mutation_capable",
) -> contracts.DirectedEffectClassificationResultV1:
    return contracts.DirectedEffectClassificationResultV1(
        applicability=applicability,
        canonical_tool_name="write_file",
        normalized_arguments=_ARGUMENTS,
        arguments_hash=_ARGUMENTS_HASH,
    )


def _claim_grant() -> DirectedEffectClaimGrantV1:
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
    registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempt)
    binding = DirectedEffectParentBindingV1(
        schema_version=DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
        registry_identity=registry_identity,
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
    member = DirectedEffectInventoryMemberV1(
        ordinal=1,
        tool_call_id="call-1",
        effect_id="effect-1",
        operation_id="operation-1",
        normalized_tool_name="write_file",
        effect_type="write",
        execution_mode="write_serial",
        intended_effect_fingerprint=_HASH,
        policy_verdict_hash=_HASH,
        expected_receipt_binding_hash=_HASH,
    )
    operation = DirectedEffectOperationIdentityV1(
        workspace=attempt.workspace,
        task_id=attempt.task_id,
        execution_attempt_id=registry_identity.execution_attempt_id,
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


def _forged_binding(
    binding: contracts.DirectorEffectAuthorizationBindingV1,
    **changes: object,
) -> contracts.DirectorEffectAuthorizationBindingV1:
    forged = object.__new__(contracts.DirectorEffectAuthorizationBindingV1)
    for field in fields(binding):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(binding, field.name)))
    return forged


def _forged_bound(
    bound: policy_contracts.DirectorEffectPolicyBoundSnapshotV1,
    **changes: object,
) -> policy_contracts.DirectorEffectPolicyBoundSnapshotV1:
    forged = object.__new__(policy_contracts.DirectorEffectPolicyBoundSnapshotV1)
    for field in fields(bound):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(bound, field.name)))
    return forged


def _forged_policy(
    policy: contracts.DirectorEffectPublicPolicyEvidenceV1,
    **changes: object,
) -> contracts.DirectorEffectPublicPolicyEvidenceV1:
    forged = object.__new__(contracts.DirectorEffectPublicPolicyEvidenceV1)
    for field in fields(policy):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(policy, field.name)))
    return forged


def test_all_deo_results_are_frozen_typed_and_closed() -> None:
    """Every authorization value is a frozen, slotted contract with closed codes."""
    classes = (
        contracts.DirectedEffectClassificationResultV1,
        contracts.DirectorEffectClassificationEvidenceV1,
        contracts.DirectorEffectAuthorizationEvidenceV1,
        contracts.DirectorEffectAuthorizationBindingV1,
        contracts.DirectorEffectPublicPolicyEvidenceV1,
        contracts.DirectorEffectExecutionEvidenceComparisonRequestV1,
        contracts.DirectorEffectExecutionEvidenceComparisonResultV1,
        contracts.DirectorEffectPreflightRequestV1,
        contracts.DirectorEffectPreflightResultV1,
        contracts.DirectorEffectExecutionValidationRequestV1,
        contracts.DirectorEffectExecutionValidationResultV1,
    )
    assert all(is_dataclass(contract_type) for contract_type in classes)
    assert all(hasattr(contract_type, "__slots__") for contract_type in classes)
    assert set(get_args(contracts.DirectedEffectErrorCodeV1)) == _EXPECTED_ERROR_CODES
    assert set(get_args(contracts.DirectorEffectPreflightStatusV1)) == {
        "authorized",
        "not_applicable",
        "denied",
    }

    evidence = _evidence()
    with pytest.raises(AttributeError):
        evidence.workspace = "/other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        contracts.DirectedEffectClassificationResultV1(
            applicability="mutation_capable",
            canonical_tool_name="write_file",
            normalized_arguments=cast(contracts.DirectedEffectImmutableItemsV1, {"path": "src/a.py"}),
            arguments_hash=_HASH,
        )


def _authority_replacement(field_name: str) -> object:
    if field_name == "capability_scope":
        return ("other/",)
    if field_name == "mutation_guard_mode":
        return "relaxed"
    if field_name.endswith("_hash"):
        return _OTHER_HASH
    return f"other-{field_name}"


@pytest.mark.parametrize("field_name", _AUTHORITY_FIELDS)
def test_authorization_hash_binds_every_authority_field_and_rejects_substitution(field_name: str) -> None:
    """No authority-bearing field can retain another evidence object's digest."""
    values = _evidence_values()
    baseline_hash = _authorization_hash(values)
    substituted = cast(_AuthorizationEvidenceValues, {**values, field_name: _authority_replacement(field_name)})

    assert _authorization_hash(substituted) != baseline_hash
    with pytest.raises(ValueError):
        contracts.DirectorEffectAuthorizationEvidenceV1(
            **substituted,
            authorization_hash=baseline_hash,
        )


def test_authorization_hash_is_exported_and_forged_object_is_detectable() -> None:
    """The public facade exposes the canonical hash used to detect wire bypasses."""
    evidence = _evidence()
    forged = object.__new__(contracts.DirectorEffectAuthorizationEvidenceV1)
    for field in fields(evidence):
        object.__setattr__(
            forged,
            field.name,
            _OTHER_HASH if field.name == "policy_hash" else getattr(evidence, field.name),
        )
    forged_values = cast(
        _AuthorizationEvidenceValues,
        {field_name: getattr(forged, field_name) for field_name in _AUTHORITY_FIELDS},
    )

    assert director_public.hash_director_effect_authorization_evidence is (
        contracts.hash_director_effect_authorization_evidence
    )
    assert _authorization_hash(forged_values) != forged.authorization_hash


def test_read_only_preflight_is_successful_non_error() -> None:
    """A read-only call is intentionally outside DEO mutation admission."""
    classification = contracts.DirectedEffectClassificationResultV1(
        applicability="read_only",
        canonical_tool_name="read_file",
        normalized_arguments=_ARGUMENTS,
        arguments_hash=_ARGUMENTS_HASH,
    )
    result = contracts.DirectorEffectPreflightResultV1(
        status="not_applicable",
        applicability="read_only",
        intent=None,
        evidence=None,
        error_code=None,
    )

    assert classification.error_code is None
    assert result.status == "not_applicable"
    assert result.error_code is None
    assert result.intent is None
    assert result.evidence is None


def test_preflight_cross_field_invariants_fail_closed() -> None:
    """Denied and authorized preflights cannot claim conflicting evidence."""
    with pytest.raises(ValueError, match="not_applicable"):
        contracts.DirectorEffectPreflightResultV1(
            status="not_applicable",
            applicability="mutation_capable",
            intent=None,
            evidence=None,
            error_code=None,
        )
    with pytest.raises(ValueError, match="error_code"):
        contracts.DirectorEffectExecutionValidationResultV1(
            allowed=False,
            status="denied",
            error_code=None,
        )


def test_preflight_request_binds_classification_to_authorization_evidence() -> None:
    """A mutation preflight binds the exact classification and evidence identity."""
    request = contracts.DirectorEffectPreflightRequestV1(
        classification=_classification(),
        authorization_evidence=_evidence(),
    )

    assert request.classification.canonical_tool_name == request.authorization_evidence.normalized_tool_name
    assert request.classification.arguments_hash == request.authorization_evidence.arguments_hash


@pytest.mark.parametrize(
    ("classification", "evidence", "match"),
    (
        pytest.param(
            _classification(),
            _forged_evidence(normalized_tool_name="execute_command"),
            "canonical_tool_name",
            id="tool-name-drift",
        ),
        pytest.param(
            _classification(),
            _forged_evidence(arguments_hash=_OTHER_HASH),
            "arguments_hash",
            id="arguments-hash-drift",
        ),
        pytest.param(
            _classification(applicability="read_only"),
            _evidence(),
            "mutation_capable",
            id="read-only-preflight-request",
        ),
    ),
)
def test_preflight_request_rejects_applicability_and_identity_drift(
    classification: contracts.DirectedEffectClassificationResultV1,
    evidence: contracts.DirectorEffectAuthorizationEvidenceV1,
    match: str,
) -> None:
    """Classification/evidence drift fails closed before intent derivation."""
    with pytest.raises(ValueError, match=match):
        contracts.DirectorEffectPreflightRequestV1(
            classification=classification,
            authorization_evidence=evidence,
        )


def test_authorization_contracts_instantiate_full_execution_validation_path() -> None:
    """Every Director authorization contract has a valid concrete construction."""
    classification = _classification()
    evidence = _evidence()
    preflight_request = contracts.DirectorEffectPreflightRequestV1(
        classification=classification,
        authorization_evidence=evidence,
    )
    execution_request = contracts.DirectorEffectExecutionValidationRequestV1(
        actual_normalized_tool_name="write_file",
        actual_arguments_hash=_ARGUMENTS_HASH,
        current_policy_hash=_HASH,
        current_scope_hash=_HASH,
        current_job_token_evidence_hash=_HASH,
        expected_context_id="context-1",
        authorization_evidence=evidence,
        claim_grant=_claim_grant(),
    )
    allowed = contracts.DirectorEffectExecutionValidationResultV1(
        allowed=True,
        status="allowed",
        error_code=None,
    )

    assert preflight_request.classification is classification
    assert execution_request.authorization_evidence is evidence
    assert allowed.allowed is True


def test_nested_mutable_authorization_payload_is_rejected() -> None:
    """Nested mutable payloads cannot cross the deeply immutable boundary."""
    with pytest.raises(TypeError, match="mutable mappings"):
        contracts.DirectedEffectClassificationResultV1(
            applicability="mutation_capable",
            canonical_tool_name="write_file",
            normalized_arguments=cast(
                contracts.DirectedEffectImmutableItemsV1,
                (("options", ({"overwrite": True},)),),
            ),
            arguments_hash=_HASH,
        )


@pytest.mark.parametrize(
    "case",
    (
        "tool",
        "arguments",
        "policy",
        "scope",
        "job_token",
        "batch_evidence",
        "attempt",
        "parent",
        "member",
        "operation",
    ),
)
def test_execution_validation_request_rejects_outer_and_nested_identity_drift(
    case: str,
) -> None:
    """Execution validation rejects every outer or nested identity mismatch."""
    evidence = _evidence()
    grant = _claim_grant()
    values: dict[str, object] = {
        "actual_normalized_tool_name": "write_file",
        "actual_arguments_hash": _ARGUMENTS_HASH,
        "current_policy_hash": _HASH,
        "current_scope_hash": _HASH,
        "current_job_token_evidence_hash": _HASH,
        "expected_context_id": "context-1",
        "authorization_evidence": evidence,
        "claim_grant": grant,
    }
    if case == "tool":
        values["actual_normalized_tool_name"] = "execute_command"
    elif case == "arguments":
        values["actual_arguments_hash"] = _OTHER_HASH
    elif case == "policy":
        values["current_policy_hash"] = _OTHER_HASH
    elif case == "scope":
        values["current_scope_hash"] = _OTHER_HASH
    elif case == "job_token":
        values["current_job_token_evidence_hash"] = _OTHER_HASH
    elif case == "batch_evidence":
        values["authorization_evidence"] = _forged_evidence(evidence, batch_id="batch-drift")
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
        contracts.DirectorEffectExecutionValidationRequestV1(
            actual_normalized_tool_name=cast(str, values["actual_normalized_tool_name"]),
            actual_arguments_hash=cast(str, values["actual_arguments_hash"]),
            current_policy_hash=cast(str, values["current_policy_hash"]),
            current_scope_hash=cast(str, values["current_scope_hash"]),
            current_job_token_evidence_hash=cast(str, values["current_job_token_evidence_hash"]),
            expected_context_id=cast(str, values["expected_context_id"]),
            authorization_evidence=cast(
                contracts.DirectorEffectAuthorizationEvidenceV1, values["authorization_evidence"]
            ),
            claim_grant=cast(DirectedEffectClaimGrantV1, values["claim_grant"]),
        )


def test_authorization_tokens_are_strict_and_public_statuses_are_exported() -> None:
    """Public token boundaries reject coercion and expose stable status aliases."""
    with pytest.raises(TypeError, match="canonical_tool_name"):
        contracts.DirectedEffectClassificationResultV1(
            applicability="mutation_capable",
            canonical_tool_name=cast(str, 7),
            normalized_arguments=(),
            arguments_hash=_HASH,
        )

    assert director_public.DirectorEffectPreflightStatusV1 is contracts.DirectorEffectPreflightStatusV1
    assert (
        director_public.DirectorEffectExecutionValidationStatusV1 is contracts.DirectorEffectExecutionValidationStatusV1
    )


def test_contract_fields_do_not_expose_mutable_mapping_annotations() -> None:
    """Authorization values carry canonical immutable tuples rather than maps."""
    annotation = next(
        field.type
        for field in fields(contracts.DirectedEffectClassificationResultV1)
        if field.name == "normalized_arguments"
    )
    assert "Mapping" not in str(annotation)


def test_task4_binding_and_public_policy_projection_are_self_hashing() -> None:
    """Task4 wraps, but never changes, the frozen Task3 authorization evidence."""
    classification = contracts.DirectorEffectClassificationEvidenceV1(
        raw_tool_name="write_file",
        canonical_tool_name="write_file",
        effect_type="write",
        execution_mode="write_serial",
        normalized_arguments=_ARGUMENTS,
        arguments_hash=_ARGUMENTS_HASH,
        tool_spec_hash=_HASH,
        tool_spec_snapshot_hash=_HASH,
        alias_binding_hash=_HASH,
    )
    binding = contracts.DirectorEffectAuthorizationBindingV1(
        authorization_evidence=_evidence(),
        classification_evidence=classification,
        tool_spec_hash=_HASH,
        tool_spec_snapshot_hash=_HASH,
        alias_binding_hash=_HASH,
    )

    policy = contracts.project_director_effect_public_policy_evidence(binding)

    assert binding.classification_evidence_hash == classification.classification_evidence_hash
    assert policy.source_authorization_binding_hash == binding.authorization_binding_hash
    assert policy.public_policy_evidence_hash


def _comparison_snapshot() -> policy_contracts.DirectorEffectPolicySnapshotResultV1:
    target_hash = policy_contracts.hash_directed_effect_target_state_components(
        target_path="src/a.py",
        exists=True,
        before_content_hash=_HASH,
        minimal_content_evidence=(("prefix", "content"),),
        agents_policy_hash=_HASH,
        is_no_file_state=False,
    )
    target = policy_contracts.DirectorEffectTargetStateEvidenceV1(
        target_path="src/a.py",
        exists=True,
        before_content_hash=_HASH,
        minimal_content_evidence=(("prefix", "content"),),
        agents_policy_hash=_HASH,
        target_state_hash=target_hash,
        is_no_file_state=False,
    )
    subject = policy_contracts.DirectorEffectPolicyOperationSubjectV1(
        workspace="/workspace",
        turn_id="turn-1",
        batch_id="batch-1",
        tool_call_id="call-1",
        inventory_ordinal=1,
        normalized_tool_name="write_file",
        normalized_arguments=_ARGUMENTS,
        effect_type="write",
        execution_mode="write_serial",
        prospective_operation_hash=_HASH,
    )
    snapshot_hash = policy_contracts.hash_directed_effect_policy_snapshot_evidence(
        status="allowed",
        allowed=True,
        error_code=None,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=target,
        normalized_operation_hash=_HASH,
    )
    return policy_contracts.DirectorEffectPolicySnapshotResultV1(
        status="allowed",
        allowed=True,
        error_code=None,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=target,
        target_state_hash=target.target_state_hash,
        normalized_operation_hash=_HASH,
        evidence_hash=snapshot_hash,
    )


def _comparison_binding() -> tuple[
    contracts.DirectorEffectAuthorizationBindingV1,
    policy_contracts.DirectorEffectPolicyBoundSnapshotV1,
    DirectedEffectClaimGrantV1,
]:
    grant = _claim_grant()
    snapshot = _comparison_snapshot()
    values = _evidence_values()
    values.update(
        {
            "bound_policy_snapshot_hash": snapshot.evidence_hash,
            "target_state_hash": snapshot.target_state_hash,
        }
    )
    authorization = _evidence_from_values(values)
    classification = contracts.DirectorEffectClassificationEvidenceV1(
        raw_tool_name="write_file",
        canonical_tool_name="write_file",
        effect_type="write",
        execution_mode="write_serial",
        normalized_arguments=_ARGUMENTS,
        arguments_hash=_ARGUMENTS_HASH,
        tool_spec_hash=_HASH,
        tool_spec_snapshot_hash=_HASH,
        alias_binding_hash=_HASH,
    )
    binding = contracts.DirectorEffectAuthorizationBindingV1(
        authorization_evidence=authorization,
        classification_evidence=classification,
        tool_spec_hash=_HASH,
        tool_spec_snapshot_hash=_HASH,
        alias_binding_hash=_HASH,
    )
    bound = policy_contracts.DirectorEffectPolicyBoundSnapshotV1(
        snapshot=snapshot,
        authorization_evidence_hash=authorization.authorization_hash,
        authorization_binding=binding,
        authorization_binding_hash=binding.authorization_binding_hash,
        member=grant.member,
        member_binding_hash=policy_contracts.hash_directed_effect_policy_member_binding(
            snapshot.evidence_hash,
            authorization.authorization_hash,
            binding.authorization_binding_hash,
            grant.member,
        ),
    )
    return binding, bound, grant


def test_task4_comparator_accepts_only_equal_baseline_structure() -> None:
    """The comparator proves identity equality only and makes no freshness claim."""
    binding, bound, grant = _comparison_binding()
    public_policy = contracts.project_director_effect_public_policy_evidence(binding)

    result = director_public.compare_directed_effect_execution_evidence(
        contracts.DirectorEffectExecutionEvidenceComparisonRequestV1(
            baseline_authorization_binding=binding,
            baseline_public_policy_evidence=public_policy,
            supplied_authorization_binding=binding,
            supplied_public_policy_evidence=public_policy,
            supplied_bound_snapshot=bound,
            supplied_member=grant.member,
            supplied_grant=grant,
            supplied_normalized_tool="write_file",
            supplied_arguments_hash=_ARGUMENTS_HASH,
        )
    )

    assert result.status == "matched"
    assert result.comparison_scope == "structure_hash_identity_only"
    assert not hasattr(result, "current")
    assert not hasattr(result, "fresh")


def _rehash_evidence(
    evidence: contracts.DirectorEffectAuthorizationEvidenceV1,
    **changes: object,
) -> contracts.DirectorEffectAuthorizationEvidenceV1:
    values = cast(
        _AuthorizationEvidenceValues,
        {field_name: changes.get(field_name, getattr(evidence, field_name)) for field_name in _AUTHORITY_FIELDS},
    )
    return _evidence_from_values(values)


def _comparison_request(**changes: object) -> contracts.DirectorEffectExecutionEvidenceComparisonRequestV1:
    binding, bound, grant = _comparison_binding()
    public_policy = contracts.project_director_effect_public_policy_evidence(binding)
    return contracts.DirectorEffectExecutionEvidenceComparisonRequestV1(
        baseline_authorization_binding=cast(
            contracts.DirectorEffectAuthorizationBindingV1,
            changes.get("baseline_authorization_binding", binding),
        ),
        baseline_public_policy_evidence=cast(
            contracts.DirectorEffectPublicPolicyEvidenceV1,
            changes.get("baseline_public_policy_evidence", public_policy),
        ),
        supplied_authorization_binding=cast(
            contracts.DirectorEffectAuthorizationBindingV1,
            changes.get("supplied_authorization_binding", binding),
        ),
        supplied_public_policy_evidence=cast(
            contracts.DirectorEffectPublicPolicyEvidenceV1,
            changes.get("supplied_public_policy_evidence", public_policy),
        ),
        supplied_bound_snapshot=cast(
            policy_contracts.DirectorEffectPolicyBoundSnapshotV1,
            changes.get("supplied_bound_snapshot", bound),
        ),
        supplied_member=cast(DirectedEffectInventoryMemberV1, changes.get("supplied_member", grant.member)),
        supplied_grant=cast(DirectedEffectClaimGrantV1, changes.get("supplied_grant", grant)),
        supplied_normalized_tool=cast(str, changes.get("supplied_normalized_tool", "write_file")),
        supplied_arguments_hash=cast(str, changes.get("supplied_arguments_hash", _ARGUMENTS_HASH)),
    )


def _bound_with_authorization(
    bound: policy_contracts.DirectorEffectPolicyBoundSnapshotV1,
    authorization: contracts.DirectorEffectAuthorizationEvidenceV1,
) -> policy_contracts.DirectorEffectPolicyBoundSnapshotV1:
    prior_binding = bound.authorization_binding
    binding = contracts.DirectorEffectAuthorizationBindingV1(
        authorization_evidence=authorization,
        classification_evidence=prior_binding.classification_evidence,
        tool_spec_hash=prior_binding.tool_spec_hash,
        tool_spec_snapshot_hash=prior_binding.tool_spec_snapshot_hash,
        alias_binding_hash=prior_binding.alias_binding_hash,
    )
    return policy_contracts.DirectorEffectPolicyBoundSnapshotV1(
        snapshot=bound.snapshot,
        authorization_evidence_hash=authorization.authorization_hash,
        authorization_binding=binding,
        authorization_binding_hash=binding.authorization_binding_hash,
        member=bound.member,
        member_binding_hash=policy_contracts.hash_directed_effect_policy_member_binding(
            bound.snapshot.evidence_hash,
            authorization.authorization_hash,
            binding.authorization_binding_hash,
            bound.member,
        ),
    )


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        (
            "malformed_grant",
            "deo_malformed_nested_grant",
        ),
        (
            "legacy_authorization_digest",
            "deo_authorization_binding_drift",
        ),
        (
            "additive_binding_digest",
            "deo_authorization_binding_drift",
        ),
        (
            "bound_member_binding",
            "deo_bound_snapshot_member_mismatch",
        ),
        (
            "bound_authorization_anchor",
            "deo_bound_snapshot_member_mismatch",
        ),
    ),
)
def test_task4_comparator_rejects_forged_nested_authority(
    case: str,
    expected_code: str,
) -> None:
    """Malformed grants and forged legacy/additive/bound anchors never compare equal."""
    binding, bound, grant = _comparison_binding()
    if case == "malformed_grant":
        request = _comparison_request(supplied_grant=_tamper_grant(grant, grant_hash=_OTHER_HASH))
    elif case == "legacy_authorization_digest":
        forged = _forged_evidence(binding.authorization_evidence, authorization_hash=_OTHER_HASH)
        request = _comparison_request(
            supplied_authorization_binding=_forged_binding(binding, authorization_evidence=forged)
        )
    elif case == "additive_binding_digest":
        request = _comparison_request(
            supplied_authorization_binding=_forged_binding(binding, authorization_binding_hash=_OTHER_HASH)
        )
    elif case == "bound_member_binding":
        request = _comparison_request(supplied_bound_snapshot=_forged_bound(bound, member_binding_hash=_OTHER_HASH))
    else:
        request = _comparison_request(
            supplied_bound_snapshot=_forged_bound(bound, authorization_evidence_hash=_OTHER_HASH)
        )

    result = director_public.compare_directed_effect_execution_evidence(request)

    assert (result.status, result.matches, result.error_code) == ("denied", False, expected_code)


@pytest.mark.parametrize(
    "field",
    ("workspace", "tool_call_id", "normalized_tool_name", "intended_effect_fingerprint"),
)
def test_task4_comparator_rejects_grant_identity_or_fingerprint_substitution(field: str) -> None:
    """Grant workspace/call/tool/fingerprint changes cannot become a structural match."""
    _, _, grant = _comparison_binding()
    if field == "workspace":
        supplied_grant = _tamper_grant(grant, execution_attempt=replace(grant.execution_attempt, workspace="/other"))
    elif field == "tool_call_id":
        supplied_grant = _tamper_grant(grant, member=replace(grant.member, tool_call_id="call-other"))
    elif field == "normalized_tool_name":
        supplied_grant = _tamper_grant(grant, member=replace(grant.member, normalized_tool_name="execute_command"))
    else:
        supplied_grant = _tamper_grant(
            grant,
            member=replace(grant.member, intended_effect_fingerprint=_OTHER_HASH),
        )

    result = director_public.compare_directed_effect_execution_evidence(
        _comparison_request(supplied_grant=supplied_grant)
    )

    assert result.status == "denied"
    assert result.matches is False


@pytest.mark.parametrize(
    ("field", "expected_code"),
    (
        ("policy_hash", "deo_public_policy_evidence_drift"),
        ("capability_scope_hash", "deo_capability_scope_drift"),
        ("job_token_evidence_hash", "deo_job_token_evidence_drift"),
        ("tool_spec_hash", "deo_authorization_binding_drift"),
        ("alias_binding_hash", "deo_authorization_binding_drift"),
    ),
)
def test_task4_comparator_rejects_supplied_policy_scope_token_or_spec_drift(
    field: str,
    expected_code: str,
) -> None:
    """Supplied policy, scope, token, ToolSpec, and alias facts are all bound."""
    binding, bound, _ = _comparison_binding()
    if field == "policy_hash":
        supplied_binding = binding
        supplied_policy = _forged_policy(
            contracts.project_director_effect_public_policy_evidence(binding),
            policy_hash=_OTHER_HASH,
        )
        supplied_bound = bound
    elif field in {"capability_scope_hash", "job_token_evidence_hash"}:
        evidence = _rehash_evidence(binding.authorization_evidence, **{field: _OTHER_HASH})
        supplied_binding = contracts.DirectorEffectAuthorizationBindingV1(
            authorization_evidence=evidence,
            classification_evidence=binding.classification_evidence,
            tool_spec_hash=binding.tool_spec_hash,
            tool_spec_snapshot_hash=binding.tool_spec_snapshot_hash,
            alias_binding_hash=binding.alias_binding_hash,
        )
        supplied_policy = contracts.project_director_effect_public_policy_evidence(supplied_binding)
        supplied_bound = _bound_with_authorization(bound, evidence)
    elif field == "tool_spec_hash":
        supplied_binding = _forged_binding(binding, tool_spec_hash=_OTHER_HASH)
        supplied_policy = contracts.project_director_effect_public_policy_evidence(binding)
        supplied_bound = bound
    else:
        supplied_binding = _forged_binding(binding, alias_binding_hash=_OTHER_HASH)
        supplied_policy = contracts.project_director_effect_public_policy_evidence(binding)
        supplied_bound = bound

    result = director_public.compare_directed_effect_execution_evidence(
        _comparison_request(
            supplied_authorization_binding=supplied_binding,
            supplied_public_policy_evidence=supplied_policy,
            supplied_bound_snapshot=supplied_bound,
        )
    )

    assert (result.status, result.matches, result.error_code) == ("denied", False, expected_code)


def test_directed_effect_service_static_fence_excludes_kernel_adapter_taskruntime_and_effect_io() -> None:
    """The real comparator remains a pure Director public service boundary."""
    tree = ast.parse(inspect.getsource(service_module))
    modules = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    source = inspect.getsource(service_module)

    assert not any(module.startswith("polaris.cells.roles.kernel") for module in modules)
    assert not any(module.startswith("polaris.cells.roles.adapters") for module in modules)
    assert not any(module.startswith("polaris.cells.runtime.task_runtime") for module in modules)
    assert not any(token in source for token in ("subprocess", "pathlib", "open("))


def test_canonical_arguments_hash_normalizes_top_level_key_order() -> None:
    """Canonical argument hashing is deterministic across equivalent key order."""
    ordered = (("command", "pytest -q"), ("path", "src/a.py"))
    reversed_order = tuple(reversed(ordered))

    assert contracts.hash_directed_effect_arguments(ordered) == contracts.hash_directed_effect_arguments(reversed_order)


def test_classification_rejects_payload_drift_with_preserved_arguments_hash() -> None:
    """Classification cannot attach a digest produced for a different payload."""
    original = (("path", "src/a.py"),)
    original_hash = contracts.hash_directed_effect_arguments(original)

    with pytest.raises(ValueError, match=r"arguments_hash.*payload"):
        contracts.DirectedEffectClassificationResultV1(
            applicability="mutation_capable",
            canonical_tool_name="write_file",
            normalized_arguments=(("path", "src/b.py"),),
            arguments_hash=original_hash,
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "workspace",
        "execution_attempt_id",
        "turn_id",
        "batch_id",
        "tool_call_id",
        "normalized_tool_name",
        "parent_binding_id",
        "effect_id",
        "operation_id",
    ),
)
@pytest.mark.parametrize("invalid_value", (7, " "))
def test_identity_view_rejects_invalid_identity_tokens(field_name: str, invalid_value: object) -> None:
    """Every identity-view token is validated even on direct construction."""
    values: dict[str, object] = {
        "workspace": "/workspace",
        "execution_attempt_id": "session-1:1",
        "turn_id": "turn-1",
        "batch_id": "batch-1",
        "tool_call_id": "call-1",
        "normalized_tool_name": "write_file",
        "arguments_hash": _HASH,
        "parent_binding_id": "binding-1",
        "effect_id": "effect-1",
        "operation_id": "operation-1",
    }
    values[field_name] = invalid_value

    with pytest.raises((TypeError, ValueError), match=field_name):
        contracts.DirectedEffectIdentityViewV1(
            workspace=cast(str, values["workspace"]),
            execution_attempt_id=cast(str, values["execution_attempt_id"]),
            turn_id=cast(str, values["turn_id"]),
            batch_id=cast(str, values["batch_id"]),
            tool_call_id=cast(str, values["tool_call_id"]),
            normalized_tool_name=cast(str, values["normalized_tool_name"]),
            arguments_hash=cast(str, values["arguments_hash"]),
            parent_binding_id=cast(str, values["parent_binding_id"]),
            effect_id=cast(str, values["effect_id"]),
            operation_id=cast(str, values["operation_id"]),
        )


@pytest.mark.parametrize("invalid_hash", (7, "", "not-a-digest"))
def test_identity_view_rejects_invalid_arguments_hash(invalid_hash: object) -> None:
    """The identity view validates its digest on direct construction."""
    with pytest.raises((TypeError, ValueError), match="arguments_hash"):
        contracts.DirectedEffectIdentityViewV1(
            workspace="/workspace",
            execution_attempt_id="session-1:1",
            turn_id="turn-1",
            batch_id="batch-1",
            tool_call_id="call-1",
            normalized_tool_name="write_file",
            arguments_hash=cast(str, invalid_hash),
            parent_binding_id="binding-1",
            effect_id="effect-1",
            operation_id="operation-1",
        )


@pytest.mark.parametrize("invalid_allowed", (0, 1, "true"))
def test_execution_validation_allowed_requires_exact_bool(invalid_allowed: object) -> None:
    """Authorization verdicts reject truthy and falsy bool substitutes."""
    with pytest.raises(TypeError, match="allowed"):
        contracts.DirectorEffectExecutionValidationResultV1(
            allowed=cast(bool, invalid_allowed),
            status="allowed",
            error_code=None,
        )


def test_canonical_arguments_hash_normalizes_nested_map_order() -> None:
    """Equivalent maps hash identically at every nested depth."""
    left = (
        (
            "options",
            contracts.DirectedEffectImmutableMapV1(
                items=(
                    ("encoding", "utf-8"),
                    ("flags", contracts.DirectedEffectImmutableMapV1(items=(("a", 1), ("b", 2)))),
                ),
            ),
        ),
    )
    right = (
        (
            "options",
            contracts.DirectedEffectImmutableMapV1(
                items=(
                    ("flags", contracts.DirectedEffectImmutableMapV1(items=(("b", 2), ("a", 1)))),
                    ("encoding", "utf-8"),
                ),
            ),
        ),
    )

    assert contracts.hash_directed_effect_arguments(left) == contracts.hash_directed_effect_arguments(right)


def test_canonical_arguments_hash_rejects_nested_duplicate_map_keys() -> None:
    """Duplicate keys fail closed inside nested maps, not only at the root."""
    with pytest.raises(ValueError, match="duplicate"):
        contracts.DirectedEffectImmutableMapV1(
            items=(("path", "src/a.py"), ("path", "src/b.py")),
        )


def test_canonical_arguments_hash_distinguishes_map_and_ordered_sequence() -> None:
    """Container tags prevent map/sequence collisions and preserve sequence order."""
    map_value = contracts.DirectedEffectImmutableMapV1(items=(("key", "value"),))
    sequence_value = contracts.DirectedEffectImmutableSequenceV1(items=(("key", "value"),))
    forward = contracts.DirectedEffectImmutableSequenceV1(items=("first", "second"))
    reverse = contracts.DirectedEffectImmutableSequenceV1(items=("second", "first"))

    assert contracts.hash_directed_effect_arguments(
        (("value", map_value),)
    ) != contracts.hash_directed_effect_arguments((("value", sequence_value),))
    assert contracts.hash_directed_effect_arguments((("value", forward),)) != contracts.hash_directed_effect_arguments(
        (("value", reverse),)
    )


def test_canonical_arguments_hash_handles_deep_map_sequence_composition() -> None:
    """Recursive tags remain stable through mixed maps and ordered sequences."""
    first = contracts.DirectedEffectImmutableMapV1(
        items=(
            (
                "steps",
                contracts.DirectedEffectImmutableSequenceV1(
                    items=(
                        contracts.DirectedEffectImmutableMapV1(items=(("z", 3), ("a", 1))),
                        "done",
                    ),
                ),
            ),
            ("version", 1),
        ),
    )
    equivalent = contracts.DirectedEffectImmutableMapV1(
        items=(
            ("version", 1),
            (
                "steps",
                contracts.DirectedEffectImmutableSequenceV1(
                    items=(
                        contracts.DirectedEffectImmutableMapV1(items=(("a", 1), ("z", 3))),
                        "done",
                    ),
                ),
            ),
        ),
    )

    assert contracts.hash_directed_effect_arguments((("config", first),)) == contracts.hash_directed_effect_arguments(
        (("config", equivalent),)
    )
