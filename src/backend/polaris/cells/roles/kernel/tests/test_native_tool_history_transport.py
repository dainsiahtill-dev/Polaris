"""Actual native request builders preserve observations across tool turns."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.public.transcript_ir import TranscriptToolCall, TranscriptToolResult
from polaris.kernelone.llm.engine.provider_native_request import (
    FactoryProviderNativeRequestProjectionError,
    build_anthropic_native_messages,
    build_openai_native_messages,
)
from polaris.kernelone.llm.provider_adapters.anthropic_messages_adapter import _build_anthropic_messages_from_transcript


def _history() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "Director current authority"},
        {"role": "user", "content": "Read source, then repair its failing test."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "read-1",
                    "type": "function",
                    "function": {"name": "repo_read_head", "arguments": json.dumps({"path": "src/a.ts"})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "read-1", "content": "export const label = '月光';\n"},
        {
            "role": "assistant",
            "content": "Verify the actual code.",
            "tool_calls": [
                {
                    "id": "test-1",
                    "type": "function",
                    "function": {"name": "execute_command", "arguments": json.dumps({"command": "npm test"})},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "test-1",
            "content": "exit_code=1\nAssertionError: expected 7, got 8",
            "is_error": True,
        },
        {"role": "user", "content": "Repair that assertion failure."},
    ]


def test_anthropic_native_messages_keep_linked_calls_and_actual_failed_result() -> None:
    messages, system = build_anthropic_native_messages(_history())
    assert system == "Director current authority"
    blocks = [block for message in messages for block in message["content"] if isinstance(block, dict)]
    calls = [block for block in blocks if block.get("type") == "tool_use"]
    results = [block for block in blocks if block.get("type") == "tool_result"]
    assert [block["id"] for block in calls] == ["read-1", "test-1"]
    assert calls[0]["input"] == {"path": "src/a.ts"}
    assert [block["tool_use_id"] for block in results] == ["read-1", "test-1"]
    assert results[0]["content"] == "export const label = '月光';\n"
    assert results[1]["content"] == "exit_code=1\nAssertionError: expected 7, got 8"
    assert results[1]["is_error"] is True


def test_openai_native_messages_keep_tool_ids_and_pure_call_turn() -> None:
    messages = build_openai_native_messages(_history())
    calls = [message for message in messages if message.get("tool_calls")]
    results = [message for message in messages if message.get("role") == "tool"]
    assert len(calls) == 2
    assert calls[0]["tool_calls"][0]["id"] == "read-1"
    assert [message["tool_call_id"] for message in results] == ["read-1", "test-1"]
    assert results[0]["content"] == "export const label = '月光';\n"
    assert results[1]["content"] == "exit_code=1\nAssertionError: expected 7, got 8"


def test_public_typed_transcript_tools_render_as_native_anthropic_pairs() -> None:
    state = SimpleNamespace(
        transcript=[
            TranscriptToolCall(call_id="call-1", tool_name="repo_read_head", args={"path": "src/a.ts"}),
            TranscriptToolResult(
                call_id="call-1", tool_name="repo_read_head", status="error", content="permission denied"
            ),
        ]
    )
    messages = _build_anthropic_messages_from_transcript(state)
    assert len(messages) == 2
    assert messages[0]["content"][0] == {
        "type": "tool_use",
        "id": "call-1",
        "name": "repo_read_head",
        "input": {"path": "src/a.ts"},
    }
    assert messages[1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "call-1",
        "content": "permission denied",
        "is_error": True,
    }


def test_existing_native_content_blocks_are_not_stringified() -> None:
    original = [
        {"role": "user", "content": [{"type": "text", "text": "Read."}]},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "native-1", "name": "repo_tree", "input": {"path": "."}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "native-1", "content": "src/a.ts"}]},
    ]
    messages, _ = build_anthropic_native_messages(original)
    assert messages == original


def test_typed_adapter_messages_pass_shared_native_builder_without_duplicate_calls() -> None:
    state = SimpleNamespace(
        transcript=[
            TranscriptToolCall(call_id="call-1", tool_name="repo_tree", args={"path": "."}),
            TranscriptToolResult(call_id="call-1", tool_name="repo_tree", content="src/a.ts"),
        ]
    )
    messages, _ = build_anthropic_native_messages(_build_anthropic_messages_from_transcript(state))
    calls = [block for message in messages for block in message["content"] if block.get("type") == "tool_use"]
    assert len(calls) == 1
    assert calls[0]["id"] == "call-1"


def test_conflicting_native_block_and_call_sidecar_cannot_change_arguments() -> None:
    history = _history()
    history[2]["content"] = [
        {"type": "tool_use", "id": "read-1", "name": "repo_read_head", "input": {"path": "other.ts"}}
    ]
    with pytest.raises(FactoryProviderNativeRequestProjectionError, match="native_tool_history_call_conflict"):
        build_anthropic_native_messages(history)


def test_openai_tool_history_uses_normalized_role_names() -> None:
    history = _history()
    history[2]["role"] = " ASSISTANT "
    history[3]["role"] = " TOOL "
    messages = build_openai_native_messages(history)
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "tool"


def test_merged_assistant_cannot_repeat_same_native_call_id() -> None:
    history = _history()
    history.insert(3, dict(history[2]))
    with pytest.raises(FactoryProviderNativeRequestProjectionError, match="native_tool_history_duplicate_call"):
        build_anthropic_native_messages(history)


def test_native_result_cannot_reference_a_missing_call() -> None:
    history = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "orphan", "content": "unknown"}]}]
    with pytest.raises(FactoryProviderNativeRequestProjectionError, match="native_tool_history_orphan_result"):
        build_anthropic_native_messages(history)


def test_current_user_cannot_continue_past_an_unanswered_native_call() -> None:
    history = _history()
    history.pop(3)
    with pytest.raises(FactoryProviderNativeRequestProjectionError, match="native_tool_history_result_missing"):
        build_anthropic_native_messages(history)
