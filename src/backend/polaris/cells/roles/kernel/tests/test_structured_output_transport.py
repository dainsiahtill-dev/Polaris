"""Provider-tool structured output stays a protocol transport, never an effect."""

from __future__ import annotations

import json
import logging
from copy import deepcopy

import pytest
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
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


def _real_ce_contract_and_payload() -> tuple[RoleStructuredOutputContractV1, dict[str, object]]:
    """Use Factory's real dynamic CE schema, including completion obligations."""

    task_ids = ("TASK-1", "TASK-2", "TASK-3")
    contract = OrchestrationStageExecutor._chief_engineer_structured_output_contract(task_ids)
    payload: dict[str, object] = {
        "construction_plan": {
            "task_plans": {
                task_id: {
                    "summary": f"Blueprint for {task_id}",
                    "behavior_invariant_refs": ["INV-1"],
                }
                for task_id in task_ids
            },
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-1",
                        "statement": "The entrypoint and verification tasks observe the same result.",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-2", "TASK-3"],
                        "covered_obligation_ids": ["artifact-main", "verify-build"],
                        "verification_examples": [
                            {
                                "given": "the entrypoint is built",
                                "when": "the verifier runs it",
                                "then": "the declared result is observed",
                            }
                        ],
                    }
                ]
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "artifact-main",
                        "path": "src/main.ts",
                        "semantic_role": "entrypoint",
                        "applicability": "required",
                        "owner_task_id": "TASK-1",
                    }
                ],
                "entrypoints": [
                    {
                        "obligation_id": "entrypoint-main",
                        "kind": "cli",
                        "applicability": "required",
                        "owner_task_id": "TASK-1",
                        "source_path": "src/main.ts",
                        "runtime_path": None,
                        "command": "node dist/main.js",
                    }
                ],
                "verification": [
                    {
                        "obligation_id": "verify-build",
                        "modality": "build",
                        "command_authority_hash": None,
                        "applicability": "required",
                        "owner_task_id": "TASK-3",
                        "covers_obligation_ids": ["artifact-main"],
                    }
                ],
            }
        },
        "scope_for_apply": [],
        "risk_flags": [],
    }
    return contract, payload


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


def _displaced_portfolio_contract() -> RoleStructuredOutputContractV1:
    return RoleStructuredOutputContractV1(
        schema_name="displaced_portfolio",
        description="Submit one nested portfolio.",
        json_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "construction_plan": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "task_plans": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "TASK-1": {"type": "object"},
                                "TASK-2": {"type": "object"},
                            },
                            "required": ["TASK-1", "TASK-2"],
                        },
                        "project_interface_contract": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "provider_declarations": {"type": "array"},
                                "consumer_declarations": {"type": "array"},
                            },
                            "required": ["provider_declarations", "consumer_declarations"],
                        },
                        "shared_behavior_contract": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"invariants": {"type": "array"}},
                            "required": ["invariants"],
                        },
                    },
                    "required": ["task_plans", "project_interface_contract", "shared_behavior_contract"],
                },
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "risk_flags"],
        },
    )


def test_schema_proven_displaced_root_members_recover_without_provider_retry() -> None:
    """L3-22: preserve and rehome complete CE members leaked from a nested object."""

    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _displaced_portfolio_contract().to_context_projection()}
    )
    assert plan is not None
    malformed = {
        "construction_plan": {
            "project_interface_contract": {"consumer_declarations": [{"consumer_file": "main.go"}]}
        },
        "task_plans": {"TASK-1": {"summary": "models"}},
        "TASK-2": {"summary": "tests"},
        "provider_declarations": [{"owner_file": "models/model.go"}],
        "shared_behavior_contract": {"invariants": [{"invariant_id": "INV-1"}]},
        "risk_flags": [],
    }

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": malformed,
                    "call_id": "call-displaced-root",
                }
            ],
        },
        plan,
    )

    payload = json.loads(normalized["content"])
    construction_plan = payload["construction_plan"]
    assert construction_plan["task_plans"] == {
        "TASK-1": {"summary": "models"},
        "TASK-2": {"summary": "tests"},
    }
    assert construction_plan["project_interface_contract"] == {
        "consumer_declarations": [{"consumer_file": "main.go"}],
        "provider_declarations": [{"owner_file": "models/model.go"}],
    }
    assert construction_plan["shared_behavior_contract"] == {
        "invariants": [{"invariant_id": "INV-1"}]
    }
    evidence = normalized["structured_output_transport"]
    assert evidence["schema_normalization_policy"] == "schema_proven_displaced_root_members_v1"
    assert evidence["schema_normalization_details"]["displaced_root_relocations"] == [
        "TASK-2->construction_plan.task_plans.TASK-2",
        "provider_declarations->construction_plan.project_interface_contract.provider_declarations",
        "shared_behavior_contract->construction_plan.shared_behavior_contract",
        "task_plans->construction_plan.task_plans",
    ]


