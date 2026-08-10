"""Directed-effect parent registry, operation commands, readiness, recovery.

Parent admit/batch/enroll/projection, operation Admit/Claim/Abort/Commit/
Mark/DeadLetter, readiness projections, and reconcile/recovery sweep.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from polaris.cells.runtime.task_runtime.public.contracts._directed_effect_common import (
    _DIRECTED_EFFECT_OPERATION_STATES,
    DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1,
    DirectedEffectOperationCodeV1,
    DirectedEffectOperationStateV1,
    DirectedEffectParentReadinessCodeV1,
    DirectedEffectReceiptOutcomeV1,
    _directed_effect_inventory_digest,
    _directed_effect_inventory_token,
    _directed_effect_non_negative_int,
    _directed_effect_positive_int,
    _directed_effect_token,
)
from polaris.cells.runtime.task_runtime.public.contracts._directed_effect_inventory import (
    DirectedEffectInventoryMemberV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    ParentCorrelationV1,
)
from polaris.cells.runtime.task_runtime.public.contracts._execution_attempts import (
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.contracts._helpers import (
    _SUCCESS_READINESS_EVIDENCE_KEYS,
    _to_detached_dict,
    _to_immutable_evidence,
)


@dataclass(frozen=True, slots=True)
class DirectedEffectOperationIdentityV1:
    """Canonical TaskRuntime identity for one child directed effect."""

    workspace: str
    task_id: int
    execution_attempt_id: str
    parent_binding_id: str
    parent_sequence: int
    tool_call_id: str
    effect_id: str
    operation_id: str
    operation_stream_token: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        _directed_effect_positive_int("parent_sequence", self.parent_sequence)
        for field_name in (
            "execution_attempt_id",
            "parent_binding_id",
            "tool_call_id",
            "effect_id",
            "operation_id",
            "operation_stream_token",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))

    def to_record(self) -> dict[str, object]:
        return {
            "workspace": self.workspace,
            "task_id": self.task_id,
            "execution_attempt_id": self.execution_attempt_id,
            "parent_binding_id": self.parent_binding_id,
            "parent_sequence": self.parent_sequence,
            "tool_call_id": self.tool_call_id,
            "effect_id": self.effect_id,
            "operation_id": self.operation_id,
            "operation_stream_token": self.operation_stream_token,
        }


@dataclass(frozen=True, slots=True)
class DirectedEffectClaimGrantV1:
    """One hash-bound claim capability returned only by the original claim."""

    schema_version: str
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    operation: DirectedEffectOperationIdentityV1
    member: DirectedEffectInventoryMemberV1
    inventory_hash: str
    operation_version: int
    claim_event_id: str
    claim_event_seq: int
    operation_source_head_seq: int
    parent_registry_source_head_seq: int
    grant_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string")
        if self.schema_version != DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1:
            raise ValueError("unsupported directed effect claim grant schema")
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
        if type(self.parent_binding) is not DirectedEffectParentBindingV1:
            raise TypeError("parent_binding must be exactly DirectedEffectParentBindingV1")
        if type(self.operation) is not DirectedEffectOperationIdentityV1:
            raise TypeError("operation must be exactly DirectedEffectOperationIdentityV1")
        if type(self.member) is not DirectedEffectInventoryMemberV1:
            raise TypeError("member must be exactly DirectedEffectInventoryMemberV1")

        expected_registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
            self.execution_attempt
        )
        if self.parent_binding.registry_identity != expected_registry_identity:
            raise ValueError("parent_binding registry identity must match execution_attempt")
        if self.operation.workspace != self.execution_attempt.workspace:
            raise ValueError("operation workspace must match execution_attempt")
        if self.operation.task_id != self.execution_attempt.task_id:
            raise ValueError("operation task_id must match execution_attempt")
        if self.operation.execution_attempt_id != expected_registry_identity.execution_attempt_id:
            raise ValueError("operation execution_attempt_id must match execution_attempt")
        if self.operation.parent_binding_id != self.parent_binding.binding_id:
            raise ValueError("operation parent_binding_id must match parent_binding")
        if self.operation.parent_sequence != self.parent_binding.parent_sequence:
            raise ValueError("operation parent_sequence must match parent_binding")
        if self.operation.operation_stream_token != self.parent_binding.operation_stream_token:
            raise ValueError("operation stream must match parent_binding")
        if self.operation.tool_call_id != self.member.tool_call_id:
            raise ValueError("operation tool_call_id must match inventory member")
        if self.operation.effect_id != self.member.effect_id:
            raise ValueError("operation effect_id must match inventory member")
        if self.operation.operation_id != self.member.operation_id:
            raise ValueError("operation operation_id must match inventory member")

        object.__setattr__(
            self,
            "inventory_hash",
            _directed_effect_inventory_digest("inventory_hash", self.inventory_hash),
        )
        if (
            isinstance(self.operation_version, bool)
            or not isinstance(self.operation_version, int)
            or self.operation_version < 2
        ):
            raise ValueError("operation_version must be an int >= 2")
        object.__setattr__(
            self,
            "claim_event_id",
            _directed_effect_inventory_token("claim_event_id", self.claim_event_id),
        )
        _directed_effect_positive_int("claim_event_seq", self.claim_event_seq)
        _directed_effect_positive_int("operation_source_head_seq", self.operation_source_head_seq)
        if self.claim_event_seq != self.operation_source_head_seq:
            raise ValueError("claim_event_seq must equal operation_source_head_seq")
        if self.operation_source_head_seq < self.operation_version:
            raise ValueError("operation_source_head_seq must be >= operation_version")
        _directed_effect_positive_int(
            "parent_registry_source_head_seq",
            self.parent_registry_source_head_seq,
        )
        minimum_parent_registry_head_seq = self.parent_binding.source_event_seq + 2
        if self.parent_registry_source_head_seq < minimum_parent_registry_head_seq:
            raise ValueError("parent registry head must cover parent binding, inventory seal, and inventory ready")
        object.__setattr__(
            self,
            "grant_hash",
            _directed_effect_inventory_digest("grant_hash", self.grant_hash),
        )
        if self.grant_hash != self._canonical_grant_hash():
            raise ValueError("grant_hash must match the canonical unsigned grant record")

    def _unsigned_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "execution_attempt": self.execution_attempt.to_record(),
            "parent_binding": self.parent_binding.to_record(),
            "operation": self.operation.to_record(),
            "member": self.member.to_record(),
            "inventory_hash": self.inventory_hash,
            "operation_version": self.operation_version,
            "claim_event_id": self.claim_event_id,
            "claim_event_seq": self.claim_event_seq,
            "operation_source_head_seq": self.operation_source_head_seq,
            "parent_registry_source_head_seq": self.parent_registry_source_head_seq,
        }

    def _canonical_grant_hash(self) -> str:
        encoded = json.dumps(
            self._unsigned_record(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_record(self) -> dict[str, object]:
        """Return the canonical signed grant record."""

        return {**self._unsigned_record(), "grant_hash": self.grant_hash}


@dataclass(frozen=True, slots=True)
class DirectedEffectOperationSnapshotV1:
    """Rebuildable, non-authoritative cached projection of a DEO aggregate."""

    schema_version: str
    source_head_seq: int
    last_event_id: str
    operation: DirectedEffectOperationIdentityV1
    state: DirectedEffectOperationStateV1
    version: int

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1:
            raise ValueError("unsupported directed effect operation snapshot schema")
        _directed_effect_non_negative_int("source_head_seq", self.source_head_seq)
        object.__setattr__(self, "last_event_id", _directed_effect_token("last_event_id", self.last_event_id))
        if not isinstance(self.operation, DirectedEffectOperationIdentityV1):
            raise TypeError("operation must be DirectedEffectOperationIdentityV1")
        if self.state not in {
            "INTENT_COMMITTED",
            "EFFECT_STARTED",
            "RECOVERY_PENDING",
            "RECEIPT_COMMITTED",
            "CLOSED_BY_PARENT",
            "ABORTED",
            "DEAD_LETTER",
        }:
            raise ValueError("unsupported directed effect operation state")
        _directed_effect_positive_int("version", self.version)

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_head_seq": self.source_head_seq,
            "last_event_id": self.last_event_id,
            "operation": self.operation.to_record(),
            "state": self.state,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class DirectedEffectParentRegistryProjectionV1:
    """Read-only strict reconstruction of one attempt-scoped parent registry."""

    schema_version: str
    registry_identity: DirectedEffectParentRegistryIdentityV1
    registry_stream_token: str
    registry_version: int
    source_head_seq: int
    next_expected_seq: int
    next_parent_sequence: int
    open_binding: DirectedEffectParentBindingV1 | None
    admissions_by_idempotency_key: Mapping[str, DirectedEffectParentBindingV1] = field(default_factory=dict)
    bindings_by_id: Mapping[str, DirectedEffectParentBindingV1] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent registry projection schema")
        if not isinstance(self.registry_identity, DirectedEffectParentRegistryIdentityV1):
            raise TypeError("registry_identity must be DirectedEffectParentRegistryIdentityV1")
        object.__setattr__(
            self,
            "registry_stream_token",
            _directed_effect_token("registry_stream_token", self.registry_stream_token),
        )
        _directed_effect_non_negative_int("registry_version", self.registry_version)
        _directed_effect_non_negative_int("source_head_seq", self.source_head_seq)
        _directed_effect_positive_int("next_expected_seq", self.next_expected_seq)
        _directed_effect_positive_int("next_parent_sequence", self.next_parent_sequence)
        if self.registry_version != self.source_head_seq:
            raise ValueError("registry_version must equal source_head_seq")
        if self.next_expected_seq != self.source_head_seq + 1:
            raise ValueError("next_expected_seq must equal source_head_seq + 1")
        admissions = dict(self.admissions_by_idempotency_key)
        bindings = dict(self.bindings_by_id)
        if any(not isinstance(value, DirectedEffectParentBindingV1) for value in admissions.values()):
            raise TypeError("admissions_by_idempotency_key values must be parent bindings")
        if any(not isinstance(value, DirectedEffectParentBindingV1) for value in bindings.values()):
            raise TypeError("bindings_by_id values must be parent bindings")
        if self.open_binding is not None and not isinstance(self.open_binding, DirectedEffectParentBindingV1):
            raise TypeError("open_binding must be DirectedEffectParentBindingV1 or None")
        object.__setattr__(self, "admissions_by_idempotency_key", admissions)
        object.__setattr__(self, "bindings_by_id", bindings)

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "registry_identity": self.registry_identity.to_record(),
            "registry_stream_token": self.registry_stream_token,
            "registry_version": self.registry_version,
            "source_head_seq": self.source_head_seq,
            "next_expected_seq": self.next_expected_seq,
            "next_parent_sequence": self.next_parent_sequence,
            "open_binding": self.open_binding.to_record() if self.open_binding is not None else None,
            "admissions_by_idempotency_key": {
                key: value.to_record() for key, value in self.admissions_by_idempotency_key.items()
            },
            "bindings_by_id": {key: value.to_record() for key, value in self.bindings_by_id.items()},
        }


@dataclass(frozen=True, slots=True)
class AdmitDirectedEffectParentCommandV1:
    """CAS admission request for one attempt-scoped parent registry."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    correlation: ParentCorrelationV1
    admission_idempotency_key: str
    expected_version: int
    expected_seq: int
    actor: str = "task_runtime"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.correlation, ParentCorrelationV1):
            raise TypeError("correlation must be ParentCorrelationV1")
        object.__setattr__(
            self,
            "admission_idempotency_key",
            _directed_effect_token("admission_idempotency_key", self.admission_idempotency_key),
        )
        object.__setattr__(self, "actor", _directed_effect_token("actor", self.actor))
        _directed_effect_non_negative_int("expected_version", self.expected_version)
        _directed_effect_positive_int("expected_seq", self.expected_seq)


