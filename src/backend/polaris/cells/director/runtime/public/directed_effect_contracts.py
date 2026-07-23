"""Immutable public contracts for DEO-2B Director authorization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from polaris.cells.runtime.task_runtime.public import (
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectParentRegistryIdentityV1,
)

if TYPE_CHECKING:
    from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
        DirectorEffectPolicyBoundSnapshotV1,
    )

DirectedEffectErrorCodeV1: TypeAlias = Literal[
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
]

DirectedEffectApplicabilityV1: TypeAlias = Literal["mutation_capable", "read_only"]
DirectorEffectPreflightStatusV1: TypeAlias = Literal["authorized", "not_applicable", "denied"]
DirectorEffectExecutionValidationStatusV1: TypeAlias = Literal["allowed", "denied"]


def create_directed_effect_inventory_intent(
    *,
    ordinal: int,
    tool_call_id: str,
    normalized_tool_name: str,
    effect_type: Literal["write", "async"],
    execution_mode: Literal["write_serial", "async_receipt"],
    prospective_operation_hash: str,
) -> DirectedEffectInventoryIntentV1:
    """Project one TaskRuntime inventory value from already-validated public evidence."""
    return DirectedEffectInventoryIntentV1(
        ordinal=ordinal,
        tool_call_id=tool_call_id,
        normalized_tool_name=normalized_tool_name,
        effect_type=effect_type,
        execution_mode=execution_mode,
        intended_effect_fingerprint=prospective_operation_hash,
        policy_verdict_hash=prospective_operation_hash,
        expected_receipt_binding_hash=prospective_operation_hash,
    )


@dataclass(frozen=True, slots=True)
class DirectedEffectImmutableMapV1:
    """Explicit immutable map value with recursively canonicalized items."""

    items: DirectedEffectImmutableItemsV1

    def __post_init__(self) -> None:
        normalized = _normalize_directed_effect_immutable_items("map items", self.items)
        object.__setattr__(self, "items", tuple(sorted(normalized, key=lambda item: item[0])))


@dataclass(frozen=True, slots=True)
class DirectedEffectImmutableSequenceV1:
    """Explicit immutable ordered sequence value."""

    items: tuple[DirectedEffectImmutableValueV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple):
            raise TypeError("sequence items must be an immutable tuple")
        object.__setattr__(self, "items", tuple(_require_immutable_value(item) for item in self.items))


DirectedEffectImmutableValueV1: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["DirectedEffectImmutableValueV1", ...]
    | DirectedEffectImmutableMapV1
    | DirectedEffectImmutableSequenceV1
)
DirectedEffectImmutableItemsV1: TypeAlias = tuple[tuple[str, DirectedEffectImmutableValueV1], ...]

_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
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
    }
)
_LOWER_HEX: Final[frozenset[str]] = frozenset("0123456789abcdef")


def _require_token(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _require_hash(name: str, value: str) -> str:
    normalized = _require_token(name, value)
    if len(normalized) != 64 or not set(normalized).issubset(_LOWER_HEX):
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 digest")
    return normalized


def _require_error_code(value: DirectedEffectErrorCodeV1 | None) -> DirectedEffectErrorCodeV1 | None:
    if value is not None and value not in _ERROR_CODES:
        raise ValueError("error_code must be a DirectedEffectErrorCodeV1 value")
    return value


def require_directed_effect_hash(name: str, value: str) -> str:
    """Validate one canonical lowercase SHA-256 digest for a DEO contract."""
    return _require_hash(name, value)


def require_directed_effect_bool(name: str, value: object) -> bool:
    """Require an exact bool without accepting integer substitutes."""
    if type(value) is not bool:
        raise TypeError(f"{name} must be exactly bool")
    return value


def validate_directed_effect_error_code(
    value: DirectedEffectErrorCodeV1 | None,
) -> DirectedEffectErrorCodeV1 | None:
    """Validate that an optional denial belongs to the closed DEO taxonomy."""
    return _require_error_code(value)


def _require_immutable_value(value: object) -> DirectedEffectImmutableValueV1:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, DirectedEffectImmutableMapV1 | DirectedEffectImmutableSequenceV1):
        return value
    if isinstance(value, Mapping):
        raise TypeError("mutable mappings are not permitted in DEO contracts")
    if isinstance(value, tuple):
        return tuple(_require_immutable_value(item) for item in value)
    raise TypeError("DEO contract payload values must be immutable scalars or tuples")


def _normalize_directed_effect_immutable_items(
    name: str,
    value: DirectedEffectImmutableItemsV1,
) -> DirectedEffectImmutableItemsV1:
    if isinstance(value, Mapping) or not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple of immutable key/value pairs")
    normalized: list[tuple[str, DirectedEffectImmutableValueV1]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(f"{name} entries must be two-item tuples")
        key, item_value = item
        normalized.append((_require_token(f"{name} key", key), _require_immutable_value(item_value)))
    result = tuple(normalized)
    if len({key for key, _ in result}) != len(result):
        raise ValueError(f"{name} must not contain duplicate keys")
    return result


def require_directed_effect_immutable_items(
    name: str,
    value: DirectedEffectImmutableItemsV1,
) -> DirectedEffectImmutableItemsV1:
    """Validate one sorted immutable key/value payload without coercion."""
    result = _normalize_directed_effect_immutable_items(name, value)
    if result != tuple(sorted(result, key=lambda item: item[0])):
        raise ValueError(f"{name} must be sorted by key")
    return result


def hash_directed_effect_arguments(value: DirectedEffectImmutableItemsV1) -> str:
    """Return the SHA-256 of one canonical immutable arguments payload."""
    normalized = _normalize_directed_effect_immutable_items("normalized_arguments", value)
    canonical = (
        "map",
        tuple(
            (key, _canonicalize_directed_effect_value(item_value))
            for key, item_value in sorted(normalized, key=lambda item: item[0])
        ),
    )
    encoded = json.dumps(
        canonical,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonicalize_directed_effect_value(value: DirectedEffectImmutableValueV1) -> object:
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, DirectedEffectImmutableMapV1):
        return (
            "map",
            tuple((key, _canonicalize_directed_effect_value(item)) for key, item in value.items),
        )
    if isinstance(value, DirectedEffectImmutableSequenceV1):
        return ("sequence", tuple(_canonicalize_directed_effect_value(item) for item in value.items))
    return ("sequence", tuple(_canonicalize_directed_effect_value(item) for item in value))


def _require_sorted_tokens(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be an immutable tuple")
    normalized = tuple(_require_token(name, value) for value in values)
    if normalized != tuple(sorted(normalized)) or len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must be sorted and unique")
    return normalized


def hash_director_effect_authorization_evidence(
    *,
    workspace: str,
    execution_attempt_id: str,
    turn_id: str,
    batch_id: str,
    tool_call_id: str,
    normalized_tool_name: str,
    arguments_hash: str,
    tool_spec_hash: str,
    role_policy_id: str,
    role_policy_hash: str,
    canonical_allow_list_hash: str,
    capability_scope: tuple[str, ...],
    capability_scope_hash: str,
    job_token_id: str,
    job_token_evidence_hash: str,
    execution_envelope_hash: str,
    allowed_command_hash: str,
    mutation_guard_mode: str,
    bound_policy_snapshot_hash: str,
    target_state_hash: str,
    normalized_operation_hash: str,
    policy_version: str,
    policy_hash: str,
) -> str:
    """Bind every authority-bearing authorization field in a distinct domain."""
    return hash_directed_effect_arguments(
        (
            ("allowed_command_hash", allowed_command_hash),
            ("arguments_hash", arguments_hash),
            ("batch_id", batch_id),
            ("bound_policy_snapshot_hash", bound_policy_snapshot_hash),
            ("canonical_allow_list_hash", canonical_allow_list_hash),
            ("capability_scope", capability_scope),
            ("capability_scope_hash", capability_scope_hash),
            ("domain", "director_effect_authorization_evidence_v1"),
            ("execution_attempt_id", execution_attempt_id),
            ("execution_envelope_hash", execution_envelope_hash),
            ("job_token_evidence_hash", job_token_evidence_hash),
            ("job_token_id", job_token_id),
            ("mutation_guard_mode", mutation_guard_mode),
            ("normalized_operation_hash", normalized_operation_hash),
            ("normalized_tool_name", normalized_tool_name),
            ("policy_hash", policy_hash),
            ("policy_version", policy_version),
            ("role_policy_hash", role_policy_hash),
            ("role_policy_id", role_policy_id),
            ("target_state_hash", target_state_hash),
            ("tool_call_id", tool_call_id),
            ("tool_spec_hash", tool_spec_hash),
            ("turn_id", turn_id),
            ("workspace", workspace),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectedEffectIdentityViewV1:
    """Canonical immutable identity shared by all pre-execution boundaries."""

    workspace: str
    execution_attempt_id: str
    turn_id: str
    batch_id: str
    tool_call_id: str
    normalized_tool_name: str
    arguments_hash: str
    parent_binding_id: str
    effect_id: str
    operation_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "workspace",
            "execution_attempt_id",
            "turn_id",
            "batch_id",
            "tool_call_id",
            "normalized_tool_name",
            "parent_binding_id",
            "effect_id",
            "operation_id",
        ):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        object.__setattr__(self, "arguments_hash", _require_hash("arguments_hash", self.arguments_hash))


def validate_directed_effect_identity_binding(
    *,
    boundary_name: str,
    authorization_evidence: DirectorEffectAuthorizationEvidenceV1,
    claim_grant: DirectedEffectClaimGrantV1,
    normalized_tool_name: str,
    arguments_hash: str,
    workspace: str | None = None,
    batch_id: str | None = None,
    tool_call_id: str | None = None,
    member: DirectedEffectInventoryMemberV1 | None = None,
    operation_id: str | None = None,
) -> DirectedEffectIdentityViewV1:
    """Validate and project one exact evidence/grant/outer identity binding."""
    if not isinstance(authorization_evidence, DirectorEffectAuthorizationEvidenceV1):
        raise TypeError("authorization_evidence must be DirectorEffectAuthorizationEvidenceV1")
    if not isinstance(claim_grant, DirectedEffectClaimGrantV1):
        raise TypeError("claim_grant must be DirectedEffectClaimGrantV1")
    normalized_boundary = _require_token("boundary_name", boundary_name)
    normalized_tool = _require_token("normalized_tool_name", normalized_tool_name)
    normalized_arguments_hash = _require_hash("arguments_hash", arguments_hash)
    grant_member = claim_grant.member
    grant_operation = claim_grant.operation
    grant_parent = claim_grant.parent_binding
    grant_attempt = claim_grant.execution_attempt
    if not isinstance(grant_member, DirectedEffectInventoryMemberV1):
        raise TypeError("claim_grant.member must be DirectedEffectInventoryMemberV1")
    expected_registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(grant_attempt)
    nested_identity_matches = (
        grant_parent.registry_identity == expected_registry_identity
        and grant_operation.workspace == grant_attempt.workspace
        and grant_operation.task_id == grant_attempt.task_id
        and grant_operation.execution_attempt_id == expected_registry_identity.execution_attempt_id
        and grant_operation.parent_binding_id == grant_parent.binding_id
        and grant_operation.parent_sequence == grant_parent.parent_sequence
        and grant_operation.operation_stream_token == grant_parent.operation_stream_token
        and grant_operation.tool_call_id == grant_member.tool_call_id
        and grant_operation.effect_id == grant_member.effect_id
        and grant_operation.operation_id == grant_member.operation_id
    )
    evidence_identity_matches = (
        authorization_evidence.workspace == grant_attempt.workspace
        and authorization_evidence.execution_attempt_id == expected_registry_identity.execution_attempt_id
        and authorization_evidence.turn_id == grant_parent.correlation.turn_id
        and authorization_evidence.batch_id == grant_parent.correlation.batch_id
        and authorization_evidence.tool_call_id == grant_member.tool_call_id
        and authorization_evidence.normalized_tool_name == grant_member.normalized_tool_name
        and normalized_tool == authorization_evidence.normalized_tool_name
        and normalized_arguments_hash == authorization_evidence.arguments_hash
    )
    outer_identity_matches = (
        (workspace is None or workspace == authorization_evidence.workspace)
        and (batch_id is None or batch_id == authorization_evidence.batch_id)
        and (tool_call_id is None or tool_call_id == authorization_evidence.tool_call_id)
        and (member is None or member == grant_member)
        and (operation_id is None or operation_id == grant_operation.operation_id)
    )
    if not nested_identity_matches or not evidence_identity_matches or not outer_identity_matches:
        raise ValueError(f"{normalized_boundary} identity mismatch")
    return DirectedEffectIdentityViewV1(
        workspace=authorization_evidence.workspace,
        execution_attempt_id=authorization_evidence.execution_attempt_id,
        turn_id=authorization_evidence.turn_id,
        batch_id=authorization_evidence.batch_id,
        tool_call_id=authorization_evidence.tool_call_id,
        normalized_tool_name=authorization_evidence.normalized_tool_name,
        arguments_hash=authorization_evidence.arguments_hash,
        parent_binding_id=grant_parent.binding_id,
        effect_id=grant_member.effect_id,
        operation_id=grant_operation.operation_id,
    )


@dataclass(frozen=True, slots=True)
class DirectedEffectClassificationResultV1:
    """One canonical tool classification before mutation admission."""

    applicability: DirectedEffectApplicabilityV1
    canonical_tool_name: str
    normalized_arguments: DirectedEffectImmutableItemsV1
    arguments_hash: str
    error_code: DirectedEffectErrorCodeV1 | None = None

    def __post_init__(self) -> None:
        if self.applicability not in {"mutation_capable", "read_only"}:
            raise ValueError("applicability must be mutation_capable or read_only")
        object.__setattr__(self, "canonical_tool_name", _require_token("canonical_tool_name", self.canonical_tool_name))
        object.__setattr__(
            self,
            "normalized_arguments",
            require_directed_effect_immutable_items("normalized_arguments", self.normalized_arguments),
        )
        object.__setattr__(self, "arguments_hash", _require_hash("arguments_hash", self.arguments_hash))
        if self.arguments_hash != hash_directed_effect_arguments(self.normalized_arguments):
            raise ValueError("arguments_hash payload mismatch for normalized arguments")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.applicability == "read_only" and self.error_code is not None:
            raise ValueError("read_only classification cannot carry an error_code")


@dataclass(frozen=True, slots=True)
class DirectorEffectAuthorizationEvidenceV1:
    """Immutable restriction evidence for one canonical prospective mutation."""

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
    authorization_hash: str

    def __post_init__(self) -> None:
        for field_name in (
            "workspace",
            "execution_attempt_id",
            "turn_id",
            "batch_id",
            "tool_call_id",
            "normalized_tool_name",
            "role_policy_id",
            "job_token_id",
            "policy_version",
        ):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        object.__setattr__(self, "capability_scope", _require_sorted_tokens("capability_scope", self.capability_scope))
        for field_name in (
            "arguments_hash",
            "tool_spec_hash",
            "role_policy_hash",
            "canonical_allow_list_hash",
            "capability_scope_hash",
            "job_token_evidence_hash",
            "execution_envelope_hash",
            "allowed_command_hash",
            "bound_policy_snapshot_hash",
            "target_state_hash",
            "normalized_operation_hash",
            "policy_hash",
            "authorization_hash",
        ):
            object.__setattr__(self, field_name, _require_hash(field_name, getattr(self, field_name)))
        if self.mutation_guard_mode != "strict":
            raise ValueError("mutation_guard_mode must be strict")
        expected_authorization_hash = hash_director_effect_authorization_evidence(
            workspace=self.workspace,
            execution_attempt_id=self.execution_attempt_id,
            turn_id=self.turn_id,
            batch_id=self.batch_id,
            tool_call_id=self.tool_call_id,
            normalized_tool_name=self.normalized_tool_name,
            arguments_hash=self.arguments_hash,
            tool_spec_hash=self.tool_spec_hash,
            role_policy_id=self.role_policy_id,
            role_policy_hash=self.role_policy_hash,
            canonical_allow_list_hash=self.canonical_allow_list_hash,
            capability_scope=self.capability_scope,
            capability_scope_hash=self.capability_scope_hash,
            job_token_id=self.job_token_id,
            job_token_evidence_hash=self.job_token_evidence_hash,
            execution_envelope_hash=self.execution_envelope_hash,
            allowed_command_hash=self.allowed_command_hash,
            mutation_guard_mode=self.mutation_guard_mode,
            bound_policy_snapshot_hash=self.bound_policy_snapshot_hash,
            target_state_hash=self.target_state_hash,
            normalized_operation_hash=self.normalized_operation_hash,
            policy_version=self.policy_version,
            policy_hash=self.policy_hash,
        )
        if self.authorization_hash != expected_authorization_hash:
            raise ValueError("authorization_hash must bind complete authorization evidence")

    def validate_arguments_binding(self, normalized_arguments: DirectedEffectImmutableItemsV1) -> None:
        """Fail closed unless this evidence hash binds the supplied canonical payload."""
        if self.arguments_hash != hash_directed_effect_arguments(normalized_arguments):
            raise ValueError("authorization evidence arguments_hash payload mismatch")


@dataclass(frozen=True, slots=True)
class DirectorEffectPreflightRequestV1:
    """Pure Director preflight input after canonical classification."""

    classification: DirectedEffectClassificationResultV1
    authorization_evidence: DirectorEffectAuthorizationEvidenceV1

    def __post_init__(self) -> None:
        if not isinstance(self.classification, DirectedEffectClassificationResultV1):
            raise TypeError("classification must be DirectedEffectClassificationResultV1")
        if not isinstance(self.authorization_evidence, DirectorEffectAuthorizationEvidenceV1):
            raise TypeError("authorization_evidence must be DirectorEffectAuthorizationEvidenceV1")
        if self.classification.error_code is not None:
            raise ValueError("preflight requests cannot carry failed classification evidence")
        if self.classification.applicability != "mutation_capable":
            raise ValueError("preflight requests require mutation_capable classification")
        if self.classification.canonical_tool_name != self.authorization_evidence.normalized_tool_name:
            raise ValueError("classification canonical_tool_name must match authorization evidence")
        if self.classification.arguments_hash != self.authorization_evidence.arguments_hash:
            raise ValueError("classification arguments_hash must match authorization evidence")
        self.authorization_evidence.validate_arguments_binding(self.classification.normalized_arguments)


@dataclass(frozen=True, slots=True)
class DirectorEffectPreflightResultV1:
    """Typed outcome of pure Director mutation preflight."""

    status: DirectorEffectPreflightStatusV1
    applicability: DirectedEffectApplicabilityV1
    intent: DirectedEffectInventoryIntentV1 | None
    evidence: DirectorEffectAuthorizationEvidenceV1 | None
    error_code: DirectedEffectErrorCodeV1 | None

    def __post_init__(self) -> None:
        if self.status not in {"authorized", "not_applicable", "denied"}:
            raise ValueError("status must be authorized, not_applicable, or denied")
        if self.applicability not in {"mutation_capable", "read_only"}:
            raise ValueError("applicability must be mutation_capable or read_only")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.intent is not None and not isinstance(self.intent, DirectedEffectInventoryIntentV1):
            raise TypeError("intent must be DirectedEffectInventoryIntentV1")
        if self.evidence is not None and not isinstance(self.evidence, DirectorEffectAuthorizationEvidenceV1):
            raise TypeError("evidence must be DirectorEffectAuthorizationEvidenceV1")
        if self.status == "not_applicable":
            if self.applicability != "read_only" or any((self.intent, self.evidence, self.error_code)):
                raise ValueError("not_applicable is reserved for error-free read_only results")
        elif self.status == "authorized":
            if (
                self.applicability != "mutation_capable"
                or self.intent is None
                or self.evidence is None
                or self.error_code is not None
            ):
                raise ValueError("authorized requires mutation intent and authorization evidence")
        elif (
            self.applicability != "mutation_capable"
            or self.intent is not None
            or self.evidence is not None
            or self.error_code is None
        ):
            raise ValueError("denied requires a closed error_code and no executable capability")


@dataclass(frozen=True, slots=True)
class DirectorEffectExecutionValidationRequestV1:
    """Exact execution-time revalidation input for one claimed mutation."""

    actual_normalized_tool_name: str
    actual_arguments_hash: str
    current_policy_hash: str
    current_scope_hash: str
    current_job_token_evidence_hash: str
    expected_context_id: str
    authorization_evidence: DirectorEffectAuthorizationEvidenceV1
    claim_grant: DirectedEffectClaimGrantV1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "actual_normalized_tool_name",
            _require_token("actual_normalized_tool_name", self.actual_normalized_tool_name),
        )
        object.__setattr__(self, "expected_context_id", _require_token("expected_context_id", self.expected_context_id))
        for field_name in (
            "actual_arguments_hash",
            "current_policy_hash",
            "current_scope_hash",
            "current_job_token_evidence_hash",
        ):
            object.__setattr__(self, field_name, _require_hash(field_name, getattr(self, field_name)))
        if not isinstance(self.authorization_evidence, DirectorEffectAuthorizationEvidenceV1):
            raise TypeError("authorization_evidence must be DirectorEffectAuthorizationEvidenceV1")
        if not isinstance(self.claim_grant, DirectedEffectClaimGrantV1):
            raise TypeError("claim_grant must be DirectedEffectClaimGrantV1")
        validate_directed_effect_identity_binding(
            boundary_name="execution validation request",
            authorization_evidence=self.authorization_evidence,
            claim_grant=self.claim_grant,
            normalized_tool_name=self.actual_normalized_tool_name,
            arguments_hash=self.actual_arguments_hash,
        )
        if (
            self.current_policy_hash != self.authorization_evidence.policy_hash
            or self.current_scope_hash != self.authorization_evidence.capability_scope_hash
            or self.current_job_token_evidence_hash != self.authorization_evidence.job_token_evidence_hash
        ):
            raise ValueError("execution validation request policy identity mismatch")


@dataclass(frozen=True, slots=True)
class DirectorEffectExecutionValidationResultV1:
    """Closed execution-time authorization verdict without execution handles."""

    allowed: bool
    status: DirectorEffectExecutionValidationStatusV1
    error_code: DirectedEffectErrorCodeV1 | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed", require_directed_effect_bool("allowed", self.allowed))
        if self.status not in {"allowed", "denied"}:
            raise ValueError("status must be allowed or denied")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.allowed != (self.status == "allowed"):
            raise ValueError("allowed must agree with status")
        if self.allowed and self.error_code is not None:
            raise ValueError("allowed validation cannot carry an error_code")
        if not self.allowed and self.error_code is None:
            raise ValueError("denied validation requires an error_code")


def validate_director_effect_authorization_evidence(
    evidence: DirectorEffectAuthorizationEvidenceV1,
) -> DirectorEffectAuthorizationEvidenceV1:
    """Reconstruct legacy Task3 evidence to detect forged frozen instances."""
    if not isinstance(evidence, DirectorEffectAuthorizationEvidenceV1):
        raise TypeError("authorization_evidence must be DirectorEffectAuthorizationEvidenceV1")
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
    if canonical != evidence:
        raise ValueError("authorization evidence canonical reconstruction mismatch")
    return canonical


def hash_director_effect_classification_evidence(
    *,
    raw_tool_name: str,
    canonical_tool_name: str,
    effect_type: str,
    execution_mode: str,
    normalized_arguments: DirectedEffectImmutableItemsV1,
    arguments_hash: str,
    tool_spec_hash: str,
    tool_spec_snapshot_hash: str,
    alias_binding_hash: str,
) -> str:
    """Hash one mutation classification captured from a single ToolSpec snapshot."""
    return hash_directed_effect_arguments(
        (
            ("alias_binding_hash", alias_binding_hash),
            ("arguments_hash", arguments_hash),
            ("canonical_tool_name", canonical_tool_name),
            ("domain", "director_effect_classification_evidence_v1"),
            ("effect_type", effect_type),
            ("execution_mode", execution_mode),
            ("normalized_arguments", DirectedEffectImmutableMapV1(items=normalized_arguments)),
            ("raw_tool_name", raw_tool_name),
            ("tool_spec_hash", tool_spec_hash),
            ("tool_spec_snapshot_hash", tool_spec_snapshot_hash),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectorEffectClassificationEvidenceV1:
    """Frozen mutation classification tied to one captured ToolSpec view."""

    raw_tool_name: str
    canonical_tool_name: str
    effect_type: Literal["write", "async"]
    execution_mode: Literal["write_serial", "async_receipt"]
    normalized_arguments: DirectedEffectImmutableItemsV1
    arguments_hash: str
    tool_spec_hash: str
    tool_spec_snapshot_hash: str
    alias_binding_hash: str
    classification_evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in ("raw_tool_name", "canonical_tool_name"):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        expected_mode = "write_serial" if self.effect_type == "write" else "async_receipt"
        if self.execution_mode != expected_mode:
            raise ValueError("execution_mode must match mutation effect_type")
        object.__setattr__(
            self,
            "normalized_arguments",
            require_directed_effect_immutable_items("normalized_arguments", self.normalized_arguments),
        )
        for field_name in (
            "arguments_hash",
            "tool_spec_hash",
            "tool_spec_snapshot_hash",
            "alias_binding_hash",
        ):
            object.__setattr__(self, field_name, _require_hash(field_name, getattr(self, field_name)))
        if self.arguments_hash != hash_directed_effect_arguments(self.normalized_arguments):
            raise ValueError("arguments_hash payload mismatch for classification evidence")
        object.__setattr__(
            self,
            "classification_evidence_hash",
            hash_director_effect_classification_evidence(
                raw_tool_name=self.raw_tool_name,
                canonical_tool_name=self.canonical_tool_name,
                effect_type=self.effect_type,
                execution_mode=self.execution_mode,
                normalized_arguments=self.normalized_arguments,
                arguments_hash=self.arguments_hash,
                tool_spec_hash=self.tool_spec_hash,
                tool_spec_snapshot_hash=self.tool_spec_snapshot_hash,
                alias_binding_hash=self.alias_binding_hash,
            ),
        )


def validate_director_effect_classification_evidence(
    evidence: DirectorEffectClassificationEvidenceV1,
) -> DirectorEffectClassificationEvidenceV1:
    """Reconstruct Task4 classification evidence to reject object-level forgeries."""
    if not isinstance(evidence, DirectorEffectClassificationEvidenceV1):
        raise TypeError("classification_evidence must be DirectorEffectClassificationEvidenceV1")
    canonical = DirectorEffectClassificationEvidenceV1(
        raw_tool_name=evidence.raw_tool_name,
        canonical_tool_name=evidence.canonical_tool_name,
        effect_type=evidence.effect_type,
        execution_mode=evidence.execution_mode,
        normalized_arguments=evidence.normalized_arguments,
        arguments_hash=evidence.arguments_hash,
        tool_spec_hash=evidence.tool_spec_hash,
        tool_spec_snapshot_hash=evidence.tool_spec_snapshot_hash,
        alias_binding_hash=evidence.alias_binding_hash,
    )
    if canonical != evidence:
        raise ValueError("classification evidence canonical reconstruction mismatch")
    return canonical


def hash_director_effect_authorization_binding(
    *,
    authorization_hash: str,
    classification_evidence_hash: str,
    tool_spec_hash: str,
    tool_spec_snapshot_hash: str,
    alias_binding_hash: str,
) -> str:
    """Bind immutable Task3 authority to immutable Task4 classification evidence."""
    return hash_directed_effect_arguments(
        (
            ("alias_binding_hash", alias_binding_hash),
            ("authorization_hash", authorization_hash),
            ("classification_evidence_hash", classification_evidence_hash),
            ("domain", "director_effect_authorization_binding_v1"),
            ("tool_spec_hash", tool_spec_hash),
            ("tool_spec_snapshot_hash", tool_spec_snapshot_hash),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectorEffectAuthorizationBindingV1:
    """Additive Task4 wrapper; it never changes the legacy Task3 hash contract."""

    authorization_evidence: DirectorEffectAuthorizationEvidenceV1
    classification_evidence: DirectorEffectClassificationEvidenceV1
    tool_spec_hash: str
    tool_spec_snapshot_hash: str
    alias_binding_hash: str
    classification_evidence_hash: str = field(init=False)
    authorization_binding_hash: str = field(init=False)

    def __post_init__(self) -> None:
        authorization = validate_director_effect_authorization_evidence(self.authorization_evidence)
        classification = validate_director_effect_classification_evidence(self.classification_evidence)
        for field_name in ("tool_spec_hash", "tool_spec_snapshot_hash", "alias_binding_hash"):
            object.__setattr__(self, field_name, _require_hash(field_name, getattr(self, field_name)))
        if (
            authorization.normalized_tool_name != classification.canonical_tool_name
            or authorization.arguments_hash != classification.arguments_hash
            or authorization.tool_spec_hash != classification.tool_spec_hash
            or authorization.tool_spec_hash != self.tool_spec_hash
            or classification.tool_spec_snapshot_hash != self.tool_spec_snapshot_hash
            or classification.alias_binding_hash != self.alias_binding_hash
        ):
            raise ValueError("authorization binding must retain exact classification and ToolSpec identity")
        object.__setattr__(self, "classification_evidence_hash", classification.classification_evidence_hash)
        object.__setattr__(
            self,
            "authorization_binding_hash",
            hash_director_effect_authorization_binding(
                authorization_hash=authorization.authorization_hash,
                classification_evidence_hash=classification.classification_evidence_hash,
                tool_spec_hash=self.tool_spec_hash,
                tool_spec_snapshot_hash=self.tool_spec_snapshot_hash,
                alias_binding_hash=self.alias_binding_hash,
            ),
        )


def validate_director_effect_authorization_binding(
    binding: DirectorEffectAuthorizationBindingV1,
) -> DirectorEffectAuthorizationBindingV1:
    """Reconstruct the additive wrapper and its nested evidence without I/O."""
    if not isinstance(binding, DirectorEffectAuthorizationBindingV1):
        raise TypeError("authorization_binding must be DirectorEffectAuthorizationBindingV1")
    canonical = DirectorEffectAuthorizationBindingV1(
        authorization_evidence=binding.authorization_evidence,
        classification_evidence=binding.classification_evidence,
        tool_spec_hash=binding.tool_spec_hash,
        tool_spec_snapshot_hash=binding.tool_spec_snapshot_hash,
        alias_binding_hash=binding.alias_binding_hash,
    )
    if canonical != binding:
        raise ValueError("authorization binding canonical reconstruction mismatch")
    return canonical


_PUBLIC_POLICY_PROJECTION_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class DirectorEffectPublicPolicyEvidenceV1:
    """One-way baseline projection of an authorization binding, never policy truth."""

    source_authorization_binding_hash: str
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
    policy_version: str
    policy_hash: str
    classification_evidence_hash: str
    tool_spec_hash: str
    tool_spec_snapshot_hash: str
    alias_binding_hash: str
    public_policy_evidence_hash: str

    def __init__(
        self,
        *,
        source_authorization_binding_hash: str,
        role_policy_id: str,
        role_policy_hash: str,
        canonical_allow_list_hash: str,
        capability_scope: tuple[str, ...],
        capability_scope_hash: str,
        job_token_id: str,
        job_token_evidence_hash: str,
        execution_envelope_hash: str,
        allowed_command_hash: str,
        mutation_guard_mode: Literal["strict"],
        policy_version: str,
        policy_hash: str,
        classification_evidence_hash: str,
        tool_spec_hash: str,
        tool_spec_snapshot_hash: str,
        alias_binding_hash: str,
        _projection_token: object,
    ) -> None:
        if _projection_token is not _PUBLIC_POLICY_PROJECTION_TOKEN:
            raise ValueError("public policy evidence must be created by its authorization binding projection")
        for field_name, value in (
            ("source_authorization_binding_hash", source_authorization_binding_hash),
            ("role_policy_hash", role_policy_hash),
            ("canonical_allow_list_hash", canonical_allow_list_hash),
            ("capability_scope_hash", capability_scope_hash),
            ("job_token_evidence_hash", job_token_evidence_hash),
            ("execution_envelope_hash", execution_envelope_hash),
            ("allowed_command_hash", allowed_command_hash),
            ("policy_hash", policy_hash),
            ("classification_evidence_hash", classification_evidence_hash),
            ("tool_spec_hash", tool_spec_hash),
            ("tool_spec_snapshot_hash", tool_spec_snapshot_hash),
            ("alias_binding_hash", alias_binding_hash),
        ):
            object.__setattr__(self, field_name, _require_hash(field_name, value))
        for field_name, value in (
            ("role_policy_id", role_policy_id),
            ("job_token_id", job_token_id),
            ("policy_version", policy_version),
        ):
            object.__setattr__(self, field_name, _require_token(field_name, value))
        object.__setattr__(self, "capability_scope", _require_sorted_tokens("capability_scope", capability_scope))
        if mutation_guard_mode != "strict":
            raise ValueError("mutation_guard_mode must be strict")
        object.__setattr__(self, "mutation_guard_mode", mutation_guard_mode)
        object.__setattr__(
            self,
            "public_policy_evidence_hash",
            hash_director_effect_public_policy_evidence(self),
        )


def hash_director_effect_public_policy_evidence(evidence: DirectorEffectPublicPolicyEvidenceV1) -> str:
    """Hash the complete baseline-only public policy projection."""
    return hash_directed_effect_arguments(
        (
            ("alias_binding_hash", evidence.alias_binding_hash),
            ("allowed_command_hash", evidence.allowed_command_hash),
            ("canonical_allow_list_hash", evidence.canonical_allow_list_hash),
            ("capability_scope", evidence.capability_scope),
            ("capability_scope_hash", evidence.capability_scope_hash),
            ("classification_evidence_hash", evidence.classification_evidence_hash),
            ("domain", "director_effect_public_policy_evidence_v1"),
            ("execution_envelope_hash", evidence.execution_envelope_hash),
            ("job_token_evidence_hash", evidence.job_token_evidence_hash),
            ("job_token_id", evidence.job_token_id),
            ("mutation_guard_mode", evidence.mutation_guard_mode),
            ("policy_hash", evidence.policy_hash),
            ("policy_version", evidence.policy_version),
            ("role_policy_hash", evidence.role_policy_hash),
            ("role_policy_id", evidence.role_policy_id),
            ("source_authorization_binding_hash", evidence.source_authorization_binding_hash),
            ("tool_spec_hash", evidence.tool_spec_hash),
            ("tool_spec_snapshot_hash", evidence.tool_spec_snapshot_hash),
        )
    )


def project_director_effect_public_policy_evidence(
    authorization_binding: DirectorEffectAuthorizationBindingV1,
) -> DirectorEffectPublicPolicyEvidenceV1:
    """Project public policy evidence deterministically from exactly one binding."""
    binding = validate_director_effect_authorization_binding(authorization_binding)
    evidence = binding.authorization_evidence
    return DirectorEffectPublicPolicyEvidenceV1(
        source_authorization_binding_hash=binding.authorization_binding_hash,
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
        policy_version=evidence.policy_version,
        policy_hash=evidence.policy_hash,
        classification_evidence_hash=binding.classification_evidence_hash,
        tool_spec_hash=binding.tool_spec_hash,
        tool_spec_snapshot_hash=binding.tool_spec_snapshot_hash,
        alias_binding_hash=binding.alias_binding_hash,
        _projection_token=_PUBLIC_POLICY_PROJECTION_TOKEN,
    )


def validate_director_effect_public_policy_evidence(
    evidence: DirectorEffectPublicPolicyEvidenceV1,
) -> DirectorEffectPublicPolicyEvidenceV1:
    """Validate a projection hash without constructing a second policy source."""
    if not isinstance(evidence, DirectorEffectPublicPolicyEvidenceV1):
        raise TypeError("public_policy_evidence must be DirectorEffectPublicPolicyEvidenceV1")
    expected_hash = hash_director_effect_public_policy_evidence(evidence)
    if evidence.public_policy_evidence_hash != expected_hash:
        raise ValueError("public policy evidence hash mismatch")
    return evidence


DirectorEffectExecutionEvidenceComparisonStatusV1: TypeAlias = Literal["matched", "denied"]


@dataclass(frozen=True, slots=True)
class DirectorEffectExecutionEvidenceComparisonRequestV1:
    """Baseline/supplied structural comparison input; it has no freshness semantics."""

    baseline_authorization_binding: DirectorEffectAuthorizationBindingV1
    baseline_public_policy_evidence: DirectorEffectPublicPolicyEvidenceV1
    supplied_authorization_binding: DirectorEffectAuthorizationBindingV1
    supplied_public_policy_evidence: DirectorEffectPublicPolicyEvidenceV1
    supplied_bound_snapshot: DirectorEffectPolicyBoundSnapshotV1
    supplied_member: DirectedEffectInventoryMemberV1
    supplied_grant: DirectedEffectClaimGrantV1
    supplied_normalized_tool: str
    supplied_arguments_hash: str

    def __post_init__(self) -> None:
        for field_name in ("baseline_authorization_binding", "supplied_authorization_binding"):
            if not isinstance(getattr(self, field_name), DirectorEffectAuthorizationBindingV1):
                raise TypeError(f"{field_name} must be DirectorEffectAuthorizationBindingV1")
        for field_name in ("baseline_public_policy_evidence", "supplied_public_policy_evidence"):
            if not isinstance(getattr(self, field_name), DirectorEffectPublicPolicyEvidenceV1):
                raise TypeError(f"{field_name} must be DirectorEffectPublicPolicyEvidenceV1")
        if not isinstance(self.supplied_member, DirectedEffectInventoryMemberV1):
            raise TypeError("supplied_member must be DirectedEffectInventoryMemberV1")
        if not isinstance(self.supplied_grant, DirectedEffectClaimGrantV1):
            raise TypeError("supplied_grant must be DirectedEffectClaimGrantV1")
        object.__setattr__(
            self, "supplied_normalized_tool", _require_token("supplied_normalized_tool", self.supplied_normalized_tool)
        )
        object.__setattr__(
            self, "supplied_arguments_hash", _require_hash("supplied_arguments_hash", self.supplied_arguments_hash)
        )


@dataclass(frozen=True, slots=True)
class DirectorEffectExecutionEvidenceComparisonResultV1:
    """Closed structural comparison verdict, explicitly not an execution grant."""

    status: DirectorEffectExecutionEvidenceComparisonStatusV1
    matches: bool
    error_code: DirectedEffectErrorCodeV1 | None
    comparison_scope: Literal["structure_hash_identity_only"] = "structure_hash_identity_only"

    def __post_init__(self) -> None:
        object.__setattr__(self, "matches", require_directed_effect_bool("matches", self.matches))
        if self.status not in {"matched", "denied"}:
            raise ValueError("status must be matched or denied")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.matches != (self.status == "matched"):
            raise ValueError("matches must agree with status")
        if self.matches and self.error_code is not None:
            raise ValueError("matched comparison cannot carry an error_code")
        if not self.matches and self.error_code is None:
            raise ValueError("denied comparison requires an error_code")
