"""Typed role final-request evidence anchors, slots, and policy facts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.events.final_request_evidence._constants import (
    _EXACT_HASH_64_RE,
    _ROLE_FINAL_REQUEST_ANCHOR_FIELDS,
    _ROLE_FINAL_REQUEST_ANCHOR_STRING_FIELDS,
    _ROLE_FINAL_REQUEST_POLICY_FACTS_FIELDS,
    _ROLE_FINAL_REQUEST_POLICY_FACTS_STRING_FIELDS,
    _ROLE_FINAL_REQUEST_POLICY_PROMPT_FIELDS,
    _ROLE_FINAL_REQUEST_PROMPT_ANCHOR_FIELDS,
    _ROLE_FINAL_REQUEST_PROMPT_SLOT_FIELDS,
    _ROLE_FINAL_REQUEST_SLOT_FIELDS,
    _ROLE_FINAL_REQUEST_SLOT_STRING_FIELDS,
    _ROLE_FINAL_REQUEST_STATES,
    FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA,
    FINAL_REQUEST_EVIDENCE_PROMPT_ANCHOR_SCHEMA,
    ROLE_FINAL_REQUEST_EVIDENCE_PROMPT_SLOT_SCHEMA,
    ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
    ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA,
    ROLE_FINAL_REQUEST_POLICY_PROMPT_SCHEMA,
)
from polaris.kernelone.events.final_request_evidence._helpers import (
    _require_role_final_request_string,
)
from polaris.kernelone.events.final_request_evidence._policy import (
    canonical_role_final_request_json,
    role_final_request_policy,
)


@dataclass(frozen=True, slots=True)
class RoleFinalRequestEvidenceAnchorV1:
    """One immutable provider-visible item reference; never raw fact payload."""

    schema_version: str
    ref_kind: str
    canonical_source_ref: str
    canonical_ref: str
    canonical_hash: str
    source_fact_schema: str
    source_fact_version: str
    factory_run_id: str
    run_id: str
    role: str
    request_freeze_id: str
    cutoff_fact_id: str
    cutoff_fact_sequence: int
    cutoff_fact_hash: str
    source_fact_id: str
    source_fact_sequence: int
    source_fact_hash: str
    source_head_sequence: int
    source_head_hash: str
    execution_authority_hash: str

    def __post_init__(self) -> None:
        for field_name in _ROLE_FINAL_REQUEST_ANCHOR_STRING_FIELDS:
            _require_role_final_request_string(field_name, getattr(self, field_name))
        policy = role_final_request_policy(self.role)
        if self.ref_kind not in policy.slot_order:
            raise ValueError("role_final_request_anchor_ref_kind_mismatch")
        text_fields = (
            self.ref_kind,
            self.canonical_source_ref,
            self.canonical_ref,
            self.source_fact_schema,
            self.source_fact_version,
            self.factory_run_id,
            self.run_id,
            self.request_freeze_id,
            self.cutoff_fact_id,
            self.source_fact_id,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("role_final_request_anchor_empty_binding")
        if isinstance(self.cutoff_fact_sequence, bool) or self.cutoff_fact_sequence <= 0:
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        if isinstance(self.source_fact_sequence, bool) or not isinstance(self.source_fact_sequence, int):
            raise ValueError("source_fact_sequence_must_be_integer")
        if isinstance(self.source_head_sequence, bool) or not isinstance(self.source_head_sequence, int):
            raise ValueError("source_head_sequence_must_be_non_negative")
        if self.source_head_sequence < 0:
            raise ValueError("source_head_sequence_must_be_non_negative")
        if not _EXACT_HASH_64_RE.fullmatch(self.source_head_hash):
            raise ValueError("source_head_hash_must_be_64_lowercase_hex")
        if self.source_fact_sequence <= 0:
            raise ValueError("source_fact_sequence_must_be_positive")
        if self.source_fact_sequence > self.source_head_sequence:
            raise ValueError("source_fact_sequence_exceeds_head")
        for field_name, value in (
            ("canonical_hash", self.canonical_hash),
            ("cutoff_fact_hash", self.cutoff_fact_hash),
            ("source_fact_hash", self.source_fact_hash),
            ("source_head_hash", self.source_head_hash),
            ("execution_authority_hash", self.execution_authority_hash),
        ):
            if not _EXACT_HASH_64_RE.fullmatch(value):
                raise ValueError(f"{field_name}_must_be_64_lowercase_hex")
        if self.schema_version != FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA:
            raise ValueError("role_final_request_anchor_schema_mismatch")

    @classmethod
    def create(
        cls,
        *,
        ref_kind: str,
        canonical_source_ref: str,
        canonical_ref: str,
        canonical_hash: str,
        source_fact_schema: str,
        source_fact_version: str,
        factory_run_id: str,
        run_id: str,
        role: str,
        request_freeze_id: str,
        cutoff_fact_id: str,
        cutoff_fact_sequence: int,
        cutoff_fact_hash: str,
        source_fact_id: str,
        source_fact_sequence: int,
        source_fact_hash: str,
        source_head_sequence: int,
        source_head_hash: str,
        execution_authority_hash: str,
    ) -> RoleFinalRequestEvidenceAnchorV1:
        return cls(
            schema_version=FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA,
            ref_kind=_require_role_final_request_string("ref_kind", ref_kind).strip(),
            canonical_source_ref=_require_role_final_request_string(
                "canonical_source_ref", canonical_source_ref
            ).strip(),
            canonical_ref=_require_role_final_request_string("canonical_ref", canonical_ref).strip(),
            canonical_hash=_require_role_final_request_string("canonical_hash", canonical_hash).strip(),
            source_fact_schema=_require_role_final_request_string("source_fact_schema", source_fact_schema).strip(),
            source_fact_version=_require_role_final_request_string("source_fact_version", source_fact_version).strip(),
            factory_run_id=_require_role_final_request_string("factory_run_id", factory_run_id).strip(),
            run_id=_require_role_final_request_string("run_id", run_id).strip(),
            role=_require_role_final_request_string("role", role).strip(),
            request_freeze_id=_require_role_final_request_string("request_freeze_id", request_freeze_id).strip(),
            cutoff_fact_id=_require_role_final_request_string("cutoff_fact_id", cutoff_fact_id).strip(),
            cutoff_fact_sequence=cutoff_fact_sequence,
            cutoff_fact_hash=_require_role_final_request_string("cutoff_fact_hash", cutoff_fact_hash).strip(),
            source_fact_id=_require_role_final_request_string("source_fact_id", source_fact_id).strip(),
            source_fact_sequence=source_fact_sequence,
            source_fact_hash=_require_role_final_request_string("source_fact_hash", source_fact_hash).strip(),
            source_head_sequence=source_head_sequence,
            source_head_hash=_require_role_final_request_string("source_head_hash", source_head_hash).strip(),
            execution_authority_hash=_require_role_final_request_string(
                "execution_authority_hash", execution_authority_hash
            ).strip(),
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RoleFinalRequestEvidenceAnchorV1:
        if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_ANCHOR_FIELDS:
            raise ValueError("role_final_request_anchor_fields_mismatch")
        string_fields = {
            field_name: _require_role_final_request_string(field_name, record.get(field_name))
            for field_name in _ROLE_FINAL_REQUEST_ANCHOR_STRING_FIELDS
        }
        cutoff_fact_sequence = record.get("cutoff_fact_sequence")
        if isinstance(cutoff_fact_sequence, bool) or not isinstance(cutoff_fact_sequence, int):
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        source_fact_sequence = record.get("source_fact_sequence")
        if isinstance(source_fact_sequence, bool) or not isinstance(source_fact_sequence, int):
            raise ValueError("source_fact_sequence_must_be_integer")
        source_head_sequence = record.get("source_head_sequence")
        if isinstance(source_head_sequence, bool) or not isinstance(source_head_sequence, int):
            raise ValueError("source_head_sequence_must_be_non_negative")
        return cls(
            schema_version=string_fields["schema_version"],
            ref_kind=string_fields["ref_kind"],
            canonical_source_ref=string_fields["canonical_source_ref"],
            canonical_ref=string_fields["canonical_ref"],
            canonical_hash=string_fields["canonical_hash"],
            source_fact_schema=string_fields["source_fact_schema"],
            source_fact_version=string_fields["source_fact_version"],
            factory_run_id=string_fields["factory_run_id"],
            run_id=string_fields["run_id"],
            role=string_fields["role"],
            request_freeze_id=string_fields["request_freeze_id"],
            cutoff_fact_id=string_fields["cutoff_fact_id"],
            cutoff_fact_sequence=cutoff_fact_sequence,
            cutoff_fact_hash=string_fields["cutoff_fact_hash"],
            source_fact_id=string_fields["source_fact_id"],
            source_fact_sequence=source_fact_sequence,
            source_fact_hash=string_fields["source_fact_hash"],
            source_head_sequence=source_head_sequence,
            source_head_hash=string_fields["source_head_hash"],
            execution_authority_hash=string_fields["execution_authority_hash"],
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ref_kind": self.ref_kind,
            "canonical_source_ref": self.canonical_source_ref,
            "canonical_ref": self.canonical_ref,
            "canonical_hash": self.canonical_hash,
            "source_fact_schema": self.source_fact_schema,
            "source_fact_version": self.source_fact_version,
            "factory_run_id": self.factory_run_id,
            "run_id": self.run_id,
            "role": self.role,
            "request_freeze_id": self.request_freeze_id,
            "cutoff_fact_id": self.cutoff_fact_id,
            "cutoff_fact_sequence": self.cutoff_fact_sequence,
            "cutoff_fact_hash": self.cutoff_fact_hash,
            "source_fact_id": self.source_fact_id,
            "source_fact_sequence": self.source_fact_sequence,
            "source_fact_hash": self.source_fact_hash,
            "source_head_sequence": self.source_head_sequence,
            "source_head_hash": self.source_head_hash,
            "execution_authority_hash": self.execution_authority_hash,
        }


@dataclass(frozen=True, slots=True)
class RoleFinalRequestEvidenceSlotV1:
    """One policy slot at the Factory-issued source-head cut."""

    schema_version: str
    ref_kind: str
    state: str
    canonical_source_ref: str
    source_fact_schema: str
    source_fact_version: str
    factory_run_id: str
    run_id: str
    role: str
    request_freeze_id: str
    cutoff_fact_id: str
    cutoff_fact_sequence: int
    cutoff_fact_hash: str
    source_head_sequence: int
    source_head_hash: str
    execution_authority_hash: str
    items: tuple[RoleFinalRequestEvidenceAnchorV1, ...]

    def __post_init__(self) -> None:
        for field_name in _ROLE_FINAL_REQUEST_SLOT_STRING_FIELDS:
            _require_role_final_request_string(field_name, getattr(self, field_name))
        policy = role_final_request_policy(self.role)
        if self.schema_version != ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA:
            raise ValueError("role_final_request_slot_schema_mismatch")
        if self.ref_kind not in policy.slot_order:
            raise ValueError("role_final_request_slot_ref_kind_mismatch")
        text_fields = (
            self.ref_kind,
            self.canonical_source_ref,
            self.source_fact_schema,
            self.source_fact_version,
            self.factory_run_id,
            self.run_id,
            self.request_freeze_id,
            self.cutoff_fact_id,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("role_final_request_slot_empty_binding")
        if isinstance(self.cutoff_fact_sequence, bool) or not isinstance(self.cutoff_fact_sequence, int):
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        if self.cutoff_fact_sequence <= 0:
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        if isinstance(self.source_head_sequence, bool) or not isinstance(self.source_head_sequence, int):
            raise ValueError("source_head_sequence_must_be_non_negative")
        if self.source_head_sequence < 0:
            raise ValueError("source_head_sequence_must_be_non_negative")
        for field_name, value in (
            ("cutoff_fact_hash", self.cutoff_fact_hash),
            ("source_head_hash", self.source_head_hash),
            ("execution_authority_hash", self.execution_authority_hash),
        ):
            if not _EXACT_HASH_64_RE.fullmatch(value):
                raise ValueError(f"{field_name}_must_be_64_lowercase_hex")
        if self.state not in _ROLE_FINAL_REQUEST_STATES:
            raise ValueError("role_final_request_slot_invalid_state")
        if not isinstance(self.items, tuple):
            raise ValueError("role_final_request_slot_items_must_be_tuple")
        if any(not isinstance(item, RoleFinalRequestEvidenceAnchorV1) for item in self.items):
            raise ValueError("role_final_request_slot_items_must_be_typed_anchor")
        if self.state == "present" and not self.items:
            raise ValueError("present_slot_items_must_not_be_empty")
        if self.state == "absent_at_request_time" and self.items:
            raise ValueError("absent_slot_items_must_be_empty")
        expected_binding = (
            self.ref_kind,
            self.canonical_source_ref,
            self.source_fact_schema,
            self.source_fact_version,
            self.factory_run_id,
            self.run_id,
            self.role,
            self.request_freeze_id,
            self.cutoff_fact_id,
            self.cutoff_fact_sequence,
            self.cutoff_fact_hash,
            self.source_head_sequence,
            self.source_head_hash,
            self.execution_authority_hash,
        )
        for item in self.items:
            item_binding = (
                item.ref_kind,
                item.canonical_source_ref,
                item.source_fact_schema,
                item.source_fact_version,
                item.factory_run_id,
                item.run_id,
                item.role,
                item.request_freeze_id,
                item.cutoff_fact_id,
                item.cutoff_fact_sequence,
                item.cutoff_fact_hash,
                item.source_head_sequence,
                item.source_head_hash,
                item.execution_authority_hash,
            )
            if item_binding != expected_binding:
                raise ValueError("role_final_request_slot_item_binding_mismatch")
            if item.source_fact_sequence > self.source_head_sequence:
                raise ValueError("source_fact_sequence_exceeds_head")

    @classmethod
    def create(
        cls,
        *,
        ref_kind: str,
        state: str,
        canonical_source_ref: str,
        source_fact_schema: str,
        source_fact_version: str,
        factory_run_id: str,
        run_id: str,
        role: str,
        request_freeze_id: str,
        cutoff_fact_id: str,
        cutoff_fact_sequence: int,
        cutoff_fact_hash: str,
        source_head_sequence: int,
        source_head_hash: str,
        execution_authority_hash: str,
        items: tuple[RoleFinalRequestEvidenceAnchorV1, ...],
    ) -> RoleFinalRequestEvidenceSlotV1:
        return cls(
            schema_version=ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
            ref_kind=_require_role_final_request_string("ref_kind", ref_kind).strip(),
            state=_require_role_final_request_string("state", state).strip(),
            canonical_source_ref=_require_role_final_request_string(
                "canonical_source_ref", canonical_source_ref
            ).strip(),
            source_fact_schema=_require_role_final_request_string("source_fact_schema", source_fact_schema).strip(),
            source_fact_version=_require_role_final_request_string("source_fact_version", source_fact_version).strip(),
            factory_run_id=_require_role_final_request_string("factory_run_id", factory_run_id).strip(),
            run_id=_require_role_final_request_string("run_id", run_id).strip(),
            role=_require_role_final_request_string("role", role).strip(),
            request_freeze_id=_require_role_final_request_string("request_freeze_id", request_freeze_id).strip(),
            cutoff_fact_id=_require_role_final_request_string("cutoff_fact_id", cutoff_fact_id).strip(),
            cutoff_fact_sequence=cutoff_fact_sequence,
            cutoff_fact_hash=_require_role_final_request_string("cutoff_fact_hash", cutoff_fact_hash).strip(),
            source_head_sequence=source_head_sequence,
            source_head_hash=_require_role_final_request_string("source_head_hash", source_head_hash).strip(),
            execution_authority_hash=_require_role_final_request_string(
                "execution_authority_hash", execution_authority_hash
            ).strip(),
            items=items,
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RoleFinalRequestEvidenceSlotV1:
        if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_SLOT_FIELDS:
            raise ValueError("role_final_request_slot_fields_mismatch")
        string_fields = {
            field_name: _require_role_final_request_string(field_name, record.get(field_name))
            for field_name in _ROLE_FINAL_REQUEST_SLOT_STRING_FIELDS
        }
        raw_items = record.get("items")
        if not isinstance(raw_items, (list, tuple)):
            raise ValueError("role_final_request_slot_items_must_be_sequence")
        cutoff_fact_sequence = record.get("cutoff_fact_sequence")
        if isinstance(cutoff_fact_sequence, bool) or not isinstance(cutoff_fact_sequence, int):
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        source_head_sequence = record.get("source_head_sequence")
        if isinstance(source_head_sequence, bool) or not isinstance(source_head_sequence, int):
            raise ValueError("source_head_sequence_must_be_non_negative")
        return cls(
            schema_version=string_fields["schema_version"],
            ref_kind=string_fields["ref_kind"],
            state=string_fields["state"],
            canonical_source_ref=string_fields["canonical_source_ref"],
            source_fact_schema=string_fields["source_fact_schema"],
            source_fact_version=string_fields["source_fact_version"],
            factory_run_id=string_fields["factory_run_id"],
            run_id=string_fields["run_id"],
            role=string_fields["role"],
            request_freeze_id=string_fields["request_freeze_id"],
            cutoff_fact_id=string_fields["cutoff_fact_id"],
            cutoff_fact_sequence=cutoff_fact_sequence,
            cutoff_fact_hash=string_fields["cutoff_fact_hash"],
            source_head_sequence=source_head_sequence,
            source_head_hash=string_fields["source_head_hash"],
            execution_authority_hash=string_fields["execution_authority_hash"],
            items=tuple(RoleFinalRequestEvidenceAnchorV1.from_record(item) for item in raw_items),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ref_kind": self.ref_kind,
            "state": self.state,
            "canonical_source_ref": self.canonical_source_ref,
            "source_fact_schema": self.source_fact_schema,
            "source_fact_version": self.source_fact_version,
            "factory_run_id": self.factory_run_id,
            "run_id": self.run_id,
            "role": self.role,
            "request_freeze_id": self.request_freeze_id,
            "cutoff_fact_id": self.cutoff_fact_id,
            "cutoff_fact_sequence": self.cutoff_fact_sequence,
            "cutoff_fact_hash": self.cutoff_fact_hash,
            "source_head_sequence": self.source_head_sequence,
            "source_head_hash": self.source_head_hash,
            "execution_authority_hash": self.execution_authority_hash,
            "items": [item.to_record() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class RoleFinalRequestPolicyFactsV1:
    """Validated ordered role slots for one frozen provider request."""

    schema_version: str
    role: str
    slots: tuple[RoleFinalRequestEvidenceSlotV1, ...]

    def __post_init__(self) -> None:
        for field_name in _ROLE_FINAL_REQUEST_POLICY_FACTS_STRING_FIELDS:
            _require_role_final_request_string(field_name, getattr(self, field_name))
        policy = role_final_request_policy(self.role)
        if self.schema_version != ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA:
            raise ValueError("role_final_request_policy_facts_schema_mismatch")
        if not isinstance(self.slots, tuple) or any(
            not isinstance(slot, RoleFinalRequestEvidenceSlotV1) for slot in self.slots
        ):
            raise ValueError("role_final_request_policy_facts_slots_must_be_typed")
        kinds = tuple(slot.ref_kind for slot in self.slots)
        if kinds != policy.slot_order:
            raise ValueError("role_final_request_policy_facts_slot_order_mismatch")
        first = self.slots[0]
        for slot in self.slots:
            if slot.role != self.role:
                raise ValueError("role_final_request_policy_facts_role_mismatch")
            if (
                slot.factory_run_id != first.factory_run_id
                or slot.run_id != first.run_id
                or slot.request_freeze_id != first.request_freeze_id
                or slot.cutoff_fact_id != first.cutoff_fact_id
                or slot.cutoff_fact_sequence != first.cutoff_fact_sequence
                or slot.cutoff_fact_hash != first.cutoff_fact_hash
                or slot.execution_authority_hash != first.execution_authority_hash
            ):
                raise ValueError("role_final_request_policy_facts_binding_mismatch")
        required = frozenset(policy.required_present_slots)
        absent_required = [
            slot.ref_kind for slot in self.slots if slot.ref_kind in required and slot.state != "present"
        ]
        if absent_required:
            raise ValueError(f"role_final_request_policy_facts_required_slot_absent:{','.join(absent_required)}")

    @classmethod
    def create(
        cls,
        *,
        role: str,
        slots: Iterable[RoleFinalRequestEvidenceSlotV1],
    ) -> RoleFinalRequestPolicyFactsV1:
        return cls(
            schema_version=ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA,
            role=_require_role_final_request_string("role", role).strip(),
            slots=tuple(slots),
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RoleFinalRequestPolicyFactsV1:
        if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_POLICY_FACTS_FIELDS:
            raise ValueError("role_final_request_policy_facts_fields_mismatch")
        string_fields = {
            field_name: _require_role_final_request_string(field_name, record.get(field_name))
            for field_name in _ROLE_FINAL_REQUEST_POLICY_FACTS_STRING_FIELDS
        }
        raw_slots = record.get("slots")
        if not isinstance(raw_slots, (list, tuple)):
            raise ValueError("role_final_request_policy_facts_slots_must_be_sequence")
        return cls(
            schema_version=string_fields["schema_version"],
            role=string_fields["role"],
            slots=tuple(RoleFinalRequestEvidenceSlotV1.from_record(item) for item in raw_slots),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "slots": [slot.to_record() for slot in self.slots],
        }


def render_role_final_request_policy_facts(facts: RoleFinalRequestPolicyFactsV1) -> str:
    """Render a prompt-safe projection of validated authority facts.

    ``RoleFinalRequestPolicyFactsV1.to_record()`` is the durable control-plane
    authority record.  Provider prompts need only evidence meaning and immutable
    content references; attempt, cutoff, source-head, and execution-authority
    identities must remain outside the data plane.
    """

    if not isinstance(facts, RoleFinalRequestPolicyFactsV1):
        raise ValueError("role_final_request_policy_facts_typed_value_required")
    prompt_projection = {
        "schema_version": ROLE_FINAL_REQUEST_POLICY_PROMPT_SCHEMA,
        "role": facts.role,
        "slots": [
            {
                "schema_version": ROLE_FINAL_REQUEST_EVIDENCE_PROMPT_SLOT_SCHEMA,
                "ref_kind": slot.ref_kind,
                "state": slot.state,
                "canonical_source_ref": slot.canonical_source_ref,
                "source_fact_schema": slot.source_fact_schema,
                "source_fact_version": slot.source_fact_version,
                "items": [
                    {
                        "schema_version": FINAL_REQUEST_EVIDENCE_PROMPT_ANCHOR_SCHEMA,
                        "ref_kind": item.ref_kind,
                        "canonical_source_ref": item.canonical_source_ref,
                        "canonical_ref": item.canonical_ref,
                        "canonical_hash": item.canonical_hash,
                        "source_fact_schema": item.source_fact_schema,
                        "source_fact_version": item.source_fact_version,
                    }
                    for item in slot.items
                ],
            }
            for slot in facts.slots
        ],
    }
    return canonical_role_final_request_json(prompt_projection)


def validate_role_final_request_policy_prompt_projection(
    record: Mapping[str, Any],
    *,
    expected_role: str,
) -> None:
    """Validate provider-visible evidence meaning without recreating authority.

    The prompt projection is deliberately incapable of reconstructing cutoff,
    execution, or attempt authority.  Runtime authorization must come from the
    separately carried ``RoleFinalRequestPolicyFactsV1`` binding; this validator
    checks only the closed, prompt-safe schema and its semantic invariants.
    """

    if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_POLICY_PROMPT_FIELDS:
        raise ValueError("role_final_request_prompt_fields_mismatch")
    role = _require_role_final_request_string("role", record.get("role")).strip()
    expected = _require_role_final_request_string("expected_role", expected_role).strip()
    if role != expected:
        raise ValueError("role_final_request_prompt_role_mismatch")
    if record.get("schema_version") != ROLE_FINAL_REQUEST_POLICY_PROMPT_SCHEMA:
        raise ValueError("role_final_request_prompt_schema_mismatch")
    policy = role_final_request_policy(role)
    slots = record.get("slots")
    if not isinstance(slots, list) or len(slots) != len(policy.slot_order):
        raise ValueError("role_final_request_prompt_slot_order_mismatch")

    for expected_kind, slot in zip(policy.slot_order, slots, strict=True):
        if not isinstance(slot, Mapping) or frozenset(slot) != _ROLE_FINAL_REQUEST_PROMPT_SLOT_FIELDS:
            raise ValueError("role_final_request_prompt_slot_fields_mismatch")
        if slot.get("schema_version") != ROLE_FINAL_REQUEST_EVIDENCE_PROMPT_SLOT_SCHEMA:
            raise ValueError("role_final_request_prompt_slot_schema_mismatch")
        ref_kind = _require_role_final_request_string("ref_kind", slot.get("ref_kind")).strip()
        if ref_kind != expected_kind:
            raise ValueError("role_final_request_prompt_slot_order_mismatch")
        state = _require_role_final_request_string("state", slot.get("state")).strip()
        if state not in _ROLE_FINAL_REQUEST_STATES:
            raise ValueError("role_final_request_prompt_slot_state_invalid")
        canonical_source_ref = _require_role_final_request_string(
            "canonical_source_ref", slot.get("canonical_source_ref")
        ).strip()
        source_fact_schema = _require_role_final_request_string(
            "source_fact_schema", slot.get("source_fact_schema")
        ).strip()
        source_fact_version = _require_role_final_request_string(
            "source_fact_version", slot.get("source_fact_version")
        ).strip()
        if not canonical_source_ref or not source_fact_schema or not source_fact_version:
            raise ValueError("role_final_request_prompt_slot_empty_binding")
        items = slot.get("items")
        if not isinstance(items, list):
            raise ValueError("role_final_request_prompt_items_must_be_list")
        if state == "present" and not items:
            raise ValueError("role_final_request_prompt_present_items_missing")
        if state == "absent_at_request_time" and items:
            raise ValueError("role_final_request_prompt_absent_items_present")
        for item in items:
            if not isinstance(item, Mapping) or frozenset(item) != _ROLE_FINAL_REQUEST_PROMPT_ANCHOR_FIELDS:
                raise ValueError("role_final_request_prompt_anchor_fields_mismatch")
            if item.get("schema_version") != FINAL_REQUEST_EVIDENCE_PROMPT_ANCHOR_SCHEMA:
                raise ValueError("role_final_request_prompt_anchor_schema_mismatch")
            item_binding = (
                _require_role_final_request_string("ref_kind", item.get("ref_kind")).strip(),
                _require_role_final_request_string("canonical_source_ref", item.get("canonical_source_ref")).strip(),
                _require_role_final_request_string("source_fact_schema", item.get("source_fact_schema")).strip(),
                _require_role_final_request_string("source_fact_version", item.get("source_fact_version")).strip(),
            )
            if item_binding != (ref_kind, canonical_source_ref, source_fact_schema, source_fact_version):
                raise ValueError("role_final_request_prompt_anchor_binding_mismatch")
            canonical_ref = _require_role_final_request_string("canonical_ref", item.get("canonical_ref")).strip()
            canonical_hash = _require_role_final_request_string("canonical_hash", item.get("canonical_hash")).strip()
            if not canonical_ref or not _EXACT_HASH_64_RE.fullmatch(canonical_hash):
                raise ValueError("role_final_request_prompt_anchor_identity_invalid")
