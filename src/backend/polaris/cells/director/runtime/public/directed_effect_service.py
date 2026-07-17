"""Pure Task4 directed-effect evidence projection and comparison services."""

from __future__ import annotations

from dataclasses import fields

from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectorEffectAuthorizationBindingV1,
    DirectorEffectExecutionEvidenceComparisonRequestV1,
    DirectorEffectExecutionEvidenceComparisonResultV1,
    DirectorEffectPublicPolicyEvidenceV1,
    validate_directed_effect_identity_binding,
    validate_director_effect_authorization_binding,
    validate_director_effect_authorization_evidence,
    validate_director_effect_public_policy_evidence,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    DirectorEffectPolicyBoundSnapshotV1,
)


def _denied(error_code: str) -> DirectorEffectExecutionEvidenceComparisonResultV1:
    return DirectorEffectExecutionEvidenceComparisonResultV1(
        status="denied",
        matches=False,
        error_code=error_code,  # type: ignore[arg-type]
    )


def _grant_is_canonical(grant: object) -> bool:
    """Recreate the TaskRuntime grant to reject malformed nested records."""
    try:
        canonical = type(grant)(**{field.name: getattr(grant, field.name) for field in fields(grant)})  # type: ignore[arg-type]
    except (AttributeError, TypeError, ValueError):
        return False
    return canonical == grant


def _bound_snapshot_is_canonical(bound_snapshot: object) -> bool:
    try:
        if not isinstance(bound_snapshot, DirectorEffectPolicyBoundSnapshotV1):
            return False
        canonical = DirectorEffectPolicyBoundSnapshotV1(
            snapshot=bound_snapshot.snapshot,
            authorization_evidence_hash=bound_snapshot.authorization_evidence_hash,
            member=bound_snapshot.member,
            member_binding_hash=bound_snapshot.member_binding_hash,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return canonical == bound_snapshot


def _policy_matches_binding(
    binding: DirectorEffectAuthorizationBindingV1,
    policy: DirectorEffectPublicPolicyEvidenceV1,
) -> bool:
    evidence = binding.authorization_evidence
    return bool(
        policy.source_authorization_binding_hash == binding.authorization_binding_hash
        and policy.role_policy_id == evidence.role_policy_id
        and policy.role_policy_hash == evidence.role_policy_hash
        and policy.canonical_allow_list_hash == evidence.canonical_allow_list_hash
        and policy.capability_scope == evidence.capability_scope
        and policy.capability_scope_hash == evidence.capability_scope_hash
        and policy.job_token_id == evidence.job_token_id
        and policy.job_token_evidence_hash == evidence.job_token_evidence_hash
        and policy.execution_envelope_hash == evidence.execution_envelope_hash
        and policy.allowed_command_hash == evidence.allowed_command_hash
        and policy.mutation_guard_mode == evidence.mutation_guard_mode
        and policy.policy_version == evidence.policy_version
        and policy.policy_hash == evidence.policy_hash
        and policy.classification_evidence_hash == binding.classification_evidence_hash
        and policy.tool_spec_hash == binding.tool_spec_hash
        and policy.tool_spec_snapshot_hash == binding.tool_spec_snapshot_hash
        and policy.alias_binding_hash == binding.alias_binding_hash
    )


def compare_directed_effect_execution_evidence(
    request: DirectorEffectExecutionEvidenceComparisonRequestV1,
) -> DirectorEffectExecutionEvidenceComparisonResultV1:
    """Compare baseline and supplied evidence using only hashes and identities."""
    try:
        baseline = validate_director_effect_authorization_binding(request.baseline_authorization_binding)
        supplied = validate_director_effect_authorization_binding(request.supplied_authorization_binding)
    except (AttributeError, TypeError, ValueError):
        return _denied("deo_authorization_binding_drift")
    try:
        validate_director_effect_authorization_evidence(baseline.authorization_evidence)
        validate_director_effect_authorization_evidence(supplied.authorization_evidence)
    except (AttributeError, TypeError, ValueError):
        return _denied("deo_authorization_hash_drift")
    try:
        baseline_policy = validate_director_effect_public_policy_evidence(request.baseline_public_policy_evidence)
        supplied_policy = validate_director_effect_public_policy_evidence(request.supplied_public_policy_evidence)
    except (AttributeError, TypeError, ValueError):
        return _denied("deo_public_policy_evidence_drift")
    if not _policy_matches_binding(baseline, baseline_policy) or not _policy_matches_binding(supplied, supplied_policy):
        return _denied("deo_public_policy_evidence_drift")
    if not _grant_is_canonical(request.supplied_grant):
        return _denied("deo_malformed_nested_grant")
    if not _bound_snapshot_is_canonical(request.supplied_bound_snapshot):
        return _denied("deo_bound_snapshot_member_mismatch")
    bound_snapshot = request.supplied_bound_snapshot
    assert isinstance(bound_snapshot, DirectorEffectPolicyBoundSnapshotV1)
    if (
        bound_snapshot.member != request.supplied_member
        or bound_snapshot.authorization_evidence_hash != supplied.authorization_evidence.authorization_hash
        or bound_snapshot.snapshot.evidence_hash != supplied.authorization_evidence.bound_policy_snapshot_hash
        or bound_snapshot.snapshot.policy_hash != supplied.authorization_evidence.policy_hash
        or bound_snapshot.snapshot.target_state_hash != supplied.authorization_evidence.target_state_hash
    ):
        return _denied("deo_bound_snapshot_member_mismatch")
    if (
        request.supplied_normalized_tool != supplied.authorization_evidence.normalized_tool_name
        or request.supplied_arguments_hash != supplied.authorization_evidence.arguments_hash
    ):
        return _denied("deo_authorization_hash_drift")
    try:
        validate_directed_effect_identity_binding(
            boundary_name="execution evidence comparison",
            authorization_evidence=supplied.authorization_evidence,
            claim_grant=request.supplied_grant,
            normalized_tool_name=request.supplied_normalized_tool,
            arguments_hash=request.supplied_arguments_hash,
            workspace=supplied.authorization_evidence.workspace,
            member=request.supplied_member,
            operation_id=request.supplied_member.operation_id,
        )
    except (AttributeError, TypeError, ValueError):
        return _denied("deo_bound_snapshot_member_mismatch")
    if baseline_policy.capability_scope_hash != supplied_policy.capability_scope_hash:
        return _denied("deo_capability_scope_drift")
    if baseline_policy.job_token_evidence_hash != supplied_policy.job_token_evidence_hash:
        return _denied("deo_job_token_evidence_drift")
    if (
        baseline.authorization_binding_hash != supplied.authorization_binding_hash
        or baseline_policy.public_policy_evidence_hash != supplied_policy.public_policy_evidence_hash
    ):
        return _denied("deo_authorization_binding_drift")
    return DirectorEffectExecutionEvidenceComparisonResultV1(status="matched", matches=True, error_code=None)
