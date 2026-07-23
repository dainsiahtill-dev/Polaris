"""Typed A009B1 seam for Factory-authorized final-request evidence cutoffs.

The request deliberately carries only semantic identity and non-authoritative
candidate-reference hints.  Source heads, slot states, policy facts, anchors,
and cutoff locators are produced exclusively by the Factory authority port.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Protocol, cast, runtime_checkable

from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FactoryPhysicalAttemptControlPort,
)
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestPolicyFactsV1,
    canonical_role_final_request_hash,
    render_role_final_request_policy_facts,
    role_final_request_policy,
    validate_role_final_request_policy_prompt_projection,
)

FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA = "polaris.factory_role_evidence_cutoff_request.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA = "polaris.factory_role_evidence_cutoff_ack.v1"
FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA = "polaris.factory_role_evidence_authority_binding.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_PROOF_SCHEMA = "polaris.factory_role_evidence_cutoff_proof.v1"
FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA = "polaris.factory_role_semantic_candidate.v1"
FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA = "polaris.factory_role_frozen_semantic_request.v1"

_HASH_LENGTH = 64
_AUTHORITY_STREAM_PREFIX = "factory.role_evidence_authority."
_CANDIDATE_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@#?=&%+\-]{0,255}\Z")
_MAX_CANDIDATE_REFS = 32
_MAX_CUTOFF_FRAGMENTS = 64
_UUID_HEX_RE = re.compile(r"[0-9a-f]{32}\Z")
_ROLE_IDENTITY_MARKER_PREFIX = "polaris.role_identity.v1:"
_EVIDENCE_BEGIN = "polaris.final_request_evidence.v1:begin"
_EVIDENCE_END = "polaris.final_request_evidence.v1:end"


def _identifier(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}_missing")
    return normalized


def _hash64(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    if len(value) != _HASH_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field_name}_invalid")
    return value


def _positive_int(field_name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}_type_invalid")
    if value <= 0:
        raise ValueError(f"{field_name}_invalid")
    return value


def _canonical_role(value: object) -> str:
    role = _identifier("role", value)
    role_final_request_policy(role)
    return role


def _candidate_refs(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError("candidate_refs_tuple_required")
    if len(value) > _MAX_CANDIDATE_REFS:
        raise ValueError("candidate_refs_too_many")
    normalized: list[str] = []
    for index, item in enumerate(value):
        candidate_ref = _identifier(f"candidate_refs_{index}", item)
        if _CANDIDATE_REF_PATTERN.fullmatch(candidate_ref) is None:
            raise ValueError(f"candidate_refs_{index}_locator_invalid")
        normalized.append(candidate_ref)
    if len(set(normalized)) != len(normalized):
        raise ValueError("candidate_refs_duplicate")
    return tuple(normalized)


def _strict_json_value(value: object, *, field_name: str, depth: int = 0) -> Any:
    if depth > 64:
        raise ValueError(f"{field_name}_json_depth_exceeded")
    if value is None or type(value) in {bool, str, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field_name}_non_finite")
        return value
    if type(value) is dict:
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{field_name}_non_string_key")
            normalized[key] = _strict_json_value(item, field_name=field_name, depth=depth + 1)
        return normalized
    if type(value) in {list, tuple}:
        sequence = cast("list[Any] | tuple[Any, ...]", value)
        return [_strict_json_value(item, field_name=field_name, depth=depth + 1) for item in sequence]
    raise TypeError(f"{field_name}_unsupported_json_type")


def _canonical_json(value: object, *, field_name: str) -> str:
    return json.dumps(
        _strict_json_value(value, field_name=field_name),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _identity_record(identity: FactoryRoleSemanticRequestIdentityV1) -> dict[str, str]:
    return {
        "run_id": identity.run_id,
        "turn_id": identity.turn_id,
        "call_id": identity.call_id,
        "request_freeze_id": identity.request_freeze_id,
    }


def _ack_record(ack: FactoryRoleEvidenceCutoffAckV1) -> dict[str, object]:
    return {field.name: getattr(ack, field.name) for field in fields(ack)}


def _recover_candidate_payload_from_frozen(
    payload: dict[str, Any],
    *,
    expected_policy_facts: RoleFinalRequestPolicyFactsV1 | None = None,
) -> dict[str, Any]:
    """Verify and strip one prompt-safe evidence suffix.

    The provider-visible JSON is data-plane context, never reconstructed
    control-plane authority.  Exact authority equality is checked only when
    the live typed binding supplies ``expected_policy_facts``.
    """

    messages = payload["messages"]
    if not messages or messages[0].get("role") != "system":
        raise ValueError("frozen_semantic_evidence_first_system_required")
    begin_count = 0
    end_count = 0
    for message in messages:
        content = message.get("content")
        if type(content) is not str:
            raise TypeError("frozen_semantic_message_fields_invalid")
        begin_count += content.count(_EVIDENCE_BEGIN)
        end_count += content.count(_EVIDENCE_END)
    if begin_count != 1 or end_count != 1:
        raise ValueError("frozen_semantic_evidence_unique_block_required")

    first_content = messages[0]["content"]
    separator = f"\n\n{_EVIDENCE_BEGIN}\n"
    suffix = f"\n{_EVIDENCE_END}"
    if first_content.count(separator) != 1 or not first_content.endswith(suffix):
        raise ValueError("frozen_semantic_evidence_canonical_suffix_required")
    candidate_system, rendered_facts = first_content.split(separator, 1)
    rendered_facts = rendered_facts[: -len(suffix)]
    if not rendered_facts or "\n" in rendered_facts:
        raise ValueError("frozen_semantic_evidence_canonical_json_required")
    try:
        facts_record = json.loads(rendered_facts)
    except (TypeError, ValueError) as exc:
        raise ValueError("frozen_semantic_evidence_policy_json_invalid") from exc
    if (
        type(facts_record) is not dict
        or _canonical_json(
            facts_record,
            field_name="frozen_semantic_evidence_policy",
        )
        != rendered_facts
    ):
        raise ValueError("frozen_semantic_evidence_policy_not_canonical")
    try:
        validate_role_final_request_policy_prompt_projection(
            facts_record,
            expected_role=payload["role"],
        )
    except (TypeError, ValueError) as exc:
        message = str(exc)
        if message == "role_final_request_prompt_role_mismatch":
            raise ValueError("frozen_semantic_evidence_policy_role_mismatch") from exc
        raise ValueError("frozen_semantic_evidence_policy_invalid") from exc
    if expected_policy_facts is not None:
        expected_rendered = render_role_final_request_policy_facts(expected_policy_facts)
        if rendered_facts != expected_rendered:
            raise ValueError("frozen_semantic_evidence_policy_binding_mismatch")

    expected_marker = f"{_ROLE_IDENTITY_MARKER_PREFIX}{payload['role']}"
    marker_lines = [
        line
        for message in messages
        for line in str(message["content"]).splitlines()
        if line.startswith(_ROLE_IDENTITY_MARKER_PREFIX)
    ]
    if marker_lines != [expected_marker] or expected_marker not in candidate_system.splitlines():
        raise ValueError("frozen_semantic_evidence_role_marker_mismatch")
    if candidate_system != expected_marker and not candidate_system.endswith(f"\n\n{expected_marker}"):
        raise ValueError("frozen_semantic_evidence_role_marker_not_terminal")

    candidate_messages = [dict(message) for message in messages]
    candidate_messages[0]["content"] = candidate_system
    return {
        "schema_version": FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA,
        "identity": payload["identity"],
        "role": payload["role"],
        "provider_id": payload["provider_id"],
        "model": payload["model"],
        "interaction_mode": payload["interaction_mode"],
        "capability_profile_id": payload["capability_profile_id"],
        "messages": candidate_messages,
        "tools": payload["tools"],
        "tool_choice": payload["tool_choice"],
        "response_format": payload["response_format"],
        "temperature": payload["temperature"],
        "max_tokens": payload["max_tokens"],
        "stream": payload["stream"],
        "required_tools": payload["required_tools"],
    }


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceAuthorityBindingV1:
    """Runtime-only pre-cutoff capability shared across the Cell boundary."""

    schema_version: str
    verification_scope: str
    factory_run_id: str
    role: str
    cutoff_port: FactoryRoleEvidenceCutoffPort
    physical_attempt_control_port: FactoryPhysicalAttemptControlPort
    attempt_budget: int
    execution_authority_hash: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str:
            raise TypeError("schema_version_type_invalid")
        if self.schema_version != FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA:
            raise ValueError("factory_role_evidence_authority_binding_schema_mismatch")
        if type(self.verification_scope) is not str:
            raise TypeError("verification_scope_type_invalid")
        if self.verification_scope != "factory":
            raise ValueError("verification_scope_mismatch")
        object.__setattr__(self, "factory_run_id", _identifier("factory_run_id", self.factory_run_id))
        object.__setattr__(self, "role", _canonical_role(self.role))
        if not isinstance(self.cutoff_port, FactoryRoleEvidenceCutoffPort):
            raise TypeError("factory_role_evidence_cutoff_port_required")
        if not isinstance(self.physical_attempt_control_port, FactoryPhysicalAttemptControlPort):
            raise TypeError("factory_physical_attempt_control_port_required")
        object.__setattr__(self, "attempt_budget", _positive_int("attempt_budget", self.attempt_budget))
        object.__setattr__(
            self,
            "execution_authority_hash",
            _hash64("execution_authority_hash", self.execution_authority_hash),
        )


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceCutoffRequestV1:
    """Hint-only request for one semantic request's bounded cutoff authority."""

    schema_version: str
    run_id: str
    role: str
    turn_id: str
    call_id: str
    request_freeze_id: str
    semantic_candidate_hash: str
    attempt_budget: int
    execution_authority_hash: str
    candidate_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str:
            raise TypeError("schema_version_type_invalid")
        if self.schema_version != FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA:
            raise ValueError("factory_role_evidence_cutoff_request_schema_mismatch")
        object.__setattr__(self, "run_id", _identifier("run_id", self.run_id))
        object.__setattr__(self, "role", _canonical_role(self.role))
        object.__setattr__(self, "turn_id", _identifier("turn_id", self.turn_id))
        object.__setattr__(self, "call_id", _identifier("call_id", self.call_id))
        object.__setattr__(self, "request_freeze_id", _identifier("request_freeze_id", self.request_freeze_id))
        object.__setattr__(
            self,
            "semantic_candidate_hash",
            _hash64("semantic_candidate_hash", self.semantic_candidate_hash),
        )
        object.__setattr__(self, "attempt_budget", _positive_int("attempt_budget", self.attempt_budget))
        object.__setattr__(
            self,
            "execution_authority_hash",
            _hash64("execution_authority_hash", self.execution_authority_hash),
        )
        object.__setattr__(self, "candidate_refs", _candidate_refs(self.candidate_refs))


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceCutoffAckV1:
    """Locator-only acknowledgement derived from a strictly re-read fact."""

    schema_version: str
    factory_run_id: str
    run_id: str
    role: str
    turn_id: str
    call_id: str
    request_freeze_id: str
    semantic_candidate_hash: str
    attempt_budget: int
    execution_authority_hash: str
    authority_stream: str
    cutoff_fact_id: str
    cutoff_fact_sequence: int
    cutoff_fact_hash: str
    cutoff_body_hash: str
    cutoff_fragment_vector_hash: str
    cutoff_fragment_count: int

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str:
            raise TypeError("schema_version_type_invalid")
        if self.schema_version != FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA:
            raise ValueError("factory_role_evidence_cutoff_ack_schema_mismatch")
        object.__setattr__(self, "factory_run_id", _identifier("factory_run_id", self.factory_run_id))
        object.__setattr__(self, "run_id", _identifier("run_id", self.run_id))
        object.__setattr__(self, "role", _canonical_role(self.role))
        object.__setattr__(self, "turn_id", _identifier("turn_id", self.turn_id))
        object.__setattr__(self, "call_id", _identifier("call_id", self.call_id))
        object.__setattr__(self, "request_freeze_id", _identifier("request_freeze_id", self.request_freeze_id))
        object.__setattr__(
            self,
            "semantic_candidate_hash",
            _hash64("semantic_candidate_hash", self.semantic_candidate_hash),
        )
        object.__setattr__(self, "attempt_budget", _positive_int("attempt_budget", self.attempt_budget))
        object.__setattr__(
            self,
            "execution_authority_hash",
            _hash64("execution_authority_hash", self.execution_authority_hash),
        )
        authority_stream = _identifier("authority_stream", self.authority_stream)
        expected_authority_stream = (
            f"{_AUTHORITY_STREAM_PREFIX}{hashlib.sha256(self.factory_run_id.encode('utf-8')).hexdigest()}"
        )
        if authority_stream != expected_authority_stream:
            raise ValueError("authority_stream_namespace_invalid")
        object.__setattr__(self, "authority_stream", authority_stream)
        object.__setattr__(self, "cutoff_fact_id", _identifier("cutoff_fact_id", self.cutoff_fact_id))
        object.__setattr__(
            self,
            "cutoff_fact_sequence",
            _positive_int("cutoff_fact_sequence", self.cutoff_fact_sequence),
        )
        object.__setattr__(self, "cutoff_fact_hash", _hash64("cutoff_fact_hash", self.cutoff_fact_hash))
        object.__setattr__(self, "cutoff_body_hash", _hash64("cutoff_body_hash", self.cutoff_body_hash))
        object.__setattr__(
            self,
            "cutoff_fragment_vector_hash",
            _hash64("cutoff_fragment_vector_hash", self.cutoff_fragment_vector_hash),
        )
        fragment_count = _positive_int("cutoff_fragment_count", self.cutoff_fragment_count)
        if fragment_count > _MAX_CUTOFF_FRAGMENTS:
            raise ValueError("cutoff_fragment_count_invalid")
        object.__setattr__(self, "cutoff_fragment_count", fragment_count)


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceCutoffSourceHeadV1:
    """One detached authority-captured source head in policy order."""

    canonical_source_ref: str
    source_fact_schema: str
    source_fact_version: str
    source_head_fact_id: str
    source_head_sequence: int
    source_head_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_source_ref", _identifier("canonical_source_ref", self.canonical_source_ref))
        object.__setattr__(self, "source_fact_schema", _identifier("source_fact_schema", self.source_fact_schema))
        object.__setattr__(self, "source_fact_version", _identifier("source_fact_version", self.source_fact_version))
        if type(self.source_head_sequence) is not int or self.source_head_sequence < 0:
            raise (
                TypeError("source_head_sequence_invalid")
                if type(self.source_head_sequence) is not int
                else ValueError("source_head_sequence_invalid")
            )
        if type(self.source_head_fact_id) is not str:
            raise TypeError("source_head_fact_id_type_invalid")
        head_id = self.source_head_fact_id.strip()
        if self.source_head_sequence > 0 and not head_id:
            raise ValueError("source_head_fact_id_missing")
        if self.source_head_sequence == 0 and head_id:
            raise ValueError("zero_source_head_fact_id_must_be_empty")
        object.__setattr__(self, "source_head_fact_id", head_id)
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

    def validation_error(self) -> str:
        try:
            type(self).__post_init__(self)
        except (TypeError, ValueError) as exc:
            return str(exc)
        return ""


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceCutoffProofV1:
    """Detached, canonical proof reconstructed from one committed cutoff."""

    schema_version: str
    ack: FactoryRoleEvidenceCutoffAckV1
    signed_factory_binding_ref: str
    signed_factory_binding_hash: str
    source_head_vector: tuple[FactoryRoleEvidenceCutoffSourceHeadV1, ...]
    source_head_vector_hash: str
    policy_facts: RoleFinalRequestPolicyFactsV1

    @classmethod
    def create(
        cls,
        *,
        ack: FactoryRoleEvidenceCutoffAckV1,
        source_head_vector: tuple[FactoryRoleEvidenceCutoffSourceHeadV1, ...],
        policy_facts: RoleFinalRequestPolicyFactsV1,
    ) -> FactoryRoleEvidenceCutoffProofV1:
        if type(ack) is not FactoryRoleEvidenceCutoffAckV1:
            raise TypeError("factory_role_evidence_cutoff_ack_exact_type_required")
        FactoryRoleEvidenceCutoffAckV1.__post_init__(ack)
        if type(source_head_vector) is not tuple or not source_head_vector:
            raise TypeError("source_head_vector_exact_tuple_required")
        if any(type(item) is not FactoryRoleEvidenceCutoffSourceHeadV1 for item in source_head_vector):
            raise TypeError("source_head_vector_exact_type_required")
        for item in source_head_vector:
            FactoryRoleEvidenceCutoffSourceHeadV1.__post_init__(item)
        if type(policy_facts) is not RoleFinalRequestPolicyFactsV1:
            raise TypeError("policy_facts_exact_type_required")
        validated_facts = RoleFinalRequestPolicyFactsV1.from_record(policy_facts.to_record())
        if validated_facts != policy_facts:
            raise ValueError("policy_facts_revalidation_mismatch")
        expected_heads = tuple(
            (
                slot.canonical_source_ref,
                slot.source_fact_schema,
                slot.source_fact_version,
                slot.source_head_sequence,
                slot.source_head_hash,
            )
            for slot in policy_facts.slots
        )
        actual_heads = tuple(
            (
                head.canonical_source_ref,
                head.source_fact_schema,
                head.source_fact_version,
                head.source_head_sequence,
                head.source_head_hash,
            )
            for head in source_head_vector
        )
        if actual_heads != expected_heads:
            raise ValueError("source_head_vector_policy_mismatch")
        first = policy_facts.slots[0]
        ack_binding = (
            ack.factory_run_id,
            ack.run_id,
            ack.role,
            ack.request_freeze_id,
            ack.cutoff_fact_id,
            ack.cutoff_fact_sequence,
            ack.cutoff_fact_hash,
            ack.execution_authority_hash,
        )
        facts_binding = (
            first.factory_run_id,
            first.run_id,
            policy_facts.role,
            first.request_freeze_id,
            first.cutoff_fact_id,
            first.cutoff_fact_sequence,
            first.cutoff_fact_hash,
            first.execution_authority_hash,
        )
        if facts_binding != ack_binding:
            raise ValueError("policy_facts_ack_binding_mismatch")
        vector_hash = canonical_role_final_request_hash([item.to_record() for item in source_head_vector])
        binding_ref = f"{ack.authority_stream}@{ack.cutoff_fact_sequence}#{ack.cutoff_fact_id}"
        payload = {
            "schema_version": FACTORY_ROLE_EVIDENCE_CUTOFF_PROOF_SCHEMA,
            "ack": _ack_record(ack),
            "signed_factory_binding_ref": binding_ref,
            "source_head_vector": [item.to_record() for item in source_head_vector],
            "source_head_vector_hash": vector_hash,
            "policy_facts": policy_facts.to_record(),
        }
        instance = object.__new__(cls)
        object.__setattr__(instance, "schema_version", FACTORY_ROLE_EVIDENCE_CUTOFF_PROOF_SCHEMA)
        object.__setattr__(instance, "ack", ack)
        object.__setattr__(instance, "signed_factory_binding_ref", binding_ref)
        object.__setattr__(instance, "signed_factory_binding_hash", canonical_role_final_request_hash(payload))
        object.__setattr__(instance, "source_head_vector", source_head_vector)
        object.__setattr__(instance, "source_head_vector_hash", vector_hash)
        object.__setattr__(instance, "policy_facts", policy_facts)
        return instance

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_ROLE_EVIDENCE_CUTOFF_PROOF_SCHEMA:
            raise ValueError("factory_role_evidence_cutoff_proof_schema_mismatch")
        rebuilt = type(self).create(
            ack=self.ack,
            source_head_vector=self.source_head_vector,
            policy_facts=self.policy_facts,
        )
        for field in fields(self):
            if getattr(self, field.name) != getattr(rebuilt, field.name):
                raise ValueError(f"factory_role_evidence_cutoff_proof_{field.name}_mismatch")


