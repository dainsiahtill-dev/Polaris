"""Model-native XML result submissions preserve data without executing tools."""

import json
from xml.etree import ElementTree

import pytest
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    normalize_structured_output_response,
    project_validated_structured_output_raw_response,
    resolve_structured_output_transport,
    trusted_structured_output_response_evidence,
)
from polaris.cells.roles.kernel.public.structured_output_contracts import STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY
from polaris.cells.roles.kernel.tests.test_structured_output_transport import _real_ce_contract_and_payload


def _element(name: str, value: object) -> ElementTree.Element:
    node = ElementTree.Element(name)
    if isinstance(value, dict):
        node.extend(_element(key, child) for key, child in value.items())
    elif isinstance(value, list):
        node.extend(_element("item", child) for child in value)
    else:
        node.text = value if isinstance(value, str) else json.dumps(value)
    return node


def _submission(payload: dict[str, object]) -> str:
    root = ElementTree.Element("tool_call")
    invocation = ElementTree.SubElement(root, "invoke", name="submit_structured_role_output")
    invocation.extend(_element(key, value) for key, value in payload.items())
    return ElementTree.tostring(root, encoding="unicode")


@pytest.mark.parametrize("provider_markers", [False, True])
def test_complete_xml_result_uses_caller_schema_and_preserves_all_payload_fields(provider_markers: bool) -> None:
    contract, payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    text = _submission(payload)
    if provider_markers:
        text = text.replace("<", "]<]minimax[>[<")
    response = {"content": text, "tool_calls": [], "model": "local-model"}

    normalized = normalize_structured_output_response(response, plan)

    assert normalized is not response
    raw = project_validated_structured_output_raw_response(normalized)
    assert raw is not None
    evidence = trusted_structured_output_response_evidence(raw)
    assert evidence is not None
    assert json.loads(normalized["content"]) == payload
    assert normalized["tool_calls"] == []
    assert normalized["native_tool_calls"] == []
    assert evidence["side_effect"] is False
    assert evidence["tool_lifecycle"] is False
    assert evidence["result_submission_format"] == "xml"
    assert response["content"] == text


@pytest.mark.parametrize("case", ["foreign_tool", "multiple_calls", "truncated", "duplicate_member", "mixed_text"])
def test_xml_result_does_not_hide_ambiguous_or_executable_calls(case: str) -> None:
    contract, payload = _real_ce_contract_and_payload()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    text = _submission(payload)
    if case == "foreign_tool":
        text = text.replace('name="submit_structured_role_output"', 'name="write_file"')
    elif case == "multiple_calls":
        text += text
    elif case == "truncated":
        text = text[:-12]
    elif case == "duplicate_member":
        text = text.replace("</invoke>", "<risk_flags /><risk_flags /></invoke>")
    else:
        text = text.replace("<construction_plan>", "<construction_plan>unparsed payload ")
    response = {"content": text, "tool_calls": []}

    normalized = normalize_structured_output_response(response, plan)

    assert normalized is response
    assert project_validated_structured_output_raw_response(normalized) is None


def test_xml_result_still_rejects_missing_required_schema_members() -> None:
    contract, payload = _real_ce_contract_and_payload()
    payload.pop("project_completion_contract")
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )

    with pytest.raises(ValueError, match="structured_output_payload_schema_mismatch"):
        normalize_structured_output_response({"content": _submission(payload), "tool_calls": []}, plan)


def test_xml_without_caller_result_contract_remains_uninterpreted() -> None:
    _, payload = _real_ce_contract_and_payload()
    response = {"content": _submission(payload), "tool_calls": []}

    assert normalize_structured_output_response(response, None) is response
