"""Runtime-private pre-cutoff authority and post-cutoff Factory proof seams.

A009B3-B3.2 connects the Factory-owned pre-cutoff runtime authority to the
matching durable cutoff proof, injects its detached policy facts, and freezes
the provider-visible semantic request.  This seam does not authorize physical
provider dispatch; B3.3 must qualify that later boundary independently.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeAlias

from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffProofV1,
    FactoryRoleEvidenceCutoffSourceHeadV1,
    bind_factory_role_evidence_authority,
    get_factory_role_evidence_authority_binding,
)
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestPolicyFactsV1,
    canonical_role_final_request_hash,
    role_final_request_policy,
)

FACTORY_ROLE_EVIDENCE_BINDING_SCHEMA = "polaris.factory_role_evidence_binding.v1"
_HASH_64_LENGTH = 64


def _is_hash_64(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == _HASH_64_LENGTH and all(char in "0123456789abcdef" for char in value)
    )


def _exact_identifier(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}_missing")
    return normalized


FactoryRoleEvidenceSourceHeadV1: TypeAlias = FactoryRoleEvidenceCutoffSourceHeadV1


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceBindingV1:
    """Future-stable typed cutoff ACK carrier; never reconstructed from mappings."""

    schema_version: str
    verification_scope: str
    factory_run_id: str
    run_id: str
    role: str
    turn_id: str
    call_id: str
    request_freeze_id: str
    signed_factory_binding_ref: str
    signed_factory_binding_hash: str
    cutoff_acknowledged: bool
    cutoff_fact_id: str
    cutoff_fact_sequence: int
    cutoff_fact_hash: str
    source_head_vector: tuple[FactoryRoleEvidenceSourceHeadV1, ...]
    source_head_vector_hash: str
    policy_facts: RoleFinalRequestPolicyFactsV1
    cutoff_proof: FactoryRoleEvidenceCutoffProofV1

    @classmethod
    def from_cutoff_proof(cls, proof: FactoryRoleEvidenceCutoffProofV1) -> FactoryRoleEvidenceBindingV1:
        if type(proof) is not FactoryRoleEvidenceCutoffProofV1:
            raise TypeError("cutoff_proof_exact_type_required")
        FactoryRoleEvidenceCutoffProofV1.__post_init__(proof)
        ack = proof.ack
        return cls(
            schema_version=FACTORY_ROLE_EVIDENCE_BINDING_SCHEMA,
            verification_scope="factory",
            factory_run_id=ack.factory_run_id,
            run_id=ack.run_id,
            role=ack.role,
            turn_id=ack.turn_id,
            call_id=ack.call_id,
            request_freeze_id=ack.request_freeze_id,
            signed_factory_binding_ref=proof.signed_factory_binding_ref,
            signed_factory_binding_hash=proof.signed_factory_binding_hash,
            cutoff_acknowledged=True,
            cutoff_fact_id=ack.cutoff_fact_id,
            cutoff_fact_sequence=ack.cutoff_fact_sequence,
            cutoff_fact_hash=ack.cutoff_fact_hash,
            source_head_vector=proof.source_head_vector,
            source_head_vector_hash=proof.source_head_vector_hash,
            policy_facts=proof.policy_facts,
            cutoff_proof=proof,
        )

    def validation_error(self, *, expected_role: str) -> str:
        """Return stable validation error, or empty string when structurally valid."""

        if type(self.cutoff_proof) is not FactoryRoleEvidenceCutoffProofV1:
            return "cutoff_proof_exact_type_required"
        try:
            FactoryRoleEvidenceCutoffProofV1.__post_init__(self.cutoff_proof)
        except (TypeError, ValueError):
            return "cutoff_proof_malformed"
        for field_name in (
            "schema_version",
            "verification_scope",
            "factory_run_id",
            "run_id",
            "role",
            "turn_id",
            "call_id",
            "request_freeze_id",
            "signed_factory_binding_ref",
            "signed_factory_binding_hash",
            "cutoff_fact_id",
            "cutoff_fact_hash",
            "source_head_vector_hash",
        ):
            if type(getattr(self, field_name)) is not str:
                return f"{field_name}_type_invalid"
        if type(expected_role) is not str:
            return "expected_role_type_invalid"
        proof = self.cutoff_proof
        ack = proof.ack
        projected_values = (
            (self.factory_run_id, ack.factory_run_id, "factory_run_id"),
            (self.run_id, ack.run_id, "run_id"),
            (self.role, ack.role, "role"),
            (self.turn_id, ack.turn_id, "turn_id"),
            (self.call_id, ack.call_id, "call_id"),
            (self.request_freeze_id, ack.request_freeze_id, "request_freeze_id"),
            (self.signed_factory_binding_ref, proof.signed_factory_binding_ref, "signed_factory_binding_ref"),
            (self.signed_factory_binding_hash, proof.signed_factory_binding_hash, "signed_factory_binding_hash"),
            (self.cutoff_fact_id, ack.cutoff_fact_id, "cutoff_fact_id"),
            (self.cutoff_fact_sequence, ack.cutoff_fact_sequence, "cutoff_fact_sequence"),
            (self.cutoff_fact_hash, ack.cutoff_fact_hash, "cutoff_fact_hash"),
            (self.source_head_vector, proof.source_head_vector, "source_head_vector"),
            (self.source_head_vector_hash, proof.source_head_vector_hash, "source_head_vector_hash"),
            (self.policy_facts, proof.policy_facts, "policy_facts"),
        )
        for actual, expected, field_name in projected_values:
            if actual != expected:
                return f"{field_name}_proof_projection_mismatch"
        if self.schema_version != FACTORY_ROLE_EVIDENCE_BINDING_SCHEMA:
            return "schema_version_mismatch"
        if self.verification_scope != "factory":
            return "verification_scope_mismatch"
        try:
            role_final_request_policy(self.role)
        except ValueError:
            return "unknown_role"
        if not self.factory_run_id.strip():
            return "factory_run_id_missing"
        if not self.run_id.strip():
            return "run_id_missing"
        if not self.turn_id.strip():
            return "turn_id_missing"
        if not self.call_id.strip():
            return "call_id_missing"
        if not self.request_freeze_id.strip():
            return "request_freeze_id_missing"
        if self.role != expected_role.strip():
            return "role_mismatch"
        if not self.signed_factory_binding_ref.strip():
            return "signed_factory_binding_ref_missing"
        if not _is_hash_64(self.signed_factory_binding_hash):
            return "signed_factory_binding_hash_invalid"
        if not isinstance(self.cutoff_acknowledged, bool):
            return "cutoff_acknowledged_type_invalid"
        if not self.cutoff_acknowledged:
            return "cutoff_not_acknowledged"
        if not self.cutoff_fact_id.strip():
            return "cutoff_fact_id_missing"
        if isinstance(self.cutoff_fact_sequence, bool) or not isinstance(self.cutoff_fact_sequence, int):
            return "cutoff_fact_sequence_invalid"
        if self.cutoff_fact_sequence <= 0:
            return "cutoff_fact_sequence_invalid"
        if not _is_hash_64(self.cutoff_fact_hash):
            return "cutoff_fact_hash_invalid"
        if not isinstance(self.source_head_vector, tuple) or not self.source_head_vector:
            return "source_head_vector_missing"
        for index, source_head in enumerate(self.source_head_vector):
            if type(source_head) is not FactoryRoleEvidenceSourceHeadV1:
                return f"source_head_vector_type_mismatch:{index}"
            try:
                FactoryRoleEvidenceSourceHeadV1.__post_init__(source_head)
            except (TypeError, ValueError):
                return f"source_head_vector_malformed:{index}"
        if not _is_hash_64(self.source_head_vector_hash):
            return "source_head_vector_hash_invalid"
        expected_vector_hash = canonical_role_final_request_hash(
            [source_head.to_record() for source_head in self.source_head_vector]
        )
        if self.source_head_vector_hash != expected_vector_hash:
            return "source_head_vector_hash_mismatch"
        facts = self.policy_facts
        if not isinstance(facts, RoleFinalRequestPolicyFactsV1):
            return "policy_facts_type_mismatch"
        if facts.role != self.role:
            return "policy_facts_role_mismatch"
        first = facts.slots[0]
        if first.factory_run_id != self.factory_run_id:
            return "policy_facts_factory_run_mismatch"
        if first.run_id != self.run_id:
            return "policy_facts_run_mismatch"
        if first.request_freeze_id != self.request_freeze_id:
            return "policy_facts_request_freeze_mismatch"
        if first.cutoff_fact_id != self.cutoff_fact_id:
            return "policy_facts_cutoff_fact_id_mismatch"
        if first.cutoff_fact_sequence != self.cutoff_fact_sequence:
            return "policy_facts_cutoff_fact_sequence_mismatch"
        if first.cutoff_fact_hash != self.cutoff_fact_hash:
            return "policy_facts_cutoff_fact_hash_mismatch"
        expected_heads = tuple(
            (
                slot.canonical_source_ref,
                slot.source_fact_schema,
                slot.source_fact_version,
                slot.source_head_sequence,
                slot.source_head_hash,
            )
            for slot in facts.slots
        )
        actual_heads = tuple(
            (
                source_head.canonical_source_ref,
                source_head.source_fact_schema,
                source_head.source_fact_version,
                source_head.source_head_sequence,
                source_head.source_head_hash,
            )
            for source_head in self.source_head_vector
        )
        if actual_heads != expected_heads:
            return "source_head_vector_policy_mismatch"
        return ""


_FACTORY_ROLE_EVIDENCE_BINDING: ContextVar[FactoryRoleEvidenceBindingV1 | None] = ContextVar(
    "factory_role_evidence_binding",
    default=None,
)


def get_factory_role_evidence_binding() -> FactoryRoleEvidenceBindingV1 | None:
    """Return current typed carrier without consulting request metadata/context."""

    return _FACTORY_ROLE_EVIDENCE_BINDING.get()


@contextmanager
def bind_factory_role_evidence(binding: FactoryRoleEvidenceBindingV1) -> Iterator[None]:
    """Bind carrier for one call and always restore prior context."""

    if type(binding) is not FactoryRoleEvidenceBindingV1:
        raise TypeError("factory_role_evidence_binding_exact_type_required")
    try:
        validation_error = FactoryRoleEvidenceBindingV1.validation_error(
            binding,
            expected_role=binding.role,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("factory_role_evidence_binding_malformed:unreadable") from exc
    if validation_error:
        raise RuntimeError(f"factory_role_evidence_binding_malformed:{validation_error}")
    token = _FACTORY_ROLE_EVIDENCE_BINDING.set(binding)
    try:
        yield
    finally:
        _FACTORY_ROLE_EVIDENCE_BINDING.reset(token)


__all__ = [
    "FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_BINDING_SCHEMA",
    "FactoryRoleEvidenceAuthorityBindingV1",
    "FactoryRoleEvidenceBindingV1",
    "FactoryRoleEvidenceSourceHeadV1",
    "bind_factory_role_evidence",
    "bind_factory_role_evidence_authority",
    "get_factory_role_evidence_authority_binding",
    "get_factory_role_evidence_binding",
]
