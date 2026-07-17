"""Baseline-only Task4 policy guard with no TaskRuntime or physical effects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from polaris.cells.director.runtime.public.directed_effect_contracts import (
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
    project_director_effect_public_policy_evidence,
    require_directed_effect_immutable_items,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    DirectorEffectPolicySnapshotPortV1,
    DirectorEffectPolicySnapshotRequestV1,
    DirectorEffectPolicySnapshotResultV1,
)
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolEffectType,
    ToolInvocation,
    tool_classification_matches_snapshot,
)
from polaris.kernelone.tool_execution.contracts import CapturedToolSpecSnapshotV1

DirectedEffectPolicyGuardStatusV1 = Literal["authorized", "not_applicable", "denied"]


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
class DirectedEffectPolicyGuardResultV1:
    """Typed no-effect guard verdict with baseline evidence only on success."""

    status: DirectedEffectPolicyGuardStatusV1
    error_code: str | None
    preflight: DirectorEffectPreflightResultV1 | None
    snapshot: DirectorEffectPolicySnapshotResultV1 | None
    authorization_binding: DirectorEffectAuthorizationBindingV1 | None
    public_policy_evidence: DirectorEffectPublicPolicyEvidenceV1 | None

    def __post_init__(self) -> None:
        if self.status not in {"authorized", "not_applicable", "denied"}:
            raise ValueError("status must be authorized, not_applicable, or denied")
        if self.status == "not_applicable":
            if any((self.error_code, self.snapshot, self.authorization_binding, self.public_policy_evidence)):
                raise ValueError("not_applicable retains no mutation evidence")
        elif self.status == "denied":
            if self.error_code is None or any((self.snapshot, self.authorization_binding, self.public_policy_evidence)):
                raise ValueError("denied retains no mutation evidence")
        elif (
            self.error_code is not None
            or self.preflight is None
            or self.snapshot is None
            or self.authorization_binding is None
            or self.public_policy_evidence is None
        ):
            raise ValueError("authorized requires complete baseline evidence")


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
        )
