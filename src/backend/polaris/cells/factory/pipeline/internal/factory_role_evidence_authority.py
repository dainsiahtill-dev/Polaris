"""A009B1 Factory-owned fenced role-evidence cutoff authority.

This module freezes only the authority ledger boundary.  Source reconstruction
is deliberately injected and defaults to unavailable until A009B2; role/provider
binding remains absent until A009B3.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import secrets
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    QuerySegmentedFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    SegmentedFactEventAppendedV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactLedgerReadyV1,
    SegmentedFactQueryResultV1,
    append_segmented_fact_event,
    ensure_segmented_fact_ledger,
    query_segmented_fact_events,
    query_segmented_fact_ledger_head,
)
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptLiveControlPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_admission import FactoryWorkspaceRunAdmission
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryRun, FactoryRunStatus
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffProofV1,
    FactoryRoleEvidenceCutoffRequestV1,
    FactoryRoleEvidenceCutoffSourceHeadV1 as PublicFactoryRoleEvidenceCutoffSourceHeadV1,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
    canonical_role_final_request_hash,
    canonical_role_final_request_json,
    role_final_request_policy,
)

FACTORY_ROLE_EVIDENCE_SOURCE_CUT_SCHEMA = "polaris.factory_role_evidence_source_cut.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_BODY_SCHEMA = "polaris.factory_role_evidence_cutoff_body.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_SCHEMA = "polaris.factory_role_evidence_cutoff.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE = "factory.role_evidence_cutoff.issued"
FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_SCHEMA = "polaris.factory_role_evidence_cutoff_fragment.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE = "factory.role_evidence_cutoff.fragment"
FACTORY_ROLE_EVIDENCE_EXECUTION_AUTHORITY_SCHEMA = "polaris.factory_role_evidence_execution_authority.v1"
FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET = 32
FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY = "_factory_role_evidence_cutoff_port"

_AUTHORITY_STREAM_PREFIX = "factory.role_evidence_authority."
_AUTHORITY_SOURCE = "factory.pipeline"
_ABSENT_STATE = "absent_at_request_time"
_PRESENT_STATE = "present"
_LOCATOR_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@#?=&%+\-]{0,255}\Z")
_HASH_LENGTH = 64
_FRAGMENT_RAW_BYTES = 1024
_MAX_CUTOFF_BODY_BYTES = 64 * 1024
_MAX_CUTOFF_FRAGMENTS = 64
_MAX_SOURCE_ITEMS_PER_SLOT = 32
_MAX_SOURCE_ITEMS_TOTAL = 128
_FRAGMENT_ENCODING = "base64url"
_MAX_REQUEST_FREEZES_PER_GRANT = FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET
_STAGE_ROLE_AND_GRANT_CAP: dict[str, tuple[str, int]] = {
    "docs_generation": ("architect", 1),
    "pm_planning": ("pm", 2),
    "chief_engineer_review": ("chief_engineer", 1),
    "director_dispatch": ("director", 512),
    "quality_gate": ("qa", 1),
}

_T = TypeVar("_T")


class FactoryRoleEvidenceAuthorityError(RuntimeError):
    """Stable fail-closed A009B1 authority error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _FactoryRoleEvidenceGrantState:
    """Factory-private capability registry row; never serialized or projected."""

    grant_nonce: str
    role: str
    attempt_budget: int
    execution_authority_hash: str
    controlled_child_run_id: str = ""
    request_freeze_ids: set[str] = field(default_factory=set)
    revoked: bool = False


def _text(field_name: str, value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name}_missing")
    return normalized


def _locator(field_name: str, value: object, *, allow_empty: bool = False) -> str:
    normalized = _text(field_name, value, allow_empty=allow_empty)
    if not normalized and allow_empty:
        return ""
    if _LOCATOR_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name}_locator_invalid")
    return normalized


def factory_role_evidence_authority_stream(factory_run_id: str) -> str:
    """Return the one durable cutoff stream identity for a Factory run."""

    normalized_run_id = _locator("factory_run_id", factory_run_id)
    run_hash = hashlib.sha256(normalized_run_id.encode("utf-8")).hexdigest()
    return f"{_AUTHORITY_STREAM_PREFIX}{run_hash}"


def _hash64(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    if len(value) != _HASH_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field_name}_invalid")
    return value


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}_type_invalid")
    if value < 0:
        raise ValueError(f"{field_name}_invalid")
    return value


def _positive_int(field_name: str, value: object) -> int:
    result = _non_negative_int(field_name, value)
    if result == 0:
        raise ValueError(f"{field_name}_invalid")
    return result