@dataclass(frozen=True, slots=True)
class FactoryRoleSemanticRequestIdentityV1:
    """Invoker-owned stable identity for one semantic preparation pass."""

    run_id: str
    turn_id: str
    call_id: str
    request_freeze_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier("run_id", self.run_id))
        object.__setattr__(self, "turn_id", _identifier("turn_id", self.turn_id))
        if not self.turn_id.startswith(f"{self.run_id}:turn:"):
            raise ValueError("turn_id_run_mismatch")
        raw_round = self.turn_id.removeprefix(f"{self.run_id}:turn:")
        if not raw_round.isdigit() or int(raw_round) < 0:
            raise ValueError("turn_id_round_invalid")
        for field_name in ("call_id", "request_freeze_id"):
            value = getattr(self, field_name)
            if type(value) is not str:
                raise TypeError(f"{field_name}_type_invalid")
            if _UUID_HEX_RE.fullmatch(value) is None:
                raise ValueError(f"{field_name}_uuid_hex_required")


@dataclass(frozen=True, slots=True)
class FactoryRoleSemanticCandidateV1:
    """Canonical pre-evidence semantic candidate; caller containers are discarded."""

    schema_version: str
    identity: FactoryRoleSemanticRequestIdentityV1
    canonical_payload_json: str
    semantic_candidate_hash: str
    candidate_refs: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        identity: FactoryRoleSemanticRequestIdentityV1,
        role: str,
        provider_id: str,
        model: str,
        interaction_mode: str,
        capability_profile: object,
        messages: object,
        tools: object,
        tool_choice: object,
        response_format: object,
        temperature: object,
        max_tokens: object,
        stream: object,
        required_tools: object = (),
    ) -> FactoryRoleSemanticCandidateV1:
        if type(identity) is not FactoryRoleSemanticRequestIdentityV1:
            raise TypeError("factory_role_semantic_identity_exact_type_required")
        FactoryRoleSemanticRequestIdentityV1.__post_init__(identity)
        canonical_profile = _strict_json_value(capability_profile, field_name="capability_profile")
        capability_profile_id = canonical_role_final_request_hash(canonical_profile)
        normalized_role = _canonical_role(role)
        payload = {
            "schema_version": FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA,
            "identity": _identity_record(identity),
            "role": normalized_role,
            "provider_id": _identifier("provider_id", provider_id),
            "model": _identifier("model", model),
            "interaction_mode": _identifier("interaction_mode", interaction_mode),
            "capability_profile_id": capability_profile_id,
            "messages": _strict_json_value(messages, field_name="messages"),
            "tools": _strict_json_value(tools, field_name="tools"),
            "tool_choice": _strict_json_value(tool_choice, field_name="tool_choice"),
            "response_format": _strict_json_value(response_format, field_name="response_format"),
            "temperature": _strict_json_value(temperature, field_name="temperature"),
            "max_tokens": _strict_json_value(max_tokens, field_name="max_tokens"),
            "stream": _strict_json_value(stream, field_name="stream"),
            "required_tools": _strict_json_value(
                list(required_tools) if type(required_tools) is tuple else required_tools,
                field_name="required_tools",
            ),
        }
        if type(payload["messages"]) is not list or type(payload["tools"]) is not list:
            raise TypeError("semantic_candidate_ordered_sequences_required")
        if type(payload["max_tokens"]) is not int or int(payload["max_tokens"]) <= 0:
            raise ValueError("semantic_candidate_max_tokens_invalid")
        if type(payload["stream"]) is not bool:
            raise TypeError("semantic_candidate_stream_bool_required")
        required_tool_names = payload["required_tools"]
        if (
            type(required_tool_names) is not list
            or any(type(item) is not str or not item.strip() for item in required_tool_names)
            or len(required_tool_names) != len(set(required_tool_names))
        ):
            raise TypeError("semantic_candidate_required_tools_invalid")
        canonical = _canonical_json(payload, field_name="semantic_candidate")
        return cls(
            schema_version=FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA,
            identity=identity,
            canonical_payload_json=canonical,
            semantic_candidate_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            candidate_refs=(),
        )

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA:
            raise ValueError("factory_role_semantic_candidate_schema_mismatch")
        if type(self.identity) is not FactoryRoleSemanticRequestIdentityV1:
            raise TypeError("factory_role_semantic_identity_exact_type_required")
        FactoryRoleSemanticRequestIdentityV1.__post_init__(self.identity)
        if type(self.canonical_payload_json) is not str:
            raise TypeError("canonical_payload_json_type_invalid")
        try:
            payload = json.loads(self.canonical_payload_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("canonical_payload_json_invalid") from exc
        if _canonical_json(payload, field_name="semantic_candidate") != self.canonical_payload_json:
            raise ValueError("canonical_payload_json_not_canonical")
        expected_keys = {
            "schema_version",
            "identity",
            "role",
            "provider_id",
            "model",
            "interaction_mode",
            "capability_profile_id",
            "messages",
            "tools",
            "tool_choice",
            "response_format",
            "temperature",
            "max_tokens",
            "stream",
            "required_tools",
        }
        if type(payload) is not dict or set(payload) != expected_keys:
            raise ValueError("semantic_candidate_payload_closed_set_required")
        if payload["schema_version"] != FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA:
            raise ValueError("semantic_candidate_payload_schema_mismatch")
        if payload.get("identity") != _identity_record(self.identity):
            raise ValueError("semantic_candidate_identity_mismatch")
        if _canonical_role(payload["role"]) != payload["role"]:
            raise ValueError("semantic_candidate_role_not_canonical")
        for field_name in ("provider_id", "model", "interaction_mode"):
            if _identifier(field_name, payload[field_name]) != payload[field_name]:
                raise ValueError(f"semantic_candidate_{field_name}_not_canonical")
        _hash64("capability_profile_id", payload["capability_profile_id"])
        messages = payload["messages"]
        tools = payload["tools"]
        if type(messages) is not list or any(type(item) is not dict for item in messages):
            raise TypeError("semantic_candidate_messages_invalid")
        for message in messages:
            if type(message.get("role")) is not str or type(message.get("content")) is not str:
                raise TypeError("semantic_candidate_message_fields_invalid")
        if type(tools) is not list or any(type(item) is not dict for item in tools):
            raise TypeError("semantic_candidate_tools_invalid")
        if payload["tool_choice"] is not None and type(payload["tool_choice"]) not in {str, dict}:
            raise TypeError("semantic_candidate_tool_choice_invalid")
        if payload["response_format"] is not None and type(payload["response_format"]) is not dict:
            raise TypeError("semantic_candidate_response_format_invalid")
        if type(payload["temperature"]) not in {int, float}:
            raise TypeError("semantic_candidate_temperature_invalid")
        if type(payload["max_tokens"]) is not int or payload["max_tokens"] <= 0:
            raise ValueError("semantic_candidate_max_tokens_invalid")
        if type(payload["stream"]) is not bool:
            raise TypeError("semantic_candidate_stream_bool_required")
        required_tool_names = payload["required_tools"]
        if (
            type(required_tool_names) is not list
            or any(type(item) is not str or not item.strip() for item in required_tool_names)
            or len(required_tool_names) != len(set(required_tool_names))
        ):
            raise TypeError("semantic_candidate_required_tools_invalid")
        _hash64("semantic_candidate_hash", self.semantic_candidate_hash)
        if hashlib.sha256(self.canonical_payload_json.encode("utf-8")).hexdigest() != self.semantic_candidate_hash:
            raise ValueError("semantic_candidate_hash_mismatch")
        if type(self.candidate_refs) is not tuple or self.candidate_refs:
            raise ValueError("semantic_candidate_refs_must_be_empty")


@dataclass(frozen=True, slots=True)
class FactoryRoleFrozenSemanticRequestV1:
    """Immutable post-evidence request authority for later dispatch drift checks."""

    schema_version: str
    identity: FactoryRoleSemanticRequestIdentityV1
    semantic_candidate_hash: str
    signed_factory_binding_ref: str
    signed_factory_binding_hash: str
    canonical_final_payload_json: str
    final_semantic_request_hash: str

    @classmethod
    def create(
        cls,
        *,
        candidate: FactoryRoleSemanticCandidateV1,
        signed_factory_binding_ref: str,
        signed_factory_binding_hash: str,
        messages: object,
        tools: object,
        tool_choice: object,
        response_format: object,
        temperature: object,
        max_tokens: object,
        stream: object,
    ) -> FactoryRoleFrozenSemanticRequestV1:
        if type(candidate) is not FactoryRoleSemanticCandidateV1:
            raise TypeError("factory_role_semantic_candidate_exact_type_required")
        FactoryRoleSemanticCandidateV1.__post_init__(candidate)
        candidate_payload = json.loads(candidate.canonical_payload_json)
        final_semantic_values = {
            "tools": _strict_json_value(tools, field_name="tools"),
            "tool_choice": _strict_json_value(tool_choice, field_name="tool_choice"),
            "response_format": _strict_json_value(response_format, field_name="response_format"),
            "temperature": _strict_json_value(temperature, field_name="temperature"),
            "max_tokens": _strict_json_value(max_tokens, field_name="max_tokens"),
            "stream": _strict_json_value(stream, field_name="stream"),
            "required_tools": candidate_payload["required_tools"],
        }
        for field_name, value in final_semantic_values.items():
            if value != candidate_payload[field_name]:
                raise ValueError(f"factory_role_semantic_request_drift:{field_name}")
        payload = {
            "schema_version": FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA,
            "identity": candidate_payload["identity"],
            "role": candidate_payload["role"],
            "provider_id": candidate_payload["provider_id"],
            "model": candidate_payload["model"],
            "interaction_mode": candidate_payload["interaction_mode"],
            "capability_profile_id": candidate_payload["capability_profile_id"],
            "messages": _strict_json_value(messages, field_name="messages"),
            **final_semantic_values,
            "semantic_candidate_hash": candidate.semantic_candidate_hash,
            "signed_factory_binding_ref": _identifier("signed_factory_binding_ref", signed_factory_binding_ref),
            "signed_factory_binding_hash": _hash64("signed_factory_binding_hash", signed_factory_binding_hash),
        }
        canonical = _canonical_json(payload, field_name="frozen_semantic_request")
        return cls(
            schema_version=FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA,
            identity=candidate.identity,
            semantic_candidate_hash=candidate.semantic_candidate_hash,
            signed_factory_binding_ref=str(payload["signed_factory_binding_ref"]),
            signed_factory_binding_hash=str(payload["signed_factory_binding_hash"]),
            canonical_final_payload_json=canonical,
            final_semantic_request_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA:
            raise ValueError("factory_role_frozen_semantic_request_schema_mismatch")
        if type(self.identity) is not FactoryRoleSemanticRequestIdentityV1:
            raise TypeError("factory_role_semantic_identity_exact_type_required")
        FactoryRoleSemanticRequestIdentityV1.__post_init__(self.identity)
        _hash64("semantic_candidate_hash", self.semantic_candidate_hash)
        _identifier("signed_factory_binding_ref", self.signed_factory_binding_ref)
        _hash64("signed_factory_binding_hash", self.signed_factory_binding_hash)
        if type(self.canonical_final_payload_json) is not str:
            raise TypeError("canonical_final_payload_json_type_invalid")
        try:
            payload = json.loads(self.canonical_final_payload_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("canonical_final_payload_json_invalid") from exc
        if _canonical_json(payload, field_name="frozen_semantic_request") != self.canonical_final_payload_json:
            raise ValueError("canonical_final_payload_json_not_canonical")
        expected_keys = {
            "schema_version",
            "identity",
            "role",
            "provider_id",
            "model",
            "interaction_mode",
            "capability_profile_id",
            "messages",
            "tools",
            "tool_choice",
            "response_format",
            "temperature",
            "max_tokens",
            "stream",
            "required_tools",
            "semantic_candidate_hash",
            "signed_factory_binding_ref",
            "signed_factory_binding_hash",
        }
        if type(payload) is not dict or set(payload) != expected_keys:
            raise ValueError("frozen_semantic_payload_closed_set_required")
        if payload["schema_version"] != FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA:
            raise ValueError("frozen_semantic_payload_schema_mismatch")
        if payload.get("identity") != _identity_record(self.identity):
            raise ValueError("frozen_semantic_identity_mismatch")
        if _canonical_role(payload["role"]) != payload["role"]:
            raise ValueError("frozen_semantic_role_not_canonical")
        for field_name in ("provider_id", "model", "interaction_mode"):
            if _identifier(field_name, payload[field_name]) != payload[field_name]:
                raise ValueError(f"frozen_semantic_{field_name}_not_canonical")
        _hash64("capability_profile_id", payload["capability_profile_id"])
        messages = payload["messages"]
        tools = payload["tools"]
        if type(messages) is not list or any(type(item) is not dict for item in messages):
            raise TypeError("frozen_semantic_messages_invalid")
        for message in messages:
            if type(message.get("role")) is not str or type(message.get("content")) is not str:
                raise TypeError("frozen_semantic_message_fields_invalid")
        if type(tools) is not list or any(type(item) is not dict for item in tools):
            raise TypeError("frozen_semantic_tools_invalid")
        if payload["tool_choice"] is not None and type(payload["tool_choice"]) not in {str, dict}:
            raise TypeError("frozen_semantic_tool_choice_invalid")
        if payload["response_format"] is not None and type(payload["response_format"]) is not dict:
            raise TypeError("frozen_semantic_response_format_invalid")
        if type(payload["temperature"]) not in {int, float}:
            raise TypeError("frozen_semantic_temperature_invalid")
        if type(payload["max_tokens"]) is not int or payload["max_tokens"] <= 0:
            raise ValueError("frozen_semantic_max_tokens_invalid")
        if type(payload["stream"]) is not bool:
            raise TypeError("frozen_semantic_stream_bool_required")
        required_tool_names = payload["required_tools"]
        if (
            type(required_tool_names) is not list
            or any(type(item) is not str or not item.strip() for item in required_tool_names)
            or len(required_tool_names) != len(set(required_tool_names))
        ):
            raise TypeError("frozen_semantic_required_tools_invalid")
        recovered_candidate_payload = _recover_candidate_payload_from_frozen(payload)
        recovered_candidate_hash = canonical_role_final_request_hash(recovered_candidate_payload)
        if recovered_candidate_hash != self.semantic_candidate_hash:
            raise ValueError("frozen_semantic_candidate_reconstruction_mismatch")
        if payload.get("semantic_candidate_hash") != self.semantic_candidate_hash:
            raise ValueError("frozen_semantic_candidate_hash_mismatch")
        if payload.get("signed_factory_binding_ref") != self.signed_factory_binding_ref:
            raise ValueError("frozen_semantic_binding_ref_mismatch")
        if payload.get("signed_factory_binding_hash") != self.signed_factory_binding_hash:
            raise ValueError("frozen_semantic_binding_hash_mismatch")
        expected_hash = hashlib.sha256(self.canonical_final_payload_json.encode("utf-8")).hexdigest()
        if self.final_semantic_request_hash != expected_hash:
            raise ValueError("final_semantic_request_hash_mismatch")


def validate_factory_role_frozen_semantic_evidence_policy(
    frozen: FactoryRoleFrozenSemanticRequestV1,
    *,
    policy_facts: RoleFinalRequestPolicyFactsV1,
) -> None:
    """Bind prompt evidence to the separate typed control-plane authority."""

    if type(frozen) is not FactoryRoleFrozenSemanticRequestV1:
        raise TypeError("factory_role_frozen_semantic_request_exact_type_required")
    FactoryRoleFrozenSemanticRequestV1.__post_init__(frozen)
    try:
        payload = json.loads(frozen.canonical_final_payload_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical_final_payload_json_invalid") from exc
    _recover_candidate_payload_from_frozen(payload, expected_policy_facts=policy_facts)


@runtime_checkable
class FactoryRoleEvidenceCutoffPort(Protocol):
    """Async authority boundary implemented only by the Factory control plane."""

    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        """Issue or replay exactly one fenced cutoff fact."""

    async def resolve_cutoff_proof(
        self,
        ack: FactoryRoleEvidenceCutoffAckV1,
    ) -> FactoryRoleEvidenceCutoffProofV1:
        """Strictly re-read and resolve one committed locator into a detached proof."""


_FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING: ContextVar[FactoryRoleEvidenceAuthorityBindingV1 | None] = ContextVar(
    "factory_role_evidence_authority_binding",
    default=None,
)


def get_factory_role_evidence_authority_binding() -> FactoryRoleEvidenceAuthorityBindingV1 | None:
    """Return the current pre-cutoff capability without serializable projection."""

    return _FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING.get()


@contextmanager
def bind_factory_role_evidence_authority(binding: FactoryRoleEvidenceAuthorityBindingV1) -> Iterator[None]:
    """Bind one exact capability and restore prior task context on every exit."""

    if type(binding) is not FactoryRoleEvidenceAuthorityBindingV1:
        raise TypeError("factory_role_evidence_authority_binding_exact_type_required")
    FactoryRoleEvidenceAuthorityBindingV1.__post_init__(binding)
    token = _FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING.set(binding)
    try:
        yield
    finally:
        _FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING.reset(token)


def contains_factory_role_evidence_runtime_authority(value: object) -> bool:
    """Fail-closed bounded scan for runtime-only carrier/port leakage."""

    max_depth = 32
    max_nodes = 8192
    max_items_per_container = 4096
    max_dataclass_fields = 4096
    max_mro_entries = 64
    max_slot_names = 256
    visited_nodes = 0

    def contains(candidate: object, seen: set[int], *, depth: int) -> bool:
        nonlocal visited_nodes
        if type(candidate) is FactoryRoleEvidenceAuthorityBindingV1:
            return True
        if isinstance(candidate, FactoryRoleEvidenceCutoffPort):
            return True
        if isinstance(candidate, FactoryPhysicalAttemptControlPort):
            return True
        if depth > max_depth or visited_nodes >= max_nodes:
            return True
        identity = id(candidate)
        if identity in seen:
            return False
        visited_nodes += 1
        if isinstance(candidate, Mapping):
            seen.add(identity)
            for index, (key, item) in enumerate(candidate.items()):
                if index >= max_items_per_container:
                    return True
                if contains(key, seen, depth=depth + 1) or contains(item, seen, depth=depth + 1):
                    return True
            return False
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray, memoryview)):
            seen.add(identity)
            for index, item in enumerate(candidate):
                if index >= max_items_per_container or contains(item, seen, depth=depth + 1):
                    return True
            return False
        if isinstance(candidate, (set, frozenset)):
            seen.add(identity)
            for index, item in enumerate(candidate):
                if index >= max_items_per_container or contains(item, seen, depth=depth + 1):
                    return True
            return False
        if is_dataclass(candidate) and not isinstance(candidate, type):
            seen.add(identity)
            candidate_fields = fields(candidate)
            if len(candidate_fields) > max_dataclass_fields:
                return True
            for item in candidate_fields:
                try:
                    field_value = getattr(candidate, item.name)
                except Exception:  # noqa: BLE001 - hostile descriptors must fail closed
                    return True
                if contains(field_value, seen, depth=depth + 1):
                    return True
            return False
        slot_names: list[str] = []
        candidate_mro = type(candidate).__mro__
        if len(candidate_mro) > max_mro_entries:
            return True
        for base in candidate_mro:
            declared = base.__dict__.get("__slots__", ())
            if isinstance(declared, str):
                declared_names = (declared,)
            elif isinstance(declared, (tuple, list)):
                declared_names = tuple(declared)
            else:
                return True
            for slot_name in declared_names:
                if slot_name in {"__dict__", "__weakref__"}:
                    continue
                if type(slot_name) is not str or not slot_name:
                    return True
                slot_names.append(slot_name)
                if len(slot_names) > max_slot_names:
                    return True
        if slot_names:
            seen.add(identity)
            for slot_name in slot_names:
                try:
                    slot_value = getattr(candidate, slot_name)
                except AttributeError:
                    continue
                except Exception:  # noqa: BLE001 - hostile slot descriptors must fail closed
                    return True
                if contains(slot_value, seen, depth=depth + 1):
                    return True
            return False
        try:
            attributes = vars(candidate)
        except TypeError:
            return False
        if isinstance(attributes, Mapping):
            seen.add(identity)
            return contains(attributes, seen, depth=depth + 1)
        return False

    try:
        return contains(value, set(), depth=0)
    except Exception:  # noqa: BLE001 - any hostile traversal behavior is a leak signal
        return True


__all__ = [
    "FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_PROOF_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA",
    "FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA",
    "FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA",
    "FactoryRoleEvidenceAuthorityBindingV1",
    "FactoryRoleEvidenceCutoffAckV1",
    "FactoryRoleEvidenceCutoffPort",
    "FactoryRoleEvidenceCutoffProofV1",
    "FactoryRoleEvidenceCutoffRequestV1",
    "FactoryRoleEvidenceCutoffSourceHeadV1",
    "FactoryRoleFrozenSemanticRequestV1",
    "FactoryRoleSemanticCandidateV1",
    "FactoryRoleSemanticRequestIdentityV1",
    "bind_factory_role_evidence_authority",
    "contains_factory_role_evidence_runtime_authority",
    "get_factory_role_evidence_authority_binding",
]