@dataclass(frozen=True, slots=True)
class AdmitDirectedEffectParentBatchCommandV1:
    """Admit one canonical batch, rolling over only a receipt-complete predecessor.

    TaskRuntime owns the predecessor close and derives every registry CAS value
    while holding the active execution-attempt locks. Callers provide identity
    and correlation only; they cannot manufacture a parent sequence or head.
    """

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    correlation: ParentCorrelationV1
    admission_idempotency_key: str
    actor: str = "task_runtime"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.correlation, ParentCorrelationV1):
            raise TypeError("correlation must be ParentCorrelationV1")
        object.__setattr__(
            self,
            "admission_idempotency_key",
            _directed_effect_token("admission_idempotency_key", self.admission_idempotency_key),
        )
        object.__setattr__(self, "actor", _directed_effect_token("actor", self.actor))


@dataclass(frozen=True, slots=True)
class GetDirectedEffectParentRegistryQueryV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")


@dataclass(frozen=True, slots=True)
class EnrollDirectedEffectParentRegistryStreamCommandV1:
    """Explicitly enroll the parent-registry stream for one validated attempt.

    This is a maintenance command. Its receipt is observational FactStream
    evidence and never grants business-operation authority.
    """

    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1

    def __post_init__(self) -> None:
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")


@dataclass(frozen=True, slots=True)
class EnrollDirectedEffectOperationStreamCommandV1:
    """Explicitly enroll one durable parent binding's operation stream.

    The command is intentionally complete: the attempt and parent binding are
    both revalidated against strict registry facts before FactStream enrollment.
    """

    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1

    def __post_init__(self) -> None:
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")