def _exact_mapping(record: object, expected_fields: frozenset[str], *, code: str) -> Mapping[str, Any]:
    if type(record) is not dict or any(type(key) is not str for key in record) or frozenset(record) != expected_fields:
        raise ValueError(code)
    return record


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceStageAuthorityV1:
    """Factory-owned immutable fence/stage claim captured by RunService."""

    factory_run_id: str
    stage: str
    workspace_fencing_token: int
    stage_claim_attempt: int
    stage_claim_nonce: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "factory_run_id", _locator("factory_run_id", self.factory_run_id))
        object.__setattr__(self, "stage", _locator("stage", self.stage))
        object.__setattr__(
            self,
            "workspace_fencing_token",
            _positive_int("workspace_fencing_token", self.workspace_fencing_token),
        )
        object.__setattr__(
            self,
            "stage_claim_attempt",
            _positive_int("stage_claim_attempt", self.stage_claim_attempt),
        )
        object.__setattr__(self, "stage_claim_nonce", _locator("stage_claim_nonce", self.stage_claim_nonce))


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceSourceHeadV1:
    """Scalar-only current head for one Factory-owned canonical source."""

    canonical_source_ref: str
    source_fact_schema: str
    source_fact_version: str
    source_head_fact_id: str
    source_head_sequence: int
    source_head_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "canonical_source_ref",
            _locator("canonical_source_ref", self.canonical_source_ref),
        )
        object.__setattr__(self, "source_fact_schema", _locator("source_fact_schema", self.source_fact_schema))
        object.__setattr__(self, "source_fact_version", _locator("source_fact_version", self.source_fact_version))
        sequence = _non_negative_int("source_head_sequence", self.source_head_sequence)
        object.__setattr__(self, "source_head_sequence", sequence)
        fact_id = _locator("source_head_fact_id", self.source_head_fact_id, allow_empty=True)
        if sequence == 0 and fact_id:
            raise ValueError("zero_source_head_fact_id_must_be_empty")
        if sequence > 0 and not fact_id:
            raise ValueError("source_head_fact_id_missing")
        object.__setattr__(self, "source_head_fact_id", fact_id)
        object.__setattr__(self, "source_head_hash", _hash64("source_head_hash", self.source_head_hash))

    def to_record(self) -> dict[str, object]:
        return {
            "canonical_source_ref": self.canonical_source_ref,
            "source_fact_schema": self.source_fact_schema,
            "source_fact_version": self.source_fact_version,
            "source_head_fact_id": self.source_head_fact_id,
            "source_head_sequence": self.source_head_sequence,
            "source_head_hash": self.source_head_hash,
        }

    @classmethod
    def from_record(cls, record: object) -> FactoryRoleEvidenceSourceHeadV1:
        value = _exact_mapping(
            record,
            frozenset(
                {
                    "canonical_source_ref",
                    "source_fact_schema",
                    "source_fact_version",
                    "source_head_fact_id",
                    "source_head_sequence",
                    "source_head_hash",
                }
            ),
            code="source_head_fields_mismatch",
        )
        return cls(**value)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceSourceItemV1:
    """One locator/hash-only item at or before its captured source head."""

    ref_kind: str
    canonical_ref: str
    canonical_hash: str
    source_fact_id: str
    source_fact_sequence: int
    source_fact_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref_kind", _locator("ref_kind", self.ref_kind))
        object.__setattr__(self, "canonical_ref", _locator("canonical_ref", self.canonical_ref))
        object.__setattr__(self, "canonical_hash", _hash64("canonical_hash", self.canonical_hash))
        object.__setattr__(self, "source_fact_id", _locator("source_fact_id", self.source_fact_id))
        object.__setattr__(
            self,
            "source_fact_sequence",
            _positive_int("source_fact_sequence", self.source_fact_sequence),
        )
        object.__setattr__(self, "source_fact_hash", _hash64("source_fact_hash", self.source_fact_hash))

    def to_record(self) -> dict[str, object]:
        return {
            "ref_kind": self.ref_kind,
            "canonical_ref": self.canonical_ref,
            "canonical_hash": self.canonical_hash,
            "source_fact_id": self.source_fact_id,
            "source_fact_sequence": self.source_fact_sequence,
            "source_fact_hash": self.source_fact_hash,
        }

    @classmethod
    def from_record(cls, record: object) -> FactoryRoleEvidenceSourceItemV1:
        value = _exact_mapping(
            record,
            frozenset(
                {
                    "ref_kind",
                    "canonical_ref",
                    "canonical_hash",
                    "source_fact_id",
                    "source_fact_sequence",
                    "source_fact_hash",
                }
            ),
            code="source_item_fields_mismatch",
        )
        return cls(**value)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceSourceSlotV1:
    """One exact policy slot with explicit present/absent state."""

    ref_kind: str
    state: str
    source_head: FactoryRoleEvidenceSourceHeadV1
    items: tuple[FactoryRoleEvidenceSourceItemV1, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref_kind", _locator("ref_kind", self.ref_kind))
        state = _text("state", self.state)
        if state not in {_PRESENT_STATE, _ABSENT_STATE}:
            raise ValueError("source_slot_state_invalid")
        object.__setattr__(self, "state", state)
        if type(self.source_head) is not FactoryRoleEvidenceSourceHeadV1:
            raise TypeError("source_head_type_invalid")
        FactoryRoleEvidenceSourceHeadV1.__post_init__(self.source_head)
        if type(self.items) is not tuple:
            raise TypeError("source_items_tuple_required")
        if len(self.items) > _MAX_SOURCE_ITEMS_PER_SLOT:
            raise ValueError("source_items_per_slot_limit_exceeded")
        if state == _PRESENT_STATE and not self.items:
            raise ValueError("present_slot_items_required")
        if state == _ABSENT_STATE and self.items:
            raise ValueError("absent_slot_items_forbidden")
        canonical_refs: set[str] = set()
        fact_locators: set[tuple[str, int]] = set()
        previous_sequence = 0
        for item in self.items:
            if type(item) is not FactoryRoleEvidenceSourceItemV1:
                raise TypeError("source_item_type_invalid")
            FactoryRoleEvidenceSourceItemV1.__post_init__(item)
            if item.ref_kind != self.ref_kind:
                raise ValueError("item_ref_kind_mismatch")
            if item.source_fact_sequence > self.source_head.source_head_sequence:
                raise ValueError("source_fact_sequence_exceeds_head")
            if item.canonical_ref in canonical_refs:
                raise ValueError("duplicate_canonical_ref")
            locator = (item.source_fact_id, item.source_fact_sequence)
            if locator in fact_locators:
                raise ValueError("duplicate_source_fact_locator")
            if item.source_fact_sequence <= previous_sequence:
                raise ValueError("source_item_sequence_not_strictly_increasing")
            if item.source_fact_sequence == self.source_head.source_head_sequence and (
                item.source_fact_id != self.source_head.source_head_fact_id
                or item.source_fact_hash != self.source_head.source_head_hash
            ):
                raise ValueError("source_head_item_locator_mismatch")
            canonical_refs.add(item.canonical_ref)
            fact_locators.add(locator)
            previous_sequence = item.source_fact_sequence

    def to_record(self) -> dict[str, object]:
        return {
            "ref_kind": self.ref_kind,
            "state": self.state,
            "source_head": self.source_head.to_record(),
            "items": [item.to_record() for item in self.items],
        }

    @classmethod
    def from_record(cls, record: object) -> FactoryRoleEvidenceSourceSlotV1:
        value = _exact_mapping(
            record,
            frozenset({"ref_kind", "state", "source_head", "items"}),
            code="source_slot_fields_mismatch",
        )
        raw_items = value.get("items")
        if type(raw_items) is not list:
            raise ValueError("source_items_list_required")
        return cls(
            ref_kind=value.get("ref_kind"),  # type: ignore[arg-type]
            state=value.get("state"),  # type: ignore[arg-type]
            source_head=FactoryRoleEvidenceSourceHeadV1.from_record(value.get("source_head")),
            items=tuple(FactoryRoleEvidenceSourceItemV1.from_record(item) for item in raw_items),
        )


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceResolvedCutV1:
    """Exact role policy source cut returned synchronously by Factory authority."""

    role: str
    policy_hash: str
    slots: tuple[FactoryRoleEvidenceSourceSlotV1, ...]
    schema_version: str = FACTORY_ROLE_EVIDENCE_SOURCE_CUT_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str:
            raise TypeError("schema_version_type_invalid")
        if self.schema_version != FACTORY_ROLE_EVIDENCE_SOURCE_CUT_SCHEMA:
            raise ValueError("source_cut_schema_mismatch")
        role = _locator("role", self.role)
        policy = role_final_request_policy(role)
        object.__setattr__(self, "role", role)
        policy_hash = _hash64("policy_hash", self.policy_hash)
        if policy_hash != policy.policy_hash:
            raise ValueError("source_cut_policy_hash_mismatch")
        object.__setattr__(self, "policy_hash", policy_hash)
        if type(self.slots) is not tuple:
            raise TypeError("source_cut_slots_tuple_required")
        for slot in self.slots:
            if type(slot) is not FactoryRoleEvidenceSourceSlotV1:
                raise TypeError("source_cut_slot_type_invalid")
            FactoryRoleEvidenceSourceSlotV1.__post_init__(slot)
        actual_order = tuple(slot.ref_kind for slot in self.slots)
        if actual_order != policy.slot_order:
            raise ValueError("source_cut_slot_order_mismatch")
        for ref_kind in policy.required_present_slots:
            slot = self.slots[policy.slot_order.index(ref_kind)]
            if slot.state != _PRESENT_STATE:
                raise ValueError(f"source_cut_required_slot_not_present:{ref_kind}")
        source_refs = tuple(slot.source_head.canonical_source_ref for slot in self.slots)
        if len(set(source_refs)) != len(source_refs):
            raise ValueError("source_cut_duplicate_source_ref")
        if sum(len(slot.items) for slot in self.slots) > _MAX_SOURCE_ITEMS_TOTAL:
            raise ValueError("source_cut_total_items_limit_exceeded")

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "policy_hash": self.policy_hash,
            "slots": [slot.to_record() for slot in self.slots],
        }

    @classmethod
    def from_record(cls, record: object) -> FactoryRoleEvidenceResolvedCutV1:
        value = _exact_mapping(
            record,
            frozenset({"schema_version", "role", "policy_hash", "slots"}),
            code="source_cut_fields_mismatch",
        )
        raw_slots = value.get("slots")
        if type(raw_slots) is not list:
            raise ValueError("source_cut_slots_list_required")
        return cls(
            schema_version=value.get("schema_version"),  # type: ignore[arg-type]
            role=value.get("role"),  # type: ignore[arg-type]
            policy_hash=value.get("policy_hash"),  # type: ignore[arg-type]
            slots=tuple(FactoryRoleEvidenceSourceSlotV1.from_record(slot) for slot in raw_slots),
        )


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceCutoffBodyV1:
    """Immutable locator-free cutoff body stored inside the authority event."""

    factory_run_id: str
    request: FactoryRoleEvidenceCutoffRequestV1
    authority: FactoryRoleEvidenceStageAuthorityV1
    resolved_source_cut: FactoryRoleEvidenceResolvedCutV1
    schema_version: str = FACTORY_ROLE_EVIDENCE_CUTOFF_BODY_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str:
            raise TypeError("cutoff_body_schema_type_invalid")
        if self.schema_version != FACTORY_ROLE_EVIDENCE_CUTOFF_BODY_SCHEMA:
            raise ValueError("cutoff_body_schema_mismatch")
        factory_run_id = _locator("factory_run_id", self.factory_run_id)
        object.__setattr__(self, "factory_run_id", factory_run_id)
        if type(self.request) is not FactoryRoleEvidenceCutoffRequestV1:
            raise TypeError("cutoff_request_type_invalid")
        FactoryRoleEvidenceCutoffRequestV1.__post_init__(self.request)
        if type(self.authority) is not FactoryRoleEvidenceStageAuthorityV1:
            raise TypeError("cutoff_stage_authority_type_invalid")
        FactoryRoleEvidenceStageAuthorityV1.__post_init__(self.authority)
        if factory_run_id != self.authority.factory_run_id:
            raise ValueError("cutoff_factory_run_authority_mismatch")
        if type(self.resolved_source_cut) is not FactoryRoleEvidenceResolvedCutV1:
            raise TypeError("resolved_source_cut_type_invalid")
        FactoryRoleEvidenceResolvedCutV1.__post_init__(self.resolved_source_cut)
        if self.resolved_source_cut.role != self.request.role:
            raise ValueError("source_cut_role_mismatch")

    def to_record(self) -> dict[str, object]:
        request = self.request
        authority = self.authority
        return {
            "schema_version": self.schema_version,
            "factory_run_id": self.factory_run_id,
            "run_id": request.run_id,
            "role": request.role,
            "turn_id": request.turn_id,
            "call_id": request.call_id,
            "request_freeze_id": request.request_freeze_id,
            "semantic_candidate_hash": request.semantic_candidate_hash,
            "attempt_budget": request.attempt_budget,
            "execution_authority_hash": request.execution_authority_hash,
            "candidate_refs": list(request.candidate_refs),
            "stage": authority.stage,
            "workspace_fencing_token": authority.workspace_fencing_token,
            "stage_claim_attempt": authority.stage_claim_attempt,
            "stage_claim_nonce": authority.stage_claim_nonce,
            "resolved_source_cut": self.resolved_source_cut.to_record(),
        }

    @classmethod
    def from_record(cls, record: object) -> FactoryRoleEvidenceCutoffBodyV1:
        fields = frozenset(
            {
                "schema_version",
                "factory_run_id",
                "run_id",
                "role",
                "turn_id",
                "call_id",
                "request_freeze_id",
                "semantic_candidate_hash",
                "attempt_budget",
                "execution_authority_hash",
                "candidate_refs",
                "stage",
                "workspace_fencing_token",
                "stage_claim_attempt",
                "stage_claim_nonce",
                "resolved_source_cut",
            }
        )
        value = _exact_mapping(record, fields, code="cutoff_body_fields_mismatch")
        raw_candidate_refs = value.get("candidate_refs")
        if type(raw_candidate_refs) is not list:
            raise ValueError("cutoff_body_candidate_refs_list_required")
        request = FactoryRoleEvidenceCutoffRequestV1(
            schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
            run_id=value.get("run_id"),  # type: ignore[arg-type]
            role=value.get("role"),  # type: ignore[arg-type]
            turn_id=value.get("turn_id"),  # type: ignore[arg-type]
            call_id=value.get("call_id"),  # type: ignore[arg-type]
            request_freeze_id=value.get("request_freeze_id"),  # type: ignore[arg-type]
            semantic_candidate_hash=value.get("semantic_candidate_hash"),  # type: ignore[arg-type]
            attempt_budget=value.get("attempt_budget"),  # type: ignore[arg-type]
            execution_authority_hash=value.get("execution_authority_hash"),  # type: ignore[arg-type]
            candidate_refs=tuple(raw_candidate_refs),  # type: ignore[arg-type]
        )
        authority = FactoryRoleEvidenceStageAuthorityV1(
            factory_run_id=value.get("factory_run_id"),  # type: ignore[arg-type]
            stage=value.get("stage"),  # type: ignore[arg-type]
            workspace_fencing_token=value.get("workspace_fencing_token"),  # type: ignore[arg-type]
            stage_claim_attempt=value.get("stage_claim_attempt"),  # type: ignore[arg-type]
            stage_claim_nonce=value.get("stage_claim_nonce"),  # type: ignore[arg-type]
        )
        created = cls(
            schema_version=value.get("schema_version"),  # type: ignore[arg-type]
            factory_run_id=authority.factory_run_id,
            request=request,
            authority=authority,
            resolved_source_cut=FactoryRoleEvidenceResolvedCutV1.from_record(value.get("resolved_source_cut")),
        )
        if created.to_record() != dict(value):
            raise ValueError("cutoff_body_not_canonical")
        return created


