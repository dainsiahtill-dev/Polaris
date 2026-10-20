"""Physical loopback wire regression for native tool-only continuation history.

The synthetic server replaces model configuration only. The production client,
provider adapter, message normalization, and HTTP serialization remain real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.infrastructure.llm.providers.provider_helpers import build_chat_messages_payload
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.toolkit.parsers.native_function import NativeFunctionCallingParser

_MESSAGES = [
    {"role": "system", "content": "You are the synthetic Director."},
    {"role": "user", "content": "Inspect the same file twice."},
]
_IDS = ["inspect-first", "inspect-second"]
_ARGS = {"file": "你好.txt"}
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 file.",
            "parameters": {"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]},
        },
    }
]


def _request(messages: list[dict[str, Any]]) -> AIRequest:
    return AIRequest(
        task_type=TaskType.DIALOGUE,
        role="engineering_request_history_test",
        provider_id="synthetic",
        model="synthetic-model",
        input="Fallback prompt must not replace native history.",
        options={"messages": messages, "tools": _TOOLS, "tool_choice": "auto", "max_tokens": 512, "timeout": 3},
    )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("assistant_content", [None, ""])
@pytest.mark.parametrize("result_content", ["", "observed UTF-8: 你好"])
async def test_physical_continuation_keeps_tool_only_assistant_and_each_result(
    protocol: str,
    assistant_content: str | None,
    result_content: str,
    scripted_provider: ScriptedProvider,
    isolated_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = scripted_provider.client(protocol, isolated_workspace, monkeypatch)
    native_calls = [
        {
            "id": identity,
            "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps(_ARGS, ensure_ascii=False)},
        }
        for identity in _IDS
    ]
    if protocol == "openai":
        scripted_provider.enqueue_json(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": assistant_content, "tool_calls": native_calls},
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        scripted_provider.enqueue_json(
            {"choices": [{"message": {"role": "assistant", "content": "History received."}, "finish_reason": "stop"}]}
        )
    else:
        scripted_provider.enqueue_json(
            {
                "id": "native-history",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": identity, "name": "read_file", "input": _ARGS} for identity in _IDS
                ],
                "stop_reason": "tool_use",
            }
        )
        scripted_provider.enqueue_json(
            {
                "id": "native-final",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "History received."}],
                "stop_reason": "end_turn",
            }
        )
    first = await client.invoke(_request(list(_MESSAGES)))
    assert first.ok, first.error
    assert isinstance(first.raw, dict)
    if protocol == "openai":
        calls = NativeFunctionCallingParser.parse_openai(first.raw["choices"][0]["message"]["tool_calls"])
    else:
        calls = NativeFunctionCallingParser.parse_anthropic(first.raw["content"])
    assert [call.id for call in calls] == _IDS
    assert [call.arguments for call in calls] == [_ARGS, _ARGS]
    assistant = {"role": "assistant", "content": assistant_content, "tool_calls": native_calls}
    results = [{"role": "tool", "tool_call_id": identity, "content": result_content} for identity in _IDS]
    second = await client.invoke(_request([*_MESSAGES, assistant, *results]))
    assert second.ok, second.error
    assert second.output == "History received."
    assert len(scripted_provider.requests) == 2
    body = scripted_provider.requests[1]["body"]
    if protocol == "openai":
        assert body["messages"] == [*_MESSAGES, {**assistant, "content": ""}, *results]
    else:
        assert body["system"] == "You are the synthetic Director."
        assert body["messages"] == [
            {"role": "user", "content": "Inspect the same file twice."},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "inspect-first", "name": "read_file", "input": {"file": "你好.txt"}},
                    {"type": "tool_use", "id": "inspect-second", "name": "read_file", "input": {"file": "你好.txt"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "inspect-first", "content": result_content},
                    {"type": "tool_result", "tool_use_id": "inspect-second", "content": result_content},
                ],
            },
        ]


def test_blank_plain_messages_keep_fallback_and_text_is_not_a_native_call() -> None:
    assert build_chat_messages_payload(
        [{"role": "assistant", "content": ""}, {"role": "user", "content": "   "}], "fallback"
    ) == [{"role": "user", "content": "fallback"}]
    assert build_chat_messages_payload(
        [
            {"role": "user", "content": "Keep me."},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": 'read_file({"file":"你好.txt"})'},
        ],
        "fallback",
    ) == [{"role": "user", "content": 'Keep me.\n\nread_file({"file":"你好.txt"})'}]