DirectedEffectStreamEnrollmentCodeV1 = (
    DirectedEffectOperationCodeV1
    | Literal[
        "parent_registry_stream_enrolled",
        "operation_stream_enrolled",
    ]
)


@dataclass(frozen=True, slots=True)
class DirectedEffectStreamEnrollmentResultV1:
    """Typed result for explicit DEO dynamic-stream maintenance enrollment."""

    ok: bool
    code: DirectedEffectStreamEnrollmentCodeV1
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1 | None = None
    receipt: Mapping[str, Any] | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        success_codes = {"parent_registry_stream_enrolled", "operation_stream_enrolled"}
        if self.ok != (self.code in success_codes):
            raise ValueError("ok must match directed effect stream enrollment result code")
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.parent_binding is not None and not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1 or None")
        if self.code == "parent_registry_stream_enrolled" and self.parent_binding is not None:
            raise ValueError("parent registry enrollment result must not carry a parent binding")
        if self.code == "operation_stream_enrolled" and self.parent_binding is None:
            raise ValueError("operation stream enrollment result requires a parent binding")
        if self.code in success_codes and self.receipt is None:
            raise ValueError("successful stream enrollment result requires an observational receipt")
        if self.receipt is not None:
            object.__setattr__(self, "receipt", _to_detached_dict(self.receipt))
        object.__setattr__(self, "evidence", _to_detached_dict(self.evidence))