def _canonical_cutoff_body_bytes(record: Mapping[str, object]) -> bytes:
    """Return the exact bounded UTF-8 bytes fragmented into authority facts."""

    raw = canonical_role_final_request_json(dict(record)).encode("utf-8")
    if len(raw) > _MAX_CUTOFF_BODY_BYTES:
        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_body_too_large")
    return raw


def _request_authority_hash(
    request: FactoryRoleEvidenceCutoffRequestV1,
    authority: FactoryRoleEvidenceStageAuthorityV1,
) -> str:
    return canonical_role_final_request_hash(
        {
            "schema_version": "polaris.factory_role_evidence_request_authority.v1",
            "factory_run_id": authority.factory_run_id,
            "run_id": request.run_id,
            "role": request.role,
            "turn_id": request.turn_id,
            "call_id": request.call_id,
            "request_freeze_id": request.request_freeze_id,
            "semantic_candidate_hash": request.semantic_candidate_hash,
            "attempt_budget": request.attempt_budget,
            "execution_authority_hash": request.execution_authority_hash,
            "candidate_refs": list(request.candidate_refs),
            "stage": authority.stage,
            "workspace_fencing_token": authority.workspace_fencing_token,
            "stage_claim_attempt": authority.stage_claim_attempt,
            "stage_claim_nonce": authority.stage_claim_nonce,
        }
    )


def _encode_base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_base64url(value: object) -> bytes:
    if type(value) is not str or not value or "=" in value:
        raise ValueError("cutoff_fragment_data_invalid")
    padding = "=" * (-len(value) % 4)
    try:
        raw = base64.b64decode(f"{value}{padding}", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("cutoff_fragment_data_invalid") from exc
    if _encode_base64url(raw) != value:
        raise ValueError("cutoff_fragment_data_not_canonical")
    return raw


@dataclass(frozen=True, slots=True)
class _CutoffFragmentPayload:
    factory_run_id: str
    request_freeze_id: str
    request_authority_hash: str
    cutoff_body_hash: str
    index: int
    count: int
    raw: bytes
    chunk_hash: str

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_SCHEMA,
            "factory_run_id": self.factory_run_id,
            "request_freeze_id": self.request_freeze_id,
            "request_authority_hash": self.request_authority_hash,
            "cutoff_body_hash": self.cutoff_body_hash,
            "index": self.index,
            "count": self.count,
            "encoding": _FRAGMENT_ENCODING,
            "raw_byte_count": len(self.raw),
            "chunk_hash": self.chunk_hash,
            "data": _encode_base64url(self.raw),
        }

    @classmethod
    def from_record(cls, record: object) -> _CutoffFragmentPayload:
        value = _exact_mapping(
            record,
            frozenset(
                {
                    "schema_version",
                    "factory_run_id",
                    "request_freeze_id",
                    "request_authority_hash",
                    "cutoff_body_hash",
                    "index",
                    "count",
                    "encoding",
                    "raw_byte_count",
                    "chunk_hash",
                    "data",
                }
            ),
            code="cutoff_fragment_payload_fields_mismatch",
        )
        if value.get("schema_version") != FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_SCHEMA:
            raise ValueError("cutoff_fragment_schema_mismatch")
        if value.get("encoding") != _FRAGMENT_ENCODING:
            raise ValueError("cutoff_fragment_encoding_mismatch")
        index = _non_negative_int("cutoff_fragment_index", value.get("index"))
        count = _positive_int("cutoff_fragment_count", value.get("count"))
        if count > _MAX_CUTOFF_FRAGMENTS or index >= count:
            raise ValueError("cutoff_fragment_index_or_count_invalid")
        raw = _decode_base64url(value.get("data"))
        raw_byte_count = _positive_int("cutoff_fragment_raw_byte_count", value.get("raw_byte_count"))
        if raw_byte_count > _FRAGMENT_RAW_BYTES or len(raw) != raw_byte_count:
            raise ValueError("cutoff_fragment_raw_byte_count_mismatch")
        chunk_hash = _hash64("cutoff_fragment_chunk_hash", value.get("chunk_hash"))
        if hashlib.sha256(raw).hexdigest() != chunk_hash:
            raise ValueError("cutoff_fragment_chunk_hash_mismatch")
        created = cls(
            factory_run_id=_locator("factory_run_id", value.get("factory_run_id")),
            request_freeze_id=_locator("request_freeze_id", value.get("request_freeze_id")),
            request_authority_hash=_hash64(
                "request_authority_hash",
                value.get("request_authority_hash"),
            ),
            cutoff_body_hash=_hash64("cutoff_body_hash", value.get("cutoff_body_hash")),
            index=index,
            count=count,
            raw=raw,
            chunk_hash=chunk_hash,
        )
        if created.to_record() != dict(value):
            raise ValueError("cutoff_fragment_payload_not_canonical")
        return created


def _fragment_cutoff_body(
    body: FactoryRoleEvidenceCutoffBodyV1,
) -> tuple[bytes, str, tuple[_CutoffFragmentPayload, ...]]:
    raw = _canonical_cutoff_body_bytes(body.to_record())
    body_hash = hashlib.sha256(raw).hexdigest()
    chunks = tuple(raw[offset : offset + _FRAGMENT_RAW_BYTES] for offset in range(0, len(raw), _FRAGMENT_RAW_BYTES))
    if not chunks or len(chunks) > _MAX_CUTOFF_FRAGMENTS:
        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_fragment_count_invalid")
    request_hash = _request_authority_hash(body.request, body.authority)
    return (
        raw,
        body_hash,
        tuple(
            _CutoffFragmentPayload(
                factory_run_id=body.factory_run_id,
                request_freeze_id=body.request.request_freeze_id,
                request_authority_hash=request_hash,
                cutoff_body_hash=body_hash,
                index=index,
                count=len(chunks),
                raw=chunk,
                chunk_hash=hashlib.sha256(chunk).hexdigest(),
            )
            for index, chunk in enumerate(chunks)
        ),
    )


@dataclass(frozen=True, slots=True)
class _CutoffCommitManifest:
    factory_run_id: str
    request_freeze_id: str
    request_authority_hash: str
    cutoff_body_hash: str
    fragment_count: int
    cutoff_fragment_vector_hash: str

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_SCHEMA,
            "factory_run_id": self.factory_run_id,
            "request_freeze_id": self.request_freeze_id,
            "request_authority_hash": self.request_authority_hash,
            "cutoff_body_hash": self.cutoff_body_hash,
            "fragment_count": self.fragment_count,
            "cutoff_fragment_vector_hash": self.cutoff_fragment_vector_hash,
        }

    @classmethod
    def from_record(cls, record: object) -> _CutoffCommitManifest:
        value = _exact_mapping(
            record,
            frozenset(
                {
                    "schema_version",
                    "factory_run_id",
                    "request_freeze_id",
                    "request_authority_hash",
                    "cutoff_body_hash",
                    "fragment_count",
                    "cutoff_fragment_vector_hash",
                }
            ),
            code="cutoff_commit_payload_fields_mismatch",
        )
        if value.get("schema_version") != FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_SCHEMA:
            raise ValueError("cutoff_commit_schema_mismatch")
        fragment_count = _positive_int("cutoff_fragment_count", value.get("fragment_count"))
        if fragment_count > _MAX_CUTOFF_FRAGMENTS:
            raise ValueError("cutoff_fragment_count_invalid")
        created = cls(
            factory_run_id=_locator("factory_run_id", value.get("factory_run_id")),
            request_freeze_id=_locator("request_freeze_id", value.get("request_freeze_id")),
            request_authority_hash=_hash64(
                "request_authority_hash",
                value.get("request_authority_hash"),
            ),
            cutoff_body_hash=_hash64("cutoff_body_hash", value.get("cutoff_body_hash")),
            fragment_count=fragment_count,
            cutoff_fragment_vector_hash=_hash64(
                "cutoff_fragment_vector_hash",
                value.get("cutoff_fragment_vector_hash"),
            ),
        )
        if created.to_record() != dict(value):
            raise ValueError("cutoff_commit_payload_not_canonical")
        return created


class FactoryRoleEvidenceSourceAuthority(Protocol):
    """Synchronous Factory-owned source resolver boundary for A009B2."""

    def resolve_source_cut(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        authority: FactoryRoleEvidenceStageAuthorityV1,
        factory_run: FactoryRun,
    ) -> FactoryRoleEvidenceResolvedCutV1:
        """Capture canonical source facts and heads while the claim lock is held."""


class UnavailableFactoryRoleEvidenceSourceAuthority:
    """Production A009B1 default: no source authority until A009B2 exists."""

    def resolve_source_cut(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        authority: FactoryRoleEvidenceStageAuthorityV1,
        factory_run: FactoryRun,
    ) -> FactoryRoleEvidenceResolvedCutV1:
        del request, authority, factory_run
        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_authority_unavailable")


class FactoryRoleEvidenceFactStream(Protocol):
    def ensure(self, command: EnsureSegmentedFactLedgerCommandV1) -> SegmentedFactLedgerReadyV1: ...

    def query_events(self, query: QuerySegmentedFactEventsV1) -> SegmentedFactQueryResultV1: ...

    def query_head(self, query: QuerySegmentedFactLedgerHeadV1) -> SegmentedFactLedgerHeadV1: ...

    def append(self, command: AppendSegmentedFactEventCommandV1) -> SegmentedFactEventAppendedV1: ...


