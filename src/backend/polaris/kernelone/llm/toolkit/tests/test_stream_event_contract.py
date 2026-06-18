"""Regression tests for AIStreamEvent contract safety."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.llm.engine.contracts import AIRequest, AIStreamEvent, StreamEventType, TaskType
from polaris.kernelone.llm.engine.stream_executor import StreamExecutor, _safe_text_length
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
    assert item.content == expected_text


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
    assert item.content == expected_text


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
