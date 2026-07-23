"""A009B1 public cutoff authority contract tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
from typing import get_type_hints

import pytest
from polaris.cells.roles.kernel.public import final_request_evidence_cutoff as cutoff_contract
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffRequestV1,
)
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
    canonical_role_final_request_hash,
    render_role_final_request_policy_facts,
    role_final_request_policy,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64


class _StrSubclass(str):
    pass


class _IntSubclass(int):
    pass


class _TupleSubclass(tuple):
    pass


def _request(**overrides: object) -> FactoryRoleEvidenceCutoffRequestV1:
    values: dict[str, object] = {
        "schema_version": FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
        "run_id": "run-1",
        "role": "director",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_freeze_id": "freeze-1",
        "semantic_candidate_hash": _HASH_A,
        "attempt_budget": 3,
        "execution_authority_hash": _HASH_B,
        "candidate_refs": ("pm-contract-1", "blueprint-1"),
    }
    values.update(overrides)
    return FactoryRoleEvidenceCutoffRequestV1(**values)  # type: ignore[arg-type]


def _ack(**overrides: object) -> FactoryRoleEvidenceCutoffAckV1:
    values: dict[str, object] = {
        "schema_version": FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
        "factory_run_id": "factory-run-1",
        "run_id": "run-1",
        "role": "director",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_freeze_id": "freeze-1",
        "semantic_candidate_hash": _HASH_A,
        "attempt_budget": 3,
        "execution_authority_hash": _HASH_B,
        "authority_stream": (f"factory.role_evidence_authority.{hashlib.sha256(b'factory-run-1').hexdigest()}"),
        "cutoff_fact_id": "fact-1",
        "cutoff_fact_sequence": 9,
        "cutoff_fact_hash": _HASH_C,
        "cutoff_body_hash": _HASH_A,
        "cutoff_fragment_vector_hash": _HASH_B,
        "cutoff_fragment_count": 4,
    }
    values.update(overrides)
    return FactoryRoleEvidenceCutoffAckV1(**values)  # type: ignore[arg-type]


def _new_contract_type(name: str) -> type:
    contract_type = getattr(cutoff_contract, name, None)
    assert contract_type is not None, f"B3.2 public contract missing: {name}"
    assert isinstance(contract_type, type)
    return contract_type


def _policy_facts(
    role: str = "director",
    *,
    run_id: str = "run-1",
    request_freeze_id: str = "freeze-1",
) -> RoleFinalRequestPolicyFactsV1:
    policy = role_final_request_policy(role)
    slots: list[RoleFinalRequestEvidenceSlotV1] = []
    for index, ref_kind in enumerate(policy.slot_order, start=1):
        present = ref_kind in policy.required_present_slots
        source_hash = canonical_role_final_request_hash([role, ref_kind, index])
        source_head_sequence = index if present else 0
        items = (
            (
                RoleFinalRequestEvidenceAnchorV1.create(
                    ref_kind=ref_kind,
                    canonical_source_ref=f"factory/sources/{ref_kind}",
                    canonical_ref=f"runtime/facts/{ref_kind}/item.json",
                    canonical_hash=source_hash,
                    source_fact_schema="polaris.test_fact.v1",
                    source_fact_version="1",
                    factory_run_id="factory-run-1",
                    run_id=run_id,
                    role=role,
                    request_freeze_id=request_freeze_id,
                    cutoff_fact_id="fact-1",
                    cutoff_fact_sequence=9,
                    cutoff_fact_hash=_HASH_C,
                    source_fact_id=f"fact-{ref_kind}",
                    source_fact_sequence=index,
                    source_fact_hash=source_hash,
                    source_head_sequence=source_head_sequence,
                    source_head_hash=source_hash,
                    execution_authority_hash=_HASH_B,
                ),
            )
            if present
            else ()
        )
        slots.append(
            RoleFinalRequestEvidenceSlotV1.create(
                ref_kind=ref_kind,
                state="present" if present else "absent_at_request_time",
                canonical_source_ref=f"factory/sources/{ref_kind}",
                source_fact_schema="polaris.test_fact.v1",
                source_fact_version="1",
                factory_run_id="factory-run-1",
                run_id=run_id,
                role=role,
                request_freeze_id=request_freeze_id,
                cutoff_fact_id="fact-1",
                cutoff_fact_sequence=9,
                cutoff_fact_hash=_HASH_C,
                source_head_sequence=source_head_sequence,
                source_head_hash=source_hash,
                execution_authority_hash=_HASH_B,
                items=items,
            )
        )
    return RoleFinalRequestPolicyFactsV1.create(role=role, slots=slots)


def test_request_is_frozen_hint_only_and_normalizes_identifiers() -> None:
    request = _request(run_id="  run-1  ", candidate_refs=("pm-contract:1",))

    assert request.run_id == "run-1"
    assert request.candidate_refs == ("pm-contract:1",)
    assert request.attempt_budget == 3
    assert {field.name for field in fields(request)} == {
        "schema_version",
        "run_id",
        "role",
        "turn_id",
        "call_id",
        "request_freeze_id",
        "semantic_candidate_hash",
        "attempt_budget",
        "execution_authority_hash",
        "candidate_refs",
    }
    with pytest.raises(AttributeError):
        request.call_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("run_id", " "),
        ("role", "auditor"),
        ("semantic_candidate_hash", "A" * 64),
        ("semantic_candidate_hash", "a" * 63),
        ("execution_authority_hash", 64),
        ("attempt_budget", True),
        ("attempt_budget", 0),
        ("attempt_budget", -1),
        ("candidate_refs", ["pm-contract-1"]),
        ("candidate_refs", ({"raw": "payload"},)),
        ("candidate_refs", ("dup", "dup")),
        ("candidate_refs", ("raw evidence with spaces",)),
        ("candidate_refs", ("line-one\nline-two",)),
        ("candidate_refs", ("x" * 257,)),
        ("candidate_refs", tuple(f"candidate-{index}" for index in range(33))),
    ],
)
def test_request_fails_closed_on_malformed_or_authority_shaped_input(field_name: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _request(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", _StrSubclass(FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA)),
        ("run_id", _StrSubclass("run-1")),
        ("semantic_candidate_hash", _StrSubclass(_HASH_A)),
        ("attempt_budget", _IntSubclass(3)),
        ("candidate_refs", _TupleSubclass(("pm-contract-1",))),
        ("candidate_refs", (_StrSubclass("pm-contract-1"),)),
    ],
)
def test_request_rejects_scalar_and_container_subclasses(field_name: str, value: object) -> None:
    with pytest.raises(TypeError):
        _request(**{field_name: value})


def test_ack_is_strict_locator_only() -> None:
    ack = _ack()

    assert ack.cutoff_fact_sequence == 9
    assert ack.cutoff_fact_hash == _HASH_C
    assert ack.cutoff_body_hash == _HASH_A
    assert ack.cutoff_fragment_vector_hash == _HASH_B
    assert ack.cutoff_fragment_count == 4
    assert {field.name for field in fields(ack)} == {
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
        "authority_stream",
        "cutoff_fact_id",
        "cutoff_fact_sequence",
        "cutoff_fact_hash",
        "cutoff_body_hash",
        "cutoff_fragment_vector_hash",
        "cutoff_fragment_count",
    }


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", "wrong"),
        ("role", "auditor"),
        ("cutoff_fact_sequence", True),
        ("cutoff_fact_sequence", 0),
        ("cutoff_fact_hash", "f" * 65),
        ("semantic_candidate_hash", "not-a-hash"),
        ("attempt_budget", 0),
        ("authority_stream", 5),
        ("authority_stream", "factory.role_evidence_authority.deadbeef"),
        ("authority_stream", f"factory.role_evidence_authority.{'A' * 64}"),
        ("authority_stream", f"factory.role_evidence_authority.{_HASH_A}"),
        ("cutoff_body_hash", "f" * 63),
        ("cutoff_fragment_vector_hash", "f" * 65),
        ("cutoff_fragment_count", 0),
        ("cutoff_fragment_count", 65),
    ],
)
def test_ack_fails_closed_on_malformed_locator(field_name: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _ack(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", _StrSubclass(FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA)),
        ("factory_run_id", _StrSubclass("factory-run-1")),
        ("cutoff_fact_sequence", _IntSubclass(9)),
        ("cutoff_fact_hash", _StrSubclass(_HASH_C)),
    ],
)
def test_ack_rejects_scalar_subclasses(field_name: str, value: object) -> None:
    with pytest.raises(TypeError):
        _ack(**{field_name: value})


def test_protocol_uses_authority_specific_async_method_name() -> None:
    from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
        FactoryRoleEvidenceCutoffPort,
    )

    assert hasattr(FactoryRoleEvidenceCutoffPort, "acquire_cutoff")
    assert hasattr(FactoryRoleEvidenceCutoffPort, "resolve_cutoff_proof")
    assert not hasattr(FactoryRoleEvidenceCutoffPort, "acquire")
    assert get_type_hints(FactoryRoleEvidenceCutoffPort.acquire_cutoff)["return"] is FactoryRoleEvidenceCutoffAckV1
    assert get_type_hints(FactoryRoleEvidenceCutoffPort.resolve_cutoff_proof)["return"] is _new_contract_type(
        "FactoryRoleEvidenceCutoffProofV1"
    )


def test_b32_public_proof_is_exact_typed_detached_value() -> None:
    source_head_type = _new_contract_type("FactoryRoleEvidenceCutoffSourceHeadV1")
    proof_type = _new_contract_type("FactoryRoleEvidenceCutoffProofV1")
    facts = _policy_facts()
    heads = tuple(
        source_head_type(
            canonical_source_ref=slot.canonical_source_ref,
            source_fact_schema=slot.source_fact_schema,
            source_fact_version=slot.source_fact_version,
            source_head_fact_id=f"head-{slot.ref_kind}" if slot.source_head_sequence else "",
            source_head_sequence=slot.source_head_sequence,
            source_head_hash=slot.source_head_hash,
        )
        for slot in facts.slots
    )
    proof = proof_type.create(ack=_ack(), source_head_vector=heads, policy_facts=facts)

    assert type(proof) is proof_type
    assert proof.ack == _ack()
    assert proof.signed_factory_binding_ref == f"{_ack().authority_stream}@9#fact-1"
    assert proof.source_head_vector == heads
    assert proof.policy_facts is facts
    assert len(proof.signed_factory_binding_hash) == 64
    assert {field.name for field in fields(proof)} == {
        "schema_version",
        "ack",
        "signed_factory_binding_ref",
        "signed_factory_binding_hash",
        "source_head_vector",
        "source_head_vector_hash",
        "policy_facts",
    }
    assert not hasattr(proof, "cutoff_port")
    assert not hasattr(proof, "authority_binding")


def test_cutoff_proof_rejects_source_head_or_policy_fact_tampering() -> None:
    source_head_type = _new_contract_type("FactoryRoleEvidenceCutoffSourceHeadV1")
    proof_type = _new_contract_type("FactoryRoleEvidenceCutoffProofV1")
    facts = _policy_facts()
    heads = tuple(
        source_head_type(
            canonical_source_ref=slot.canonical_source_ref,
            source_fact_schema=slot.source_fact_schema,
            source_fact_version=slot.source_fact_version,
            source_head_fact_id=f"head-{slot.ref_kind}" if slot.source_head_sequence else "",
            source_head_sequence=slot.source_head_sequence,
            source_head_hash=slot.source_head_hash,
        )
        for slot in facts.slots
    )

    with pytest.raises((TypeError, ValueError)):
        proof_type.create(
            ack=_ack(),
            source_head_vector=(replace(heads[0], source_head_hash="f" * 64), *heads[1:]),
            policy_facts=facts,
        )

    object.__setattr__(facts.slots[0], "source_head_hash", "f" * 64)
    with pytest.raises((TypeError, ValueError)):
        proof_type.create(ack=_ack(), source_head_vector=heads, policy_facts=facts)


def test_cutoff_proof_and_frozen_values_do_not_leak_runtime_authority_recursively() -> None:
    source_head_type = _new_contract_type("FactoryRoleEvidenceCutoffSourceHeadV1")
    proof_type = _new_contract_type("FactoryRoleEvidenceCutoffProofV1")
    facts = _policy_facts()
    heads = tuple(
        source_head_type(
            canonical_source_ref=slot.canonical_source_ref,
            source_fact_schema=slot.source_fact_schema,
            source_fact_version=slot.source_fact_version,
            source_head_fact_id=f"head-{slot.ref_kind}" if slot.source_head_sequence else "",
            source_head_sequence=slot.source_head_sequence,
            source_head_hash=slot.source_head_hash,
        )
        for slot in facts.slots
    )
    proof = proof_type.create(ack=_ack(), source_head_vector=heads, policy_facts=facts)

    assert cutoff_contract.contains_factory_role_evidence_runtime_authority(proof) is False
    assert cutoff_contract.contains_factory_role_evidence_runtime_authority({"nested": [proof]}) is False
    assert "cutoff_port" not in repr(proof)


def test_semantic_identity_is_exact_frozen_invoker_owned_record() -> None:
    identity_type = _new_contract_type("FactoryRoleSemanticRequestIdentityV1")
    identity = identity_type(
        run_id="role-run-1",
        turn_id="role-run-1:turn:2",
        call_id="a" * 32,
        request_freeze_id="b" * 32,
    )

    assert {field.name for field in fields(identity)} == {
        "run_id",
        "turn_id",
        "call_id",
        "request_freeze_id",
    }
    with pytest.raises(AttributeError):
        identity.call_id = "c" * 32  # type: ignore[misc]
    with pytest.raises((TypeError, ValueError)):
        identity_type(
            run_id="role-run-1",
            turn_id="role-run-1:turn:2",
            call_id="short",
            request_freeze_id="b" * 32,
        )


def _candidate_inputs() -> dict[str, object]:
    return {
        "role": "director",
        "provider_id": "provider-a",
        "model": "kimi-for-coding",
        "interaction_mode": "native_tools",
        "capability_profile": {
            "schema_version": "polaris.resolved_actor_capability_profile.v1",
            "actor": "director",
            "provider_id": "provider-a",
            "model": "kimi-for-coding",
            "native_tools": True,
        },
        "messages": [
            {"role": "system", "content": "You are Director.\n\npolaris.role_identity.v1:director"},
            {"role": "user", "content": "Implement the task."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 4000,
        "stream": False,
    }


def _identity() -> object:
    identity_type = _new_contract_type("FactoryRoleSemanticRequestIdentityV1")
    return identity_type(
        run_id="role-run-1",
        turn_id="role-run-1:turn:2",
        call_id="a" * 32,
        request_freeze_id="b" * 32,
    )


def _signed_binding_ref() -> str:
    stream = "factory.role_evidence_authority." + hashlib.sha256(b"factory-run-1").hexdigest()
    return f"{stream}@9#fact-1"


def _post_evidence_messages(inputs: dict[str, object], identity: object) -> list[dict[str, str]]:
    messages = json.loads(json.dumps(inputs["messages"]))
    facts = _policy_facts(
        "director",
        run_id=identity.run_id,
        request_freeze_id=identity.request_freeze_id,
    )
    policy_line = render_role_final_request_policy_facts(facts)
    messages[0]["content"] += (
        f"\n\npolaris.final_request_evidence.v1:begin\n{policy_line}\npolaris.final_request_evidence.v1:end"
    )
    return messages


def _valid_frozen_semantic_request() -> object:
    candidate_type = _new_contract_type("FactoryRoleSemanticCandidateV1")
    frozen_type = _new_contract_type("FactoryRoleFrozenSemanticRequestV1")
    inputs = _candidate_inputs()
    identity = _identity()
    candidate = candidate_type.create(identity=identity, **inputs)
    return frozen_type.create(
        candidate=candidate,
        signed_factory_binding_ref=_signed_binding_ref(),
        signed_factory_binding_hash=_HASH_A,
        messages=_post_evidence_messages(inputs, identity),
        tools=inputs["tools"],
        tool_choice=inputs["tool_choice"],
        response_format=inputs["response_format"],
        temperature=inputs["temperature"],
        max_tokens=inputs["max_tokens"],
        stream=inputs["stream"],
    )


def _reconstruct_frozen_with_payload(frozen: object, payload: dict[str, object]) -> object:
    frozen_type = _new_contract_type("FactoryRoleFrozenSemanticRequestV1")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return frozen_type(
        schema_version=frozen.schema_version,
        identity=frozen.identity,
        semantic_candidate_hash=frozen.semantic_candidate_hash,
        signed_factory_binding_ref=frozen.signed_factory_binding_ref,
        signed_factory_binding_hash=frozen.signed_factory_binding_hash,
        canonical_final_payload_json=canonical,
        final_semantic_request_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def test_semantic_candidate_is_canonical_deep_copy_with_authoritative_capability_id() -> None:
    candidate_type = _new_contract_type("FactoryRoleSemanticCandidateV1")
    inputs = _candidate_inputs()
    original_messages = inputs["messages"]
    original_profile = inputs["capability_profile"]
    candidate = candidate_type.create(identity=_identity(), **inputs)

    assert candidate.candidate_refs == ()
    payload = json.loads(candidate.canonical_payload_json)
    assert payload["capability_profile_id"] == canonical_role_final_request_hash(original_profile)
    assert candidate.semantic_candidate_hash == canonical_role_final_request_hash(payload)
    assert set(payload) == {
        "schema_version",
        "identity",
        "role",
        "provider_id",
        "model",
        "interaction_mode",
        "capability_profile_id",
        "messages",
        "tools",
        "required_tools",
        "tool_choice",
        "response_format",
        "temperature",
        "max_tokens",
        "stream",
    }
    assert isinstance(original_messages, list)
    original_messages[0]["content"] = "mutated"  # type: ignore[index]
    assert isinstance(original_profile, dict)
    original_profile["actor"] = "mutated"
    assert json.loads(candidate.canonical_payload_json) == payload


def test_resolved_capability_profile_change_changes_capability_id_and_candidate_hash() -> None:
    candidate_type = _new_contract_type("FactoryRoleSemanticCandidateV1")
    first_inputs = _candidate_inputs()
    second_inputs = _candidate_inputs()
    second_profile = second_inputs["capability_profile"]
    assert isinstance(second_profile, dict)
    second_profile["native_tools"] = False

    first = candidate_type.create(identity=_identity(), **first_inputs)
    second = candidate_type.create(identity=_identity(), **second_inputs)
    first_payload = json.loads(first.canonical_payload_json)
    second_payload = json.loads(second.canonical_payload_json)

    assert first_payload["capability_profile_id"] != second_payload["capability_profile_id"]
    assert first.semantic_candidate_hash != second.semantic_candidate_hash
    assert len(first_payload["capability_profile_id"]) == 64
    assert all(char in "0123456789abcdef" for char in first_payload["capability_profile_id"])


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("messages", {"role": "system"}),
        ("tools", {"name": "write_file"}),
        ("tool_choice", {1: "non-string-key"}),
        ("response_format", {"value": {"unordered"}}),
        ("temperature", float("nan")),
        ("capability_profile", {"opaque": object()}),
    ],
)
def test_semantic_candidate_rejects_lossy_or_nondeterministic_values(
    field_name: str,
    invalid_value: object,
) -> None:
    candidate_type = _new_contract_type("FactoryRoleSemanticCandidateV1")
    inputs = _candidate_inputs()
    inputs[field_name] = invalid_value
    with pytest.raises((TypeError, ValueError)):
        candidate_type.create(identity=_identity(), **inputs)


def test_frozen_semantic_request_binds_post_injection_payload_without_mutable_containers() -> None:
    candidate_type = _new_contract_type("FactoryRoleSemanticCandidateV1")
    frozen_type = _new_contract_type("FactoryRoleFrozenSemanticRequestV1")
    inputs = _candidate_inputs()
    identity = _identity()
    candidate = candidate_type.create(identity=identity, **inputs)
    post_messages = _post_evidence_messages(inputs, identity)
    frozen = frozen_type.create(
        candidate=candidate,
        signed_factory_binding_ref=_signed_binding_ref(),
        signed_factory_binding_hash=_HASH_A,
        messages=post_messages,
        tools=inputs["tools"],
        tool_choice=inputs["tool_choice"],
        response_format=inputs["response_format"],
        temperature=inputs["temperature"],
        max_tokens=inputs["max_tokens"],
        stream=inputs["stream"],
    )

    payload = json.loads(frozen.canonical_final_payload_json)
    candidate_payload = json.loads(candidate.canonical_payload_json)
    assert candidate_payload["schema_version"] == cutoff_contract.FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA
    assert payload["schema_version"] == cutoff_contract.FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA
    assert payload["schema_version"] != candidate_payload["schema_version"]
    assert frozen.identity == candidate.identity
    assert frozen.semantic_candidate_hash == candidate.semantic_candidate_hash
    assert payload["semantic_candidate_hash"] == candidate.semantic_candidate_hash
    assert payload["signed_factory_binding_ref"] == frozen.signed_factory_binding_ref
    assert payload["signed_factory_binding_hash"] == frozen.signed_factory_binding_hash
    assert payload["capability_profile_id"] == json.loads(candidate.canonical_payload_json)["capability_profile_id"]
    assert frozen.final_semantic_request_hash == canonical_role_final_request_hash(payload)
    post_messages[0]["content"] = "mutated-after-freeze"
    tools = inputs["tools"]
    assert isinstance(tools, list)
    tools[0]["function"]["name"] = "mutated_tool"
    assert json.loads(frozen.canonical_final_payload_json) == payload
    with pytest.raises(ValueError, match="factory_role_frozen_semantic_request_schema_mismatch"):
        replace(frozen, schema_version=candidate.schema_version)


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("tools", []),
        ("tool_choice", "none"),
        ("response_format", None),
        ("temperature", 0.8),
        ("max_tokens", 3999),
        ("stream", True),
    ],
)
def test_frozen_semantic_request_rejects_post_candidate_semantic_drift(
    field_name: str,
    changed_value: object,
) -> None:
    candidate_type = _new_contract_type("FactoryRoleSemanticCandidateV1")
    frozen_type = _new_contract_type("FactoryRoleFrozenSemanticRequestV1")
    inputs = _candidate_inputs()
    identity = _identity()
    candidate = candidate_type.create(identity=identity, **inputs)
    final_inputs = {
        "messages": _post_evidence_messages(inputs, identity),
        "tools": inputs["tools"],
        "tool_choice": inputs["tool_choice"],
        "response_format": inputs["response_format"],
        "temperature": inputs["temperature"],
        "max_tokens": inputs["max_tokens"],
        "stream": inputs["stream"],
    }
    final_inputs[field_name] = changed_value

    with pytest.raises(ValueError, match=f"factory_role_semantic_request_drift:{field_name}"):
        frozen_type.create(
            candidate=candidate,
            signed_factory_binding_ref=_signed_binding_ref(),
            signed_factory_binding_hash=_HASH_A,
            **final_inputs,
        )


def test_semantic_candidate_direct_constructor_rejects_self_consistent_extra_field() -> None:
    candidate_type = _new_contract_type("FactoryRoleSemanticCandidateV1")
    candidate = candidate_type.create(identity=_identity(), **_candidate_inputs())
    payload = json.loads(candidate.canonical_payload_json)
    payload["uncontracted"] = "forged"
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    with pytest.raises(ValueError, match="semantic_candidate_payload_closed_set_required"):
        candidate_type(
            schema_version=candidate.schema_version,
            identity=candidate.identity,
            canonical_payload_json=canonical,
            semantic_candidate_hash=canonical_role_final_request_hash(payload),
            candidate_refs=(),
        )


def test_frozen_semantic_direct_constructor_rejects_self_consistent_extra_field() -> None:
    candidate_type = _new_contract_type("FactoryRoleSemanticCandidateV1")
    frozen_type = _new_contract_type("FactoryRoleFrozenSemanticRequestV1")
    inputs = _candidate_inputs()
    identity = _identity()
    candidate = candidate_type.create(identity=identity, **inputs)
    frozen = frozen_type.create(
        candidate=candidate,
        signed_factory_binding_ref=_signed_binding_ref(),
        signed_factory_binding_hash=_HASH_A,
        messages=_post_evidence_messages(inputs, identity),
        tools=inputs["tools"],
        tool_choice=inputs["tool_choice"],
        response_format=inputs["response_format"],
        temperature=inputs["temperature"],
        max_tokens=inputs["max_tokens"],
        stream=inputs["stream"],
    )
    payload = json.loads(frozen.canonical_final_payload_json)
    payload["uncontracted"] = "forged"
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    with pytest.raises(ValueError, match="frozen_semantic_payload_closed_set_required"):
        frozen_type(
            schema_version=frozen.schema_version,
            identity=frozen.identity,
            semantic_candidate_hash=frozen.semantic_candidate_hash,
            signed_factory_binding_ref=frozen.signed_factory_binding_ref,
            signed_factory_binding_hash=frozen.signed_factory_binding_hash,
            canonical_final_payload_json=canonical,
            final_semantic_request_hash=canonical_role_final_request_hash(payload),
        )


def test_frozen_direct_constructor_rejects_candidate_message_tamper_with_self_consistent_final_hash() -> None:
    frozen = _valid_frozen_semantic_request()
    payload = json.loads(frozen.canonical_final_payload_json)
    payload["messages"][1]["content"] = "forged-after-candidate-freeze"

    with pytest.raises(ValueError, match="frozen_semantic_candidate_reconstruction_mismatch"):
        _reconstruct_frozen_with_payload(frozen, payload)


@pytest.mark.parametrize(
    ("tamper", "expected_error"),
    [
        ("nonfirst", "frozen_semantic_evidence_canonical_suffix_required"),
        ("duplicate", "frozen_semantic_evidence_unique_block_required"),
        ("noncanonical", "frozen_semantic_evidence_policy_not_canonical"),
        ("wrong_role", "frozen_semantic_evidence_policy_role_mismatch"),
        ("nonterminal_marker", "frozen_semantic_evidence_role_marker_not_terminal"),
    ],
)
def test_frozen_direct_constructor_rejects_invalid_authority_evidence_block(
    tamper: str,
    expected_error: str,
) -> None:
    frozen = _valid_frozen_semantic_request()
    payload = json.loads(frozen.canonical_final_payload_json)
    messages = payload["messages"]
    first_content = messages[0]["content"]
    separator = "\n\npolaris.final_request_evidence.v1:begin\n"
    candidate_system, evidence_tail = first_content.split(separator, 1)

    if tamper == "nonfirst":
        messages[0]["content"] = candidate_system
        messages[1]["content"] = f"{messages[1]['content']}{separator}{evidence_tail}"
    elif tamper == "duplicate":
        messages[0]["content"] = f"{first_content}{separator}{evidence_tail}"
    elif tamper == "noncanonical":
        policy_json, end_marker = evidence_tail.rsplit("\n", 1)
        noncanonical = json.dumps(json.loads(policy_json), ensure_ascii=False, sort_keys=True)
        messages[0]["content"] = f"{candidate_system}{separator}{noncanonical}\n{end_marker}"
    elif tamper == "wrong_role":
        wrong_facts = _policy_facts(
            "qa",
            run_id=frozen.identity.run_id,
            request_freeze_id=frozen.identity.request_freeze_id,
        )
        wrong_policy = render_role_final_request_policy_facts(wrong_facts)
        messages[0]["content"] = f"{candidate_system}{separator}{wrong_policy}\npolaris.final_request_evidence.v1:end"
    else:
        messages[0]["content"] = f"{candidate_system}\nintervening text{separator}{evidence_tail}"

    with pytest.raises(ValueError, match=expected_error):
        _reconstruct_frozen_with_payload(frozen, payload)