@dataclass(frozen=True, slots=True)
class _DirectedEffectOperationCommandBaseV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    tool_call_id: str
    effect_id: str
    expected_version: int
    expected_seq: int
    actor: str = "task_runtime"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")
        for field_name in ("tool_call_id", "effect_id", "actor"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))
        _directed_effect_non_negative_int("expected_version", self.expected_version)
        _directed_effect_positive_int("expected_seq", self.expected_seq)


@dataclass(frozen=True, slots=True)
class AdmitDirectedEffectOperationCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Admit the one legal ``ABSENT -> INTENT_COMMITTED`` transition."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class ClaimDirectedEffectCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Claim the one legal ``INTENT_COMMITTED -> EFFECT_STARTED`` transition."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class AbortDirectedEffectOperationCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Abort the one legal ``INTENT_COMMITTED -> ABORTED`` transition."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "reason",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class CommitDirectedEffectReceiptCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Commit one durable physical-effect receipt to an ``EFFECT_STARTED`` operation."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""
    receipt_ref: str = ""
    receipt_hash: str = ""
    receipt_binding_hash: str = ""
    receipt_outcome: DirectedEffectReceiptOutcomeV1 = "succeeded"

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "receipt_hash",
            "receipt_binding_hash",
        ):
            object.__setattr__(
                self, field_name, _directed_effect_inventory_digest(field_name, getattr(self, field_name))
            )
        object.__setattr__(self, "receipt_ref", _directed_effect_token("receipt_ref", self.receipt_ref))
        if self.receipt_outcome not in ("succeeded", "failed"):
            raise ValueError("receipt_outcome must be 'succeeded' or 'failed'")


