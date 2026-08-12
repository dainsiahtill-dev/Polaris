"""Shared constants, private types, and pure helpers for directed-effect operations."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypeAlias

from polaris.cells.events.fact_stream.public import (
    GuardedFactSnapshotV1,
)

from ...public.contracts import (
    DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DeadLetterDirectedEffectOperationCommandV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectOperationCodeV1,
    DirectedEffectOperationIdentityV1,
    DirectedEffectOperationResultV1,
    DirectedEffectOperationStateV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    GetDirectedEffectOperationQueryV1,
    GetDirectedEffectParentReadinessQueryV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    SealDirectedEffectInventoryCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    TaskRuntimeExecutionAttemptValidationCodeV1,
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


@dataclass(frozen=True, slots=True)
class _CloseDirectedEffectByParentForBatchCommandV1:
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
    batch_rollover_hash: str
    actor: str = "runtime.task_runtime.batch_rollover"


_Command: TypeAlias = (
    AdmitDirectedEffectOperationCommandV1
    | ClaimDirectedEffectCommandV1
    | AbortDirectedEffectOperationCommandV1
    | CommitDirectedEffectReceiptCommandV1
    | MarkDirectedEffectRecoveryPendingCommandV1
    | DeadLetterDirectedEffectOperationCommandV1
    | _CloseDirectedEffectByParentCommandV1
    | _CloseDirectedEffectByParentForBatchCommandV1
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
    # KernelOne LockedRegularFileSetV1 raises lock_acquisition_timeout when
    # flock exceeds the monotonic deadline (default 2s / raised 15s for
    # JsonlEventStore). Without this map, DEO surfaces opaque
    # fact_stream_unknown_failure and multi-task batches soft-drop every
    # write (r148 residual: deo_member_admission_failed:fact_stream_unknown_failure).
    "lock_acquisition_timeout": "stream_lock_timeout",
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
    batch_rollover_close_proof: Mapping[str, object] | None = None


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
    batch_rollover_hash: str

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
            "batch_rollover_hash": self.batch_rollover_hash,
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


__all__ = [
    "_APPEND_FACT_FAILURE_CODES",
    "_DIRECTED_EFFECT_ADMISSION_SET_HASH_SCHEMA_V1",
    "_DIRECTED_EFFECT_INVENTORY_HASH_SCHEMA_V1",
    "_EXECUTION_ATTEMPT_FAILURE_CODES",
    "_GUARDED_REPREPARE_DRIFT_CODES",
    "_MAX_GUARDED_ATTEMPTS",
    "_MAX_OPERATION_EVENTS",
    "_MAX_REGISTRY_EVENTS",
    "_OPERATION_EVENT_PREFIX",
    "_PARENT_ADMITTED_EVENT_TYPE",
    "_PARENT_CLOSED_EVENT_TYPE",
    "_PARENT_INVENTORY_READY_EVENT_TYPE",
    "_PARENT_INVENTORY_SEALED_EVENT_TYPE",
    "_READ_FACT_FAILURE_CODES",
    "_TERMINAL_STATES",
    "DirectedEffectSettlementPreBarrierVerdictV1",
    "_Aggregate",
    "_AuthorityCommand",
    "_CloseDirectedEffectByParentCommandV1",
    "_CloseDirectedEffectByParentForBatchCommandV1",
    "_Command",
    "_CommandKind",
    "_CommittedTransition",
    "_DirectedEffectRecoveryCursor",
    "_DirectedEffectRecoveryRepositorySweep",
    "_DirectedEffectRecoverySweepPreparation",
    "_FactOperation",
    "_InventoryGuardedCommand",
    "_InventoryOperationProjection",
    "_NormalizedDirectedEffectReplayDescriptorV1",
    "_NormalizedDirectedEffectTransitionV1",
    "_OperationStreamReduction",
    "_ParentBindingReadCommand",
    "_ParentRegistry",
    "_ParentRegistryBoundCommand",
    "_ParentSettlementGuardedClose",
    "_ParentSettlementPreparation",
    "_ReadCommand",
    "_ReadyDirectedEffectInventory",
    "_ReadyGatedCommand",
    "_ReadyOperationContext",
    "_RegistryAdmission",
    "_SealedDirectedEffectInventory",
    "_SettlementPreBarrierCode",
    "_StreamKind",
    "_StreamRead",
    "_StrictInventoryConfirmation",
    "_StrictOperationProjection",
    "_admission_set_hash",
    "_binding_id",
    "_canonical_json",
    "_hash_token",
    "_inventory_effect_id",
    "_inventory_hash",
    "_inventory_member",
    "_is_canonical_sha256",
    "_is_timezone_aware_timestamp",
    "_new_append_attempt_nonce",
    "_operation_event_type",
    "_operation_id",
    "_operation_stream_token",
    "_registry_fact_idempotency_key",
    "_registry_stream_token",
]
