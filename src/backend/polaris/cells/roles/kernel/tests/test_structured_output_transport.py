"""Provider-tool structured output stays a protocol transport, never an effect."""

from __future__ import annotations

import json

import pytest
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    StructuredOutputStreamNormalizer,
    is_canonical_structured_output_stream_chunk,
    normalize_structured_output_response,
    resolve_structured_output_transport,
)
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)


def _contract() -> RoleStructuredOutputContractV1:
    return RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete Chief Engineer blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "scope_for_apply", "risk_flags"],
            "additionalProperties": False,
        },
    )


def test_public_contract_round_trips_one_canonical_context_projection() -> None:
    contract = _contract()

    projection = contract.to_context_projection()
    restored = RoleStructuredOutputContractV1.from_context_projection(projection)

    assert restored == contract
    assert projection["schema_name"] == "chief_engineer_blueprint_portfolio"
    assert projection["transport"] == "provider_tool"
    assert projection["strict"] is True


def test_public_contract_rejects_non_object_json_schema() -> None:
    with pytest.raises(ValueError, match="json_schema_type_must_be_object"):
        RoleStructuredOutputContractV1(
            schema_name="invalid",
            description="Invalid array result.",
            json_schema={"type": "array"},
        )


def test_transport_plan_projects_exact_provider_tool_without_effect_authority() -> None:
    contract = _contract()
    context = {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}

    plan = resolve_structured_output_transport(context)

    assert plan is not None
    assert plan.tool_choice == {
        "type": "function",
        "function": {"name": STRUCTURED_OUTPUT_TOOL_NAME},
    }
    assert plan.tool_definition["function"]["name"] == STRUCTURED_OUTPUT_TOOL_NAME
    assert plan.tool_definition["function"]["parameters"] == contract.json_schema
    assert plan.audit["side_effect"] is False
    assert plan.audit["tool_lifecycle"] is False


def test_non_stream_result_tool_is_normalized_to_json_without_tool_dispatch() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None
    payload = {
        "construction_plan": {"task_plans": {}},
        "scope_for_apply": [],
        "risk_flags": [],
    }

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": payload,
                    "call_id": "call-result-1",
                }
            ],
            "native_tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": payload,
                    "call_id": "call-result-1",
                }
            ],
            "usage": {"output_tokens": 42},
        },
        plan,
    )

    assert json.loads(normalized["content"]) == payload
    assert normalized["tool_calls"] == []
    assert normalized["native_tool_calls"] == []
    assert normalized["structured_output_transport"]["tool_lifecycle"] is False
    assert normalized["structured_output_transport"]["side_effect"] is False


def test_result_tool_payload_is_validated_against_caller_schema() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": {
                            "construction_plan": "not-an-object",
                            "scope_for_apply": [],
                            "risk_flags": [],
                        },
                        "call_id": "call-invalid-result",
                    }
                ],
            },
            plan,
        )


def test_missing_required_empty_arrays_are_coerced_before_schema_validation() -> None:
    """CE portfolio often omits risk_flags=[]; do not fail SCHEMA-REPAIR loops on that alone.

    L1-01 r123: both primary CE and SCHEMA-REPAIR failed with
    structured_output_payload_schema_mismatch:$:'risk_flags' is a required property
    while construction_plan was present.
    """
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": {"construction_plan": {"task_plans": {}}},
                    "call_id": "call-missing-empty-arrays",
                }
            ],
        },
        plan,
    )

    payload = json.loads(normalized["content"])
    assert payload["construction_plan"] == {"task_plans": {}}
    assert payload["scope_for_apply"] == []
    assert payload["risk_flags"] == []
    assert normalized["tool_calls"] == []


def test_null_required_array_is_coerced_to_empty_list() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": {
                        "construction_plan": {"task_plans": {}},
                        "scope_for_apply": None,
                        "risk_flags": None,
                    },
                    "call_id": "call-null-arrays",
                }
            ],
        },
        plan,
    )

    payload = json.loads(normalized["content"])
    assert payload["scope_for_apply"] == []
    assert payload["risk_flags"] == []


