"""OutputParser executable tool-call parsing is native-tool-only.

This file used to pin JSON/text fallback execution. That behavior is retired:
assistant text may still be sanitized or audited elsewhere, but executable
tool calls must come from provider-native tool call payloads so dispatch can be
accounted for by the tool lifecycle receipt chain.
"""

from __future__ import annotations

import json

from polaris.cells.roles.kernel.internal.output_parser import OutputParser, ToolCallResult


def test_tool_call_result_canonicalizes_create_file_alias() -> None:
    result = ToolCallResult(
        tool="create_file",
        args={"path": "src/app.py", "content": "print('ok')\n"},
    )

    assert result.tool == "write_file"
    assert result.name == "write_file"
    canonical = result.to_canonical()
    assert canonical.name == "write_file"
    assert canonical.arguments == {
        "file": "src/app.py",
        "content": "print('ok')\n",
    }


def test_native_openai_tool_calls_are_executable() -> None:
    parser = OutputParser()
    result = parser.parse_execution_tool_calls(
        content='{"name": "read_file", "arguments": {"path": "fallback.txt"}}',
        native_tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "README.md"})},
            }
        ],
        native_provider="openai",
    )

    assert len(result) == 1
    assert result[0].tool == "read_file"
    assert result[0].args == {"path": "README.md"}
    assert result[0].to_canonical().source == "native_tool_call"


def test_native_anthropic_tool_calls_are_executable() -> None:
    parser = OutputParser()
    result = parser.parse_execution_tool_calls(
        content="",
        native_tool_calls=[
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "write_file",
                "input": {"file": "src/app.py", "content": "print('ok')\n"},
            }
        ],
        native_provider="anthropic",
    )

    assert len(result) == 1
    assert result[0].tool == "write_file"
    assert result[0].args == {"file": "src/app.py", "content": "print('ok')\n"}
    assert result[0].to_canonical().source == "native_tool_call"


def test_json_text_is_not_executable_without_native_tool_calls() -> None:
    parser = OutputParser()
    result = parser.parse_execution_tool_calls(
        content='{"name": "read_file", "arguments": {"path": "test.py"}}',
    )

    assert result == []


def test_textual_tool_recovery_is_not_executable_without_native_tool_calls() -> None:
    parser = OutputParser()
    result = parser.parse_execution_tool_calls(
        content='<|tool_call>call:repo_read_head{file:<|"|>src/utils/helpers.py<|"|>,n:50}<tool_call|>',
    )

    assert result == []


def test_allowed_tool_names_filter_native_calls() -> None:
    parser = OutputParser()
    result = parser.parse_execution_tool_calls(
        content="",
        allowed_tool_names=["write_file"],
        native_tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "README.md"})},
            }
        ],
    )

    assert result == []


def test_plain_text_yields_no_tool_calls() -> None:
    parser = OutputParser()
    assert parser.parse_execution_tool_calls(content="Just a normal answer.") == []