@dataclass(frozen=True, slots=True)
class MarkDirectedEffectRecoveryPendingCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Move ``EFFECT_STARTED`` to finite, evidence-bound recovery."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""
    reason: str = ""
    recovery_evidence_ref: str = ""
    recovery_evidence_hash: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "recovery_evidence_hash",
        ):
            object.__setattr__(
                self, field_name, _directed_effect_inventory_digest(field_name, getattr(self, field_name))
            )
        for field_name in ("reason", "recovery_evidence_ref"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class DeadLetterDirectedEffectOperationCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Resolve ``RECOVERY_PENDING`` to an evidence-bound terminal dead letter."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""
    reason: str = ""
    resolution_evidence_ref: str = ""
    resolution_evidence_hash: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "resolution_evidence_hash",
        ):
            object.__setattr__(
                self, field_name, _directed_effect_inventory_digest(field_name, getattr(self, field_name))
            )
        for field_name in ("reason", "resolution_evidence_ref"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class GetDirectedEffectOperationQueryV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    tool_call_id: str
    effect_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")
        for field_name in ("tool_call_id", "effect_id"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class GetDirectedEffectParentReadinessQueryV1:
    """Read one parent-bound operation-stream diagnostic without authority effects."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")


@dataclass(frozen=True, slots=True)
class DirectedEffectParentReadinessStateCountV1:
    """One immutable final-state count from a strict parent operation scan."""

    state: DirectedEffectOperationStateV1
    count: int

    def __post_init__(self) -> None:
        if self.state not in _DIRECTED_EFFECT_OPERATION_STATES:
            raise ValueError("state must be an existing directed effect operation state")
        _directed_effect_non_negative_int("count", self.count)


@dataclass(frozen=True, slots=True)
class DirectedEffectParentReadinessProjectionV1:
    """Immutable, non-authoritative diagnostic aggregate for one parent."""

    schema_version: str
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding_id: str
    parent_registry_stream_token: str
    parent_registry_source_head_seq: int
    operation_stream_token: str
    operation_source_head_seq: int
    operation_count: int
    state_counts: tuple[DirectedEffectParentReadinessStateCountV1, ...]
    enforcement: Literal["not_enabled"] = "not_enabled"

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent readiness projection schema")
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.workspace != self.execution_attempt.workspace or self.task_id != self.execution_attempt.task_id:
            raise ValueError("readiness projection workspace and task must match execution_attempt")
        for field_name in (
            "parent_binding_id",
            "parent_registry_stream_token",
            "operation_stream_token",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))
        _directed_effect_non_negative_int("parent_registry_source_head_seq", self.parent_registry_source_head_seq)
        _directed_effect_non_negative_int("operation_source_head_seq", self.operation_source_head_seq)
        _directed_effect_non_negative_int("operation_count", self.operation_count)
        if self.enforcement != "not_enabled":
            raise ValueError("enforcement must be not_enabled")
        if not isinstance(self.state_counts, tuple):
            raise TypeError("state_counts must be a tuple")
        if any(not isinstance(item, DirectedEffectParentReadinessStateCountV1) for item in self.state_counts):
            raise TypeError("state_counts must contain DirectedEffectParentReadinessStateCountV1")
        if tuple(item.state for item in self.state_counts) != _DIRECTED_EFFECT_OPERATION_STATES:
            raise ValueError("state_counts must contain each directed effect state in canonical order")
        if sum(item.count for item in self.state_counts) != self.operation_count:
            raise ValueError("state_counts must sum to operation_count")


@dataclass(frozen=True, slots=True)
class DirectedEffectParentReadinessResultV1:
    """Typed outcome for the read-only parent operation-stream diagnostic."""

    ok: bool
    code: DirectedEffectParentReadinessCodeV1
    projection: DirectedEffectParentReadinessProjectionV1 | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ok != (self.code == "readiness_observed"):
            raise ValueError("ok must match readiness_observed")
        if self.ok != (self.projection is not None):
            raise ValueError("successful readiness result requires a projection")
        if self.projection is not None and not isinstance(self.projection, DirectedEffectParentReadinessProjectionV1):
            raise TypeError("projection must be DirectedEffectParentReadinessProjectionV1 or None")
        evidence = _to_immutable_evidence(self.evidence)
        if self.ok:
            projection = cast(DirectedEffectParentReadinessProjectionV1, self.projection)
            expected_source_heads = {
                "parent_registry_source_head_seq": projection.parent_registry_source_head_seq,
                "operation_source_head_seq": projection.operation_source_head_seq,
            }
            if set(evidence) != _SUCCESS_READINESS_EVIDENCE_KEYS or any(
                isinstance(evidence[key], bool) or not isinstance(evidence[key], int) or evidence[key] != expected_value
                for key, expected_value in expected_source_heads.items()
            ):
                raise ValueError("successful readiness evidence must match diagnostic schema")
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True, slots=True)
class DirectedEffectOperationResultV1:
    ok: bool
    code: DirectedEffectOperationCodeV1
    operation: DirectedEffectOperationIdentityV1 | None = None
    parent_binding: DirectedEffectParentBindingV1 | None = None
    parent_registry: DirectedEffectParentRegistryProjectionV1 | None = None
    state: DirectedEffectOperationStateV1 | None = None
    version: int = 0
    snapshot: DirectedEffectOperationSnapshotV1 | None = None
    idempotent: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)
    claim_grant: DirectedEffectClaimGrantV1 | None = None

    def __post_init__(self) -> None:
        success_codes = {
            "parent_admitted",
            "parent_idempotent_replay",
            "admitted",
            "effect_claimed",
            "receipt_committed",
            "recovery_pending",
            "dead_lettered",
            "closed_by_parent",
            "aborted",
            "found",
            "idempotent_replay",
        }
        if self.ok != (self.code in success_codes):
            raise ValueError("ok must match directed effect operation result code")
        if self.operation is not None and not isinstance(self.operation, DirectedEffectOperationIdentityV1):
            raise TypeError("operation must be DirectedEffectOperationIdentityV1 or None")
        if self.snapshot is not None and not isinstance(self.snapshot, DirectedEffectOperationSnapshotV1):
            raise TypeError("snapshot must be DirectedEffectOperationSnapshotV1 or None")
        if self.parent_binding is not None and not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1 or None")
        if self.parent_registry is not None and not isinstance(
            self.parent_registry, DirectedEffectParentRegistryProjectionV1
        ):
            raise TypeError("parent_registry must be DirectedEffectParentRegistryProjectionV1 or None")
        if self.claim_grant is not None and type(self.claim_grant) is not DirectedEffectClaimGrantV1:
            raise TypeError("claim_grant must be exactly DirectedEffectClaimGrantV1 or None")
        if (self.code == "effect_claimed") != (self.claim_grant is not None):
            raise ValueError("effect_claimed requires exactly one claim_grant")
        parent_success = self.code in {"parent_admitted", "parent_idempotent_replay"}
        if self.ok and parent_success != (self.parent_binding is not None and self.operation is None):
            raise ValueError("parent admission success requires exactly one parent binding")
        if self.ok and not parent_success and (self.operation is None or self.state is None or self.version < 1):
            raise ValueError("successful directed effect operation result requires aggregate state")
        if self.idempotent != (self.code in {"idempotent_replay", "parent_idempotent_replay"}):
            raise ValueError("idempotent must match an idempotent replay code")
        if self.claim_grant is not None:
            if self.code != "effect_claimed":
                raise ValueError("only effect_claimed may carry a claim_grant")
            if self.operation != self.claim_grant.operation:
                raise ValueError("operation must match claim_grant operation")
            if self.state != "EFFECT_STARTED":
                raise ValueError("claim_grant requires EFFECT_STARTED state")
            if self.version != self.claim_grant.operation_version:
                raise ValueError("version must match claim_grant operation_version")
            if self.parent_binding is not None and self.parent_binding != self.claim_grant.parent_binding:
                raise ValueError("parent_binding must match claim_grant parent_binding")
        object.__setattr__(self, "evidence", _to_detached_dict(self.evidence))


@dataclass(frozen=True, slots=True)
class ReconcileAmbiguousDirectedEffectsCommandV1:
    """Request one bounded Factory-startup recovery sweep without effect replay.

    TaskRuntime mints and persists the actual maintenance authority under its
    workspace recovery lock.  Callers deliberately cannot supply an owner PID,
    epoch, lease id, or token.
    """

    workspace: str
    reason: str
    factory_run_id: str = ""
    actor: str = "factory.settlement.startup"
    authority_kind: Literal["factory_settlement_startup"] = "factory_settlement_startup"
    max_sessions: int = 256
    max_operations: int = 4096
    deadline_seconds: float = 30.0
    lock_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        workspace = str(Path(self.workspace).expanduser().resolve())
        if self.workspace != workspace:
            raise ValueError("workspace must be canonical")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "reason", _directed_effect_token("reason", self.reason))
        object.__setattr__(self, "factory_run_id", str(self.factory_run_id or "").strip())
        object.__setattr__(self, "actor", _directed_effect_token("actor", self.actor))
        if self.actor != "factory.settlement.startup":
            raise ValueError("actor must be factory.settlement.startup")
        if self.authority_kind != "factory_settlement_startup":
            raise ValueError("authority_kind must be factory_settlement_startup")
        _directed_effect_positive_int("max_sessions", self.max_sessions)
        _directed_effect_positive_int("max_operations", self.max_operations)
        for field_name in ("deadline_seconds", "lock_timeout_seconds"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field_name} must be a finite positive number")
            normalized = float(value)
            if not math.isfinite(normalized) or normalized <= 0:
                raise ValueError(f"{field_name} must be a finite positive number")
            object.__setattr__(self, field_name, normalized)


@dataclass(frozen=True, slots=True)
class DirectedEffectRecoverySweepItemV1:
    """One TaskRuntime recovery/dead-letter fact exposed to read-only sinks."""

    factory_run_id: str
    session_id: str
    task_id: int
    operation_id: str
    code: Literal["recovery_pending", "dead_lettered"]
    state: Literal["RECOVERY_PENDING", "DEAD_LETTER"]
    version: int
    event_id: str
    evidence_ref: str
    evidence_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "factory_run_id", str(self.factory_run_id or "").strip())
        for field_name in ("session_id", "operation_id", "event_id", "evidence_ref"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))
        _directed_effect_positive_int("task_id", self.task_id)
        _directed_effect_positive_int("version", self.version)
        expected_code = {"RECOVERY_PENDING": "recovery_pending", "DEAD_LETTER": "dead_lettered"}.get(self.state)
        if self.code != expected_code:
            raise ValueError("recovery sweep state and code must agree")
        object.__setattr__(
            self,
            "evidence_hash",
            _directed_effect_inventory_digest("evidence_hash", self.evidence_hash),
        )

    def to_record(self) -> dict[str, object]:
        evidence_prefix = "recovery" if self.state == "RECOVERY_PENDING" else "resolution"
        return {
            "schema_version": "roles.adapters.directed_effect_recovery_fact.v1",
            "authoritative": True,
            "durable": True,
            "factory_run_id": self.factory_run_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "operation_id": self.operation_id,
            "code": self.code,
            "state": self.state,
            "version": self.version,
            "event_id": self.event_id,
            f"{evidence_prefix}_evidence_ref": self.evidence_ref,
            f"{evidence_prefix}_evidence_hash": self.evidence_hash,
        }


@dataclass(frozen=True, slots=True)
class DirectedEffectRecoverySweepResultV1:
    """Bounded startup/stale-owner recovery report."""

    ok: bool
    code: Literal["reconciled", "partial_failure"]
    workspace: str
    scanned_session_count: int
    items: tuple[DirectedEffectRecoverySweepItemV1, ...] = ()
    failures: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.ok != (self.code == "reconciled"):
            raise ValueError("ok must match recovery sweep code")
        workspace = str(Path(self.workspace).expanduser().resolve())
        if self.workspace != workspace:
            raise ValueError("workspace must be canonical")
        _directed_effect_non_negative_int("scanned_session_count", self.scanned_session_count)
        if not isinstance(self.items, tuple) or any(
            type(item) is not DirectedEffectRecoverySweepItemV1 for item in self.items
        ):
            raise TypeError("items must contain exact DirectedEffectRecoverySweepItemV1 values")
        if not isinstance(self.failures, tuple) or any(not isinstance(item, Mapping) for item in self.failures):
            raise TypeError("failures must be a tuple of mappings")
        object.__setattr__(self, "failures", tuple(_to_immutable_evidence(item) for item in self.failures))


@dataclass(frozen=True, slots=True)
class DirectedEffectParentRegistryResultV1:
    ok: bool
    code: DirectedEffectOperationCodeV1
    registry: DirectedEffectParentRegistryProjectionV1 | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ok != (self.code == "parent_registry_found"):
            raise ValueError("ok must match parent registry result code")
        if self.ok != (self.registry is not None):
            raise ValueError("successful parent registry result requires a projection")
        if self.registry is not None and not isinstance(self.registry, DirectedEffectParentRegistryProjectionV1):
            raise TypeError("registry must be DirectedEffectParentRegistryProjectionV1 or None")
        object.__setattr__(self, "evidence", _to_detached_dict(self.evidence))
