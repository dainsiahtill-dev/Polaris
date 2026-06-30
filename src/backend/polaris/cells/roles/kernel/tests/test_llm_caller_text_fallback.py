"""Native-only tool call extraction regression tests."""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import extract_native_tool_calls


def test_openai_native_tool_calls_are_extracted() -> None:
    raw = {
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "src/app.py"}'},
            }
        ]
    }

    calls, provider = extract_native_tool_calls(
        raw,
        provider_id="openai",
        model="gpt-5.3",
        response_text='{"name": "write_file", "arguments": {"path": "ignored.py"}}',
    )

    assert calls == raw["tool_calls"]
    assert provider == "openai"


def test_anthropic_native_tool_use_blocks_are_extracted() -> None:
    raw = {
        "content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_1", "name": "repo_tree", "input": {"path": "."}},
        ]
    }

    calls, provider = extract_native_tool_calls(
        raw,
        provider_id="anthropic",
        model="claude-sonnet",
        response_text=None,
    )

    assert calls == [raw["content"][1]]
    assert provider == "anthropic"


def test_json_text_is_not_promoted_to_native_tool_call() -> None:
    raw: dict[str, Any] = {}

    calls, provider = extract_native_tool_calls(
        raw,
        provider_id="openai",
        model="gpt-5.3",
        response_text='{"name": "read_file", "arguments": {"path": "src/app.py"}}',
    )

    assert calls == []
    assert provider == "openai"


def test_textual_function_protocol_is_not_promoted_to_native_tool_call() -> None:
    raw: dict[str, Any] = {}
    response_text = (
        "<function=write_file>"
        "<parameter=path>src/app.py</parameter>"
        "<parameter=text>print('unsafe')</parameter>"
        "</function>"
    )

    calls, provider = extract_native_tool_calls(
        raw,
        provider_id="openai_compat-1780683130410",
        model="qwen3-coder",
        response_text=response_text,
    )

    assert calls == []
    assert provider == "openai"


def test_file_delivery_text_is_not_promoted_to_native_tool_call() -> None:
    raw: dict[str, Any] = {}

    calls, provider = extract_native_tool_calls(
        raw,
        provider_id="openai",
        model="gpt-5.3",
        response_text="""```file: package.json
{
  "name": "polaris-project"
}
```""",
    )

    assert calls == []
    assert provider == "openai"
