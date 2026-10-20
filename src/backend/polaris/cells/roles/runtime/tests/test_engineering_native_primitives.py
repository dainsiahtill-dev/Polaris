"""Native component proof using a synthetic provider and real HTTP/codecs."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.kernelone.llm.engine.contracts import AIRequest, StreamEventType, TaskType
from polaris.kernelone.llm.providers import get_provider_manager
from polaris.kernelone.llm.toolkit.parsers.core import parse_tool_calls
from polaris.kernelone.llm.toolkit.parsers.native_function import NativeFunctionCallingParser
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments

_ARGS = {"filename": "hello.txt", "text": "你好"}
_IDS = ["call-one", "call-two"]
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write UTF-8 text.",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "content": {"type": "string"}},
                "required": ["file", "content"],
            },
        },
    }
]
_MESSAGES = [
    {"role": "system", "content": "You are the synthetic Director."},
    {"role": "user", "content": "Write the greeting twice."},
]


def _request(messages: list[dict[str, Any]] | None = None) -> AIRequest:
    return AIRequest(
        task_type=TaskType.DIALOGUE,
        role="engineering_native_test",
        provider_id="synthetic",
        model="synthetic-model",
        input="flattened input must not replace messages",
        options={
            "messages": _MESSAGES if messages is None else messages,
            "tools": _TOOLS,
            "tool_choice": "auto",
            "max_tokens": 512,
            "timeout": 3,
        },
    )


def _tool_response(protocol: str) -> dict[str, Any]:
    if protocol == "anthropic":
        return {
            "id": "message-one",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": "create_file", "input": _ARGS} for call_id in _IDS],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
    return {
        "id": "message-one",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "create_file", "arguments": json.dumps(_ARGS, ensure_ascii=False)},
                        }
                        for call_id in _IDS
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }


def _final_response(protocol: str) -> dict[str, Any]:
    if protocol == "anthropic":
        return {
            "id": "message-two",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Both results received."}],
            "stop_reason": "end_turn",
        }
    return {
        "choices": [{"message": {"role": "assistant", "content": "Both results received."}, "finish_reason": "stop"}]
    }


def _stream_events(protocol: str, *, arguments: str, terminal: str | None) -> list[dict[str, Any] | str]:
    if protocol == "anthropic":
        events: list[dict[str, Any] | str] = []
        for index, call_id in enumerate(_IDS):
            events.extend(
                [
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "tool_use", "id": call_id, "name": "create_file", "input": {}},
                    },
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "input_json_delta", "partial_json": arguments},
                    },
                    {"type": "content_block_stop", "index": index},
                ]
            )
        if terminal:
            events.extend([{"type": "message_delta", "delta": {"stop_reason": terminal}}, {"type": "message_stop"}])
        return events
    events = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": "create_file", "arguments": arguments},
                            }
                            for index, call_id in enumerate(_IDS)
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
    ]
    if terminal:
        events.extend([{"choices": [{"index": 0, "delta": {}, "finish_reason": terminal}]}, "[DONE]"])
    return events


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_native_http_round_trip_preserves_call_identity_and_each_result(
    protocol: str, scripted_provider: ScriptedProvider, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = scripted_provider.client(protocol, isolated_workspace, monkeypatch)
    scripted_provider.enqueue_json(_tool_response(protocol))
    scripted_provider.enqueue_json(_final_response(protocol))
    first = await client.invoke(_request())
    assert first.ok, first.error
    raw = first.raw
    assert isinstance(raw, dict)
    if protocol == "anthropic":
        calls = NativeFunctionCallingParser.parse_anthropic(raw["content"], allowed_tool_names=["write_file"])
    else:
        calls = NativeFunctionCallingParser.parse_openai(
            raw["choices"][0]["message"]["tool_calls"], allowed_tool_names=["write_file"]
        )
    assert [call.id for call in calls] == _IDS
    assert [call.name for call in calls] == ["write_file", "write_file"]
    assert all(call.parse_error is None for call in calls)
    assert [normalize_tool_arguments(call.name, call.arguments) for call in calls] == [
        {"file": "hello.txt", "content": "你好"},
        {"file": "hello.txt", "content": "你好"},
    ]
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
            }
            for call in calls
        ],
    }
    results = [{"role": "tool", "tool_call_id": call.id, "content": f"observed:{call.id}"} for call in calls]
    second = await client.invoke(_request([*_MESSAGES, assistant, *results]))
    assert second.ok, second.error
    assert second.output == "Both results received."
    first_body = scripted_provider.requests[0]["body"]
    second_body = scripted_provider.requests[1]["body"]
    if protocol == "anthropic":
        assert first_body["system"] == "You are the synthetic Director."
        assert first_body["messages"] == [_MESSAGES[1]]
        assert first_body["tools"][0]["name"] == "write_file"
        assert first_body["tool_choice"] == {"type": "auto"}
        tool_results = [
            block
            for message in second_body["messages"]
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert [block["tool_use_id"] for block in tool_results] == _IDS
        assert [block["content"] for block in tool_results] == ["observed:call-one", "observed:call-two"]
    else:
        assert first_body["messages"] == _MESSAGES
        assert first_body["tools"] == _TOOLS
        assert first_body["tool_choice"] == "auto"
        assert second_body["messages"][-2:] == results


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_native_stream_keeps_two_calls_with_equal_arguments(
    protocol: str, scripted_provider: ScriptedProvider, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal = "tool_use" if protocol == "anthropic" else "tool_calls"
    scripted_provider.enqueue_stream(_stream_events(protocol, arguments=json.dumps(_ARGS), terminal=terminal))
    events = [
        event
        async for event in scripted_provider.client(protocol, isolated_workspace, monkeypatch).invoke_stream(_request())
    ]
    assert not [event.error for event in events if event.type == StreamEventType.ERROR]
    calls = [event.tool_call for event in events if event.type == StreamEventType.TOOL_CALL]
    assert [call["call_id"] for call in calls if call] == _IDS
    assert [call["arguments"] for call in calls if call] == [_ARGS, _ARGS]
    assert events[-1].type == StreamEventType.COMPLETE
    body = scripted_provider.requests[0]["body"]
    assert (body["system"] if protocol == "anthropic" else body["messages"][0]["content"]) == _MESSAGES[0]["content"]


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "arguments,ending",
    [
        ('{"filename":"hello.txt","text":"unfinished"', None),
        ('{"filename":"hello.txt","text":"valid JSON but no provider finish"}', None),
        ('{"filename":"hello.txt","text":"unfinished"', "normal"),
        ('{"filename":"hello.txt","text":"valid JSON but truncated turn"}', "limit"),
    ],
)
async def test_interrupted_or_incomplete_stream_never_publishes_executable_calls(
    protocol: str,
    arguments: str,
    ending: str | None,
    scripted_provider: ScriptedProvider,
    isolated_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = None
    if ending == "normal":
        terminal = "tool_use" if protocol == "anthropic" else "tool_calls"
    elif ending == "limit":
        terminal = "max_tokens" if protocol == "anthropic" else "length"
    scripted_provider.enqueue_stream(_stream_events(protocol, arguments=arguments, terminal=terminal))
    events = [
        event
        async for event in scripted_provider.client(protocol, isolated_workspace, monkeypatch).invoke_stream(_request())
    ]
    assert not [event for event in events if event.type == StreamEventType.TOOL_CALL]
    assert not [event for event in events if event.type == StreamEventType.COMPLETE]
    errors = [event for event in events if event.type == StreamEventType.ERROR]
    assert errors
    evidence = [event.meta for event in errors if event.meta and "interrupted_tool_calls" in event.meta]
    assert evidence
    assert [call["call_id"] for call in evidence[0]["interrupted_tool_calls"]] == _IDS


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("stream", [False, True])
async def test_http_failure_is_never_successful_model_text(
    protocol: str,
    stream: bool,
    scripted_provider: ScriptedProvider,
    isolated_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted_provider.enqueue_json(_final_response(protocol), status=403)
    client = scripted_provider.client(protocol, isolated_workspace, monkeypatch)
    if stream:
        events = [event async for event in client.invoke_stream(_request())]
        assert any(event.type == StreamEventType.ERROR for event in events)
        assert all(
            event.type not in {StreamEventType.COMPLETE, StreamEventType.CHUNK, StreamEventType.TOOL_CALL}
            for event in events
        )
    else:
        response = await client.invoke(_request())
        assert not response.ok
        assert response.output == ""
        assert response.error


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_native_parser_preserves_rejected_call_ids_for_explicit_tool_feedback(protocol: str) -> None:
    if protocol == "anthropic":
        calls = NativeFunctionCallingParser.parse_anthropic(
            [
                {"type": "tool_use", "id": "denied", "name": "write_file", "input": _ARGS},
                {"type": "tool_use", "id": "unnamed", "input": {}},
            ],
            allowed_tool_names=["read_file"],
        )
    else:
        calls = NativeFunctionCallingParser.parse_openai(
            [
                {
                    "type": "function",
                    "id": "denied",
                    "function": {"name": "write_file", "arguments": json.dumps(_ARGS)},
                },
                {"type": "function", "id": "unnamed", "function": {"arguments": "{}"}},
            ],
            allowed_tool_names=["read_file"],
        )
    assert [call.id for call in calls] == ["denied", "unnamed"]
    assert all(call.parse_error for call in calls)
    assert calls[0].name == "write_file"
    assert calls[0].arguments == _ARGS


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_native_parser_never_invents_provider_call_identity(protocol: str) -> None:
    if protocol == "anthropic":
        calls = NativeFunctionCallingParser.parse_anthropic(
            [{"type": "tool_use", "name": "write_file", "input": _ARGS}]
        )
    else:
        calls = NativeFunctionCallingParser.parse_openai(
            [{"type": "function", "function": {"name": "write_file", "arguments": json.dumps(_ARGS)}}]
        )
    assert len(calls) == 1
    assert calls[0].id == ""
    assert calls[0].parse_error and "id" in calls[0].parse_error


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_legacy_context_messages_remain_supported(
    protocol: str, scripted_provider: ScriptedProvider, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request()
    del request.options["messages"]
    request.context["chat_messages"] = _MESSAGES
    scripted_provider.enqueue_json(_final_response(protocol))
    response = await scripted_provider.client(protocol, isolated_workspace, monkeypatch).invoke(request)
    assert response.ok, response.error
    body = scripted_provider.requests[0]["body"]
    assert (body["system"] if protocol == "anthropic" else body["messages"][0]["content"]) == _MESSAGES[0]["content"]


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_native_stream_error_after_json_completion_keeps_calls_nonexecutable(
    protocol: str, scripted_provider: ScriptedProvider, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _stream_events(protocol, arguments=json.dumps(_ARGS), terminal=None)
    events.append({"type": "error", "error": {"type": "overloaded_error", "message": "synthetic provider failed"}})
    scripted_provider.enqueue_stream(events)
    result = [
        event
        async for event in scripted_provider.client(protocol, isolated_workspace, monkeypatch).invoke_stream(_request())
    ]
    assert not [event for event in result if event.type in {StreamEventType.TOOL_CALL, StreamEventType.COMPLETE}]
    errors = [event for event in result if event.type == StreamEventType.ERROR]
    assert errors
    evidence = [event.meta for event in errors if event.meta and "interrupted_tool_calls" in event.meta]
    assert [call["call_id"] for call in evidence[0]["interrupted_tool_calls"]] == _IDS


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_native_pending_limit_rejects_turn_instead_of_dropping_earlier_call(
    protocol: str, scripted_provider: ScriptedProvider, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_LLM_MAX_PENDING_CALLS", "1")
    terminal = "tool_use" if protocol == "anthropic" else "tool_calls"
    scripted_provider.enqueue_stream(_stream_events(protocol, arguments=json.dumps(_ARGS), terminal=terminal))
    events = [
        event
        async for event in scripted_provider.client(protocol, isolated_workspace, monkeypatch).invoke_stream(_request())
    ]
    assert not [event for event in events if event.type in {StreamEventType.TOOL_CALL, StreamEventType.COMPLETE}]
    errors = [event for event in events if event.type == StreamEventType.ERROR]
    assert any("pending" in str(event.error) for event in errors)
    evidence = [event.meta for event in errors if event.meta and "interrupted_tool_calls" in event.meta]
    assert [call["call_id"] for call in evidence[0]["interrupted_tool_calls"]] == _IDS


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("provider", ["anthropic", "auto", "openai"])
@pytest.mark.parametrize("count", [1, 2])
def test_unified_native_entry_preserves_protocol_shape_call_order_and_identity(
    protocol: str, provider: str, count: int
) -> None:
    ids = _IDS[:count]
    if protocol == "anthropic":
        blocks = [{"type": "tool_use", "id": call_id, "name": "create_file", "input": _ARGS} for call_id in ids]
        raw = [
            {"type": "text", "text": "before"},
            blocks[0],
            {"type": "text", "text": "between"},
            *blocks[1:],
            {"type": "text", "text": "after"},
        ]
    else:
        raw = [
            {
                "type": "function",
                "id": call_id,
                "function": {"name": "create_file", "arguments": json.dumps(_ARGS, ensure_ascii=False)},
            }
            for call_id in ids
        ]
    calls = parse_tool_calls(tool_calls=raw, provider=provider, allowed_tool_names=["write_file"])
    assert [call.id for call in calls] == ids
    assert [call.name for call in calls] == ["write_file"] * count
    assert all(call.parse_error is None for call in calls)
    assert [call.arguments for call in calls] == [_ARGS] * count


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("provider", ["anthropic", "auto", "openai"])
def test_unified_native_entry_preserves_each_rejected_call_and_invalid_identity(protocol: str, provider: str) -> None:
    if protocol == "anthropic":
        raw = [
            {"type": "tool_use", "name": "write_file", "input": _ARGS},
            {"type": "tool_use", "id": "unnamed", "input": {}},
            {"type": "tool_use", "id": "denied", "name": "write_file", "input": _ARGS},
        ]
    else:
        raw = [
            {"type": "function", "function": {"name": "write_file", "arguments": json.dumps(_ARGS)}},
            {"type": "function", "id": "unnamed", "function": {"arguments": "{}"}},
            {"type": "function", "id": "denied", "function": {"name": "write_file", "arguments": json.dumps(_ARGS)}},
        ]
    calls = parse_tool_calls(tool_calls=raw, provider=provider, allowed_tool_names=["read_file"])
    assert [call.id for call in calls] == ["", "unnamed", "denied"]
    assert [call.source for call in calls] == [protocol, protocol, protocol]
    assert all(call.parse_error for call in calls)
    assert calls[0].arguments == _ARGS
    assert calls[2].arguments == _ARGS


@pytest.mark.parametrize("provider", ["anthropic", "auto", "openai"])
def test_unified_native_entry_preserves_mixed_protocol_call_order(provider: str) -> None:
    raw = [
        {"type": "tool_use", "id": "first", "name": "write_file", "input": _ARGS},
        {"type": "function", "id": "second", "function": {"name": "write_file", "arguments": json.dumps(_ARGS)}},
        {"type": "tool_use", "id": "third", "name": "write_file", "input": _ARGS},
    ]
    calls = parse_tool_calls(tool_calls=raw, provider=provider)
    assert [call.id for call in calls] == ["first", "second", "third"]
    assert all(call.parse_error is None for call in calls)


def test_unified_native_only_mode_never_interprets_assistant_text_as_actions() -> None:
    text = '<tool_calls>[{"tool":"read_file","arguments":{"file":"README.md"}}]</tool_calls>'
    legacy = parse_tool_calls(text=text)
    assert len(legacy) == 1
    assert legacy[0].name == "read_file"
    assert parse_tool_calls(text=text, allow_text_fallback=False) == []


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("canonical", [False, True])
async def test_legacy_structured_adapter_clean_iterator_end_preserves_native_calls(
    protocol: str,
    canonical: bool,
    scripted_provider: ScriptedProvider,
    isolated_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LegacyEventAdapter:
        async def invoke_stream_events(
            self, _prompt: str, _model: str, _config: dict[str, Any]
        ) -> AsyncGenerator[dict[str, Any], None]:
            for event in _stream_events(protocol, arguments=json.dumps(_ARGS), terminal=None):
                assert isinstance(event, dict)
                yield event

    monkeypatch.setattr(get_provider_manager(), "get_provider_instance", lambda _provider_type: LegacyEventAdapter())
    request = _request()
    if not canonical:
        del request.options["messages"]
        request.context["chat_messages"] = _MESSAGES
    events = [
        event
        async for event in scripted_provider.client(protocol, isolated_workspace, monkeypatch).invoke_stream(request)
    ]
    calls = [event.tool_call for event in events if event.type == StreamEventType.TOOL_CALL]
    errors = [event for event in events if event.type == StreamEventType.ERROR]
    if canonical:
        assert not calls
        assert errors
    else:
        assert [call["call_id"] for call in calls if call] == _IDS
        assert [call["arguments"] for call in calls if call] == [_ARGS, _ARGS]
        assert not errors
        assert events[-1].type == StreamEventType.COMPLETE


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_builtin_native_http_requires_wire_terminal_for_legacy_request(
    protocol: str, scripted_provider: ScriptedProvider, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request()
    del request.options["messages"]
    request.context["chat_messages"] = _MESSAGES
    scripted_provider.enqueue_stream(_stream_events(protocol, arguments=json.dumps(_ARGS), terminal=None))
    events = [
        event
        async for event in scripted_provider.client(protocol, isolated_workspace, monkeypatch).invoke_stream(request)
    ]
    assert not [event for event in events if event.type in {StreamEventType.TOOL_CALL, StreamEventType.COMPLETE}]
    assert any(
        "missing_provider_terminal" in str(event.error) for event in events if event.type == StreamEventType.ERROR
    )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_legacy_structured_adapter_incomplete_json_never_becomes_executable(
    protocol: str, scripted_provider: ScriptedProvider, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LegacyEventAdapter:
        async def invoke_stream_events(
            self, _prompt: str, _model: str, _config: dict[str, Any]
        ) -> AsyncGenerator[dict[str, Any], None]:
            for event in _stream_events(protocol, arguments='{"filename":"unfinished"', terminal=None):
                assert isinstance(event, dict)
                yield event

    monkeypatch.setattr(get_provider_manager(), "get_provider_instance", lambda _provider_type: LegacyEventAdapter())
    request = _request()
    del request.options["messages"]
    request.context["chat_messages"] = _MESSAGES
    events = [
        event
        async for event in scripted_provider.client(protocol, isolated_workspace, monkeypatch).invoke_stream(request)
    ]
    assert not [event for event in events if event.type in {StreamEventType.TOOL_CALL, StreamEventType.COMPLETE}]
    errors = [event for event in events if event.type == StreamEventType.ERROR]
    assert errors
    evidence = [event.meta for event in errors if event.meta and "interrupted_tool_calls" in event.meta]
    assert [call["call_id"] for call in evidence[0]["interrupted_tool_calls"]] == _IDS


@pytest.mark.parametrize("remaining", [None, 5400.0, 30.0])
async def test_role_callers_pass_host_deadline_without_overriding_provider_timeout(
    remaining: float | None, tmp_path: Path
) -> None:
    import asyncio
    import time

    from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
    from polaris.kernelone.llm.engine.contracts import AIResponse
    from polaris.kernelone.llm.engine.invocation_budget import get_provider_invocation_budget

    observations: list[tuple[dict[str, Any], float | None]] = []

    class CaptureClient:
        async def invoke(self, request: AIRequest) -> AIResponse:
            budget = get_provider_invocation_budget()
            observations.append((request.options, budget.remaining() if budget is not None else None))
            return AIResponse.success(
                output="Read-only final answer.",
                raw={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Read-only final answer."},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

    limits = RunLimits(time.time() + remaining if remaining is not None else None, 100_000, 16_000)
    client = CaptureClient()
    service = EngineeringRunService(tmp_path, client=client)  # type: ignore[arg-type]
    assert (await service.chat_role("director", "Discuss the budget.", limits=limits)).ok
    scope = PermissionScope((tmp_path,), (), frozenset(), False)
    command = RoleRunCommand("budget-producer", "director", "Discuss the budget.", tmp_path)
    store = EngineeringSessionStore(tmp_path)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=client,  # type: ignore[arg-type]
    )
    assert result.status == "completed"
    assert len(observations) == 2
    for options, cap in observations:
        assert "timeout" not in options
        if remaining is None:
            assert cap is None
        else:
            assert cap is not None and remaining - 2 < cap <= remaining
