"""Regression tests for AIStreamEvent contract safety."""

from __future__ import annotations

from typing import Any, cast

import pytest
from polaris.kernelone.llm.engine.contracts import AIRequest, AIStreamEvent, StreamEventType, TaskType
from polaris.kernelone.llm.engine.stream import StreamExecutor
from polaris.kernelone.llm.engine.stream.tool_accumulator import _safe_text_length
from polaris.kernelone.llm.provider_adapters.anthropic_messages_adapter import AnthropicMessagesAdapter
from polaris.kernelone.llm.provider_adapters.base import AssistantMessage, ReasoningSummary
from polaris.kernelone.llm.provider_adapters.openai_responses_adapter import OpenAIResponsesAdapter


def test_ai_stream_event_defaults_do_not_leak_callable_defaults() -> None:
    """COMPLETE event must keep chunk/reasoning defaults as None."""
    event = AIStreamEvent.complete()
    assert event.type == StreamEventType.COMPLETE
    assert event.chunk is None
    assert event.reasoning is None
    assert not callable(event.chunk)
    assert not callable(event.reasoning)


def test_ai_stream_event_chunk_and_reasoning_factories() -> None:
    """Factory methods must populate only their dedicated text field."""
    chunk_event = AIStreamEvent.chunk_event("hello")
    assert chunk_event.type == StreamEventType.CHUNK
    assert chunk_event.chunk == "hello"
    assert chunk_event.reasoning is None

    reasoning_event = AIStreamEvent.reasoning_event("think")
    assert reasoning_event.type == StreamEventType.REASONING_CHUNK
    assert reasoning_event.chunk is None
    assert reasoning_event.reasoning == "think"


def test_safe_text_length_ignores_callable_values() -> None:
    """Stream debug payload length calc should never crash on callable values."""

    class _Probe:
        def marker(self) -> str:
            return "x"

    probe = _Probe()
    assert _safe_text_length("abc") == 3
    assert _safe_text_length(probe.marker) == 0
    assert _safe_text_length(None) == 0


@pytest.mark.parametrize(
    ("raw_event", "expected_text", "expected_item_type"),
    [
        ({"choices": [{"delta": {"content": " This"}}]}, " This", AssistantMessage),
        ({"type": "content_chunk", "content": " allows"}, " allows", AssistantMessage),
        ({"content": " me"}, " me", AssistantMessage),
        ({"delta": {"content": " to"}}, " to", AssistantMessage),
        ({"message": {"content": " demonstrate"}}, " demonstrate", AssistantMessage),
        ({"type": "response.output_text.delta", "delta": " comprehensive"}, " comprehensive", AssistantMessage),
        ({"type": "response.reasoning_text.delta", "delta": " think"}, " think", ReasoningSummary),
        (
            {"candidates": [{"content": {"parts": [{"text": " PM"}]}}]},
            " PM",
            AssistantMessage,
        ),
    ],
)
def test_openai_adapter_decodes_common_provider_stream_shapes(
    raw_event: dict[str, Any],
    expected_text: str,
    expected_item_type: type,
) -> None:
    """OpenAI-compatible adapter should normalize common LLM streaming deltas."""

    decoded = OpenAIResponsesAdapter().decode_stream_event(raw_event)

    assert decoded is not None
    assert len(decoded.transcript_items) == 1
    item = decoded.transcript_items[0]
    assert isinstance(item, expected_item_type)
    typed_item = cast(AssistantMessage | ReasoningSummary, item)
    assert typed_item.content == expected_text