def test_displaced_root_member_collision_remains_fail_closed() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _displaced_portfolio_contract().to_context_projection()}
    )
    assert plan is not None
    malformed = {
        "construction_plan": {
            "task_plans": {"TASK-1": {}, "TASK-2": {}},
            "project_interface_contract": {
                "provider_declarations": [{"owner_file": "canonical.go"}],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {"invariants": []},
        },
        "provider_declarations": [{"owner_file": "conflict.go"}],
        "risk_flags": [],
    }

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": malformed,
                        "call_id": "call-displaced-collision",
                    }
                ],
            },
            plan,
        )


def test_unknown_root_noise_without_schema_proven_relocation_remains_fail_closed() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _displaced_portfolio_contract().to_context_projection()}
    )
    assert plan is not None
    malformed = {
        "construction_plan": {
            "task_plans": {"TASK-1": {}, "TASK-2": {}},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {"invariants": []},
        },
        "untrusted_bypass": True,
        "risk_flags": [],
    }

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": malformed,
                        "call_id": "call-unknown-root-noise",
                    }
                ],
            },
            plan,
        )


def test_displaced_root_member_plus_arbitrary_residual_remains_fail_closed() -> None:
    """A valid relocation must not smuggle an unrelated member into an open object."""

    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _displaced_portfolio_contract().to_context_projection()}
    )
    assert plan is not None
    malformed = {
        "construction_plan": {
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {"invariants": []},
        },
        "task_plans": {"TASK-1": {}, "TASK-2": {}},
        "untrusted_bypass": {"scope_for_apply": ["outside/**"]},
        "risk_flags": [],
    }

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": malformed,
                        "call_id": "call-displaced-plus-residual",
                    }
                ],
            },
            plan,
        )


def test_required_defaults_compose_with_displaced_root_recovery() -> None:
    """Missing empty root containers are defaulted before strict relocation proof."""

    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _displaced_portfolio_contract().to_context_projection()}
    )
    assert plan is not None
    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": {
                        "construction_plan": {
                            "project_interface_contract": {
                                "provider_declarations": [],
                                "consumer_declarations": [],
                            },
                            "shared_behavior_contract": {"invariants": []},
                        },
                        "task_plans": {"TASK-1": {}, "TASK-2": {}},
                    },
                    "call_id": "call-displaced-with-defaults",
                }
            ],
        },
        plan,
    )

    payload = json.loads(normalized["content"])
    assert payload["risk_flags"] == []
    assert payload["construction_plan"]["task_plans"] == {"TASK-1": {}, "TASK-2": {}}
    assert normalized["structured_output_transport"]["schema_normalization_policy"] == (
        "schema_proven_displaced_root_members_v1+required_empty_container_defaults_v1"
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


def test_nested_required_empty_object_is_coerced_without_provider_retry() -> None:
    """A missing advisory CE interface object is an empty-container omission."""

    contract, payload = _real_ce_contract_and_payload()
    construction_plan = dict(payload["construction_plan"])
    construction_plan.pop("project_interface_contract")
    payload["construction_plan"] = construction_plan
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": payload,
                    "call_id": "call-missing-nested-empty-object",
                }
            ],
        },
        plan,
    )

    projected = json.loads(normalized["content"])
    assert projected["construction_plan"]["project_interface_contract"] == {}
    assert normalized["structured_output_transport"]["schema_normalization_applied"] is True
    assert (
        "required_empty_container_defaults_v1"
        in normalized["structured_output_transport"]["schema_normalization_policy"]
    )


