"""Value objects and cutoff body/fragment codecs for role-evidence authority."""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
    FactoryRoleEvidenceCutoffRequestV1,
)
from polaris.kernelone.events.final_request_evidence import (
    canonical_role_final_request_hash,
    canonical_role_final_request_json,
    role_final_request_policy,
)

from ._constants import (
    _ABSENT_STATE,
    _FRAGMENT_ENCODING,
    _FRAGMENT_RAW_BYTES,
    _MAX_CUTOFF_BODY_BYTES,
    _MAX_CUTOFF_FRAGMENTS,
    _MAX_SOURCE_ITEMS_PER_SLOT,
    _MAX_SOURCE_ITEMS_TOTAL,
    _PRESENT_STATE,
    FACTORY_ROLE_EVIDENCE_CUTOFF_BODY_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_SCHEMA,
    FACTORY_ROLE_EVIDENCE_SOURCE_CUT_SCHEMA,
)
from ._primitives import (
    FactoryRoleEvidenceAuthorityError,
    _exact_mapping,
    _hash64,
    _locator,
    _non_negative_int,
    _positive_int,
    _text,
)


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
