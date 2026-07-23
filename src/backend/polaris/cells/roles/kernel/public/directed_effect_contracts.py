"""Immutable DEO-2B contracts exposed by the roles.kernel cell."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, SupportsIndex, TypeAlias, runtime_checkable

from polaris.cells.director.runtime.public import (
    DirectorRepairEffectPlanV1,
    DirectorRepairEffectV1,
    validate_director_repair_effect_plan,
)
from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectedEffectErrorCodeV1,
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableValueV1,
    DirectorEffectAuthorizationEvidenceV1,
    hash_directed_effect_arguments,
    require_directed_effect_bool,
    require_directed_effect_hash,
    require_directed_effect_immutable_items,
    validate_directed_effect_error_code,
    validate_directed_effect_identity_binding,
    validate_director_effect_authorization_evidence,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    DirectorEffectCurrentPolicyEvidenceV1,
    DirectorEffectPolicyBoundSnapshotV1,
    DirectorEffectPolicyMemberBindingResultV1,
    DirectorEffectPolicySnapshotPortV1,
    validate_director_effect_current_policy_evidence,
    validate_director_effect_policy_bound_snapshot,
    validate_director_effect_policy_member_binding_result,
)
from polaris.cells.roles.kernel.public.turn_contracts import ToolInvocation
from polaris.cells.runtime.task_runtime.public import (
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectInventoryProjectionV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)

DirectedEffectLifecycleStatusV1: TypeAlias = Literal["ready", "not_applicable", "denied"]
DirectedEffectAttemptValidationStatusV1: TypeAlias = Literal["valid", "denied"]
DirectedEffectAttemptHeartbeatStatusV1: TypeAlias = Literal["fresh", "denied"]
DirectedEffectContextClaimStatusV1: TypeAlias = Literal["claimed", "denied"]
DirectedEffectOperationClaimStatusV1: TypeAlias = Literal["not_claimed", "claimed", "unknown"]
DirectedEffectFenceRegistrationStatusV1: TypeAlias = Literal["registered", "denied"]
DirectedEffectFenceConsumeStatusV1: TypeAlias = Literal["consumed", "denied"]
DirectedEffectFenceReleaseStatusV1: TypeAlias = Literal["released", "absent", "denied"]
DirectedEffectMutationStatusV1: TypeAlias = Literal["executed", "denied", "failed"]
DirectedEffectToolResultValueV1: TypeAlias = DirectedEffectImmutableValueV1


def _require_token(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_error_code(value: DirectedEffectErrorCodeV1 | None) -> DirectedEffectErrorCodeV1 | None:
    return validate_directed_effect_error_code(value)


def _canonical_json_payload(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    def _reject_constant(token: str) -> None:
        raise ValueError(f"{name} contains invalid JSON constant {token}")

    try:
        payload = json.loads(value, parse_constant=_reject_constant)
        canonical = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} must contain canonical JSON") from exc
    if value != canonical:
        raise ValueError(f"{name} must be canonical JSON")
    return canonical


def _canonical_workspace(value: str) -> str:
    workspace = _require_token("workspace", value)
    if not workspace.startswith("/") or posixpath.normpath(workspace) != workspace:
        raise ValueError("workspace must be an absolute canonical POSIX path")
    return workspace


def _canonical_relative_paths(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be an immutable tuple")
    normalized: list[str] = []
    for value in values:
        path = _require_token(name, value).replace("\\", "/")
        if path.startswith("/") or posixpath.normpath(path) != path or path in {".", ".."}:
            raise ValueError(f"{name} must contain canonical workspace-relative paths")
        normalized.append(path)
    result = tuple(normalized)
    if result != tuple(sorted(result)) or len(result) != len(set(result)):
        raise ValueError(f"{name} must be sorted and unique")
    return result


def _canonical_relative_directory(name: str, value: str) -> str:
    path = _require_token(name, value).replace("\\", "/")
    if path == ".":
        return path
    if path.startswith("/") or posixpath.normpath(path) != path or path == ".." or path.startswith("../"):
        raise ValueError(f"{name} must be a canonical workspace-relative directory")
    return path


@dataclass(frozen=True, slots=True)
class DeferredDirectorRepairRequestV1:
    """Immutable adapter-to-kernel request for one revalidated repair round."""

    request_id: str
    workspace: str
    task_id: str
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    plan: DirectorRepairEffectPlanV1
    planning_payload_json: str
    allowed_paths: tuple[str, ...]
    request_hash: str = field(init=False)
    schema_version: str = "roles.kernel.deferred_director_repair_request.v1"

    def __post_init__(self) -> None:
        request_id = _require_token("request_id", self.request_id)
        workspace = _canonical_workspace(self.workspace)
        task_id = _require_token("task_id", self.task_id)
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
        canonical_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(self.execution_attempt.to_record())
        if canonical_attempt != self.execution_attempt:
            raise ValueError("execution_attempt must be canonical")
        if canonical_attempt.workspace != workspace:
            raise ValueError("workspace must match execution_attempt workspace")
        if canonical_attempt.external_task_id != task_id:
            raise ValueError("task_id must match execution_attempt external_task_id")
        plan = validate_director_repair_effect_plan(self.plan)
        planning_payload_json = _canonical_json_payload("planning_payload_json", self.planning_payload_json)
        allowed_paths = _canonical_relative_paths("allowed_paths", self.allowed_paths)
        schema_version = _require_token("schema_version", self.schema_version)
        forward_paths = tuple(
            sorted({effect.target_path for effect in plan.effects if effect.contingency_kind == "forward"})
        )
        if not set(forward_paths).issubset(allowed_paths):
            raise ValueError("allowed_paths must cover every forward repair target")
        attempt_record_json = json.dumps(
            canonical_attempt.to_record(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        request_hash = hash_directed_effect_arguments(
            (
                ("allowed_paths", allowed_paths),
                ("attempt_record_json", attempt_record_json),
                ("domain", "roles_kernel_deferred_director_repair_request_v1"),
                ("plan_hash", plan.plan_hash),
                ("planning_payload_json", planning_payload_json),
                ("request_id", request_id),
                ("schema_version", schema_version),
                ("task_id", task_id),
                ("workspace", workspace),
            )
        )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "execution_attempt", canonical_attempt)
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "planning_payload_json", planning_payload_json)
        object.__setattr__(self, "allowed_paths", allowed_paths)
        object.__setattr__(self, "request_hash", request_hash)
        object.__setattr__(self, "schema_version", schema_version)


@dataclass(frozen=True, slots=True)
class DeferredDirectorCommandRequestV1:
    """Immutable adapter-to-kernel request for one governed command effect.

    Adapter quality helpers may discover that a verifier or environment
    preparation command is required, but they cannot execute it.  This value
    binds that command to the exact TaskRuntime attempt; roles.kernel later
    admits it as a visible ``execute_command`` follow-up ToolBatch.
    """

    request_id: str
    workspace: str
    task_id: str
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    command: str
    cwd: str = "."
    timeout_seconds: int = 60
    purpose: str = "verification"
    request_hash: str = field(init=False)
    schema_version: str = "roles.kernel.deferred_director_command_request.v1"

    def __post_init__(self) -> None:
        request_id = _require_token("request_id", self.request_id)
        workspace = _canonical_workspace(self.workspace)
        task_id = _require_token("task_id", self.task_id)
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
        canonical_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(self.execution_attempt.to_record())
        if canonical_attempt != self.execution_attempt:
            raise ValueError("execution_attempt must be canonical")
        if canonical_attempt.workspace != workspace:
            raise ValueError("workspace must match execution_attempt workspace")
        if canonical_attempt.external_task_id != task_id:
            raise ValueError("task_id must match execution_attempt.external_task_id")
        command = _require_token("command", self.command)
        if "\x00" in command or "\r" in command or "\n" in command:
            raise ValueError("command must be one canonical command line")
        cwd = _canonical_relative_directory("cwd", self.cwd)
        timeout_seconds = _require_positive_int("timeout_seconds", self.timeout_seconds)
        if timeout_seconds > 300:
            raise ValueError("timeout_seconds must not exceed 300")
        purpose = _require_token("purpose", self.purpose)
        schema_version = _require_token("schema_version", self.schema_version)
        if schema_version != "roles.kernel.deferred_director_command_request.v1":
            raise ValueError("schema_version must be roles.kernel.deferred_director_command_request.v1")
        attempt_record_json = json.dumps(
            canonical_attempt.to_record(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        request_hash = hash_directed_effect_arguments(
            (
                ("attempt_record_json", attempt_record_json),
                ("command", command),
                ("cwd", cwd),
                ("domain", "roles_kernel_deferred_director_command_request_v1"),
                ("purpose", purpose),
                ("request_id", request_id),
                ("schema_version", schema_version),
                ("task_id", task_id),
                ("timeout_seconds", timeout_seconds),
                ("workspace", workspace),
            )
        )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "execution_attempt", canonical_attempt)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "timeout_seconds", timeout_seconds)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "request_hash", request_hash)
        object.__setattr__(self, "schema_version", schema_version)


@dataclass(frozen=True, slots=True)
class DeferredDirectorRepairEffectBindingV1:
    """Hash-bound repair state contract sealed through the synthetic call id."""

    request_id: str
    request_hash: str
    plan_hash: str
    effect: DirectorRepairEffectV1
    binding_hash: str = field(init=False)
    tool_call_id: str = field(init=False)
    schema_version: str = "roles.kernel.deferred_director_repair_effect_binding.v1"

    def __post_init__(self) -> None:
        request_id = _require_token("request_id", self.request_id)
        request_hash = require_directed_effect_hash("request_hash", self.request_hash)
        plan_hash = require_directed_effect_hash("plan_hash", self.plan_hash)
        if type(self.effect) is not DirectorRepairEffectV1:
            raise TypeError("effect must be exactly DirectorRepairEffectV1")
        effect = DirectorRepairEffectV1(
            call_id=self.effect.call_id,
            operation_id=self.effect.operation_id,
            tool_name=self.effect.tool_name,
            arguments=self.effect.arguments,
            contingency_kind=self.effect.contingency_kind,
            target_path=self.effect.target_path,
            expected_before_hash=self.effect.expected_before_hash,
            expected_after_hash=self.effect.expected_after_hash,
            exists_before=self.effect.exists_before,
            exists_after=self.effect.exists_after,
            activates_after_call_id=self.effect.activates_after_call_id,
            schema_version=self.effect.schema_version,
        )
        if effect != self.effect:
            raise ValueError("effect must be canonical")
        schema_version = _require_token("schema_version", self.schema_version)
        if schema_version != "roles.kernel.deferred_director_repair_effect_binding.v1":
            raise ValueError("schema_version must be roles.kernel.deferred_director_repair_effect_binding.v1")
        binding_hash = hash_directed_effect_arguments(
            (
                ("domain", "roles_kernel_deferred_director_repair_effect_binding_v1"),
                ("effect", effect.immutable_identity()),
                ("plan_hash", plan_hash),
                ("request_hash", request_hash),
                ("request_id", request_id),
                ("schema_version", schema_version),
            )
        )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "request_hash", request_hash)
        object.__setattr__(self, "plan_hash", plan_hash)
        object.__setattr__(self, "effect", effect)
        object.__setattr__(self, "binding_hash", binding_hash)
        object.__setattr__(self, "tool_call_id", f"deferred-repair-{binding_hash[:24]}")
        object.__setattr__(self, "schema_version", schema_version)


@dataclass(frozen=True, slots=True)
class DeferredDirectorRepairSynthesisResultV1:
    """Pure kernel synthesis result; contains invocations but no execution authority."""

    ok: bool
    request_id: str
    request_hash: str
    plan_hash: str
    forward_invocations: tuple[ToolInvocation, ...] = ()
    rollback_invocations: tuple[ToolInvocation, ...] = ()
    rollback_activation_by_call_id: tuple[tuple[str, str], ...] = ()
    effect_bindings_by_call_id: tuple[tuple[str, DeferredDirectorRepairEffectBindingV1], ...] = ()
    error_code: str | None = None
    schema_version: str = "roles.kernel.deferred_director_repair_synthesis_result.v1"

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise TypeError("ok must be bool")
        object.__setattr__(self, "request_id", _require_token("request_id", self.request_id))
        object.__setattr__(self, "request_hash", require_directed_effect_hash("request_hash", self.request_hash))
        object.__setattr__(self, "plan_hash", require_directed_effect_hash("plan_hash", self.plan_hash))
        if not isinstance(self.forward_invocations, tuple) or not isinstance(self.rollback_invocations, tuple):
            raise TypeError("synthesis invocations must be immutable tuples")
        if not all(type(item) is ToolInvocation for item in self.forward_invocations + self.rollback_invocations):
            raise TypeError("synthesis invocations must contain exact ToolInvocation values")
        if not isinstance(self.rollback_activation_by_call_id, tuple):
            raise TypeError("rollback_activation_by_call_id must be an immutable tuple")
        activation_pairs = tuple(
            (_require_token("rollback_call_id", rollback_id), _require_token("forward_call_id", forward_id))
            for rollback_id, forward_id in self.rollback_activation_by_call_id
        )
        if len(dict(activation_pairs)) != len(activation_pairs):
            raise ValueError("rollback activation call ids must be unique")
        forward_ids = {str(invocation.call_id) for invocation in self.forward_invocations}
        rollback_ids = {str(invocation.call_id) for invocation in self.rollback_invocations}
        if {rollback_id for rollback_id, _ in activation_pairs} != rollback_ids:
            raise ValueError("rollback activation map must cover exact rollback invocations")
        if any(forward_id not in forward_ids for _, forward_id in activation_pairs):
            raise ValueError("rollback activation map must reference forward invocations")
        if not isinstance(self.effect_bindings_by_call_id, tuple):
            raise TypeError("effect_bindings_by_call_id must be an immutable tuple")
        binding_pairs: tuple[tuple[str, DeferredDirectorRepairEffectBindingV1], ...] = tuple(
            (_require_token("effect_binding_call_id", call_id), binding)
            for call_id, binding in self.effect_bindings_by_call_id
        )
        if not all(type(binding) is DeferredDirectorRepairEffectBindingV1 for _, binding in binding_pairs):
            raise TypeError("effect bindings must contain exact DeferredDirectorRepairEffectBindingV1 values")
        if len(dict(binding_pairs)) != len(binding_pairs):
            raise ValueError("effect binding call ids must be unique")
        invocation_by_call_id = {
            str(invocation.call_id): invocation for invocation in self.forward_invocations + self.rollback_invocations
        }
        if set(dict(binding_pairs)) != set(invocation_by_call_id):
            raise ValueError("effect bindings must cover exact synthesized invocations")
        for call_id, binding in binding_pairs:
            invocation = invocation_by_call_id[call_id]
            if (
                binding.tool_call_id != call_id
                or binding.request_id != self.request_id
                or binding.request_hash != self.request_hash
                or binding.plan_hash != self.plan_hash
                or binding.effect.tool_name != invocation.tool_name
                or binding.effect.arguments_hash
                != hash_directed_effect_arguments(tuple(sorted(invocation.arguments.items())))
            ):
                raise ValueError("effect binding must match its synthesized invocation")
        error_code = str(self.error_code or "").strip() or None
        if self.ok and error_code is not None:
            raise ValueError("successful synthesis must not include error_code")
        if not self.ok and error_code is None:
            raise ValueError("failed synthesis requires error_code")
        if not self.ok and (self.forward_invocations or self.rollback_invocations or activation_pairs or binding_pairs):
            raise ValueError("failed synthesis must not expose tool invocations")
        object.__setattr__(self, "rollback_activation_by_call_id", activation_pairs)
        object.__setattr__(self, "effect_bindings_by_call_id", binding_pairs)
        object.__setattr__(self, "error_code", error_code)
        object.__setattr__(self, "schema_version", _require_token("schema_version", self.schema_version))


@dataclass(frozen=True, slots=True)
class DirectedEffectExecutionContextV1:
    """Deeply immutable context registered by the process-local fence."""

    context_id: str
    batch_id: str
    creator_pid: int
    tool_call_id: str
    normalized_tool_name: str
    arguments_hash: str
    authorization_evidence: DirectorEffectAuthorizationEvidenceV1
    claim_grant: DirectedEffectClaimGrantV1
    bound_snapshot: DirectorEffectPolicyBoundSnapshotV1
    current_policy_evidence: DirectorEffectCurrentPolicyEvidenceV1
    current_job_token_restriction_evidence: DirectedEffectImmutableItemsV1

    def __post_init__(self) -> None:
        for field_name in ("context_id", "batch_id", "tool_call_id", "normalized_tool_name"):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        object.__setattr__(self, "arguments_hash", require_directed_effect_hash("arguments_hash", self.arguments_hash))
        _require_positive_int("creator_pid", self.creator_pid)
        if not isinstance(self.authorization_evidence, DirectorEffectAuthorizationEvidenceV1):
            raise TypeError("authorization_evidence must be DirectorEffectAuthorizationEvidenceV1")
        if not isinstance(self.claim_grant, DirectedEffectClaimGrantV1):
            raise TypeError("claim_grant must be DirectedEffectClaimGrantV1")
        try:
            canonical_bound_snapshot = validate_director_effect_policy_bound_snapshot(self.bound_snapshot)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("bound_snapshot must be canonical") from exc
        if canonical_bound_snapshot != self.bound_snapshot:
            raise ValueError("bound_snapshot must be canonical")
        try:
            canonical_current_policy = validate_director_effect_current_policy_evidence(self.current_policy_evidence)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("current_policy_evidence must be canonical") from exc
        if canonical_current_policy != self.current_policy_evidence:
            raise ValueError("current_policy_evidence must be canonical")
        object.__setattr__(
            self,
            "current_job_token_restriction_evidence",
            require_directed_effect_immutable_items(
                "current_job_token_restriction_evidence",
                self.current_job_token_restriction_evidence,
            ),
        )
        validate_directed_effect_identity_binding(
            boundary_name="execution context",
            authorization_evidence=self.authorization_evidence,
            claim_grant=self.claim_grant,
            normalized_tool_name=self.normalized_tool_name,
            arguments_hash=self.arguments_hash,
            batch_id=self.batch_id,
            tool_call_id=self.tool_call_id,
        )
        if (
            self.bound_snapshot.authorization_binding.authorization_evidence != self.authorization_evidence
            or self.bound_snapshot.member != self.claim_grant.member
            or self.current_policy_evidence.claim_grant_hash != self.claim_grant.grant_hash
            or self.current_policy_evidence.bound_member_hash != self.bound_snapshot.member_binding_hash
            or self.current_policy_evidence.baseline_authorization_binding_hash
            != self.bound_snapshot.authorization_binding_hash
        ):
            raise ValueError("execution context bound snapshot identity mismatch")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        """Reject every pickle/IPC transport of this process-local capability."""
        del protocol
        raise TypeError("DirectedEffectExecutionContextV1 is not serializable")


def validate_directed_effect_execution_context(
    context: DirectedEffectExecutionContextV1,
) -> DirectedEffectExecutionContextV1:
    """Canonical-reconstruct a context and its nested authorization and claim grant."""

    if type(context) is not DirectedEffectExecutionContextV1:
        raise TypeError("context must be exactly DirectedEffectExecutionContextV1")
    authorization = validate_director_effect_authorization_evidence(context.authorization_evidence)
    bound_snapshot = validate_director_effect_policy_bound_snapshot(context.bound_snapshot)
    current_policy_evidence = validate_director_effect_current_policy_evidence(context.current_policy_evidence)
    grant = context.claim_grant
    if type(grant) is not DirectedEffectClaimGrantV1:
        raise TypeError("claim_grant must be exactly DirectedEffectClaimGrantV1")
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
    canonical = DirectedEffectExecutionContextV1(
        context_id=context.context_id,
        batch_id=context.batch_id,
        creator_pid=context.creator_pid,
        tool_call_id=context.tool_call_id,
        normalized_tool_name=context.normalized_tool_name,
        arguments_hash=context.arguments_hash,
        authorization_evidence=authorization,
        claim_grant=canonical_grant,
        bound_snapshot=bound_snapshot,
        current_policy_evidence=current_policy_evidence,
        current_job_token_restriction_evidence=context.current_job_token_restriction_evidence,
    )
    if canonical != context:
        raise ValueError("execution context canonical reconstruction mismatch")
    return canonical


@dataclass(frozen=True, slots=True)
class DirectedEffectPreparedMemberV1:
    """One admitted member and the exact stream heads required to claim it."""

    member: DirectedEffectInventoryMemberV1
    policy_binding: DirectorEffectPolicyMemberBindingResultV1
    admitted_operation_version: int
    latest_operation_stream_head: int

    def __post_init__(self) -> None:
        if not isinstance(self.member, DirectedEffectInventoryMemberV1):
            raise TypeError("member must be DirectedEffectInventoryMemberV1")
        try:
            canonical_binding = validate_director_effect_policy_member_binding_result(self.policy_binding)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("policy_binding must be canonical") from exc
        if (
            canonical_binding.status != "allowed"
            or canonical_binding.member != self.member
            or canonical_binding.bound_snapshot is None
        ):
            raise ValueError("policy_binding must be an allowed binding for the exact member")
        _require_positive_int("admitted_operation_version", self.admitted_operation_version)
        _require_non_negative_int("latest_operation_stream_head", self.latest_operation_stream_head)


@dataclass(frozen=True, slots=True)
class PreparedDirectedEffectBatchV1:
    """Ready immutable batch projection prepared before any physical effect."""

    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    inventory: DirectedEffectInventoryProjectionV1
    prepared_members: tuple[DirectedEffectPreparedMemberV1, ...]
    call_id_index: tuple[tuple[str, int], ...]
    latest_parent_registry_head: int
    latest_operation_stream_head: int
    authorization_evidence_by_call_id: tuple[tuple[str, DirectorEffectAuthorizationEvidenceV1], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")
        if not isinstance(self.inventory, DirectedEffectInventoryProjectionV1) or not self.inventory.inventory_ready:
            raise ValueError("inventory must be a ready DirectedEffectInventoryProjectionV1")
        expected_registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
            self.execution_attempt
        )
        if (
            self.inventory.execution_attempt != self.execution_attempt
            or self.inventory.workspace != self.execution_attempt.workspace
            or self.inventory.task_id != self.execution_attempt.task_id
        ):
            raise ValueError("ready inventory execution attempt must exactly match prepared batch")
        if (
            self.parent_binding.registry_identity != expected_registry_identity
            or self.inventory.parent_binding_id != self.parent_binding.binding_id
        ):
            raise ValueError("ready inventory parent binding must exactly match prepared batch")
        if (
            self.latest_parent_registry_head != self.inventory.parent_registry_source_head_seq
            or self.latest_operation_stream_head != self.inventory.operation_source_head_seq
        ):
            raise ValueError("prepared batch source heads must exactly match ready inventory")
        if not isinstance(self.prepared_members, tuple) or not self.prepared_members:
            raise ValueError("prepared_members must be a non-empty immutable tuple")
        members = tuple(self.prepared_members)
        if any(not isinstance(member, DirectedEffectPreparedMemberV1) for member in members):
            raise TypeError("prepared_members must contain DirectedEffectPreparedMemberV1")
        if tuple(member.member for member in members) != self.inventory.members:
            raise ValueError("prepared_members must exactly match ready inventory members")
        call_ids = tuple(member.member.tool_call_id for member in members)
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("prepared inventory members must have unique tool_call_id")
        if not isinstance(self.call_id_index, tuple) or self.call_id_index != tuple(
            (call_id, index) for index, call_id in enumerate(call_ids)
        ):
            raise ValueError("call_id_index must exactly index prepared_members")
        _require_non_negative_int("latest_parent_registry_head", self.latest_parent_registry_head)
        _require_non_negative_int("latest_operation_stream_head", self.latest_operation_stream_head)
        if members[-1].latest_operation_stream_head != self.latest_operation_stream_head:
            raise ValueError("prepared member source heads must end at the ready inventory head")
        if not isinstance(self.authorization_evidence_by_call_id, tuple):
            raise TypeError("authorization_evidence_by_call_id must be an immutable tuple")
        if len(self.authorization_evidence_by_call_id) != len(members):
            raise ValueError("authorization evidence must exactly cover prepared members")
        expected_attempt_id = expected_registry_identity.execution_attempt_id
        for item, prepared_member in zip(
            self.authorization_evidence_by_call_id,
            members,
            strict=True,
        ):
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("authorization evidence entries must be immutable pairs")
            call_id, evidence = item
            member = prepared_member.member
            if not isinstance(evidence, DirectorEffectAuthorizationEvidenceV1):
                raise TypeError("authorization evidence values must be DirectorEffectAuthorizationEvidenceV1")
            if (
                call_id != member.tool_call_id
                or evidence.tool_call_id != member.tool_call_id
                or evidence.normalized_tool_name != member.normalized_tool_name
                or evidence.workspace != self.execution_attempt.workspace
                or evidence.execution_attempt_id != expected_attempt_id
                or evidence.turn_id != self.parent_binding.correlation.turn_id
                or evidence.batch_id != self.parent_binding.correlation.batch_id
            ):
                raise ValueError("authorization evidence identity must exactly match prepared member")
            bound_snapshot = prepared_member.policy_binding.bound_snapshot
            if bound_snapshot is None or bound_snapshot.authorization_binding.authorization_evidence != evidence:
                raise ValueError("prepared member policy binding must retain exact batch authorization evidence")


@dataclass(frozen=True, slots=True)
class DirectedEffectLifecycleResultV1:
    """Whole-batch lifecycle preparation result."""

    status: DirectedEffectLifecycleStatusV1
    prepared_batch: PreparedDirectedEffectBatchV1 | None
    error_code: DirectedEffectErrorCodeV1 | None
    upstream_evidence: DirectedEffectImmutableItemsV1

    def __post_init__(self) -> None:
        if self.status not in {"ready", "not_applicable", "denied"}:
            raise ValueError("status must be ready, not_applicable, or denied")
        object.__setattr__(
            self,
            "upstream_evidence",
            require_directed_effect_immutable_items("upstream_evidence", self.upstream_evidence),
        )
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.status == "ready":
            if not isinstance(self.prepared_batch, PreparedDirectedEffectBatchV1) or self.error_code is not None:
                raise ValueError("ready lifecycle requires a prepared batch and no error")
        elif self.status == "not_applicable":
            if self.prepared_batch is not None or self.error_code is not None:
                raise ValueError("not_applicable lifecycle is a successful non-error result")
        elif self.prepared_batch is not None or self.error_code is None:
            raise ValueError("denied lifecycle requires a closed error and no prepared batch")


@dataclass(frozen=True, slots=True)
class DirectedEffectAttemptValidationResultV1:
    """Typed validation result for the process-local TaskRuntime authority."""

    status: DirectedEffectAttemptValidationStatusV1
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None
    error_code: DirectedEffectErrorCodeV1 | None

    def __post_init__(self) -> None:
        if self.status not in {"valid", "denied"}:
            raise ValueError("status must be valid or denied")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.status == "valid":
            if (
                not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1)
                or self.error_code is not None
            ):
                raise ValueError("valid attempt result requires exact identity and no error")
        elif self.execution_attempt is not None or self.error_code is None:
            raise ValueError("denied attempt result requires a closed error")


@dataclass(frozen=True, slots=True)
class DirectedEffectAttemptHeartbeatResultV1:
    """Typed freshness result for the process-local TaskRuntime authority."""

    status: DirectedEffectAttemptHeartbeatStatusV1
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None
    error_code: DirectedEffectErrorCodeV1 | None

    def __post_init__(self) -> None:
        if self.status not in {"fresh", "denied"}:
            raise ValueError("status must be fresh or denied")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.status == "fresh":
            if (
                not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1)
                or self.error_code is not None
            ):
                raise ValueError("fresh heartbeat requires exact identity and no error")
        elif self.execution_attempt is not None or self.error_code is None:
            raise ValueError("denied heartbeat requires a closed error")


@dataclass(frozen=True, slots=True)
class DirectedEffectContextClaimResultV1:
    """Typed result of claim plus current evidence, before fence registration."""

    status: DirectedEffectContextClaimStatusV1
    context: DirectedEffectExecutionContextV1 | None
    error_code: DirectedEffectErrorCodeV1 | None
    operation_claim_status: DirectedEffectOperationClaimStatusV1

    def __post_init__(self) -> None:
        if self.status not in {"claimed", "denied"}:
            raise ValueError("status must be claimed or denied")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.operation_claim_status not in {"not_claimed", "claimed", "unknown"}:
            raise ValueError("operation_claim_status must be not_claimed, claimed, or unknown")
        if self.status == "claimed":
            if (
                not isinstance(self.context, DirectedEffectExecutionContextV1)
                or self.error_code is not None
                or self.operation_claim_status != "claimed"
            ):
                raise ValueError("claimed requires one execution context and no error")
        elif self.context is not None or self.error_code is None:
            raise ValueError("denied requires no execution context and one closed error")


@dataclass(frozen=True, slots=True)
class DirectedEffectFenceRegistrationResultV1:
    """Typed one-time fence registration result."""

    ok: bool
    status: DirectedEffectFenceRegistrationStatusV1
    context_id: str
    error_code: DirectedEffectErrorCodeV1 | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", require_directed_effect_bool("ok", self.ok))
        if self.status not in {"registered", "denied"} or self.ok != (self.status == "registered"):
            raise ValueError("registration ok must agree with status")
        object.__setattr__(self, "context_id", _require_token("context_id", self.context_id))
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.ok != (self.error_code is None):
            raise ValueError("registration success and error_code must agree")


@dataclass(frozen=True, slots=True)
class DirectedEffectFenceConsumeResultV1:
    """Typed one-time fence consumption result."""

    ok: bool
    status: DirectedEffectFenceConsumeStatusV1
    context_id: str
    error_code: DirectedEffectErrorCodeV1 | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", require_directed_effect_bool("ok", self.ok))
        if self.status not in {"consumed", "denied"} or self.ok != (self.status == "consumed"):
            raise ValueError("consume ok must agree with status")
        object.__setattr__(self, "context_id", _require_token("context_id", self.context_id))
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.ok != (self.error_code is None):
            raise ValueError("consume success and error_code must agree")


@dataclass(frozen=True, slots=True)
class DirectedEffectFenceReleaseResultV1:
    """Typed idempotent fence cleanup result."""

    ok: bool
    status: DirectedEffectFenceReleaseStatusV1
    batch_id: str
    released_count: int
    error_code: DirectedEffectErrorCodeV1 | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", require_directed_effect_bool("ok", self.ok))
        if self.status not in {"released", "absent", "denied"}:
            raise ValueError("status must be released, absent, or denied")
        if self.ok != (self.status in {"released", "absent"}):
            raise ValueError("release ok must agree with status")
        object.__setattr__(self, "batch_id", _require_token("batch_id", self.batch_id))
        _require_non_negative_int("released_count", self.released_count)
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.ok != (self.error_code is None):
            raise ValueError("release success and error_code must agree")


@dataclass(frozen=True, slots=True)
class DirectedEffectToolResultV1:
    """Canonical immutable tool payload returned by the mutation port."""

    payload: DirectedEffectImmutableItemsV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", require_directed_effect_immutable_items("payload", self.payload))


@dataclass(frozen=True, slots=True)
class DirectedEffectMutationPortResultV1:
    """Closed mutation outcome with optional durable failure evidence.

    Executed results carry the physical tool payload. Failed results may carry
    only immutable TaskRuntime recovery/dead-letter evidence so downstream
    Run Ledger projection can distinguish present-but-failed evidence from a
    missing receipt. Denials never carry a payload.
    """

    ok: bool
    status: DirectedEffectMutationStatusV1
    tool_result: DirectedEffectToolResultV1 | None
    error_code: DirectedEffectErrorCodeV1 | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", require_directed_effect_bool("ok", self.ok))
        if self.status not in {"executed", "denied", "failed"}:
            raise ValueError("status must be executed, denied, or failed")
        object.__setattr__(self, "error_code", _require_error_code(self.error_code))
        if self.status == "executed":
            if (
                not self.ok
                or not isinstance(self.tool_result, DirectedEffectToolResultV1)
                or self.error_code is not None
            ):
                raise ValueError("executed mutation requires an immutable tool_result and no error")
        elif self.status == "denied":
            if self.ok or self.tool_result is not None or self.error_code is None:
                raise ValueError("denied mutation cannot carry a tool_result")
        elif self.ok or self.error_code is None:
            raise ValueError("failed mutation requires false ok and an error")
        elif self.tool_result is not None and type(self.tool_result) is not DirectedEffectToolResultV1:
            raise TypeError("failed mutation tool_result must be immutable recovery evidence")


@runtime_checkable
class DirectedEffectFenceAdminPortV1(Protocol):
    """Kernel-only fence administration capability."""

    def register(self, context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceRegistrationResultV1:
        """Register one exact immutable context before adapter visibility."""

    def release_batch(
        self,
        batch_id: str,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectFenceReleaseResultV1:
        """Release all contexts owned by one batch during kernel cleanup."""


@runtime_checkable
class DirectedEffectFenceConsumePortV1(Protocol):
    """Adapter-visible one-time fence spending capability."""

    def consume(self, context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceConsumeResultV1:
        """Spend exactly one registered context without administrative access."""


@runtime_checkable
class DirectedEffectMutationPortV1(Protocol):
    """Adapter-owned physical mutation port called only after fence consume."""

    async def execute_mutation(
        self,
        context: DirectedEffectExecutionContextV1,
        normalized_tool_name: str,
        normalized_arguments: DirectedEffectImmutableItemsV1,
        repair_effect_binding: DeferredDirectorRepairEffectBindingV1 | None = None,
    ) -> DirectedEffectMutationPortResultV1:
        """Execute one normalized mutation and return immutable tool output."""


@dataclass(frozen=True, slots=True)
class DirectedEffectFencePortsV1:
    """Narrowed fence views exposed to their respective consumers."""

    admin: DirectedEffectFenceAdminPortV1
    consume: DirectedEffectFenceConsumePortV1

    def __post_init__(self) -> None:
        if not isinstance(self.admin, DirectedEffectFenceAdminPortV1):
            raise TypeError("admin must satisfy DirectedEffectFenceAdminPortV1")
        if not isinstance(self.consume, DirectedEffectFenceConsumePortV1):
            raise TypeError("consume must satisfy DirectedEffectFenceConsumePortV1")


@dataclass(frozen=True, slots=True)
class DirectedEffectRuntimeDependenciesV1:
    """Kernel dependency bundle without adapter implementation access."""

    policy_snapshot_port: DirectorEffectPolicySnapshotPortV1
    fence_admin_port: DirectedEffectFenceAdminPortV1
    mutation_port: DirectedEffectMutationPortV1

    def __post_init__(self) -> None:
        if not isinstance(self.policy_snapshot_port, DirectorEffectPolicySnapshotPortV1):
            raise TypeError("policy_snapshot_port must satisfy DirectorEffectPolicySnapshotPortV1")
        if not isinstance(self.fence_admin_port, DirectedEffectFenceAdminPortV1):
            raise TypeError("fence_admin_port must satisfy DirectedEffectFenceAdminPortV1")
        if not isinstance(self.mutation_port, DirectedEffectMutationPortV1):
            raise TypeError("mutation_port must satisfy DirectedEffectMutationPortV1")
