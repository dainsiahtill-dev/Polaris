"""Directed-effect inventory contracts: intent, member, projection, seal.

Also hosts parent-correlation/identity/binding types sealed with inventory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from polaris.cells.runtime.task_runtime.public.contracts._directed_effect_common import (
    _DIRECTED_EFFECT_AUTHORITY_FAILURE_CODES,
    _DIRECTED_EFFECT_INVENTORY_EFFECT_MODE_PAIRS,
    DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1,
    DirectedEffectInventoryCodeV1,
    DirectedEffectInventoryContingencyKindV1,
    DirectedEffectInventoryEffectTypeV1,
    DirectedEffectInventoryExecutionModeV1,
    _directed_effect_inventory_digest,
    _directed_effect_inventory_token,
    _directed_effect_non_negative_int,
    _directed_effect_positive_int,
    _directed_effect_token,
)
from polaris.cells.runtime.task_runtime.public.contracts._execution_attempts import (
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.contracts._helpers import (
    _to_immutable_evidence,
)


@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryIntentV1:
    """One immutable, execution-grade member of a sealed effect inventory."""

    ordinal: int
    tool_call_id: str
    normalized_tool_name: str
    effect_type: DirectedEffectInventoryEffectTypeV1
    execution_mode: DirectedEffectInventoryExecutionModeV1
    intended_effect_fingerprint: str
    policy_verdict_hash: str
    expected_receipt_binding_hash: str
    contingency_kind: DirectedEffectInventoryContingencyKindV1 | None = None
    schema_version: str = DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1

    def __post_init__(self) -> None:
        _directed_effect_non_negative_int("ordinal", self.ordinal)
        object.__setattr__(
            self,
            "tool_call_id",
            _directed_effect_inventory_token("tool_call_id", self.tool_call_id),
        )
        object.__setattr__(
            self,
            "normalized_tool_name",
            _directed_effect_inventory_token("normalized_tool_name", self.normalized_tool_name),
        )
        if not isinstance(self.effect_type, str):
            raise TypeError("effect_type must be a string")
        if not isinstance(self.execution_mode, str):
            raise TypeError("execution_mode must be a string")
        if (self.effect_type, self.execution_mode) not in _DIRECTED_EFFECT_INVENTORY_EFFECT_MODE_PAIRS:
            raise ValueError("effect_type and execution_mode must form a supported pair")
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _directed_effect_inventory_digest(field_name, getattr(self, field_name)),
            )
        if self.contingency_kind is not None and not isinstance(self.contingency_kind, str):
            raise TypeError("contingency_kind must be a string or None")
        if self.contingency_kind not in (None, "forward", "rollback"):
            raise ValueError("contingency_kind must be None, 'forward', or 'rollback'")
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string")
        if self.schema_version != DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1:
            raise ValueError("unsupported directed effect inventory intent schema")

    def to_record(self) -> dict[str, object]:
        """Return the exact canonical persisted intent projection."""

        return {
            "schema_version": self.schema_version,
            "ordinal": self.ordinal,
            "tool_call_id": self.tool_call_id,
            "normalized_tool_name": self.normalized_tool_name,
            "effect_type": self.effect_type,
            "execution_mode": self.execution_mode,
            "intended_effect_fingerprint": self.intended_effect_fingerprint,
            "policy_verdict_hash": self.policy_verdict_hash,
            "expected_receipt_binding_hash": self.expected_receipt_binding_hash,
            "contingency_kind": self.contingency_kind,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DirectedEffectInventoryIntentV1:
        """Parse one exact canonical inventory intent, failing closed on drift."""

        if not isinstance(record, Mapping):
            raise TypeError("directed effect inventory intent record must be a mapping")
        expected_fields = {
            "schema_version",
            "ordinal",
            "tool_call_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "contingency_kind",
        }
        actual_fields = set(record)
        if actual_fields != expected_fields:
            missing_fields = sorted(expected_fields - actual_fields)
            unexpected_fields = sorted(actual_fields - expected_fields)
            raise ValueError(
                "directed effect inventory intent record fields must match canonical schema: "
                f"missing={missing_fields!r}, unexpected={unexpected_fields!r}"
            )
        ordinal = record["ordinal"]
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise TypeError("directed effect inventory intent ordinal must be an int")
        string_fields = (
            "schema_version",
            "tool_call_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        )
        for field_name in string_fields:
            if not isinstance(record[field_name], str):
                raise TypeError(f"directed effect inventory intent {field_name} must be a string")
        contingency_kind = record["contingency_kind"]
        if contingency_kind is not None and not isinstance(contingency_kind, str):
            raise TypeError("directed effect inventory intent contingency_kind must be a string or None")
        if record["schema_version"] != DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1:
            raise ValueError("directed effect inventory intent schema_version is unsupported")
        return cls(
            schema_version=record["schema_version"],
            ordinal=ordinal,
            tool_call_id=record["tool_call_id"],
            normalized_tool_name=record["normalized_tool_name"],
            effect_type=cast(DirectedEffectInventoryEffectTypeV1, record["effect_type"]),
            execution_mode=cast(DirectedEffectInventoryExecutionModeV1, record["execution_mode"]),
            intended_effect_fingerprint=record["intended_effect_fingerprint"],
            policy_verdict_hash=record["policy_verdict_hash"],
            expected_receipt_binding_hash=record["expected_receipt_binding_hash"],
            contingency_kind=cast(DirectedEffectInventoryContingencyKindV1 | None, contingency_kind),
        )


@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryMemberV1:
    """One canonical sealed member with server-derived effect identities."""

    ordinal: int
    tool_call_id: str
    effect_id: str
    operation_id: str
    normalized_tool_name: str
    effect_type: DirectedEffectInventoryEffectTypeV1
    execution_mode: DirectedEffectInventoryExecutionModeV1
    intended_effect_fingerprint: str
    policy_verdict_hash: str
    expected_receipt_binding_hash: str
    contingency_kind: DirectedEffectInventoryContingencyKindV1 | None = None
    schema_version: str = DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1

    def __post_init__(self) -> None:
        intent = DirectedEffectInventoryIntentV1(
            ordinal=self.ordinal,
            tool_call_id=self.tool_call_id,
            normalized_tool_name=self.normalized_tool_name,
            effect_type=self.effect_type,
            execution_mode=self.execution_mode,
            intended_effect_fingerprint=self.intended_effect_fingerprint,
            policy_verdict_hash=self.policy_verdict_hash,
            expected_receipt_binding_hash=self.expected_receipt_binding_hash,
            contingency_kind=self.contingency_kind,
        )
        for field_name in (
            "ordinal",
            "tool_call_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "contingency_kind",
        ):
            object.__setattr__(self, field_name, getattr(intent, field_name))
        object.__setattr__(self, "effect_id", _directed_effect_inventory_token("effect_id", self.effect_id))
        object.__setattr__(
            self,
            "operation_id",
            _directed_effect_inventory_token("operation_id", self.operation_id),
        )
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string")
        if self.schema_version != DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1:
            raise ValueError("unsupported directed effect inventory member schema")

    def to_record(self) -> dict[str, object]:
        """Return the exact canonical persisted member projection."""

        return {
            "schema_version": self.schema_version,
            "ordinal": self.ordinal,
            "tool_call_id": self.tool_call_id,
            "effect_id": self.effect_id,
            "operation_id": self.operation_id,
            "normalized_tool_name": self.normalized_tool_name,
            "effect_type": self.effect_type,
            "execution_mode": self.execution_mode,
            "intended_effect_fingerprint": self.intended_effect_fingerprint,
            "policy_verdict_hash": self.policy_verdict_hash,
            "expected_receipt_binding_hash": self.expected_receipt_binding_hash,
            "contingency_kind": self.contingency_kind,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DirectedEffectInventoryMemberV1:
        """Parse one exact canonical inventory member, failing closed on drift."""

        if not isinstance(record, Mapping):
            raise TypeError("directed effect inventory member record must be a mapping")
        expected_fields = {
            "schema_version",
            "ordinal",
            "tool_call_id",
            "effect_id",
            "operation_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "contingency_kind",
        }
        actual_fields = set(record)
        if actual_fields != expected_fields:
            missing_fields = sorted(expected_fields - actual_fields)
            unexpected_fields = sorted(actual_fields - expected_fields)
            raise ValueError(
                "directed effect inventory member record fields must match canonical schema: "
                f"missing={missing_fields!r}, unexpected={unexpected_fields!r}"
            )
        schema_version = record["schema_version"]
        if not isinstance(schema_version, str):
            raise TypeError("directed effect inventory member schema_version must be a string")
        if schema_version != DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1:
            raise ValueError("directed effect inventory member schema_version is unsupported")
        intent = DirectedEffectInventoryIntentV1.from_record(
            {
                "schema_version": DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1,
                "ordinal": record["ordinal"],
                "tool_call_id": record["tool_call_id"],
                "normalized_tool_name": record["normalized_tool_name"],
                "effect_type": record["effect_type"],
                "execution_mode": record["execution_mode"],
                "intended_effect_fingerprint": record["intended_effect_fingerprint"],
                "policy_verdict_hash": record["policy_verdict_hash"],
                "expected_receipt_binding_hash": record["expected_receipt_binding_hash"],
                "contingency_kind": record["contingency_kind"],
            }
        )
        return cls(
            schema_version=schema_version,
            ordinal=intent.ordinal,
            tool_call_id=intent.tool_call_id,
            effect_id=record["effect_id"],
            operation_id=record["operation_id"],
            normalized_tool_name=intent.normalized_tool_name,
            effect_type=intent.effect_type,
            execution_mode=intent.execution_mode,
            intended_effect_fingerprint=intent.intended_effect_fingerprint,
            policy_verdict_hash=intent.policy_verdict_hash,
            expected_receipt_binding_hash=intent.expected_receipt_binding_hash,
            contingency_kind=intent.contingency_kind,
        )


@dataclass(frozen=True, slots=True)
class ParentCorrelationV1:
    """Non-authoritative caller correlation attached to one parent admission."""

    turn_id: str
    batch_id: str
    schema_version: str = DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent correlation schema")
        object.__setattr__(self, "turn_id", _directed_effect_token("turn_id", self.turn_id))
        object.__setattr__(self, "batch_id", _directed_effect_token("batch_id", self.batch_id))

    def to_record(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "turn_id": self.turn_id,
            "batch_id": self.batch_id,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ParentCorrelationV1:
        expected = {"schema_version", "turn_id", "batch_id"}
        if not isinstance(record, Mapping) or set(record) != expected:
            raise ValueError("parent correlation record fields must match canonical schema")
        if any(not isinstance(record[field_name], str) for field_name in expected):
            raise TypeError("parent correlation record fields must be strings")
        return cls(
            schema_version=record["schema_version"],
            turn_id=record["turn_id"],
            batch_id=record["batch_id"],
        )


@dataclass(frozen=True, slots=True)
class DirectedEffectParentRegistryIdentityV1:
    """Lease-independent identity for one execution-attempt parent registry."""

    workspace: str
    task_id: int
    external_task_id: str
    session_id: str
    attempt: int
    role_id: str
    worker_id: str
    run_id: str
    schema_version: str = DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent registry identity schema")
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        _directed_effect_positive_int("attempt", self.attempt)
        for field_name in ("session_id", "role_id", "worker_id"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))
        object.__setattr__(self, "external_task_id", str(self.external_task_id or "").strip())
        object.__setattr__(self, "run_id", str(self.run_id or "").strip())

    @classmethod
    def from_execution_attempt(
        cls,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectParentRegistryIdentityV1:
        if not isinstance(identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        return cls(
            workspace=identity.workspace,
            task_id=identity.task_id,
            external_task_id=identity.external_task_id,
            session_id=identity.session_id,
            attempt=identity.attempt,
            role_id=identity.role_id,
            worker_id=identity.worker_id,
            run_id=identity.run_id,
        )

    @property
    def execution_attempt_id(self) -> str:
        return f"{self.session_id}:{self.attempt}"

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "task_id": self.task_id,
            "external_task_id": self.external_task_id,
            "session_id": self.session_id,
            "attempt": self.attempt,
            "role_id": self.role_id,
            "worker_id": self.worker_id,
            "run_id": self.run_id,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DirectedEffectParentRegistryIdentityV1:
        expected = {
            "schema_version",
            "workspace",
            "task_id",
            "external_task_id",
            "session_id",
            "attempt",
            "role_id",
            "worker_id",
            "run_id",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise ValueError("parent registry identity fields must match canonical schema")
        string_fields = expected - {"task_id", "attempt"}
        if any(not isinstance(record[field_name], str) for field_name in string_fields):
            raise TypeError("parent registry identity string fields are invalid")
        for field_name in ("task_id", "attempt"):
            value = record[field_name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"parent registry identity {field_name} must be an int")
        return cls(
            schema_version=record["schema_version"],
            workspace=record["workspace"],
            task_id=record["task_id"],
            external_task_id=record["external_task_id"],
            session_id=record["session_id"],
            attempt=record["attempt"],
            role_id=record["role_id"],
            worker_id=record["worker_id"],
            run_id=record["run_id"],
        )


@dataclass(frozen=True, slots=True)
class DirectedEffectParentBindingV1:
    """Restart-safe reference to one authoritative registry admission fact."""

    schema_version: str
    registry_identity: DirectedEffectParentRegistryIdentityV1
    registry_stream_token: str
    registry_version: int
    parent_sequence: int
    binding_id: str
    operation_stream_token: str
    binding_hash: str
    admission_idempotency_key: str
    correlation: ParentCorrelationV1
    actor: str
    source_event_id: str
    source_event_seq: int

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent binding schema")
        if not isinstance(self.registry_identity, DirectedEffectParentRegistryIdentityV1):
            raise TypeError("registry_identity must be DirectedEffectParentRegistryIdentityV1")
        if not isinstance(self.correlation, ParentCorrelationV1):
            raise TypeError("correlation must be ParentCorrelationV1")
        _directed_effect_positive_int("registry_version", self.registry_version)
        _directed_effect_positive_int("parent_sequence", self.parent_sequence)
        _directed_effect_positive_int("source_event_seq", self.source_event_seq)
        if self.source_event_seq != self.registry_version:
            raise ValueError("source_event_seq must equal registry_version")
        for field_name in (
            "registry_stream_token",
            "binding_id",
            "operation_stream_token",
            "binding_hash",
            "admission_idempotency_key",
            "actor",
            "source_event_id",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))

    @property
    def workspace(self) -> str:
        return self.registry_identity.workspace

    @property
    def task_id(self) -> int:
        return self.registry_identity.task_id

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "registry_identity": self.registry_identity.to_record(),
            "registry_stream_token": self.registry_stream_token,
            "registry_version": self.registry_version,
            "parent_sequence": self.parent_sequence,
            "binding_id": self.binding_id,
            "operation_stream_token": self.operation_stream_token,
            "binding_hash": self.binding_hash,
            "admission_idempotency_key": self.admission_idempotency_key,
            "correlation": self.correlation.to_record(),
            "actor": self.actor,
            "source_event_id": self.source_event_id,
            "source_event_seq": self.source_event_seq,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DirectedEffectParentBindingV1:
        expected = {
            "schema_version",
            "registry_identity",
            "registry_stream_token",
            "registry_version",
            "parent_sequence",
            "binding_id",
            "operation_stream_token",
            "binding_hash",
            "admission_idempotency_key",
            "correlation",
            "actor",
            "source_event_id",
            "source_event_seq",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise ValueError("parent binding record fields must match canonical schema")
        registry_identity = record["registry_identity"]
        correlation = record["correlation"]
        if not isinstance(registry_identity, Mapping) or not isinstance(correlation, Mapping):
            raise TypeError("parent binding nested records must be mappings")
        string_fields = expected - {
            "registry_identity",
            "registry_version",
            "parent_sequence",
            "correlation",
            "source_event_seq",
        }
        if any(not isinstance(record[field_name], str) for field_name in string_fields):
            raise TypeError("parent binding string fields are invalid")
        for field_name in ("registry_version", "parent_sequence", "source_event_seq"):
            value = record[field_name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"parent binding {field_name} must be an int")
        return cls(
            schema_version=record["schema_version"],
            registry_identity=DirectedEffectParentRegistryIdentityV1.from_record(registry_identity),
            registry_stream_token=record["registry_stream_token"],
            registry_version=record["registry_version"],
            parent_sequence=record["parent_sequence"],
            binding_id=record["binding_id"],
            operation_stream_token=record["operation_stream_token"],
            binding_hash=record["binding_hash"],
            admission_idempotency_key=record["admission_idempotency_key"],
            correlation=ParentCorrelationV1.from_record(correlation),
            actor=record["actor"],
            source_event_id=record["source_event_id"],
            source_event_seq=record["source_event_seq"],
        )


@dataclass(frozen=True, slots=True)
class SealDirectedEffectInventoryCommandV1:
    """Seal the complete immutable inventory before any child effect claim."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    intents: tuple[DirectedEffectInventoryIntentV1, ...]
    expected_registry_version: int
    expected_registry_seq: int
    expected_operation_head_seq: int = 0
    actor: str = "roles.kernel"

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, str):
            raise TypeError("workspace must be a string")
        workspace = self.workspace.strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        canonical_workspace = str(Path(workspace).resolve())
        if workspace != canonical_workspace:
            raise ValueError("workspace must be canonical")
        object.__setattr__(self, "workspace", canonical_workspace)

        _directed_effect_positive_int("task_id", self.task_id)
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
        if type(self.parent_binding) is not DirectedEffectParentBindingV1:
            raise TypeError("parent_binding must be exactly DirectedEffectParentBindingV1")
        if self.execution_attempt.workspace != canonical_workspace:
            raise ValueError("execution_attempt workspace must match workspace")
        if self.parent_binding.workspace != canonical_workspace:
            raise ValueError("parent_binding workspace must match workspace")
        if self.execution_attempt.task_id != self.task_id:
            raise ValueError("execution_attempt task_id must match task_id")
        if self.parent_binding.task_id != self.task_id:
            raise ValueError("parent_binding task_id must match task_id")
        expected_registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
            self.execution_attempt
        )
        if self.parent_binding.registry_identity != expected_registry_identity:
            raise ValueError("parent_binding registry identity must match execution_attempt")

        if not isinstance(self.intents, tuple):
            raise TypeError("intents must be a tuple")
        if not 1 <= len(self.intents) <= 64:
            raise ValueError("intents must contain between 1 and 64 items")
        detached_intents = tuple(intent for intent in self.intents)
        seen_tool_call_ids: set[str] = set()
        for expected_ordinal, intent in enumerate(detached_intents):
            if type(intent) is not DirectedEffectInventoryIntentV1:
                raise TypeError("each intent must be exactly DirectedEffectInventoryIntentV1")
            if intent.ordinal != expected_ordinal:
                raise ValueError("intent ordinals must be contiguous and ordered from zero")
            if intent.tool_call_id in seen_tool_call_ids:
                raise ValueError("intent tool_call_id values must be unique")
            seen_tool_call_ids.add(intent.tool_call_id)
        object.__setattr__(self, "intents", detached_intents)

        _directed_effect_positive_int("expected_registry_version", self.expected_registry_version)
        if (
            isinstance(self.expected_registry_seq, bool)
            or not isinstance(self.expected_registry_seq, int)
            or self.expected_registry_seq < 2
        ):
            raise ValueError("expected_registry_seq must be an int >= 2")
        if self.expected_registry_seq != self.expected_registry_version + 1:
            raise ValueError("expected_registry_seq must equal expected_registry_version + 1")
        if (
            isinstance(self.expected_operation_head_seq, bool)
            or not isinstance(self.expected_operation_head_seq, int)
            or self.expected_operation_head_seq != 0
        ):
            raise ValueError("expected_operation_head_seq must be exactly 0")
        object.__setattr__(self, "actor", _directed_effect_inventory_token("actor", self.actor))