def test_nested_required_non_empty_obligations_remain_fail_closed() -> None:
    """Recursive empty defaults never bypass minItems delivery obligations."""

    contract, payload = _real_ce_contract_and_payload()
    schema = deepcopy(contract.json_schema)
    obligations_schema = schema["properties"]["project_completion_contract"]["properties"]["obligations"]
    for obligation_schema in obligations_schema["properties"].values():
        obligation_schema["minItems"] = 1
    contract = RoleStructuredOutputContractV1(
        schema_name=contract.schema_name,
        description=contract.description,
        json_schema=schema,
    )
    payload["project_completion_contract"] = {"obligations": {}}
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None

    with pytest.raises(
        ValueError,
        match=r"structured_output_payload_schema_mismatch:project_completion_contract\.obligations:",
    ):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": payload,
                        "call_id": "call-missing-non-empty-obligations",
                    }
                ],
            },
            plan,
        )


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


def test_schema_proven_json_string_container_is_decoded_without_provider_retry() -> None:
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
                        "construction_plan": '{"task_plans": {}}',
                        "scope_for_apply": [],
                        "risk_flags": [],
                    },
                    "call_id": "call-stringified-container",
                }
            ],
        },
        plan,
    )

    payload = json.loads(normalized["content"])
    assert payload["construction_plan"] == {"task_plans": {}}
    evidence = normalized["structured_output_transport"]
    assert evidence["schema_normalization_applied"] is True
    assert evidence["schema_normalization_policy"] == "schema_proven_json_container_v1"


def test_schema_proven_root_fragment_recovers_overescaped_provider_envelope() -> None:
    """L1-01 r17: DeepSeek serialized all root siblings into construction_plan."""

    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None
    root_fragment = r"""{"task_plans":{"TASK-1":{"signature":"describe(\'garden\')","html":"<canvas id=\\"garden\\"></canvas>"}}}, "scope_for_apply": [], "risk_flags": []}"""

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": {"construction_plan": root_fragment},
                    "call_id": "call-root-fragment",
                }
            ],
        },
        plan,
    )

    payload = json.loads(normalized["content"])
    assert payload["construction_plan"]["task_plans"]["TASK-1"] == {
        "signature": "describe('garden')",
        "html": '<canvas id="garden"></canvas>',
    }
    assert payload["scope_for_apply"] == []
    assert payload["risk_flags"] == []
    evidence = normalized["structured_output_transport"]
    assert evidence["schema_normalization_applied"] is True
    assert evidence["schema_normalization_policy"] == "schema_proven_root_fragment_v1"


def test_root_fragment_recovery_remains_fail_closed_for_unknown_root_property() -> None:
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
                            "construction_plan": (
                                '{"task_plans": {}}, "scope_for_apply": [], "risk_flags": [], "untrusted_extra": true}'
                            )
                        },
                        "call_id": "call-unknown-root-property",
                    }
                ],
            },
            plan,
        )


@pytest.mark.parametrize(
    ("existing_value", "fragment_value"),
    [
        ([1], [True]),
        ([1], [1.0]),
        ([{"nested": [1]}], [{"nested": [True]}]),
    ],
)
def test_root_fragment_recovery_rejects_type_distinct_sibling_overwrite(
    existing_value: list[object],
    fragment_value: list[object],
) -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None
    fragment = (
        '{"task_plans": {}}, "scope_for_apply": [], "risk_flags": '
        + json.dumps(fragment_value, separators=(",", ":"))
        + "}"
    )

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": {
                            "construction_plan": fragment,
                            "risk_flags": existing_value,
                        },
                        "call_id": "call-conflicting-sibling",
                    }
                ],
            },
            plan,
        )


def test_root_fragment_recovery_rejects_duplicate_json_members() -> None:
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
                            "construction_plan": (
                                '{"task_plans": {}}, "scope_for_apply": [], '
                                '"risk_flags": [], "risk_flags": ["ambiguous"]}'
                            )
                        },
                        "call_id": "call-duplicate-root-member",
                    }
                ],
            },
            plan,
        )


