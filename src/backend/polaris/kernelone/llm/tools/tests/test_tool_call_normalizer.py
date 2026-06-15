"""Tests for LLM tool-call normalization."""

from __future__ import annotations

from polaris.kernelone.llm.contracts.tool import ToolCall
from polaris.kernelone.llm.tools.normalizer import normalize_tool_calls


def test_normalize_tool_calls_accepts_registered_name_variants() -> None:
    calls = [
        ToolCall(
            id="call_read",
            name="fs.read_file",
            arguments={"path": "sample.txt"},
            source="test",
        )
    ]

    normalized = normalize_tool_calls(calls)

    assert len(normalized) == 1
    assert normalized[0].name == "read_file"
    assert normalized[0].arguments["file"] == "sample.txt"


def test_normalize_tool_calls_drops_unknown_namespaced_tools() -> None:
    calls = [
        ToolCall(
            id="call_unknown",
            name="fs.delete_everything",
            arguments={},
            source="test",
        )
    ]

    assert normalize_tool_calls(calls) == []