def test_openai_adapter_decodes_responses_api_payload_and_usage() -> None:
    """Responses API non-stream payloads should produce text, reasoning, tools, and usage."""

    decoded = OpenAIResponsesAdapter().decode_response(
        {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "final answer"}],
                },
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "checked constraints"}]},
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "repo_tree",
                    "arguments": '{"path":"."}',
                },
            ],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 5,
                "input_tokens_details": {"cached_tokens": 4},
            },
        }
    )

    assert any(
        isinstance(item, AssistantMessage) and item.content == "final answer" for item in decoded.transcript_items
    )
    assert any(
        isinstance(item, ReasoningSummary) and item.content == "checked constraints"
        for item in decoded.transcript_items
    )
    assert decoded.tool_calls == [
        {
            "tool": "repo_tree",
            "arguments": {"path": "."},
            "arguments_text": '{"path":"."}',
            "arguments_complete": True,
            "call_id": "call-1",
            "index": None,
        }
    ]
    assert decoded.usage["prompt_tokens"] == 12
    assert decoded.usage["completion_tokens"] == 5
    assert decoded.usage["total_tokens"] == 17
    assert decoded.usage["cached_tokens"] == 4


def test_openai_adapter_decodes_responses_stream_usage_and_function_args() -> None:
    """Responses stream function-call and final usage events should not be dropped."""

    args_delta = OpenAIResponsesAdapter().decode_stream_event(
        {
            "type": "response.function_call_arguments.delta",
            "call_id": "call-1",
            "name": "read_file",
            "delta": '{"path"',
            "output_index": 0,
        }
    )
    completed = OpenAIResponsesAdapter().decode_stream_event(
        {
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 21, "output_tokens": 7, "total_tokens": 28}},
        }
    )

    assert args_delta is not None
    assert args_delta.tool_calls[0]["tool"] == "read_file"
    assert args_delta.tool_calls[0]["arguments_text"] == '{"path"'
    assert args_delta.tool_calls[0]["arguments_complete"] is False
    assert completed is not None
    assert completed.usage["prompt_tokens"] == 21
    assert completed.usage["completion_tokens"] == 7


@pytest.mark.parametrize(
    ("raw_event", "expected_text", "expected_item_type"),
    [
        (
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " across"}},
            " across",
            AssistantMessage,
        ),
        ({"type": "content_chunk", "content": " all"}, " all", AssistantMessage),
        ({"delta": {"content": " evaluation"}}, " evaluation", AssistantMessage),
        ({"type": "thinking_delta", "thinking": " risk"}, " risk", ReasoningSummary),
    ],
)
def test_anthropic_adapter_decodes_common_compat_stream_shapes(
    raw_event: dict[str, Any],
    expected_text: str,
    expected_item_type: type,
) -> None:
    """Anthropic-compatible adapter should normalize common Kimi/Claude-style deltas."""

    decoded = AnthropicMessagesAdapter().decode_stream_event(raw_event)

    assert decoded is not None
    assert len(decoded.transcript_items) == 1
    item = decoded.transcript_items[0]
    assert isinstance(item, expected_item_type)
    typed_item = cast(AssistantMessage | ReasoningSummary, item)
    assert typed_item.content == expected_text


def test_anthropic_adapter_decodes_thinking_and_cache_usage() -> None:
    """Anthropic usage must include cache creation/read tokens in prompt cost."""

    decoded = AnthropicMessagesAdapter().decode_response(
        {
            "content": [
                {"type": "thinking", "thinking": "private chain summary"},
                {"type": "redacted_thinking", "data": "opaque"},
                {"type": "text", "text": "visible"},
            ],
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 3,
                "cache_read_input_tokens": 5,
                "output_tokens": 2,
            },
        }
    )

    assert any(
        isinstance(item, ReasoningSummary) and item.content == "private chain summary"
        for item in decoded.transcript_items
    )
    assert any(isinstance(item, AssistantMessage) and item.content == "visible" for item in decoded.transcript_items)
    assert all(getattr(item, "content", "") != "opaque" for item in decoded.transcript_items)
    assert decoded.usage["prompt_tokens"] == 18
    assert decoded.usage["completion_tokens"] == 2
    assert decoded.usage["total_tokens"] == 20
    assert decoded.usage["cached_tokens"] == 5


def test_anthropic_adapter_decodes_message_delta_usage_and_error() -> None:
    """Anthropic stream final usage and error events should be structured."""

    usage_event = AnthropicMessagesAdapter().decode_stream_event(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {
                "input_tokens": 6,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 1,
                "output_tokens": 4,
            },
        }
    )
    error_event = AnthropicMessagesAdapter().decode_stream_event(
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    )

    assert usage_event is not None
    assert usage_event.usage["prompt_tokens"] == 9
    assert usage_event.usage["completion_tokens"] == 4
    assert error_event is not None
    assert error_event.error == "Overloaded"


