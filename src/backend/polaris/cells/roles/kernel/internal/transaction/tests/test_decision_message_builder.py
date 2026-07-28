"""Final decision-message truth must match the physical Provider tool schema."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    resolve_structured_output_transport,
)
from polaris.cells.roles.kernel.internal.transaction.decision_message_builder import (
    build_decision_messages,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)


def _structured_result_tool() -> dict[str, object]:
    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete blueprint portfolio.",
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
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    return plan.tool_definition


def test_decision_prompt_projects_exact_current_turn_tool_schema() -> None:
    context = [
        {
            "role": "system",
            "content": "Allowed tools: read_file, write_file, execute_command",
        },
        {
            "role": "user",
            "content": "Create main.go and verify that the project builds successfully.",
        },
    ]
    tools = [{"type": "function", "function": {"name": "write_file"}}]

    messages = build_decision_messages(context, tools)
    rendered = "\n".join(str(message.get("content") or "") for message in messages)

    assert "CURRENT TURN PHYSICAL TOOL SCHEMA: write_file" in rendered
    assert "supersedes broader role-capability lists" in rendered
    assert "Tools not listed here are unavailable in this Provider request" in rendered
    assert "Include an available verification step" not in rendered
    assert "No verification tool is exposed in this physical request" in rendered


def test_materialize_prompt_does_not_reinvent_unavailable_verification_tool() -> None:
    context = [
        {
            "role": "user",
            "content": "Create main.go and verify that the project builds successfully.",
        }
    ]
    tools = [{"type": "function", "function": {"name": "write_file"}}]
    ledger = SimpleNamespace(delivery_contract=SimpleNamespace(mode=DeliveryMode.MATERIALIZE_CHANGES))

    messages = build_decision_messages(context, tools, ledger=ledger)  # type: ignore[arg-type]
    rendered = "\n".join(str(message.get("content") or "") for message in messages)

    assert "verification tools for this turn have been emitted" not in rendered
    assert "Verification remains mandatory in a later governed continuation or quality phase" in rendered


def test_structured_result_protocol_omits_mutation_execution_contracts() -> None:
    """A provider result tool is an output protocol, never a workspace mutation surface."""

    context = [
        {
            "role": "system",
            "content": "You are Polaris Chief Engineer. Produce a coherent blueprint portfolio.",
            "metadata": {"role_id": "chief_engineer"},
        },
        {
            "role": "user",
            "content": (
                "Return the complete Chief Engineer portfolio for TASK-1 through TASK-3. "
                "The downstream Director will implement src/main.rs and tests."
            ),
        },
    ]
    tools = [_structured_result_tool()]

    messages = build_decision_messages(context, tools)
    rendered = "\n".join(str(message.get("content") or "") for message in messages)

    assert "SYSTEM CONSTRAINT (Structured Result)" in rendered
    assert "Call submit_structured_role_output exactly once" in rendered
    assert "CURRENT TURN PHYSICAL TOOL SCHEMA: submit_structured_role_output" in rendered
    assert "SYSTEM CONSTRAINT (Execution)" not in rendered
    assert "TASK CONTRACT (single-batch planning)" not in rendered
    assert "This request requires mutation" not in rendered
    assert "POSITIVE TOOL SEQUENCE TEMPLATES" not in rendered


def test_mixed_structured_result_and_executable_surface_fails_closed() -> None:
    """The reserved result protocol cannot coexist with executable tools."""

    context = [
        {
            "role": "user",
            "content": "Create src/main.rs, then return the structured Chief Engineer result.",
        }
    ]
    tools = [
        _structured_result_tool(),
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a workspace file.",
            },
        },
    ]

    with pytest.raises(ValueError, match="structured_output_tool_surface_must_be_exact_singleton"):
        build_decision_messages(context, tools)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "tools,error",
    [
        (
            [_structured_result_tool(), _structured_result_tool()],
            "structured_output_tool_surface_must_be_exact_singleton",
        ),
        (
            [{"name": "submit_structured_role_output"}],
            "structured_output_tool_definition_envelope_malformed",
        ),
        (
            [
                {
                    "type": "function",
                    "function": {
                        "name": "submit_structured_role_output",
                        "description": (
                            "Submit. Call this result-submission tool exactly once. "
                            "It records no side effect and is not an executable workspace tool."
                        ),
                        "parameters": {"type": "array"},
                        "strict": True,
                    },
                }
            ],
            "structured_output_tool_parameters_must_be_object_schema",
        ),
    ],
)
def test_malformed_structured_result_surface_fails_closed(
    tools: list[dict[str, object]],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        build_decision_messages(
            [{"role": "user", "content": "Submit the result."}],
            tools,  # type: ignore[arg-type]
        )