def _directed_effect_inventory_identity(
    *,
    workspace: str,
    task_id: int,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    parent_binding: DirectedEffectParentBindingV1,
) -> str:
    """Validate and return one exact canonical inventory parent identity."""

    if not isinstance(workspace, str):
        raise TypeError("workspace must be a string")
    workspace_token = workspace.strip()
    if not workspace_token:
        raise ValueError("workspace must be a non-empty string")
    if workspace != workspace_token:
        raise ValueError("workspace must not contain surrounding whitespace")
    canonical_workspace = str(Path(workspace_token).resolve())
    if workspace_token != canonical_workspace:
        raise ValueError("workspace must be canonical")
    _directed_effect_positive_int("task_id", task_id)
    if type(execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
        raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
    if type(parent_binding) is not DirectedEffectParentBindingV1:
        raise TypeError("parent_binding must be exactly DirectedEffectParentBindingV1")
    if execution_attempt.workspace != canonical_workspace:
        raise ValueError("execution_attempt workspace must match workspace")
    if parent_binding.workspace != canonical_workspace:
        raise ValueError("parent_binding workspace must match workspace")
    if execution_attempt.task_id != task_id:
        raise ValueError("execution_attempt task_id must match task_id")
    if parent_binding.task_id != task_id:
        raise ValueError("parent_binding task_id must match task_id")
    expected_registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(execution_attempt)
    if parent_binding.registry_identity != expected_registry_identity:
        raise ValueError("parent_binding registry identity must match execution_attempt")
    return canonical_workspace


@dataclass(frozen=True, slots=True)
class FinalizeDirectedEffectInventoryAdmissionCommandV1:
    """Request exact sealed/admitted inventory equality before effect claims."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    inventory_hash: str
    expected_registry_version: int
    expected_registry_seq: int
    expected_operation_head_seq: int
    actor: str = "roles.kernel"

    def __post_init__(self) -> None:
        canonical_workspace = _directed_effect_inventory_identity(
            workspace=self.workspace,
            task_id=self.task_id,
            execution_attempt=self.execution_attempt,
            parent_binding=self.parent_binding,
        )
        object.__setattr__(self, "workspace", canonical_workspace)
        object.__setattr__(
            self,
            "inventory_hash",
            _directed_effect_inventory_digest("inventory_hash", self.inventory_hash),
        )
        if (
            isinstance(self.expected_registry_version, bool)
            or not isinstance(self.expected_registry_version, int)
            or self.expected_registry_version < 2
        ):
            raise ValueError("expected_registry_version must be an int >= 2")
        if isinstance(self.expected_registry_seq, bool) or not isinstance(self.expected_registry_seq, int):
            raise ValueError("expected_registry_seq must be an int")
        if self.expected_registry_seq != self.expected_registry_version + 1:
            raise ValueError("expected_registry_seq must equal expected_registry_version + 1")
        _directed_effect_positive_int("expected_operation_head_seq", self.expected_operation_head_seq)
        if not isinstance(self.actor, str):
            raise TypeError("actor must be a string")
        if self.actor != "roles.kernel":
            raise ValueError("actor must be exactly 'roles.kernel'")


@dataclass(frozen=True, slots=True)
class GetDirectedEffectInventoryQueryV1:
    """Read the inventory bound to one exact attempt and parent admission."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1

    def __post_init__(self) -> None:
        canonical_workspace = _directed_effect_inventory_identity(
            workspace=self.workspace,
            task_id=self.task_id,
            execution_attempt=self.execution_attempt,
            parent_binding=self.parent_binding,
        )
        object.__setattr__(self, "workspace", canonical_workspace)


@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryProjectionV1:
    """Immutable diagnostic projection of one sealed parent inventory."""

    schema_version: str
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding_id: str
    members: tuple[DirectedEffectInventoryMemberV1, ...]
    inventory_hash: str
    sealed_event_id: str
    sealed_event_seq: int
    parent_registry_source_head_seq: int
    operation_source_head_seq: int
    inventory_ready: bool
    ready_event_id: str | None
    ready_event_seq: int | None
    admitted_count: int
    missing_operation_ids: tuple[str, ...]
    unexpected_operation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string")
        if self.schema_version != DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1:
            raise ValueError("unsupported directed effect inventory projection schema")

        if not isinstance(self.workspace, str):
            raise TypeError("workspace must be a string")
        workspace = self.workspace.strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        if self.workspace != workspace:
            raise ValueError("workspace must not contain surrounding whitespace")
        canonical_workspace = str(Path(workspace).resolve())
        if workspace != canonical_workspace:
            raise ValueError("workspace must be canonical")
        object.__setattr__(self, "workspace", canonical_workspace)
        _directed_effect_positive_int("task_id", self.task_id)
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
        if self.execution_attempt.workspace != canonical_workspace:
            raise ValueError("execution_attempt workspace must match workspace")
        if self.execution_attempt.task_id != self.task_id:
            raise ValueError("execution_attempt task_id must match task_id")
        object.__setattr__(
            self,
            "parent_binding_id",
            _directed_effect_inventory_token("parent_binding_id", self.parent_binding_id),
        )

        if not isinstance(self.members, tuple):
            raise TypeError("members must be a tuple")
        if not 1 <= len(self.members) <= 64:
            raise ValueError("members must contain between 1 and 64 items")
        members = tuple(member for member in self.members)
        tool_call_ids: set[str] = set()
        effect_ids: set[str] = set()
        operation_ids: set[str] = set()
        for expected_ordinal, member in enumerate(members):
            if type(member) is not DirectedEffectInventoryMemberV1:
                raise TypeError("each member must be exactly DirectedEffectInventoryMemberV1")
            if member.ordinal != expected_ordinal:
                raise ValueError("member ordinals must be contiguous and ordered from zero")
            for field_name, seen_values in (
                ("tool_call_id", tool_call_ids),
                ("effect_id", effect_ids),
                ("operation_id", operation_ids),
            ):
                value = getattr(member, field_name)
                if value in seen_values:
                    raise ValueError(f"member {field_name} values must be unique")
                seen_values.add(value)
        object.__setattr__(self, "members", members)
        object.__setattr__(
            self,
            "inventory_hash",
            _directed_effect_inventory_digest("inventory_hash", self.inventory_hash),
        )
        object.__setattr__(
            self,
            "sealed_event_id",
            _directed_effect_inventory_token("sealed_event_id", self.sealed_event_id),
        )
        _directed_effect_positive_int("sealed_event_seq", self.sealed_event_seq)
        _directed_effect_non_negative_int(
            "parent_registry_source_head_seq",
            self.parent_registry_source_head_seq,
        )
        _directed_effect_non_negative_int(
            "operation_source_head_seq",
            self.operation_source_head_seq,
        )
        if self.sealed_event_seq > self.parent_registry_source_head_seq:
            raise ValueError("sealed_event_seq must not exceed parent_registry_source_head_seq")
        if type(self.inventory_ready) is not bool:
            raise TypeError("inventory_ready must be exactly bool")

        if self.inventory_ready:
            if self.ready_event_id is None or self.ready_event_seq is None:
                raise ValueError("ready inventory requires ready event id and sequence")
            object.__setattr__(
                self,
                "ready_event_id",
                _directed_effect_inventory_token("ready_event_id", self.ready_event_id),
            )
            _directed_effect_positive_int("ready_event_seq", self.ready_event_seq)
            if self.ready_event_seq <= self.sealed_event_seq:
                raise ValueError("ready_event_seq must follow sealed_event_seq")
            if self.ready_event_seq > self.parent_registry_source_head_seq:
                raise ValueError("ready_event_seq must not exceed parent_registry_source_head_seq")
        elif self.ready_event_id is not None or self.ready_event_seq is not None:
            raise ValueError("non-ready inventory must not carry a ready event")

        _directed_effect_non_negative_int("admitted_count", self.admitted_count)
        missing_operation_ids = self._validated_operation_ids(
            "missing_operation_ids",
            self.missing_operation_ids,
        )
        unexpected_operation_ids = self._validated_operation_ids(
            "unexpected_operation_ids",
            self.unexpected_operation_ids,
        )
        member_operation_ids = tuple(member.operation_id for member in members)
        member_operation_id_set = set(member_operation_ids)
        missing_set = set(missing_operation_ids)
        unexpected_set = set(unexpected_operation_ids)
        if not missing_set.issubset(member_operation_id_set):
            raise ValueError("missing_operation_ids must be a subset of member operation ids")
        expected_missing_order = tuple(
            operation_id for operation_id in member_operation_ids if operation_id in missing_set
        )
        if missing_operation_ids != expected_missing_order:
            raise ValueError("missing_operation_ids must be unique and follow member order")
        if len(unexpected_set) != len(unexpected_operation_ids):
            raise ValueError("unexpected_operation_ids must be unique")
        if unexpected_set & member_operation_id_set:
            raise ValueError("unexpected_operation_ids must be disjoint from member operation ids")
        expected_admitted_count = len(members) - len(missing_operation_ids)
        if self.admitted_count != expected_admitted_count:
            raise ValueError("admitted_count must equal members minus missing operations")
        if self.inventory_ready and (missing_operation_ids or unexpected_operation_ids):
            raise ValueError("ready inventory must have exact complete admission")
        minimum_operation_head_seq = self.admitted_count + len(unexpected_operation_ids)
        if self.operation_source_head_seq < minimum_operation_head_seq:
            raise ValueError("operation_source_head_seq must cover admitted and unexpected operations")
        object.__setattr__(self, "missing_operation_ids", missing_operation_ids)
        object.__setattr__(self, "unexpected_operation_ids", unexpected_operation_ids)

    @staticmethod
    def _validated_operation_ids(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(values, tuple):
            raise TypeError(f"{name} must be a tuple")
        return tuple(_directed_effect_inventory_token(name, value) for value in values)


_DIRECTED_EFFECT_INVENTORY_SUCCESS_CODES: Final[frozenset[str]] = frozenset(
    {
        "inventory_sealed",
        "inventory_seal_idempotent_replay",
        "inventory_ready",
        "inventory_ready_idempotent_replay",
        "inventory_observed",
    }
)


@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryResultV1:
    """Typed inventory command/query result with immutable diagnostic evidence."""

    ok: bool
    code: DirectedEffectInventoryCodeV1
    projection: DirectedEffectInventoryProjectionV1 | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise TypeError("ok must be exactly bool")
        if not isinstance(self.code, str):
            raise TypeError("code must be a string")
        success = self.code in _DIRECTED_EFFECT_INVENTORY_SUCCESS_CODES
        failure = self.code in _DIRECTED_EFFECT_AUTHORITY_FAILURE_CODES
        if not success and not failure:
            raise ValueError("code must be an inventory success or directed effect authority failure")
        if self.ok != success:
            raise ValueError("ok must match inventory success code")
        if self.ok != (self.projection is not None):
            raise ValueError("successful inventory result requires exactly one projection")
        if self.projection is not None and type(self.projection) is not DirectedEffectInventoryProjectionV1:
            raise TypeError("projection must be exactly DirectedEffectInventoryProjectionV1 or None")
        if not isinstance(self.evidence, Mapping):
            raise TypeError("evidence must be a mapping")
        object.__setattr__(self, "evidence", _to_immutable_evidence(self.evidence))