@pytest.mark.asyncio
async def test_stream_executor_tool_call_event_does_not_crash_on_length_audit(monkeypatch) -> None:
    """Structured tool-call events must pass through chunk debug audit without TypeError."""

    class _Decoded:
        transcript_items: list[Any] = []
        tool_calls = [
            {
                "tool": "read_file",
                "arguments": {"path": "README.md"},
                "arguments_complete": True,
                "call_id": "call-1",
            }
        ]

    class _FakeAdapter:
        def decode_stream_event(self, raw_event: dict[str, Any]) -> _Decoded:
            return _Decoded()

    class _FakeProvider:
        async def invoke_stream_events(self, prompt_input: str, model: str, invoke_cfg: dict[str, Any]):
            yield {"kind": "tool_delta"}

    class _FakeProviderManager:
        def get_provider_instance(self, provider_type: str) -> _FakeProvider:
            return _FakeProvider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.providers.get_provider_manager",
        _FakeProviderManager,
    )
    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.stream.executor.get_adapter",
        lambda provider_type: _FakeAdapter(),
    )

    executor = StreamExecutor(workspace=".")
    monkeypatch.setattr(executor, "_resolve_provider_model", lambda request: ("fake_provider", "fake-model"))
    monkeypatch.setattr(executor, "_get_provider_config", lambda provider_id: {"type": "fake"})
    monkeypatch.setattr(executor, "_build_invoke_config", lambda provider_cfg, options: {"timeout": 5})
    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.model_catalog.ModelCatalog._resolve_context_window",
        lambda self, *a, **kw: 128000,
    )
    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.model_catalog.ModelCatalog._resolve_output_limit",
        lambda self, *a, **kw: 4096,
    )

    request = AIRequest(task_type=TaskType.GENERATION, role="director", input="hello")
    observed: list[StreamEventType] = []
    async for event in executor.invoke_stream(request):
        observed.append(event.type)

    assert StreamEventType.TOOL_CALL in observed
    assert StreamEventType.COMPLETE in observed


@pytest.mark.asyncio
async def test_stream_executor_uses_provider_stream_usage(monkeypatch) -> None:
    """Provider final usage from structured stream should reach the public complete event."""

    class _FakeProvider:
        async def invoke_stream_events(self, prompt_input: str, model: str, invoke_cfg: dict[str, Any]):
            yield {"type": "response.output_text.delta", "delta": "ok"}
            yield {
                "type": "response.completed",
                "response": {"usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}},
            }

    class _FakeProviderManager:
        def get_provider_instance(self, provider_type: str) -> _FakeProvider:
            return _FakeProvider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.providers.get_provider_manager",
        _FakeProviderManager,
    )
    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.model_catalog.ModelCatalog._resolve_context_window",
        lambda self, *a, **kw: 128000,
    )
    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.model_catalog.ModelCatalog._resolve_output_limit",
        lambda self, *a, **kw: 4096,
    )

    executor = StreamExecutor(workspace=".")
    monkeypatch.setattr(executor, "_resolve_provider_model", lambda request: ("fake_provider", "fake-model"))
    monkeypatch.setattr(executor, "_get_provider_config", lambda provider_id: {"type": "openai_compat"})
    monkeypatch.setattr(executor, "_build_invoke_config", lambda provider_cfg, options: {"timeout": 5})

    request = AIRequest(task_type=TaskType.GENERATION, role="director", input="hello")
    complete_meta: dict[str, Any] | None = None
    async for event in executor.invoke_stream(request):
        if event.type == StreamEventType.COMPLETE:
            complete_meta = event.meta

    assert complete_meta is not None
    assert complete_meta["usage"]["prompt_tokens"] == 11
    assert complete_meta["usage"]["completion_tokens"] == 3
    assert complete_meta["usage"]["total_tokens"] == 14
