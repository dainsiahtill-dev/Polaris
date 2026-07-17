"""Immutable DEO-2B contracts exposed by the roles.kernel cell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectedEffectErrorCodeV1,
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableValueV1,
    DirectorEffectAuthorizationEvidenceV1,
    require_directed_effect_bool,
    require_directed_effect_hash,
    require_directed_effect_immutable_items,
    validate_directed_effect_error_code,
    validate_directed_effect_identity_binding,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import DirectorEffectPolicySnapshotPortV1
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

    def __post_init__(self) -> None:
        for field_name in ("context_id", "batch_id", "tool_call_id", "normalized_tool_name"):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        object.__setattr__(self, "arguments_hash", require_directed_effect_hash("arguments_hash", self.arguments_hash))
        _require_positive_int("creator_pid", self.creator_pid)
        if not isinstance(self.authorization_evidence, DirectorEffectAuthorizationEvidenceV1):
            raise TypeError("authorization_evidence must be DirectorEffectAuthorizationEvidenceV1")
        if not isinstance(self.claim_grant, DirectedEffectClaimGrantV1):
            raise TypeError("claim_grant must be DirectedEffectClaimGrantV1")
        validate_directed_effect_identity_binding(
            boundary_name="execution context",
            authorization_evidence=self.authorization_evidence,
            claim_grant=self.claim_grant,
            normalized_tool_name=self.normalized_tool_name,
            arguments_hash=self.arguments_hash,
            batch_id=self.batch_id,
            tool_call_id=self.tool_call_id,
        )


@dataclass(frozen=True, slots=True)
class DirectedEffectPreparedMemberV1:
    """One admitted member and the exact stream heads required to claim it."""

    member: DirectedEffectInventoryMemberV1
    admitted_operation_version: int
    latest_operation_stream_head: int

    def __post_init__(self) -> None:
        if not isinstance(self.member, DirectedEffectInventoryMemberV1):
            raise TypeError("member must be DirectedEffectInventoryMemberV1")
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
    """Closed mutation outcome; only executed results contain a tool payload."""

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
        elif self.ok or self.tool_result is not None or self.error_code is None:
            raise ValueError("denied or failed mutation cannot carry a tool_result")


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
