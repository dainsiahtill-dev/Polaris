"""Evidence-only adapter for the DEO-2B Director policy snapshot protocol."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from polaris.cells.director.runtime.public import (
    DirectedEffectImmutableMapV1,
    DirectorEffectAuthorizationEvidenceV1,
    DirectorEffectPolicyBoundSnapshotV1,
    DirectorEffectPolicyMemberBindingRequestV1,
    DirectorEffectPolicyMemberBindingResultV1,
    DirectorEffectPolicyOperationSubjectV1,
    DirectorEffectPolicyRevalidationRequestV1,
    DirectorEffectPolicyRevalidationResultV1,
    DirectorEffectPolicySnapshotRequestV1,
    DirectorEffectPolicySnapshotResultV1,
    DirectorEffectTargetStateEvidenceV1,
    hash_directed_effect_arguments,
    hash_directed_effect_policy_member_binding,
    hash_directed_effect_policy_revalidation_evidence,
    hash_director_effect_authorization_evidence,
    validate_directed_effect_identity_binding,
)
from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectedEffectErrorCodeV1,
    DirectedEffectImmutableItemsV1,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    DirectorEffectPolicySnapshotStatusV1,
    hash_directed_effect_policy_snapshot_evidence,
    hash_directed_effect_target_state_components,
)
from polaris.cells.runtime.task_runtime.public import (
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryMemberV1,
)
from polaris.kernelone.llm.toolkit.executor.command_capability import (
    CommandCapabilityValidationInputV1,
    validate_command_capability,
)

from .execution_tools import DirectorToolExecutor

_NO_FILE_HASH = "0" * 64
_WRITE_TOOLS = frozenset({"write_file", "edit_file", "delete_file"})
_COMMAND_TOOLS = frozenset({"execute_command", "run_command"})


@dataclass(frozen=True, slots=True)
class _JobRestrictionEvidence:
    """Canonical, immutable Job Token restrictions used by this boundary."""

    token_id: str
    token_hash: str
    allowed_commands: tuple[str, ...]
    allowed_commands_hash: str
    allowed_paths: tuple[str, ...]
    allowed_paths_hash: str
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class _TargetRead:
    """One stable target read and its derived immutable policy evidence."""

    evidence: DirectorEffectTargetStateEvidenceV1
    old_content: str


def _hash_payload(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _operation_hash(
    *,
    workspace: str,
    turn_id: str,
    batch_id: str,
    tool_call_id: str,
    inventory_ordinal: int,
    normalized_tool_name: str,
    normalized_arguments: DirectedEffectImmutableItemsV1,
    effect_type: str,
    execution_mode: str,
) -> str:
    return hash_directed_effect_arguments(
        (
            ("batch_id", batch_id),
            ("effect_type", effect_type),
            ("execution_mode", execution_mode),
            ("inventory_ordinal", inventory_ordinal),
            ("normalized_arguments", DirectedEffectImmutableMapV1(items=normalized_arguments)),
            ("normalized_tool_name", normalized_tool_name),
            ("tool_call_id", tool_call_id),
            ("turn_id", turn_id),
            ("workspace", workspace),
        )
    )


def _subject_operation_hash(subject: DirectorEffectPolicyOperationSubjectV1) -> str:
    return _operation_hash(
        workspace=subject.workspace,
        turn_id=subject.turn_id,
        batch_id=subject.batch_id,
        tool_call_id=subject.tool_call_id,
        inventory_ordinal=subject.inventory_ordinal,
        normalized_tool_name=subject.normalized_tool_name,
        normalized_arguments=subject.normalized_arguments,
        effect_type=subject.effect_type,
        execution_mode=subject.execution_mode,
    )


def _items_to_dict(items: DirectedEffectImmutableItemsV1) -> dict[str, object]:
    return dict(items)


def _tuple_tokens(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(not isinstance(token, str) or not token.strip() for token in value):
        raise ValueError(f"{field_name} must be an immutable tuple of non-empty strings")
    return tuple(value)


def _scope_hash(field_name: str, values: tuple[str, ...]) -> str:
    return hash_directed_effect_arguments(((field_name, values),))


def _restriction_values(items: DirectedEffectImmutableItemsV1) -> _JobRestrictionEvidence:
    values = _items_to_dict(items)
    token_id = values.get("job_token_id")
    token_hash = values.get("job_token_hash")
    if (
        not isinstance(token_id, str)
        or not token_id.strip()
        or not isinstance(token_hash, str)
        or len(token_hash) != 64
    ):
        raise ValueError("job token evidence must contain exact id and hash")
    allowed_commands = _tuple_tokens(values.get("allowed_commands", ()), field_name="allowed_commands")
    allowed_paths = _tuple_tokens(values.get("allowed_paths", ()), field_name="allowed_paths")
    if allowed_commands != tuple(sorted(set(allowed_commands))) or allowed_paths != tuple(sorted(set(allowed_paths))):
        raise ValueError("job token scopes must be sorted and unique")
    allowed_commands_hash = values.get("allowed_commands_hash")
    allowed_paths_hash = values.get("allowed_paths_hash")
    if allowed_commands_hash != _scope_hash("allowed_commands", allowed_commands) or allowed_paths_hash != _scope_hash(
        "allowed_paths", allowed_paths
    ):
        raise ValueError("job token scope hashes must bind their canonical scopes")
    return _JobRestrictionEvidence(
        token_id=token_id,
        token_hash=token_hash,
        allowed_commands=allowed_commands,
        allowed_commands_hash=allowed_commands_hash,
        allowed_paths=allowed_paths,
        allowed_paths_hash=allowed_paths_hash,
        evidence_hash=hash_directed_effect_arguments(items),
    )


def _scope_tokens(items: DirectedEffectImmutableItemsV1, *, field_name: str) -> tuple[str, ...]:
    return _tuple_tokens(_items_to_dict(items).get(field_name, ()), field_name=field_name)


def _thaw(value: object) -> object:
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    items = getattr(value, "items", None)
    if isinstance(items, tuple):
        return {key: _thaw(item) for key, item in items}
    return value


class _DirectorEffectPolicySnapshotPort:
    """Private, stateless owner of Director policy evidence and revalidation."""

    def __init__(self, workspace: str) -> None:
        self._workspace = Path(workspace).expanduser().resolve()

    async def snapshot(self, request: DirectorEffectPolicySnapshotRequestV1) -> DirectorEffectPolicySnapshotResultV1:
        """Capture a no-effect snapshot from canonical workspace evidence."""
        result, _ = self._evaluate(request)
        return result

    def bind_member(
        self,
        request: DirectorEffectPolicyMemberBindingRequestV1,
    ) -> DirectorEffectPolicyMemberBindingResultV1:
        """Purely bind a successful snapshot to one exact sealed member."""
        snapshot = request.snapshot
        authorization = request.authorization_evidence
        if (
            self._snapshot_integrity_error(snapshot) is not None
            or not self._authorization_hash_matches(authorization)
            or not self._authorization_binds_snapshot(authorization, snapshot)
        ):
            return DirectorEffectPolicyMemberBindingResultV1(
                status="denied",
                error_code="deo_authorization_evidence_drift",
                member=None,
                member_binding_hash=None,
                bound_snapshot=None,
            )
        if not self._member_matches_snapshot(snapshot, request.member):
            return DirectorEffectPolicyMemberBindingResultV1(
                status="denied",
                error_code="deo_member_identity_mismatch",
                member=None,
                member_binding_hash=None,
                bound_snapshot=None,
            )
        member_binding_hash = hash_directed_effect_policy_member_binding(
            snapshot.evidence_hash,
            authorization.authorization_hash,
            request.member,
        )
        bound_snapshot = DirectorEffectPolicyBoundSnapshotV1(
            snapshot=snapshot,
            authorization_evidence_hash=authorization.authorization_hash,
            member=request.member,
            member_binding_hash=member_binding_hash,
        )
        return DirectorEffectPolicyMemberBindingResultV1(
            status="allowed",
            error_code=None,
            member=request.member,
            member_binding_hash=member_binding_hash,
            bound_snapshot=bound_snapshot,
        )

    async def revalidate(
        self,
        request: DirectorEffectPolicyRevalidationRequestV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        """Reconstruct and validate immutable evidence without process-local state."""
        snapshot = request.bound_snapshot.snapshot
        evidence = request.authorization_evidence
        if (
            not self._authorization_hash_matches(evidence)
            or evidence.authorization_hash != request.bound_snapshot.authorization_evidence_hash
            or self._snapshot_integrity_error(snapshot) is not None
            or not self._authorization_binds_snapshot(evidence, snapshot)
        ):
            return self._revalidation_result(
                snapshot,
                "deo_authorization_evidence_drift",
                target_observation_performed=False,
            )
        subject = snapshot.subject
        if request.workspace != str(self._workspace) or request.workspace != subject.workspace:
            return self._revalidation_result(
                snapshot,
                "deo_operation_hash_mismatch",
                target_observation_performed=False,
            )
        if not self._member_is_bound(request, snapshot):
            return self._revalidation_result(
                snapshot,
                "deo_member_identity_mismatch",
                target_observation_performed=False,
            )
        try:
            actual_arguments_hash = hash_directed_effect_arguments(request.actual_normalized_arguments)
            request_operation_matches = bool(
                request.actual_normalized_tool_name == subject.normalized_tool_name
                and request.actual_normalized_arguments == subject.normalized_arguments
                and request.actual_arguments_hash == actual_arguments_hash
                and evidence.arguments_hash == actual_arguments_hash
            )
            actual_operation_hash = _operation_hash(
                workspace=request.workspace,
                turn_id=subject.turn_id,
                batch_id=subject.batch_id,
                tool_call_id=subject.tool_call_id,
                inventory_ordinal=subject.inventory_ordinal,
                normalized_tool_name=request.actual_normalized_tool_name,
                normalized_arguments=request.actual_normalized_arguments,
                effect_type=subject.effect_type,
                execution_mode=subject.execution_mode,
            )
        except (AttributeError, TypeError, ValueError):
            request_operation_matches = False
            actual_operation_hash = snapshot.normalized_operation_hash
        if (
            not request_operation_matches
            or actual_operation_hash != subject.prospective_operation_hash
            or actual_operation_hash != snapshot.normalized_operation_hash
        ):
            return self._revalidation_result(
                snapshot,
                "deo_operation_hash_mismatch",
                target_observation_performed=False,
            )
        current_read, read_error = self._read_target_state(
            normalized_tool_name=subject.normalized_tool_name,
            normalized_arguments=subject.normalized_arguments,
            expected_target_path=snapshot_target_path(request),
        )
        if read_error is not None:
            return self._revalidation_denial_without_target(request, read_error)
        assert current_read is not None
        current_target_state = current_read.evidence
        baseline = snapshot.baseline_target_state_evidence
        target_error: DirectedEffectErrorCodeV1 | None = (
            "deo_policy_version_drift"
            if current_target_state.agents_policy_hash != baseline.agents_policy_hash
            else "deo_target_state_drift"
            if current_target_state != baseline
            else None
        )
        if target_error is not None:
            return self._revalidation_denial_from_target(
                request,
                actual_operation_hash,
                current_target_state,
                target_error,
            )
        try:
            restriction = _restriction_values(request.current_job_token_restriction_evidence)
        except ValueError:
            return self._revalidation_denial_from_target(
                request,
                actual_operation_hash,
                current_target_state,
                "deo_job_token_invalid",
            )
        if evidence.job_token_id != restriction.token_id:
            return self._revalidation_denial_from_target(
                request,
                actual_operation_hash,
                current_target_state,
                "deo_job_token_invalid",
            )
        if evidence.allowed_command_hash != restriction.allowed_commands_hash:
            return self._revalidation_denial_from_target(
                request,
                actual_operation_hash,
                current_target_state,
                "deo_command_scope_denied",
            )
        if subject.normalized_tool_name not in _COMMAND_TOOLS and (
            evidence.capability_scope != restriction.allowed_paths
            or evidence.capability_scope_hash != restriction.allowed_paths_hash
        ):
            return self._revalidation_denial_from_target(
                request,
                actual_operation_hash,
                current_target_state,
                "deo_path_scope_denied",
            )
        if evidence.job_token_evidence_hash != restriction.evidence_hash:
            return self._revalidation_denial_from_target(
                request,
                actual_operation_hash,
                current_target_state,
                "deo_job_token_invalid",
            )
        reconstructed = self._reconstruct_request(request, current_target_state)
        current, _ = self._evaluate(reconstructed, observed=current_read)
        if not current.allowed:
            return self._revalidation_result(
                current,
                current.error_code or "deo_authorization_evidence_drift",
                target_observation_performed=True,
            )
        return self._revalidation_result(current, None, target_observation_performed=True)

    @staticmethod
    def _authorization_hash_matches(evidence: DirectorEffectAuthorizationEvidenceV1) -> bool:
        try:
            expected_hash = hash_director_effect_authorization_evidence(
                workspace=evidence.workspace,
                execution_attempt_id=evidence.execution_attempt_id,
                turn_id=evidence.turn_id,
                batch_id=evidence.batch_id,
                tool_call_id=evidence.tool_call_id,
                normalized_tool_name=evidence.normalized_tool_name,
                arguments_hash=evidence.arguments_hash,
                tool_spec_hash=evidence.tool_spec_hash,
                role_policy_id=evidence.role_policy_id,
                role_policy_hash=evidence.role_policy_hash,
                canonical_allow_list_hash=evidence.canonical_allow_list_hash,
                capability_scope=evidence.capability_scope,
                capability_scope_hash=evidence.capability_scope_hash,
                job_token_id=evidence.job_token_id,
                job_token_evidence_hash=evidence.job_token_evidence_hash,
                execution_envelope_hash=evidence.execution_envelope_hash,
                allowed_command_hash=evidence.allowed_command_hash,
                mutation_guard_mode=evidence.mutation_guard_mode,
                bound_policy_snapshot_hash=evidence.bound_policy_snapshot_hash,
                target_state_hash=evidence.target_state_hash,
                normalized_operation_hash=evidence.normalized_operation_hash,
                policy_version=evidence.policy_version,
                policy_hash=evidence.policy_hash,
            )
            canonical = DirectorEffectAuthorizationEvidenceV1(
                workspace=evidence.workspace,
                execution_attempt_id=evidence.execution_attempt_id,
                turn_id=evidence.turn_id,
                batch_id=evidence.batch_id,
                tool_call_id=evidence.tool_call_id,
                normalized_tool_name=evidence.normalized_tool_name,
                arguments_hash=evidence.arguments_hash,
                tool_spec_hash=evidence.tool_spec_hash,
                role_policy_id=evidence.role_policy_id,
                role_policy_hash=evidence.role_policy_hash,
                canonical_allow_list_hash=evidence.canonical_allow_list_hash,
                capability_scope=evidence.capability_scope,
                capability_scope_hash=evidence.capability_scope_hash,
                job_token_id=evidence.job_token_id,
                job_token_evidence_hash=evidence.job_token_evidence_hash,
                execution_envelope_hash=evidence.execution_envelope_hash,
                allowed_command_hash=evidence.allowed_command_hash,
                mutation_guard_mode=evidence.mutation_guard_mode,
                bound_policy_snapshot_hash=evidence.bound_policy_snapshot_hash,
                target_state_hash=evidence.target_state_hash,
                normalized_operation_hash=evidence.normalized_operation_hash,
                policy_version=evidence.policy_version,
                policy_hash=evidence.policy_hash,
                authorization_hash=evidence.authorization_hash,
            )
            return canonical == evidence and evidence.authorization_hash == expected_hash
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _member_matches_snapshot(
        snapshot: DirectorEffectPolicySnapshotResultV1,
        member: DirectedEffectInventoryMemberV1,
    ) -> bool:
        try:
            if DirectedEffectInventoryMemberV1.from_record(member.to_record()) != member:
                return False
        except (AttributeError, TypeError, ValueError):
            return False
        subject = snapshot.subject
        return bool(
            subject.prospective_operation_hash == snapshot.normalized_operation_hash
            and member.ordinal == subject.inventory_ordinal
            and member.tool_call_id == subject.tool_call_id
            and member.normalized_tool_name == subject.normalized_tool_name
            and member.effect_type == subject.effect_type
            and member.execution_mode == subject.execution_mode
            and member.intended_effect_fingerprint == subject.prospective_operation_hash
            and member.policy_verdict_hash == subject.prospective_operation_hash
            and member.expected_receipt_binding_hash == subject.prospective_operation_hash
        )

    @staticmethod
    def _snapshot_integrity_error(
        snapshot: DirectorEffectPolicySnapshotResultV1,
    ) -> DirectedEffectErrorCodeV1 | None:
        try:
            baseline = snapshot.baseline_target_state_evidence
            subject = snapshot.subject
            canonical_subject = DirectorEffectPolicyOperationSubjectV1(
                workspace=subject.workspace,
                turn_id=subject.turn_id,
                batch_id=subject.batch_id,
                tool_call_id=subject.tool_call_id,
                inventory_ordinal=subject.inventory_ordinal,
                normalized_tool_name=subject.normalized_tool_name,
                normalized_arguments=subject.normalized_arguments,
                effect_type=subject.effect_type,
                execution_mode=subject.execution_mode,
                prospective_operation_hash=subject.prospective_operation_hash,
            )
            canonical_baseline = DirectorEffectTargetStateEvidenceV1(
                target_path=baseline.target_path,
                exists=baseline.exists,
                before_content_hash=baseline.before_content_hash,
                minimal_content_evidence=baseline.minimal_content_evidence,
                agents_policy_hash=baseline.agents_policy_hash,
                target_state_hash=baseline.target_state_hash,
                is_no_file_state=baseline.is_no_file_state,
            )
            canonical_snapshot = DirectorEffectPolicySnapshotResultV1(
                status=snapshot.status,
                allowed=snapshot.allowed,
                error_code=snapshot.error_code,
                policy_version=snapshot.policy_version,
                policy_hash=snapshot.policy_hash,
                subject=canonical_subject,
                baseline_target_state_evidence=canonical_baseline,
                target_state_hash=snapshot.target_state_hash,
                normalized_operation_hash=snapshot.normalized_operation_hash,
                evidence_hash=snapshot.evidence_hash,
            )
            subject_operation_hash = _subject_operation_hash(subject)
            target_state_hash = hash_directed_effect_target_state_components(
                target_path=baseline.target_path,
                exists=baseline.exists,
                before_content_hash=baseline.before_content_hash,
                minimal_content_evidence=baseline.minimal_content_evidence,
                agents_policy_hash=baseline.agents_policy_hash,
                is_no_file_state=baseline.is_no_file_state,
            )
            expected_evidence_hash = hash_directed_effect_policy_snapshot_evidence(
                status=snapshot.status,
                allowed=snapshot.allowed,
                error_code=snapshot.error_code,
                policy_version=snapshot.policy_version,
                policy_hash=snapshot.policy_hash,
                subject=subject,
                baseline_target_state_evidence=baseline,
                normalized_operation_hash=snapshot.normalized_operation_hash,
            )
        except (AttributeError, TypeError, ValueError):
            return "deo_authorization_evidence_drift"
        if (
            canonical_snapshot != snapshot
            or canonical_subject != subject
            or canonical_baseline != baseline
            or subject_operation_hash != subject.prospective_operation_hash
            or snapshot.normalized_operation_hash != subject.prospective_operation_hash
            or baseline.target_state_hash != target_state_hash
            or snapshot.target_state_hash != baseline.target_state_hash
            or snapshot.evidence_hash != expected_evidence_hash
        ):
            return "deo_authorization_evidence_drift"
        return None

    def _member_is_bound(
        self,
        request: DirectorEffectPolicyRevalidationRequestV1,
        snapshot: DirectorEffectPolicySnapshotResultV1,
    ) -> bool:
        bound = request.bound_snapshot
        member = bound.member
        grant = request.claim_grant
        try:
            canonical_bound = DirectorEffectPolicyBoundSnapshotV1(
                snapshot=bound.snapshot,
                authorization_evidence_hash=bound.authorization_evidence_hash,
                member=member,
                member_binding_hash=bound.member_binding_hash,
            )
            canonical_grant = DirectedEffectClaimGrantV1(
                schema_version=grant.schema_version,
                execution_attempt=grant.execution_attempt,
                parent_binding=grant.parent_binding,
                operation=grant.operation,
                member=grant.member,
                inventory_hash=grant.inventory_hash,
                operation_version=grant.operation_version,
                claim_event_id=grant.claim_event_id,
                claim_event_seq=grant.claim_event_seq,
                operation_source_head_seq=grant.operation_source_head_seq,
                parent_registry_source_head_seq=grant.parent_registry_source_head_seq,
                grant_hash=grant.grant_hash,
            )
            validate_directed_effect_identity_binding(
                boundary_name="policy adapter revalidation",
                authorization_evidence=request.authorization_evidence,
                claim_grant=grant,
                normalized_tool_name=snapshot.subject.normalized_tool_name,
                arguments_hash=hash_directed_effect_arguments(snapshot.subject.normalized_arguments),
                workspace=snapshot.subject.workspace,
                member=member,
                operation_id=member.operation_id,
            )
            binding_hash_matches = bound.member_binding_hash == hash_directed_effect_policy_member_binding(
                snapshot.evidence_hash,
                bound.authorization_evidence_hash,
                member,
            )
        except (AttributeError, TypeError, ValueError):
            return False
        return bool(
            canonical_bound == bound
            and canonical_grant == grant
            and binding_hash_matches
            and bound.authorization_evidence_hash == request.authorization_evidence.authorization_hash
            and self._member_matches_snapshot(snapshot, member)
            and request.member == member
            and request.operation_id == member.operation_id
            and grant.member == member
            and grant.operation.operation_id == member.operation_id
            and grant.operation.tool_call_id == member.tool_call_id
            and grant.operation.effect_id == member.effect_id
        )

    @staticmethod
    def _authorization_binds_snapshot(
        evidence: DirectorEffectAuthorizationEvidenceV1,
        snapshot: DirectorEffectPolicySnapshotResultV1,
    ) -> bool:
        subject = snapshot.subject
        return bool(
            evidence.bound_policy_snapshot_hash == snapshot.evidence_hash
            and evidence.policy_hash == snapshot.policy_hash
            and evidence.target_state_hash == snapshot.target_state_hash
            and evidence.normalized_operation_hash == snapshot.normalized_operation_hash
            and evidence.policy_version == snapshot.policy_version
            and evidence.workspace == subject.workspace
            and evidence.turn_id == subject.turn_id
            and evidence.batch_id == subject.batch_id
            and evidence.tool_call_id == subject.tool_call_id
            and evidence.normalized_tool_name == subject.normalized_tool_name
            and evidence.arguments_hash == hash_directed_effect_arguments(subject.normalized_arguments)
        )

    def _reconstruct_request(
        self,
        request: DirectorEffectPolicyRevalidationRequestV1,
        target_state: DirectorEffectTargetStateEvidenceV1,
    ) -> DirectorEffectPolicySnapshotRequestV1:
        evidence = request.authorization_evidence
        subject = request.bound_snapshot.snapshot.subject
        current_restriction = _restriction_values(request.current_job_token_restriction_evidence)
        command = _items_to_dict(subject.normalized_arguments).get("command", "")
        return DirectorEffectPolicySnapshotRequestV1(
            subject=subject,
            workspace=subject.workspace,
            normalized_tool_name=subject.normalized_tool_name,
            normalized_arguments=subject.normalized_arguments,
            job_token_restriction_evidence=request.current_job_token_restriction_evidence,
            expected_policy_version=evidence.policy_version,
            canonical_command=command if isinstance(command, str) else "",
            path_scope_evidence=(("allowed_paths", evidence.capability_scope),),
            command_scope_evidence=(("allowed_commands", current_restriction.allowed_commands),),
            target_state_evidence=target_state,
        )

    def _evaluate(
        self,
        request: DirectorEffectPolicySnapshotRequestV1,
        *,
        observed: _TargetRead | None = None,
    ) -> tuple[DirectorEffectPolicySnapshotResultV1, _TargetRead | None]:
        fallback = request.target_state_evidence
        if request.workspace != str(self._workspace) or request.subject.workspace != str(self._workspace):
            return self._snapshot_denial(request, "deo_operation_hash_mismatch", fallback), observed
        if request.subject.prospective_operation_hash != _subject_operation_hash(request.subject):
            return self._snapshot_denial(request, "deo_operation_hash_mismatch", fallback), observed
        if observed is None:
            observed, target_error = self._read_target_state(
                normalized_tool_name=request.normalized_tool_name,
                normalized_arguments=request.normalized_arguments,
                expected_target_path=request.target_state_evidence.target_path,
            )
            if target_error is not None:
                return self._snapshot_denial(request, target_error, fallback), observed
        assert observed is not None
        target_state = observed.evidence
        if request.normalized_tool_name in _COMMAND_TOOLS:
            command_result, _ = self._evaluate_command(request, target_state)
            return command_result, observed
        if request.normalized_tool_name not in _WRITE_TOOLS:
            return self._snapshot_denial(request, "deo_director_policy_denied", target_state), observed
        if target_state != request.target_state_evidence:
            code: DirectedEffectErrorCodeV1 = (
                "deo_policy_version_drift"
                if target_state.agents_policy_hash != request.target_state_evidence.agents_policy_hash
                else "deo_target_state_drift"
            )
            return self._snapshot_denial(request, code, target_state), observed
        try:
            restriction = _restriction_values(request.job_token_restriction_evidence)
            policy_paths = _scope_tokens(request.path_scope_evidence, field_name="allowed_paths")
        except ValueError:
            return self._snapshot_denial(request, "deo_job_token_invalid", target_state), observed
        if (
            policy_paths != restriction.allowed_paths
            or not self._path_is_allowed(target_state.target_path, restriction.allowed_paths)
            or not self._path_is_allowed(target_state.target_path, policy_paths)
        ):
            return self._snapshot_denial(request, "deo_path_scope_denied", target_state), observed
        policy_result = self._validate_write_policy(request, observed, policy_paths)
        if not bool(policy_result.get("ok")):
            return self._snapshot_denial(request, "deo_director_policy_denied", target_state), observed
        return self._snapshot_allowed(request, target_state, policy_result), observed

    def _evaluate_command(
        self,
        request: DirectorEffectPolicySnapshotRequestV1,
        target_state: DirectorEffectTargetStateEvidenceV1,
    ) -> tuple[DirectorEffectPolicySnapshotResultV1, DirectorEffectTargetStateEvidenceV1]:
        if not request.target_state_evidence.is_no_file_state:
            return self._snapshot_denial(request, "deo_target_state_drift", target_state), target_state
        if target_state != request.target_state_evidence:
            code: DirectedEffectErrorCodeV1 = (
                "deo_policy_version_drift"
                if target_state.agents_policy_hash != request.target_state_evidence.agents_policy_hash
                else "deo_target_state_drift"
            )
            return self._snapshot_denial(request, code, target_state), target_state
        try:
            restriction = _restriction_values(request.job_token_restriction_evidence)
            scoped_commands = _scope_tokens(request.command_scope_evidence, field_name="allowed_commands")
            actual_command = _items_to_dict(request.normalized_arguments).get("command")
            if restriction.allowed_commands != scoped_commands or actual_command != request.canonical_command:
                return self._snapshot_denial(request, "deo_command_scope_denied", target_state), target_state
            if not isinstance(actual_command, str):
                return self._snapshot_denial(request, "deo_operation_hash_mismatch", target_state), target_state
            result = validate_command_capability(
                CommandCapabilityValidationInputV1(
                    capability_token_id=restriction.token_id,
                    capability_token_hash=restriction.token_hash,
                    allowed_commands=restriction.allowed_commands,
                    canonical_command=request.canonical_command,
                )
            )
        except ValueError:
            return self._snapshot_denial(request, "deo_job_token_invalid", target_state), target_state
        if not result.allowed:
            return self._snapshot_denial(request, "deo_command_scope_denied", target_state), target_state
        policy_evidence = {"command_capability": {"allowed": result.allowed, "evidence_hash": result.evidence_hash}}
        return self._snapshot_allowed(request, target_state, policy_evidence), target_state

    def _read_target_state(
        self,
        *,
        normalized_tool_name: str,
        normalized_arguments: DirectedEffectImmutableItemsV1,
        expected_target_path: str,
    ) -> tuple[_TargetRead | None, DirectedEffectErrorCodeV1 | None]:
        if normalized_tool_name in _COMMAND_TOOLS:
            try:
                agents_hash = self._agents_policy_hash("")
            except ValueError:
                return None, "deo_target_state_drift"
            evidence = DirectorEffectTargetStateEvidenceV1(
                target_path="",
                exists=False,
                before_content_hash=_NO_FILE_HASH,
                minimal_content_evidence=(),
                agents_policy_hash=agents_hash,
                target_state_hash=_target_state_hash("", False, _NO_FILE_HASH, (), agents_hash),
                is_no_file_state=True,
            )
            return _TargetRead(evidence=evidence, old_content=""), None
        values = _items_to_dict(normalized_arguments)
        raw_path = next((values.get(key) for key in ("path", "file", "filepath") if values.get(key) is not None), None)
        if not isinstance(raw_path, str) or not raw_path.strip() or "\n" in raw_path or "\r" in raw_path:
            return None, "deo_path_scope_denied"
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return None, "deo_path_scope_denied"
        try:
            target = (self._workspace / candidate).resolve(strict=False)
            target.relative_to(self._workspace)
        except (OSError, RuntimeError, ValueError):
            return None, "deo_path_scope_denied"
        if target == self._workspace or target.is_dir():
            return None, "deo_path_scope_denied"
        target_path = target.relative_to(self._workspace).as_posix()
        if target_path != expected_target_path:
            return None, "deo_path_scope_denied"
        try:
            try:
                before_stat = target.stat()
            except FileNotFoundError:
                before_stat = None
            if before_stat is None:
                raw_content = b""
                old_content = ""
            else:
                raw_content = target.read_bytes()
                old_content = raw_content.decode("utf-8")
            agents_hash = self._agents_policy_hash(target_path)
            try:
                after_stat = target.stat()
            except FileNotFoundError:
                after_stat = None
        except (OSError, UnicodeError, ValueError):
            return None, "deo_target_state_drift"
        if _stat_identity(before_stat) != _stat_identity(after_stat):
            return None, "deo_target_state_drift"
        exists = before_stat is not None
        before_hash = _content_hash(old_content) if exists else _NO_FILE_HASH
        minimal = (
            ("byte_length", len(raw_content)),
            ("prefix_hash", _content_hash(old_content[:256])),
            ("stat_dev", before_stat.st_dev if before_stat is not None else 0),
            ("stat_ino", before_stat.st_ino if before_stat is not None else 0),
            ("stat_mtime_ns", before_stat.st_mtime_ns if before_stat is not None else 0),
            ("stat_size", before_stat.st_size if before_stat is not None else 0),
        )
        evidence = DirectorEffectTargetStateEvidenceV1(
            target_path=target_path,
            exists=exists,
            before_content_hash=before_hash,
            minimal_content_evidence=minimal,
            agents_policy_hash=agents_hash,
            target_state_hash=_target_state_hash(target_path, exists, before_hash, minimal, agents_hash),
            is_no_file_state=False,
        )
        return _TargetRead(evidence=evidence, old_content=old_content), None

    def _agents_policy_hash(self, target_path: str) -> str:
        """Return fail-closed, stable policy evidence for every applicable AGENTS file."""
        records: list[dict[str, object]] = []
        for index in range(len(Path(target_path).parts[:-1]) + 1):
            candidate = self._workspace.joinpath(*Path(target_path).parts[:index], "AGENTS.md")
            try:
                before_lstat = candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ValueError("unable to observe AGENTS policy candidate") from exc
            try:
                before_stat = candidate.stat()
                before_resolved = candidate.resolve(strict=True)
                before_relative = before_resolved.relative_to(self._workspace).as_posix()
                if not stat.S_ISREG(before_stat.st_mode):
                    raise ValueError("AGENTS policy candidate must resolve to a regular file")
                before_link_target = os.readlink(candidate) if stat.S_ISLNK(before_lstat.st_mode) else None
                content = candidate.read_bytes()
                after_lstat = candidate.lstat()
                after_stat = candidate.stat()
                after_resolved = candidate.resolve(strict=True)
                after_relative = after_resolved.relative_to(self._workspace).as_posix()
                if not stat.S_ISREG(after_stat.st_mode):
                    raise ValueError("AGENTS policy candidate must resolve to a regular file")
                after_link_target = os.readlink(candidate) if stat.S_ISLNK(after_lstat.st_mode) else None
            except (OSError, RuntimeError, ValueError) as exc:
                raise ValueError("unable to stably observe AGENTS policy candidate") from exc
            if (
                _stat_identity(before_lstat) != _stat_identity(after_lstat)
                or _stat_identity(before_stat) != _stat_identity(after_stat)
                or before_relative != after_relative
                or before_link_target != after_link_target
            ):
                raise ValueError("AGENTS policy candidate changed during observation")
            records.append(
                {
                    "content_hash": hashlib.sha256(content).hexdigest(),
                    "lstat": _stat_identity(before_lstat),
                    "path": candidate.relative_to(self._workspace).as_posix(),
                    "resolved_path": before_relative,
                    "stat": _stat_identity(before_stat),
                    "symlink_target": before_link_target,
                }
            )
        return _hash_payload({"candidates": records, "domain": "director_effect_agents_policy_evidence_v1"})

    def _validate_write_policy(
        self,
        request: DirectorEffectPolicySnapshotRequestV1,
        target_read: _TargetRead,
        policy_paths: tuple[str, ...],
    ) -> dict[str, Any]:
        values = {key: _thaw(value) for key, value in request.normalized_arguments}
        values["allowed_scope"] = list(policy_paths)
        proposed = values.get("content", "")
        new_content = proposed if isinstance(proposed, str) else ""
        operation = request.normalized_tool_name
        if operation == "write_file":
            operation = "write_file:modify" if target_read.evidence.exists else "write_file:create"
        return DirectorToolExecutor._validate_director_policy_for_write(
            cast(DirectorToolExecutor, self),
            workspace=self._workspace,
            rel_path=target_read.evidence.target_path,
            old_content=target_read.old_content,
            new_content="" if request.normalized_tool_name == "delete_file" else new_content,
            operation=operation,
            tool_kwargs=values,
        )

    @staticmethod
    def _path_is_allowed(target_path: str, scopes: tuple[str, ...]) -> bool:
        return bool(scopes) and any(
            target_path == scope.strip("/") or target_path.startswith(f"{scope.strip('/')}/")
            for scope in scopes
            if scope.strip("/")
        )

    def _snapshot_allowed(
        self,
        request: DirectorEffectPolicySnapshotRequestV1,
        target_state: DirectorEffectTargetStateEvidenceV1,
        policy_evidence: dict[str, Any],
    ) -> DirectorEffectPolicySnapshotResultV1:
        policy_hash = _hash_payload(
            {
                "policy_evidence": policy_evidence,
                "policy_version": request.expected_policy_version,
                "target_state_hash": target_state.target_state_hash,
            }
        )
        evidence_hash = hash_directed_effect_policy_snapshot_evidence(
            status="allowed",
            allowed=True,
            error_code=None,
            policy_version=request.expected_policy_version,
            policy_hash=policy_hash,
            subject=request.subject,
            baseline_target_state_evidence=target_state,
            normalized_operation_hash=request.subject.prospective_operation_hash,
        )
        return DirectorEffectPolicySnapshotResultV1(
            status="allowed",
            allowed=True,
            error_code=None,
            policy_version=request.expected_policy_version,
            policy_hash=policy_hash,
            subject=request.subject,
            baseline_target_state_evidence=target_state,
            target_state_hash=target_state.target_state_hash,
            normalized_operation_hash=request.subject.prospective_operation_hash,
            evidence_hash=evidence_hash,
        )

    def _snapshot_denial(
        self,
        request: DirectorEffectPolicySnapshotRequestV1,
        error_code: DirectedEffectErrorCodeV1,
        target_state: DirectorEffectTargetStateEvidenceV1,
    ) -> DirectorEffectPolicySnapshotResultV1:
        operation_hash = _subject_operation_hash(request.subject)
        policy_hash = _hash_payload({"error_code": error_code, "policy_version": request.expected_policy_version})
        evidence_hash = hash_directed_effect_policy_snapshot_evidence(
            status="denied",
            allowed=False,
            error_code=error_code,
            policy_version=request.expected_policy_version,
            policy_hash=policy_hash,
            subject=request.subject,
            baseline_target_state_evidence=target_state,
            normalized_operation_hash=operation_hash,
        )
        return DirectorEffectPolicySnapshotResultV1(
            status="denied",
            allowed=False,
            error_code=error_code,
            policy_version=request.expected_policy_version,
            policy_hash=policy_hash,
            subject=request.subject,
            baseline_target_state_evidence=target_state,
            target_state_hash=target_state.target_state_hash,
            normalized_operation_hash=operation_hash,
            evidence_hash=evidence_hash,
        )

    @staticmethod
    def _revalidation_result(
        current: DirectorEffectPolicySnapshotResultV1,
        error_code: DirectedEffectErrorCodeV1 | None,
        *,
        target_observation_performed: bool,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        status: DirectorEffectPolicySnapshotStatusV1 = "allowed" if error_code is None else "denied"
        allowed = error_code is None
        current_evidence_hash = hash_directed_effect_policy_revalidation_evidence(
            status=status,
            allowed=allowed,
            error_code=error_code,
            current_policy_version=current.policy_version,
            current_policy_hash=current.policy_hash,
            current_target_state_evidence=current.baseline_target_state_evidence,
            current_normalized_operation_hash=current.normalized_operation_hash,
            target_observation_performed=target_observation_performed,
        )
        return DirectorEffectPolicyRevalidationResultV1(
            status=status,
            allowed=allowed,
            error_code=error_code,
            current_policy_version=current.policy_version,
            current_policy_hash=current.policy_hash,
            current_target_state_evidence=current.baseline_target_state_evidence,
            current_target_state_hash=current.target_state_hash,
            current_normalized_operation_hash=current.normalized_operation_hash,
            target_observation_performed=target_observation_performed,
            current_evidence_hash=current_evidence_hash,
        )

    def _revalidation_denial_from_target(
        self,
        request: DirectorEffectPolicyRevalidationRequestV1,
        actual_operation_hash: str,
        current_target_state: DirectorEffectTargetStateEvidenceV1,
        error_code: DirectedEffectErrorCodeV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        policy_version = request.authorization_evidence.policy_version
        policy_hash = _hash_payload(
            {
                "current_target_state_hash": current_target_state.target_state_hash,
                "error_code": error_code,
                "policy_version": policy_version,
            }
        )
        current_evidence_hash = hash_directed_effect_policy_revalidation_evidence(
            status="denied",
            allowed=False,
            error_code=error_code,
            current_policy_version=policy_version,
            current_policy_hash=policy_hash,
            current_target_state_evidence=current_target_state,
            current_normalized_operation_hash=actual_operation_hash,
            target_observation_performed=True,
        )
        return DirectorEffectPolicyRevalidationResultV1(
            status="denied",
            allowed=False,
            error_code=error_code,
            current_policy_version=policy_version,
            current_policy_hash=policy_hash,
            current_target_state_evidence=current_target_state,
            current_target_state_hash=current_target_state.target_state_hash,
            current_normalized_operation_hash=actual_operation_hash,
            target_observation_performed=True,
            current_evidence_hash=current_evidence_hash,
        )

    def _revalidation_denial_without_target(
        self,
        request: DirectorEffectPolicyRevalidationRequestV1,
        error_code: DirectedEffectErrorCodeV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        """Project the trusted baseline when fresh target capture did not complete."""
        snapshot = request.bound_snapshot.snapshot
        return self._revalidation_result(
            snapshot,
            error_code,
            target_observation_performed=False,
        )


def _stat_identity(stat: object | None) -> tuple[int, int, int, int] | None:
    if stat is None:
        return None
    return (
        cast(Any, stat).st_dev,
        cast(Any, stat).st_ino,
        cast(Any, stat).st_size,
        cast(Any, stat).st_mtime_ns,
    )


def _target_state_hash(
    target_path: str,
    exists: bool,
    before_content_hash: str,
    minimal_content_evidence: DirectedEffectImmutableItemsV1,
    agents_policy_hash: str,
) -> str:
    return hash_directed_effect_target_state_components(
        target_path=target_path,
        exists=exists,
        before_content_hash=before_content_hash,
        minimal_content_evidence=minimal_content_evidence,
        agents_policy_hash=agents_policy_hash,
        is_no_file_state=not target_path,
    )


def snapshot_target_path(request: DirectorEffectPolicyRevalidationRequestV1) -> str:
    """Return the retained pre-seal target instead of trusting current arguments."""
    return request.bound_snapshot.snapshot.baseline_target_state_evidence.target_path
