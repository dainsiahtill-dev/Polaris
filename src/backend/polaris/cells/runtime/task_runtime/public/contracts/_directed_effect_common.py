"""Directed-effect SCHEMA constants, Literals, and shared token helpers.

Shared vocabulary for inventory, parent registry, and operation contracts.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

from polaris.cells.runtime.task_runtime.public.contracts._helpers import (
    _require_non_empty,
)

DIRECTED_EFFECT_OPERATION_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-operation/1"
DIRECTED_EFFECT_OPERATION_SCHEMA_V2: Final[str] = "task-runtime.directed-effect-operation/2"
DIRECTED_EFFECT_OPERATION_SCHEMA_V3: Final[str] = "task-runtime.directed-effect-operation/3"
DIRECTED_EFFECT_OPERATION_SCHEMA_V4: Final[str] = "task-runtime.directed-effect-operation/4"
DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-operation-snapshot/1"
DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-claim-grant/1"
DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-inventory-intent/1"
DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-inventory-member/1"
DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-inventory-projection/1"
DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-parent-binding/1"
DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-parent-correlation/1"
DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1: Final[str] = (
    "task-runtime.directed-effect-parent-registry-identity/1"
)
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-parent-registry/1"
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2: Final[str] = "task-runtime.directed-effect-parent-registry/2"
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3: Final[str] = "task-runtime.directed-effect-parent-registry/3"
DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1: Final[str] = (
    "task-runtime.directed-effect-parent-registry-projection/1"
)
DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1: Final[str] = (
    "task-runtime.directed-effect-parent-readiness-projection/1"
)

DirectedEffectInventoryEffectTypeV1 = Literal["write", "async"]
DirectedEffectInventoryExecutionModeV1 = Literal["write_serial", "async_receipt"]
DirectedEffectInventoryContingencyKindV1 = Literal["forward", "rollback"]

_DIRECTED_EFFECT_INVENTORY_EFFECT_MODE_PAIRS: Final[
    frozenset[tuple[DirectedEffectInventoryEffectTypeV1, DirectedEffectInventoryExecutionModeV1]]
] = frozenset(
    {
        ("write", "write_serial"),
        ("async", "async_receipt"),
    }
)
_LOWERCASE_HEX_DIGITS: Final[frozenset[str]] = frozenset("0123456789abcdef")

DirectedEffectOperationStateV1 = Literal[
    "INTENT_COMMITTED",
    "EFFECT_STARTED",
    "RECOVERY_PENDING",
    "RECEIPT_COMMITTED",
    "CLOSED_BY_PARENT",
    "ABORTED",
    "DEAD_LETTER",
]

DirectedEffectReceiptOutcomeV1 = Literal["succeeded", "failed"]

_DIRECTED_EFFECT_OPERATION_STATES: tuple[DirectedEffectOperationStateV1, ...] = (
    "INTENT_COMMITTED",
    "EFFECT_STARTED",
    "RECOVERY_PENDING",
    "RECEIPT_COMMITTED",
    "CLOSED_BY_PARENT",
    "ABORTED",
    "DEAD_LETTER",
)

DirectedEffectAuthorityFailureCodeV1 = Literal[
    "operation_not_found",
    "parent_binding_not_found",
    "parent_binding_conflict",
    "parent_binding_version_conflict",
    "parent_binding_event_conflict",
    "parent_binding_hash_mismatch",
    "parent_admission_idempotency_conflict",
    "parent_open_conflict",
    "parent_closed",
    "parent_registry_version_conflict",
    "parent_registry_expected_seq_conflict",
    "workspace_mismatch",
    "task_mismatch",
    "execution_attempt_mismatch",
    "turn_mismatch",
    "batch_mismatch",
    "operation_identity_conflict",
    "operation_version_conflict",
    "stream_expected_seq_conflict",
    "illegal_transition",
    "idempotency_conflict",
    "deo_semantic_drift",
    "task_not_found",
    "session_not_found",
    "session_corrupt",
    "session_task_mismatch",
    "session_mismatch",
    "attempt_mismatch",
    "role_mismatch",
    "worker_mismatch",
    "run_mismatch",
    "external_task_id_mismatch",
    "lease_version_mismatch",
    "session_not_active",
    "session_lease_expired",
    "file_lock_timeout",
    "execution_attempt_validation_unknown",
    "strict_stream_corruption",
    "strict_stream_torn_tail",
    "strict_stream_unknown_schema",
    "strict_stream_overload",
    "stream_lock_timeout",
    "stream_append_failed",
    "stream_cas_exhausted",
    "fact_stream_unknown_failure",
    "stream_lock_missing",
    "guarded_reprepare_exhausted",
    "guarded_receipt_mismatch",
    "idempotency_semantic_conflict",
    "inventory_not_sealed",
    "inventory_seal_conflict",
    "inventory_requires_empty_operation_stream",
    "inventory_member_not_found",
    "inventory_member_conflict",
    "inventory_admission_incomplete",
    "inventory_admission_unexpected",
    "inventory_not_ready",
    "receipt_binding_conflict",
    "receipt_evidence_conflict",
    "recovery_evidence_conflict",
    "recovery_deadline_exceeded",
    "dead_letter_evidence_conflict",
]

DirectedEffectOperationCodeV1 = (
    Literal[
        "parent_admitted",
        "parent_idempotent_replay",
        "parent_registry_found",
        "admitted",
        "effect_claimed",
        "receipt_committed",
        "recovery_pending",
        "dead_lettered",
        "closed_by_parent",
        "aborted",
        "found",
        "idempotent_replay",
    ]
    | DirectedEffectAuthorityFailureCodeV1
)

DirectedEffectInventoryCodeV1 = (
    Literal[
        "inventory_sealed",
        "inventory_seal_idempotent_replay",
        "inventory_ready",
        "inventory_ready_idempotent_replay",
        "inventory_observed",
    ]
    | DirectedEffectAuthorityFailureCodeV1
)

_DIRECTED_EFFECT_AUTHORITY_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    get_args(DirectedEffectAuthorityFailureCodeV1)
)

DirectedEffectParentReadinessCodeV1 = DirectedEffectOperationCodeV1 | Literal["readiness_observed"]


def _directed_effect_token(name: str, value: str) -> str:
    return _require_non_empty(name, value)


def _directed_effect_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be an int >= 1")
    return value


def _directed_effect_non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an int >= 0")
    return value


def _directed_effect_inventory_token(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _directed_effect_inventory_digest(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if len(value) != 64 or any(character not in _LOWERCASE_HEX_DIGITS for character in value):
        raise ValueError(f"{name} must be exactly 64 lowercase hexadecimal characters")
    return value
