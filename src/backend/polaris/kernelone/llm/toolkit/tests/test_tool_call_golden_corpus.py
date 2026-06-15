"""Golden provider payloads for parser + argument normalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.llm.toolkit.parsers import NativeFunctionCallingParser, ParsedToolCall, parse_tool_calls
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments

_CORPUS_PATH = Path(__file__).with_name("golden_tool_call_inputs.json")


def _load_cases() -> list[dict[str, Any]]:
    with _CORPUS_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    cases = payload.get("cases", [])
    assert isinstance(cases, list)
    return [case for case in cases if isinstance(case, dict)]


def _parse_case(case: dict[str, Any]) -> list[ParsedToolCall]:
    provider = str(case["provider"])
    payload = case["payload"]
    if provider == "openai":
        assert isinstance(payload, list)
        return NativeFunctionCallingParser.parse_openai(payload)
    if provider == "deepseek":
        assert isinstance(payload, dict)
        return NativeFunctionCallingParser.parse_deepseek(payload)
    if provider == "anthropic":
        assert isinstance(payload, list)
        return NativeFunctionCallingParser.parse_anthropic(payload)
    if provider == "azure":
        assert isinstance(payload, dict)
        return NativeFunctionCallingParser.parse_azure_openai(payload)
    if provider == "gemini":
        assert isinstance(payload, dict)
        return NativeFunctionCallingParser.parse_gemini(payload)
    if provider == "cohere":
        assert isinstance(payload, dict)
        return NativeFunctionCallingParser.parse_cohere(payload)
    if provider == "bedrock":
        assert isinstance(payload, dict)
        return NativeFunctionCallingParser.parse_bedrock_claude(payload)
    if provider == "text":
        assert isinstance(payload, str)
        allowed = case.get("allowed_tools")
        assert allowed is None or isinstance(allowed, list)
        return parse_tool_calls(text=payload, allowed_tool_names=allowed)
    raise AssertionError(f"Unsupported corpus provider: {provider}")


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: str(case["id"]))
def test_provider_payload_normalizes_to_canonical_arguments(case: dict[str, Any]) -> None:
    parsed = _parse_case(case)

    assert len(parsed) == 1
    call = parsed[0]
    assert call.name == case["expected_name"]

    normalized = normalize_tool_arguments(call.name, call.arguments)
    expected = case["expected_arguments"]
    assert isinstance(expected, dict)
    for key, value in expected.items():
        assert normalized.get(key) == value
    for key in case.get("forbidden_keys", []):
        assert key not in normalized
