"""TaskRuntime-owned Directed Effect Operation v1 authority.

One lease-independent execution-attempt registry is the sole authority for
parent existence and OPEN state. Each admitted parent owns a separate child
operation stream. Registry and operation snapshots are never authorization
inputs; all decisions rebuild bounded strict FactStream partitions.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    AppendIfGuardedSnapshotCommandV1,
    EnrollFactStreamStreamsCommandV1,
    FactStreamError,
    GuardedFactAppendedV1,
    GuardedFactEventV1,
    GuardedFactSnapshotV1,
    QueryFactEventsV1,
    ReadGuardedFactSnapshotCommandV1,
    append_fact_event,
    append_if_guarded_snapshot,
    enroll_fact_stream_streams,
    query_fact_events,
    read_guarded_fact_snapshot,
)

from ..public.contracts import (
    DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V2,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V3,
    DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2,
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DeadLetterDirectedEffectOperationCommandV1,
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryCodeV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectInventoryProjectionV1,
    DirectedEffectInventoryResultV1,
    DirectedEffectOperationCodeV1,
    DirectedEffectOperationIdentityV1,
    DirectedEffectOperationResultV1,
    DirectedEffectOperationSnapshotV1,
    DirectedEffectOperationStateV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentReadinessProjectionV1,
    DirectedEffectParentReadinessResultV1,
    DirectedEffectParentReadinessStateCountV1,
    DirectedEffectParentRegistryIdentityV1,
    DirectedEffectParentRegistryProjectionV1,
    DirectedEffectParentRegistryResultV1,
    DirectedEffectStreamEnrollmentResultV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    GetDirectedEffectOperationQueryV1,
    GetDirectedEffectParentReadinessQueryV1,
    GetDirectedEffectParentRegistryQueryV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    ParentCorrelationV1,
    SealDirectedEffectInventoryCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    TaskRuntimeExecutionAttemptValidationCodeV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)

_MAX_REGISTRY_EVENTS = 512
_MAX_OPERATION_EVENTS = 512
_PARENT_ADMITTED_EVENT_TYPE = "task_runtime.directed_effect_parent_registry.v1.parent_admitted"
_PARENT_CLOSED_EVENT_TYPE = "task_runtime.deo_parent_registry.v1.closed"
_PARENT_INVENTORY_SEALED_EVENT_TYPE = "task_runtime.directed_effect_parent_registry.v1.parent_inventory_sealed"
_PARENT_INVENTORY_READY_EVENT_TYPE = "task_runtime.directed_effect_parent_registry.v1.parent_inventory_ready"
_DIRECTED_EFFECT_INVENTORY_HASH_SCHEMA_V1 = "task-runtime.directed-effect-inventory/1"
_DIRECTED_EFFECT_ADMISSION_SET_HASH_SCHEMA_V1 = "task-runtime.directed-effect-inventory-admission-set/1"
_OPERATION_EVENT_PREFIX = "task_runtime.directed_effect_operation.v1"
_TERMINAL_STATES = frozenset({"CLOSED_BY_PARENT", "ABORTED", "DEAD_LETTER"})


@dataclass(frozen=True, slots=True)
class _CloseDirectedEffectByParentCommandV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    tool_call_id: str
    effect_id: str
    expected_version: int
    expected_seq: int
    intended_effect_fingerprint: str
    policy_verdict_hash: str
    expected_receipt_binding_hash: str
    terminal_intent_hash: str
    settlement_outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1
    actor: str = "runtime.task_runtime.settlement"


_Command: TypeAlias = (
    AdmitDirectedEffectOperationCommandV1
    | ClaimDirectedEffectCommandV1
    | AbortDirectedEffectOperationCommandV1
    | CommitDirectedEffectReceiptCommandV1
    | MarkDirectedEffectRecoveryPendingCommandV1
    | DeadLetterDirectedEffectOperationCommandV1
    | _CloseDirectedEffectByParentCommandV1
)
_ReadyGatedCommand: TypeAlias = ClaimDirectedEffectCommandV1 | AbortDirectedEffectOperationCommandV1
_InventoryGuardedCommand: TypeAlias = (
    SealDirectedEffectInventoryCommandV1
    | FinalizeDirectedEffectInventoryAdmissionCommandV1
    | GetDirectedEffectInventoryQueryV1
)
_AuthorityCommand: TypeAlias = (
    _Command | SealDirectedEffectInventoryCommandV1 | FinalizeDirectedEffectInventoryAdmissionCommandV1
)
_ReadCommand: TypeAlias = _Command | GetDirectedEffectOperationQueryV1
_ParentRegistryBoundCommand: TypeAlias = _ReadCommand | _InventoryGuardedCommand
_ParentBindingReadCommand: TypeAlias = _ReadCommand | GetDirectedEffectParentReadinessQueryV1
_CommandKind = Literal[
    "admit",
    "claim",
    "abort",
    "commit_receipt",
    "mark_recovery_pending",
    "dead_letter",
    "close_by_parent",
]
_FactOperation = Literal["read", "append"]
_StreamKind = Literal["parent_registry", "operation", "inventory_guarded_pair"]

_EXECUTION_ATTEMPT_FAILURE_CODES: dict[TaskRuntimeExecutionAttemptValidationCodeV1, DirectedEffectOperationCodeV1] = {
    "valid": "execution_attempt_validation_unknown",
    "workspace_mismatch": "workspace_mismatch",
    "task_not_found": "task_not_found",
    "session_not_found": "session_not_found",
    "session_corrupt": "session_corrupt",
    "session_task_mismatch": "session_task_mismatch",
    "session_mismatch": "session_mismatch",
    "attempt_mismatch": "attempt_mismatch",
    "role_mismatch": "role_mismatch",
    "worker_mismatch": "worker_mismatch",
    "run_mismatch": "run_mismatch",
    "external_task_id_mismatch": "external_task_id_mismatch",
    "lease_version_mismatch": "lease_version_mismatch",
    "session_not_active": "session_not_active",
    "session_lease_expired": "session_lease_expired",
    "file_lock_timeout": "file_lock_timeout",
}

_READ_FACT_FAILURE_CODES: dict[str, DirectedEffectOperationCodeV1] = {
    "event_sourcing_error": "fact_stream_unknown_failure",
    "file_lock_timeout": "stream_lock_timeout",
    "lock_timeout": "stream_lock_timeout",
    "stream_lock_timeout": "stream_lock_timeout",
    "strict_scan_limit_exceeded": "strict_stream_overload",
    "strict_scan_limit_check_failed": "strict_stream_overload",
    "torn_tail": "strict_stream_torn_tail",
    "unknown_schema_version": "strict_stream_unknown_schema",
    "unknown_event_version": "strict_stream_unknown_schema",
    "stream_corruption": "strict_stream_corruption",
    "strict_record_corruption": "strict_stream_corruption",
    "sequence_violation": "strict_stream_corruption",
    "integrity_digest_mismatch": "strict_stream_corruption",
    "missing_integrity_digest": "strict_stream_corruption",
    "invalid_raw_integer": "strict_stream_corruption",
    "stream_read_failed": "strict_stream_corruption",
    "query_failed": "fact_stream_unknown_failure",
    "head_query_failed": "fact_stream_unknown_failure",
    "fact_stream_error": "fact_stream_unknown_failure",
    "stream_lock_missing": "stream_lock_missing",
}

_APPEND_FACT_FAILURE_CODES: dict[str, DirectedEffectOperationCodeV1] = {
    **_READ_FACT_FAILURE_CODES,
    "expected_seq_drift": "stream_cas_exhausted",
    "idempotency_conflict": "idempotency_conflict",
    "idempotency_semantic_conflict": "idempotency_semantic_conflict",
    "append_failed": "stream_append_failed",
    "append_write_failed": "stream_append_failed",
    "provenance_mismatch": "stream_append_failed",
    "target_snapshot_drift": "stream_cas_exhausted",
    "guard_snapshot_drift": "stream_cas_exhausted",
}

_GUARDED_REPREPARE_DRIFT_CODES = frozenset({"target_snapshot_drift", "guard_snapshot_drift"})
_MAX_GUARDED_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class _StreamRead:
    events: tuple[dict[str, Any], ...]
    head_seq: int


@dataclass(frozen=True, slots=True)
class _RegistryAdmission:
    binding: DirectedEffectParentBindingV1
    request_descriptor: Mapping[str, object]
    canonical_event: Mapping[str, object]
    event_id: str
    seq: int


@dataclass(frozen=True, slots=True)
class _SealedDirectedEffectInventory:
    """One immutable inventory seal reconstructed from registry facts."""

    binding_id: str
    members: tuple[DirectedEffectInventoryMemberV1, ...]
    inventory_hash: str
    actor: str
    event_id: str
    seq: int


@dataclass(frozen=True, slots=True)
class _ReadyDirectedEffectInventory:
    """One immutable readiness fact reserved for the admission-finalize slice."""

    binding_id: str
    inventory_hash: str
    ordered_operation_ids: tuple[str, ...]
    admission_set_hash: str
    operation_source_head_seq: int
    event_id: str
    seq: int


@dataclass(frozen=True, slots=True)
class _InventoryOperationProjection:
    """Strict operation-stream classification relative to one sealed inventory."""

    source_head_seq: int
    admitted_count: int
    missing_operation_ids: tuple[str, ...]
    unexpected_operation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _StrictInventoryConfirmation:
    """Strict registry seal plus operation classification from one dual snapshot."""

    binding: DirectedEffectParentBindingV1
    registry: _ParentRegistry
    sealed: _SealedDirectedEffectInventory
    operations: _InventoryOperationProjection


@dataclass(frozen=True, slots=True)
class _ParentRegistry:
    identity: DirectedEffectParentRegistryIdentityV1
    stream_token: str
    registry_version: int
    source_head_seq: int
    next_expected_seq: int
    next_parent_sequence: int
    open_binding: DirectedEffectParentBindingV1 | None
    admissions_by_idempotency_key: Mapping[str, _RegistryAdmission]
    bindings_by_id: Mapping[str, DirectedEffectParentBindingV1]
    sealed_inventories_by_binding_id: Mapping[str, _SealedDirectedEffectInventory]
    ready_inventories_by_binding_id: Mapping[str, _ReadyDirectedEffectInventory]
    settlement_close_proof: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class _CommittedTransition:
    operation: DirectedEffectOperationIdentityV1
    state: DirectedEffectOperationStateV1
    previous_version: int
    version: int
    descriptor: Mapping[str, object]
    normalized: _NormalizedDirectedEffectTransitionV1
    canonical_event: Mapping[str, object]
    event_id: str
    seq: int


@dataclass(frozen=True, slots=True)
class _NormalizedDirectedEffectReplayDescriptorV1:
    """Schema-neutral command semantics excluding CAS and storage volatility."""

    command: _CommandKind
    actor: str
    reason: str
    intended_effect_fingerprint: str
    policy_verdict_hash: str
    expected_receipt_binding_hash: str
    receipt_ref: str
    receipt_hash: str
    receipt_binding_hash: str
    receipt_outcome: str
    recovery_evidence_ref: str
    recovery_evidence_hash: str
    resolution_evidence_ref: str
    resolution_evidence_hash: str
    terminal_intent_hash: str
    settlement_outcome: str

    def to_record(self) -> dict[str, str]:
        return {
            "command": self.command,
            "actor": self.actor,
            "reason": self.reason,
            "intended_effect_fingerprint": self.intended_effect_fingerprint,
            "policy_verdict_hash": self.policy_verdict_hash,
            "expected_receipt_binding_hash": self.expected_receipt_binding_hash,
            "receipt_ref": self.receipt_ref,
            "receipt_hash": self.receipt_hash,
            "receipt_binding_hash": self.receipt_binding_hash,
            "receipt_outcome": self.receipt_outcome,
            "recovery_evidence_ref": self.recovery_evidence_ref,
            "recovery_evidence_hash": self.recovery_evidence_hash,
            "resolution_evidence_ref": self.resolution_evidence_ref,
            "resolution_evidence_hash": self.resolution_evidence_hash,
            "terminal_intent_hash": self.terminal_intent_hash,
            "settlement_outcome": self.settlement_outcome,
        }


@dataclass(frozen=True, slots=True)
class _NormalizedDirectedEffectTransitionV1:
    """Schema-neutral persisted transition used for replay and idempotency."""

    operation: DirectedEffectOperationIdentityV1
    state: DirectedEffectOperationStateV1
    replay: _NormalizedDirectedEffectReplayDescriptorV1

    def to_record(self) -> dict[str, object]:
        return {
            "operation": self.operation.to_record(),
            "state": self.state,
            "replay": self.replay.to_record(),
        }


@dataclass(frozen=True, slots=True)
class _Aggregate:
    operation: DirectedEffectOperationIdentityV1
    state: DirectedEffectOperationStateV1 | None
    version: int
    intended_effect_fingerprint: str
    policy_verdict_hash: str
    expected_receipt_binding_hash: str
    source_head_seq: int
    last_event_id: str
    transitions: tuple[_CommittedTransition, ...]


@dataclass(frozen=True, slots=True)
class _DirectedEffectRecoveryRepositorySweep:
    """Bounded repository result including the number of sealed members read."""

    results: tuple[DirectedEffectOperationResultV1, ...]
    scanned_operation_count: int


@dataclass(frozen=True, slots=True)
class _DirectedEffectRecoverySweepPreparation:
    binding: DirectedEffectParentBindingV1
    members: tuple[DirectedEffectInventoryMemberV1, ...]


@dataclass(frozen=True, slots=True)
class _DirectedEffectRecoveryCursor:
    expected_version: int
    expected_seq: int
    observed_state: DirectedEffectOperationStateV1


@dataclass(frozen=True, slots=True)
class _ParentSettlementPreparation:
    """Immutable facts required to close receipt-backed children and parent."""

    identity: DirectedEffectParentRegistryIdentityV1
    registry: _ParentRegistry
    binding: DirectedEffectParentBindingV1
    reduced: _OperationStreamReduction
    receipt_records: tuple[dict[str, object], ...]
    close_candidates: tuple[_Aggregate, ...]


@dataclass(frozen=True, slots=True)
class _ParentSettlementGuardedClose:
    """One dual-stream snapshot prepared for the guarded parent close."""

    prepared: GuardedFactSnapshotV1
    final_registry: _ParentRegistry
    final_reduced: _OperationStreamReduction


@dataclass(frozen=True, slots=True)
class _OperationStreamReduction:
    """Immutable facts reconstructed from one bounded strict operation stream."""

    source_head_seq: int
    aggregates: tuple[_Aggregate, ...]


@dataclass(frozen=True, slots=True)
class _StrictOperationProjection:
    """One strict durable binding and child-stream reconstruction."""

    binding: DirectedEffectParentBindingV1
    registry: _ParentRegistry
    aggregate: _Aggregate
    operation_records: tuple[dict[str, Any], ...]
    ready_context: _ReadyOperationContext | None


@dataclass(frozen=True, slots=True)
class _ReadyOperationContext:
    """Registry-owned readiness and sealed member used by claim or abort."""

    sealed: _SealedDirectedEffectInventory
    ready: _ReadyDirectedEffectInventory
    member: DirectedEffectInventoryMemberV1


_SettlementPreBarrierCode: TypeAlias = Literal[
    "settlement_parent_registry_clear",
    "settlement_parent_close_required",
    "settlement_parent_close_proof_required",
    "settlement_parent_registry_invalid",
    "settlement_parent_registry_unavailable",
    "settlement_directed_effect_unresolved",
    "settlement_effect_outcome_conflict",
    "settlement_terminal_intent_conflict",
    "settlement_parent_close_failed",
]


@dataclass(frozen=True, slots=True)
class DirectedEffectSettlementPreBarrierVerdictV1:
    """Strict registry verdict consumed while TaskRuntime holds session locks."""

    allowed: bool
    code: _SettlementPreBarrierCode
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.allowed != (self.code == "settlement_parent_registry_clear"):
            raise ValueError("allowed must match settlement_parent_registry_clear")
        object.__setattr__(self, "evidence", dict(self.evidence))


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_token(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _new_append_attempt_nonce() -> str:
    return secrets.token_hex(16)


def _is_canonical_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _is_timezone_aware_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _registry_stream_token(identity: DirectedEffectParentRegistryIdentityV1) -> str:
    digest = _hash_token(
        {
            "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
            "stable_registry_identity": identity.to_record(),
        }
    )
    return f"deo_parent_registry_v1_{digest[:48]}"


def _binding_id(identity: DirectedEffectParentRegistryIdentityV1, parent_sequence: int) -> str:
    digest = _hash_token(
        {
            "stable_registry_identity": identity.to_record(),
            "parent_sequence": parent_sequence,
        }
    )
    return f"deo_parent_v1_{digest[:48]}"


def _operation_stream_token(binding_id: str) -> str:
    return f"deo_operation_v1_{_hash_token({'binding_id': binding_id})[:48]}"


def _registry_fact_idempotency_key(
    identity: DirectedEffectParentRegistryIdentityV1,
    admission_idempotency_key: str,
) -> str:
    digest = _hash_token(
        {
            "stable_registry_identity": identity.to_record(),
            "admission_idempotency_key": admission_idempotency_key,
        }
    )
    return f"deo_parent_admission_v1_{digest[:48]}"


def _operation_id(*, binding_id: str, tool_call_id: str, effect_id: str) -> str:
    digest = _hash_token(
        {
            "binding_id": binding_id,
            "tool_call_id": tool_call_id,
            "effect_id": effect_id,
        }
    )
    return f"deo_v1_{digest[:48]}"


def _inventory_effect_id(
    *,
    binding_id: str,
    tool_call_id: str,
    intended_effect_fingerprint: str,
) -> str:
    digest = _hash_token(
        {
            "schema_version": DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
            "parent_binding_id": binding_id,
            "tool_call_id": tool_call_id,
            "intended_effect_fingerprint": intended_effect_fingerprint,
        }
    )
    return f"deo_effect_v1_{digest[:48]}"


def _inventory_member(
    binding: DirectedEffectParentBindingV1,
    intent: DirectedEffectInventoryIntentV1,
) -> DirectedEffectInventoryMemberV1:
    effect_id = _inventory_effect_id(
        binding_id=binding.binding_id,
        tool_call_id=intent.tool_call_id,
        intended_effect_fingerprint=intent.intended_effect_fingerprint,
    )
    return DirectedEffectInventoryMemberV1(
        ordinal=intent.ordinal,
        tool_call_id=intent.tool_call_id,
        effect_id=effect_id,
        operation_id=_operation_id(
            binding_id=binding.binding_id,
            tool_call_id=intent.tool_call_id,
            effect_id=effect_id,
        ),
        normalized_tool_name=intent.normalized_tool_name,
        effect_type=intent.effect_type,
        execution_mode=intent.execution_mode,
        intended_effect_fingerprint=intent.intended_effect_fingerprint,
        policy_verdict_hash=intent.policy_verdict_hash,
        expected_receipt_binding_hash=intent.expected_receipt_binding_hash,
        contingency_kind=intent.contingency_kind,
    )


def _inventory_hash(
    binding_id: str,
    members: tuple[DirectedEffectInventoryMemberV1, ...],
) -> str:
    return _hash_token(
        {
            "schema_version": _DIRECTED_EFFECT_INVENTORY_HASH_SCHEMA_V1,
            "parent_binding_id": binding_id,
            "members": [member.to_record() for member in members],
        }
    )


def _admission_set_hash(
    *,
    binding_id: str,
    inventory_hash: str,
    ordered_operation_ids: tuple[str, ...],
    operation_source_head_seq: int,
) -> str:
    return _hash_token(
        {
            "schema_version": _DIRECTED_EFFECT_ADMISSION_SET_HASH_SCHEMA_V1,
            "binding_id": binding_id,
            "inventory_hash": inventory_hash,
            "ordered_operation_ids": list(ordered_operation_ids),
            "operation_source_head_seq": operation_source_head_seq,
        }
    )


def _operation_event_type(state: DirectedEffectOperationStateV1) -> str:
    return f"{_OPERATION_EVENT_PREFIX}.{state.lower()}"


class DirectedEffectOperationRepository:
    """Strict registry and child-operation aggregate repository."""

    @staticmethod
    def attempt_validation_failure_result(
        verdict: TaskRuntimeExecutionAttemptValidationVerdictV1,
    ) -> DirectedEffectOperationResultV1:
        """Map a locked TaskRuntime validation refusal to the DEO taxonomy."""

        if verdict.valid:
            raise ValueError("a valid attempt verdict cannot be mapped to failure")
        code = _EXECUTION_ATTEMPT_FAILURE_CODES.get(
            verdict.code,
            "execution_attempt_validation_unknown",
        )
        return DirectedEffectOperationResultV1(
            ok=False,
            code=code,
            evidence={"execution_attempt_validation": verdict.to_record()},
        )

    def validate_attempt(
        self,
        workspace: str,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectOperationResultV1 | None:
        """Validate current durable execution authority with typed evidence."""

        from .service import TaskRuntimeService

        verdict = TaskRuntimeService(workspace).validate_execution_attempt(
            ValidateTaskRuntimeExecutionAttemptQueryV1(workspace=workspace, identity=identity)
        )
        if verdict.valid:
            return None
        return self.attempt_validation_failure_result(verdict)

    def _guarded_attempt_failure(
        self,
        command: _AuthorityCommand,
        *,
        attempt_number: int,
        phase: Literal["prepare", "commit", "replay", "reprepare"],
        operation: DirectedEffectOperationIdentityV1 | None = None,
        drift_codes: list[str] | tuple[str, ...] = (),
    ) -> DirectedEffectOperationResultV1 | None:
        """Revalidate complete authority at every guarded iteration boundary."""

        failure = self.validate_attempt(command.workspace, command.execution_attempt)
        if failure is None:
            return None
        evidence = dict(failure.evidence)
        evidence.update(
            {
                "guarded_attempt": attempt_number,
                "guarded_authority_phase": phase,
                "drift_codes": tuple(drift_codes),
            }
        )
        return DirectedEffectOperationResultV1(
            ok=False,
            code=failure.code,
            operation=operation,
            parent_binding=failure.parent_binding,
            parent_registry=failure.parent_registry,
            state=failure.state,
            version=failure.version,
            snapshot=failure.snapshot,
            idempotent=failure.idempotent,
            evidence=evidence,
        )

    def enroll_parent_registry_stream(
        self,
        command: EnrollDirectedEffectParentRegistryStreamCommandV1,
    ) -> DirectedEffectStreamEnrollmentResultV1:
        """Enroll only the registry stream derived from a validated full attempt."""

        identity = command.execution_attempt
        identity_failure = self._command_identity_failure(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
        )
        if identity_failure is not None:
            return self._enrollment_failure(command=command, failure=identity_failure)
        attempt_failure = self.validate_attempt(identity.workspace, identity)
        if attempt_failure is not None:
            return self._enrollment_failure(command=command, failure=attempt_failure)
        registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(identity)
        try:
            receipt = enroll_fact_stream_streams(
                EnrollFactStreamStreamsCommandV1(
                    workspace=identity.workspace,
                    streams=(_registry_stream_token(registry_identity),),
                    maintenance_reason="task_runtime_directed_effect_parent_registry",
                )
            )
        except FactStreamError as exc:
            return self._enrollment_fact_failure(command=command, exc=exc)
        return DirectedEffectStreamEnrollmentResultV1(
            ok=True,
            code="parent_registry_stream_enrolled",
            execution_attempt=identity,
            receipt=self._maintenance_receipt_record(receipt),
            evidence={
                "receipt_authoritative": False,
                "registry_stream_token": _registry_stream_token(registry_identity),
            },
        )

    @staticmethod
    def _after_guarded_prepare(snapshot: GuardedFactSnapshotV1) -> None:
        """Scheduling seam for deterministic tests; production intentionally does nothing."""

        del snapshot

    @staticmethod
    def _after_guarded_commit(
        receipt: GuardedFactAppendedV1,
    ) -> GuardedFactAppendedV1 | None:
        """Post-durability test seam; production preserves the exact receipt."""

        return receipt

    @staticmethod
    def _after_guarded_drift(exc: FactStreamError, attempt_number: int) -> None:
        """Reprepare-boundary seam used only for deterministic authority races."""

        del exc, attempt_number

    @staticmethod
    def _after_settlement_child_close(
        result: DirectedEffectOperationResultV1,
        close_index: int,
    ) -> None:
        """Deterministic crash seam after one durable child close."""

        del result, close_index

    @staticmethod
    def _after_settlement_parent_close(receipt: GuardedFactAppendedV1) -> None:
        """Deterministic crash seam after the outcome-bound parent close."""

        del receipt

    def _prepare_guarded_snapshot(
        self,
        command: _Command,
        identity: DirectedEffectParentRegistryIdentityV1,
    ) -> GuardedFactSnapshotV1 | DirectedEffectOperationResultV1:
        """Prepare immutable child-target and parent-registry snapshots without domain locks."""

        try:
            return read_guarded_fact_snapshot(
                ReadGuardedFactSnapshotCommandV1(
                    workspace=command.workspace,
                    target_stream=command.parent_binding.operation_stream_token,
                    guard_stream=_registry_stream_token(identity),
                )
            )
        except FactStreamError as exc:
            return self._fact_failure_result(exc, operation="read", stream_kind="operation")

    def _validated_parent_binding_from_registry(
        self,
        command: _ParentRegistryBoundCommand,
        *,
        expected_identity: DirectedEffectParentRegistryIdentityV1,
        registry_read: _StreamRead,
        require_open: bool,
    ) -> tuple[DirectedEffectParentBindingV1, _ParentRegistry] | DirectedEffectOperationResultV1:
        """Validate a command binding using the exact registry facts from one prepare."""

        registry = self._reduce_registry_from_read(expected_identity, registry_read)
        if isinstance(registry, DirectedEffectOperationResultV1):
            return registry
        supplied = command.parent_binding
        if supplied.registry_identity != expected_identity:
            return self._parent_failure(
                self._registry_identity_mismatch_code(expected_identity, supplied.registry_identity),
                binding=supplied,
                registry=registry,
            )
        durable = registry.bindings_by_id.get(supplied.binding_id)
        if durable is None:
            return self._parent_failure(
                "parent_binding_not_found",
                binding=supplied,
                registry=registry,
                evidence={"expected_registry_stream_token": _registry_stream_token(expected_identity)},
            )
        mismatch = self._binding_mismatch_result(supplied, durable, registry)
        if mismatch is not None:
            return mismatch
        if require_open and (registry.open_binding is None or registry.open_binding.binding_id != durable.binding_id):
            return self._parent_registry_conflict(
                "parent_closed",
                registry,
                {"requested_binding_id": durable.binding_id, "reason": "binding_is_closed_historical_parent"},
                binding=durable,
            )
        return durable, registry

    def enroll_operation_stream(
        self,
        command: EnrollDirectedEffectOperationStreamCommandV1,
    ) -> DirectedEffectStreamEnrollmentResultV1:
        """Enroll only an operation stream attested by strict durable registry facts."""

        identity = command.execution_attempt
        supplied = command.parent_binding
        identity_failure = self._command_identity_failure(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
        )
        if identity_failure is not None:
            return self._enrollment_failure(command=command, failure=identity_failure)
        attempt_failure = self.validate_attempt(identity.workspace, identity)
        if attempt_failure is not None:
            return self._enrollment_failure(command=command, failure=attempt_failure)
        expected_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(identity)
        registry = self._load_registry(identity.workspace, expected_identity)
        if isinstance(registry, DirectedEffectOperationResultV1):
            return self._enrollment_failure(command=command, failure=registry)
        if supplied.registry_identity != expected_identity:
            failure = self._parent_failure(
                self._registry_identity_mismatch_code(expected_identity, supplied.registry_identity),
                binding=supplied,
                registry=registry,
            )
            return self._enrollment_failure(command=command, failure=failure)
        durable = registry.bindings_by_id.get(supplied.binding_id)
        if durable is None:
            failure = self._parent_failure(
                "parent_binding_not_found",
                binding=supplied,
                registry=registry,
                evidence={"binding_id": supplied.binding_id},
            )
            return self._enrollment_failure(command=command, failure=failure)
        mismatch = self._binding_mismatch_result(supplied, durable, registry)
        if mismatch is not None:
            return self._enrollment_failure(command=command, failure=mismatch)
        try:
            receipt = enroll_fact_stream_streams(
                EnrollFactStreamStreamsCommandV1(
                    workspace=identity.workspace,
                    streams=(durable.operation_stream_token,),
                    maintenance_reason="task_runtime_directed_effect_operation",
                )
            )
        except FactStreamError as exc:
            return self._enrollment_fact_failure(command=command, exc=exc)
        return DirectedEffectStreamEnrollmentResultV1(
            ok=True,
            code="operation_stream_enrolled",
            execution_attempt=identity,
            parent_binding=durable,
            receipt=self._maintenance_receipt_record(receipt),
            evidence={
                "receipt_authoritative": False,
                "registry_stream_token": registry.stream_token,
                "operation_stream_token": durable.operation_stream_token,
                "durable_binding_id": durable.binding_id,
            },
        )

    def settlement_pre_barrier(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        """Strictly classify the attempt registry before an inactive write.

        The caller must hold TaskRuntime's local-session and cooperative
        session-file locks and must have read the identity from that locked
        session. This method performs only strict FactStream reconstruction.
        """

        identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(execution_attempt)
        registry = self._load_registry(execution_attempt.workspace, identity)
        if isinstance(registry, DirectedEffectOperationResultV1):
            if registry.code == "stream_lock_missing":
                return DirectedEffectSettlementPreBarrierVerdictV1(
                    allowed=True,
                    code="settlement_parent_registry_clear",
                    evidence={
                        "registry_state": "unenrolled_or_nonexistent",
                        "registry_identity": identity.to_record(),
                        "registry_result_code": registry.code,
                    },
                )
            unavailable_codes = {
                "fact_stream_unknown_failure",
                "stream_lock_timeout",
                "strict_stream_overload",
            }
            code: _SettlementPreBarrierCode = (
                "settlement_parent_registry_unavailable"
                if registry.code in unavailable_codes
                else "settlement_parent_registry_invalid"
            )
            return DirectedEffectSettlementPreBarrierVerdictV1(
                allowed=False,
                code=code,
                evidence={
                    "registry_state": "strict_read_failed",
                    "registry_identity": identity.to_record(),
                    "registry_result_code": registry.code,
                    "registry_evidence": dict(registry.evidence),
                },
            )
        if registry.open_binding is not None:
            return DirectedEffectSettlementPreBarrierVerdictV1(
                allowed=False,
                code="settlement_parent_close_required",
                evidence={
                    "registry_state": "OPEN",
                    "registry_identity": identity.to_record(),
                    "registry_version": registry.registry_version,
                    "source_head_seq": registry.source_head_seq,
                    "parent_binding_id": registry.open_binding.binding_id,
                },
            )
        if registry.bindings_by_id:
            return DirectedEffectSettlementPreBarrierVerdictV1(
                allowed=False,
                code="settlement_parent_close_proof_required",
                evidence={
                    "registry_state": "CLOSED_WITHOUT_OUTCOME_PROOF",
                    "registry_identity": identity.to_record(),
                    "registry_version": registry.registry_version,
                    "source_head_seq": registry.source_head_seq,
                    "binding_ids": tuple(sorted(registry.bindings_by_id)),
                },
            )
        return DirectedEffectSettlementPreBarrierVerdictV1(
            allowed=True,
            code="settlement_parent_registry_clear",
            evidence={
                "registry_state": "strict_empty",
                "registry_identity": identity.to_record(),
                "registry_version": registry.registry_version,
                "source_head_seq": registry.source_head_seq,
            },
        )

    def settle_parent_for_terminal_intent(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        """Close receipt-backed children then their parent under exact stream guards."""

        return self._settle_parent_for_terminal_intent(
            execution_attempt,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
            commit=True,
        )

    def preflight_parent_for_terminal_intent(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        """Validate exact terminal eligibility without mutating either stream."""

        return self._settle_parent_for_terminal_intent(
            execution_attempt,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
            commit=False,
        )

    def _settle_parent_for_terminal_intent(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
        commit: bool,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        """Validate and optionally close one receipt-backed parent."""

        prepared = self._prepare_parent_settlement(
            execution_attempt,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
        )
        if isinstance(prepared, DirectedEffectSettlementPreBarrierVerdictV1):
            return prepared
        if not commit:
            return self._parent_settlement_preflight(prepared)
        child_close = self._close_parent_settlement_children(
            prepared,
            execution_attempt=execution_attempt,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
        )
        if child_close is not None:
            return child_close
        guarded = self._prepare_parent_settlement_guarded_close(
            prepared,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
        )
        if isinstance(guarded, DirectedEffectSettlementPreBarrierVerdictV1):
            return guarded
        return self._append_parent_settlement_close(
            prepared,
            guarded=guarded,
            execution_attempt=execution_attempt,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
        )

    @staticmethod
    def _parent_settlement_verdict(
        allowed: bool,
        code: _SettlementPreBarrierCode,
        evidence: Mapping[str, object],
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        return DirectedEffectSettlementPreBarrierVerdictV1(
            allowed=allowed,
            code=code,
            evidence=evidence,
        )

    def _prepare_parent_settlement(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> _ParentSettlementPreparation | DirectedEffectSettlementPreBarrierVerdictV1:
        identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(execution_attempt)
        registry = self._load_registry(execution_attempt.workspace, identity)
        classified = self._classify_parent_settlement_registry(
            registry,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
        )
        if isinstance(classified, DirectedEffectSettlementPreBarrierVerdictV1):
            return classified
        binding = classified.open_binding
        if binding is None:
            raise AssertionError("classified open registry requires binding")
        return self._prepare_parent_settlement_operations(
            execution_attempt,
            identity=identity,
            registry=classified,
            binding=binding,
            outcome=outcome,
        )

    def _classify_parent_settlement_registry(
        self,
        registry: _ParentRegistry | DirectedEffectOperationResultV1,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> _ParentRegistry | DirectedEffectSettlementPreBarrierVerdictV1:
        verdict = self._parent_settlement_verdict
        if isinstance(registry, DirectedEffectOperationResultV1):
            if registry.code == "stream_lock_missing":
                return verdict(
                    True, "settlement_parent_registry_clear", {"registry_state": "unenrolled_or_nonexistent"}
                )
            unavailable = registry.code in {
                "fact_stream_unknown_failure",
                "stream_lock_timeout",
                "strict_stream_overload",
            }
            return verdict(
                False,
                "settlement_parent_registry_unavailable" if unavailable else "settlement_parent_registry_invalid",
                {
                    "registry_state": "strict_read_failed",
                    "registry_result_code": registry.code,
                    "registry_evidence": dict(registry.evidence),
                },
            )
        if not registry.bindings_by_id:
            return verdict(
                True,
                "settlement_parent_registry_clear",
                {"registry_state": "strict_empty", "registry_version": registry.registry_version},
            )
        if registry.open_binding is not None:
            return registry
        proof = registry.settlement_close_proof
        if proof is None:
            return verdict(
                False, "settlement_parent_close_proof_required", {"registry_state": "CLOSED_WITHOUT_OUTCOME_PROOF"}
            )
        if proof.get("terminal_intent_hash") != terminal_intent_hash or proof.get("settlement_outcome") != outcome:
            return verdict(
                False,
                "settlement_terminal_intent_conflict",
                {"registry_state": "CLOSED_WITH_DIFFERENT_TERMINAL_INTENT", "close_proof": dict(proof)},
            )
        return verdict(
            True,
            "settlement_parent_registry_clear",
            {"registry_state": "CLOSED_WITH_OUTCOME_PROOF", "close_proof": dict(proof)},
        )

    def _prepare_parent_settlement_operations(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        identity: DirectedEffectParentRegistryIdentityV1,
        registry: _ParentRegistry,
        binding: DirectedEffectParentBindingV1,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    ) -> _ParentSettlementPreparation | DirectedEffectSettlementPreBarrierVerdictV1:
        verdict = self._parent_settlement_verdict
        sealed = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
        ready = registry.ready_inventories_by_binding_id.get(binding.binding_id)
        if sealed is None or ready is None or ready.inventory_hash != sealed.inventory_hash:
            return verdict(
                False, "settlement_parent_close_required", {"registry_state": "OPEN", "reason": "inventory_not_ready"}
            )
        operation_read = self._read_stream(
            execution_attempt.workspace,
            binding.operation_stream_token,
            max_events=_MAX_OPERATION_EVENTS,
            stream_kind="operation",
        )
        if isinstance(operation_read, DirectedEffectOperationResultV1):
            return verdict(
                False,
                "settlement_parent_registry_unavailable",
                {"operation_result_code": operation_read.code, "operation_evidence": dict(operation_read.evidence)},
            )
        reduced = self._reduce_operation_stream(operation_read, binding, target=None)
        if isinstance(reduced, DirectedEffectOperationResultV1):
            return verdict(
                False,
                "settlement_parent_registry_invalid",
                {"operation_result_code": reduced.code, "operation_evidence": dict(reduced.evidence)},
            )
        aggregates = {aggregate.operation.operation_id: aggregate for aggregate in reduced.aggregates}
        if set(aggregates) != set(ready.ordered_operation_ids):
            return verdict(
                False,
                "settlement_directed_effect_unresolved",
                {
                    "reason": "ready_inventory_operation_set_mismatch",
                    "ready_operation_ids": ready.ordered_operation_ids,
                    "observed_operation_ids": tuple(sorted(aggregates)),
                },
            )
        classified = self._classify_parent_settlement_aggregates(
            aggregates, ready.ordered_operation_ids, outcome=outcome
        )
        if isinstance(classified, DirectedEffectSettlementPreBarrierVerdictV1):
            return classified
        receipt_records, close_candidates = classified
        return _ParentSettlementPreparation(identity, registry, binding, reduced, receipt_records, close_candidates)

    def _classify_parent_settlement_aggregates(
        self,
        aggregates: Mapping[str, _Aggregate],
        ordered_operation_ids: tuple[str, ...],
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    ) -> tuple[tuple[dict[str, object], ...], tuple[_Aggregate, ...]] | DirectedEffectSettlementPreBarrierVerdictV1:
        receipts: list[dict[str, object]] = []
        candidates: list[_Aggregate] = []
        for operation_id in ordered_operation_ids:
            aggregate = aggregates[operation_id]
            if aggregate.state in {"RECEIPT_COMMITTED", "CLOSED_BY_PARENT"}:
                classified = self._classify_receipt_backed_settlement_aggregate(aggregate, outcome=outcome)
                if isinstance(classified, DirectedEffectSettlementPreBarrierVerdictV1):
                    return classified
                receipts.append(classified)
                if aggregate.state == "RECEIPT_COMMITTED":
                    candidates.append(aggregate)
                continue
            if aggregate.state == "ABORTED":
                continue
            if aggregate.state == "DEAD_LETTER" and outcome != "completed":
                continue
            code: _SettlementPreBarrierCode = (
                "settlement_effect_outcome_conflict"
                if aggregate.state == "DEAD_LETTER"
                else "settlement_directed_effect_unresolved"
            )
            return self._parent_settlement_verdict(
                False, code, {"operation_id": operation_id, "operation_state": aggregate.state}
            )
        return tuple(receipts), tuple(candidates)

    def _classify_receipt_backed_settlement_aggregate(
        self,
        aggregate: _Aggregate,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    ) -> dict[str, object] | DirectedEffectSettlementPreBarrierVerdictV1:
        transition = self._transition_for_kind(aggregate, "commit_receipt")
        operation_id = aggregate.operation.operation_id
        if transition is None:
            return self._parent_settlement_verdict(
                False,
                "settlement_directed_effect_unresolved",
                {"reason": "receipt_transition_missing", "operation_id": operation_id},
            )
        receipt_outcome = str(transition.descriptor.get("receipt_outcome") or "")
        if outcome == "completed" and receipt_outcome != "succeeded":
            return self._parent_settlement_verdict(
                False,
                "settlement_effect_outcome_conflict",
                {"operation_id": operation_id, "receipt_outcome": receipt_outcome},
            )
        return {
            "operation_id": operation_id,
            "receipt_hash": str(transition.descriptor.get("receipt_hash") or ""),
            "receipt_outcome": receipt_outcome,
        }

    def _parent_settlement_preflight(
        self,
        prepared: _ParentSettlementPreparation,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        return self._parent_settlement_verdict(
            True,
            "settlement_parent_registry_clear",
            {
                "registry_state": "OPEN_READY_FOR_OUTCOME_BOUND_CLOSE",
                "binding_id": prepared.binding.binding_id,
                "operation_source_head_seq": prepared.reduced.source_head_seq,
                "operation_count": len(prepared.reduced.aggregates),
                "receipt_count": len(prepared.receipt_records),
            },
        )

    def _close_parent_settlement_children(
        self,
        prepared: _ParentSettlementPreparation,
        *,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1 | None:
        next_expected_seq = prepared.reduced.source_head_seq + 1
        for close_index, aggregate in enumerate(prepared.close_candidates, start=1):
            closed = self._close_by_parent(
                _CloseDirectedEffectByParentCommandV1(
                    workspace=execution_attempt.workspace,
                    task_id=execution_attempt.task_id,
                    execution_attempt=execution_attempt,
                    parent_binding=prepared.binding,
                    tool_call_id=aggregate.operation.tool_call_id,
                    effect_id=aggregate.operation.effect_id,
                    expected_version=aggregate.version,
                    expected_seq=next_expected_seq,
                    intended_effect_fingerprint=aggregate.intended_effect_fingerprint,
                    policy_verdict_hash=aggregate.policy_verdict_hash,
                    expected_receipt_binding_hash=aggregate.expected_receipt_binding_hash,
                    terminal_intent_hash=terminal_intent_hash,
                    settlement_outcome=outcome,
                )
            )
            if not closed.ok or closed.state != "CLOSED_BY_PARENT":
                return self._parent_settlement_verdict(
                    False,
                    "settlement_parent_close_failed",
                    {"operation_id": aggregate.operation.operation_id, "operation_result_code": closed.code},
                )
            self._after_settlement_child_close(closed, close_index)
            next_expected_seq += 1
        return None

    def _prepare_parent_settlement_guarded_close(
        self,
        settlement: _ParentSettlementPreparation,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> _ParentSettlementGuardedClose | DirectedEffectSettlementPreBarrierVerdictV1:
        try:
            prepared = read_guarded_fact_snapshot(
                ReadGuardedFactSnapshotCommandV1(
                    workspace=settlement.identity.workspace,
                    target_stream=settlement.binding.registry_stream_token,
                    guard_stream=settlement.binding.operation_stream_token,
                )
            )
        except FactStreamError as exc:
            return self._parent_settlement_verdict(
                False,
                "settlement_parent_close_failed",
                {"fact_stream_code": exc.code, "fact_stream_details": dict(exc.details)},
            )
        registry = self._reduce_registry_from_read(
            settlement.identity, _StreamRead(prepared.target_records(), prepared.proof.target_head_seq)
        )
        if isinstance(registry, DirectedEffectOperationResultV1):
            return self._parent_settlement_verdict(False, "settlement_parent_registry_invalid", dict(registry.evidence))
        if registry.open_binding is None:
            return self._classify_existing_parent_close(
                registry, outcome=outcome, terminal_intent_hash=terminal_intent_hash
            )
        reduced = self._reduce_operation_stream(
            _StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq), settlement.binding, target=None
        )
        if isinstance(reduced, DirectedEffectOperationResultV1):
            return self._parent_settlement_verdict(False, "settlement_parent_registry_invalid", dict(reduced.evidence))
        if any(aggregate.state not in _TERMINAL_STATES for aggregate in reduced.aggregates):
            return self._parent_settlement_verdict(
                False, "settlement_directed_effect_unresolved", {"reason": "non_terminal_operation_after_child_close"}
            )
        return _ParentSettlementGuardedClose(prepared, registry, reduced)

    def _classify_existing_parent_close(
        self,
        registry: _ParentRegistry,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        proof = registry.settlement_close_proof
        matches = (
            proof is not None
            and proof.get("terminal_intent_hash") == terminal_intent_hash
            and proof.get("settlement_outcome") == outcome
        )
        if matches:
            return self._parent_settlement_verdict(
                True, "settlement_parent_registry_clear", {"close_proof": dict(proof or {})}
            )
        return self._parent_settlement_verdict(
            False, "settlement_terminal_intent_conflict", {"close_proof": dict(proof or {})}
        )

    def _append_parent_settlement_close(
        self,
        settlement: _ParentSettlementPreparation,
        *,
        guarded: _ParentSettlementGuardedClose,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        command = self._parent_settlement_close_command(
            settlement,
            guarded=guarded,
            execution_attempt=execution_attempt,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
        )
        try:
            appended = append_if_guarded_snapshot(command)
            self._after_settlement_parent_close(appended)
        except FactStreamError as exc:
            return self._reconcile_parent_settlement_append_error(
                settlement, outcome=outcome, terminal_intent_hash=terminal_intent_hash, error=exc
            )
        refreshed = self._load_registry(execution_attempt.workspace, settlement.identity)
        if isinstance(refreshed, DirectedEffectOperationResultV1) or refreshed.settlement_close_proof is None:
            return self._parent_settlement_verdict(
                False,
                "settlement_parent_close_failed",
                {"reason": "parent_close_not_reconstructable", "event_id": appended.event_id},
            )
        return self._parent_settlement_verdict(
            True,
            "settlement_parent_registry_clear",
            {
                "registry_state": "CLOSED_WITH_OUTCOME_PROOF",
                "close_proof": dict(refreshed.settlement_close_proof),
                "event_id": appended.event_id,
            },
        )

    def _parent_settlement_close_command(
        self,
        settlement: _ParentSettlementPreparation,
        *,
        guarded: _ParentSettlementGuardedClose,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
    ) -> AppendIfGuardedSnapshotCommandV1:
        receipts = settlement.receipt_records
        receipt_summary_hash = _hash_token(tuple(sorted(receipts, key=lambda item: str(item["operation_id"]))))
        close_evidence_hash = _hash_token(
            {
                "terminal_intent_hash": terminal_intent_hash,
                "settlement_outcome": outcome,
                "operation_source_head_seq": guarded.prepared.proof.guard_head_seq,
                "receipt_summary_hash": receipt_summary_hash,
            }
        )
        payload: dict[str, object] = {
            "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2,
            "stable_registry_identity": settlement.identity.to_record(),
            "previous_version": guarded.final_registry.registry_version,
            "version": guarded.final_registry.registry_version + 1,
            "parent_sequence": settlement.binding.parent_sequence,
            "binding_id": settlement.binding.binding_id,
            "close_evidence_ref": f"settlement://{terminal_intent_hash}",
            "close_evidence_hash": close_evidence_hash,
            "actor": "runtime.task_runtime.settlement",
            "settlement_outcome": outcome,
            "terminal_intent_hash": terminal_intent_hash,
            "operation_source_head_seq": guarded.prepared.proof.guard_head_seq,
            "receipt_summary_hash": receipt_summary_hash,
            "receipt_count": len(receipts),
            "failed_receipt_count": sum(1 for item in receipts if item["receipt_outcome"] == "failed"),
            "dead_letter_count": sum(
                1 for aggregate in guarded.final_reduced.aggregates if aggregate.state == "DEAD_LETTER"
            ),
            "aborted_count": sum(1 for aggregate in guarded.final_reduced.aggregates if aggregate.state == "ABORTED"),
        }
        return AppendIfGuardedSnapshotCommandV1(
            snapshot_proof=guarded.prepared.proof,
            event=GuardedFactEventV1(
                event_type=_PARENT_CLOSED_EVENT_TYPE,
                source="runtime.task_runtime",
                payload=payload,
                aggregate_id=str(execution_attempt.task_id),
                correlation_id=close_evidence_hash,
            ),
            idempotency_key=close_evidence_hash,
        )

    def _reconcile_parent_settlement_append_error(
        self,
        settlement: _ParentSettlementPreparation,
        *,
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1,
        terminal_intent_hash: str,
        error: FactStreamError,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        refreshed = self._load_registry(settlement.identity.workspace, settlement.identity)
        proof = None if isinstance(refreshed, DirectedEffectOperationResultV1) else refreshed.settlement_close_proof
        matches = (
            proof is not None
            and proof.get("terminal_intent_hash") == terminal_intent_hash
            and proof.get("settlement_outcome") == outcome
        )
        if matches:
            return self._parent_settlement_verdict(
                True,
                "settlement_parent_registry_clear",
                {"close_proof": dict(proof or {}), "reconciled_after_error": error.code},
            )
        return self._parent_settlement_verdict(
            False,
            "settlement_parent_close_failed",
            {"fact_stream_code": error.code, "fact_stream_details": dict(error.details)},
        )

    def admit_parent_with_validated_authority(
        self,
        command: AdmitDirectedEffectParentCommandV1,
    ) -> DirectedEffectOperationResultV1:
        """Append one registry fact for a caller-validated, lock-held attempt.

        ``TaskRuntimeService`` is the only production caller. It owns both
        session locks and validates the complete active attempt before entering
        this repository method. No session validation or lock acquisition is
        permitted here.
        """

        registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(command.execution_attempt)
        registry = self._load_registry(command.workspace, registry_identity)
        if isinstance(registry, DirectedEffectOperationResultV1):
            return registry
        existing = registry.admissions_by_idempotency_key.get(command.admission_idempotency_key)
        if existing is not None:
            return self._parent_replay_result(command, registry, existing)
        if registry.open_binding is not None:
            return self._parent_open_conflict(registry, command.admission_idempotency_key)
        if command.expected_version != registry.registry_version:
            return self._parent_registry_conflict(
                "parent_registry_version_conflict",
                registry,
                {
                    "expected_version": command.expected_version,
                    "fresh_registry_version": registry.registry_version,
                },
            )
        if command.expected_seq != registry.next_expected_seq:
            return self._parent_registry_conflict(
                "parent_registry_expected_seq_conflict",
                registry,
                {
                    "expected_seq": command.expected_seq,
                    "fresh_next_expected_seq": registry.next_expected_seq,
                },
            )
        canonical_event = self._parent_event_canonical(command, registry)
        payload = dict(canonical_event)
        payload["recorded_at"] = datetime.now(timezone.utc).isoformat()
        append_attempt_id = uuid.uuid4().hex
        try:
            appended = append_fact_event(
                AppendFactEventCommandV1(
                    workspace=command.workspace,
                    stream=registry.stream_token,
                    event_type=_PARENT_ADMITTED_EVENT_TYPE,
                    payload=payload,
                    source="runtime.task_runtime",
                    run_id=registry.identity.run_id or None,
                    task_id=str(command.task_id),
                    correlation_id=append_attempt_id,
                    idempotency_key=_registry_fact_idempotency_key(
                        registry.identity,
                        command.admission_idempotency_key,
                    ),
                    expected_seq=command.expected_seq,
                    durability="fsync",
                    strict_integrity=True,
                )
            )
        except FactStreamError as exc:
            return self._reconcile_parent_append(command, registry.identity, canonical_event, exc)
        refreshed = self._load_registry(command.workspace, registry.identity)
        if isinstance(refreshed, DirectedEffectOperationResultV1):
            return refreshed
        committed = refreshed.admissions_by_idempotency_key.get(command.admission_idempotency_key)
        if (
            committed is None
            or dict(committed.canonical_event) != dict(canonical_event)
            or committed.event_id != appended.event_id
        ):
            return self._parent_registry_conflict(
                "stream_cas_exhausted",
                refreshed,
                {"append_event_id": appended.event_id},
            )
        return DirectedEffectOperationResultV1(
            ok=True,
            code="parent_admitted",
            parent_binding=committed.binding,
            parent_registry=self._registry_projection(refreshed),
            evidence={
                "event_id": appended.event_id,
                "appended_seq": appended.appended_seq,
                "authoritative_append": True,
                "registry_version": refreshed.registry_version,
                "source_head_seq": refreshed.source_head_seq,
            },
        )

    def get_parent_registry(
        self,
        query: GetDirectedEffectParentRegistryQueryV1,
    ) -> DirectedEffectParentRegistryResultV1:
        identity_failure = self._command_identity_failure(
            workspace=query.workspace,
            task_id=query.task_id,
            execution_attempt=query.execution_attempt,
        )
        if identity_failure is not None:
            return self._registry_query_failure(identity_failure)
        identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(query.execution_attempt)
        registry = self._load_registry(query.workspace, identity)
        if isinstance(registry, DirectedEffectOperationResultV1):
            return self._registry_query_failure(registry)
        return DirectedEffectParentRegistryResultV1(
            ok=True,
            code="parent_registry_found",
            registry=self._registry_projection(registry),
            evidence={"source_head_seq": registry.source_head_seq},
        )

    def seal_inventory(
        self,
        command: SealDirectedEffectInventoryCommandV1,
    ) -> DirectedEffectInventoryResultV1:
        """Persist one registry-owned inventory under an empty operation-stream guard."""

        append_attempt_nonce = _new_append_attempt_nonce()
        identity_failure = self._command_identity_failure(
            workspace=command.workspace,
            task_id=command.task_id,
            execution_attempt=command.execution_attempt,
        )
        if identity_failure is not None:
            return self._inventory_failure_from_operation(identity_failure)
        expected_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(command.execution_attempt)
        drift_codes: list[str] = []
        last_snapshot: GuardedFactSnapshotV1 | None = None
        for attempt_number in range(1, _MAX_GUARDED_ATTEMPTS + 1):
            attempt_failure = self._guarded_attempt_failure(
                command,
                attempt_number=attempt_number,
                phase="prepare",
                drift_codes=drift_codes,
            )
            if attempt_failure is not None:
                return self._inventory_failure_from_operation(attempt_failure)
            prepared = self._prepare_inventory_guarded_snapshot(command, expected_identity)
            if isinstance(prepared, DirectedEffectOperationResultV1):
                return self._inventory_failure_from_operation(prepared)
            last_snapshot = prepared
            validated = self._validated_parent_binding_from_registry(
                command,
                expected_identity=expected_identity,
                registry_read=_StreamRead(prepared.target_records(), prepared.proof.target_head_seq),
                require_open=False,
            )
            if isinstance(validated, DirectedEffectOperationResultV1):
                return self._inventory_failure_from_operation(validated)
            binding, registry = validated
            requested_members = tuple(_inventory_member(binding, intent) for intent in command.intents)
            requested_inventory_hash = _inventory_hash(binding.binding_id, requested_members)
            existing = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
            if existing is not None:
                replay_authority_failure = self._guarded_attempt_failure(
                    command,
                    attempt_number=attempt_number,
                    phase="replay",
                    drift_codes=drift_codes,
                )
                if replay_authority_failure is not None:
                    return self._inventory_failure_from_operation(replay_authority_failure)
                if (
                    existing.members != requested_members
                    or existing.inventory_hash != requested_inventory_hash
                    or existing.actor != command.actor
                ):
                    return self._inventory_failure(
                        "inventory_seal_conflict",
                        {
                            "binding_id": binding.binding_id,
                            "sealed_inventory_hash": existing.inventory_hash,
                            "requested_inventory_hash": requested_inventory_hash,
                        },
                    )
                operation_projection = self._inventory_operation_projection(
                    binding=binding,
                    sealed=existing,
                    operation_read=_StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq),
                )
                if isinstance(operation_projection, DirectedEffectOperationResultV1):
                    return self._inventory_failure_from_operation(operation_projection)
                return self._inventory_success(
                    command,
                    registry,
                    existing,
                    operation_projection=operation_projection,
                    code="inventory_seal_idempotent_replay",
                    evidence={
                        "event_id": existing.event_id,
                        "event_seq": existing.seq,
                        "authoritative_append": False,
                        "idempotent_replay": True,
                    },
                )
            if registry.open_binding is None or registry.open_binding.binding_id != binding.binding_id:
                return self._inventory_failure_from_operation(
                    self._parent_registry_conflict(
                        "parent_closed",
                        registry,
                        {
                            "requested_binding_id": binding.binding_id,
                            "reason": "binding_is_closed_historical_parent",
                        },
                        binding=binding,
                    )
                )
            if prepared.proof.guard_head_seq != command.expected_operation_head_seq:
                return self._inventory_failure(
                    "inventory_requires_empty_operation_stream",
                    {
                        "expected_operation_head_seq": command.expected_operation_head_seq,
                        "fresh_operation_head_seq": prepared.proof.guard_head_seq,
                        "binding_id": binding.binding_id,
                    },
                )
            if command.expected_registry_version != registry.registry_version:
                return self._inventory_failure_from_operation(
                    self._parent_registry_conflict(
                        "parent_registry_version_conflict",
                        registry,
                        {
                            "expected_version": command.expected_registry_version,
                            "fresh_registry_version": registry.registry_version,
                        },
                        binding=binding,
                    )
                )
            if command.expected_registry_seq != registry.next_expected_seq:
                return self._inventory_failure_from_operation(
                    self._parent_registry_conflict(
                        "parent_registry_expected_seq_conflict",
                        registry,
                        {
                            "expected_seq": command.expected_registry_seq,
                            "fresh_next_expected_seq": registry.next_expected_seq,
                        },
                        binding=binding,
                    )
                )

            payload = self._inventory_seal_payload(
                registry=registry,
                binding=binding,
                members=requested_members,
                inventory_hash=requested_inventory_hash,
                actor=command.actor,
            )
            fact_idempotency_key = (
                "deo_inventory_seal_v1_"
                + _hash_token(
                    {
                        "payload": payload,
                        "append_attempt_nonce": append_attempt_nonce,
                    }
                )[:48]
            )
            guarded_command = AppendIfGuardedSnapshotCommandV1(
                snapshot_proof=prepared.proof,
                event=GuardedFactEventV1(
                    event_type=_PARENT_INVENTORY_SEALED_EVENT_TYPE,
                    source="runtime.task_runtime",
                    payload=payload,
                    aggregate_id=str(command.task_id),
                    correlation_id=fact_idempotency_key,
                ),
                idempotency_key=fact_idempotency_key,
            )
            appended: GuardedFactAppendedV1 | None = None
            try:
                self._after_guarded_prepare(prepared)
                commit_authority_failure = self._guarded_attempt_failure(
                    command,
                    attempt_number=attempt_number,
                    phase="commit",
                    drift_codes=drift_codes,
                )
                if commit_authority_failure is not None:
                    return self._inventory_failure_from_operation(commit_authority_failure)
                appended = append_if_guarded_snapshot(guarded_command)
                seam_receipt = self._after_guarded_commit(appended)
                if seam_receipt is not None:
                    appended = seam_receipt
            except FactStreamError as exc:
                if exc.code in _GUARDED_REPREPARE_DRIFT_CODES:
                    drift_codes.append(exc.code)
                    self._after_guarded_drift(exc, attempt_number)
                    reprepare_authority_failure = self._guarded_attempt_failure(
                        command,
                        attempt_number=attempt_number,
                        phase="reprepare",
                        drift_codes=drift_codes,
                    )
                    if reprepare_authority_failure is not None:
                        return self._inventory_failure_from_operation(reprepare_authority_failure)
                    continue
                return self._reconcile_inventory_append(
                    command=command,
                    expected_identity=expected_identity,
                    members=requested_members,
                    inventory_hash=requested_inventory_hash,
                    guarded_attempt=attempt_number,
                    exc=exc,
                    receipt=appended,
                    guarded_command=guarded_command,
                )
            if appended is None:
                return self._inventory_failure(
                    "guarded_receipt_mismatch",
                    {
                        "reason": "guarded_commit_returned_without_receipt",
                        "guarded_attempt": attempt_number,
                        "binding_id": binding.binding_id,
                    },
                )
            return self._confirm_inventory_append(
                command=command,
                expected_identity=expected_identity,
                members=requested_members,
                inventory_hash=requested_inventory_hash,
                guarded_attempt=attempt_number,
                receipt=appended,
                guarded_command=guarded_command,
            )
        assert last_snapshot is not None
        return self._inventory_failure(
            "guarded_reprepare_exhausted",
            {
                "attempts_total": _MAX_GUARDED_ATTEMPTS,
                "reprepare_count": _MAX_GUARDED_ATTEMPTS - 1,
                "drift_codes": tuple(drift_codes),
                "registry_head_seq": last_snapshot.proof.target_head_seq,
                "operation_head_seq": last_snapshot.proof.guard_head_seq,
                "binding_id": command.parent_binding.binding_id,
            },
        )

    def finalize_inventory(
        self,
        command: FinalizeDirectedEffectInventoryAdmissionCommandV1,
    ) -> DirectedEffectInventoryResultV1:
        """Persist readiness only for an exact sealed version-one intent set."""

        append_attempt_nonce = _new_append_attempt_nonce()
        identity_failure = self._command_identity_failure(
            workspace=command.workspace,
            task_id=command.task_id,
            execution_attempt=command.execution_attempt,
        )
        if identity_failure is not None:
            return self._inventory_failure_from_operation(identity_failure)
        expected_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(command.execution_attempt)
        drift_codes: list[str] = []
        last_snapshot: GuardedFactSnapshotV1 | None = None
        for attempt_number in range(1, _MAX_GUARDED_ATTEMPTS + 1):
            attempt_failure = self._guarded_attempt_failure(
                command,
                attempt_number=attempt_number,
                phase="prepare",
                drift_codes=drift_codes,
            )
            if attempt_failure is not None:
                return self._inventory_failure_from_operation(attempt_failure)
            prepared = self._prepare_inventory_guarded_snapshot(command, expected_identity)
            if isinstance(prepared, DirectedEffectOperationResultV1):
                return self._inventory_failure_from_operation(prepared)
            last_snapshot = prepared
            validated = self._validated_parent_binding_from_registry(
                command,
                expected_identity=expected_identity,
                registry_read=_StreamRead(prepared.target_records(), prepared.proof.target_head_seq),
                require_open=False,
            )
            if isinstance(validated, DirectedEffectOperationResultV1):
                return self._inventory_failure_from_operation(validated)
            binding, registry = validated
            sealed = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
            if sealed is None:
                return self._inventory_failure("inventory_not_sealed", {"binding_id": binding.binding_id})
            if command.inventory_hash != sealed.inventory_hash:
                return self._inventory_failure(
                    "inventory_seal_conflict",
                    {
                        "binding_id": binding.binding_id,
                        "sealed_inventory_hash": sealed.inventory_hash,
                        "requested_inventory_hash": command.inventory_hash,
                    },
                )
            ready = registry.ready_inventories_by_binding_id.get(binding.binding_id)
            if ready is not None:
                replay_projection = self._validate_ready_inventory_prefix(
                    binding=binding,
                    sealed=sealed,
                    ready=ready,
                    operation_events=prepared.guard_records(),
                )
                if isinstance(replay_projection, DirectedEffectOperationResultV1):
                    return self._inventory_failure_from_operation(replay_projection)
                current_projection = self._inventory_operation_projection(
                    binding=binding,
                    sealed=sealed,
                    operation_read=_StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq),
                )
                if isinstance(current_projection, DirectedEffectOperationResultV1):
                    return self._inventory_failure_from_operation(current_projection)
                ready_current_failure = self._ready_current_inventory_failure(current_projection)
                if ready_current_failure is not None:
                    return self._inventory_failure_from_operation(ready_current_failure)
                replay_authority_failure = self._guarded_attempt_failure(
                    command,
                    attempt_number=attempt_number,
                    phase="replay",
                    drift_codes=drift_codes,
                )
                if replay_authority_failure is not None:
                    return self._inventory_failure_from_operation(replay_authority_failure)
                return self._inventory_success(
                    command,
                    registry,
                    sealed,
                    operation_projection=current_projection,
                    code="inventory_ready_idempotent_replay",
                    evidence={
                        "event_id": ready.event_id,
                        "event_seq": ready.seq,
                        "admission_set_hash": ready.admission_set_hash,
                        "authoritative_append": False,
                        "idempotent_replay": True,
                    },
                )
            if registry.open_binding is None or registry.open_binding.binding_id != binding.binding_id:
                return self._inventory_failure_from_operation(
                    self._parent_registry_conflict(
                        "parent_closed",
                        registry,
                        {"requested_binding_id": binding.binding_id},
                        binding=binding,
                    )
                )
            operation_read = _StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq)
            admission = self._validate_exact_inventory_admission(
                binding=binding,
                sealed=sealed,
                operation_read=operation_read,
            )
            if isinstance(admission, DirectedEffectOperationResultV1):
                return self._inventory_failure_from_operation(admission)
            if command.expected_operation_head_seq != prepared.proof.guard_head_seq:
                return self._inventory_failure(
                    "stream_expected_seq_conflict",
                    {
                        "expected_operation_head_seq": command.expected_operation_head_seq,
                        "fresh_operation_head_seq": prepared.proof.guard_head_seq,
                    },
                )
            if command.expected_registry_version != registry.registry_version:
                return self._inventory_failure_from_operation(
                    self._parent_registry_conflict(
                        "parent_registry_version_conflict",
                        registry,
                        {
                            "expected_version": command.expected_registry_version,
                            "fresh_registry_version": registry.registry_version,
                        },
                        binding=binding,
                    )
                )
            if command.expected_registry_seq != registry.next_expected_seq:
                return self._inventory_failure_from_operation(
                    self._parent_registry_conflict(
                        "parent_registry_expected_seq_conflict",
                        registry,
                        {
                            "expected_seq": command.expected_registry_seq,
                            "fresh_next_expected_seq": registry.next_expected_seq,
                        },
                        binding=binding,
                    )
                )
            ordered_operation_ids = tuple(member.operation_id for member in sealed.members)
            admission_hash = _admission_set_hash(
                binding_id=binding.binding_id,
                inventory_hash=sealed.inventory_hash,
                ordered_operation_ids=ordered_operation_ids,
                operation_source_head_seq=prepared.proof.guard_head_seq,
            )
            payload = self._inventory_ready_payload(
                registry=registry,
                binding=binding,
                sealed=sealed,
                ordered_operation_ids=ordered_operation_ids,
                admission_set_hash=admission_hash,
                operation_source_head_seq=prepared.proof.guard_head_seq,
                actor=command.actor,
            )
            fact_idempotency_key = (
                "deo_inventory_ready_v1_"
                + _hash_token(
                    {
                        "payload": payload,
                        "append_attempt_nonce": append_attempt_nonce,
                    }
                )[:48]
            )
            guarded_command = AppendIfGuardedSnapshotCommandV1(
                snapshot_proof=prepared.proof,
                event=GuardedFactEventV1(
                    event_type=_PARENT_INVENTORY_READY_EVENT_TYPE,
                    source="runtime.task_runtime",
                    payload=payload,
                    aggregate_id=str(command.task_id),
                    correlation_id=fact_idempotency_key,
                ),
                idempotency_key=fact_idempotency_key,
            )
            appended: GuardedFactAppendedV1 | None = None
            try:
                self._after_guarded_prepare(prepared)
                commit_failure = self._guarded_attempt_failure(
                    command,
                    attempt_number=attempt_number,
                    phase="commit",
                    drift_codes=drift_codes,
                )
                if commit_failure is not None:
                    return self._inventory_failure_from_operation(commit_failure)
                appended = append_if_guarded_snapshot(guarded_command)
                seam_receipt = self._after_guarded_commit(appended)
                if seam_receipt is not None:
                    appended = seam_receipt
            except FactStreamError as exc:
                if exc.code in _GUARDED_REPREPARE_DRIFT_CODES:
                    drift_codes.append(exc.code)
                    self._after_guarded_drift(exc, attempt_number)
                    reprepare_failure = self._guarded_attempt_failure(
                        command,
                        attempt_number=attempt_number,
                        phase="reprepare",
                        drift_codes=drift_codes,
                    )
                    if reprepare_failure is not None:
                        return self._inventory_failure_from_operation(reprepare_failure)
                    continue
                return self._confirm_inventory_ready_append(
                    command=command,
                    expected_identity=expected_identity,
                    receipt=appended,
                    guarded_command=guarded_command,
                    guarded_attempt=attempt_number,
                    reconciliation_error=exc,
                )
            return self._confirm_inventory_ready_append(
                command=command,
                expected_identity=expected_identity,
                receipt=appended,
                guarded_command=guarded_command,
                guarded_attempt=attempt_number,
            )
        assert last_snapshot is not None
        return self._inventory_failure(
            "guarded_reprepare_exhausted",
            {
                "attempts_total": _MAX_GUARDED_ATTEMPTS,
                "drift_codes": tuple(drift_codes),
                "registry_head_seq": last_snapshot.proof.target_head_seq,
                "operation_head_seq": last_snapshot.proof.guard_head_seq,
            },
        )

    def get_inventory(
        self,
        query: GetDirectedEffectInventoryQueryV1,
    ) -> DirectedEffectInventoryResultV1:
        """Return one strict current inventory projection without mutation."""

        identity_failure = self._command_identity_failure(
            workspace=query.workspace,
            task_id=query.task_id,
            execution_attempt=query.execution_attempt,
        )
        if identity_failure is not None:
            return self._inventory_failure_from_operation(identity_failure)
        expected_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(query.execution_attempt)
        prepared = self._prepare_inventory_guarded_snapshot(query, expected_identity)
        if isinstance(prepared, DirectedEffectOperationResultV1):
            return self._inventory_failure_from_operation(prepared)
        validated = self._validated_parent_binding_from_registry(
            query,
            expected_identity=expected_identity,
            registry_read=_StreamRead(prepared.target_records(), prepared.proof.target_head_seq),
            require_open=False,
        )
        if isinstance(validated, DirectedEffectOperationResultV1):
            return self._inventory_failure_from_operation(validated)
        binding, registry = validated
        sealed = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
        if sealed is None:
            return self._inventory_failure("inventory_not_sealed", {"binding_id": binding.binding_id})
        ready = registry.ready_inventories_by_binding_id.get(binding.binding_id)
        if ready is not None:
            prefix = self._validate_ready_inventory_prefix(
                binding=binding,
                sealed=sealed,
                ready=ready,
                operation_events=prepared.guard_records(),
            )
            if isinstance(prefix, DirectedEffectOperationResultV1):
                return self._inventory_failure_from_operation(prefix)
        current = self._inventory_operation_projection(
            binding=binding,
            sealed=sealed,
            operation_read=_StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq),
        )
        if isinstance(current, DirectedEffectOperationResultV1):
            return self._inventory_failure_from_operation(current)
        ready_current_failure = self._ready_current_inventory_failure(current) if ready is not None else None
        if ready_current_failure is not None:
            return self._inventory_failure_from_operation(ready_current_failure)
        return self._inventory_success(
            query,
            registry,
            sealed,
            operation_projection=current,
            code="inventory_observed",
            evidence={
                "parent_registry_source_head_seq": registry.source_head_seq,
                "operation_source_head_seq": current.source_head_seq,
            },
        )

    def admit(self, command: AdmitDirectedEffectOperationCommandV1) -> DirectedEffectOperationResultV1:
        return self._mutate(command, kind="admit", target="INTENT_COMMITTED", allowed_from=frozenset({None}))

    def claim(self, command: ClaimDirectedEffectCommandV1) -> DirectedEffectOperationResultV1:
        return self._mutate(
            command,
            kind="claim",
            target="EFFECT_STARTED",
            allowed_from=frozenset({"INTENT_COMMITTED"}),
        )

    def abort(self, command: AbortDirectedEffectOperationCommandV1) -> DirectedEffectOperationResultV1:
        return self._mutate(
            command,
            kind="abort",
            target="ABORTED",
            allowed_from=frozenset({"INTENT_COMMITTED"}),
        )

    def commit_receipt(self, command: CommitDirectedEffectReceiptCommandV1) -> DirectedEffectOperationResultV1:
        return self._mutate(
            command,
            kind="commit_receipt",
            target="RECEIPT_COMMITTED",
            allowed_from=frozenset({"EFFECT_STARTED", "RECOVERY_PENDING"}),
        )

    def mark_recovery_pending(
        self,
        command: MarkDirectedEffectRecoveryPendingCommandV1,
    ) -> DirectedEffectOperationResultV1:
        return self._mutate(
            command,
            kind="mark_recovery_pending",
            target="RECOVERY_PENDING",
            allowed_from=frozenset({"EFFECT_STARTED"}),
        )

    def dead_letter(
        self,
        command: DeadLetterDirectedEffectOperationCommandV1,
    ) -> DirectedEffectOperationResultV1:
        return self._mutate(
            command,
            kind="dead_letter",
            target="DEAD_LETTER",
            allowed_from=frozenset({"RECOVERY_PENDING"}),
        )

    def _close_by_parent(
        self,
        command: _CloseDirectedEffectByParentCommandV1,
    ) -> DirectedEffectOperationResultV1:
        # TaskRuntime owns this private transition and calls it while holding
        # both execution-session locks. Re-entering the public attempt
        # validator would reacquire the cooperative session-file lock and
        # self-deadlock. The guarded FactStream CAS checks remain mandatory.
        return self._mutate(
            command,
            kind="close_by_parent",
            target="CLOSED_BY_PARENT",
            allowed_from=frozenset({"RECEIPT_COMMITTED"}),
            attempt_authority_lock_held=True,
        )

    def reconcile_ambiguous_started_operations(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        actor: str,
        reason: str,
        max_operations: int,
        deadline_monotonic: float,
    ) -> _DirectedEffectRecoveryRepositorySweep:
        """Converge ambiguous operations without re-executing any effect."""

        prepared = self._prepare_ambiguous_recovery_sweep(
            execution_attempt,
            max_operations=max_operations,
            deadline_monotonic=deadline_monotonic,
        )
        if isinstance(prepared, _DirectedEffectRecoveryRepositorySweep):
            return prepared
        results: list[DirectedEffectOperationResultV1] = []
        scanned = 0
        for member in prepared.members:
            if time.monotonic() >= deadline_monotonic:
                return self._recovery_deadline_sweep(results, stage="before_operation_read", scanned=scanned)
            current = self.get(
                GetDirectedEffectOperationQueryV1(
                    workspace=execution_attempt.workspace,
                    task_id=execution_attempt.task_id,
                    execution_attempt=execution_attempt,
                    parent_binding=prepared.binding,
                    tool_call_id=member.tool_call_id,
                    effect_id=member.effect_id,
                )
            )
            scanned += 1
            member_sweep = self._reconcile_ambiguous_recovery_member(
                execution_attempt,
                binding=prepared.binding,
                member=member,
                current=current,
                actor=actor,
                reason=reason,
                scanned=scanned,
                deadline_monotonic=deadline_monotonic,
            )
            results.extend(member_sweep.results)
            if self._recovery_sweep_hit_deadline(member_sweep):
                return _DirectedEffectRecoveryRepositorySweep(tuple(results), scanned)
        return _DirectedEffectRecoveryRepositorySweep(tuple(results), scanned)

    def _prepare_ambiguous_recovery_sweep(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        max_operations: int,
        deadline_monotonic: float,
    ) -> _DirectedEffectRecoverySweepPreparation | _DirectedEffectRecoveryRepositorySweep:
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep((), stage="before_registry_read", scanned=0)
        identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(execution_attempt)
        registry = self._load_registry(execution_attempt.workspace, identity)
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep((), stage="after_registry_read", scanned=0)
        if isinstance(registry, DirectedEffectOperationResultV1):
            return _DirectedEffectRecoveryRepositorySweep(
                () if registry.code == "stream_lock_missing" else (registry,), 0
            )
        binding = registry.open_binding
        if binding is None:
            return _DirectedEffectRecoveryRepositorySweep((), 0)
        sealed = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
        if sealed is None:
            return _DirectedEffectRecoveryRepositorySweep((), 0)
        if len(sealed.members) > max_operations:
            failure = DirectedEffectOperationResultV1(
                ok=False,
                code="strict_stream_overload",
                evidence={
                    "reason": "recovery_sweep_operation_limit_exceeded",
                    "operation_count": len(sealed.members),
                    "max_operations": max_operations,
                },
            )
            return _DirectedEffectRecoveryRepositorySweep((failure,), 0)
        return _DirectedEffectRecoverySweepPreparation(binding, sealed.members)

    def _reconcile_ambiguous_recovery_member(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        binding: DirectedEffectParentBindingV1,
        member: DirectedEffectInventoryMemberV1,
        current: DirectedEffectOperationResultV1,
        actor: str,
        reason: str,
        scanned: int,
        deadline_monotonic: float,
    ) -> _DirectedEffectRecoveryRepositorySweep:
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep(
                (), operation=current.operation, stage="after_operation_read", scanned=scanned
            )
        if not current.ok:
            results = () if current.code == "operation_not_found" else (current,)
            return _DirectedEffectRecoveryRepositorySweep(results, scanned)
        if current.operation is None:
            return _DirectedEffectRecoveryRepositorySweep((), scanned)
        if current.state == "DEAD_LETTER":
            return self._project_recovery_facts_with_deadline(
                execution_attempt.workspace,
                binding=binding,
                operation=current.operation,
                stage="recovery_projection",
                scanned=scanned,
                deadline_monotonic=deadline_monotonic,
            )
        if current.state not in {"EFFECT_STARTED", "RECOVERY_PENDING"}:
            return _DirectedEffectRecoveryRepositorySweep((), scanned)
        return self._recover_started_or_pending_operation(
            execution_attempt,
            binding=binding,
            member=member,
            current=current,
            actor=actor,
            reason=reason,
            scanned=scanned,
            deadline_monotonic=deadline_monotonic,
        )

    def _recover_started_or_pending_operation(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        binding: DirectedEffectParentBindingV1,
        member: DirectedEffectInventoryMemberV1,
        current: DirectedEffectOperationResultV1,
        actor: str,
        reason: str,
        scanned: int,
        deadline_monotonic: float,
    ) -> _DirectedEffectRecoveryRepositorySweep:
        results: list[DirectedEffectOperationResultV1] = []
        if current.state == "RECOVERY_PENDING":
            projection = self._project_recovery_facts_with_deadline(
                execution_attempt.workspace,
                binding=binding,
                operation=cast(DirectedEffectOperationIdentityV1, current.operation),
                stage="pending_projection",
                scanned=scanned,
                deadline_monotonic=deadline_monotonic,
            )
            results.extend(projection.results)
            if self._recovery_sweep_hit_deadline(projection) or (
                len(projection.results) == 1 and not projection.results[0].ok
            ):
                return _DirectedEffectRecoveryRepositorySweep(tuple(results), scanned)
        cursor = self._recovery_cursor(current)
        if isinstance(cursor, DirectedEffectOperationResultV1):
            results.append(cursor)
            return _DirectedEffectRecoveryRepositorySweep(tuple(results), scanned)
        if current.state == "EFFECT_STARTED":
            pending = self._commit_restart_recovery_pending(
                execution_attempt,
                binding=binding,
                member=member,
                current=current,
                cursor=cursor,
                actor=actor,
                reason=reason,
                scanned=scanned,
                deadline_monotonic=deadline_monotonic,
            )
            results.extend(pending.results)
            if (
                self._recovery_sweep_hit_deadline(pending)
                or not pending.results[-1].ok
                or pending.results[-1].state != "RECOVERY_PENDING"
            ):
                return _DirectedEffectRecoveryRepositorySweep(tuple(results), scanned)
            cursor = _DirectedEffectRecoveryCursor(
                pending.results[-1].version, cursor.expected_seq + 1, "RECOVERY_PENDING"
            )
        dead_letter = self._commit_restart_dead_letter(
            execution_attempt,
            binding=binding,
            member=member,
            operation=cast(DirectedEffectOperationIdentityV1, current.operation),
            cursor=cursor,
            actor=actor,
            reason=reason,
            scanned=scanned,
            deadline_monotonic=deadline_monotonic,
        )
        results.extend(dead_letter.results)
        return _DirectedEffectRecoveryRepositorySweep(tuple(results), scanned)

    def _project_recovery_facts_with_deadline(
        self,
        workspace: str,
        *,
        binding: DirectedEffectParentBindingV1,
        operation: DirectedEffectOperationIdentityV1,
        stage: str,
        scanned: int,
        deadline_monotonic: float,
    ) -> _DirectedEffectRecoveryRepositorySweep:
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep((), operation=operation, stage=f"before_{stage}", scanned=scanned)
        projected = self._recovery_fact_projection_results(workspace=workspace, binding=binding, operation=operation)
        results = (projected,) if isinstance(projected, DirectedEffectOperationResultV1) else projected
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep(results, operation=operation, stage=f"after_{stage}", scanned=scanned)
        return _DirectedEffectRecoveryRepositorySweep(results, scanned)

    def _recovery_cursor(
        self,
        current: DirectedEffectOperationResultV1,
    ) -> _DirectedEffectRecoveryCursor | DirectedEffectOperationResultV1:
        source_head_seq = current.evidence.get("source_head_seq")
        if isinstance(source_head_seq, bool) or not isinstance(source_head_seq, int) or source_head_seq < 0:
            return self._operation_failure(
                "strict_stream_corruption",
                cast(DirectedEffectOperationIdentityV1, current.operation),
                None,
                {"reason": "recovery_sweep_source_head_seq_invalid"},
            )
        return _DirectedEffectRecoveryCursor(
            current.version, source_head_seq + 1, cast(DirectedEffectOperationStateV1, current.state)
        )

    def _commit_restart_recovery_pending(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        binding: DirectedEffectParentBindingV1,
        member: DirectedEffectInventoryMemberV1,
        current: DirectedEffectOperationResultV1,
        cursor: _DirectedEffectRecoveryCursor,
        actor: str,
        reason: str,
        scanned: int,
        deadline_monotonic: float,
    ) -> _DirectedEffectRecoveryRepositorySweep:
        operation = cast(DirectedEffectOperationIdentityV1, current.operation)
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep(
                (), operation=operation, stage="before_recovery_pending_mutation", scanned=scanned
            )
        evidence_hash = _hash_token(
            {
                "schema_version": "task-runtime.directed-effect-restart-recovery/1",
                "operation_id": operation.operation_id,
                "observed_state": current.state,
                "reason": reason,
            }
        )
        command = MarkDirectedEffectRecoveryPendingCommandV1(
            workspace=execution_attempt.workspace,
            task_id=execution_attempt.task_id,
            execution_attempt=execution_attempt,
            parent_binding=binding,
            tool_call_id=member.tool_call_id,
            effect_id=member.effect_id,
            expected_version=cursor.expected_version,
            expected_seq=cursor.expected_seq,
            actor=actor,
            intended_effect_fingerprint=member.intended_effect_fingerprint,
            policy_verdict_hash=member.policy_verdict_hash,
            expected_receipt_binding_hash=member.expected_receipt_binding_hash,
            reason=reason,
            recovery_evidence_ref=f"recovery://task-runtime/restart/{operation.operation_id}",
            recovery_evidence_hash=evidence_hash,
        )
        recovery = self._mutate(
            command,
            kind="mark_recovery_pending",
            target="RECOVERY_PENDING",
            allowed_from=frozenset({"EFFECT_STARTED"}),
            attempt_authority_lock_held=True,
        )
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep(
                (recovery,), operation=operation, stage="after_recovery_pending_mutation", scanned=scanned
            )
        return _DirectedEffectRecoveryRepositorySweep((recovery,), scanned)

    def _commit_restart_dead_letter(
        self,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        binding: DirectedEffectParentBindingV1,
        member: DirectedEffectInventoryMemberV1,
        operation: DirectedEffectOperationIdentityV1,
        cursor: _DirectedEffectRecoveryCursor,
        actor: str,
        reason: str,
        scanned: int,
        deadline_monotonic: float,
    ) -> _DirectedEffectRecoveryRepositorySweep:
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep(
                (), operation=operation, stage="before_dead_letter_mutation", scanned=scanned
            )
        evidence_hash = _hash_token(
            {
                "schema_version": "task-runtime.directed-effect-restart-dead-letter/1",
                "operation_id": operation.operation_id,
                "observed_state": cursor.observed_state,
                "reason": reason,
            }
        )
        command = DeadLetterDirectedEffectOperationCommandV1(
            workspace=execution_attempt.workspace,
            task_id=execution_attempt.task_id,
            execution_attempt=execution_attempt,
            parent_binding=binding,
            tool_call_id=member.tool_call_id,
            effect_id=member.effect_id,
            expected_version=cursor.expected_version,
            expected_seq=cursor.expected_seq,
            actor=actor,
            intended_effect_fingerprint=member.intended_effect_fingerprint,
            policy_verdict_hash=member.policy_verdict_hash,
            expected_receipt_binding_hash=member.expected_receipt_binding_hash,
            reason=reason,
            resolution_evidence_ref=f"dead-letter://task-runtime/restart/{operation.operation_id}",
            resolution_evidence_hash=evidence_hash,
        )
        dead_letter = self._mutate(
            command,
            kind="dead_letter",
            target="DEAD_LETTER",
            allowed_from=frozenset({"RECOVERY_PENDING"}),
            attempt_authority_lock_held=True,
        )
        if time.monotonic() >= deadline_monotonic:
            return self._recovery_deadline_sweep(
                (dead_letter,),
                operation=dead_letter.operation or operation,
                stage="after_dead_letter_mutation",
                scanned=scanned,
            )
        return _DirectedEffectRecoveryRepositorySweep((dead_letter,), scanned)

    @staticmethod
    def _recovery_sweep_hit_deadline(sweep: _DirectedEffectRecoveryRepositorySweep) -> bool:
        return any(result.code == "recovery_deadline_exceeded" for result in sweep.results)

    @staticmethod
    def _recovery_deadline_sweep(
        results: tuple[DirectedEffectOperationResultV1, ...] | list[DirectedEffectOperationResultV1],
        *,
        stage: str,
        scanned: int,
        operation: DirectedEffectOperationIdentityV1 | None = None,
    ) -> _DirectedEffectRecoveryRepositorySweep:
        failure = DirectedEffectOperationResultV1(
            ok=False, code="recovery_deadline_exceeded", operation=operation, evidence={"stage": stage}
        )
        return _DirectedEffectRecoveryRepositorySweep((*results, failure), scanned)

    def _recovery_fact_projection_results(
        self,
        *,
        workspace: str,
        binding: DirectedEffectParentBindingV1,
        operation: DirectedEffectOperationIdentityV1,
    ) -> tuple[DirectedEffectOperationResultV1, ...] | DirectedEffectOperationResultV1:
        """Rehydrate durable recovery facts as an idempotent projection outbox."""

        read = self._read_stream(
            workspace,
            binding.operation_stream_token,
            max_events=_MAX_OPERATION_EVENTS,
            stream_kind="operation",
        )
        if isinstance(read, DirectedEffectOperationResultV1):
            return read
        aggregate = self._reduce_operation(read, operation, binding)
        if isinstance(aggregate, DirectedEffectOperationResultV1):
            return aggregate
        projected: list[DirectedEffectOperationResultV1] = []
        for transition in aggregate.transitions:
            if transition.state not in {"RECOVERY_PENDING", "DEAD_LETTER"}:
                continue
            code: Literal["recovery_pending", "dead_lettered"] = (
                "recovery_pending" if transition.state == "RECOVERY_PENDING" else "dead_lettered"
            )
            prefix = "recovery" if transition.state == "RECOVERY_PENDING" else "resolution"
            projected.append(
                DirectedEffectOperationResultV1(
                    ok=True,
                    code=code,
                    operation=operation,
                    state=transition.state,
                    version=transition.version,
                    evidence={
                        "event_id": transition.event_id,
                        "source_head_seq": aggregate.source_head_seq,
                        f"{prefix}_evidence_ref": transition.descriptor.get(f"{prefix}_evidence_ref"),
                        f"{prefix}_evidence_hash": transition.descriptor.get(f"{prefix}_evidence_hash"),
                        "replayed_recovery_fact": True,
                    },
                )
            )
        return tuple(projected)

    def get(self, query: GetDirectedEffectOperationQueryV1) -> DirectedEffectOperationResultV1:
        validated = self._validated_parent_binding(query, require_open=False)
        if isinstance(validated, DirectedEffectOperationResultV1):
            return validated
        binding, _registry = validated
        operation = self._derive_operation(query, binding)
        read = self._read_stream(
            query.workspace,
            binding.operation_stream_token,
            max_events=_MAX_OPERATION_EVENTS,
            stream_kind="operation",
        )
        if isinstance(read, DirectedEffectOperationResultV1):
            return read
        aggregate = self._reduce_operation(read, operation, binding)
        if isinstance(aggregate, DirectedEffectOperationResultV1):
            return aggregate
        if aggregate.state is None:
            return self._operation_failure(
                "operation_not_found",
                operation,
                aggregate,
                {"source_head_seq": aggregate.source_head_seq},
            )
        snapshot = self._project_snapshot(aggregate)
        return DirectedEffectOperationResultV1(
            ok=True,
            code="found",
            operation=operation,
            state=aggregate.state,
            version=aggregate.version,
            snapshot=snapshot,
            evidence={"source_head_seq": aggregate.source_head_seq},
        )

    def get_parent_readiness(
        self,
        query: GetDirectedEffectParentReadinessQueryV1,
    ) -> DirectedEffectParentReadinessResultV1:
        """Rebuild one parent operation stream as a non-authoritative diagnostic."""

        validated = self._validated_parent_binding(query, require_open=False)
        if isinstance(validated, DirectedEffectOperationResultV1):
            return DirectedEffectParentReadinessResultV1(
                ok=False,
                code=validated.code,
                evidence=validated.evidence,
            )
        binding, registry = validated
        read = self._read_stream(
            query.workspace,
            binding.operation_stream_token,
            max_events=_MAX_OPERATION_EVENTS,
            stream_kind="operation",
        )
        if isinstance(read, DirectedEffectOperationResultV1):
            return DirectedEffectParentReadinessResultV1(
                ok=False,
                code=read.code,
                evidence=read.evidence,
            )
        reduced = self._reduce_operation_stream(read, binding, target=None)
        if isinstance(reduced, DirectedEffectOperationResultV1):
            return DirectedEffectParentReadinessResultV1(
                ok=False,
                code=reduced.code,
                evidence=reduced.evidence,
            )
        ordered_states: tuple[DirectedEffectOperationStateV1, ...] = (
            "INTENT_COMMITTED",
            "EFFECT_STARTED",
            "RECOVERY_PENDING",
            "RECEIPT_COMMITTED",
            "CLOSED_BY_PARENT",
            "ABORTED",
            "DEAD_LETTER",
        )
        counts = dict.fromkeys(ordered_states, 0)
        for aggregate in reduced.aggregates:
            if aggregate.state is None:
                continue
            counts[aggregate.state] += 1
        projection = DirectedEffectParentReadinessProjectionV1(
            schema_version=DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1,
            workspace=query.workspace,
            task_id=query.task_id,
            execution_attempt=query.execution_attempt,
            parent_binding_id=binding.binding_id,
            parent_registry_stream_token=binding.registry_stream_token,
            parent_registry_source_head_seq=registry.source_head_seq,
            operation_stream_token=binding.operation_stream_token,
            operation_source_head_seq=reduced.source_head_seq,
            operation_count=len(reduced.aggregates),
            state_counts=tuple(
                DirectedEffectParentReadinessStateCountV1(state=state, count=counts[state]) for state in ordered_states
            ),
        )
        return DirectedEffectParentReadinessResultV1(
            ok=True,
            code="readiness_observed",
            projection=projection,
            evidence={
                "parent_registry_source_head_seq": registry.source_head_seq,
                "operation_source_head_seq": reduced.source_head_seq,
            },
        )

    def _mutate(
        self,
        command: _Command,
        *,
        kind: _CommandKind,
        target: DirectedEffectOperationStateV1,
        allowed_from: frozenset[DirectedEffectOperationStateV1 | None],
        attempt_authority_lock_held: bool = False,
    ) -> DirectedEffectOperationResultV1:
        identity_failure = self._command_identity_failure(
            workspace=command.workspace,
            task_id=command.task_id,
            execution_attempt=command.execution_attempt,
        )
        if identity_failure is not None:
            return identity_failure
        expected_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(command.execution_attempt)
        claim_append_ownership_nonce = _new_append_attempt_nonce() if kind == "claim" else None
        drift_codes: list[str] = []
        last_snapshot: GuardedFactSnapshotV1 | None = None
        for attempt_number in range(1, _MAX_GUARDED_ATTEMPTS + 1):
            attempt_failure = (
                None
                if attempt_authority_lock_held
                else self._guarded_attempt_failure(
                    command,
                    attempt_number=attempt_number,
                    phase="prepare",
                    drift_codes=drift_codes,
                )
            )
            if attempt_failure is not None:
                return attempt_failure
            prepared = self._prepare_guarded_snapshot(command, expected_identity)
            if isinstance(prepared, DirectedEffectOperationResultV1):
                return prepared
            last_snapshot = prepared
            validated = self._validated_parent_binding_from_registry(
                command,
                expected_identity=expected_identity,
                registry_read=_StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq),
                require_open=False,
            )
            if isinstance(validated, DirectedEffectOperationResultV1):
                return validated
            binding, registry = validated
            operation = self._derive_operation(command, binding)
            if kind in {"claim", "abort"}:
                ready_context = self._ready_operation_context(
                    command=cast(_ReadyGatedCommand, command),
                    operation=operation,
                    binding=binding,
                    registry=registry,
                    operation_events=prepared.target_records(),
                    operation_head_seq=prepared.proof.target_head_seq,
                )
                if isinstance(ready_context, DirectedEffectOperationResultV1):
                    return ready_context
            aggregate = self._reduce_operation(
                _StreamRead(prepared.target_records(), prepared.proof.target_head_seq),
                operation,
                binding,
            )
            if isinstance(aggregate, DirectedEffectOperationResultV1):
                return aggregate
            if kind == "admit":
                inventory_failure = self._admit_inventory_member_failure(
                    command=cast(AdmitDirectedEffectOperationCommandV1, command),
                    operation=operation,
                    aggregate=aggregate,
                    binding=binding,
                    registry=registry,
                )
                if inventory_failure is not None:
                    return inventory_failure
            if kind == "commit_receipt":
                receipt_command = cast(CommitDirectedEffectReceiptCommandV1, command)
                if receipt_command.receipt_binding_hash != command.expected_receipt_binding_hash:
                    return self._operation_failure(
                        "receipt_binding_conflict",
                        operation,
                        aggregate,
                        {
                            "expected_receipt_binding_hash": command.expected_receipt_binding_hash,
                            "observed_receipt_binding_hash": receipt_command.receipt_binding_hash,
                        },
                    )
            descriptor = self._operation_descriptor(command, kind=kind)
            committed = self._transition_for_kind(aggregate, kind)
            if committed is not None:
                replay_authority_failure = (
                    None
                    if attempt_authority_lock_held
                    else self._guarded_attempt_failure(
                        command,
                        attempt_number=attempt_number,
                        phase="replay",
                        operation=operation,
                        drift_codes=drift_codes,
                    )
                )
                if replay_authority_failure is not None:
                    return replay_authority_failure
                return self._replay_result(operation, aggregate, committed, descriptor)
            if registry.open_binding is None or registry.open_binding.binding_id != binding.binding_id:
                return self._parent_registry_conflict(
                    "parent_closed",
                    registry,
                    {"requested_binding_id": binding.binding_id, "reason": "binding_is_closed_historical_parent"},
                    binding=binding,
                )
            semantic_failure = self._semantic_continuity_failure(operation, aggregate, descriptor)
            if semantic_failure is not None:
                return semantic_failure
            if aggregate.version != command.expected_version:
                return self._operation_failure(
                    "operation_version_conflict",
                    operation,
                    aggregate,
                    {"expected_version": command.expected_version, "fresh_version": aggregate.version},
                )
            if aggregate.state not in allowed_from or aggregate.state in _TERMINAL_STATES:
                return self._operation_failure(
                    "illegal_transition",
                    operation,
                    aggregate,
                    {"from_state": aggregate.state, "target_state": target},
                )
            if command.expected_seq != prepared.proof.target_head_seq + 1:
                return self._operation_failure(
                    "stream_expected_seq_conflict",
                    operation,
                    aggregate,
                    {
                        "expected_seq": command.expected_seq,
                        "fresh_next_expected_seq": prepared.proof.target_head_seq + 1,
                    },
                )
            canonical_event = self._operation_event_canonical(
                operation=operation,
                state=target,
                previous_version=aggregate.version,
                descriptor=descriptor,
            )
            normalized = self._normalized_transition(
                operation=operation,
                state=target,
                descriptor=descriptor,
            )
            fact_idempotency_key = (
                _hash_token(
                    {
                        "normalized_transition": normalized.to_record(),
                        "claim_append_ownership_nonce": claim_append_ownership_nonce,
                    }
                )
                if kind == "claim"
                else _hash_token(normalized.to_record())
            )
            guarded_command = AppendIfGuardedSnapshotCommandV1(
                snapshot_proof=prepared.proof,
                event=GuardedFactEventV1(
                    event_type=_operation_event_type(target),
                    source="runtime.task_runtime",
                    payload=canonical_event,
                    aggregate_id=str(command.task_id),
                    correlation_id=fact_idempotency_key,
                ),
                idempotency_key=fact_idempotency_key,
            )
            appended: GuardedFactAppendedV1 | None = None
            try:
                self._after_guarded_prepare(prepared)
                commit_authority_failure = (
                    None
                    if attempt_authority_lock_held
                    else self._guarded_attempt_failure(
                        command,
                        attempt_number=attempt_number,
                        phase="commit",
                        operation=operation,
                        drift_codes=drift_codes,
                    )
                )
                if commit_authority_failure is not None:
                    return commit_authority_failure
                appended = append_if_guarded_snapshot(guarded_command)
                seam_receipt = self._after_guarded_commit(appended)
                if seam_receipt is not None:
                    appended = seam_receipt
            except FactStreamError as exc:
                if exc.code in _GUARDED_REPREPARE_DRIFT_CODES:
                    drift_codes.append(exc.code)
                    self._after_guarded_drift(exc, attempt_number)
                    reprepare_authority_failure = (
                        None
                        if attempt_authority_lock_held
                        else self._guarded_attempt_failure(
                            command,
                            attempt_number=attempt_number,
                            phase="reprepare",
                            operation=operation,
                            drift_codes=drift_codes,
                        )
                    )
                    if reprepare_authority_failure is not None:
                        return reprepare_authority_failure
                    continue
                return self._reconcile_operation_append(
                    command=command,
                    operation=operation,
                    kind=kind,
                    target=target,
                    canonical_event=canonical_event,
                    normalized=normalized,
                    expected_previous_version=aggregate.version,
                    guarded_attempt=attempt_number,
                    exc=exc,
                    receipt=appended,
                    guarded_command=guarded_command,
                )
            if appended is None:
                return self._operation_failure(
                    "guarded_receipt_mismatch",
                    operation,
                    aggregate,
                    {
                        "reason": "guarded_commit_returned_without_receipt",
                        "guarded_attempt": attempt_number,
                        "parent_binding_id": binding.binding_id,
                    },
                )
            return self._confirm_guarded_append(
                command=command,
                operation=operation,
                kind=kind,
                target=target,
                canonical_event=canonical_event,
                normalized=normalized,
                expected_previous_version=aggregate.version,
                guarded_attempt=attempt_number,
                receipt=appended,
                guarded_command=guarded_command,
            )
        assert last_snapshot is not None
        return DirectedEffectOperationResultV1(
            ok=False,
            code="guarded_reprepare_exhausted",
            operation=self._derive_operation(command, command.parent_binding),
            evidence={
                "attempts_total": _MAX_GUARDED_ATTEMPTS,
                "reprepare_count": _MAX_GUARDED_ATTEMPTS - 1,
                "drift_codes": tuple(drift_codes),
                "target_head_seq": last_snapshot.proof.target_head_seq,
                "guard_head_seq": last_snapshot.proof.guard_head_seq,
                "operation_identity": self._derive_operation(command, command.parent_binding).to_record(),
                "parent_binding_id": command.parent_binding.binding_id,
            },
        )

    def _admit_inventory_member_failure(
        self,
        *,
        command: AdmitDirectedEffectOperationCommandV1,
        operation: DirectedEffectOperationIdentityV1,
        aggregate: _Aggregate,
        binding: DirectedEffectParentBindingV1,
        registry: _ParentRegistry,
    ) -> DirectedEffectOperationResultV1 | None:
        sealed = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
        if sealed is None:
            return self._operation_failure(
                "inventory_not_sealed",
                operation,
                aggregate,
                {"binding_id": binding.binding_id},
            )
        member = next(
            (
                candidate
                for candidate in sealed.members
                if candidate.tool_call_id == command.tool_call_id
                and candidate.effect_id == command.effect_id
                and candidate.operation_id == operation.operation_id
            ),
            None,
        )
        if member is None:
            return self._operation_failure(
                "inventory_member_not_found",
                operation,
                aggregate,
                {
                    "binding_id": binding.binding_id,
                    "tool_call_id": command.tool_call_id,
                    "effect_id": command.effect_id,
                    "operation_id": operation.operation_id,
                },
            )
        expected_semantic = (
            member.intended_effect_fingerprint,
            member.policy_verdict_hash,
            member.expected_receipt_binding_hash,
        )
        requested_semantic = (
            command.intended_effect_fingerprint,
            command.policy_verdict_hash,
            command.expected_receipt_binding_hash,
        )
        if requested_semantic != expected_semantic:
            return self._operation_failure(
                "inventory_member_conflict",
                operation,
                aggregate,
                {
                    "binding_id": binding.binding_id,
                    "operation_id": operation.operation_id,
                    "expected_semantic": expected_semantic,
                    "requested_semantic": requested_semantic,
                },
            )
        return None

    @staticmethod
    def _operation_gate_failure(
        code: DirectedEffectOperationCodeV1,
        operation: DirectedEffectOperationIdentityV1,
        evidence: Mapping[str, object],
    ) -> DirectedEffectOperationResultV1:
        return DirectedEffectOperationResultV1(
            ok=False,
            code=code,
            operation=operation,
            evidence=dict(evidence),
        )

    def _ready_operation_context(
        self,
        *,
        command: _ReadyGatedCommand,
        operation: DirectedEffectOperationIdentityV1,
        binding: DirectedEffectParentBindingV1,
        registry: _ParentRegistry,
        operation_events: tuple[dict[str, Any], ...],
        operation_head_seq: int,
    ) -> _ReadyOperationContext | DirectedEffectOperationResultV1:
        """Authorize claim or abort only from the exact durable ready prefix."""

        sealed = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
        if sealed is None:
            return self._operation_gate_failure(
                "inventory_not_sealed",
                operation,
                {"binding_id": binding.binding_id},
            )
        ready = registry.ready_inventories_by_binding_id.get(binding.binding_id)
        if ready is None:
            return self._operation_gate_failure(
                "inventory_not_ready",
                operation,
                {"binding_id": binding.binding_id},
            )
        member = next(
            (
                candidate
                for candidate in sealed.members
                if candidate.tool_call_id == command.tool_call_id
                and candidate.effect_id == command.effect_id
                and candidate.operation_id == operation.operation_id
            ),
            None,
        )
        if member is None:
            return self._operation_gate_failure(
                "inventory_member_not_found",
                operation,
                {
                    "binding_id": binding.binding_id,
                    "tool_call_id": command.tool_call_id,
                    "effect_id": command.effect_id,
                    "operation_id": operation.operation_id,
                },
            )
        expected_semantic = (
            member.intended_effect_fingerprint,
            member.policy_verdict_hash,
            member.expected_receipt_binding_hash,
        )
        requested_semantic = (
            command.intended_effect_fingerprint,
            command.policy_verdict_hash,
            command.expected_receipt_binding_hash,
        )
        if requested_semantic != expected_semantic:
            return self._operation_gate_failure(
                "inventory_member_conflict",
                operation,
                {
                    "binding_id": binding.binding_id,
                    "operation_id": operation.operation_id,
                    "expected_semantic": expected_semantic,
                    "requested_semantic": requested_semantic,
                },
            )
        prefix = self._validate_ready_inventory_prefix(
            binding=binding,
            sealed=sealed,
            ready=ready,
            operation_events=operation_events,
        )
        if isinstance(prefix, DirectedEffectOperationResultV1):
            return prefix
        current = self._inventory_operation_projection(
            binding=binding,
            sealed=sealed,
            operation_read=_StreamRead(operation_events, operation_head_seq),
        )
        if isinstance(current, DirectedEffectOperationResultV1):
            return current
        current_failure = self._ready_current_inventory_failure(current)
        if current_failure is not None:
            return current_failure
        return _ReadyOperationContext(sealed=sealed, ready=ready, member=member)

    def _prepare_inventory_guarded_snapshot(
        self,
        command: _InventoryGuardedCommand,
        identity: DirectedEffectParentRegistryIdentityV1,
    ) -> GuardedFactSnapshotV1 | DirectedEffectOperationResultV1:
        """Prepare registry target facts guarded by the exact operation stream."""

        try:
            return read_guarded_fact_snapshot(
                ReadGuardedFactSnapshotCommandV1(
                    workspace=command.workspace,
                    target_stream=_registry_stream_token(identity),
                    guard_stream=command.parent_binding.operation_stream_token,
                )
            )
        except FactStreamError as exc:
            failure = self._fact_failure_result(
                exc,
                operation="read",
                stream_kind="inventory_guarded_pair",
            )
            evidence = dict(failure.evidence)
            evidence.update(
                {
                    "target_stream_kind": "parent_registry",
                    "target_stream_token": _registry_stream_token(identity),
                    "guard_stream_kind": "operation",
                    "guard_stream_token": command.parent_binding.operation_stream_token,
                }
            )
            return DirectedEffectOperationResultV1(
                ok=False,
                code=failure.code,
                evidence=evidence,
            )

    @staticmethod
    def _inventory_seal_payload(
        *,
        registry: _ParentRegistry,
        binding: DirectedEffectParentBindingV1,
        members: tuple[DirectedEffectInventoryMemberV1, ...],
        inventory_hash: str,
        actor: str,
    ) -> dict[str, object]:
        return {
            "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
            "stable_registry_identity": registry.identity.to_record(),
            "previous_version": registry.registry_version,
            "version": registry.registry_version + 1,
            "parent_sequence": binding.parent_sequence,
            "binding_id": binding.binding_id,
            "members": [member.to_record() for member in members],
            "member_count": len(members),
            "inventory_hash": inventory_hash,
            "actor": actor,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _inventory_ready_payload(
        *,
        registry: _ParentRegistry,
        binding: DirectedEffectParentBindingV1,
        sealed: _SealedDirectedEffectInventory,
        ordered_operation_ids: tuple[str, ...],
        admission_set_hash: str,
        operation_source_head_seq: int,
        actor: str,
    ) -> dict[str, object]:
        return {
            "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
            "stable_registry_identity": registry.identity.to_record(),
            "previous_version": registry.registry_version,
            "version": registry.registry_version + 1,
            "parent_sequence": binding.parent_sequence,
            "binding_id": binding.binding_id,
            "inventory_hash": sealed.inventory_hash,
            "ordered_operation_ids": list(ordered_operation_ids),
            "admission_set_hash": admission_set_hash,
            "operation_source_head_seq": operation_source_head_seq,
            "actor": actor,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    def _confirm_inventory_append(
        self,
        *,
        command: SealDirectedEffectInventoryCommandV1,
        expected_identity: DirectedEffectParentRegistryIdentityV1,
        members: tuple[DirectedEffectInventoryMemberV1, ...],
        inventory_hash: str,
        guarded_attempt: int,
        receipt: GuardedFactAppendedV1,
        guarded_command: AppendIfGuardedSnapshotCommandV1,
    ) -> DirectedEffectInventoryResultV1:
        confirmation = self._strict_inventory_confirmation(
            command,
            expected_identity=expected_identity,
            members=members,
            inventory_hash=inventory_hash,
        )
        if isinstance(confirmation, DirectedEffectInventoryResultV1):
            return confirmation
        try:
            canonical_receipt = append_if_guarded_snapshot(guarded_command)
        except FactStreamError as exc:
            return self._inventory_failure(
                "guarded_receipt_mismatch",
                {
                    "reason": "public_exact_replay_receipt_failed",
                    "guarded_attempt": guarded_attempt,
                    "fact_stream_code": exc.code,
                    "fact_stream_details": dict(exc.details),
                    "sealed_event_id": confirmation.sealed.event_id,
                    "sealed_event_seq": confirmation.sealed.seq,
                },
            )
        return self._confirmed_inventory_result(
            command,
            confirmation=confirmation,
            guarded_attempt=guarded_attempt,
            receipt=receipt,
            canonical_receipt=canonical_receipt,
        )

    def _reconcile_inventory_append(
        self,
        *,
        command: SealDirectedEffectInventoryCommandV1,
        expected_identity: DirectedEffectParentRegistryIdentityV1,
        members: tuple[DirectedEffectInventoryMemberV1, ...],
        inventory_hash: str,
        guarded_attempt: int,
        exc: FactStreamError,
        receipt: GuardedFactAppendedV1 | None,
        guarded_command: AppendIfGuardedSnapshotCommandV1,
    ) -> DirectedEffectInventoryResultV1:
        """Resolve an ambiguous append only from a fresh strict two-stream snapshot."""

        confirmation = self._strict_inventory_confirmation(
            command,
            expected_identity=expected_identity,
            members=members,
            inventory_hash=inventory_hash,
        )
        if isinstance(confirmation, DirectedEffectInventoryResultV1):
            return self._with_inventory_reconciliation_evidence(confirmation, exc)
        try:
            canonical_receipt = append_if_guarded_snapshot(guarded_command)
        except FactStreamError as replay_exc:
            return self._inventory_failure(
                "guarded_receipt_mismatch",
                {
                    "reason": "public_exact_replay_receipt_failed",
                    "guarded_attempt": guarded_attempt,
                    "fact_stream_code": replay_exc.code,
                    "fact_stream_details": dict(replay_exc.details),
                    "reconciled_after_guarded_error": True,
                    "original_fact_stream_code": exc.code,
                    "original_fact_stream_details": dict(exc.details),
                    "sealed_event_id": confirmation.sealed.event_id,
                    "sealed_event_seq": confirmation.sealed.seq,
                },
            )
        return self._confirmed_inventory_result(
            command,
            confirmation=confirmation,
            guarded_attempt=guarded_attempt,
            receipt=receipt,
            canonical_receipt=canonical_receipt,
            reconciliation_error=exc,
        )

    def _strict_inventory_confirmation(
        self,
        command: SealDirectedEffectInventoryCommandV1,
        *,
        expected_identity: DirectedEffectParentRegistryIdentityV1,
        members: tuple[DirectedEffectInventoryMemberV1, ...],
        inventory_hash: str,
    ) -> _StrictInventoryConfirmation | DirectedEffectInventoryResultV1:
        """Rebuild both streams and bind one exact seal to its operation facts."""

        prepared = self._prepare_inventory_guarded_snapshot(command, expected_identity)
        if isinstance(prepared, DirectedEffectOperationResultV1):
            return self._inventory_failure_from_operation(prepared)
        validated = self._validated_parent_binding_from_registry(
            command,
            expected_identity=expected_identity,
            registry_read=_StreamRead(prepared.target_records(), prepared.proof.target_head_seq),
            require_open=False,
        )
        if isinstance(validated, DirectedEffectOperationResultV1):
            return self._inventory_failure_from_operation(validated)
        binding, registry = validated
        sealed = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
        if sealed is None:
            return self._inventory_failure(
                "guarded_receipt_mismatch",
                {"reason": "sealed_inventory_not_found", "binding_id": binding.binding_id},
            )
        if sealed.members != members or sealed.inventory_hash != inventory_hash or sealed.actor != command.actor:
            return self._inventory_failure(
                "guarded_receipt_mismatch",
                {
                    "reason": "sealed_inventory_semantics_mismatch",
                    "binding_id": binding.binding_id,
                    "sealed_inventory_hash": sealed.inventory_hash,
                    "requested_inventory_hash": inventory_hash,
                },
            )
        operations = self._inventory_operation_projection(
            binding=binding,
            sealed=sealed,
            operation_read=_StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq),
        )
        if isinstance(operations, DirectedEffectOperationResultV1):
            return self._inventory_failure_from_operation(operations)
        return _StrictInventoryConfirmation(
            binding=binding,
            registry=registry,
            sealed=sealed,
            operations=operations,
        )

    def _inventory_operation_projection(
        self,
        *,
        binding: DirectedEffectParentBindingV1,
        sealed: _SealedDirectedEffectInventory,
        operation_read: _StreamRead,
    ) -> _InventoryOperationProjection | DirectedEffectOperationResultV1:
        reduced = self._reduce_operation_stream(operation_read, binding, target=None)
        if isinstance(reduced, DirectedEffectOperationResultV1):
            if reduced.code in {"operation_identity_conflict", "deo_semantic_drift"}:
                return DirectedEffectOperationResultV1(
                    ok=False,
                    code="inventory_member_conflict",
                    evidence={"operation_stream_failure": dict(reduced.evidence)},
                )
            return reduced
        members_by_operation_id = {member.operation_id: member for member in sealed.members}
        admitted_operation_ids: set[str] = set()
        unexpected_operation_ids: list[str] = []
        for aggregate in reduced.aggregates:
            member = members_by_operation_id.get(aggregate.operation.operation_id)
            if member is None:
                unexpected_operation_ids.append(aggregate.operation.operation_id)
                continue
            expected_operation = DirectedEffectOperationIdentityV1(
                workspace=binding.workspace,
                task_id=binding.task_id,
                execution_attempt_id=binding.registry_identity.execution_attempt_id,
                parent_binding_id=binding.binding_id,
                parent_sequence=binding.parent_sequence,
                tool_call_id=member.tool_call_id,
                effect_id=member.effect_id,
                operation_id=member.operation_id,
                operation_stream_token=binding.operation_stream_token,
            )
            expected_semantic = (
                member.intended_effect_fingerprint,
                member.policy_verdict_hash,
                member.expected_receipt_binding_hash,
            )
            observed_semantic = (
                aggregate.intended_effect_fingerprint,
                aggregate.policy_verdict_hash,
                aggregate.expected_receipt_binding_hash,
            )
            if aggregate.operation != expected_operation or observed_semantic != expected_semantic:
                return DirectedEffectOperationResultV1(
                    ok=False,
                    code="inventory_member_conflict",
                    evidence={
                        "operation_id": member.operation_id,
                        "expected_operation": expected_operation.to_record(),
                        "observed_operation": aggregate.operation.to_record(),
                        "expected_semantic": expected_semantic,
                        "observed_semantic": observed_semantic,
                    },
                )
            admitted_operation_ids.add(member.operation_id)
        missing_operation_ids = tuple(
            member.operation_id for member in sealed.members if member.operation_id not in admitted_operation_ids
        )
        return _InventoryOperationProjection(
            source_head_seq=reduced.source_head_seq,
            admitted_count=len(sealed.members) - len(missing_operation_ids),
            missing_operation_ids=missing_operation_ids,
            unexpected_operation_ids=tuple(unexpected_operation_ids),
        )

    def _validate_exact_inventory_admission(
        self,
        *,
        binding: DirectedEffectParentBindingV1,
        sealed: _SealedDirectedEffectInventory,
        operation_read: _StreamRead,
    ) -> _InventoryOperationProjection | DirectedEffectOperationResultV1:
        projection = self._inventory_operation_projection(
            binding=binding,
            sealed=sealed,
            operation_read=operation_read,
        )
        if isinstance(projection, DirectedEffectOperationResultV1):
            return projection
        if projection.unexpected_operation_ids:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="inventory_admission_unexpected",
                evidence={
                    "unexpected_operation_ids": projection.unexpected_operation_ids,
                    "operation_source_head_seq": projection.source_head_seq,
                },
            )
        if projection.missing_operation_ids:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="inventory_admission_incomplete",
                evidence={
                    "missing_operation_ids": projection.missing_operation_ids,
                    "operation_source_head_seq": projection.source_head_seq,
                },
            )
        reduced = self._reduce_operation_stream(operation_read, binding, target=None)
        if isinstance(reduced, DirectedEffectOperationResultV1):
            return reduced
        if any(
            aggregate.state != "INTENT_COMMITTED"
            or aggregate.version != 1
            or len(aggregate.transitions) != 1
            or aggregate.transitions[0].state != "INTENT_COMMITTED"
            or aggregate.transitions[0].previous_version != 0
            or aggregate.transitions[0].version != 1
            for aggregate in reduced.aggregates
        ):
            return DirectedEffectOperationResultV1(
                ok=False,
                code="inventory_admission_incomplete",
                evidence={
                    "reason": "inventory_members_not_exact_version_one_intents",
                    "operation_source_head_seq": reduced.source_head_seq,
                },
            )
        return projection

    @staticmethod
    def _ready_current_inventory_failure(
        projection: _InventoryOperationProjection,
    ) -> DirectedEffectOperationResultV1 | None:
        if not projection.unexpected_operation_ids:
            return None
        return DirectedEffectOperationResultV1(
            ok=False,
            code="inventory_admission_unexpected",
            evidence={
                "reason": "unexpected_operation_after_inventory_ready",
                "unexpected_operation_ids": projection.unexpected_operation_ids,
                "operation_source_head_seq": projection.source_head_seq,
            },
        )

    def _validate_ready_inventory_prefix(
        self,
        *,
        binding: DirectedEffectParentBindingV1,
        sealed: _SealedDirectedEffectInventory,
        ready: _ReadyDirectedEffectInventory,
        operation_events: tuple[dict[str, Any], ...],
    ) -> _InventoryOperationProjection | DirectedEffectOperationResultV1:
        if ready.operation_source_head_seq > len(operation_events):
            return DirectedEffectOperationResultV1(
                ok=False,
                code="strict_stream_corruption",
                evidence={"reason": "ready_operation_prefix_exceeds_current_stream"},
            )
        prefix = _StreamRead(
            operation_events[: ready.operation_source_head_seq],
            ready.operation_source_head_seq,
        )
        admission = self._validate_exact_inventory_admission(
            binding=binding,
            sealed=sealed,
            operation_read=prefix,
        )
        if isinstance(admission, DirectedEffectOperationResultV1):
            return DirectedEffectOperationResultV1(
                ok=False,
                code="strict_stream_corruption",
                evidence={
                    "reason": "ready_operation_prefix_invalid",
                    "ready_event_id": ready.event_id,
                    "ready_event_seq": ready.seq,
                    "prefix_failure_code": admission.code,
                    "prefix_failure_evidence": dict(admission.evidence),
                },
            )
        if (
            ready.ordered_operation_ids != tuple(member.operation_id for member in sealed.members)
            or ready.inventory_hash != sealed.inventory_hash
            or ready.admission_set_hash
            != _admission_set_hash(
                binding_id=binding.binding_id,
                inventory_hash=sealed.inventory_hash,
                ordered_operation_ids=ready.ordered_operation_ids,
                operation_source_head_seq=ready.operation_source_head_seq,
            )
        ):
            return DirectedEffectOperationResultV1(
                ok=False,
                code="strict_stream_corruption",
                evidence={"reason": "ready_prefix_identity_or_hash_invalid"},
            )
        return admission

    def _confirm_inventory_ready_append(
        self,
        *,
        command: FinalizeDirectedEffectInventoryAdmissionCommandV1,
        expected_identity: DirectedEffectParentRegistryIdentityV1,
        receipt: GuardedFactAppendedV1 | None,
        guarded_command: AppendIfGuardedSnapshotCommandV1,
        guarded_attempt: int,
        reconciliation_error: FactStreamError | None = None,
    ) -> DirectedEffectInventoryResultV1:
        prepared = self._prepare_inventory_guarded_snapshot(command, expected_identity)
        if isinstance(prepared, DirectedEffectOperationResultV1):
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure_from_operation(prepared),
                reconciliation_error,
            )
        validated = self._validated_parent_binding_from_registry(
            command,
            expected_identity=expected_identity,
            registry_read=_StreamRead(prepared.target_records(), prepared.proof.target_head_seq),
            require_open=False,
        )
        if isinstance(validated, DirectedEffectOperationResultV1):
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure_from_operation(validated),
                reconciliation_error,
            )
        binding, registry = validated
        sealed = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
        ready = registry.ready_inventories_by_binding_id.get(binding.binding_id)
        if sealed is None or sealed.inventory_hash != command.inventory_hash:
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure(
                    "guarded_receipt_mismatch",
                    {"reason": "ready_fact_not_confirmed_by_strict_registry_reread"},
                ),
                reconciliation_error,
            )
        if ready is None:
            if reconciliation_error is not None and receipt is None:
                return self._inventory_failure_from_operation(
                    self._fact_failure_result(
                        reconciliation_error,
                        operation="append",
                        stream_kind="inventory_guarded_pair",
                    )
                )
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure(
                    "guarded_receipt_mismatch",
                    {"reason": "ready_fact_not_confirmed_by_strict_registry_reread"},
                ),
                reconciliation_error,
            )
        if ready.inventory_hash != command.inventory_hash:
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure(
                    "guarded_receipt_mismatch",
                    {"reason": "ready_fact_not_confirmed_by_strict_registry_reread"},
                ),
                reconciliation_error,
            )
        prefix = self._validate_ready_inventory_prefix(
            binding=binding,
            sealed=sealed,
            ready=ready,
            operation_events=prepared.guard_records(),
        )
        if isinstance(prefix, DirectedEffectOperationResultV1):
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure_from_operation(prefix),
                reconciliation_error,
            )
        current = self._inventory_operation_projection(
            binding=binding,
            sealed=sealed,
            operation_read=_StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq),
        )
        if isinstance(current, DirectedEffectOperationResultV1):
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure_from_operation(current),
                reconciliation_error,
            )
        ready_current_failure = self._ready_current_inventory_failure(current)
        if ready_current_failure is not None:
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure_from_operation(ready_current_failure),
                reconciliation_error,
            )
        try:
            canonical_receipt = append_if_guarded_snapshot(guarded_command)
        except FactStreamError as exc:
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure(
                    "guarded_receipt_mismatch",
                    {
                        "reason": "public_exact_replay_receipt_failed",
                        "fact_stream_code": exc.code,
                        "fact_stream_details": dict(exc.details),
                    },
                ),
                reconciliation_error,
            )
        receipt_drift_fields = self._guarded_receipt_proof_drift(receipt, canonical_receipt)
        canonical_mismatch = (
            canonical_receipt.workspace != command.workspace
            or canonical_receipt.stream != registry.stream_token
            or canonical_receipt.event_id != ready.event_id
            or canonical_receipt.appended_seq != ready.seq
        )
        if receipt_drift_fields or canonical_mismatch:
            evidence: dict[str, object] = {
                "reason": (
                    "receipt_identity_mismatch" if receipt_drift_fields else "canonical_receipt_identity_mismatch"
                ),
                "receipt_drift_fields": receipt_drift_fields,
                "canonical_receipt": self._guarded_receipt_record(canonical_receipt),
                "ready_event_id": ready.event_id,
                "ready_event_seq": ready.seq,
            }
            if receipt is not None:
                evidence["observed_receipt"] = self._guarded_receipt_record(receipt)
            return self._with_inventory_reconciliation_evidence(
                self._inventory_failure("guarded_receipt_mismatch", evidence),
                reconciliation_error,
            )
        evidence = {
            **self._guarded_receipt_event_evidence(canonical_receipt),
            "admission_set_hash": ready.admission_set_hash,
            "authoritative_append": False,
            "authoritative_effect_receipt": True,
            "append_disposition": "committed_or_exact_replay",
            "guarded_attempt": guarded_attempt,
            "guarded_receipt": self._guarded_receipt_record(canonical_receipt),
        }
        if reconciliation_error is not None:
            evidence.update(
                {
                    "reconciled_after_guarded_error": True,
                    "fact_stream_code": reconciliation_error.code,
                    "fact_stream_details": dict(reconciliation_error.details),
                }
            )
        return self._inventory_success(
            command,
            registry,
            sealed,
            operation_projection=current,
            code="inventory_ready",
            evidence=evidence,
        )

    def _confirmed_inventory_result(
        self,
        command: SealDirectedEffectInventoryCommandV1,
        *,
        confirmation: _StrictInventoryConfirmation,
        guarded_attempt: int,
        receipt: GuardedFactAppendedV1 | None,
        canonical_receipt: GuardedFactAppendedV1,
        reconciliation_error: FactStreamError | None = None,
    ) -> DirectedEffectInventoryResultV1:
        receipt_drift_fields = self._guarded_receipt_proof_drift(receipt, canonical_receipt)
        canonical_receipt_mismatch = (
            canonical_receipt.workspace != command.workspace
            or canonical_receipt.stream != confirmation.registry.stream_token
            or canonical_receipt.event_id != confirmation.sealed.event_id
            or canonical_receipt.appended_seq != confirmation.sealed.seq
        )
        if receipt_drift_fields or canonical_receipt_mismatch:
            evidence: dict[str, object] = {
                "reason": (
                    "receipt_identity_mismatch" if receipt_drift_fields else "canonical_receipt_identity_mismatch"
                ),
                "receipt_drift_fields": receipt_drift_fields,
                "canonical_receipt": self._guarded_receipt_record(canonical_receipt),
                "sealed_event_id": confirmation.sealed.event_id,
                "sealed_event_seq": confirmation.sealed.seq,
                "binding_id": confirmation.binding.binding_id,
            }
            if receipt is not None:
                evidence["observed_receipt"] = self._guarded_receipt_record(receipt)
            if reconciliation_error is not None:
                evidence.update(
                    {
                        "reconciled_after_guarded_error": True,
                        "fact_stream_code": reconciliation_error.code,
                        "fact_stream_details": dict(reconciliation_error.details),
                    }
                )
            return self._inventory_failure("guarded_receipt_mismatch", evidence)
        evidence = {
            **self._guarded_receipt_event_evidence(canonical_receipt),
            "inventory_hash": confirmation.sealed.inventory_hash,
            "authoritative_append": False,
            "authoritative_effect_receipt": True,
            "append_disposition": "committed_or_exact_replay",
            "guarded_attempt": guarded_attempt,
            "guarded_receipt": self._guarded_receipt_record(canonical_receipt),
            "guarded_semantic_digest": canonical_receipt.semantic_digest,
        }
        if reconciliation_error is not None:
            evidence.update(
                {
                    "reconciled_after_guarded_error": True,
                    "fact_stream_code": reconciliation_error.code,
                    "fact_stream_details": dict(reconciliation_error.details),
                }
            )
        return self._inventory_success(
            command,
            confirmation.registry,
            confirmation.sealed,
            operation_projection=confirmation.operations,
            code="inventory_sealed",
            evidence=evidence,
        )

    @staticmethod
    def _with_inventory_reconciliation_evidence(
        result: DirectedEffectInventoryResultV1,
        exc: FactStreamError | None,
    ) -> DirectedEffectInventoryResultV1:
        if exc is None:
            return result
        evidence = dict(result.evidence)
        evidence["reconciled_after_guarded_error"] = True
        if "fact_stream_code" in evidence:
            evidence["original_fact_stream_code"] = exc.code
            evidence["original_fact_stream_details"] = dict(exc.details)
        else:
            evidence["fact_stream_code"] = exc.code
            evidence["fact_stream_details"] = dict(exc.details)
        return DirectedEffectInventoryResultV1(ok=False, code=result.code, evidence=evidence)

    @staticmethod
    def _inventory_success(
        command: _InventoryGuardedCommand,
        registry: _ParentRegistry,
        sealed: _SealedDirectedEffectInventory,
        *,
        operation_projection: _InventoryOperationProjection,
        code: DirectedEffectInventoryCodeV1,
        evidence: Mapping[str, object],
    ) -> DirectedEffectInventoryResultV1:
        ready = registry.ready_inventories_by_binding_id.get(sealed.binding_id)
        projection = DirectedEffectInventoryProjectionV1(
            schema_version=DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
            workspace=command.workspace,
            task_id=command.task_id,
            execution_attempt=command.execution_attempt,
            parent_binding_id=sealed.binding_id,
            members=sealed.members,
            inventory_hash=sealed.inventory_hash,
            sealed_event_id=sealed.event_id,
            sealed_event_seq=sealed.seq,
            parent_registry_source_head_seq=registry.source_head_seq,
            operation_source_head_seq=operation_projection.source_head_seq,
            inventory_ready=ready is not None,
            ready_event_id=ready.event_id if ready is not None else None,
            ready_event_seq=ready.seq if ready is not None else None,
            admitted_count=operation_projection.admitted_count,
            missing_operation_ids=operation_projection.missing_operation_ids,
            unexpected_operation_ids=operation_projection.unexpected_operation_ids,
        )
        return DirectedEffectInventoryResultV1(
            ok=True,
            code=code,
            projection=projection,
            evidence={
                "parent_registry_source_head_seq": registry.source_head_seq,
                "operation_source_head_seq": operation_projection.source_head_seq,
                **dict(evidence),
            },
        )

    @staticmethod
    def _inventory_failure(
        code: DirectedEffectInventoryCodeV1,
        evidence: Mapping[str, object],
    ) -> DirectedEffectInventoryResultV1:
        return DirectedEffectInventoryResultV1(ok=False, code=code, evidence=dict(evidence))

    @staticmethod
    def _inventory_failure_from_operation(
        failure: DirectedEffectOperationResultV1,
    ) -> DirectedEffectInventoryResultV1:
        return DirectedEffectInventoryResultV1(
            ok=False,
            code=cast(DirectedEffectInventoryCodeV1, failure.code),
            evidence=failure.evidence,
        )

    def _load_registry(
        self,
        workspace: str,
        identity: DirectedEffectParentRegistryIdentityV1,
    ) -> _ParentRegistry | DirectedEffectOperationResultV1:
        stream_token = _registry_stream_token(identity)
        read = self._read_stream(
            workspace,
            stream_token,
            max_events=_MAX_REGISTRY_EVENTS,
            stream_kind="parent_registry",
        )
        if isinstance(read, DirectedEffectOperationResultV1):
            return read
        return self._reduce_registry_from_read(identity, read)

    def _reduce_registry_from_read(
        self,
        identity: DirectedEffectParentRegistryIdentityV1,
        read: _StreamRead,
    ) -> _ParentRegistry | DirectedEffectOperationResultV1:
        """Strictly rebuild one registry aggregate from already-read immutable facts."""

        stream_token = _registry_stream_token(identity)
        registry = _ParentRegistry(
            identity=identity,
            stream_token=stream_token,
            registry_version=0,
            source_head_seq=read.head_seq,
            next_expected_seq=1,
            next_parent_sequence=1,
            open_binding=None,
            admissions_by_idempotency_key={},
            bindings_by_id={},
            sealed_inventories_by_binding_id={},
            ready_inventories_by_binding_id={},
        )
        for record in read.events:
            applied = self._apply_registry_event(registry, record)
            if isinstance(applied, DirectedEffectOperationResultV1):
                return applied
            registry = applied
        if registry.registry_version != read.head_seq or registry.next_expected_seq != read.head_seq + 1:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {
                    "reason": "registry_version_head_mismatch",
                    "registry_version": registry.registry_version,
                    "source_head_seq": read.head_seq,
                },
            )
        return registry

    def _apply_registry_event(
        self,
        registry: _ParentRegistry,
        record: Mapping[str, Any],
    ) -> _ParentRegistry | DirectedEffectOperationResultV1:
        event_type = record.get("event_type")
        if event_type == _PARENT_ADMITTED_EVENT_TYPE:
            return self._apply_parent_admitted(registry, record)
        if event_type == _PARENT_CLOSED_EVENT_TYPE:
            return self._apply_parent_closed(registry, record)
        if event_type == _PARENT_INVENTORY_SEALED_EVENT_TYPE:
            return self._apply_parent_inventory_sealed(registry, record)
        if event_type == _PARENT_INVENTORY_READY_EVENT_TYPE:
            return self._apply_parent_inventory_ready(registry, record)
        return self._parent_registry_failure(
            "strict_stream_unknown_schema",
            registry,
            {"reason": "unsupported_parent_registry_event", "event_type": event_type},
        )

    def _apply_parent_admitted(
        self,
        registry: _ParentRegistry,
        record: Mapping[str, Any],
    ) -> _ParentRegistry | DirectedEffectOperationResultV1:
        payload = record.get("payload")
        expected_payload_fields = {
            "schema_version",
            "stable_registry_identity",
            "previous_version",
            "version",
            "parent_sequence",
            "binding_id",
            "operation_stream_token",
            "binding_hash",
            "admission_idempotency_key",
            "correlation",
            "actor",
            "recorded_at",
        }
        if not isinstance(payload, dict) or set(payload) != expected_payload_fields:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_admitted_payload_fields_invalid"},
            )
        if payload.get("schema_version") != DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1:
            return self._parent_registry_failure(
                "strict_stream_unknown_schema",
                registry,
                {"observed_schema_version": payload.get("schema_version")},
            )
        raw_identity = payload.get("stable_registry_identity")
        raw_correlation = payload.get("correlation")
        if not isinstance(raw_identity, Mapping) or not isinstance(raw_correlation, Mapping):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_admitted_nested_identity_invalid"},
            )
        try:
            identity = DirectedEffectParentRegistryIdentityV1.from_record(raw_identity)
            correlation = ParentCorrelationV1.from_record(raw_correlation)
        except (TypeError, ValueError) as exc:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_admitted_nested_contract_invalid", "error_type": type(exc).__name__},
            )
        if identity != registry.identity:
            return self._parent_registry_failure(
                self._registry_identity_mismatch_code(registry.identity, identity),
                registry,
                {"observed_registry_identity": identity.to_record()},
            )
        previous_version = payload.get("previous_version")
        version = payload.get("version")
        parent_sequence = payload.get("parent_sequence")
        seq = record.get("seq")
        integer_values = (previous_version, version, parent_sequence, seq)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_admitted_integer_fields_invalid"},
            )
        previous_version = cast(int, previous_version)
        version = cast(int, version)
        parent_sequence = cast(int, parent_sequence)
        seq = cast(int, seq)
        if previous_version != registry.registry_version or version != previous_version + 1 or seq != version:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {
                    "reason": "parent_registry_version_not_monotonic",
                    "previous_version": previous_version,
                    "version": version,
                    "seq": seq,
                },
            )
        if parent_sequence != registry.next_parent_sequence:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {
                    "reason": "parent_sequence_not_monotonic",
                    "parent_sequence": parent_sequence,
                    "next_parent_sequence": registry.next_parent_sequence,
                },
            )
        if registry.open_binding is not None:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_admitted_while_registry_open"},
            )
        string_fields = (
            "binding_id",
            "operation_stream_token",
            "binding_hash",
            "admission_idempotency_key",
            "actor",
            "recorded_at",
        )
        if any(
            not isinstance(payload.get(field), str) or not cast(str, payload[field]).strip() for field in string_fields
        ):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_admitted_string_fields_invalid"},
            )
        binding_id = cast(str, payload["binding_id"])
        operation_stream_token = cast(str, payload["operation_stream_token"])
        binding_hash = cast(str, payload["binding_hash"])
        admission_idempotency_key = cast(str, payload["admission_idempotency_key"])
        actor = cast(str, payload["actor"])
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_admitted_event_id_invalid"},
            )
        expected_binding_id = _binding_id(identity, parent_sequence)
        expected_operation_stream = _operation_stream_token(expected_binding_id)
        expected_binding_hash = self._binding_hash(
            identity=identity,
            registry_stream_token=registry.stream_token,
            registry_version=version,
            parent_sequence=parent_sequence,
            binding_id=expected_binding_id,
            operation_stream_token=expected_operation_stream,
            admission_idempotency_key=admission_idempotency_key,
            correlation=correlation,
            actor=actor,
            source_event_seq=seq,
        )
        if binding_id != expected_binding_id or operation_stream_token != expected_operation_stream:
            return self._parent_registry_failure(
                "parent_binding_conflict",
                registry,
                {"reason": "parent_binding_server_identity_invalid"},
            )
        if binding_hash != expected_binding_hash:
            return self._parent_registry_failure("parent_binding_hash_mismatch", registry)
        if admission_idempotency_key in registry.admissions_by_idempotency_key:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "duplicate_parent_admission_idempotency_key"},
            )
        if binding_id in registry.bindings_by_id:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "duplicate_parent_binding_id"},
            )
        binding = DirectedEffectParentBindingV1(
            schema_version=DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
            registry_identity=identity,
            registry_stream_token=registry.stream_token,
            registry_version=version,
            parent_sequence=parent_sequence,
            binding_id=binding_id,
            operation_stream_token=operation_stream_token,
            binding_hash=binding_hash,
            admission_idempotency_key=admission_idempotency_key,
            correlation=correlation,
            actor=actor,
            source_event_id=event_id,
            source_event_seq=seq,
        )
        canonical_event = self._parent_event_canonical_from_binding(binding, previous_version=previous_version)
        descriptor = self._parent_request_descriptor_from_binding(binding, expected_version=previous_version)
        admission = _RegistryAdmission(
            binding=binding,
            request_descriptor=descriptor,
            canonical_event=canonical_event,
            event_id=event_id,
            seq=seq,
        )
        admissions = dict(registry.admissions_by_idempotency_key)
        admissions[admission_idempotency_key] = admission
        bindings = dict(registry.bindings_by_id)
        bindings[binding_id] = binding
        return _ParentRegistry(
            identity=registry.identity,
            stream_token=registry.stream_token,
            registry_version=version,
            source_head_seq=seq,
            next_expected_seq=seq + 1,
            next_parent_sequence=parent_sequence + 1,
            open_binding=binding,
            admissions_by_idempotency_key=admissions,
            bindings_by_id=bindings,
            sealed_inventories_by_binding_id=registry.sealed_inventories_by_binding_id,
            ready_inventories_by_binding_id=registry.ready_inventories_by_binding_id,
        )

    def _apply_parent_closed(
        self,
        registry: _ParentRegistry,
        record: Mapping[str, Any],
    ) -> _ParentRegistry | DirectedEffectOperationResultV1:
        payload = record.get("payload")
        v1_fields = {
            "schema_version",
            "stable_registry_identity",
            "previous_version",
            "version",
            "parent_sequence",
            "binding_id",
            "close_evidence_ref",
            "close_evidence_hash",
            "actor",
            "recorded_at",
        }
        v2_fields = (v1_fields - {"recorded_at"}) | {
            "settlement_outcome",
            "terminal_intent_hash",
            "operation_source_head_seq",
            "receipt_summary_hash",
            "receipt_count",
            "failed_receipt_count",
            "dead_letter_count",
            "aborted_count",
        }
        if not isinstance(payload, dict):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_payload_fields_invalid"},
            )
        close_schema = payload.get("schema_version")
        if close_schema not in {
            DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
            DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2,
        }:
            return self._parent_registry_failure(
                "strict_stream_unknown_schema",
                registry,
                {"observed_schema_version": close_schema},
            )
        expected_payload_fields = v1_fields if close_schema == DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1 else v2_fields
        if set(payload) != expected_payload_fields:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_payload_fields_invalid"},
            )
        raw_identity = payload.get("stable_registry_identity")
        if not isinstance(raw_identity, Mapping):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_registry_identity_invalid"},
            )
        try:
            identity = DirectedEffectParentRegistryIdentityV1.from_record(raw_identity)
        except (TypeError, ValueError) as exc:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_registry_identity_invalid", "error_type": type(exc).__name__},
            )
        if identity != registry.identity:
            return self._parent_registry_failure(
                self._registry_identity_mismatch_code(registry.identity, identity),
                registry,
                {"observed_registry_identity": identity.to_record()},
            )
        previous_version = payload.get("previous_version")
        version = payload.get("version")
        parent_sequence = payload.get("parent_sequence")
        seq = record.get("seq")
        integer_values = (previous_version, version, parent_sequence, seq)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_integer_fields_invalid"},
            )
        previous_version = cast(int, previous_version)
        version = cast(int, version)
        parent_sequence = cast(int, parent_sequence)
        seq = cast(int, seq)
        if previous_version != registry.registry_version or version != previous_version + 1 or seq != version:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {
                    "reason": "parent_registry_version_not_monotonic",
                    "previous_version": previous_version,
                    "version": version,
                    "seq": seq,
                },
            )
        close_evidence_ref = payload.get("close_evidence_ref")
        actor = payload.get("actor")
        if any(
            not isinstance(value, str) or not value.strip() or value != value.strip()
            for value in (close_evidence_ref, actor)
        ):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_string_fields_invalid"},
            )
        if not _is_canonical_sha256(payload.get("close_evidence_hash")):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_evidence_hash_invalid"},
            )
        if close_schema == DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1 and not _is_timezone_aware_timestamp(
            payload.get("recorded_at")
        ):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_recorded_at_invalid"},
            )
        if close_schema == DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2:
            if payload.get("settlement_outcome") not in {"completed", "failed", "suspended"}:
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {"reason": "parent_closed_settlement_outcome_invalid"},
                )
            if not _is_canonical_sha256(payload.get("terminal_intent_hash")) or not _is_canonical_sha256(
                payload.get("receipt_summary_hash")
            ):
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {"reason": "parent_closed_settlement_hash_invalid"},
                )
            count_fields = (
                "operation_source_head_seq",
                "receipt_count",
                "failed_receipt_count",
                "dead_letter_count",
                "aborted_count",
            )
            if any(
                isinstance(payload.get(field), bool)
                or not isinstance(payload.get(field), int)
                or cast(int, payload[field]) < 0
                for field in count_fields
            ):
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {"reason": "parent_closed_settlement_counts_invalid"},
                )
            receipt_count = cast(int, payload["receipt_count"])
            failed_receipt_count = cast(int, payload["failed_receipt_count"])
            dead_letter_count = cast(int, payload["dead_letter_count"])
            aborted_count = cast(int, payload["aborted_count"])
            if failed_receipt_count > receipt_count:
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {"reason": "parent_closed_failed_receipt_count_invalid"},
                )
            ready = registry.ready_inventories_by_binding_id.get(str(payload.get("binding_id") or ""))
            if ready is None or receipt_count + dead_letter_count + aborted_count != len(ready.ordered_operation_ids):
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {"reason": "parent_closed_inventory_counts_invalid"},
                )
            if payload.get("settlement_outcome") == "completed" and (failed_receipt_count > 0 or dead_letter_count > 0):
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {"reason": "parent_closed_completed_outcome_conflict"},
                )
            terminal_intent_hash = cast(str, payload["terminal_intent_hash"])
            expected_close_evidence_hash = _hash_token(
                {
                    "terminal_intent_hash": terminal_intent_hash,
                    "settlement_outcome": payload["settlement_outcome"],
                    "operation_source_head_seq": payload["operation_source_head_seq"],
                    "receipt_summary_hash": payload["receipt_summary_hash"],
                }
            )
            if (
                payload.get("close_evidence_ref") != f"settlement://{terminal_intent_hash}"
                or payload.get("close_evidence_hash") != expected_close_evidence_hash
            ):
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {"reason": "parent_closed_settlement_evidence_binding_invalid"},
                )
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_event_id_invalid"},
            )
        open_binding = registry.open_binding
        if open_binding is None:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_closed_without_open_binding"},
            )
        if parent_sequence != open_binding.parent_sequence:
            return self._parent_registry_failure(
                "parent_binding_conflict",
                registry,
                {
                    "reason": "parent_closed_sequence_mismatch",
                    "expected_parent_sequence": open_binding.parent_sequence,
                    "observed_parent_sequence": parent_sequence,
                },
            )
        if payload.get("binding_id") != open_binding.binding_id:
            return self._parent_registry_failure(
                "parent_binding_conflict",
                registry,
                {
                    "reason": "parent_closed_binding_mismatch",
                    "expected_binding_id": open_binding.binding_id,
                    "observed_binding_id": payload.get("binding_id"),
                },
            )
        return _ParentRegistry(
            identity=registry.identity,
            stream_token=registry.stream_token,
            registry_version=version,
            source_head_seq=seq,
            next_expected_seq=seq + 1,
            next_parent_sequence=registry.next_parent_sequence,
            open_binding=None,
            admissions_by_idempotency_key=registry.admissions_by_idempotency_key,
            bindings_by_id=registry.bindings_by_id,
            sealed_inventories_by_binding_id=registry.sealed_inventories_by_binding_id,
            ready_inventories_by_binding_id=registry.ready_inventories_by_binding_id,
            settlement_close_proof=(
                dict(payload) if close_schema == DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2 else None
            ),
        )

    def _apply_parent_inventory_sealed(
        self,
        registry: _ParentRegistry,
        record: Mapping[str, Any],
    ) -> _ParentRegistry | DirectedEffectOperationResultV1:
        """Strictly reduce one immutable parent inventory seal fact."""

        payload = record.get("payload")
        expected_payload_fields = {
            "schema_version",
            "stable_registry_identity",
            "previous_version",
            "version",
            "parent_sequence",
            "binding_id",
            "members",
            "member_count",
            "inventory_hash",
            "actor",
            "recorded_at",
        }
        if not isinstance(payload, dict) or set(payload) != expected_payload_fields:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_payload_fields_invalid"},
            )
        if payload.get("schema_version") != DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1:
            return self._parent_registry_failure(
                "strict_stream_unknown_schema",
                registry,
                {"observed_schema_version": payload.get("schema_version")},
            )
        raw_identity = payload.get("stable_registry_identity")
        if not isinstance(raw_identity, Mapping):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_registry_identity_invalid"},
            )
        try:
            identity = DirectedEffectParentRegistryIdentityV1.from_record(raw_identity)
        except (TypeError, ValueError) as exc:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {
                    "reason": "parent_inventory_sealed_registry_identity_invalid",
                    "error_type": type(exc).__name__,
                },
            )
        if identity != registry.identity:
            return self._parent_registry_failure(
                self._registry_identity_mismatch_code(registry.identity, identity),
                registry,
                {"observed_registry_identity": identity.to_record()},
            )

        previous_version = payload.get("previous_version")
        version = payload.get("version")
        parent_sequence = payload.get("parent_sequence")
        member_count = payload.get("member_count")
        seq = record.get("seq")
        integer_values = (previous_version, version, parent_sequence, member_count, seq)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_integer_fields_invalid"},
            )
        previous_version = cast(int, previous_version)
        version = cast(int, version)
        parent_sequence = cast(int, parent_sequence)
        member_count = cast(int, member_count)
        seq = cast(int, seq)
        if previous_version != registry.registry_version or version != previous_version + 1 or seq != version:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {
                    "reason": "parent_registry_version_not_monotonic",
                    "previous_version": previous_version,
                    "version": version,
                    "seq": seq,
                },
            )

        binding_id = payload.get("binding_id")
        actor = payload.get("actor")
        if any(
            not isinstance(value, str) or not value.strip() or value != value.strip() for value in (binding_id, actor)
        ):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_string_fields_invalid"},
            )
        if not _is_timezone_aware_timestamp(payload.get("recorded_at")):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_recorded_at_invalid"},
            )
        if not _is_canonical_sha256(payload.get("inventory_hash")):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_hash_invalid"},
            )
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id != event_id.strip():
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_event_id_invalid"},
            )

        binding_id = cast(str, binding_id)
        actor = cast(str, actor)
        open_binding = registry.open_binding
        if open_binding is None:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_without_open_binding"},
            )
        if open_binding.binding_id != binding_id or open_binding.parent_sequence != parent_sequence:
            return self._parent_registry_failure(
                "parent_binding_conflict",
                registry,
                {
                    "reason": "parent_inventory_sealed_binding_mismatch",
                    "expected_binding_id": open_binding.binding_id,
                    "observed_binding_id": binding_id,
                    "expected_parent_sequence": open_binding.parent_sequence,
                    "observed_parent_sequence": parent_sequence,
                },
            )
        if binding_id in registry.sealed_inventories_by_binding_id:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "duplicate_parent_inventory_seal", "binding_id": binding_id},
            )

        raw_members = payload.get("members")
        if not isinstance(raw_members, list) or not 1 <= member_count <= 64 or len(raw_members) != member_count:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_member_count_invalid"},
            )
        members: list[DirectedEffectInventoryMemberV1] = []
        seen_tool_call_ids: set[str] = set()
        seen_effect_ids: set[str] = set()
        seen_operation_ids: set[str] = set()
        for expected_ordinal, raw_member in enumerate(raw_members):
            if not isinstance(raw_member, Mapping):
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {"reason": "parent_inventory_sealed_member_invalid", "ordinal": expected_ordinal},
                )
            try:
                member = DirectedEffectInventoryMemberV1.from_record(raw_member)
            except (TypeError, ValueError) as exc:
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {
                        "reason": "parent_inventory_sealed_member_invalid",
                        "ordinal": expected_ordinal,
                        "error_type": type(exc).__name__,
                    },
                )
            expected_effect_id = _inventory_effect_id(
                binding_id=binding_id,
                tool_call_id=member.tool_call_id,
                intended_effect_fingerprint=member.intended_effect_fingerprint,
            )
            expected_operation_id = _operation_id(
                binding_id=binding_id,
                tool_call_id=member.tool_call_id,
                effect_id=expected_effect_id,
            )
            if (
                member.ordinal != expected_ordinal
                or member.effect_id != expected_effect_id
                or member.operation_id != expected_operation_id
                or member.tool_call_id in seen_tool_call_ids
                or member.effect_id in seen_effect_ids
                or member.operation_id in seen_operation_ids
            ):
                return self._parent_registry_failure(
                    "strict_stream_corruption",
                    registry,
                    {
                        "reason": "parent_inventory_sealed_member_identity_invalid",
                        "ordinal": expected_ordinal,
                    },
                )
            seen_tool_call_ids.add(member.tool_call_id)
            seen_effect_ids.add(member.effect_id)
            seen_operation_ids.add(member.operation_id)
            members.append(member)
        frozen_members = tuple(members)
        inventory_hash = cast(str, payload["inventory_hash"])
        if inventory_hash != _inventory_hash(binding_id, frozen_members):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_sealed_hash_mismatch"},
            )

        seals = dict(registry.sealed_inventories_by_binding_id)
        seals[binding_id] = _SealedDirectedEffectInventory(
            binding_id=binding_id,
            members=frozen_members,
            inventory_hash=inventory_hash,
            actor=actor,
            event_id=event_id,
            seq=seq,
        )
        return _ParentRegistry(
            identity=registry.identity,
            stream_token=registry.stream_token,
            registry_version=version,
            source_head_seq=seq,
            next_expected_seq=seq + 1,
            next_parent_sequence=registry.next_parent_sequence,
            open_binding=open_binding,
            admissions_by_idempotency_key=registry.admissions_by_idempotency_key,
            bindings_by_id=registry.bindings_by_id,
            sealed_inventories_by_binding_id=seals,
            ready_inventories_by_binding_id=registry.ready_inventories_by_binding_id,
        )

    def _apply_parent_inventory_ready(
        self,
        registry: _ParentRegistry,
        record: Mapping[str, Any],
    ) -> _ParentRegistry | DirectedEffectOperationResultV1:
        """Strictly reduce one registry-owned admission-readiness proof."""

        payload = record.get("payload")
        expected_fields = {
            "schema_version",
            "stable_registry_identity",
            "previous_version",
            "version",
            "parent_sequence",
            "binding_id",
            "inventory_hash",
            "ordered_operation_ids",
            "admission_set_hash",
            "operation_source_head_seq",
            "actor",
            "recorded_at",
        }
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_payload_fields_invalid"},
            )
        if payload.get("schema_version") != DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1:
            return self._parent_registry_failure(
                "strict_stream_unknown_schema",
                registry,
                {"observed_schema_version": payload.get("schema_version")},
            )
        raw_identity = payload.get("stable_registry_identity")
        if not isinstance(raw_identity, Mapping):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_registry_identity_invalid"},
            )
        try:
            identity = DirectedEffectParentRegistryIdentityV1.from_record(raw_identity)
        except (TypeError, ValueError) as exc:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {
                    "reason": "parent_inventory_ready_registry_identity_invalid",
                    "error_type": type(exc).__name__,
                },
            )
        if identity != registry.identity:
            return self._parent_registry_failure(
                self._registry_identity_mismatch_code(registry.identity, identity),
                registry,
                {"observed_registry_identity": identity.to_record()},
            )
        previous_version = payload.get("previous_version")
        version = payload.get("version")
        parent_sequence = payload.get("parent_sequence")
        operation_head = payload.get("operation_source_head_seq")
        seq = record.get("seq")
        integer_values = (previous_version, version, parent_sequence, operation_head, seq)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_integer_fields_invalid"},
            )
        previous_version = cast(int, previous_version)
        version = cast(int, version)
        parent_sequence = cast(int, parent_sequence)
        operation_head = cast(int, operation_head)
        seq = cast(int, seq)
        if previous_version != registry.registry_version or version != previous_version + 1 or seq != version:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_registry_version_not_monotonic"},
            )
        if operation_head < 1:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_operation_head_invalid"},
            )
        binding_id = payload.get("binding_id")
        actor = payload.get("actor")
        if any(
            not isinstance(value, str) or not value.strip() or value != value.strip() for value in (binding_id, actor)
        ):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_string_fields_invalid"},
            )
        if actor != "roles.kernel":
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_actor_invalid"},
            )
        if not _is_canonical_sha256(payload.get("inventory_hash")) or not _is_canonical_sha256(
            payload.get("admission_set_hash")
        ):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_hash_invalid"},
            )
        if not _is_timezone_aware_timestamp(payload.get("recorded_at")):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_recorded_at_invalid"},
            )
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id != event_id.strip():
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_event_id_invalid"},
            )
        binding_id = cast(str, binding_id)
        open_binding = registry.open_binding
        if (
            open_binding is None
            or open_binding.binding_id != binding_id
            or open_binding.parent_sequence != parent_sequence
        ):
            return self._parent_registry_failure(
                "parent_binding_conflict",
                registry,
                {"reason": "parent_inventory_ready_binding_mismatch"},
            )
        sealed = registry.sealed_inventories_by_binding_id.get(binding_id)
        if sealed is None:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_without_seal"},
            )
        if binding_id in registry.ready_inventories_by_binding_id:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "duplicate_parent_inventory_ready"},
            )
        inventory_hash = cast(str, payload["inventory_hash"])
        if inventory_hash != sealed.inventory_hash:
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_inventory_hash_mismatch"},
            )
        raw_operation_ids = payload.get("ordered_operation_ids")
        expected_operation_ids = tuple(member.operation_id for member in sealed.members)
        if (
            not isinstance(raw_operation_ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in raw_operation_ids)
            or tuple(raw_operation_ids) != expected_operation_ids
            or len(set(raw_operation_ids)) != len(raw_operation_ids)
        ):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_operation_ids_invalid"},
            )
        if operation_head != len(expected_operation_ids):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_operation_head_not_exact_admission_set"},
            )
        admission_set_hash = cast(str, payload["admission_set_hash"])
        if admission_set_hash != _admission_set_hash(
            binding_id=binding_id,
            inventory_hash=inventory_hash,
            ordered_operation_ids=expected_operation_ids,
            operation_source_head_seq=operation_head,
        ):
            return self._parent_registry_failure(
                "strict_stream_corruption",
                registry,
                {"reason": "parent_inventory_ready_admission_set_hash_mismatch"},
            )
        ready_facts = dict(registry.ready_inventories_by_binding_id)
        ready_facts[binding_id] = _ReadyDirectedEffectInventory(
            binding_id=binding_id,
            inventory_hash=inventory_hash,
            ordered_operation_ids=expected_operation_ids,
            admission_set_hash=admission_set_hash,
            operation_source_head_seq=operation_head,
            event_id=event_id,
            seq=seq,
        )
        return _ParentRegistry(
            identity=registry.identity,
            stream_token=registry.stream_token,
            registry_version=version,
            source_head_seq=seq,
            next_expected_seq=seq + 1,
            next_parent_sequence=registry.next_parent_sequence,
            open_binding=open_binding,
            admissions_by_idempotency_key=registry.admissions_by_idempotency_key,
            bindings_by_id=registry.bindings_by_id,
            sealed_inventories_by_binding_id=registry.sealed_inventories_by_binding_id,
            ready_inventories_by_binding_id=ready_facts,
        )

    def _parent_event_canonical(
        self,
        command: AdmitDirectedEffectParentCommandV1,
        registry: _ParentRegistry,
    ) -> dict[str, object]:
        parent_sequence = registry.next_parent_sequence
        version = registry.registry_version + 1
        binding_id = _binding_id(registry.identity, parent_sequence)
        operation_stream_token = _operation_stream_token(binding_id)
        binding_hash = self._binding_hash(
            identity=registry.identity,
            registry_stream_token=registry.stream_token,
            registry_version=version,
            parent_sequence=parent_sequence,
            binding_id=binding_id,
            operation_stream_token=operation_stream_token,
            admission_idempotency_key=command.admission_idempotency_key,
            correlation=command.correlation,
            actor=command.actor,
            source_event_seq=command.expected_seq,
        )
        return {
            "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
            "stable_registry_identity": registry.identity.to_record(),
            "previous_version": command.expected_version,
            "version": version,
            "parent_sequence": parent_sequence,
            "binding_id": binding_id,
            "operation_stream_token": operation_stream_token,
            "binding_hash": binding_hash,
            "admission_idempotency_key": command.admission_idempotency_key,
            "correlation": command.correlation.to_record(),
            "actor": command.actor,
        }

    @staticmethod
    def _parent_event_canonical_from_binding(
        binding: DirectedEffectParentBindingV1,
        *,
        previous_version: int,
    ) -> dict[str, object]:
        return {
            "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
            "stable_registry_identity": binding.registry_identity.to_record(),
            "previous_version": previous_version,
            "version": binding.registry_version,
            "parent_sequence": binding.parent_sequence,
            "binding_id": binding.binding_id,
            "operation_stream_token": binding.operation_stream_token,
            "binding_hash": binding.binding_hash,
            "admission_idempotency_key": binding.admission_idempotency_key,
            "correlation": binding.correlation.to_record(),
            "actor": binding.actor,
        }

    @staticmethod
    def _binding_hash(
        *,
        identity: DirectedEffectParentRegistryIdentityV1,
        registry_stream_token: str,
        registry_version: int,
        parent_sequence: int,
        binding_id: str,
        operation_stream_token: str,
        admission_idempotency_key: str,
        correlation: ParentCorrelationV1,
        actor: str,
        source_event_seq: int,
    ) -> str:
        return _hash_token(
            {
                "schema_version": DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
                "stable_registry_identity": identity.to_record(),
                "registry_stream_token": registry_stream_token,
                "registry_version": registry_version,
                "parent_sequence": parent_sequence,
                "binding_id": binding_id,
                "operation_stream_token": operation_stream_token,
                "admission_idempotency_key": admission_idempotency_key,
                "correlation": correlation.to_record(),
                "actor": actor,
                "source_event_seq": source_event_seq,
            }
        )

    @staticmethod
    def _parent_request_descriptor(command: AdmitDirectedEffectParentCommandV1) -> dict[str, object]:
        return {
            "stable_registry_identity": DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
                command.execution_attempt
            ).to_record(),
            "admission_idempotency_key": command.admission_idempotency_key,
            "correlation": command.correlation.to_record(),
            "actor": command.actor,
            "expected_version": command.expected_version,
            "expected_seq": command.expected_seq,
        }

    @staticmethod
    def _parent_request_descriptor_from_binding(
        binding: DirectedEffectParentBindingV1,
        *,
        expected_version: int,
    ) -> dict[str, object]:
        return {
            "stable_registry_identity": binding.registry_identity.to_record(),
            "admission_idempotency_key": binding.admission_idempotency_key,
            "correlation": binding.correlation.to_record(),
            "actor": binding.actor,
            "expected_version": expected_version,
            "expected_seq": binding.source_event_seq,
        }

    def _parent_replay_result(
        self,
        command: AdmitDirectedEffectParentCommandV1,
        registry: _ParentRegistry,
        committed: _RegistryAdmission,
    ) -> DirectedEffectOperationResultV1:
        requested = self._parent_request_descriptor(command)
        observed = dict(committed.request_descriptor)
        if requested != observed:
            drift_fields = sorted(field for field in requested if requested.get(field) != observed.get(field))
            return self._parent_registry_conflict(
                "parent_admission_idempotency_conflict",
                registry,
                {
                    "admission_idempotency_key": command.admission_idempotency_key,
                    "drift_fields": drift_fields,
                    "committed_event_id": committed.event_id,
                    "committed_seq": committed.seq,
                },
            )
        return DirectedEffectOperationResultV1(
            ok=True,
            code="parent_idempotent_replay",
            parent_binding=committed.binding,
            parent_registry=self._registry_projection(registry),
            idempotent=True,
            evidence={
                "authoritative_append": False,
                "committed_event_id": committed.event_id,
                "committed_seq": committed.seq,
                "registry_version": registry.registry_version,
                "source_head_seq": registry.source_head_seq,
            },
        )

    def _reconcile_parent_append(
        self,
        command: AdmitDirectedEffectParentCommandV1,
        identity: DirectedEffectParentRegistryIdentityV1,
        canonical_event: Mapping[str, object],
        exc: FactStreamError,
    ) -> DirectedEffectOperationResultV1:
        """Resolve a registry append while the caller still holds authority locks."""

        refreshed = self._load_registry(command.workspace, identity)
        if isinstance(refreshed, DirectedEffectOperationResultV1):
            return self._with_reconciliation_evidence(refreshed, exc)
        existing = refreshed.admissions_by_idempotency_key.get(command.admission_idempotency_key)
        if existing is not None:
            if dict(existing.canonical_event) != dict(canonical_event):
                conflict = self._parent_registry_conflict(
                    "parent_admission_idempotency_conflict",
                    refreshed,
                    {
                        "admission_idempotency_key": command.admission_idempotency_key,
                        "committed_event_id": existing.event_id,
                        "committed_seq": existing.seq,
                        "expected_canonical_event": dict(canonical_event),
                        "committed_canonical_event": dict(existing.canonical_event),
                    },
                )
                return self._with_reconciliation_evidence(conflict, exc)
            replay = self._parent_replay_result(command, refreshed, existing)
            return self._with_reconciliation_evidence(replay, exc)
        if refreshed.open_binding is not None:
            conflict = self._parent_open_conflict(refreshed, command.admission_idempotency_key)
            return self._with_reconciliation_evidence(conflict, exc)
        if exc.code == "expected_seq_drift":
            code: DirectedEffectOperationCodeV1 = "parent_registry_expected_seq_conflict"
        elif exc.code == "idempotency_conflict":
            code = "parent_admission_idempotency_conflict"
        else:
            code = self._fact_failure_code(exc.code, operation="append")
        return self._parent_registry_conflict(
            code,
            refreshed,
            {
                "reconciled_after_cas": True,
                "fact_stream_code": exc.code,
                "fact_stream_details": dict(exc.details),
                "expected_canonical_event": dict(canonical_event),
            },
        )

    def _validated_parent_binding(
        self,
        command: _ParentBindingReadCommand,
        *,
        require_open: bool,
    ) -> tuple[DirectedEffectParentBindingV1, _ParentRegistry] | DirectedEffectOperationResultV1:
        identity_failure = self._command_identity_failure(
            workspace=command.workspace,
            task_id=command.task_id,
            execution_attempt=command.execution_attempt,
        )
        if identity_failure is not None:
            return identity_failure
        expected_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(command.execution_attempt)
        supplied = command.parent_binding
        expected_registry_stream = _registry_stream_token(expected_identity)
        registry = self._load_registry(command.workspace, expected_identity)
        if isinstance(registry, DirectedEffectOperationResultV1):
            return registry
        identity_code = self._registry_identity_mismatch_code(expected_identity, supplied.registry_identity)
        if expected_identity != supplied.registry_identity:
            return self._parent_failure(
                identity_code,
                binding=supplied,
                registry=registry,
            )
        durable = registry.bindings_by_id.get(supplied.binding_id)
        if durable is None:
            return self._parent_failure(
                "parent_binding_not_found",
                binding=supplied,
                registry=registry,
                evidence={"expected_registry_stream_token": expected_registry_stream},
            )
        mismatch = self._binding_mismatch_result(supplied, durable, registry)
        if mismatch is not None:
            return mismatch
        if require_open and (registry.open_binding is None or registry.open_binding.binding_id != durable.binding_id):
            return self._parent_registry_conflict(
                "parent_closed",
                registry,
                {
                    "requested_binding_id": durable.binding_id,
                    "reason": "binding_is_closed_historical_parent",
                },
                binding=durable,
            )
        return durable, registry

    def _read_stream(
        self,
        workspace: str,
        stream_token: str,
        *,
        max_events: int,
        stream_kind: _StreamKind,
    ) -> _StreamRead | DirectedEffectOperationResultV1:
        try:
            result = query_fact_events(
                QueryFactEventsV1(
                    workspace=workspace,
                    stream=stream_token,
                    limit=max_events + 1,
                    strict_integrity=True,
                )
            )
        except FactStreamError as exc:
            return self._fact_failure_result(exc, operation="read", stream_kind=stream_kind)
        if result.total > max_events or len(result.events) > max_events:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="strict_stream_overload",
                evidence={
                    "stream_kind": stream_kind,
                    "event_total": result.total,
                    "max_events": max_events,
                },
            )
        head_seq = int(result.events[-1].get("seq") or 0) if result.events else 0
        if len(result.events) != result.total or head_seq != result.total:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="strict_stream_corruption",
                evidence={
                    "stream_kind": stream_kind,
                    "reason": "strict_stream_page_or_head_mismatch",
                    "event_total": result.total,
                    "event_count": len(result.events),
                    "head_seq": head_seq,
                },
            )
        return _StreamRead(events=result.events, head_seq=head_seq)

    def _reduce_operation(
        self,
        read: _StreamRead,
        target: DirectedEffectOperationIdentityV1,
        binding: DirectedEffectParentBindingV1,
    ) -> _Aggregate | DirectedEffectOperationResultV1:
        reduced = self._reduce_operation_stream(read, binding, target=target)
        if isinstance(reduced, DirectedEffectOperationResultV1):
            return reduced
        for aggregate in reduced.aggregates:
            if aggregate.operation.operation_id == target.operation_id:
                return aggregate
        return _Aggregate(
            operation=target,
            state=None,
            version=0,
            intended_effect_fingerprint="",
            policy_verdict_hash="",
            expected_receipt_binding_hash="",
            source_head_seq=reduced.source_head_seq,
            last_event_id="",
            transitions=(),
        )

    def _reduce_operation_stream(
        self,
        read: _StreamRead,
        binding: DirectedEffectParentBindingV1,
        *,
        target: DirectedEffectOperationIdentityV1 | None,
    ) -> _OperationStreamReduction | DirectedEffectOperationResultV1:
        """Parse each durable transition once into immutable per-operation facts."""

        states: dict[str, DirectedEffectOperationStateV1] = {}
        versions: dict[str, int] = {}
        semantics: dict[str, tuple[str, str, str]] = {}
        operations: dict[str, DirectedEffectOperationIdentityV1] = {}
        transitions: dict[str, list[_CommittedTransition]] = {}
        last_event_ids: dict[str, str] = {}
        operation_order: list[str] = []
        for record in read.events:
            parsed = self._parse_operation_transition(record, binding)
            if isinstance(parsed, DirectedEffectOperationResultV1):
                return parsed
            operation_id = parsed.operation.operation_id
            prior_operation = operations.get(operation_id)
            if target is not None and operation_id == target.operation_id and parsed.operation != target:
                return self._operation_stream_failure(
                    self._operation_identity_mismatch_code(target, parsed.operation),
                    target,
                    {"event_operation": parsed.operation.to_record()},
                )
            if prior_operation is not None and prior_operation != parsed.operation:
                return self._operation_stream_failure(
                    "operation_identity_conflict",
                    target,
                    {
                        "reason": "ambiguous_operation_identity",
                        "event_id": parsed.event_id,
                        "observed": parsed.operation.to_record(),
                        "expected": prior_operation.to_record(),
                    },
                )
            prior_state = states.get(operation_id)
            prior_version = versions.get(operation_id, 0)
            if parsed.previous_version != prior_version or parsed.version != prior_version + 1:
                return self._operation_stream_failure(
                    "strict_stream_corruption",
                    target,
                    {"reason": "non_monotonic_operation_version", "event_id": parsed.event_id},
                )
            if not self._legal_transition(prior_state, parsed.state):
                return self._operation_stream_failure(
                    "strict_stream_corruption",
                    target,
                    {"reason": "illegal_persisted_transition", "event_id": parsed.event_id},
                )
            descriptor_semantic = self._descriptor_semantic(parsed.descriptor)
            prior_semantic = semantics.get(operation_id)
            if prior_semantic is not None and prior_semantic != descriptor_semantic:
                return self._operation_stream_failure(
                    "deo_semantic_drift",
                    target,
                    {"event_id": parsed.event_id, "observed": descriptor_semantic, "expected": prior_semantic},
                )
            if prior_operation is None:
                operations[operation_id] = parsed.operation
                transitions[operation_id] = []
                operation_order.append(operation_id)
            states[operation_id] = parsed.state
            versions[operation_id] = parsed.version
            semantics[operation_id] = descriptor_semantic
            transitions[operation_id].append(parsed)
            last_event_ids[operation_id] = parsed.event_id
        return _OperationStreamReduction(
            source_head_seq=read.head_seq,
            aggregates=tuple(
                _Aggregate(
                    operation=operations[operation_id],
                    state=states[operation_id],
                    version=versions[operation_id],
                    intended_effect_fingerprint=semantics[operation_id][0],
                    policy_verdict_hash=semantics[operation_id][1],
                    expected_receipt_binding_hash=semantics[operation_id][2],
                    source_head_seq=read.head_seq,
                    last_event_id=last_event_ids[operation_id],
                    transitions=tuple(transitions[operation_id]),
                )
                for operation_id in operation_order
            ),
        )

    @staticmethod
    def _operation_stream_failure(
        code: DirectedEffectOperationCodeV1,
        target: DirectedEffectOperationIdentityV1 | None,
        evidence: Mapping[str, object],
    ) -> DirectedEffectOperationResultV1:
        if target is None:
            return DirectedEffectOperationResultV1(ok=False, code=code, evidence=dict(evidence))
        return DirectedEffectOperationRepository._operation_failure(code, target, None, evidence)

    def _parse_operation_transition(
        self,
        record: Mapping[str, Any],
        binding: DirectedEffectParentBindingV1,
    ) -> _CommittedTransition | DirectedEffectOperationResultV1:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return self._parent_failure(
                "strict_stream_corruption",
                binding=binding,
                evidence={"reason": "operation_payload_not_mapping"},
            )
        schema_version = payload.get("schema_version")
        common_payload_fields = {
            "schema_version",
            "operation",
            "parent_binding_id",
            "state",
            "previous_version",
            "version",
            "replay_descriptor",
        }
        if schema_version == DIRECTED_EFFECT_OPERATION_SCHEMA_V1:
            expected_payload_fields = common_payload_fields | {"recorded_at"}
            if set(payload) != expected_payload_fields or not _is_timezone_aware_timestamp(payload.get("recorded_at")):
                return self._parent_failure(
                    "strict_stream_corruption",
                    binding=binding,
                    evidence={"reason": "operation_v1_payload_fields_or_recorded_at_invalid"},
                )
        elif schema_version == DIRECTED_EFFECT_OPERATION_SCHEMA_V2:
            if set(payload) != common_payload_fields:
                return self._parent_failure(
                    "strict_stream_corruption",
                    binding=binding,
                    evidence={"reason": "operation_v2_payload_fields_invalid"},
                )
        elif schema_version == DIRECTED_EFFECT_OPERATION_SCHEMA_V3:
            if set(payload) != common_payload_fields:
                return self._parent_failure(
                    "strict_stream_corruption",
                    binding=binding,
                    evidence={"reason": "operation_v3_payload_fields_invalid"},
                )
        else:
            return self._parent_failure(
                "strict_stream_unknown_schema",
                binding=binding,
                evidence={"observed_schema_version": schema_version},
            )
        raw_operation = payload.get("operation")
        descriptor = payload.get("replay_descriptor")
        if not isinstance(raw_operation, dict) or not isinstance(descriptor, dict):
            return self._parent_failure("strict_stream_corruption", binding=binding)
        expected_operation_fields = {
            "workspace",
            "task_id",
            "execution_attempt_id",
            "parent_binding_id",
            "parent_sequence",
            "tool_call_id",
            "effect_id",
            "operation_id",
            "operation_stream_token",
        }
        operation_string_fields = expected_operation_fields - {"task_id", "parent_sequence"}
        raw_task_id = raw_operation.get("task_id")
        raw_parent_sequence = raw_operation.get("parent_sequence")
        if (
            set(raw_operation) != expected_operation_fields
            or isinstance(raw_task_id, bool)
            or not isinstance(raw_task_id, int)
            or raw_task_id < 1
            or isinstance(raw_parent_sequence, bool)
            or not isinstance(raw_parent_sequence, int)
            or raw_parent_sequence < 1
            or any(
                not isinstance(raw_operation.get(field), str) or not cast(str, raw_operation[field]).strip()
                for field in operation_string_fields
            )
        ):
            return self._parent_failure(
                "strict_stream_corruption",
                binding=binding,
                evidence={"reason": "operation_identity_types_invalid"},
            )
        try:
            operation = DirectedEffectOperationIdentityV1(**raw_operation)
        except (TypeError, ValueError):
            return self._parent_failure("strict_stream_corruption", binding=binding)
        expected_operation = DirectedEffectOperationIdentityV1(
            workspace=binding.workspace,
            task_id=binding.task_id,
            execution_attempt_id=binding.registry_identity.execution_attempt_id,
            parent_binding_id=binding.binding_id,
            parent_sequence=binding.parent_sequence,
            tool_call_id=operation.tool_call_id,
            effect_id=operation.effect_id,
            operation_id=_operation_id(
                binding_id=binding.binding_id,
                tool_call_id=operation.tool_call_id,
                effect_id=operation.effect_id,
            ),
            operation_stream_token=binding.operation_stream_token,
        )
        if operation != expected_operation:
            return self._parent_failure(
                self._operation_identity_mismatch_code(expected_operation, operation),
                binding=binding,
                evidence={
                    "reason": "persisted_operation_identity_not_canonical",
                    "expected_operation": expected_operation.to_record(),
                    "observed_operation": operation.to_record(),
                },
            )
        if payload.get("parent_binding_id") != binding.binding_id:
            return self._parent_failure("parent_binding_conflict", binding=binding)
        state = payload.get("state")
        if not isinstance(state, str) or state not in {
            "INTENT_COMMITTED",
            "EFFECT_STARTED",
            "RECOVERY_PENDING",
            "RECEIPT_COMMITTED",
            "CLOSED_BY_PARENT",
            "ABORTED",
            "DEAD_LETTER",
        }:
            return self._parent_failure(
                "strict_stream_corruption",
                binding=binding,
                evidence={"reason": "invalid_state"},
            )
        previous_version = payload.get("previous_version")
        version = payload.get("version")
        seq = record.get("seq")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (previous_version, version, seq)):
            return self._parent_failure("strict_stream_corruption", binding=binding)
        previous_version = cast(int, previous_version)
        version = cast(int, version)
        seq = cast(int, seq)
        if seq < 1:
            return self._parent_failure("strict_stream_corruption", binding=binding)
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            return self._parent_failure("strict_stream_corruption", binding=binding)
        descriptor_failure = self._validate_persisted_descriptor(
            descriptor,
            schema_version=cast(str, schema_version),
            previous_version=previous_version,
            seq=seq,
        )
        if descriptor_failure is not None:
            return self._parent_failure(
                "strict_stream_corruption",
                binding=binding,
                evidence=descriptor_failure,
            )
        typed_state = cast(DirectedEffectOperationStateV1, state)
        normalized = self._normalized_transition(
            operation=operation,
            state=typed_state,
            descriptor=descriptor,
        )
        canonical = self._operation_event_canonical(
            operation=operation,
            state=typed_state,
            previous_version=previous_version,
            descriptor=descriptor,
        )
        if record.get("event_type") != _operation_event_type(typed_state):
            return self._parent_failure(
                "strict_stream_corruption",
                binding=binding,
                evidence={"reason": "event_type_state_mismatch"},
            )
        return _CommittedTransition(
            operation=operation,
            state=typed_state,
            previous_version=previous_version,
            version=version,
            descriptor=dict(descriptor),
            normalized=normalized,
            canonical_event=canonical,
            event_id=event_id,
            seq=seq,
        )

    @staticmethod
    def _validate_persisted_descriptor(
        descriptor: Mapping[str, object],
        *,
        schema_version: str,
        previous_version: int,
        seq: int,
    ) -> dict[str, object] | None:
        base_fields = {
            "command",
            "expected_version",
            "expected_seq",
            "actor",
            "reason",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        }
        v3_fields = base_fields | {
            "receipt_ref",
            "receipt_hash",
            "receipt_binding_hash",
            "receipt_outcome",
            "recovery_evidence_ref",
            "recovery_evidence_hash",
            "resolution_evidence_ref",
            "resolution_evidence_hash",
            "terminal_intent_hash",
            "settlement_outcome",
        }
        expected_fields = v3_fields if schema_version == DIRECTED_EFFECT_OPERATION_SCHEMA_V3 else base_fields
        if set(descriptor) != expected_fields:
            return {"reason": "replay_descriptor_fields_invalid"}
        command = descriptor.get("command")
        reason = descriptor.get("reason")
        legacy_commands = {"admit", "claim", "abort"}
        v3_commands = {"commit_receipt", "mark_recovery_pending", "dead_letter", "close_by_parent"}
        allowed_commands = v3_commands if schema_version == DIRECTED_EFFECT_OPERATION_SCHEMA_V3 else legacy_commands
        if command not in allowed_commands:
            return {"reason": "replay_descriptor_command_invalid"}
        expected_version = descriptor.get("expected_version")
        expected_seq = descriptor.get("expected_seq")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or isinstance(expected_seq, bool)
            or not isinstance(expected_seq, int)
            or expected_version != previous_version
            or expected_seq != seq
        ):
            return {"reason": "replay_descriptor_cas_mismatch"}
        string_fields = (
            "actor",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        )
        if any(
            not isinstance(descriptor.get(field), str) or not cast(str, descriptor[field]).strip()
            for field in string_fields
        ):
            return {"reason": "replay_descriptor_semantic_invalid"}
        reason_required = command in {"abort", "mark_recovery_pending", "dead_letter"}
        if not isinstance(reason, str) or reason_required != bool(reason.strip()):
            return {"reason": "replay_descriptor_reason_invalid"}
        if schema_version == DIRECTED_EFFECT_OPERATION_SCHEMA_V3:
            command_evidence = {
                "commit_receipt": ("receipt_ref", "receipt_hash", "receipt_binding_hash", "receipt_outcome"),
                "mark_recovery_pending": ("recovery_evidence_ref", "recovery_evidence_hash"),
                "dead_letter": ("resolution_evidence_ref", "resolution_evidence_hash"),
                "close_by_parent": ("terminal_intent_hash", "settlement_outcome"),
            }
            required = set(command_evidence[command])
            for field in v3_fields - base_fields:
                value = descriptor.get(field)
                if not isinstance(value, str) or (field in required) != bool(value.strip()):
                    return {"reason": "replay_descriptor_evidence_invalid", "field": field}
            if command == "commit_receipt" and descriptor.get("receipt_outcome") not in {"succeeded", "failed"}:
                return {"reason": "replay_descriptor_receipt_outcome_invalid"}
            if command == "close_by_parent" and descriptor.get("settlement_outcome") not in {
                "completed",
                "failed",
                "suspended",
            }:
                return {"reason": "replay_descriptor_settlement_outcome_invalid"}
            for hash_field in (
                "receipt_hash",
                "receipt_binding_hash",
                "recovery_evidence_hash",
                "resolution_evidence_hash",
                "terminal_intent_hash",
            ):
                value = descriptor.get(hash_field)
                if isinstance(value, str) and value and not _is_canonical_sha256(value):
                    return {"reason": "replay_descriptor_hash_invalid", "field": hash_field}
        return None

    def _strict_operation_projection(
        self,
        *,
        command: _Command,
        operation: DirectedEffectOperationIdentityV1,
        kind: _CommandKind,
    ) -> _StrictOperationProjection | DirectedEffectOperationResultV1:
        """Atomically rebuild the durable registry guard and child partition."""

        identity_failure = self._command_identity_failure(
            workspace=command.workspace,
            task_id=command.task_id,
            execution_attempt=command.execution_attempt,
        )
        if identity_failure is not None:
            return identity_failure
        expected_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(command.execution_attempt)
        prepared = self._prepare_guarded_snapshot(command, expected_identity)
        if isinstance(prepared, DirectedEffectOperationResultV1):
            return prepared
        validated = self._validated_parent_binding_from_registry(
            command,
            expected_identity=expected_identity,
            registry_read=_StreamRead(prepared.guard_records(), prepared.proof.guard_head_seq),
            require_open=False,
        )
        if isinstance(validated, DirectedEffectOperationResultV1):
            return validated
        binding, registry = validated
        operation_records = prepared.target_records()
        ready_context: _ReadyOperationContext | None = None
        if kind in {"claim", "abort"}:
            observed_ready_context = self._ready_operation_context(
                command=cast(_ReadyGatedCommand, command),
                operation=operation,
                binding=binding,
                registry=registry,
                operation_events=operation_records,
                operation_head_seq=prepared.proof.target_head_seq,
            )
            if isinstance(observed_ready_context, DirectedEffectOperationResultV1):
                return observed_ready_context
            ready_context = observed_ready_context
        read = _StreamRead(operation_records, prepared.proof.target_head_seq)
        aggregate = self._reduce_operation(read, operation, binding)
        if isinstance(aggregate, DirectedEffectOperationResultV1):
            return aggregate
        return _StrictOperationProjection(
            binding=binding,
            registry=registry,
            aggregate=aggregate,
            operation_records=operation_records,
            ready_context=ready_context,
        )

    @staticmethod
    def _matching_transitions(
        aggregate: _Aggregate,
        *,
        normalized: _NormalizedDirectedEffectTransitionV1,
        canonical_event: Mapping[str, object],
    ) -> tuple[_CommittedTransition, ...]:
        """Locate exact schema-neutral semantics with canonical event continuity."""

        return tuple(
            transition
            for transition in aggregate.transitions
            if transition.normalized == normalized and dict(transition.canonical_event) == dict(canonical_event)
        )

    @staticmethod
    def _claim_grant(
        *,
        command: _Command,
        operation: DirectedEffectOperationIdentityV1,
        projection: _StrictOperationProjection,
        transition: _CommittedTransition,
    ) -> DirectedEffectClaimGrantV1:
        ready_context = projection.ready_context
        if ready_context is None:
            raise ValueError("confirmed claim requires durable inventory readiness")
        unsigned_record = {
            "schema_version": DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
            "execution_attempt": command.execution_attempt.to_record(),
            "parent_binding": projection.binding.to_record(),
            "operation": operation.to_record(),
            "member": ready_context.member.to_record(),
            "inventory_hash": ready_context.sealed.inventory_hash,
            "operation_version": transition.version,
            "claim_event_id": transition.event_id,
            "claim_event_seq": transition.seq,
            "operation_source_head_seq": transition.seq,
            "parent_registry_source_head_seq": projection.registry.source_head_seq,
        }
        return DirectedEffectClaimGrantV1(
            schema_version=DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
            execution_attempt=command.execution_attempt,
            parent_binding=projection.binding,
            operation=operation,
            member=ready_context.member,
            inventory_hash=ready_context.sealed.inventory_hash,
            operation_version=transition.version,
            claim_event_id=transition.event_id,
            claim_event_seq=transition.seq,
            operation_source_head_seq=transition.seq,
            parent_registry_source_head_seq=projection.registry.source_head_seq,
            grant_hash=_hash_token(unsigned_record),
        )

    def _confirmed_mutation_result(
        self,
        *,
        command: _Command,
        operation: DirectedEffectOperationIdentityV1,
        kind: _CommandKind,
        target: DirectedEffectOperationStateV1,
        projection: _StrictOperationProjection,
        transition: _CommittedTransition,
        expected_previous_version: int,
        guarded_attempt: int,
        receipt: GuardedFactAppendedV1 | None,
        canonical_receipt: GuardedFactAppendedV1,
        reconciliation_error: FactStreamError | None = None,
    ) -> DirectedEffectOperationResultV1:
        """Return success only after all receipt and strict projection proofs agree."""

        receipt_drift_fields = self._guarded_receipt_proof_drift(receipt, canonical_receipt)
        canonical_receipt_mismatch = (
            canonical_receipt.workspace != command.workspace
            or canonical_receipt.stream != projection.binding.operation_stream_token
            or canonical_receipt.event_id != transition.event_id
            or canonical_receipt.appended_seq != transition.seq
        )
        snapshot = self._project_snapshot(projection.aggregate)
        projection_mismatch = (
            transition.operation != operation
            or transition.state != target
            or transition.previous_version != expected_previous_version
            or transition.version != expected_previous_version + 1
            or projection.aggregate.state != target
            or projection.aggregate.version != transition.version
            or projection.aggregate.last_event_id != transition.event_id
            or snapshot is None
            or snapshot.operation != operation
            or snapshot.state != target
            or snapshot.version != transition.version
            or snapshot.last_event_id != transition.event_id
        )
        if receipt_drift_fields or canonical_receipt_mismatch or projection_mismatch:
            if receipt_drift_fields:
                reason = "receipt_identity_mismatch"
            elif canonical_receipt_mismatch:
                reason = "canonical_receipt_identity_mismatch"
            else:
                reason = "confirmed_projection_mismatch"
            evidence: dict[str, object] = {
                "reason": reason,
                "parent_binding_id": projection.binding.binding_id,
                "transition_event_id": transition.event_id,
                "transition_seq": transition.seq,
                "transition_version": transition.version,
                "fresh_state": projection.aggregate.state,
                "fresh_version": projection.aggregate.version,
                "fresh_source_head_seq": projection.aggregate.source_head_seq,
                "receipt_drift_fields": receipt_drift_fields,
                "canonical_receipt": self._guarded_receipt_record(canonical_receipt),
            }
            if receipt is not None:
                evidence["observed_receipt"] = self._guarded_receipt_record(receipt)
            if reconciliation_error is not None:
                evidence.update(
                    {
                        "reconciled_after_guarded_error": True,
                        "fact_stream_code": reconciliation_error.code,
                        "fact_stream_details": dict(reconciliation_error.details),
                    }
                )
            return self._operation_failure(
                "guarded_receipt_mismatch",
                operation,
                projection.aggregate,
                evidence,
            )

        evidence = {
            **self._guarded_receipt_event_evidence(canonical_receipt),
            "authoritative_append": False,
            "authoritative_effect_receipt": True,
            "append_disposition": "committed_or_exact_replay",
            "guarded_attempt": guarded_attempt,
            "parent_binding_id": projection.binding.binding_id,
            "source_head_seq": projection.aggregate.source_head_seq,
            "guarded_receipt": self._guarded_receipt_record(canonical_receipt),
            "guarded_semantic_digest": canonical_receipt.semantic_digest,
        }
        evidence.update(self._transition_descriptor_evidence(transition))
        if reconciliation_error is not None:
            evidence.update(
                {
                    "reconciled_after_guarded_error": True,
                    "fact_stream_code": reconciliation_error.code,
                    "fact_stream_details": dict(reconciliation_error.details),
                }
            )
        claim_grant = None
        if kind == "claim":
            claim_grant = self._claim_grant(
                command=command,
                operation=operation,
                projection=projection,
                transition=transition,
            )
        return DirectedEffectOperationResultV1(
            ok=True,
            code=cast(
                Literal[
                    "admitted",
                    "effect_claimed",
                    "aborted",
                    "receipt_committed",
                    "recovery_pending",
                    "dead_lettered",
                    "closed_by_parent",
                ],
                {
                    "admit": "admitted",
                    "claim": "effect_claimed",
                    "abort": "aborted",
                    "commit_receipt": "receipt_committed",
                    "mark_recovery_pending": "recovery_pending",
                    "dead_letter": "dead_lettered",
                    "close_by_parent": "closed_by_parent",
                }[kind],
            ),
            operation=operation,
            state=target,
            version=transition.version,
            snapshot=snapshot,
            evidence=evidence,
            claim_grant=claim_grant,
        )

    @staticmethod
    def _guarded_receipt_event_evidence(
        receipt: GuardedFactAppendedV1,
    ) -> dict[str, object]:
        """Project the canonical event identity used by success evidence."""

        return {
            "event_id": receipt.event_id,
            "appended_seq": receipt.appended_seq,
        }

    @staticmethod
    def _guarded_receipt_proof_drift(
        receipt: GuardedFactAppendedV1 | None,
        canonical_receipt: GuardedFactAppendedV1,
    ) -> tuple[str, ...]:
        """Return every public receipt field that differs from exact replay proof."""

        receipt_fields = (
            "event_id",
            "workspace",
            "stream",
            "storage_path",
            "appended_at",
            "appended_seq",
            "semantic_digest",
        )
        if receipt is None:
            return ()
        return tuple(field for field in receipt_fields if getattr(receipt, field) != getattr(canonical_receipt, field))

    @staticmethod
    def _guarded_receipt_record(
        receipt: GuardedFactAppendedV1,
    ) -> dict[str, object]:
        """Project every public guarded receipt field without reinterpretation."""

        return {
            "event_id": receipt.event_id,
            "workspace": receipt.workspace,
            "stream": receipt.stream,
            "storage_path": receipt.storage_path,
            "appended_at": receipt.appended_at,
            "appended_seq": receipt.appended_seq,
            "semantic_digest": receipt.semantic_digest,
        }

    def _guarded_receipt_failure(
        self,
        *,
        operation: DirectedEffectOperationIdentityV1,
        aggregate: _Aggregate,
        reason: str,
        evidence: Mapping[str, object],
    ) -> DirectedEffectOperationResultV1:
        return self._operation_failure(
            "guarded_receipt_mismatch",
            operation,
            aggregate,
            {"reason": reason, **dict(evidence)},
        )

    def _confirm_guarded_append(
        self,
        *,
        command: _Command,
        operation: DirectedEffectOperationIdentityV1,
        kind: _CommandKind,
        target: DirectedEffectOperationStateV1,
        canonical_event: Mapping[str, object],
        normalized: _NormalizedDirectedEffectTransitionV1,
        expected_previous_version: int,
        guarded_attempt: int,
        receipt: GuardedFactAppendedV1,
        guarded_command: AppendIfGuardedSnapshotCommandV1,
    ) -> DirectedEffectOperationResultV1:
        """Strictly confirm one ambiguous guarded commit or exact replay receipt."""

        projection = self._strict_operation_projection(command=command, operation=operation, kind=kind)
        if isinstance(projection, DirectedEffectOperationResultV1):
            return projection
        matches = self._matching_transitions(
            projection.aggregate,
            normalized=normalized,
            canonical_event=canonical_event,
        )
        if len(matches) != 1:
            return self._guarded_receipt_failure(
                operation=operation,
                aggregate=projection.aggregate,
                reason="canonical_transition_not_unique",
                evidence={
                    "matching_transition_count": len(matches),
                    "receipt_event_id": receipt.event_id,
                    "receipt_seq": receipt.appended_seq,
                    "parent_binding_id": projection.binding.binding_id,
                },
            )
        try:
            canonical_receipt = append_if_guarded_snapshot(guarded_command)
        except FactStreamError as exc:
            return self._guarded_receipt_failure(
                operation=operation,
                aggregate=projection.aggregate,
                reason="public_exact_replay_receipt_failed",
                evidence={
                    "fact_stream_code": exc.code,
                    "fact_stream_details": dict(exc.details),
                    "parent_binding_id": projection.binding.binding_id,
                    "transition_event_id": matches[0].event_id,
                    "transition_seq": matches[0].seq,
                },
            )
        return self._confirmed_mutation_result(
            command=command,
            operation=operation,
            kind=kind,
            target=target,
            projection=projection,
            transition=matches[0],
            expected_previous_version=expected_previous_version,
            guarded_attempt=guarded_attempt,
            receipt=receipt,
            canonical_receipt=canonical_receipt,
        )

    def _reconcile_operation_append(
        self,
        *,
        command: _Command,
        operation: DirectedEffectOperationIdentityV1,
        kind: _CommandKind,
        target: DirectedEffectOperationStateV1,
        canonical_event: Mapping[str, object],
        normalized: _NormalizedDirectedEffectTransitionV1,
        expected_previous_version: int,
        guarded_attempt: int,
        exc: FactStreamError,
        receipt: GuardedFactAppendedV1 | None,
        guarded_command: AppendIfGuardedSnapshotCommandV1,
    ) -> DirectedEffectOperationResultV1:
        """Reconcile a non-drift error that may have crossed durability."""

        projection = self._strict_operation_projection(command=command, operation=operation, kind=kind)
        if isinstance(projection, DirectedEffectOperationResultV1):
            return self._with_guarded_reconciliation_evidence(projection, exc)
        matches = self._matching_transitions(
            projection.aggregate,
            normalized=normalized,
            canonical_event=canonical_event,
        )
        if len(matches) == 1:
            try:
                canonical_receipt = append_if_guarded_snapshot(guarded_command)
            except FactStreamError as replay_exc:
                failure = self._guarded_receipt_failure(
                    operation=operation,
                    aggregate=projection.aggregate,
                    reason="public_exact_replay_receipt_failed",
                    evidence={
                        "fact_stream_code": replay_exc.code,
                        "fact_stream_details": dict(replay_exc.details),
                        "parent_binding_id": projection.binding.binding_id,
                        "transition_event_id": matches[0].event_id,
                        "transition_seq": matches[0].seq,
                    },
                )
                return self._with_guarded_reconciliation_evidence(failure, exc)
            return self._confirmed_mutation_result(
                command=command,
                operation=operation,
                kind=kind,
                target=target,
                projection=projection,
                transition=matches[0],
                expected_previous_version=expected_previous_version,
                guarded_attempt=guarded_attempt,
                receipt=receipt,
                canonical_receipt=canonical_receipt,
                reconciliation_error=exc,
            )
        if len(matches) > 1 or receipt is not None:
            return self._guarded_receipt_failure(
                operation=operation,
                aggregate=projection.aggregate,
                reason="reconciliation_transition_not_unique",
                evidence={
                    "matching_transition_count": len(matches),
                    "receipt_event_id": receipt.event_id if receipt is not None else "",
                    "receipt_seq": receipt.appended_seq if receipt is not None else 0,
                    "reconciled_after_guarded_error": True,
                    "fact_stream_code": exc.code,
                    "fact_stream_details": dict(exc.details),
                },
            )
        attempt_failure = self.validate_attempt(command.workspace, command.execution_attempt)
        if attempt_failure is not None:
            return self._with_guarded_reconciliation_evidence(attempt_failure, exc)
        if (
            projection.registry.open_binding is None
            or projection.registry.open_binding.binding_id != projection.binding.binding_id
        ):
            closed = self._parent_registry_conflict(
                "parent_closed",
                projection.registry,
                {
                    "requested_binding_id": projection.binding.binding_id,
                    "reason": "binding_is_closed_historical_parent",
                },
                binding=projection.binding,
            )
            return self._with_guarded_reconciliation_evidence(closed, exc)
        failure = self._operation_failure(
            self._fact_failure_code(exc.code, operation="append"),
            operation,
            projection.aggregate,
            {
                "fresh_source_head_seq": projection.aggregate.source_head_seq,
                "fresh_next_expected_seq": projection.aggregate.source_head_seq + 1,
                "fresh_version": projection.aggregate.version,
                "target_state": target,
                "command": kind,
            },
        )
        return self._with_guarded_reconciliation_evidence(failure, exc)

    def _replay_result(
        self,
        operation: DirectedEffectOperationIdentityV1,
        aggregate: _Aggregate,
        committed: _CommittedTransition,
        requested_descriptor: Mapping[str, object],
    ) -> DirectedEffectOperationResultV1:
        requested = self._normalized_replay_descriptor(requested_descriptor)
        if requested != committed.normalized.replay:
            drift_fields = sorted(
                field
                for field, value in requested.to_record().items()
                if value != committed.normalized.replay.to_record()[field]
            )
            return self._operation_failure(
                "idempotency_semantic_conflict",
                operation,
                aggregate,
                {"drift_fields": drift_fields, "committed_event_id": committed.event_id},
            )
        evidence: dict[str, object] = {
            "event_id": committed.event_id,
            "appended_seq": committed.seq,
            "authoritative_append": False,
            "authoritative_effect_receipt": False,
            "append_disposition": "exact_replay",
            "committed_event_id": committed.event_id,
            "committed_seq": committed.seq,
            "source_head_seq": aggregate.source_head_seq,
        }
        evidence.update(self._transition_descriptor_evidence(committed))
        return DirectedEffectOperationResultV1(
            ok=True,
            code="idempotent_replay",
            operation=operation,
            state=aggregate.state,
            version=aggregate.version,
            snapshot=self._project_snapshot(aggregate),
            idempotent=True,
            evidence=evidence,
        )

    @staticmethod
    def _transition_descriptor_evidence(transition: _CommittedTransition) -> dict[str, object]:
        """Project only durable receipt/recovery/settlement binding fields."""

        evidence: dict[str, object] = {}
        for field in (
            "receipt_ref",
            "receipt_hash",
            "receipt_binding_hash",
            "receipt_outcome",
            "recovery_evidence_ref",
            "recovery_evidence_hash",
            "resolution_evidence_ref",
            "resolution_evidence_hash",
            "terminal_intent_hash",
            "settlement_outcome",
        ):
            value = transition.descriptor.get(field)
            if isinstance(value, str) and value:
                evidence[field] = value
        return evidence

    @staticmethod
    def _transition_for_kind(aggregate: _Aggregate, kind: _CommandKind) -> _CommittedTransition | None:
        for transition in aggregate.transitions:
            if transition.descriptor.get("command") == kind:
                return transition
        return None

    def _semantic_continuity_failure(
        self,
        operation: DirectedEffectOperationIdentityV1,
        aggregate: _Aggregate,
        descriptor: Mapping[str, object],
    ) -> DirectedEffectOperationResultV1 | None:
        if aggregate.state is None:
            return None
        requested = self._descriptor_semantic(descriptor)
        observed = (
            aggregate.intended_effect_fingerprint,
            aggregate.policy_verdict_hash,
            aggregate.expected_receipt_binding_hash,
        )
        if requested == observed:
            return None
        return self._operation_failure(
            "deo_semantic_drift",
            operation,
            aggregate,
            {"requested": requested, "observed": observed},
        )

    @staticmethod
    def _descriptor_semantic(descriptor: Mapping[str, object]) -> tuple[str, str, str]:
        return (
            str(descriptor.get("intended_effect_fingerprint") or ""),
            str(descriptor.get("policy_verdict_hash") or ""),
            str(descriptor.get("expected_receipt_binding_hash") or ""),
        )

    @staticmethod
    def _operation_descriptor(command: _Command, *, kind: _CommandKind) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "command": kind,
            "expected_version": command.expected_version,
            "expected_seq": command.expected_seq,
            "actor": command.actor,
            "reason": command.reason
            if isinstance(
                command,
                (
                    AbortDirectedEffectOperationCommandV1,
                    MarkDirectedEffectRecoveryPendingCommandV1,
                    DeadLetterDirectedEffectOperationCommandV1,
                ),
            )
            else "",
            "intended_effect_fingerprint": command.intended_effect_fingerprint,
            "policy_verdict_hash": command.policy_verdict_hash,
            "expected_receipt_binding_hash": command.expected_receipt_binding_hash,
        }
        if kind in {"commit_receipt", "mark_recovery_pending", "dead_letter", "close_by_parent"}:
            descriptor.update(
                {
                    "receipt_ref": command.receipt_ref
                    if isinstance(command, CommitDirectedEffectReceiptCommandV1)
                    else "",
                    "receipt_hash": command.receipt_hash
                    if isinstance(command, CommitDirectedEffectReceiptCommandV1)
                    else "",
                    "receipt_binding_hash": command.receipt_binding_hash
                    if isinstance(command, CommitDirectedEffectReceiptCommandV1)
                    else "",
                    "receipt_outcome": command.receipt_outcome
                    if isinstance(command, CommitDirectedEffectReceiptCommandV1)
                    else "",
                    "recovery_evidence_ref": command.recovery_evidence_ref
                    if isinstance(command, MarkDirectedEffectRecoveryPendingCommandV1)
                    else "",
                    "recovery_evidence_hash": command.recovery_evidence_hash
                    if isinstance(command, MarkDirectedEffectRecoveryPendingCommandV1)
                    else "",
                    "resolution_evidence_ref": command.resolution_evidence_ref
                    if isinstance(command, DeadLetterDirectedEffectOperationCommandV1)
                    else "",
                    "resolution_evidence_hash": command.resolution_evidence_hash
                    if isinstance(command, DeadLetterDirectedEffectOperationCommandV1)
                    else "",
                    "terminal_intent_hash": command.terminal_intent_hash
                    if isinstance(command, _CloseDirectedEffectByParentCommandV1)
                    else "",
                    "settlement_outcome": command.settlement_outcome
                    if isinstance(command, _CloseDirectedEffectByParentCommandV1)
                    else "",
                }
            )
        return descriptor

    @staticmethod
    def _operation_event_canonical(
        *,
        operation: DirectedEffectOperationIdentityV1,
        state: DirectedEffectOperationStateV1,
        previous_version: int,
        descriptor: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": DIRECTED_EFFECT_OPERATION_SCHEMA_V3
            if descriptor.get("command")
            in {"commit_receipt", "mark_recovery_pending", "dead_letter", "close_by_parent"}
            else DIRECTED_EFFECT_OPERATION_SCHEMA_V2,
            "operation": operation.to_record(),
            "parent_binding_id": operation.parent_binding_id,
            "state": state,
            "previous_version": previous_version,
            "version": previous_version + 1,
            "replay_descriptor": dict(descriptor),
        }

    @staticmethod
    def _normalized_replay_descriptor(
        descriptor: Mapping[str, object],
    ) -> _NormalizedDirectedEffectReplayDescriptorV1:
        """Strip schema, timestamp, and CAS volatility from replay semantics."""

        return _NormalizedDirectedEffectReplayDescriptorV1(
            command=cast(_CommandKind, descriptor["command"]),
            actor=cast(str, descriptor["actor"]),
            reason=cast(str, descriptor["reason"]),
            intended_effect_fingerprint=cast(str, descriptor["intended_effect_fingerprint"]),
            policy_verdict_hash=cast(str, descriptor["policy_verdict_hash"]),
            expected_receipt_binding_hash=cast(str, descriptor["expected_receipt_binding_hash"]),
            receipt_ref=cast(str, descriptor.get("receipt_ref") or ""),
            receipt_hash=cast(str, descriptor.get("receipt_hash") or ""),
            receipt_binding_hash=cast(str, descriptor.get("receipt_binding_hash") or ""),
            receipt_outcome=cast(str, descriptor.get("receipt_outcome") or ""),
            recovery_evidence_ref=cast(str, descriptor.get("recovery_evidence_ref") or ""),
            recovery_evidence_hash=cast(str, descriptor.get("recovery_evidence_hash") or ""),
            resolution_evidence_ref=cast(str, descriptor.get("resolution_evidence_ref") or ""),
            resolution_evidence_hash=cast(str, descriptor.get("resolution_evidence_hash") or ""),
            terminal_intent_hash=cast(str, descriptor.get("terminal_intent_hash") or ""),
            settlement_outcome=cast(str, descriptor.get("settlement_outcome") or ""),
        )

    def _normalized_transition(
        self,
        *,
        operation: DirectedEffectOperationIdentityV1,
        state: DirectedEffectOperationStateV1,
        descriptor: Mapping[str, object],
    ) -> _NormalizedDirectedEffectTransitionV1:
        """Build the schema-neutral idempotency and exact-replay identity."""

        return _NormalizedDirectedEffectTransitionV1(
            operation=operation,
            state=state,
            replay=self._normalized_replay_descriptor(descriptor),
        )

    @staticmethod
    def _derive_operation(
        command: _ReadCommand,
        binding: DirectedEffectParentBindingV1,
    ) -> DirectedEffectOperationIdentityV1:
        return DirectedEffectOperationIdentityV1(
            workspace=binding.workspace,
            task_id=binding.task_id,
            execution_attempt_id=binding.registry_identity.execution_attempt_id,
            parent_binding_id=binding.binding_id,
            parent_sequence=binding.parent_sequence,
            tool_call_id=command.tool_call_id,
            effect_id=command.effect_id,
            operation_id=_operation_id(
                binding_id=binding.binding_id,
                tool_call_id=command.tool_call_id,
                effect_id=command.effect_id,
            ),
            operation_stream_token=binding.operation_stream_token,
        )

    @staticmethod
    def _registry_projection(registry: _ParentRegistry) -> DirectedEffectParentRegistryProjectionV1:
        return DirectedEffectParentRegistryProjectionV1(
            schema_version=DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1,
            registry_identity=registry.identity,
            registry_stream_token=registry.stream_token,
            registry_version=registry.registry_version,
            source_head_seq=registry.source_head_seq,
            next_expected_seq=registry.next_expected_seq,
            next_parent_sequence=registry.next_parent_sequence,
            open_binding=registry.open_binding,
            admissions_by_idempotency_key={
                key: admission.binding for key, admission in registry.admissions_by_idempotency_key.items()
            },
            bindings_by_id=dict(registry.bindings_by_id),
        )

    @staticmethod
    def _command_identity_failure(
        *,
        workspace: str,
        task_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectOperationResultV1 | None:
        canonical_workspace = str(Path(workspace).expanduser().resolve())
        if workspace != execution_attempt.workspace or workspace != canonical_workspace:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="workspace_mismatch",
                evidence={
                    "workspace": workspace,
                    "canonical_workspace": canonical_workspace,
                    "attempt_workspace": execution_attempt.workspace,
                },
            )
        if task_id != execution_attempt.task_id:
            return DirectedEffectOperationResultV1(ok=False, code="task_mismatch")
        return None

    @staticmethod
    def _registry_identity_mismatch_code(
        expected: DirectedEffectParentRegistryIdentityV1,
        observed: DirectedEffectParentRegistryIdentityV1,
    ) -> DirectedEffectOperationCodeV1:
        if observed.workspace != expected.workspace:
            return "workspace_mismatch"
        if observed.task_id != expected.task_id:
            return "task_mismatch"
        return "execution_attempt_mismatch"

    @staticmethod
    def _binding_mismatch_result(
        supplied: DirectedEffectParentBindingV1,
        durable: DirectedEffectParentBindingV1,
        registry: _ParentRegistry,
    ) -> DirectedEffectOperationResultV1 | None:
        if supplied.schema_version != durable.schema_version:
            code: DirectedEffectOperationCodeV1 = "parent_binding_conflict"
        elif supplied.registry_identity != durable.registry_identity:
            code = "execution_attempt_mismatch"
        elif supplied.correlation.schema_version != durable.correlation.schema_version:
            code = "parent_binding_conflict"
        elif supplied.correlation.turn_id != durable.correlation.turn_id:
            code = "turn_mismatch"
        elif supplied.correlation.batch_id != durable.correlation.batch_id:
            code = "batch_mismatch"
        elif supplied.registry_stream_token != durable.registry_stream_token:
            code = "parent_binding_conflict"
        elif (
            supplied.registry_version != durable.registry_version or supplied.parent_sequence != durable.parent_sequence
        ):
            code = "parent_binding_version_conflict"
        elif supplied.binding_hash != durable.binding_hash:
            code = "parent_binding_hash_mismatch"
        elif supplied.source_event_seq != durable.source_event_seq:
            code = "parent_binding_version_conflict"
        elif supplied.source_event_id != durable.source_event_id:
            code = "parent_binding_event_conflict"
        elif (
            supplied.binding_id != durable.binding_id
            or supplied.operation_stream_token != durable.operation_stream_token
        ):
            code = "parent_binding_conflict"
        elif supplied.admission_idempotency_key != durable.admission_idempotency_key:
            code = "parent_admission_idempotency_conflict"
        elif supplied.actor != durable.actor:
            code = "parent_binding_conflict"
        else:
            return None
        return DirectedEffectOperationResultV1(
            ok=False,
            code=code,
            parent_binding=supplied,
            parent_registry=DirectedEffectOperationRepository._registry_projection(registry),
            evidence={"durable_binding": durable.to_record()},
        )

    @staticmethod
    def _legal_transition(
        previous: DirectedEffectOperationStateV1 | None,
        current: DirectedEffectOperationStateV1,
    ) -> bool:
        return (previous, current) in {
            (None, "INTENT_COMMITTED"),
            ("INTENT_COMMITTED", "EFFECT_STARTED"),
            ("INTENT_COMMITTED", "ABORTED"),
            ("EFFECT_STARTED", "RECOVERY_PENDING"),
            ("EFFECT_STARTED", "RECEIPT_COMMITTED"),
            ("RECOVERY_PENDING", "RECEIPT_COMMITTED"),
            ("RECOVERY_PENDING", "DEAD_LETTER"),
            ("RECEIPT_COMMITTED", "CLOSED_BY_PARENT"),
        }

    @staticmethod
    def _operation_identity_mismatch_code(
        expected: DirectedEffectOperationIdentityV1,
        observed: DirectedEffectOperationIdentityV1,
    ) -> DirectedEffectOperationCodeV1:
        if observed.workspace != expected.workspace:
            return "workspace_mismatch"
        if observed.task_id != expected.task_id:
            return "task_mismatch"
        if observed.execution_attempt_id != expected.execution_attempt_id:
            return "execution_attempt_mismatch"
        if observed.parent_binding_id != expected.parent_binding_id:
            return "parent_binding_conflict"
        return "operation_identity_conflict"

    @staticmethod
    def _fact_failure_code(code: str, *, operation: _FactOperation) -> DirectedEffectOperationCodeV1:
        mapping = _READ_FACT_FAILURE_CODES if operation == "read" else _APPEND_FACT_FAILURE_CODES
        return mapping.get(code, "fact_stream_unknown_failure")

    def _fact_failure_result(
        self,
        exc: FactStreamError,
        *,
        operation: _FactOperation,
        stream_kind: _StreamKind,
    ) -> DirectedEffectOperationResultV1:
        return DirectedEffectOperationResultV1(
            ok=False,
            code=self._fact_failure_code(exc.code, operation=operation),
            evidence={
                "fact_stream_operation": operation,
                "stream_kind": stream_kind,
                "fact_stream_code": exc.code,
                "fact_stream_details": dict(exc.details),
            },
        )

    @staticmethod
    def _maintenance_receipt_record(receipt: Any) -> dict[str, object]:
        """Detach FactStream maintenance evidence without elevating its authority."""

        return {
            "workspace": receipt.workspace,
            "storage_identity_token": receipt.storage_identity_token,
            "maintenance_reason": receipt.maintenance_reason,
            "operation": receipt.operation,
            "streams": tuple(receipt.streams),
            "proofs": tuple(
                {
                    "operation": proof.operation,
                    "verdict": proof.verdict,
                    "storage_identity_token": proof.storage_identity_token,
                    "runtime_root": proof.runtime_root,
                    "format_revision": proof.format_revision,
                    "final_validation": proof.final_validation,
                    "lock_keys": tuple(
                        {
                            "logical_path": key.logical_path,
                            "lock_key": key.lock_key,
                            "verdict": key.verdict,
                        }
                        for key in proof.lock_keys
                    ),
                }
                for proof in receipt.proofs
            ),
        }

    @staticmethod
    def _enrollment_failure(
        *,
        command: EnrollDirectedEffectParentRegistryStreamCommandV1 | EnrollDirectedEffectOperationStreamCommandV1,
        failure: DirectedEffectOperationResultV1,
    ) -> DirectedEffectStreamEnrollmentResultV1:
        return DirectedEffectStreamEnrollmentResultV1(
            ok=False,
            code=failure.code,
            execution_attempt=command.execution_attempt,
            parent_binding=(
                command.parent_binding if isinstance(command, EnrollDirectedEffectOperationStreamCommandV1) else None
            ),
            evidence=dict(failure.evidence),
        )

    def _enrollment_fact_failure(
        self,
        *,
        command: EnrollDirectedEffectParentRegistryStreamCommandV1 | EnrollDirectedEffectOperationStreamCommandV1,
        exc: FactStreamError,
    ) -> DirectedEffectStreamEnrollmentResultV1:
        code = self._fact_failure_code(exc.code, operation="append")
        return DirectedEffectStreamEnrollmentResultV1(
            ok=False,
            code=code,
            execution_attempt=command.execution_attempt,
            parent_binding=(
                command.parent_binding if isinstance(command, EnrollDirectedEffectOperationStreamCommandV1) else None
            ),
            evidence={
                "fact_stream_operation": "enroll",
                "fact_stream_code": exc.code,
                "fact_stream_details": dict(exc.details),
            },
        )

    @staticmethod
    def _with_reconciliation_evidence(
        result: DirectedEffectOperationResultV1,
        exc: FactStreamError,
    ) -> DirectedEffectOperationResultV1:
        evidence = dict(result.evidence)
        evidence.update(
            {
                "reconciled_after_cas": True,
                "fact_stream_code": exc.code,
                "fact_stream_details": dict(exc.details),
            }
        )
        return DirectedEffectOperationResultV1(
            ok=result.ok,
            code=result.code,
            operation=result.operation,
            parent_binding=result.parent_binding,
            parent_registry=result.parent_registry,
            state=result.state,
            version=result.version,
            snapshot=result.snapshot,
            idempotent=result.idempotent,
            evidence=evidence,
        )

    @staticmethod
    def _with_guarded_reconciliation_evidence(
        result: DirectedEffectOperationResultV1,
        exc: FactStreamError,
    ) -> DirectedEffectOperationResultV1:
        """Attach the original non-drift error to one strict reconciliation verdict."""

        evidence = dict(result.evidence)
        evidence.update(
            {
                "reconciled_after_guarded_error": True,
                "fact_stream_code": exc.code,
                "fact_stream_details": dict(exc.details),
            }
        )
        return DirectedEffectOperationResultV1(
            ok=result.ok,
            code=result.code,
            operation=result.operation,
            parent_binding=result.parent_binding,
            parent_registry=result.parent_registry,
            state=result.state,
            version=result.version,
            snapshot=result.snapshot,
            idempotent=result.idempotent,
            evidence=evidence,
        )

    def _parent_open_conflict(
        self,
        registry: _ParentRegistry,
        requested_idempotency_key: str,
    ) -> DirectedEffectOperationResultV1:
        return self._parent_registry_conflict(
            "parent_open_conflict",
            registry,
            {
                "requested_admission_idempotency_key": requested_idempotency_key,
                "open_binding": registry.open_binding.to_record() if registry.open_binding is not None else None,
            },
            binding=registry.open_binding,
        )

    def _parent_registry_conflict(
        self,
        code: DirectedEffectOperationCodeV1,
        registry: _ParentRegistry,
        evidence: Mapping[str, object],
        *,
        binding: DirectedEffectParentBindingV1 | None = None,
    ) -> DirectedEffectOperationResultV1:
        fresh_evidence = {
            "fresh_registry_version": registry.registry_version,
            "fresh_source_head_seq": registry.source_head_seq,
            "fresh_next_expected_seq": registry.next_expected_seq,
            "fresh_next_parent_sequence": registry.next_parent_sequence,
            **dict(evidence),
        }
        return DirectedEffectOperationResultV1(
            ok=False,
            code=code,
            parent_binding=binding,
            parent_registry=self._registry_projection(registry),
            evidence=fresh_evidence,
        )

    def _parent_registry_failure(
        self,
        code: DirectedEffectOperationCodeV1,
        registry: _ParentRegistry,
        evidence: Mapping[str, object] | None = None,
    ) -> DirectedEffectOperationResultV1:
        return DirectedEffectOperationResultV1(
            ok=False,
            code=code,
            parent_binding=registry.open_binding,
            evidence=dict(evidence or {}),
        )

    def _parent_failure(
        self,
        code: DirectedEffectOperationCodeV1,
        *,
        binding: DirectedEffectParentBindingV1 | None = None,
        registry: _ParentRegistry | None = None,
        evidence: Mapping[str, object] | None = None,
    ) -> DirectedEffectOperationResultV1:
        return DirectedEffectOperationResultV1(
            ok=False,
            code=code,
            parent_binding=binding,
            parent_registry=self._registry_projection(registry) if registry is not None else None,
            evidence=dict(evidence or {}),
        )

    @staticmethod
    def _operation_failure(
        code: DirectedEffectOperationCodeV1,
        operation: DirectedEffectOperationIdentityV1,
        aggregate: _Aggregate | None,
        evidence: Mapping[str, object],
    ) -> DirectedEffectOperationResultV1:
        return DirectedEffectOperationResultV1(
            ok=False,
            code=code,
            operation=operation,
            state=aggregate.state if aggregate is not None else None,
            version=aggregate.version if aggregate is not None else 0,
            evidence=dict(evidence),
        )

    @staticmethod
    def _registry_query_failure(
        failure: DirectedEffectOperationResultV1,
    ) -> DirectedEffectParentRegistryResultV1:
        return DirectedEffectParentRegistryResultV1(
            ok=False,
            code=failure.code,
            evidence=failure.evidence,
        )

    def _project_snapshot(self, aggregate: _Aggregate) -> DirectedEffectOperationSnapshotV1 | None:
        """Project one strict-rebuilt aggregate in memory without persistence."""

        if aggregate.state is None:
            return None
        return DirectedEffectOperationSnapshotV1(
            schema_version=DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1,
            source_head_seq=aggregate.source_head_seq,
            last_event_id=aggregate.last_event_id,
            operation=aggregate.operation,
            state=aggregate.state,
            version=aggregate.version,
        )
