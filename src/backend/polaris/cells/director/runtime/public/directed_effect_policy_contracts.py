"""Public dependency-inversion contracts for DEO-2B policy snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, cast, runtime_checkable

from polaris.cells.runtime.task_runtime.public import (
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryEffectTypeV1,
    DirectedEffectInventoryExecutionModeV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectInventoryMemberV1,
)

from .directed_effect_contracts import (
    DirectedEffectErrorCodeV1,
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableMapV1,
    DirectorEffectAuthorizationBindingV1,
    DirectorEffectAuthorizationEvidenceV1,
    DirectorEffectPublicPolicyEvidenceV1,
    hash_directed_effect_arguments,
    require_directed_effect_bool,
    require_directed_effect_hash as _require_hash,
    require_directed_effect_immutable_items,
    validate_directed_effect_error_code,
    validate_directed_effect_identity_binding,
    validate_director_effect_authorization_binding,
    validate_director_effect_public_policy_evidence,
)

DirectorEffectPolicySnapshotStatusV1 = Literal["allowed", "denied"]
DirectorEffectCurrentPolicyEvidenceCaptureErrorCodeV1 = Literal["deo_current_policy_evidence_unavailable"]


def _require_token(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _require_string(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value.strip()


def _require_error_code(value: DirectedEffectErrorCodeV1 | None) -> DirectedEffectErrorCodeV1 | None:
    return validate_directed_effect_error_code(value)


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicyOperationSubjectV1:
    """Prospective pre-seal operation identity and normalized descriptor."""

    workspace: str
    turn_id: str
    batch_id: str
    tool_call_id: str
    inventory_ordinal: int
    normalized_tool_name: str
    normalized_arguments: DirectedEffectImmutableItemsV1
    effect_type: DirectedEffectInventoryEffectTypeV1
    execution_mode: DirectedEffectInventoryExecutionModeV1
    prospective_operation_hash: str

    def __post_init__(self) -> None:
        for field_name in (
            "workspace",
            "turn_id",
            "batch_id",
            "tool_call_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
        ):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        if (
            isinstance(self.inventory_ordinal, bool)
            or not isinstance(self.inventory_ordinal, int)
            or self.inventory_ordinal < 0
        ):
            raise ValueError("inventory_ordinal must be a non-negative integer")
        object.__setattr__(
            self,
            "normalized_arguments",
            require_directed_effect_immutable_items("normalized_arguments", self.normalized_arguments),
        )
        object.__setattr__(
            self,
            "prospective_operation_hash",
            _require_hash("prospective_operation_hash", self.prospective_operation_hash),
        )
        DirectedEffectInventoryIntentV1(
            ordinal=self.inventory_ordinal,
            tool_call_id=self.tool_call_id,
            normalized_tool_name=self.normalized_tool_name,
            effect_type=self.effect_type,
            execution_mode=self.execution_mode,
            intended_effect_fingerprint=self.prospective_operation_hash,
            policy_verdict_hash=self.prospective_operation_hash,
            expected_receipt_binding_hash=self.prospective_operation_hash,
        )


def _operation_subject_items(
    subject: DirectorEffectPolicyOperationSubjectV1,
) -> DirectedEffectImmutableItemsV1:
    return (
        ("batch_id", subject.batch_id),
        ("effect_type", subject.effect_type),
        ("execution_mode", subject.execution_mode),
        ("inventory_ordinal", subject.inventory_ordinal),
        (
            "normalized_arguments",
            DirectedEffectImmutableMapV1(items=subject.normalized_arguments),
        ),
        ("normalized_tool_name", subject.normalized_tool_name),
        ("prospective_operation_hash", subject.prospective_operation_hash),
        ("tool_call_id", subject.tool_call_id),
        ("turn_id", subject.turn_id),
        ("workspace", subject.workspace),
    )


def hash_director_effect_policy_operation_subject(
    subject: DirectorEffectPolicyOperationSubjectV1,
) -> str:
    """Hash one prospective operation without trusting its supplied digest."""

    if type(subject) is not DirectorEffectPolicyOperationSubjectV1:
        raise TypeError("subject must be exactly DirectorEffectPolicyOperationSubjectV1")
    return hash_directed_effect_arguments(
        (
            ("batch_id", subject.batch_id),
            ("effect_type", subject.effect_type),
            ("execution_mode", subject.execution_mode),
            ("inventory_ordinal", subject.inventory_ordinal),
            (
                "normalized_arguments",
                DirectedEffectImmutableMapV1(items=subject.normalized_arguments),
            ),
            ("normalized_tool_name", subject.normalized_tool_name),
            ("tool_call_id", subject.tool_call_id),
            ("turn_id", subject.turn_id),
            ("workspace", subject.workspace),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectorEffectTargetStateEvidenceV1:
    """Immutable target-state evidence, including an explicit command-only form."""

    target_path: str
    exists: bool
    before_content_hash: str
    minimal_content_evidence: DirectedEffectImmutableItemsV1
    agents_policy_hash: str
    target_state_hash: str
    is_no_file_state: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "exists", require_directed_effect_bool("exists", self.exists))
        object.__setattr__(
            self,
            "is_no_file_state",
            require_directed_effect_bool("is_no_file_state", self.is_no_file_state),
        )
        object.__setattr__(self, "target_path", _require_string("target_path", self.target_path))
        object.__setattr__(self, "before_content_hash", _require_hash("before_content_hash", self.before_content_hash))
        object.__setattr__(
            self,
            "minimal_content_evidence",
            require_directed_effect_immutable_items("minimal_content_evidence", self.minimal_content_evidence),
        )
        object.__setattr__(self, "agents_policy_hash", _require_hash("agents_policy_hash", self.agents_policy_hash))
        object.__setattr__(self, "target_state_hash", _require_hash("target_state_hash", self.target_state_hash))
        if self.is_no_file_state:
            if self.target_path or self.exists or self.before_content_hash != "0" * 64:
                raise ValueError("no-file state must not claim a file target or content")
        elif not self.target_path:
            raise ValueError("file target state requires a canonical target_path")
        if self.target_state_hash != hash_directed_effect_target_state_evidence(self):
            raise ValueError("target_state_hash must bind canonical target-state evidence")


def _target_state_evidence_items(
    evidence: DirectorEffectTargetStateEvidenceV1,
) -> DirectedEffectImmutableItemsV1:
    return (
        ("agents_policy_hash", evidence.agents_policy_hash),
        ("before_content_hash", evidence.before_content_hash),
        ("exists", evidence.exists),
        ("is_no_file_state", evidence.is_no_file_state),
        (
            "minimal_content_evidence",
            DirectedEffectImmutableMapV1(items=evidence.minimal_content_evidence),
        ),
        ("target_path", evidence.target_path),
    )


def hash_directed_effect_target_state_evidence(evidence: DirectorEffectTargetStateEvidenceV1) -> str:
    """Domain-separate the aggregate target-state hash from its immutable components."""
    if not isinstance(evidence, DirectorEffectTargetStateEvidenceV1):
        raise TypeError("evidence must be DirectorEffectTargetStateEvidenceV1")
    return hash_directed_effect_target_state_components(
        target_path=evidence.target_path,
        exists=evidence.exists,
        before_content_hash=evidence.before_content_hash,
        minimal_content_evidence=evidence.minimal_content_evidence,
        agents_policy_hash=evidence.agents_policy_hash,
        is_no_file_state=evidence.is_no_file_state,
    )


def hash_directed_effect_target_state_components(
    *,
    target_path: str,
    exists: bool,
    before_content_hash: str,
    minimal_content_evidence: DirectedEffectImmutableItemsV1,
    agents_policy_hash: str,
    is_no_file_state: bool,
) -> str:
    """Return the canonical aggregate for already-validated target-state components."""
    return hash_directed_effect_arguments(
        (
            ("domain", "director_effect_target_state_evidence_v1"),
            (
                "target_state_evidence",
                DirectedEffectImmutableMapV1(
                    items=(
                        ("agents_policy_hash", agents_policy_hash),
                        ("before_content_hash", before_content_hash),
                        ("exists", exists),
                        ("is_no_file_state", is_no_file_state),
                        ("minimal_content_evidence", DirectedEffectImmutableMapV1(items=minimal_content_evidence)),
                        ("target_path", target_path),
                    )
                ),
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicySnapshotRequestV1:
    """No-effect request for one adapter-owned policy snapshot."""

    subject: DirectorEffectPolicyOperationSubjectV1
    workspace: str
    normalized_tool_name: str
    normalized_arguments: DirectedEffectImmutableItemsV1
    job_token_restriction_evidence: DirectedEffectImmutableItemsV1
    expected_policy_version: str
    canonical_command: str
    path_scope_evidence: DirectedEffectImmutableItemsV1
    command_scope_evidence: DirectedEffectImmutableItemsV1
    target_state_evidence: DirectorEffectTargetStateEvidenceV1

    def __post_init__(self) -> None:
        if not isinstance(self.subject, DirectorEffectPolicyOperationSubjectV1):
            raise TypeError("subject must be DirectorEffectPolicyOperationSubjectV1")
        object.__setattr__(self, "workspace", _require_token("workspace", self.workspace))
        object.__setattr__(
            self, "normalized_tool_name", _require_token("normalized_tool_name", self.normalized_tool_name)
        )
        object.__setattr__(
            self, "expected_policy_version", _require_token("expected_policy_version", self.expected_policy_version)
        )
        object.__setattr__(
            self,
            "canonical_command",
            _require_string("canonical_command", self.canonical_command),
        )
        for field_name in (
            "normalized_arguments",
            "job_token_restriction_evidence",
            "path_scope_evidence",
            "command_scope_evidence",
        ):
            object.__setattr__(
                self, field_name, require_directed_effect_immutable_items(field_name, getattr(self, field_name))
            )
        if not isinstance(self.target_state_evidence, DirectorEffectTargetStateEvidenceV1):
            raise TypeError("target_state_evidence must be DirectorEffectTargetStateEvidenceV1")
        if (
            self.workspace != self.subject.workspace
            or self.normalized_tool_name != self.subject.normalized_tool_name
            or self.normalized_arguments != self.subject.normalized_arguments
        ):
            raise ValueError("snapshot request must match its prospective operation subject")


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicyBaselineCaptureRequestV1:
    """Adapter-owned target capture request without caller-supplied file state."""

    subject: DirectorEffectPolicyOperationSubjectV1
    workspace: str
    normalized_tool_name: str
    normalized_arguments: DirectedEffectImmutableItemsV1
    job_token_restriction_evidence: DirectedEffectImmutableItemsV1
    expected_policy_version: str
    canonical_command: str
    path_scope_evidence: DirectedEffectImmutableItemsV1
    command_scope_evidence: DirectedEffectImmutableItemsV1

    def __post_init__(self) -> None:
        if type(self.subject) is not DirectorEffectPolicyOperationSubjectV1:
            raise TypeError("subject must be exactly DirectorEffectPolicyOperationSubjectV1")
        object.__setattr__(self, "workspace", _require_token("workspace", self.workspace))
        object.__setattr__(
            self,
            "normalized_tool_name",
            _require_token("normalized_tool_name", self.normalized_tool_name),
        )
        object.__setattr__(
            self,
            "expected_policy_version",
            _require_token("expected_policy_version", self.expected_policy_version),
        )
        object.__setattr__(
            self,
            "canonical_command",
            _require_string("canonical_command", self.canonical_command),
        )
        for field_name in (
            "normalized_arguments",
            "job_token_restriction_evidence",
            "path_scope_evidence",
            "command_scope_evidence",
        ):
            object.__setattr__(
                self,
                field_name,
                require_directed_effect_immutable_items(
                    field_name,
                    getattr(self, field_name),
                ),
            )
        if (
            self.workspace != self.subject.workspace
            or self.normalized_tool_name != self.subject.normalized_tool_name
            or self.normalized_arguments != self.subject.normalized_arguments
            or self.subject.prospective_operation_hash != hash_director_effect_policy_operation_subject(self.subject)
        ):
            raise ValueError("baseline capture request identity mismatch")


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicySnapshotResultV1:
    """Frozen no-effect policy verdict with no executable capability."""

    status: DirectorEffectPolicySnapshotStatusV1
    allowed: bool
    error_code: DirectedEffectErrorCodeV1 | None
    policy_version: str
    policy_hash: str
    subject: DirectorEffectPolicyOperationSubjectV1
    baseline_target_state_evidence: DirectorEffectTargetStateEvidenceV1
    target_state_hash: str
    normalized_operation_hash: str
    evidence_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed", require_directed_effect_bool("allowed", self.allowed))
        if self.status not in {"allowed", "denied"}:
            raise ValueError("status must be allowed or denied")
        if self.allowed != (self.status == "allowed"):
            raise ValueError("allowed must agree with status")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.allowed and self.error_code is not None:
            raise ValueError("allowed result cannot carry an error_code")
        if not self.allowed and self.error_code is None:
            raise ValueError("denied result requires an error_code")
        object.__setattr__(self, "policy_version", _require_token("policy_version", self.policy_version))
        if not isinstance(self.subject, DirectorEffectPolicyOperationSubjectV1):
            raise TypeError("subject must be DirectorEffectPolicyOperationSubjectV1")
        canonical_subject = DirectorEffectPolicyOperationSubjectV1(
            workspace=self.subject.workspace,
            turn_id=self.subject.turn_id,
            batch_id=self.subject.batch_id,
            tool_call_id=self.subject.tool_call_id,
            inventory_ordinal=self.subject.inventory_ordinal,
            normalized_tool_name=self.subject.normalized_tool_name,
            normalized_arguments=self.subject.normalized_arguments,
            effect_type=self.subject.effect_type,
            execution_mode=self.subject.execution_mode,
            prospective_operation_hash=self.subject.prospective_operation_hash,
        )
        if canonical_subject != self.subject:
            raise ValueError("subject must retain canonical pre-seal operation evidence")
        if not isinstance(self.baseline_target_state_evidence, DirectorEffectTargetStateEvidenceV1):
            raise TypeError("baseline_target_state_evidence must be DirectorEffectTargetStateEvidenceV1")
        for field_name in ("policy_hash", "target_state_hash", "normalized_operation_hash", "evidence_hash"):
            object.__setattr__(self, field_name, _require_hash(field_name, getattr(self, field_name)))
        if self.target_state_hash != self.baseline_target_state_evidence.target_state_hash:
            raise ValueError("target_state_hash must match baseline target-state evidence")
        if self.allowed and self.normalized_operation_hash != self.subject.prospective_operation_hash:
            raise ValueError("normalized_operation_hash must match retained subject evidence")
        if self.evidence_hash != hash_directed_effect_policy_snapshot_evidence(
            status=self.status,
            allowed=self.allowed,
            error_code=self.error_code,
            policy_version=self.policy_version,
            policy_hash=self.policy_hash,
            subject=self.subject,
            baseline_target_state_evidence=self.baseline_target_state_evidence,
            normalized_operation_hash=self.normalized_operation_hash,
        ):
            raise ValueError("evidence_hash must bind complete baseline policy evidence")


def validate_director_effect_policy_snapshot_result(
    snapshot: DirectorEffectPolicySnapshotResultV1,
) -> DirectorEffectPolicySnapshotResultV1:
    """Reconstruct a policy snapshot and every nested hash-bearing value."""

    if type(snapshot) is not DirectorEffectPolicySnapshotResultV1:
        raise TypeError("snapshot must be exactly DirectorEffectPolicySnapshotResultV1")
    target = snapshot.baseline_target_state_evidence
    if type(target) is not DirectorEffectTargetStateEvidenceV1:
        raise TypeError("baseline_target_state_evidence must be exactly DirectorEffectTargetStateEvidenceV1")
    canonical_target = DirectorEffectTargetStateEvidenceV1(
        target_path=target.target_path,
        exists=target.exists,
        before_content_hash=target.before_content_hash,
        minimal_content_evidence=target.minimal_content_evidence,
        agents_policy_hash=target.agents_policy_hash,
        target_state_hash=target.target_state_hash,
        is_no_file_state=target.is_no_file_state,
    )
    subject = snapshot.subject
    if type(subject) is not DirectorEffectPolicyOperationSubjectV1:
        raise TypeError("subject must be exactly DirectorEffectPolicyOperationSubjectV1")
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
    canonical = DirectorEffectPolicySnapshotResultV1(
        status=snapshot.status,
        allowed=snapshot.allowed,
        error_code=snapshot.error_code,
        policy_version=snapshot.policy_version,
        policy_hash=snapshot.policy_hash,
        subject=canonical_subject,
        baseline_target_state_evidence=canonical_target,
        target_state_hash=snapshot.target_state_hash,
        normalized_operation_hash=snapshot.normalized_operation_hash,
        evidence_hash=snapshot.evidence_hash,
    )
    if canonical != snapshot:
        raise ValueError("policy snapshot canonical reconstruction mismatch")
    return canonical


def hash_directed_effect_policy_snapshot_evidence(
    *,
    status: DirectorEffectPolicySnapshotStatusV1,
    allowed: bool,
    error_code: DirectedEffectErrorCodeV1 | None,
    policy_version: str,
    policy_hash: str,
    subject: DirectorEffectPolicyOperationSubjectV1,
    baseline_target_state_evidence: DirectorEffectTargetStateEvidenceV1,
    normalized_operation_hash: str,
) -> str:
    """Bind the complete frozen policy baseline in a distinct snapshot domain."""
    if not isinstance(subject, DirectorEffectPolicyOperationSubjectV1):
        raise TypeError("subject must be DirectorEffectPolicyOperationSubjectV1")
    if not isinstance(baseline_target_state_evidence, DirectorEffectTargetStateEvidenceV1):
        raise TypeError("baseline_target_state_evidence must be DirectorEffectTargetStateEvidenceV1")
    return hash_directed_effect_arguments(
        (
            ("allowed", allowed),
            (
                "baseline_target_state_evidence",
                DirectedEffectImmutableMapV1(items=_target_state_evidence_items(baseline_target_state_evidence)),
            ),
            ("domain", "director_effect_policy_snapshot_evidence_v1"),
            ("error_code", error_code),
            ("normalized_operation_hash", normalized_operation_hash),
            ("policy_hash", policy_hash),
            ("policy_version", policy_version),
            ("status", status),
            (
                "subject",
                DirectedEffectImmutableMapV1(items=_operation_subject_items(subject)),
            ),
        )
    )


def hash_directed_effect_policy_member_binding(
    snapshot_evidence_hash: str,
    authorization_evidence_hash: str,
    authorization_binding_hash: str,
    member: DirectedEffectInventoryMemberV1,
) -> str:
    """Bind snapshot, legacy authorization, additive binding, and member."""
    snapshot_evidence_hash = _require_hash("snapshot_evidence_hash", snapshot_evidence_hash)
    authorization_evidence_hash = _require_hash(
        "authorization_evidence_hash",
        authorization_evidence_hash,
    )
    authorization_binding_hash = _require_hash(
        "authorization_binding_hash",
        authorization_binding_hash,
    )
    if not isinstance(member, DirectedEffectInventoryMemberV1):
        raise TypeError("member must be DirectedEffectInventoryMemberV1")
    member_items_unvalidated = tuple(sorted(member.to_record().items()))
    member_items = require_directed_effect_immutable_items(
        "member identity",
        cast(DirectedEffectImmutableItemsV1, member_items_unvalidated),
    )
    return hash_directed_effect_arguments(
        (
            ("authorization_binding_hash", authorization_binding_hash),
            ("authorization_evidence_hash", authorization_evidence_hash),
            ("domain", "director_effect_policy_member_binding_v1"),
            ("member", DirectedEffectImmutableMapV1(items=member_items)),
            ("snapshot_evidence_hash", snapshot_evidence_hash),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicyBoundSnapshotV1:
    """Successful pre-seal snapshot bound to one sealed TaskRuntime member."""

    snapshot: DirectorEffectPolicySnapshotResultV1
    authorization_evidence_hash: str
    authorization_binding: DirectorEffectAuthorizationBindingV1
    authorization_binding_hash: str
    member: DirectedEffectInventoryMemberV1
    member_binding_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, DirectorEffectPolicySnapshotResultV1) or not self.snapshot.allowed:
            raise ValueError("bound snapshot requires a successful policy snapshot")
        if not isinstance(self.member, DirectedEffectInventoryMemberV1):
            raise TypeError("member must be DirectedEffectInventoryMemberV1")
        object.__setattr__(
            self,
            "authorization_evidence_hash",
            _require_hash("authorization_evidence_hash", self.authorization_evidence_hash),
        )
        if not isinstance(self.authorization_binding, DirectorEffectAuthorizationBindingV1):
            raise TypeError("authorization_binding must be DirectorEffectAuthorizationBindingV1")
        try:
            canonical_authorization_binding = validate_director_effect_authorization_binding(self.authorization_binding)
        except (TypeError, ValueError) as exc:
            raise ValueError("authorization_binding must be canonical") from exc
        if canonical_authorization_binding != self.authorization_binding:
            raise ValueError("authorization_binding must be canonical")
        object.__setattr__(
            self,
            "authorization_binding_hash",
            _require_hash("authorization_binding_hash", self.authorization_binding_hash),
        )
        if (
            self.authorization_binding.authorization_evidence.authorization_hash != self.authorization_evidence_hash
            or self.authorization_binding.authorization_binding_hash != self.authorization_binding_hash
        ):
            raise ValueError("authorization binding must retain the exact legacy authorization anchor")
        object.__setattr__(self, "member_binding_hash", _require_hash("member_binding_hash", self.member_binding_hash))
        if self.member_binding_hash != hash_directed_effect_policy_member_binding(
            self.snapshot.evidence_hash,
            self.authorization_evidence_hash,
            self.authorization_binding_hash,
            self.member,
        ):
            raise ValueError(
                "member_binding_hash must bind snapshot evidence, authorization evidence, and complete member identity"
            )


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicyMemberBindingRequestV1:
    """Pure post-seal request that binds a snapshot to an actual member."""

    snapshot: DirectorEffectPolicySnapshotResultV1
    authorization_evidence: DirectorEffectAuthorizationEvidenceV1
    authorization_binding: DirectorEffectAuthorizationBindingV1
    member: DirectedEffectInventoryMemberV1

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, DirectorEffectPolicySnapshotResultV1) or not self.snapshot.allowed:
            raise ValueError("member binding requires a successful policy snapshot")
        if not isinstance(self.authorization_evidence, DirectorEffectAuthorizationEvidenceV1):
            raise TypeError("authorization_evidence must be DirectorEffectAuthorizationEvidenceV1")
        if not isinstance(self.authorization_binding, DirectorEffectAuthorizationBindingV1):
            raise TypeError("authorization_binding must be DirectorEffectAuthorizationBindingV1")
        if self.authorization_binding.authorization_evidence != self.authorization_evidence:
            raise ValueError("authorization_binding must retain authorization_evidence")
        if not isinstance(self.member, DirectedEffectInventoryMemberV1):
            raise TypeError("member must be DirectedEffectInventoryMemberV1")


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicyMemberBindingResultV1:
    """Typed result of exact policy snapshot/member binding."""

    status: DirectorEffectPolicySnapshotStatusV1
    error_code: DirectedEffectErrorCodeV1 | None
    member: DirectedEffectInventoryMemberV1 | None
    member_binding_hash: str | None
    bound_snapshot: DirectorEffectPolicyBoundSnapshotV1 | None
    authorization_binding_hash: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"allowed", "denied"}:
            raise ValueError("status must be allowed or denied")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.status == "allowed":
            if (
                not isinstance(self.member, DirectedEffectInventoryMemberV1)
                or self.member_binding_hash is None
                or self.authorization_binding_hash is None
                or self.bound_snapshot is None
            ):
                raise ValueError("allowed member binding requires member and bound snapshot")
            object.__setattr__(
                self, "member_binding_hash", _require_hash("member_binding_hash", self.member_binding_hash)
            )
            object.__setattr__(
                self,
                "authorization_binding_hash",
                _require_hash("authorization_binding_hash", self.authorization_binding_hash),
            )
            if not isinstance(self.bound_snapshot, DirectorEffectPolicyBoundSnapshotV1):
                raise TypeError("bound_snapshot must be DirectorEffectPolicyBoundSnapshotV1")
            if self.error_code is not None:
                raise ValueError("allowed member binding cannot carry an error_code")
            if self.member != self.bound_snapshot.member:
                raise ValueError("allowed member binding member identity must match bound snapshot")
            if self.member_binding_hash != self.bound_snapshot.member_binding_hash:
                raise ValueError("allowed member_binding_hash must match bound snapshot")
            if self.authorization_binding_hash != self.bound_snapshot.authorization_binding_hash:
                raise ValueError("allowed authorization_binding_hash must match bound snapshot")
        elif (
            any(
                (
                    self.member,
                    self.member_binding_hash,
                    self.authorization_binding_hash,
                    self.bound_snapshot,
                )
            )
            or self.error_code is None
        ):
            raise ValueError("denied member binding cannot retain capability")


def validate_director_effect_policy_bound_snapshot(
    bound_snapshot: DirectorEffectPolicyBoundSnapshotV1,
) -> DirectorEffectPolicyBoundSnapshotV1:
    """Reconstruct a bound snapshot and reject forged nested policy evidence."""

    if type(bound_snapshot) is not DirectorEffectPolicyBoundSnapshotV1:
        raise TypeError("bound_snapshot must be exactly DirectorEffectPolicyBoundSnapshotV1")
    canonical_snapshot = validate_director_effect_policy_snapshot_result(bound_snapshot.snapshot)
    canonical_authorization_binding = validate_director_effect_authorization_binding(
        bound_snapshot.authorization_binding
    )
    canonical_member = DirectedEffectInventoryMemberV1.from_record(bound_snapshot.member.to_record())
    canonical = DirectorEffectPolicyBoundSnapshotV1(
        snapshot=canonical_snapshot,
        authorization_evidence_hash=bound_snapshot.authorization_evidence_hash,
        authorization_binding=canonical_authorization_binding,
        authorization_binding_hash=bound_snapshot.authorization_binding_hash,
        member=canonical_member,
        member_binding_hash=bound_snapshot.member_binding_hash,
    )
    if canonical != bound_snapshot:
        raise ValueError("bound snapshot canonical reconstruction mismatch")
    return canonical


def validate_director_effect_policy_member_binding_result(
    result: DirectorEffectPolicyMemberBindingResultV1,
) -> DirectorEffectPolicyMemberBindingResultV1:
    """Reconstruct one binding result before it can become a prepared capability."""

    if type(result) is not DirectorEffectPolicyMemberBindingResultV1:
        raise TypeError("result must be exactly DirectorEffectPolicyMemberBindingResultV1")
    canonical_bound = (
        validate_director_effect_policy_bound_snapshot(result.bound_snapshot)
        if result.bound_snapshot is not None
        else None
    )
    canonical_member = (
        DirectedEffectInventoryMemberV1.from_record(result.member.to_record()) if result.member is not None else None
    )
    canonical = DirectorEffectPolicyMemberBindingResultV1(
        status=result.status,
        error_code=result.error_code,
        member=canonical_member,
        member_binding_hash=result.member_binding_hash,
        bound_snapshot=canonical_bound,
        authorization_binding_hash=result.authorization_binding_hash,
    )
    if canonical != result:
        raise ValueError("policy member binding result canonical reconstruction mismatch")
    return canonical


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicyRevalidationRequestV1:
    """Execution-time request to recompute the same policy boundary."""

    bound_snapshot: DirectorEffectPolicyBoundSnapshotV1
    workspace: str
    actual_normalized_tool_name: str
    actual_normalized_arguments: DirectedEffectImmutableItemsV1
    actual_arguments_hash: str
    authorization_evidence: DirectorEffectAuthorizationEvidenceV1
    member: DirectedEffectInventoryMemberV1
    operation_id: str
    claim_grant: DirectedEffectClaimGrantV1
    current_job_token_restriction_evidence: DirectedEffectImmutableItemsV1

    def __post_init__(self) -> None:
        if not isinstance(self.bound_snapshot, DirectorEffectPolicyBoundSnapshotV1):
            raise TypeError("bound_snapshot must be DirectorEffectPolicyBoundSnapshotV1")
        object.__setattr__(self, "workspace", _require_token("workspace", self.workspace))
        object.__setattr__(
            self,
            "actual_normalized_tool_name",
            _require_token("actual_normalized_tool_name", self.actual_normalized_tool_name),
        )
        object.__setattr__(
            self,
            "actual_normalized_arguments",
            require_directed_effect_immutable_items("actual_normalized_arguments", self.actual_normalized_arguments),
        )
        object.__setattr__(
            self,
            "actual_arguments_hash",
            _require_hash("actual_arguments_hash", self.actual_arguments_hash),
        )
        if self.actual_arguments_hash != hash_directed_effect_arguments(self.actual_normalized_arguments):
            raise ValueError("actual_arguments_hash payload mismatch for actual normalized arguments")
        if not isinstance(self.authorization_evidence, DirectorEffectAuthorizationEvidenceV1):
            raise TypeError("authorization_evidence must be DirectorEffectAuthorizationEvidenceV1")
        self.authorization_evidence.validate_arguments_binding(self.actual_normalized_arguments)
        if not isinstance(self.member, DirectedEffectInventoryMemberV1):
            raise TypeError("member must be DirectedEffectInventoryMemberV1")
        object.__setattr__(self, "operation_id", _require_token("operation_id", self.operation_id))
        if not isinstance(self.claim_grant, DirectedEffectClaimGrantV1):
            raise TypeError("claim_grant must be DirectedEffectClaimGrantV1")
        object.__setattr__(
            self,
            "current_job_token_restriction_evidence",
            require_directed_effect_immutable_items(
                "current_job_token_restriction_evidence",
                self.current_job_token_restriction_evidence,
            ),
        )
        validate_directed_effect_identity_binding(
            boundary_name="policy revalidation request",
            authorization_evidence=self.authorization_evidence,
            claim_grant=self.claim_grant,
            normalized_tool_name=self.actual_normalized_tool_name,
            arguments_hash=self.actual_arguments_hash,
            workspace=self.workspace,
            member=self.member,
            operation_id=self.operation_id,
        )
        snapshot = self.bound_snapshot.snapshot
        subject = snapshot.subject
        if (
            self.member != self.bound_snapshot.member
            or self.operation_id != self.member.operation_id
            or self.workspace != subject.workspace
            or self.actual_normalized_tool_name != subject.normalized_tool_name
            or self.actual_normalized_arguments != subject.normalized_arguments
            or self.actual_arguments_hash != hash_directed_effect_arguments(subject.normalized_arguments)
            or self.member.ordinal != subject.inventory_ordinal
            or self.member.tool_call_id != subject.tool_call_id
            or self.member.normalized_tool_name != subject.normalized_tool_name
            or self.member.effect_type != subject.effect_type
            or self.member.execution_mode != subject.execution_mode
            or self.authorization_evidence.workspace != subject.workspace
            or self.authorization_evidence.turn_id != subject.turn_id
            or self.authorization_evidence.batch_id != subject.batch_id
            or self.authorization_evidence.tool_call_id != subject.tool_call_id
            or self.authorization_evidence.normalized_tool_name != subject.normalized_tool_name
            or self.authorization_evidence.arguments_hash
            != hash_directed_effect_arguments(subject.normalized_arguments)
            or snapshot.policy_hash != self.authorization_evidence.policy_hash
            or snapshot.target_state_hash != self.authorization_evidence.target_state_hash
            or snapshot.normalized_operation_hash != self.authorization_evidence.normalized_operation_hash
            or snapshot.normalized_operation_hash != subject.prospective_operation_hash
            or snapshot.evidence_hash != self.authorization_evidence.bound_policy_snapshot_hash
            or self.bound_snapshot.authorization_evidence_hash != self.authorization_evidence.authorization_hash
        ):
            raise ValueError("policy revalidation request identity mismatch")


@dataclass(frozen=True, slots=True)
class DirectorEffectPolicyRevalidationResultV1:
    """Current policy verdict immediately before a physical mutation."""

    status: DirectorEffectPolicySnapshotStatusV1
    allowed: bool
    error_code: DirectedEffectErrorCodeV1 | None
    current_policy_version: str
    current_policy_hash: str
    current_target_state_evidence: DirectorEffectTargetStateEvidenceV1
    current_target_state_hash: str
    current_normalized_operation_hash: str
    target_observation_performed: bool
    current_evidence_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed", require_directed_effect_bool("allowed", self.allowed))
        if self.status not in {"allowed", "denied"}:
            raise ValueError("status must be allowed or denied")
        if self.allowed != (self.status == "allowed"):
            raise ValueError("allowed must agree with status")
        object.__setattr__(
            self,
            "target_observation_performed",
            require_directed_effect_bool(
                "target_observation_performed",
                self.target_observation_performed,
            ),
        )
        if self.allowed and not self.target_observation_performed:
            raise ValueError("allowed revalidation requires a fresh target observation")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.allowed and self.error_code is not None:
            raise ValueError("allowed revalidation cannot carry an error_code")
        if not self.allowed and self.error_code is None:
            raise ValueError("denied revalidation requires an error_code")
        object.__setattr__(
            self, "current_policy_version", _require_token("current_policy_version", self.current_policy_version)
        )
        if not isinstance(self.current_target_state_evidence, DirectorEffectTargetStateEvidenceV1):
            raise TypeError("current_target_state_evidence must be DirectorEffectTargetStateEvidenceV1")
        for field_name in (
            "current_policy_hash",
            "current_target_state_hash",
            "current_normalized_operation_hash",
            "current_evidence_hash",
        ):
            object.__setattr__(self, field_name, _require_hash(field_name, getattr(self, field_name)))
        if self.current_target_state_hash != self.current_target_state_evidence.target_state_hash:
            raise ValueError("current_target_state_hash must match current target-state evidence")
        if self.current_evidence_hash != hash_directed_effect_policy_revalidation_evidence(
            status=self.status,
            allowed=self.allowed,
            error_code=self.error_code,
            current_policy_version=self.current_policy_version,
            current_policy_hash=self.current_policy_hash,
            current_target_state_evidence=self.current_target_state_evidence,
            current_normalized_operation_hash=self.current_normalized_operation_hash,
            target_observation_performed=self.target_observation_performed,
        ):
            raise ValueError("current_evidence_hash must bind complete current policy evidence")


def hash_directed_effect_policy_revalidation_evidence(
    *,
    status: DirectorEffectPolicySnapshotStatusV1,
    allowed: bool,
    error_code: DirectedEffectErrorCodeV1 | None,
    current_policy_version: str,
    current_policy_hash: str,
    current_target_state_evidence: DirectorEffectTargetStateEvidenceV1,
    current_normalized_operation_hash: str,
    target_observation_performed: bool,
) -> str:
    """Bind the complete current revalidation verdict in its own hash domain."""
    if status not in {"allowed", "denied"}:
        raise ValueError("status must be allowed or denied")
    allowed = require_directed_effect_bool("allowed", allowed)
    target_observation_performed = require_directed_effect_bool(
        "target_observation_performed",
        target_observation_performed,
    )
    error_code = _require_error_code(error_code)
    current_policy_version = _require_token("current_policy_version", current_policy_version)
    current_policy_hash = _require_hash("current_policy_hash", current_policy_hash)
    current_normalized_operation_hash = _require_hash(
        "current_normalized_operation_hash",
        current_normalized_operation_hash,
    )
    if not isinstance(current_target_state_evidence, DirectorEffectTargetStateEvidenceV1):
        raise TypeError("current_target_state_evidence must be DirectorEffectTargetStateEvidenceV1")
    return hash_directed_effect_arguments(
        (
            ("allowed", allowed),
            (
                "current_target_state_evidence",
                DirectedEffectImmutableMapV1(items=_target_state_evidence_items(current_target_state_evidence)),
            ),
            ("current_normalized_operation_hash", current_normalized_operation_hash),
            ("current_policy_hash", current_policy_hash),
            ("current_policy_version", current_policy_version),
            ("domain", "director_effect_policy_revalidation_evidence_v1"),
            ("error_code", error_code),
            ("status", status),
            ("target_observation_performed", target_observation_performed),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectorEffectCurrentPolicyEvidenceCaptureRequestV1:
    """Post-claim request accepted only by the adapter-owned evidence producer."""

    baseline_authorization_binding: DirectorEffectAuthorizationBindingV1
    baseline_public_policy_evidence: DirectorEffectPublicPolicyEvidenceV1
    bound_snapshot: DirectorEffectPolicyBoundSnapshotV1
    claimed_member: DirectedEffectInventoryMemberV1
    claim_grant: DirectedEffectClaimGrantV1
    normalized_tool: str
    normalized_arguments_hash: str
    current_job_token_restriction_evidence: DirectedEffectImmutableItemsV1

    def __post_init__(self) -> None:
        binding = validate_director_effect_authorization_binding(self.baseline_authorization_binding)
        public_policy = validate_director_effect_public_policy_evidence(self.baseline_public_policy_evidence)
        bound_snapshot = validate_director_effect_policy_bound_snapshot(self.bound_snapshot)
        object.__setattr__(
            self,
            "normalized_tool",
            _require_token("normalized_tool", self.normalized_tool),
        )
        object.__setattr__(
            self,
            "normalized_arguments_hash",
            _require_hash(
                "normalized_arguments_hash",
                self.normalized_arguments_hash,
            ),
        )
        object.__setattr__(
            self,
            "current_job_token_restriction_evidence",
            require_directed_effect_immutable_items(
                "current_job_token_restriction_evidence",
                self.current_job_token_restriction_evidence,
            ),
        )
        if type(self.claimed_member) is not DirectedEffectInventoryMemberV1:
            raise TypeError("claimed_member must be exactly DirectedEffectInventoryMemberV1")
        if type(self.claim_grant) is not DirectedEffectClaimGrantV1:
            raise TypeError("claim_grant must be exactly DirectedEffectClaimGrantV1")
        authorization = binding.authorization_evidence
        if (
            binding != self.baseline_authorization_binding
            or public_policy.source_authorization_binding_hash != binding.authorization_binding_hash
            or bound_snapshot != self.bound_snapshot
            or bound_snapshot.authorization_binding != binding
            or bound_snapshot.member != self.claimed_member
            or self.claim_grant.member != self.claimed_member
            or self.claim_grant.operation.operation_id != self.claimed_member.operation_id
            or authorization.normalized_tool_name != self.normalized_tool
            or authorization.arguments_hash != self.normalized_arguments_hash
        ):
            raise ValueError("current policy capture request identity mismatch")


def hash_director_effect_current_policy_evidence(
    *,
    baseline_authorization_binding_hash: str,
    baseline_public_policy_evidence_hash: str,
    bound_member_hash: str,
    claim_grant_hash: str,
    policy_target_version: str,
    policy_target_hash: str,
    operation_version: str,
    operation_hash: str,
    capability_scope_version: str,
    capability_scope_hash: str,
    job_token_id: str,
    job_token_version: str,
    job_token_evidence_hash: str,
    tool_spec_snapshot_hash: str,
    alias_binding_hash: str,
    execution_envelope_version: str,
    execution_envelope_hash: str,
    allowed_commands_version: str,
    allowed_commands_hash: str,
) -> str:
    """Bind every post-claim current-source observation in one hash domain."""

    return hash_directed_effect_arguments(
        (
            (
                "baseline_authorization_binding_hash",
                _require_hash(
                    "baseline_authorization_binding_hash",
                    baseline_authorization_binding_hash,
                ),
            ),
            (
                "baseline_public_policy_evidence_hash",
                _require_hash(
                    "baseline_public_policy_evidence_hash",
                    baseline_public_policy_evidence_hash,
                ),
            ),
            ("bound_member_hash", _require_hash("bound_member_hash", bound_member_hash)),
            ("claim_grant_hash", _require_hash("claim_grant_hash", claim_grant_hash)),
            ("policy_target_version", _require_token("policy_target_version", policy_target_version)),
            ("policy_target_hash", _require_hash("policy_target_hash", policy_target_hash)),
            ("operation_version", _require_token("operation_version", operation_version)),
            ("operation_hash", _require_hash("operation_hash", operation_hash)),
            (
                "capability_scope_version",
                _require_token("capability_scope_version", capability_scope_version),
            ),
            (
                "capability_scope_hash",
                _require_hash("capability_scope_hash", capability_scope_hash),
            ),
            ("job_token_id", _require_token("job_token_id", job_token_id)),
            ("job_token_version", _require_token("job_token_version", job_token_version)),
            (
                "job_token_evidence_hash",
                _require_hash("job_token_evidence_hash", job_token_evidence_hash),
            ),
            (
                "tool_spec_snapshot_hash",
                _require_hash("tool_spec_snapshot_hash", tool_spec_snapshot_hash),
            ),
            ("alias_binding_hash", _require_hash("alias_binding_hash", alias_binding_hash)),
            (
                "execution_envelope_version",
                _require_token("execution_envelope_version", execution_envelope_version),
            ),
            (
                "execution_envelope_hash",
                _require_hash("execution_envelope_hash", execution_envelope_hash),
            ),
            (
                "allowed_commands_version",
                _require_token("allowed_commands_version", allowed_commands_version),
            ),
            (
                "allowed_commands_hash",
                _require_hash("allowed_commands_hash", allowed_commands_hash),
            ),
            ("domain", "director_effect_current_policy_evidence_v1"),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectorEffectCurrentPolicyEvidenceV1:
    """Versioned post-claim evidence; only the policy port may produce it."""

    baseline_authorization_binding_hash: str
    baseline_public_policy_evidence_hash: str
    bound_member_hash: str
    claim_grant_hash: str
    policy_target_version: str
    policy_target_hash: str
    operation_version: str
    operation_hash: str
    capability_scope_version: str
    capability_scope_hash: str
    job_token_id: str
    job_token_version: str
    job_token_evidence_hash: str
    tool_spec_snapshot_hash: str
    alias_binding_hash: str
    execution_envelope_version: str
    execution_envelope_hash: str
    allowed_commands_version: str
    allowed_commands_hash: str
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        computed = hash_director_effect_current_policy_evidence(
            baseline_authorization_binding_hash=self.baseline_authorization_binding_hash,
            baseline_public_policy_evidence_hash=self.baseline_public_policy_evidence_hash,
            bound_member_hash=self.bound_member_hash,
            claim_grant_hash=self.claim_grant_hash,
            policy_target_version=self.policy_target_version,
            policy_target_hash=self.policy_target_hash,
            operation_version=self.operation_version,
            operation_hash=self.operation_hash,
            capability_scope_version=self.capability_scope_version,
            capability_scope_hash=self.capability_scope_hash,
            job_token_id=self.job_token_id,
            job_token_version=self.job_token_version,
            job_token_evidence_hash=self.job_token_evidence_hash,
            tool_spec_snapshot_hash=self.tool_spec_snapshot_hash,
            alias_binding_hash=self.alias_binding_hash,
            execution_envelope_version=self.execution_envelope_version,
            execution_envelope_hash=self.execution_envelope_hash,
            allowed_commands_version=self.allowed_commands_version,
            allowed_commands_hash=self.allowed_commands_hash,
        )
        object.__setattr__(self, "evidence_hash", computed)


def validate_director_effect_current_policy_evidence(
    evidence: DirectorEffectCurrentPolicyEvidenceV1,
) -> DirectorEffectCurrentPolicyEvidenceV1:
    """Canonical-reconstruct current evidence before execution may consume it."""

    if type(evidence) is not DirectorEffectCurrentPolicyEvidenceV1:
        raise TypeError("evidence must be exactly DirectorEffectCurrentPolicyEvidenceV1")
    canonical = DirectorEffectCurrentPolicyEvidenceV1(
        baseline_authorization_binding_hash=evidence.baseline_authorization_binding_hash,
        baseline_public_policy_evidence_hash=evidence.baseline_public_policy_evidence_hash,
        bound_member_hash=evidence.bound_member_hash,
        claim_grant_hash=evidence.claim_grant_hash,
        policy_target_version=evidence.policy_target_version,
        policy_target_hash=evidence.policy_target_hash,
        operation_version=evidence.operation_version,
        operation_hash=evidence.operation_hash,
        capability_scope_version=evidence.capability_scope_version,
        capability_scope_hash=evidence.capability_scope_hash,
        job_token_id=evidence.job_token_id,
        job_token_version=evidence.job_token_version,
        job_token_evidence_hash=evidence.job_token_evidence_hash,
        tool_spec_snapshot_hash=evidence.tool_spec_snapshot_hash,
        alias_binding_hash=evidence.alias_binding_hash,
        execution_envelope_version=evidence.execution_envelope_version,
        execution_envelope_hash=evidence.execution_envelope_hash,
        allowed_commands_version=evidence.allowed_commands_version,
        allowed_commands_hash=evidence.allowed_commands_hash,
    )
    if canonical != evidence:
        raise ValueError("current policy evidence canonical reconstruction mismatch")
    return canonical


def validate_director_effect_current_policy_capture_result(
    result: DirectorEffectCurrentPolicyEvidenceCaptureResultV1,
) -> DirectorEffectCurrentPolicyEvidenceCaptureResultV1:
    """Canonical-reconstruct the closed producer result and nested evidence."""

    if type(result) is not DirectorEffectCurrentPolicyEvidenceCaptureResultV1:
        raise TypeError("result must be exactly DirectorEffectCurrentPolicyEvidenceCaptureResultV1")
    canonical_evidence = (
        validate_director_effect_current_policy_evidence(result.evidence) if result.evidence is not None else None
    )
    canonical = DirectorEffectCurrentPolicyEvidenceCaptureResultV1(
        status=result.status,
        evidence=canonical_evidence,
        error_code=result.error_code,
    )
    if canonical != result:
        raise ValueError("current policy capture result canonical reconstruction mismatch")
    return canonical


@dataclass(frozen=True, slots=True)
class DirectorEffectCurrentPolicyEvidenceCaptureResultV1:
    """Closed capture result with no third or malformed state."""

    status: Literal["captured", "denied"]
    evidence: DirectorEffectCurrentPolicyEvidenceV1 | None
    error_code: DirectorEffectCurrentPolicyEvidenceCaptureErrorCodeV1 | None

    def __post_init__(self) -> None:
        if self.status == "captured":
            if type(self.evidence) is not DirectorEffectCurrentPolicyEvidenceV1 or self.error_code is not None:
                raise ValueError("captured requires evidence and no error")
        elif self.status == "denied":
            if self.evidence is not None or self.error_code != "deo_current_policy_evidence_unavailable":
                raise ValueError("denied requires no evidence and the closed error")
        else:
            raise ValueError("unsupported capture status")


@runtime_checkable
class DirectorEffectCurrentPolicyEvidenceCapturePortV1(Protocol):
    """Sole post-claim current-evidence producer boundary."""

    async def capture_current_policy_evidence(
        self,
        request: DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    ) -> DirectorEffectCurrentPolicyEvidenceCaptureResultV1:
        """Capture versioned evidence or return the one closed denial."""


@runtime_checkable
class DirectorEffectPolicySnapshotPortV1(
    DirectorEffectCurrentPolicyEvidenceCapturePortV1,
    Protocol,
):
    """Adapter-owned policy boundary consumed without adapter imports."""

    async def capture_baseline_snapshot(
        self,
        request: DirectorEffectPolicyBaselineCaptureRequestV1,
    ) -> DirectorEffectPolicySnapshotResultV1:
        """Read target state and capture the baseline through the sole adapter."""

    async def snapshot(self, request: DirectorEffectPolicySnapshotRequestV1) -> DirectorEffectPolicySnapshotResultV1:
        """Capture a no-effect policy snapshot for a prospective mutation."""

    def bind_member(
        self, request: DirectorEffectPolicyMemberBindingRequestV1
    ) -> DirectorEffectPolicyMemberBindingResultV1:
        """Bind one successful snapshot to the exact sealed TaskRuntime member."""

    async def revalidate(
        self,
        request: DirectorEffectPolicyRevalidationRequestV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        """Re-evaluate the same policy immediately before physical execution."""