def test_r17_root_fragment_recovers_under_factory_ce_schema() -> None:
    contract, expected_payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    serialized = json.dumps(expected_payload, ensure_ascii=False, separators=(",", ":"))
    fragment = serialized.removeprefix('{"construction_plan":')

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": {"construction_plan": fragment},
                    "call_id": "call-r17-real-ce-schema",
                }
            ],
        },
        plan,
    )

    assert json.loads(normalized["content"]) == expected_payload
    assert normalized["structured_output_transport"]["schema_normalization_policy"] == (
        "schema_proven_root_fragment_v1"
    )


def test_real_ce_schema_removes_provider_text_noise_only_from_closed_object() -> None:
    """L1-04 r50: MiniMax added XML-style $text to one strict artifact row."""

    contract, expected_payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    provider_payload = deepcopy(expected_payload)
    completion = provider_payload["project_completion_contract"]
    assert isinstance(completion, dict)
    obligations = completion["obligations"]
    assert isinstance(obligations, dict)
    artifacts = obligations["artifacts"]
    assert isinstance(artifacts, list)
    assert isinstance(artifacts[0], dict)
    artifacts[0]["$text"] = "provider-envelope-noise"

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": provider_payload,
                    "call_id": "call-closed-object-text-noise",
                }
            ],
        },
        plan,
    )

    assert json.loads(normalized["content"]) == expected_payload
    evidence = normalized["structured_output_transport"]
    assert evidence["schema_normalization_applied"] is True
    assert evidence["schema_normalization_policy"] == "schema_proven_closed_object_text_noise_v1"


def test_real_ce_schema_keeps_other_unknown_nested_members_fail_closed() -> None:
    contract, payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    completion = payload["project_completion_contract"]
    assert isinstance(completion, dict)
    obligations = completion["obligations"]
    assert isinstance(obligations, dict)
    artifacts = obligations["artifacts"]
    assert isinstance(artifacts, list)
    assert isinstance(artifacts[0], dict)
    artifacts[0]["untrusted_extra"] = "must-not-be-hidden"

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": payload,
                        "call_id": "call-unknown-nested-member",
                    }
                ],
            },
            plan,
        )


def test_schema_mismatch_logs_only_unknown_root_key_types(caplog: pytest.LogCaptureFixture) -> None:
    """Live diagnostics expose envelope shape without leaking project payload."""

    contract, payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    payload["TASK-3"] = {"secret_project_content": "must-not-enter-log"}

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(
            ValueError,
            match="structured_output_payload_schema_mismatch",
        ),
    ):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": payload,
                        "call_id": "call-unknown-root-shape",
                    }
                ],
            },
            plan,
        )

    assert 'unknown_root_shape={"TASK-3": "dict"}' in caplog.text
    assert "must-not-enter-log" not in caplog.text
    assert "secret_project_content" not in caplog.text


def test_r17_root_fragment_remains_fail_closed_when_real_ce_obligations_are_missing() -> None:
    contract, payload = _real_ce_contract_and_payload()
    payload.pop("project_completion_contract")
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    fragment = serialized.removeprefix('{"construction_plan":')

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": {"construction_plan": fragment},
                        "call_id": "call-r17-missing-project-completion-contract",
                    }
                ],
            },
            plan,
        )


def test_stream_root_fragment_recovery_projects_trusted_normalization_evidence() -> None:
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: _contract().to_context_projection()}
    )
    assert plan is not None
    normalizer = StructuredOutputStreamNormalizer(plan)

    assert (
        normalizer.project(
            {
                "type": "tool_call",
                "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                "args": {"construction_plan": ('{"task_plans": {}}, "scope_for_apply": [], "risk_flags": []}')},
                "call_id": "call-stream-root-fragment",
            }
        )
        == ()
    )
    projected = normalizer.project({"type": "complete", "content": ""})

    assert len(projected) == 2
    payload = json.loads(projected[0]["content"])
    assert payload == {
        "construction_plan": {"task_plans": {}},
        "risk_flags": [],
        "scope_for_apply": [],
    }
    evidence = projected[0]["metadata"]["structured_output_transport"]
    assert evidence["schema_normalization_applied"] is True
    assert evidence["schema_normalization_policy"] == "schema_proven_root_fragment_v1"


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