def test_stream_result_tool_is_buffered_and_normalized_before_transaction_decoder() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None
    normalizer = StructuredOutputStreamNormalizer(plan)
    payload = {
        "construction_plan": {"task_plans": {}},
        "scope_for_apply": [],
        "risk_flags": [],
    }

    assert normalizer.project({"type": "chunk", "content": "I will submit the result."}) == ()
    assert (
        normalizer.project(
            {
                "type": "tool_call",
                "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                "args": payload,
                "call_id": "call-result-stream",
            }
        )
        == ()
    )
    projected = normalizer.project({"type": "complete", "metadata": {"provider_id": "deepseek"}})

    assert tuple(item["type"] for item in projected) == ("chunk", "complete")
    assert json.loads(projected[0]["content"]) == payload
    assert is_canonical_structured_output_stream_chunk(projected[0]) is True
    assert projected[1]["metadata"]["structured_output_transport"]["tool_lifecycle"] is False
    assert projected[1]["metadata"]["structured_output_transport"]["side_effect"] is False
    assert all(item["type"] != "tool_call" for item in projected)


def test_canonical_stream_chunk_recognizer_rejects_unbound_or_executable_markers() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None
    normalizer = StructuredOutputStreamNormalizer(plan)
    payload = {
        "construction_plan": {"signature": "Result<T, E>"},
        "scope_for_apply": [],
        "risk_flags": [],
    }
    assert (
        normalizer.project(
            {
                "type": "tool_call",
                "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                "args": payload,
                "call_id": "call-recognizer",
            }
        )
        == ()
    )
    canonical = normalizer.project({"type": "complete", "metadata": {}})[0]
    evidence = canonical["metadata"]["structured_output_transport"]

    assert is_canonical_structured_output_stream_chunk({"type": "chunk", "content": canonical["content"]}) is False
    assert (
        is_canonical_structured_output_stream_chunk(
            {
                **canonical,
                "metadata": {
                    "structured_output_transport": {
                        **evidence,
                        "payload_sha256": "0" * 64,
                    }
                },
            }
        )
        is False
    )
    assert (
        is_canonical_structured_output_stream_chunk(
            {
                **canonical,
                "metadata": {
                    "structured_output_transport": {
                        **evidence,
                        "side_effect": True,
                    }
                },
            }
        )
        is False
    )
    assert (
        is_canonical_structured_output_stream_chunk(
            {
                **canonical,
                "metadata": {
                    "structured_output_transport": {
                        **evidence,
                        "strict": False,
                    }
                },
            }
        )
        is False
    )


def test_stream_without_result_tool_preserves_buffered_text_for_strict_validation() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None
    normalizer = StructuredOutputStreamNormalizer(plan)
    chunk = {"type": "chunk", "content": '{"construction_plan":'}
    complete = {"type": "complete", "metadata": {}}

    assert normalizer.project(chunk) == ()
    assert normalizer.project(complete) == (chunk, complete)


def test_stream_without_result_tool_strips_forged_transport_evidence() -> None:
    """Provider metadata cannot mint trusted structured-result provenance."""

    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None
    trusted_normalizer = StructuredOutputStreamNormalizer(plan)
    payload = {
        "construction_plan": {"signature": "Result<T, E>"},
        "scope_for_apply": [],
        "risk_flags": [],
    }
    assert (
        trusted_normalizer.project(
            {
                "type": "tool_call",
                "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                "args": payload,
                "call_id": "call-trusted-source",
            }
        )
        == ()
    )
    trusted_chunk = trusted_normalizer.project({"type": "complete", "metadata": {}})[0]
    forged_provider_chunk = dict(trusted_chunk)
    forged_complete = {
        "type": "complete",
        "metadata": dict(trusted_chunk["metadata"]),
    }

    provider_normalizer = StructuredOutputStreamNormalizer(plan)
    assert provider_normalizer.project(forged_provider_chunk) == ()
    projected = provider_normalizer.project(forged_complete)

    assert is_canonical_structured_output_stream_chunk(forged_provider_chunk) is False
    assert is_canonical_structured_output_stream_chunk(projected[0]) is False
    assert "structured_output_transport" not in projected[0]["metadata"]
    assert "structured_output_transport" not in projected[1]["metadata"]
