"""Baseline-only Task4 policy guard with no TaskRuntime or physical effects."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableMapV1,
    DirectedEffectImmutableSequenceV1,
    DirectedEffectImmutableValueV1,
    DirectorEffectAuthorizationBindingV1,
    DirectorEffectAuthorizationEvidenceV1,
    DirectorEffectClassificationEvidenceV1,
    DirectorEffectPreflightResultV1,
    DirectorEffectPublicPolicyEvidenceV1,
    create_directed_effect_inventory_intent,
    hash_directed_effect_arguments,
    hash_director_effect_authorization_evidence,
    project_director_effect_public_policy_evidence,
    require_directed_effect_immutable_items,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    DirectorEffectPolicyBaselineCaptureRequestV1,
    DirectorEffectPolicyOperationSubjectV1,
    DirectorEffectPolicySnapshotPortV1,
    DirectorEffectPolicySnapshotRequestV1,
    DirectorEffectPolicySnapshotResultV1,
    hash_director_effect_policy_operation_subject,
    validate_director_effect_policy_snapshot_result,
)
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolEffectType,
    ToolInvocation,
    tool_classification_matches_snapshot,
)
from polaris.cells.runtime.task_runtime.public import (
    DirectedEffectParentRegistryIdentityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.tool_execution.contracts import CapturedToolSpecSnapshotV1

DirectedEffectPolicyGuardStatusV1 = Literal["authorized", "not_applicable", "denied"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DirectedEffectPolicyGuardRequestV1:
    """One already-classified invocation and its baseline-only policy inputs."""

    invocation: ToolInvocation
    workspace: str
    inventory_ordinal: int
    authorization_evidence: DirectorEffectAuthorizationEvidenceV1 | None = None
    snapshot_request: DirectorEffectPolicySnapshotRequestV1 | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.invocation, ToolInvocation):
            raise TypeError("invocation must be ToolInvocation")
        if not isinstance(self.workspace, str) or not self.workspace.strip():
            raise ValueError("workspace must be a non-empty string")
        if (
            isinstance(self.inventory_ordinal, bool)
            or not isinstance(self.inventory_ordinal, int)
            or self.inventory_ordinal < 0
        ):
            raise ValueError("inventory_ordinal must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DirectedEffectAuthoritativePolicyGuardRequestV1:
    """Production request whose policy and target evidence come from owners."""

    invocation: ToolInvocation
    workspace: str
    inventory_ordinal: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    turn_id: str
    batch_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.invocation, ToolInvocation):
            raise TypeError("invocation must be ToolInvocation")
        for name in ("workspace", "turn_id", "batch_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be canonical")
        if (
            isinstance(self.inventory_ordinal, bool)
            or not isinstance(self.inventory_ordinal, int)
            or self.inventory_ordinal < 0
        ):
            raise ValueError("inventory_ordinal must be a non-negative integer")
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exact")
        canonical = TaskRuntimeExecutionAttemptIdentityV1.from_record(self.execution_attempt.to_record())
        if canonical != self.execution_attempt or canonical.workspace != self.workspace:
            raise ValueError("execution_attempt workspace identity mismatch")


@dataclass(frozen=True, slots=True)
class DirectedEffectPolicyGuardResultV1:
    """Typed no-effect guard verdict with baseline evidence only on success."""

    status: DirectedEffectPolicyGuardStatusV1
    error_code: str | None
    preflight: DirectorEffectPreflightResultV1 | None
    snapshot: DirectorEffectPolicySnapshotResultV1 | None
    authorization_binding: DirectorEffectAuthorizationBindingV1 | None
    public_policy_evidence: DirectorEffectPublicPolicyEvidenceV1 | None
    current_job_token_restriction_evidence: DirectedEffectImmutableItemsV1 | None = None

    def __post_init__(self) -> None:
        if self.status not in {"authorized", "not_applicable", "denied"}:
            raise ValueError("status must be authorized, not_applicable, or denied")
        if self.status == "not_applicable":
            if any(
                (
                    self.error_code,
                    self.snapshot,
                    self.authorization_binding,
                    self.public_policy_evidence,
                    self.current_job_token_restriction_evidence,
                )
            ):
                raise ValueError("not_applicable retains no mutation evidence")
        elif self.status == "denied":
            if self.error_code is None or any(
                (
                    self.snapshot,
                    self.authorization_binding,
                    self.public_policy_evidence,
                    self.current_job_token_restriction_evidence,
                )
            ):
                raise ValueError("denied retains no mutation evidence")
        elif (
            self.error_code is not None
            or self.preflight is None
            or self.snapshot is None
            or self.authorization_binding is None
            or self.public_policy_evidence is None
            or self.current_job_token_restriction_evidence is None
        ):
            raise ValueError("authorized requires complete baseline evidence")
        if self.current_job_token_restriction_evidence is not None:
            require_directed_effect_immutable_items(
                "current_job_token_restriction_evidence",
                self.current_job_token_restriction_evidence,
            )


def _freeze_value(value: object) -> DirectedEffectImmutableValueV1:
    if value is None or type(value) in {bool, int, float, str}:
        return value  # type: ignore[return-value]
    if isinstance(value, Mapping):
        return DirectedEffectImmutableMapV1(items=tuple((str(key), _freeze_value(item)) for key, item in value.items()))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return DirectedEffectImmutableSequenceV1(items=tuple(_freeze_value(item) for item in value))
    raise TypeError("normalized arguments must contain only immutable DEO values")


def _immutable_arguments(arguments: Mapping[str, object]) -> tuple[tuple[str, DirectedEffectImmutableValueV1], ...]:
    return require_directed_effect_immutable_items(
        "normalized_arguments",
        tuple((str(key), _freeze_value(value)) for key, value in arguments.items()),
    )


def _denied(error_code: str) -> DirectedEffectPolicyGuardResultV1:
    return DirectedEffectPolicyGuardResultV1(
        status="denied",
        error_code=error_code,
        preflight=None,
        snapshot=None,
        authorization_binding=None,
        public_policy_evidence=None,
        current_job_token_restriction_evidence=None,
    )


def _gateway_denial_code(message: str) -> str:
    lowered = message.lower()
    if "白名单" in message or "blacklist" in lowered or "allow-list" in lowered or "allowlist" in lowered:
        return "deo_tool_not_allowed"
    if "路径" in message or "scope" in lowered:
        return "deo_path_scope_denied"
    if "命令" in message or "command" in lowered:
        return "deo_command_scope_denied"
    if "严格" in message or "strict" in lowered:
        return "deo_mutation_guard_denied"
    if "token" in lowered:
        return "deo_job_token_invalid"
    return "deo_director_policy_denied"


class DirectedEffectPolicyGuard:
    """Consumes caller-captured classification evidence without registry rereads."""

    def __init__(self, gateway: RoleToolGateway, policy_port: DirectorEffectPolicySnapshotPortV1) -> None:
        self._gateway = gateway
        self._policy_port = policy_port

    async def evaluate_authoritative(
        self,
        request: DirectedEffectAuthoritativePolicyGuardRequestV1,
    ) -> DirectedEffectPolicyGuardResultV1:
        """Build baseline authorization only from gateway and adapter owners."""

        invocation = request.invocation
        classification = invocation.classification
        if classification is None or not tool_classification_matches_snapshot(classification):
            return _denied("deo_tool_normalization_failed")
        snapshot = classification.snapshot
        if (
            not isinstance(snapshot, CapturedToolSpecSnapshotV1)
            or snapshot.raw_tool_name != invocation.raw_tool_name
            or snapshot.canonical_tool_name != classification.canonical_tool_name
            or invocation.tool_name != classification.canonical_tool_name
        ):
            return _denied("deo_tool_normalization_failed")
        if classification.effect_type is ToolEffectType.READ:
            return DirectedEffectPolicyGuardResultV1(
                status="not_applicable",
                error_code=None,
                preflight=None,
                snapshot=None,
                authorization_binding=None,
                public_policy_evidence=None,
                current_job_token_restriction_evidence=None,
            )
        if classification.error_code is not None or not classification.registered:
            return _denied("deo_tool_normalization_failed")
        try:
            canonical_snapshot = CapturedToolSpecSnapshotV1(
                raw_tool_name=snapshot.raw_tool_name,
                canonical_tool_name=snapshot.canonical_tool_name,
                registered=snapshot.registered,
                canonical_effective_spec=snapshot.canonical_effective_spec,
                canonical_name_view=snapshot.canonical_name_view,
                alias_binding_view=snapshot.alias_binding_view,
            )
            if canonical_snapshot != snapshot:
                raise ValueError("ToolSpec snapshot drift")
            normalized_arguments = _immutable_arguments(invocation.arguments)
            effect_type: Literal["write", "async"] = (
                "write" if classification.effect_type is ToolEffectType.WRITE else "async"
            )
            execution_mode: Literal["write_serial", "async_receipt"] = (
                "write_serial" if effect_type == "write" else "async_receipt"
            )
            classification_evidence = DirectorEffectClassificationEvidenceV1(
                raw_tool_name=invocation.raw_tool_name or invocation.tool_name,
                canonical_tool_name=classification.canonical_tool_name,
                effect_type=effect_type,
                execution_mode=execution_mode,
                normalized_arguments=normalized_arguments,
                arguments_hash=hash_directed_effect_arguments(normalized_arguments),
                tool_spec_hash=canonical_snapshot.tool_spec_hash,
                tool_spec_snapshot_hash=canonical_snapshot.snapshot_hash,
                alias_binding_hash=canonical_snapshot.alias_binding_hash,
            )
            allowed, refusal = self._gateway.check_tool_permission_from_snapshot(
                raw_tool_name=invocation.raw_tool_name or invocation.tool_name,
                canonical_tool_name=classification.canonical_tool_name,
                normalized_tool_args=invocation.arguments,
                tool_snapshot=snapshot,
            )
            if not allowed:
                return _denied(_gateway_denial_code(refusal))
            policy_inputs = self._gateway.capture_directed_effect_policy_inputs()
            provisional_subject = DirectorEffectPolicyOperationSubjectV1(
                workspace=request.workspace,
                turn_id=request.turn_id,
                batch_id=request.batch_id,
                tool_call_id=invocation.call_id,
                inventory_ordinal=request.inventory_ordinal,
                normalized_tool_name=classification_evidence.canonical_tool_name,
                normalized_arguments=normalized_arguments,
                effect_type=effect_type,
                execution_mode=execution_mode,
                prospective_operation_hash="0" * 64,
            )
            subject = replace(
                provisional_subject,
                prospective_operation_hash=hash_director_effect_policy_operation_subject(provisional_subject),
            )
            args = dict(invocation.arguments)
            canonical_command = str(args.get("command") or "")
            snapshot_result = validate_director_effect_policy_snapshot_result(
                await self._policy_port.capture_baseline_snapshot(
                    DirectorEffectPolicyBaselineCaptureRequestV1(
                        subject=subject,
                        workspace=request.workspace,
                        normalized_tool_name=classification_evidence.canonical_tool_name,
                        normalized_arguments=normalized_arguments,
                        job_token_restriction_evidence=policy_inputs.job_token_restriction_evidence,
                        expected_policy_version=policy_inputs.policy_version,
                        canonical_command=canonical_command,
                        path_scope_evidence=(("allowed_paths", policy_inputs.capability_scope),),
                        command_scope_evidence=(
                            (
                                "allowed_commands",
                                dict(policy_inputs.job_token_restriction_evidence)["allowed_commands"],
                            ),
                        ),
                    )
                )
            )
            if not snapshot_result.allowed:
                return _denied(snapshot_result.error_code or "deo_director_policy_denied")
            attempt_id = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
                request.execution_attempt
            ).execution_attempt_id
            authorization_hash = hash_director_effect_authorization_evidence(
                workspace=request.workspace,
                execution_attempt_id=attempt_id,
                turn_id=request.turn_id,
                batch_id=request.batch_id,
                tool_call_id=invocation.call_id,
                normalized_tool_name=classification_evidence.canonical_tool_name,
                arguments_hash=classification_evidence.arguments_hash,
                tool_spec_hash=classification_evidence.tool_spec_hash,
                role_policy_id=policy_inputs.role_policy_id,
                role_policy_hash=policy_inputs.role_policy_hash,
                canonical_allow_list_hash=policy_inputs.canonical_allow_list_hash,
                capability_scope=policy_inputs.capability_scope,
                capability_scope_hash=policy_inputs.capability_scope_hash,
                job_token_id=policy_inputs.job_token_id,
                job_token_evidence_hash=policy_inputs.job_token_evidence_hash,
                execution_envelope_hash=policy_inputs.execution_envelope_hash,
                allowed_command_hash=policy_inputs.allowed_command_hash,
                mutation_guard_mode="strict",
                bound_policy_snapshot_hash=snapshot_result.evidence_hash,
                target_state_hash=snapshot_result.target_state_hash,
                normalized_operation_hash=snapshot_result.normalized_operation_hash,
                policy_version=snapshot_result.policy_version,
                policy_hash=snapshot_result.policy_hash,
            )
            authorization = DirectorEffectAuthorizationEvidenceV1(
                workspace=request.workspace,
                execution_attempt_id=attempt_id,
                turn_id=request.turn_id,
                batch_id=request.batch_id,
                tool_call_id=invocation.call_id,
                normalized_tool_name=classification_evidence.canonical_tool_name,
                arguments_hash=classification_evidence.arguments_hash,
                tool_spec_hash=classification_evidence.tool_spec_hash,
                role_policy_id=policy_inputs.role_policy_id,
                role_policy_hash=policy_inputs.role_policy_hash,
                canonical_allow_list_hash=policy_inputs.canonical_allow_list_hash,
                capability_scope=policy_inputs.capability_scope,
                capability_scope_hash=policy_inputs.capability_scope_hash,
                job_token_id=policy_inputs.job_token_id,
                job_token_evidence_hash=policy_inputs.job_token_evidence_hash,
                execution_envelope_hash=policy_inputs.execution_envelope_hash,
                allowed_command_hash=policy_inputs.allowed_command_hash,
                mutation_guard_mode="strict",
                bound_policy_snapshot_hash=snapshot_result.evidence_hash,
                target_state_hash=snapshot_result.target_state_hash,
                normalized_operation_hash=snapshot_result.normalized_operation_hash,
                policy_version=snapshot_result.policy_version,
                policy_hash=snapshot_result.policy_hash,
                authorization_hash=authorization_hash,
            )
            binding = DirectorEffectAuthorizationBindingV1(
                authorization_evidence=authorization,
                classification_evidence=classification_evidence,
                tool_spec_hash=canonical_snapshot.tool_spec_hash,
                tool_spec_snapshot_hash=canonical_snapshot.snapshot_hash,
                alias_binding_hash=canonical_snapshot.alias_binding_hash,
            )
            public_policy = project_director_effect_public_policy_evidence(binding)
            preflight = DirectorEffectPreflightResultV1(
                status="authorized",
                applicability="mutation_capable",
                intent=create_directed_effect_inventory_intent(
                    ordinal=subject.inventory_ordinal,
                    tool_call_id=subject.tool_call_id,
                    normalized_tool_name=subject.normalized_tool_name,
                    effect_type=subject.effect_type,
                    execution_mode=subject.execution_mode,
                    prospective_operation_hash=subject.prospective_operation_hash,
                ),
                evidence=authorization,
                error_code=None,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.debug("authoritative directed-effect guard denied malformed evidence: %s", exc)
            return _denied("deo_authorization_hash_drift")
        return DirectedEffectPolicyGuardResultV1(
            status="authorized",
            error_code=None,
            preflight=preflight,
            snapshot=snapshot_result,
            authorization_binding=binding,
            public_policy_evidence=public_policy,
            current_job_token_restriction_evidence=policy_inputs.job_token_restriction_evidence,
        )

    async def evaluate(self, request: DirectedEffectPolicyGuardRequestV1) -> DirectedEffectPolicyGuardResultV1:
        """Return baseline authorization evidence or a closed no-effect denial."""
        invocation = request.invocation
        classification = invocation.classification
        if classification is None:
            return _denied("deo_tool_normalization_failed")
        snapshot = classification.snapshot
        if (
            not tool_classification_matches_snapshot(classification)
            or not isinstance(snapshot, CapturedToolSpecSnapshotV1)
            or snapshot.raw_tool_name != invocation.raw_tool_name
            or snapshot.canonical_tool_name != classification.canonical_tool_name
            or invocation.tool_name != classification.canonical_tool_name
        ):
            return _denied("deo_tool_normalization_failed")
        if classification.effect_type is ToolEffectType.READ:
            return DirectedEffectPolicyGuardResultV1(
                status="not_applicable",
                error_code=None,
                preflight=None,
                snapshot=None,
                authorization_binding=None,
                public_policy_evidence=None,
                current_job_token_restriction_evidence=None,
            )
        if (
            classification.error_code is not None
            or not classification.registered
            or not isinstance(snapshot, CapturedToolSpecSnapshotV1)
            or snapshot.raw_tool_name != invocation.raw_tool_name
            or snapshot.canonical_tool_name != classification.canonical_tool_name
            or invocation.tool_name != classification.canonical_tool_name
        ):
            return _denied("deo_tool_normalization_failed")
        try:
            canonical_snapshot = CapturedToolSpecSnapshotV1(
                raw_tool_name=snapshot.raw_tool_name,
                canonical_tool_name=snapshot.canonical_tool_name,
                registered=snapshot.registered,
                canonical_effective_spec=snapshot.canonical_effective_spec,
                canonical_name_view=snapshot.canonical_name_view,
                alias_binding_view=snapshot.alias_binding_view,
            )
            normalized_arguments = _immutable_arguments(invocation.arguments)
            effect_type: Literal["write", "async"] = (
                "write" if classification.effect_type is ToolEffectType.WRITE else "async"
            )
            execution_mode: Literal["write_serial", "async_receipt"] = (
                "write_serial" if effect_type == "write" else "async_receipt"
            )
            classification_evidence = DirectorEffectClassificationEvidenceV1(
                raw_tool_name=invocation.raw_tool_name or invocation.tool_name,
                canonical_tool_name=classification.canonical_tool_name,
                effect_type=effect_type,
                execution_mode=execution_mode,
                normalized_arguments=normalized_arguments,
                arguments_hash=hash_directed_effect_arguments(normalized_arguments),
                tool_spec_hash=canonical_snapshot.tool_spec_hash,
                tool_spec_snapshot_hash=canonical_snapshot.snapshot_hash,
                alias_binding_hash=canonical_snapshot.alias_binding_hash,
            )
        except (AttributeError, TypeError, ValueError):
            return _denied("deo_tool_normalization_failed")
        if canonical_snapshot != snapshot:
            return _denied("deo_tool_normalization_failed")
        authorization = request.authorization_evidence
        if authorization is not None and (
            authorization.normalized_tool_name != classification_evidence.canonical_tool_name
            or authorization.arguments_hash != classification_evidence.arguments_hash
            or authorization.tool_spec_hash != canonical_snapshot.tool_spec_hash
        ):
            return _denied("deo_authorization_hash_drift")
        allowed, refusal = self._gateway.check_tool_permission_from_snapshot(
            raw_tool_name=invocation.raw_tool_name or invocation.tool_name,
            canonical_tool_name=classification.canonical_tool_name,
            normalized_tool_args=invocation.arguments,
            tool_snapshot=snapshot,
        )
        if not allowed:
            return _denied(_gateway_denial_code(refusal))
        snapshot_request = request.snapshot_request
        if authorization is None or snapshot_request is None:
            return _denied("deo_authorization_hash_drift")
        subject = snapshot_request.subject
        if (
            snapshot_request.workspace != request.workspace
            or subject.workspace != request.workspace
            or subject.inventory_ordinal != request.inventory_ordinal
            or subject.tool_call_id != invocation.call_id
            or subject.normalized_tool_name != classification_evidence.canonical_tool_name
            or subject.normalized_arguments != normalized_arguments
        ):
            return _denied("deo_authorization_hash_drift")
        snapshot_result = await self._policy_port.snapshot(snapshot_request)
        if not snapshot_result.allowed:
            return _denied(snapshot_result.error_code or "deo_director_policy_denied")
        try:
            if (
                authorization.bound_policy_snapshot_hash != snapshot_result.evidence_hash
                or authorization.policy_hash != snapshot_result.policy_hash
                or authorization.target_state_hash != snapshot_result.target_state_hash
                or authorization.normalized_operation_hash != snapshot_result.normalized_operation_hash
            ):
                return _denied("deo_authorization_hash_drift")
            binding = DirectorEffectAuthorizationBindingV1(
                authorization_evidence=authorization,
                classification_evidence=classification_evidence,
                tool_spec_hash=canonical_snapshot.tool_spec_hash,
                tool_spec_snapshot_hash=canonical_snapshot.snapshot_hash,
                alias_binding_hash=canonical_snapshot.alias_binding_hash,
            )
            public_policy = project_director_effect_public_policy_evidence(binding)
        except (AttributeError, TypeError, ValueError):
            return _denied("deo_authorization_binding_drift")
        preflight = DirectorEffectPreflightResultV1(
            status="authorized",
            applicability="mutation_capable",
            intent=create_directed_effect_inventory_intent(
                ordinal=snapshot_result.subject.inventory_ordinal,
                tool_call_id=snapshot_result.subject.tool_call_id,
                normalized_tool_name=snapshot_result.subject.normalized_tool_name,
                effect_type=snapshot_result.subject.effect_type,
                execution_mode=snapshot_result.subject.execution_mode,
                prospective_operation_hash=snapshot_result.subject.prospective_operation_hash,
            ),
            evidence=authorization,
            error_code=None,
        )
        return DirectedEffectPolicyGuardResultV1(
            status="authorized",
            error_code=None,
            preflight=preflight,
            snapshot=snapshot_result,
            authorization_binding=binding,
            public_policy_evidence=public_policy,
            current_job_token_restriction_evidence=snapshot_request.job_token_restriction_evidence,
        )
