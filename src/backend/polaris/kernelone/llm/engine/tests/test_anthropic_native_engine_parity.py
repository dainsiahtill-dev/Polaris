"""Engine budgeting must retain executable native history before wire guards."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from polaris.kernelone.llm.engine.prompt_budget import compress_chat_messages_to_budget
from polaris.kernelone.llm.engine.provider_native_request import build_anthropic_native_messages


def _history() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are Director."},
        {"role": "user", "content": "Complete accepted task."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "denied-1",
                    "type": "function",
                    "function": {"name": "execute_command", "arguments": '{"command":"pwd && ls -la"}'},
                }
            ],
        },
        {
            "role": "tool",
            "name": "execute_command",
            "tool_call_id": "denied-1",
            "content": "[tool_failure_summary]\nDenied command. Use python verify.py.",
            "is_error": True,
        },
        {"role": "system", "content": "Current tool schema preserved."},
        {"role": "user", "content": "Continue same task."},
    ]


def test_engine_budget_keeps_correlated_denied_call_and_error_result() -> None:
    messages = _history()
    before = deepcopy(messages)
    budgeted = compress_chat_messages_to_budget(messages, 224000)
    native, system = build_anthropic_native_messages(budgeted)
    assert native == [
        {"role": "user", "content": "Complete accepted task."},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "denied-1", "name": "execute_command", "input": {"command": "pwd && ls -la"}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "denied-1",
                    "content": "[tool_failure_summary]\nDenied command. Use python verify.py.",
                    "is_error": True,
                }
            ],
        },
        {"role": "user", "content": "Continue same task."},
    ]
    assert system == "You are Director.\n\nCurrent tool schema preserved."
    assert messages == before
    assert budgeted is not messages
    assert budgeted is not None
    budgeted[2]["tool_calls"][0]["function"]["arguments"] = "changed"
    assert messages == before


def test_engine_budget_refuses_to_truncate_native_tool_history() -> None:
    messages = _history()
    messages[3]["content"] = "failed command evidence " * 1000
    with pytest.raises(ValueError, match="native_tool_history_budget_exceeded"):
        compress_chat_messages_to_budget(messages, 50)


def test_engine_budget_retains_existing_native_blocks() -> None:
    messages = [
        {"role": "user", "content": "Read accepted file."},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "read-1", "name": "read_file", "input": {"file": "answer.py"}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "read-1", "content": "VALUE = 42", "is_error": False}],
        },
    ]
    assert compress_chat_messages_to_budget(messages, 1000) == messages


def test_engine_budget_copies_multimodal_blocks_without_mutating_original() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Inspect image"},
                {"type": "image", "source": {"type": "base64", "data": "opaque"}},
            ],
        }
    ]
    before = deepcopy(messages)
    budgeted = compress_chat_messages_to_budget(messages, 1000)
    assert budgeted == before
    assert budgeted is not None
    budgeted[0]["content"][1]["source"]["data"] = "changed"
    assert messages == before


def test_engine_budget_refuses_partial_multimodal_block_truncation() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "long image context " * 1000},
                {"type": "image", "source": {"type": "base64", "data": "opaque"}},
            ],
        }
    ]
    with pytest.raises(ValueError, match="structured_message_content_budget_exceeded"):
        compress_chat_messages_to_budget(messages, 50)


@pytest.mark.parametrize("content", [None, "", " \n\t", []])
@pytest.mark.parametrize("is_error", [False, True])
def test_engine_budget_keeps_blank_correlated_native_tool_results(content: Any, is_error: bool) -> None:
    messages = _history()
    messages[3]["content"] = content
    messages[3]["is_error"] = is_error
    before = deepcopy(messages)
    budgeted = compress_chat_messages_to_budget(messages, 224000)
    expected = deepcopy(before)
    expected[3]["content"] = "" if content is None else content
    assert budgeted == expected
    native, _ = build_anthropic_native_messages(budgeted)
    tool_result = native[2]["content"][0]
    assert tool_result["tool_use_id"] == "denied-1"
    assert tool_result["content"] == expected[3]["content"]
    assert tool_result.get("is_error", False) is is_error
    assert messages == before


def test_engine_budget_still_omits_uncorrelated_ordinary_blank_text() -> None:
    messages = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": " \n\t"},
        {"role": "user", "content": "Current task"},
    ]
    assert compress_chat_messages_to_budget(messages, 224000) == [{"role": "user", "content": "Current task"}]


def test_engine_budget_still_compresses_oversized_ordinary_text() -> None:
    messages = [
        {"role": "system", "content": "Role anchor"},
        {"role": "user", "content": "old ordinary history " * 1000},
        {"role": "user", "content": "Current task"},
    ]
    before = deepcopy(messages)
    budgeted = compress_chat_messages_to_budget(messages, 128)
    assert budgeted is not None
    assert budgeted[0] == {"role": "system", "content": "Role anchor"}
    assert budgeted[-1] == {"role": "user", "content": "Current task"}
    assert len(budgeted) == 3
    assert messages[1] not in budgeted
    assert messages == before