class _PublicFactoryRoleEvidenceFactStream:
    def ensure(self, command: EnsureSegmentedFactLedgerCommandV1) -> SegmentedFactLedgerReadyV1:
        return ensure_segmented_fact_ledger(command)

    def query_events(self, query: QuerySegmentedFactEventsV1) -> SegmentedFactQueryResultV1:
        return query_segmented_fact_events(query)

    def query_head(self, query: QuerySegmentedFactLedgerHeadV1) -> SegmentedFactLedgerHeadV1:
        return query_segmented_fact_ledger_head(query)

    def append(self, command: AppendSegmentedFactEventCommandV1) -> SegmentedFactEventAppendedV1:
        return append_segmented_fact_event(command)


@dataclass(frozen=True, slots=True)
class _StoredFragment:
    event_id: str
    sequence: int
    event_hash: str
    payload: _CutoffFragmentPayload


@dataclass(frozen=True, slots=True)
class _PartialCutoff:
    request_authority_hash: str
    body_hash: str
    fragment_count: int
    fragments: tuple[_StoredFragment, ...]
    body: FactoryRoleEvidenceCutoffBodyV1 | None
    fragment_vector_hash: str | None


@dataclass(frozen=True, slots=True)
class _StoredCutoff:
    event_id: str
    sequence: int
    event_hash: str
    body_hash: str
    body: FactoryRoleEvidenceCutoffBodyV1
    fragment_count: int
    fragment_vector_hash: str


@dataclass(frozen=True, slots=True)
class _AuthorityScan:
    stored: dict[str, _StoredCutoff]
    partial: dict[str, _PartialCutoff]
    captured_head: SegmentedFactLedgerHeadV1


def _fragment_vector_hash(fragments: tuple[_StoredFragment, ...]) -> str:
    return canonical_role_final_request_hash(
        [
            {
                "index": fragment.payload.index,
                "event_id": fragment.event_id,
                "global_seq": fragment.sequence,
                "event_hash": fragment.event_hash,
                "chunk_hash": fragment.payload.chunk_hash,
            }
            for fragment in fragments
        ]
    )


