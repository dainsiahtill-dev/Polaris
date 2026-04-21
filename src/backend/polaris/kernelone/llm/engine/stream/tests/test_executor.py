"""Tests for StreamExecutor core streaming functionality.

Covers:
- StreamExecutor initialization and configuration
- invoke_stream error handling (invalid provider, budget exceeded)
- _provider_supports_structured_stream detection
- Tool call accumulator utilities
"""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.engine.stream import (
    StreamExecutor,
    _normalize_arguments,
    _provider_supports_structured_stream,
    _tool_accumulator_key,
)
from polaris.kernelone.llm.engine.stream.config import StreamConfig as DirectStreamConfig


class TestStreamExecutorInit:
    """Tests for StreamExecutor initialization."""

    def test_default_initialization(self) -> None:
        """StreamExecutor must initialize with default values."""
        executor = StreamExecutor()

        assert executor.workspace is None
        assert executor.telemetry is None
        assert executor.model_catalog is not None
        assert executor.token_budget is not None
        assert executor.config is not None

    def test_initialization_with_workspace(self) -> None:
        """StreamExecutor must accept workspace parameter."""
        executor = StreamExecutor(workspace="/tmp/test")

        assert executor.workspace == "/tmp/test"

    def test_initialization_with_custom_config(self) -> None:
        """StreamExecutor must accept custom StreamConfig."""
        config = DirectStreamConfig(buffer_size=200, timeout_sec=60.0)
        executor = StreamExecutor(config=config)

        assert executor.config.buffer_size == 200
        assert executor.timeout == 60.0


class TestStreamExecutorInvokeStreamErrors:
    """Tests for invoke_stream error handling."""

    @pytest.mark.asyncio
    async def test_invoke_stream_error_on_invalid_provider(self) -> None:
        """invoke_stream must yield error event when provider is invalid."""
        executor = StreamExecutor()

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            provider_id="nonexistent_provider",
            model="gpt-4",
        )

        events = []
        async for event in executor.invoke_stream(request):
            events.append(event)

        assert len(events) >= 1
        error_events = [e for e in events if e.type.value == "error"]
        assert len(error_events) >= 1
        error_msg = error_events[0].error or ""
        assert "Provider" in error_msg or "provider" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_invoke_stream_error_on_missing_provider_type(self) -> None:
        """invoke_stream must yield error event when provider type is unknown."""
        executor = StreamExecutor()

        # Use a valid provider_id format but with unknown type
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            provider_id="invalid_provider_type",
            model="gpt-4",
        )

        events = []
        async for event in executor.invoke_stream(request):
            events.append(event)

        assert len(events) >= 1
        error_events = [e for e in events if e.type.value == "error"]
        assert len(error_events) >= 1

    @pytest.mark.asyncio
    async def test_invoke_stream_debug_event_uses_resolved_provider_and_model(self, monkeypatch) -> None:
        """invoke_start debug event must use resolved provider/model, not empty request fields."""
        executor = StreamExecutor()
        captured_debug_events: list[dict[str, object]] = []

        def _capture_debug_event(**kwargs: object) -> None:
            captured_debug_events.append(dict(kwargs))

        monkeypatch.setattr(
            executor,
            "_resolve_provider_model",
            lambda _request: ("resolved-provider", "resolved-model"),
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {})
        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._debug_stream_module.emit_debug_event",
            _capture_debug_event,
        )

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="hello",
            provider_id=None,
            model=None,
        )

        events = []
        async for event in executor.invoke_stream(request):
            events.append(event)

        assert any(event.type.value == "error" for event in events)
        invoke_start = next(item for item in captured_debug_events if item.get("label") == "invoke_start")
        payload = invoke_start.get("payload")
        assert isinstance(payload, dict)
        assert payload["provider_id"] == "resolved-provider"
        assert payload["model"] == "resolved-model"


class TestProviderSupportsStructuredStream:
    """Tests for _provider_supports_structured_stream detection."""

    def test_provider_with_invoke_stream_events(self) -> None:
        """Provider with invoke_stream_events must return True."""

        class FakeStructuredProvider:
            async def invoke_stream_events(self, prompt: str, model: str, config: dict) -> None:
                return

        provider = FakeStructuredProvider()
        assert _provider_supports_structured_stream(provider) is True

    def test_provider_without_invoke_stream_events(self) -> None:
        """Provider without invoke_stream_events must return False."""

        class FakeTextProvider:
            async def invoke_stream(self, prompt: str, model: str, config: dict) -> None:
                return

        provider = FakeTextProvider()
        assert _provider_supports_structured_stream(provider) is False


class TestNormalizeArguments:
    """Tests for _normalize_arguments utility."""

    def test_dict_arguments(self) -> None:
        """Dict arguments must be returned as-is."""
        args = {"path": "README.md", "lines": 10}
        result, complete = _normalize_arguments(args)

        assert result == args
        assert complete is True

    def test_none_arguments(self) -> None:
        """None arguments must return empty dict with False."""
        result, complete = _normalize_arguments(None)

        assert result == {}
        assert complete is False

    def test_empty_string_arguments(self) -> None:
        """Empty string arguments must return empty dict with False."""
        result, complete = _normalize_arguments("")

        assert result == {}
        assert complete is False

    def test_valid_json_string_arguments(self) -> None:
        """Valid JSON string arguments must be parsed."""
        result, complete = _normalize_arguments('{"path": "README.md"}')

        assert result == {"path": "README.md"}
        assert complete is True

    def test_invalid_json_string_arguments(self) -> None:
        """Invalid JSON string arguments must return empty dict."""
        result, complete = _normalize_arguments("not json")

        assert result == {}
        assert complete is False

    def test_primitive_value_arguments(self) -> None:
        """Primitive non-JSON values must return empty dict."""
        # str("hello") is not valid JSON, so it returns empty dict
        result, complete = _normalize_arguments("hello")

        assert result == {}
        assert complete is False


class TestToolAccumulatorKey:
    """Tests for _tool_accumulator_key utility."""

    def test_content_block_index_priority(self) -> None:
        """content_block_index must be used as primary key."""
        tool_call = {"content_block_index": 5}
        key = _tool_accumulator_key(tool_call, ordinal=0)

        assert key == "content_block_index:5"

    def test_index_fallback(self) -> None:
        """index must be used when content_block_index is absent."""
        tool_call = {"index": 3}
        key = _tool_accumulator_key(tool_call, ordinal=0)

        assert key == "index:3"

    def test_call_id_fallback(self) -> None:
        """call_id must be used when no index is present."""
        tool_call = {"call_id": "abc123"}
        key = _tool_accumulator_key(tool_call, ordinal=0)

        assert key == "call_id:abc123"

    def test_tool_name_fallback(self) -> None:
        """tool_name must be used when no call_id is present."""
        tool_call = {"tool": "read_file"}
        key = _tool_accumulator_key(tool_call, ordinal=10)

        assert key == "tool:read_file"

    def test_ordinal_fallback(self) -> None:
        """ordinal must be used as last resort."""
        tool_call: dict[str, object] = {}
        key = _tool_accumulator_key(tool_call, ordinal=42)

        assert key == "ordinal:42"

    def test_whitespace_stripping(self) -> None:
        """Whitespace in tool_name and call_id must be stripped."""
        tool_call = {"tool": "  read_file  ", "call_id": "  abc123  "}
        key = _tool_accumulator_key(tool_call, ordinal=0)

        assert key == "call_id:abc123"