def test_real_ce_schema_unwraps_minimax_item_wrapper_on_declared_arrays() -> None:
    """L2-11 r02: MiniMax wrapped CE arrays as {"item": [...]} / {"item": "x"}."""

    contract, expected_payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    task_plan_schema = contract.json_schema["properties"]["construction_plan"]["properties"]["task_plans"][
        "properties"
    ]["TASK-1"]
    assert task_plan_schema["properties"]["scope_for_apply"]["type"] == "array"

    provider_payload = deepcopy(expected_payload)
    construction_plan = provider_payload["construction_plan"]
    assert isinstance(construction_plan, dict)
    task_plans = construction_plan["task_plans"]
    assert isinstance(task_plans, dict)
    task_plans["TASK-1"] = {
        "summary": "Blueprint for TASK-1",
        "scope_for_apply": {"item": ["src/main.ts", "package.json"]},
        "risk_flags": {"items": ["shared-manifest"]},
    }
    completion = provider_payload["project_completion_contract"]
    assert isinstance(completion, dict)
    obligations = completion["obligations"]
    assert isinstance(obligations, dict)
    verification = obligations["verification"]
    assert isinstance(verification, list)
    assert isinstance(verification[0], dict)
    verification[0]["covers_obligation_ids"] = {"item": "artifact-main"}

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": provider_payload,
                    "call_id": "call-item-wrapper",
                }
            ],
        },
        plan,
    )

    projected = json.loads(normalized["content"])
    assert projected["construction_plan"]["task_plans"]["TASK-1"]["scope_for_apply"] == [
        "src/main.ts",
        "package.json",
    ]
    assert projected["construction_plan"]["task_plans"]["TASK-1"]["risk_flags"] == ["shared-manifest"]
    assert projected["project_completion_contract"]["obligations"]["verification"][0]["covers_obligation_ids"] == [
        "artifact-main"
    ]
    evidence = normalized["structured_output_transport"]
    assert evidence["schema_normalization_applied"] is True
    assert "schema_proven_singleton_item_wrapper_v1" in evidence["schema_normalization_policy"]


def test_item_wrapper_with_extra_keys_remains_fail_closed() -> None:
    contract, payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    construction_plan = payload["construction_plan"]
    assert isinstance(construction_plan, dict)
    task_plans = construction_plan["task_plans"]
    assert isinstance(task_plans, dict)
    task_plans["TASK-1"] = {
        "summary": "Blueprint for TASK-1",
        "scope_for_apply": {"item": ["src/main.ts"], "extra": True},
    }

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response(
            {
                "content": "",
                "tool_calls": [
                    {
                        "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": payload,
                        "call_id": "call-item-wrapper-extra-key",
                    }
                ],
            },
            plan,
        )


def test_item_wrapper_is_not_applied_to_object_fields() -> None:
    """Object-typed fields keep {item:...}; transport must not invent a plan there."""

    contract, payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    original_plan = payload["construction_plan"]
    assert isinstance(original_plan, dict)
    payload["construction_plan"] = {
        "item": original_plan,
        "project_interface_contract": original_plan["project_interface_contract"],
        "shared_behavior_contract": original_plan["shared_behavior_contract"],
    }

    normalized = normalize_structured_output_response(
        {
            "content": "",
            "tool_calls": [
                {
                    "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                    "args": payload,
                    "call_id": "call-object-item-wrapper",
                }
            ],
        },
        plan,
    )

    projected = json.loads(normalized["content"])
    assert projected["construction_plan"]["item"] == original_plan
    assert projected["construction_plan"].get("task_plans") != original_plan.get("task_plans")
    assert "schema_proven_singleton_item_wrapper_v1" not in str(
        normalized["structured_output_transport"]["schema_normalization_policy"]
    )