class FactoryRoleEvidenceAuthorityPort:
    """Factory-owned A009B1 implementation of the async cutoff port."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        authority: FactoryRoleEvidenceStageAuthorityV1,
        run_lock: asyncio.Lock,
        run_loader: Callable[[], Awaitable[FactoryRun | None]],
        admission: FactoryWorkspaceRunAdmission,
        source_authority: FactoryRoleEvidenceSourceAuthority,
        fact_stream: FactoryRoleEvidenceFactStream | None = None,
        physical_attempt_coordinator: FactoryPhysicalAttemptLiveControlPort,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if type(authority) is not FactoryRoleEvidenceStageAuthorityV1:
            raise TypeError("factory_role_evidence_stage_authority_exact_type_required")
        FactoryRoleEvidenceStageAuthorityV1.__post_init__(authority)
        self._authority = authority
        self._run_lock = run_lock
        self._owner_loop_guard = threading.Lock()
        try:
            self._owner_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._owner_loop = None
        self._run_loader = run_loader
        self._admission = admission
        self._source_authority = source_authority
        self._facts = fact_stream or _PublicFactoryRoleEvidenceFactStream()
        if type(physical_attempt_coordinator) is not FactoryPhysicalAttemptLiveControlPort:
            raise TypeError("factory_physical_attempt_control_port_exact_type_required")
        if physical_attempt_coordinator.factory_run_id != authority.factory_run_id:
            raise ValueError("factory_physical_attempt_factory_run_mismatch")
        self._physical_attempt_coordinator = physical_attempt_coordinator
        self._logical_stream = factory_role_evidence_authority_stream(authority.factory_run_id)
        self._grant_lock = threading.RLock()
        self._acquisition_condition = threading.Condition(self._grant_lock)
        self._grants: dict[str, _FactoryRoleEvidenceGrantState] = {}
        self._active_acquisitions = 0
        self._closed = False

    def _authority_owner_loop(self) -> asyncio.AbstractEventLoop:
        """Return the Factory loop that owns ``_run_lock``.

        Role attempts can execute in worker event loops, but the cutoff authority
        must remain serialized with the Factory run lifecycle.  Production
        captures that loop at construction.  Synchronous test setup has no owner
        loop to preserve, so its individual operations retain their calling loop.
        """

        current_loop = asyncio.get_running_loop()
        with self._owner_loop_guard:
            owner_loop = self._owner_loop
            if owner_loop is None:
                return current_loop
        if owner_loop.is_closed():
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_owner_loop_unavailable")
        return owner_loop

    async def _run_on_authority_owner_loop(
        self,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Execute a cutoff operation on the loop that owns the Factory lock."""

        current_loop = asyncio.get_running_loop()
        owner_loop = self._authority_owner_loop()
        if current_loop is owner_loop:
            return await operation()
        if not owner_loop.is_running():
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_owner_loop_unavailable")

        async def invoke() -> _T:
            return await operation()

        scheduled = asyncio.run_coroutine_threadsafe(invoke(), owner_loop)
        try:
            return await asyncio.wrap_future(scheduled)
        except asyncio.CancelledError:
            scheduled.cancel()
            raise

    def _stage_role_and_cap(self) -> tuple[str, int]:
        policy = _STAGE_ROLE_AND_GRANT_CAP.get(self._authority.stage)
        if policy is None:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_unsupported")
        return policy

    def _grant_hash(self, *, role: str, grant_nonce: str) -> str:
        authority = self._authority
        return canonical_role_final_request_hash(
            {
                "schema_version": FACTORY_ROLE_EVIDENCE_EXECUTION_AUTHORITY_SCHEMA,
                "factory_run_id": authority.factory_run_id,
                "stage": authority.stage,
                "workspace_fencing_token": authority.workspace_fencing_token,
                "stage_claim_attempt": authority.stage_claim_attempt,
                "stage_claim_nonce": authority.stage_claim_nonce,
                "role": role,
                "attempt_budget": FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
                "grant_nonce": grant_nonce,
            }
        )

    def mint_authority_binding(self, role: str) -> FactoryRoleEvidenceAuthorityBindingV1:
        """Mint one unique role-task grant under the immutable live-stage claim."""

        try:
            normalized_role = role_final_request_policy(role).role
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_invalid") from exc
        expected_role, grant_cap = self._stage_role_and_cap()
        if normalized_role != expected_role:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_mismatch")
        with self._grant_lock:
            if self._closed:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_closed")
            if len(self._grants) >= grant_cap:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_grant_cardinality_exceeded")
            for _attempt in range(8):
                grant_nonce = secrets.token_hex(16)
                execution_authority_hash = self._grant_hash(role=normalized_role, grant_nonce=grant_nonce)
                if execution_authority_hash not in self._grants:
                    break
            else:  # pragma: no cover - cryptographic collision fail-closed guard
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_identity_exhausted")
            self._grants[execution_authority_hash] = _FactoryRoleEvidenceGrantState(
                grant_nonce=grant_nonce,
                role=normalized_role,
                attempt_budget=FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
                execution_authority_hash=execution_authority_hash,
            )
            self._physical_attempt_coordinator.register_grant(
                FactoryPhysicalAttemptGrantViewV1(
                    schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
                    verification_scope="factory",
                    factory_run_id=self._authority.factory_run_id,
                    role=normalized_role,
                    stage=self._authority.stage,
                    workspace_fencing_token=self._authority.workspace_fencing_token,
                    stage_claim_attempt=self._authority.stage_claim_attempt,
                    stage_claim_nonce=self._authority.stage_claim_nonce,
                    execution_authority_hash=execution_authority_hash,
                    attempt_budget=FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
                )
            )
        return FactoryRoleEvidenceAuthorityBindingV1(
            schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
            verification_scope="factory",
            factory_run_id=self._authority.factory_run_id,
            role=normalized_role,
            cutoff_port=self,
            physical_attempt_control_port=self._physical_attempt_coordinator,
            attempt_budget=FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
            execution_authority_hash=execution_authority_hash,
        )

    def require_grant_capacity(self, role: str, count: int) -> None:
        """Preflight a complete stage-local fanout before any child is created."""

        try:
            normalized_role = role_final_request_policy(role).role
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_invalid") from exc
        if type(count) is not int or count < 0:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_capacity_count_invalid")
        expected_role, grant_cap = self._stage_role_and_cap()
        if normalized_role != expected_role:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_mismatch")
        with self._grant_lock:
            if self._closed:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_closed")
            if len(self._grants) + count > grant_cap:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_grant_cardinality_exceeded")

    def revoke_authority_binding(self, binding: FactoryRoleEvidenceAuthorityBindingV1) -> None:
        """Revoke one minted grant whose role-task creation never completed."""

        if type(binding) is not FactoryRoleEvidenceAuthorityBindingV1:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_binding_type_invalid")
        FactoryRoleEvidenceAuthorityBindingV1.__post_init__(binding)
        if binding.cutoff_port is not self or binding.factory_run_id != self._authority.factory_run_id:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_binding_owner_mismatch")
        with self._grant_lock:
            grant = self._grants.get(binding.execution_authority_hash)
            if grant is None:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_execution_authority_hash_mismatch")
            if binding.role != grant.role or binding.attempt_budget != grant.attempt_budget:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_binding_identity_mismatch")
            grant.revoked = True
            self._physical_attempt_coordinator.revoke_grant(binding.execution_authority_hash)

    def close_authority(self) -> None:
        """Publish closure, then wait until every registered acquisition drains."""

        with self._acquisition_condition:
            self._closed = True
            for grant in self._grants.values():
                grant.revoked = True
                self._physical_attempt_coordinator.close_grant(grant.execution_authority_hash)
            while self._active_acquisitions:
                self._acquisition_condition.wait()

    def _require_authorized_request_locked(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
        *,
        expected_role: str,
    ) -> None:
        if request.role != expected_role:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_mismatch")
        if self._closed:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_closed")
        grant = self._grants.get(request.execution_authority_hash)
        if grant is None:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_execution_authority_hash_mismatch")
        expected_hash = self._grant_hash(role=grant.role, grant_nonce=grant.grant_nonce)
        if expected_hash != request.execution_authority_hash or grant.execution_authority_hash != expected_hash:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_execution_authority_hash_mismatch")
        if grant.revoked:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_revoked")
        if request.role != grant.role:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_role_mismatch")
        if request.attempt_budget != grant.attempt_budget:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_attempt_budget_mismatch")

    def _begin_acquisition(self, request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        """Atomically validate authority and register a close-drained lease."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._acquisition_condition:
            self._require_authorized_request_locked(request, expected_role=expected_role)
            self._active_acquisitions += 1

    def _preflight_authorized_request(self, request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        """Reject invalid authority before awaiting run access or producing effects."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._grant_lock:
            self._require_authorized_request_locked(request, expected_role=expected_role)

    def _end_acquisition(self) -> None:
        with self._acquisition_condition:
            self._end_acquisition_locked()

    def _end_acquisition_locked(self) -> None:
        if self._active_acquisitions <= 0:  # pragma: no cover - internal invariant guard
            raise RuntimeError("factory_role_evidence_acquisition_lease_underflow")
        self._active_acquisitions -= 1
        if self._active_acquisitions == 0:
            self._acquisition_condition.notify_all()

    def _require_acquisition_live(self, request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        """Revalidate closure/revocation at every persistent-effect boundary."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._grant_lock:
            self._require_authorized_request_locked(request, expected_role=expected_role)

    def _append_authorized_commit(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        partial: _PartialCutoff,
        expected_sequence: int,
    ) -> SegmentedFactEventAppendedV1:
        """Linearize authority validation and the durable cutoff commit append."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._acquisition_condition:
            self._require_authorized_request_locked(request, expected_role=expected_role)
            return self._append_commit(partial=partial, expected_sequence=expected_sequence)

    def _publish_ack_and_end_acquisition(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        ack: FactoryRoleEvidenceCutoffAckV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        """Atomically authorize ACK publication and release its acquisition lease."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._acquisition_condition:
            self._require_authorized_request_locked(request, expected_role=expected_role)
            self._end_acquisition_locked()
            return ack

    def _bind_live_request_identity(self, request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        """Bind child run/freeze only after the stage claim has been revalidated."""

        with self._grant_lock:
            grant = self._grants.get(request.execution_authority_hash)
            if self._closed:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_closed")
            if grant is None or grant.revoked:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_revoked")
            if grant.controlled_child_run_id and grant.controlled_child_run_id != request.run_id:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_controlled_child_run_mismatch")
            if (
                request.request_freeze_id not in grant.request_freeze_ids
                and len(grant.request_freeze_ids) >= _MAX_REQUEST_FREEZES_PER_GRANT
            ):
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_request_freeze_cardinality_exceeded")
            if not grant.controlled_child_run_id:
                grant.controlled_child_run_id = request.run_id
            grant.request_freeze_ids.add(request.request_freeze_id)

    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        owner_loop = self._authority_owner_loop()
        if asyncio.get_running_loop() is not owner_loop:
            return await self._run_on_authority_owner_loop(lambda: self.acquire_cutoff(request))
        if type(request) is not FactoryRoleEvidenceCutoffRequestV1:
            raise TypeError("factory_role_evidence_cutoff_request_exact_type_required")
        FactoryRoleEvidenceCutoffRequestV1.__post_init__(request)
        self._preflight_authorized_request(request)
        async with self._run_lock:
            run = await self._run_loader()
            self._begin_acquisition(request)
            lease_active = True
            try:
                self._require_current_run(run)
                authority = self._authority
                with self._admission.hold_active_stage_claim(
                    authority.factory_run_id,
                    fencing_token=authority.workspace_fencing_token,
                    stage=authority.stage,
                    attempt=authority.stage_claim_attempt,
                    nonce=authority.stage_claim_nonce,
                ) as revalidate_claim:
                    self._bind_live_request_identity(request)
                    self._require_acquisition_live(request)
                    ready = self._ensure_ledger()
                    scan = self._scan_authority_events()
                    if ready.head != scan.captured_head:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_ledger_head_mismatch")
                    replay = scan.stored.get(request.request_freeze_id)
                    if replay is not None:
                        if not self._same_request_and_authority(replay.body, request):
                            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                        revalidate_claim()
                        self._require_acquisition_live(request)
                        ack = self._publish_ack_and_end_acquisition(
                            request=request,
                            ack=self._ack(replay),
                        )
                        lease_active = False
                        return ack

                    request_hash = _request_authority_hash(request, authority)
                    partial = scan.partial.get(request.request_freeze_id)
                    if partial is not None:
                        if partial.request_authority_hash != request_hash:
                            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                        if partial.body is None or partial.fragment_vector_hash is None:
                            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_partial_incomplete")
                        if not self._same_request_and_authority(partial.body, request):
                            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                        self._require_unchanged_head(scan.captured_head)
                        revalidate_claim()
                        self._require_acquisition_live(request)
                        commit = self._append_authorized_commit(
                            request=request,
                            partial=partial,
                            expected_sequence=scan.captured_head.next_expected_global_seq,
                        )
                        ack = self._strict_reread_ack(
                            request=request,
                            expected_body=partial.body,
                            expected_body_hash=partial.body_hash,
                            expected_fragment_count=partial.fragment_count,
                            expected_fragment_vector_hash=partial.fragment_vector_hash,
                            commit=commit,
                        )
                        revalidate_claim()
                        self._require_acquisition_live(request)
                        ack = self._publish_ack_and_end_acquisition(request=request, ack=ack)
                        lease_active = False
                        return ack

                    self._require_acquisition_live(request)
                    # Exit stage-claim flock BEFORE any await.  Holding the OS
                    # flock across await lets heartbeat renew (or another
                    # cutoff) block the same event-loop thread that must resume
                    # to release the claim — process-wide self-deadlock
                    # (R142 locks_lock_inode_wait / GET 30s / keepalive 1011).
                    frozen_run = cast(FactoryRun, run)
                    frozen_authority = authority
                    frozen_request_hash = request_hash

                # Stage claim released: resolve off-loop without admission flock.
                try:
                    resolved = await asyncio.to_thread(
                        self._source_authority.resolve_source_cut,
                        request=request,
                        authority=frozen_authority,
                        factory_run=frozen_run,
                    )
                except FactoryRoleEvidenceAuthorityError:
                    raise
                except Exception as exc:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_resolver_failed") from exc
                if type(resolved) is not FactoryRoleEvidenceResolvedCutV1:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_cut_type_invalid")
                try:
                    FactoryRoleEvidenceResolvedCutV1.__post_init__(resolved)
                except (TypeError, ValueError) as exc:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_cut_invalid") from exc
                if resolved.role != request.role:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_cut_role_mismatch")

                body = FactoryRoleEvidenceCutoffBodyV1(
                    factory_run_id=frozen_authority.factory_run_id,
                    request=request,
                    authority=frozen_authority,
                    resolved_source_cut=resolved,
                )
                _raw, body_hash, fragment_payloads = _fragment_cutoff_body(body)

                # Multi-fragment fsync appends under stage claim must not run on
                # the asyncio event loop: each append holds the segmented fact
                # stream lock for durability, and concurrent heartbeat/settlement
                # queries time out at the default 2s budget (R143/R144
                # factory_role_evidence_cutoff_append_failed).  Execute the whole
                # claim+write critical section off-loop on one worker thread.
                try:
                    ack = await asyncio.to_thread(
                        self._finalize_cutoff_after_resolve,
                        request=request,
                        frozen_authority=frozen_authority,
                        frozen_request_hash=frozen_request_hash,
                        body=body,
                        body_hash=body_hash,
                        fragment_payloads=fragment_payloads,
                    )
                except FactoryRoleEvidenceAuthorityError:
                    raise
                except Exception as exc:
                    # Preserve lease/admission conflicts (tests + live fail-closed
                    # paths expect the original conflict type, not a wrap into
                    # append_failed).
                    from polaris.cells.factory.pipeline.public.contracts import (
                        FactoryWorkspaceRunLeaseConflictError,
                    )

                    if isinstance(exc, FactoryWorkspaceRunLeaseConflictError):
                        raise
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_failed") from exc
                lease_active = False
                return ack
            finally:
                if lease_active:
                    self._end_acquisition()

    def _finalize_cutoff_after_resolve(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        frozen_authority: FactoryRoleEvidenceStageAuthorityV1,
        frozen_request_hash: str,
        body: FactoryRoleEvidenceCutoffBodyV1,
        body_hash: str,
        fragment_payloads: tuple[Any, ...],
    ) -> FactoryRoleEvidenceCutoffAckV1:
        """Claim + durable fragment/commit path; must run fully sync on one thread."""

        with self._admission.hold_active_stage_claim(
            frozen_authority.factory_run_id,
            fencing_token=frozen_authority.workspace_fencing_token,
            stage=frozen_authority.stage,
            attempt=frozen_authority.stage_claim_attempt,
            nonce=frozen_authority.stage_claim_nonce,
        ) as revalidate_claim:
            revalidate_claim()
            self._require_acquisition_live(request)
            rescan = self._scan_authority_events()
            replay_after = rescan.stored.get(request.request_freeze_id)
            if replay_after is not None:
                if not self._same_request_and_authority(replay_after.body, request):
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                revalidate_claim()
                self._require_acquisition_live(request)
                return self._publish_ack_and_end_acquisition(
                    request=request,
                    ack=self._ack(replay_after),
                )
            partial_after = rescan.partial.get(request.request_freeze_id)
            if partial_after is not None:
                if partial_after.request_authority_hash != frozen_request_hash:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                if partial_after.body is None or partial_after.fragment_vector_hash is None:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_partial_incomplete")
                if not self._same_request_and_authority(partial_after.body, request):
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                self._require_unchanged_head(rescan.captured_head)
                revalidate_claim()
                self._require_acquisition_live(request)
                commit = self._append_authorized_commit(
                    request=request,
                    partial=partial_after,
                    expected_sequence=rescan.captured_head.next_expected_global_seq,
                )
                ack = self._strict_reread_ack(
                    request=request,
                    expected_body=partial_after.body,
                    expected_body_hash=partial_after.body_hash,
                    expected_fragment_count=partial_after.fragment_count,
                    expected_fragment_vector_hash=partial_after.fragment_vector_hash,
                    commit=commit,
                )
                revalidate_claim()
                self._require_acquisition_live(request)
                return self._publish_ack_and_end_acquisition(request=request, ack=ack)

            self._require_unchanged_head(rescan.captured_head)
            expected_sequence = rescan.captured_head.next_expected_global_seq
            persisted_fragments: list[_StoredFragment] = []
            for fragment_payload in fragment_payloads:
                revalidate_claim()
                self._require_acquisition_live(request)
                appended = self._append_event(
                    event_type=FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE,
                    payload=fragment_payload.to_record(),
                    idempotency_key=(
                        f"role-evidence-cutoff:{request.request_freeze_id}:fragment:{fragment_payload.index}"
                    ),
                    expected_sequence=expected_sequence,
                )
                self._require_acquisition_live(request)
                persisted_fragments.append(
                    _StoredFragment(
                        event_id=appended.event_id,
                        sequence=appended.global_seq,
                        event_hash=appended.event_hash,
                        payload=fragment_payload,
                    )
                )
                expected_sequence += 1
            fragments = tuple(persisted_fragments)
            vector_hash = _fragment_vector_hash(fragments)
            partial = _PartialCutoff(
                request_authority_hash=frozen_request_hash,
                body_hash=body_hash,
                fragment_count=len(fragments),
                fragments=fragments,
                body=body,
                fragment_vector_hash=vector_hash,
            )
            revalidate_claim()
            self._require_acquisition_live(request)
            commit = self._append_authorized_commit(
                request=request,
                partial=partial,
                expected_sequence=expected_sequence,
            )
            ack = self._strict_reread_ack(
                request=request,
                expected_body=body,
                expected_body_hash=body_hash,
                expected_fragment_count=len(fragments),
                expected_fragment_vector_hash=vector_hash,
                commit=commit,
            )
            revalidate_claim()
            self._require_acquisition_live(request)
            return self._publish_ack_and_end_acquisition(request=request, ack=ack)

    async def resolve_cutoff_proof(
        self,
        ack: FactoryRoleEvidenceCutoffAckV1,
    ) -> FactoryRoleEvidenceCutoffProofV1:
        """Strictly re-read one committed ACK locator into a detached proof."""

        owner_loop = self._authority_owner_loop()
        if asyncio.get_running_loop() is not owner_loop:
            return await self._run_on_authority_owner_loop(lambda: self.resolve_cutoff_proof(ack))
        if type(ack) is not FactoryRoleEvidenceCutoffAckV1:
            raise TypeError("factory_role_evidence_cutoff_ack_exact_type_required")
        try:
            FactoryRoleEvidenceCutoffAckV1.__post_init__(ack)
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_proof_ack_invalid") from exc
        if ack.factory_run_id != self._authority.factory_run_id or ack.authority_stream != self._logical_stream:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_proof_ack_namespace_mismatch")
        async with self._run_lock:
            run = await self._run_loader()
            self._require_current_run(run)
            authority = self._authority
            with self._admission.hold_active_stage_claim(
                authority.factory_run_id,
                fencing_token=authority.workspace_fencing_token,
                stage=authority.stage,
                attempt=authority.stage_claim_attempt,
                nonce=authority.stage_claim_nonce,
            ) as revalidate_claim:
                reread = self._scan_authority_events()
                stored = reread.stored.get(ack.request_freeze_id)
                if stored is None:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_proof_not_found")
                derived_ack = self._ack(stored)
                if derived_ack != ack:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_proof_ack_mismatch")
                revalidate_claim()
                proof = self._proof_from_stored(stored, derived_ack)
                revalidate_claim()
                return proof

    @staticmethod
    def _proof_from_stored(
        stored: _StoredCutoff,
        ack: FactoryRoleEvidenceCutoffAckV1,
    ) -> FactoryRoleEvidenceCutoffProofV1:
        source_heads: list[PublicFactoryRoleEvidenceCutoffSourceHeadV1] = []
        policy_slots: list[RoleFinalRequestEvidenceSlotV1] = []
        for source_slot in stored.body.resolved_source_cut.slots:
            source_head = source_slot.source_head
            source_heads.append(
                PublicFactoryRoleEvidenceCutoffSourceHeadV1(
                    canonical_source_ref=source_head.canonical_source_ref,
                    source_fact_schema=source_head.source_fact_schema,
                    source_fact_version=source_head.source_fact_version,
                    source_head_fact_id=source_head.source_head_fact_id,
                    source_head_sequence=source_head.source_head_sequence,
                    source_head_hash=source_head.source_head_hash,
                )
            )
            anchors = tuple(
                RoleFinalRequestEvidenceAnchorV1.create(
                    ref_kind=item.ref_kind,
                    canonical_source_ref=source_head.canonical_source_ref,
                    canonical_ref=item.canonical_ref,
                    canonical_hash=item.canonical_hash,
                    source_fact_schema=source_head.source_fact_schema,
                    source_fact_version=source_head.source_fact_version,
                    factory_run_id=ack.factory_run_id,
                    run_id=ack.run_id,
                    role=ack.role,
                    request_freeze_id=ack.request_freeze_id,
                    cutoff_fact_id=ack.cutoff_fact_id,
                    cutoff_fact_sequence=ack.cutoff_fact_sequence,
                    cutoff_fact_hash=ack.cutoff_fact_hash,
                    source_fact_id=item.source_fact_id,
                    source_fact_sequence=item.source_fact_sequence,
                    source_fact_hash=item.source_fact_hash,
                    source_head_sequence=source_head.source_head_sequence,
                    source_head_hash=source_head.source_head_hash,
                    execution_authority_hash=ack.execution_authority_hash,
                )
                for item in source_slot.items
            )
            policy_slots.append(
                RoleFinalRequestEvidenceSlotV1.create(
                    ref_kind=source_slot.ref_kind,
                    state=source_slot.state,
                    canonical_source_ref=source_head.canonical_source_ref,
                    source_fact_schema=source_head.source_fact_schema,
                    source_fact_version=source_head.source_fact_version,
                    factory_run_id=ack.factory_run_id,
                    run_id=ack.run_id,
                    role=ack.role,
                    request_freeze_id=ack.request_freeze_id,
                    cutoff_fact_id=ack.cutoff_fact_id,
                    cutoff_fact_sequence=ack.cutoff_fact_sequence,
                    cutoff_fact_hash=ack.cutoff_fact_hash,
                    source_head_sequence=source_head.source_head_sequence,
                    source_head_hash=source_head.source_head_hash,
                    execution_authority_hash=ack.execution_authority_hash,
                    items=anchors,
                )
            )
        facts = RoleFinalRequestPolicyFactsV1.create(role=ack.role, slots=policy_slots)
        return FactoryRoleEvidenceCutoffProofV1.create(
            ack=ack,
            source_head_vector=tuple(source_heads),
            policy_facts=facts,
        )

    def _require_current_run(self, run: FactoryRun | None) -> None:
        authority = self._authority
        if type(run) is not FactoryRun:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_type_invalid")
        current_run = cast(FactoryRun, run)
        if type(current_run.id) is not str or current_run.id != authority.factory_run_id:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_missing_or_mismatched")
        if type(current_run.status) is not FactoryRunStatus:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_status_invalid")
        if current_run.status not in {FactoryRunStatus.RUNNING, FactoryRunStatus.RECOVERING}:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_status_invalid")
        metadata = current_run.metadata
        if type(metadata) is not dict or any(type(key) is not str for key in metadata):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_metadata_invalid")
        current_stage = metadata.get("current_stage")
        if type(current_stage) is not str or current_stage != authority.stage:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_stage_mismatch")
        if metadata.get("factory_stage_in_flight") is not True:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_not_in_flight")

    def _ensure_ledger(self) -> SegmentedFactLedgerReadyV1:
        try:
            ready = self._facts.ensure(
                EnsureSegmentedFactLedgerCommandV1(
                    workspace=str(self._workspace),
                    logical_stream=self._logical_stream,
                    maintenance_reason="factory_role_evidence_cutoff_authority",
                    retention="pinned_audit_no_delete",
                )
            )
        except Exception as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_ledger_unavailable") from exc
        if type(ready) is SegmentedFactLedgerReadyV1:
            try:
                SegmentedFactLedgerReadyV1.__post_init__(ready)
            except (TypeError, ValueError) as exc:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_ledger_corrupt") from exc
        if (
            type(ready) is not SegmentedFactLedgerReadyV1
            or ready.workspace != str(self._workspace)
            or ready.logical_stream != self._logical_stream
            or ready.retention != "pinned_audit_no_delete"
            or ready.storage_prefix != ready.head.storage_prefix
        ):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_ledger_corrupt")
        self._validate_head(ready.head)
        return ready

    def _validate_head(self, head: object) -> SegmentedFactLedgerHeadV1:
        if type(head) is not SegmentedFactLedgerHeadV1:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_type_invalid")
        validated_head = cast(SegmentedFactLedgerHeadV1, head)
        try:
            SegmentedFactLedgerHeadV1.__post_init__(validated_head)  # type: ignore[attr-defined]
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_invalid") from exc
        if (
            validated_head.workspace != str(self._workspace)
            or validated_head.logical_stream != self._logical_stream
            or validated_head.retention != "pinned_audit_no_delete"
        ):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_identity_mismatch")
        return validated_head

    def _scan_authority_events(self) -> _AuthorityScan:
        continuation: str | None = None
        seen_continuations: set[str] = set()
        events: list[dict[str, Any]] = []
        captured_head: SegmentedFactLedgerHeadV1 | None = None
        while True:
            try:
                result = self._facts.query_events(
                    QuerySegmentedFactEventsV1(
                        workspace=str(self._workspace),
                        logical_stream=self._logical_stream,
                        limit=511,
                        continuation=continuation,
                        strict_integrity=True,
                    )
                )
            except Exception as exc:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_strict_scan_failed") from exc
            if type(result) is SegmentedFactQueryResultV1:
                try:
                    SegmentedFactQueryResultV1.__post_init__(result)
                except (TypeError, ValueError) as exc:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_strict_scan_corrupt") from exc
            if (
                type(result) is not SegmentedFactQueryResultV1
                or result.workspace != str(self._workspace)
                or result.logical_stream != self._logical_stream
            ):
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_strict_scan_corrupt")
            self._validate_head(result.captured_head)
            if captured_head is None:
                captured_head = result.captured_head
            elif result.captured_head != captured_head:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_scan_head_drift")
            events.extend(result.events)
            continuation = result.continuation
            if continuation is None:
                break
            if continuation in seen_continuations:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_scan_continuation_cycle")
            seen_continuations.add(continuation)
        assert captured_head is not None
        if len(events) != captured_head.total_count:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_scan_count_mismatch")
        fragment_metadata: dict[str, tuple[str, str, int]] = {}
        fragment_groups: dict[str, dict[int, _StoredFragment]] = {}
        stored: dict[str, _StoredCutoff] = {}
        for expected_sequence, event in enumerate(events, start=1):
            try:
                event_type = event.get("event_type") if type(event) is dict else None
                if event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE:
                    fragment = self._parse_fragment_event(event, expected_sequence=expected_sequence)
                    freeze_id = fragment.payload.request_freeze_id
                    if freeze_id in stored:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_fragment_after_commit")
                    fragment_metadata_value = (
                        fragment.payload.request_authority_hash,
                        fragment.payload.cutoff_body_hash,
                        fragment.payload.count,
                    )
                    existing_metadata = fragment_metadata.setdefault(
                        freeze_id,
                        fragment_metadata_value,
                    )
                    if existing_metadata != fragment_metadata_value:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_fragment_group_conflict")
                    fragment_group = fragment_groups.setdefault(freeze_id, {})
                    if fragment.payload.index in fragment_group:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_duplicate_fragment")
                    fragment_group[fragment.payload.index] = fragment
                    continue
                if event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE:
                    manifest, event_id, sequence, event_hash = self._parse_commit_event(
                        event,
                        expected_sequence=expected_sequence,
                    )
                    freeze_id = manifest.request_freeze_id
                    if freeze_id in stored:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_duplicate_freeze")
                    commit_metadata = fragment_metadata.get(freeze_id)
                    commit_group = fragment_groups.get(freeze_id)
                    if commit_metadata is None or commit_group is None:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_commit_without_fragments")
                    commit_partial = self._build_partial(
                        metadata=commit_metadata,
                        indexed_fragments=commit_group,
                    )
                    if commit_partial.body is None or commit_partial.fragment_vector_hash is None:
                        raise FactoryRoleEvidenceAuthorityError(
                            "factory_role_evidence_cutoff_commit_fragments_incomplete"
                        )
                    if (
                        manifest.factory_run_id != self._authority.factory_run_id
                        or manifest.request_authority_hash != commit_partial.request_authority_hash
                        or manifest.cutoff_body_hash != commit_partial.body_hash
                        or manifest.fragment_count != commit_partial.fragment_count
                        or manifest.cutoff_fragment_vector_hash != commit_partial.fragment_vector_hash
                    ):
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_commit_manifest_mismatch")
                    stored[freeze_id] = _StoredCutoff(
                        event_id=event_id,
                        sequence=sequence,
                        event_hash=event_hash,
                        body_hash=commit_partial.body_hash,
                        body=commit_partial.body,
                        fragment_count=commit_partial.fragment_count,
                        fragment_vector_hash=commit_partial.fragment_vector_hash,
                    )
                    continue
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_type_mismatch")
            except FactoryRoleEvidenceAuthorityError:
                raise
            except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_malformed") from exc
        partial_cutoffs = {
            freeze_id: self._build_partial(
                metadata=metadata,
                indexed_fragments=fragment_groups[freeze_id],
            )
            for freeze_id, metadata in fragment_metadata.items()
            if freeze_id not in stored
        }
        return _AuthorityScan(stored=stored, partial=partial_cutoffs, captured_head=captured_head)

    def _parse_event_locator(
        self,
        event: object,
        *,
        expected_sequence: int,
        expected_event_type: str,
    ) -> tuple[Mapping[str, Any], str, int, str]:
        if type(event) is not dict:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_type_invalid")
        if event.get("logical_stream") != self._logical_stream:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_stream_mismatch")
        if event.get("event_type") != expected_event_type:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_type_mismatch")
        if event.get("source") != _AUTHORITY_SOURCE:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_source_mismatch")
        event_id = _locator("cutoff_event_id", event.get("event_id"))
        sequence = _positive_int("cutoff_event_sequence", event.get("global_seq"))
        if sequence != expected_sequence:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_sequence_mismatch")
        event_hash = _hash64("cutoff_event_hash", event.get("event_hash"))
        return event, event_id, sequence, event_hash

    def _parse_fragment_event(self, event: object, *, expected_sequence: int) -> _StoredFragment:
        value, event_id, sequence, event_hash = self._parse_event_locator(
            event,
            expected_sequence=expected_sequence,
            expected_event_type=FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE,
        )
        payload = _CutoffFragmentPayload.from_record(value.get("payload"))
        if payload.factory_run_id != self._authority.factory_run_id:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_factory_run_mismatch")
        expected_idempotency = f"role-evidence-cutoff:{payload.request_freeze_id}:fragment:{payload.index}"
        if value.get("idempotency_key") != expected_idempotency:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_idempotency_mismatch")
        return _StoredFragment(
            event_id=event_id,
            sequence=sequence,
            event_hash=event_hash,
            payload=payload,
        )

    def _parse_commit_event(
        self,
        event: object,
        *,
        expected_sequence: int,
    ) -> tuple[_CutoffCommitManifest, str, int, str]:
        value, event_id, sequence, event_hash = self._parse_event_locator(
            event,
            expected_sequence=expected_sequence,
            expected_event_type=FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE,
        )
        manifest = _CutoffCommitManifest.from_record(value.get("payload"))
        if value.get("idempotency_key") != f"role-evidence-cutoff:{manifest.request_freeze_id}":
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_idempotency_mismatch")
        return manifest, event_id, sequence, event_hash

    def _build_partial(
        self,
        *,
        metadata: tuple[str, str, int],
        indexed_fragments: Mapping[int, _StoredFragment],
    ) -> _PartialCutoff:
        request_hash, body_hash, fragment_count = metadata
        fragments = tuple(indexed_fragments[index] for index in sorted(indexed_fragments))
        complete = len(fragments) == fragment_count and tuple(
            fragment.payload.index for fragment in fragments
        ) == tuple(range(fragment_count))
        if not complete:
            return _PartialCutoff(
                request_authority_hash=request_hash,
                body_hash=body_hash,
                fragment_count=fragment_count,
                fragments=fragments,
                body=None,
                fragment_vector_hash=None,
            )
        raw = b"".join(fragment.payload.raw for fragment in fragments)
        if len(raw) > _MAX_CUTOFF_BODY_BYTES:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_body_too_large")
        if hashlib.sha256(raw).hexdigest() != body_hash:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_body_hash_mismatch")
        decoded = json.loads(raw.decode("utf-8"))
        if type(decoded) is not dict:
            raise ValueError("cutoff_body_mapping_required")
        canonical = _canonical_cutoff_body_bytes(decoded)
        if canonical != raw:
            raise ValueError("cutoff_body_bytes_not_canonical")
        body = FactoryRoleEvidenceCutoffBodyV1.from_record(decoded)
        if _canonical_cutoff_body_bytes(body.to_record()) != raw:
            raise ValueError("cutoff_body_roundtrip_mismatch")
        if body.factory_run_id != self._authority.factory_run_id:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_factory_run_mismatch")
        if _request_authority_hash(body.request, body.authority) != request_hash:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_request_authority_hash_mismatch")
        return _PartialCutoff(
            request_authority_hash=request_hash,
            body_hash=body_hash,
            fragment_count=fragment_count,
            fragments=fragments,
            body=body,
            fragment_vector_hash=_fragment_vector_hash(fragments),
        )

    def _require_unchanged_head(self, captured_head: SegmentedFactLedgerHeadV1) -> None:
        try:
            current_head = self._facts.query_head(
                QuerySegmentedFactLedgerHeadV1(
                    workspace=str(self._workspace),
                    logical_stream=self._logical_stream,
                )
            )
        except Exception as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_query_failed") from exc
        self._validate_head(current_head)
        if current_head != captured_head:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_drift")

    def _append_event(
        self,
        *,
        event_type: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        expected_sequence: int,
    ) -> SegmentedFactEventAppendedV1:
        try:
            appended = self._facts.append(
                AppendSegmentedFactEventCommandV1(
                    workspace=str(self._workspace),
                    logical_stream=self._logical_stream,
                    event_type=event_type,
                    source=_AUTHORITY_SOURCE,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    expected_global_seq=expected_sequence,
                    require_idempotency_replay=False,
                    durability="fsync",
                )
            )
        except Exception as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_failed") from exc
        if type(appended) is SegmentedFactEventAppendedV1:
            try:
                SegmentedFactEventAppendedV1.__post_init__(appended)
            except (TypeError, ValueError) as exc:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_corrupt") from exc
        if (
            type(appended) is not SegmentedFactEventAppendedV1
            or appended.workspace != str(self._workspace)
            or appended.logical_stream != self._logical_stream
            or appended.global_seq != expected_sequence
            or appended.segment_index < 0
            or appended.local_seq <= 0
        ):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_corrupt")
        try:
            _locator("cutoff_event_id", appended.event_id)
            _hash64("cutoff_event_hash", appended.event_hash)
            _text("cutoff_event_appended_at", appended.appended_at)
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_corrupt") from exc
        return appended

    def _append_commit(
        self,
        *,
        partial: _PartialCutoff,
        expected_sequence: int,
    ) -> SegmentedFactEventAppendedV1:
        if partial.body is None or partial.fragment_vector_hash is None:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_partial_incomplete")
        manifest = _CutoffCommitManifest(
            factory_run_id=partial.body.factory_run_id,
            request_freeze_id=partial.body.request.request_freeze_id,
            request_authority_hash=partial.request_authority_hash,
            cutoff_body_hash=partial.body_hash,
            fragment_count=partial.fragment_count,
            cutoff_fragment_vector_hash=partial.fragment_vector_hash,
        )
        return self._append_event(
            event_type=FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE,
            payload=manifest.to_record(),
            idempotency_key=f"role-evidence-cutoff:{manifest.request_freeze_id}",
            expected_sequence=expected_sequence,
        )

    def _strict_reread_ack(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        expected_body: FactoryRoleEvidenceCutoffBodyV1,
        expected_body_hash: str,
        expected_fragment_count: int,
        expected_fragment_vector_hash: str,
        commit: SegmentedFactEventAppendedV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        reread = self._scan_authority_events()
        persisted = reread.stored.get(request.request_freeze_id)
        if (
            persisted is None
            or persisted.event_id != commit.event_id
            or persisted.sequence != commit.global_seq
            or persisted.event_hash != commit.event_hash
            or persisted.body_hash != expected_body_hash
            or persisted.fragment_count != expected_fragment_count
            or persisted.fragment_vector_hash != expected_fragment_vector_hash
            or persisted.body.to_record() != expected_body.to_record()
        ):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_reread_corrupt")
        return self._ack(persisted)

    def _same_request_and_authority(
        self,
        body: FactoryRoleEvidenceCutoffBodyV1,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> bool:
        return (
            body.factory_run_id == self._authority.factory_run_id
            and body.authority == self._authority
            and body.request == request
        )

    def _ack(self, stored: _StoredCutoff) -> FactoryRoleEvidenceCutoffAckV1:
        request = stored.body.request
        return FactoryRoleEvidenceCutoffAckV1(
            schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
            factory_run_id=stored.body.factory_run_id,
            run_id=request.run_id,
            role=request.role,
            turn_id=request.turn_id,
            call_id=request.call_id,
            request_freeze_id=request.request_freeze_id,
            semantic_candidate_hash=request.semantic_candidate_hash,
            attempt_budget=request.attempt_budget,
            execution_authority_hash=request.execution_authority_hash,
            authority_stream=self._logical_stream,
            cutoff_fact_id=stored.event_id,
            cutoff_fact_sequence=stored.sequence,
            cutoff_fact_hash=stored.event_hash,
            cutoff_body_hash=stored.body_hash,
            cutoff_fragment_vector_hash=stored.fragment_vector_hash,
            cutoff_fragment_count=stored.fragment_count,
        )


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceReplayCutoffV1:
    """One detached committed cutoff fact; carries no live grant capability."""

    cutoff_fact_id: str
    cutoff_sequence: int
    cutoff_event_hash: str
    cutoff_body_hash: str
    cutoff_fragment_vector_hash: str
    cutoff_fragment_count: int
    body: FactoryRoleEvidenceCutoffBodyV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "cutoff_fact_id", _locator("cutoff_fact_id", self.cutoff_fact_id))
        object.__setattr__(self, "cutoff_sequence", _positive_int("cutoff_sequence", self.cutoff_sequence))
        object.__setattr__(self, "cutoff_event_hash", _hash64("cutoff_event_hash", self.cutoff_event_hash))
        object.__setattr__(self, "cutoff_body_hash", _hash64("cutoff_body_hash", self.cutoff_body_hash))
        object.__setattr__(
            self,
            "cutoff_fragment_vector_hash",
            _hash64("cutoff_fragment_vector_hash", self.cutoff_fragment_vector_hash),
        )
        object.__setattr__(
            self,
            "cutoff_fragment_count",
            _positive_int("cutoff_fragment_count", self.cutoff_fragment_count),
        )
        if type(self.body) is not FactoryRoleEvidenceCutoffBodyV1:
            raise TypeError("factory_role_evidence_cutoff_body_exact_type_required")
        FactoryRoleEvidenceCutoffBodyV1.__post_init__(self.body)


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceReplaySnapshotV1:
    """Strict authority-ledger snapshot at one immutable captured head."""

    workspace: str
    factory_run_id: str
    logical_stream: str
    captured_head: SegmentedFactLedgerHeadV1
    cutoffs: tuple[FactoryRoleEvidenceReplayCutoffV1, ...]

    def __post_init__(self) -> None:
        workspace = str(Path(self.workspace).resolve())
        object.__setattr__(self, "workspace", workspace)
        factory_run_id = _locator("factory_run_id", self.factory_run_id)
        object.__setattr__(self, "factory_run_id", factory_run_id)
        expected_stream = factory_role_evidence_authority_stream(factory_run_id)
        if self.logical_stream != expected_stream:
            raise ValueError("factory_role_evidence_replay_stream_mismatch")
        if type(self.captured_head) is not SegmentedFactLedgerHeadV1:
            raise TypeError("segmented_fact_ledger_head_exact_type_required")
        SegmentedFactLedgerHeadV1.__post_init__(self.captured_head)
        if self.captured_head.workspace != workspace or self.captured_head.logical_stream != self.logical_stream:
            raise ValueError("factory_role_evidence_replay_head_mismatch")
        if type(self.cutoffs) is not tuple or any(
            type(cutoff) is not FactoryRoleEvidenceReplayCutoffV1 for cutoff in self.cutoffs
        ):
            raise TypeError("factory_role_evidence_replay_cutoffs_exact_tuple_required")
        seen_freezes: set[str] = set()
        previous_sequence = 0
        for cutoff in self.cutoffs:
            FactoryRoleEvidenceReplayCutoffV1.__post_init__(cutoff)
            if cutoff.body.factory_run_id != factory_run_id:
                raise ValueError("factory_role_evidence_replay_factory_run_mismatch")
            freeze_id = cutoff.body.request.request_freeze_id
            if freeze_id in seen_freezes or cutoff.cutoff_sequence <= previous_sequence:
                raise ValueError("factory_role_evidence_replay_duplicate_or_regressing_cutoff")
            seen_freezes.add(freeze_id)
            previous_sequence = cutoff.cutoff_sequence


class _FactoryRoleEvidenceReplayScanReader(FactoryRoleEvidenceAuthorityPort):
    """Read-only reuse of the exact live cutoff ledger codec and validators."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        factory_run_id: str,
        fact_stream: FactoryRoleEvidenceFactStream,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._authority = FactoryRoleEvidenceStageAuthorityV1(
            factory_run_id=factory_run_id,
            stage="physical_attempt_replay_fence",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="replay-reader-no-live-claim",
        )
        self._facts = fact_stream
        self._logical_stream = factory_role_evidence_authority_stream(factory_run_id)


def query_factory_role_evidence_replay_snapshot(
    *,
    workspace: str | Path,
    factory_run_id: str,
    fact_stream: FactoryRoleEvidenceFactStream | None = None,
) -> FactoryRoleEvidenceReplaySnapshotV1:
    """Strictly read all committed cutoff facts without creating live authority."""

    normalized_run_id = _locator("factory_run_id", factory_run_id)
    reader = _FactoryRoleEvidenceReplayScanReader(
        workspace=workspace,
        factory_run_id=normalized_run_id,
        fact_stream=fact_stream or _PublicFactoryRoleEvidenceFactStream(),
    )
    scan = reader._scan_authority_events()
    if scan.partial:
        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_replay_partial_cutoff")
    cutoffs = tuple(
        FactoryRoleEvidenceReplayCutoffV1(
            cutoff_fact_id=stored.event_id,
            cutoff_sequence=stored.sequence,
            cutoff_event_hash=stored.event_hash,
            cutoff_body_hash=stored.body_hash,
            cutoff_fragment_vector_hash=stored.fragment_vector_hash,
            cutoff_fragment_count=stored.fragment_count,
            body=stored.body,
        )
        for stored in sorted(scan.stored.values(), key=lambda item: item.sequence)
    )
    return FactoryRoleEvidenceReplaySnapshotV1(
        workspace=str(Path(workspace).resolve()),
        factory_run_id=normalized_run_id,
        logical_stream=reader._logical_stream,
        captured_head=scan.captured_head,
        cutoffs=cutoffs,
    )


__all__ = [
    "FACTORY_ROLE_EVIDENCE_CUTOFF_BODY_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_SOURCE_CUT_SCHEMA",
    "FactoryRoleEvidenceAuthorityError",
    "FactoryRoleEvidenceAuthorityPort",
    "FactoryRoleEvidenceCutoffBodyV1",
    "FactoryRoleEvidenceReplayCutoffV1",
    "FactoryRoleEvidenceReplaySnapshotV1",
    "FactoryRoleEvidenceResolvedCutV1",
    "FactoryRoleEvidenceSourceAuthority",
    "FactoryRoleEvidenceSourceHeadV1",
    "FactoryRoleEvidenceSourceItemV1",
    "FactoryRoleEvidenceSourceSlotV1",
    "FactoryRoleEvidenceStageAuthorityV1",
    "UnavailableFactoryRoleEvidenceSourceAuthority",
    "factory_role_evidence_authority_stream",
    "query_factory_role_evidence_replay_snapshot",
]
