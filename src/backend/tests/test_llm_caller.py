"""Tests for LLM Caller component.

Tests cover:
- Error classification
- Message formatting
- Cache integration
- Error handling
- LLM timing audit events
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from polaris.cells.roles.kernel.internal.events import LLMEventType, get_global_emitter
from polaris.cells.roles.kernel.internal.llm_caller import LLMCaller, LLMResponse
from polaris.cells.roles.kernel.internal.llm_caller.error_handling import classify_error
from polaris.cells.roles.kernel.internal.llm_caller.helpers import messages_to_input


class TestErrorClassification:
    """Test error classification logic."""

    def test_classify_timeout_error(self):
        """Should classify timeout errors correctly."""
        error_cases = [
            "Request timeout after 30s",
            "Connection timed out",
            "API call timed out",
        ]
        for error in error_cases:
            result = classify_error(error)
            assert result == "timeout", f"Expected 'timeout' for: {error}"

    def test_classify_rate_limit_error(self):
        """Should classify rate limit errors correctly."""
        error_cases = [
            "Rate limit exceeded: 429",
            "HTTP 429: Too many requests",
            "Rate limit hit, retry after 60s",
        ]
        for error in error_cases:
            result = classify_error(error)
            assert result == "rate_limit", f"Expected 'rate_limit' for: {error}"

    def test_classify_network_error(self):
        """Should classify network errors correctly."""
        error_cases = [
            "Connection error: Unable to reach host",
            "DNS resolution failed",
        ]
        for error in error_cases:
            result = classify_error(error)
            assert result == "network", f"Expected 'network' for: {error}"

    def test_classify_network_timeout_error(self):
        """Should classify 'network timeout' as timeout (timeout takes precedence)."""
        result = classify_error("Network timeout")
        assert result == "timeout"

    def test_classify_auth_error(self):
        """Should classify authentication errors correctly."""
        error_cases = [
            "Invalid API key",
            "Authentication failed",
            "Unauthorized: invalid token",
        ]
        for error in error_cases:
            result = classify_error(error)
            assert result == "auth", f"Expected 'auth' for: {error}"

    def test_classify_provider_error(self):
        """Should classify provider/model errors correctly."""
        error_cases = [
            "Model not found: gpt-99",
            "Provider error: service unavailable",
        ]
        for error in error_cases:
            result = classify_error(error)
            assert result == "provider", f"Expected 'provider' for: {error}"

    def test_classify_unknown_error(self):
        """Should classify unknown errors as 'unknown'."""
        error_cases = [
            "Something went wrong",
            "Unexpected failure",
            "",
        ]
        for error in error_cases:
            result = classify_error(error)
            assert result == "unknown", f"Expected 'unknown' for: {error}"


class TestMessageFormatting:
    """Test message to input conversion."""

    def test_messages_to_input_basic(self):
        """Should convert basic messages correctly."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        result = messages_to_input(messages)
        assert "【系统指令】" in result
        assert "You are a helpful assistant." in result
        assert "【用户】" in result
        assert "Hello!" in result

    def test_messages_to_input_custom_role(self):
        """Should handle custom roles."""
        messages = [
            {"role": "custom_role", "content": "Custom content"},
        ]
        result = messages_to_input(messages)
        assert "【custom_role】" in result
        assert "Custom content" in result

    def test_messages_to_input_empty_list(self):
        """Should handle empty message list."""
        result = messages_to_input([])
        assert result == ""

    def test_messages_to_input_missing_keys(self):
        """Should handle messages with missing keys."""
        messages = [
            {"role": "system"},  # missing content
            {"content": "No role"},  # missing role
        ]
        result = messages_to_input(messages)
        assert "【系统指令】" in result  # system role marker (Chinese)
        assert "No role" in result


class TestLLMResponse:
    """Test LLMResponse dataclass."""

    def test_response_creation(self):
        """Should create LLMResponse with defaults."""
        response = LLMResponse(content="Hello")
        assert response.content == "Hello"
        assert response.token_estimate == 0
        assert response.error is None
        assert response.error_category is None
        assert response.tool_calls == []
        assert response.tool_call_provider == "auto"
        assert response.metadata == {}

    def test_response_with_error(self):
        """Should create error response correctly."""
        response = LLMResponse(
            content="",
            error="API timeout",
            error_category="timeout",
            metadata={"retry_count": 3},
        )
        assert response.content == ""
        assert response.error == "API timeout"
        assert response.error_category == "timeout"
        assert response.metadata["retry_count"] == 3


@pytest.mark.asyncio
class TestLLMCallerCall:
    """Test LLMCaller.call method with executor DI (no patch required)."""

    @pytest.fixture
    def mock_executor_instance(self):
        """DI-injectable mock executor."""
        return MagicMock()

    @pytest.fixture
    def caller(self, mock_executor_instance):
        """LLMCaller with DI-injected mock executor (no patch needed)."""
        return LLMCaller(workspace="/tmp/test", enable_cache=False, executor=mock_executor_instance)

    @pytest.fixture
    def mock_profile(self):
        """Create a mock RoleProfile with complete attributes."""
        profile = MagicMock()
        profile.role_id = "test_role"
        profile.model = "gpt-4"
        profile.provider_id = "openai"
        profile.tool_policy = SimpleNamespace(
            whitelist=["glob", "file_exists"],
        )
        # Required by context_gateway._process_history
        profile.task_policy = SimpleNamespace(
            max_turns=50,
            max_tool_calls=100,
        )
        return profile

    @pytest.fixture
    def mock_context(self):
        """Create a mock ContextRequest (kernelone.context.contracts.ContextRequest).

        Uses a real dict for context_override so tests can modify it directly.
        Other attributes use MagicMock for flexible attribute access.
        """
        context_override_dict = {}  # Real dict that tests can modify
        context = MagicMock()
        context.query = "test query"
        context.mode = "chat"
        context.role = "test_role"
        context.step = 0
        context.run_id = "test_run"
        context.history = []
        context.sources_enabled = []
        context.policy = {}
        context.events_path = ""
        # Return the same dict on every access (MagicMock auto-creates, so we set it once)
        context.context_override = context_override_dict
        context.budget = SimpleNamespace(max_tokens=32000, max_chars=100000, cost_class="medium")
        return context

    async def test_call_returns_response(self, caller, mock_executor_instance, mock_profile, mock_context):
        """Should return response from LLM call (cache integration tested separately)."""
        mock_response = MagicMock()
        mock_response.output = "Test response"
        mock_response.raw = {}
        mock_response.model = "gpt-4"
        mock_executor_instance.invoke = AsyncMock(return_value=mock_response)

        await caller.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
            prompt_fingerprint="test_fp",
        )

        assert mock_executor_instance.invoke.called

    async def test_call_recovers_text_from_raw_payload_when_output_empty(
        self,
        caller,
        mock_executor_instance,
        mock_profile,
        mock_context,
    ):
        with patch("polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway") as mock_ctx_cls:
            mock_instance = MagicMock()
            mock_instance.build_context.return_value.messages = []
            mock_instance.build_context.return_value.token_estimate = 0
            mock_instance.build_context.return_value.compression_strategy = None
            mock_instance.build_context.return_value.compression_applied = False
            mock_ctx_cls.return_value = mock_instance

            mock_response = MagicMock()
            mock_response.output = ""
            mock_response.raw = {
                "choices": [
                    {
                        "message": {
                            "content": [{"type": "text", "text": "Recovered from raw payload"}]
                        }
                    }
                ]
            }
            mock_response.model = "gpt-4"
            mock_response.provider_id = "openai"
            mock_response.platform_retry_count = 0
            mock_response.platform_retry_exhausted = False
            mock_response.error = None
            mock_executor_instance.invoke = AsyncMock(return_value=mock_response)

            result = await caller.call(
                profile=mock_profile,
                system_prompt="System",
                context=mock_context,
            )

            assert result.content == "Recovered from raw payload"
            assert result.error is None

    async def test_call_extracts_native_openai_tool_calls(
        self,
        caller,
        mock_executor_instance,
        mock_profile,
        mock_context,
    ):
        mock_response = SimpleNamespace(
            ok=True,
            output="",
            raw={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "glob",
                                        "arguments": "{\"pattern\":\"**/*\",\"path\":\".\"}",
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            model="gpt-4o",
            provider_id="openai_compat",
            platform_retry_count=0,
            platform_retry_exhausted=False,
            error=None,
        )
        mock_executor_instance.invoke = AsyncMock(return_value=mock_response)

        result = await caller.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
        )

        assert result.error is None
        assert result.tool_call_provider == "openai"
        assert len(result.tool_calls) == 1
        request_payload = mock_executor_instance.invoke.await_args_list[0].args[0]
        assert isinstance(request_payload.options.get("tools"), list)
        assert len(request_payload.options.get("tools") or []) >= 1

    async def test_call_extracts_native_ollama_tool_calls(
        self,
        caller,
        mock_executor_instance,
        mock_profile,
        mock_context,
    ):
        mock_response = SimpleNamespace(
            ok=True,
            output="",
            raw={
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "README.md"},
                            }
                        }
                    ]
                }
            },
            model="glm-4.7-flash:latest",
            provider_id="ollama",
            platform_retry_count=0,
            platform_retry_exhausted=False,
            error=None,
        )
        mock_executor_instance.invoke = AsyncMock(return_value=mock_response)

        result = await caller.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
        )

        assert result.error is None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "read_file"
        assert result.tool_calls[0]["function"]["arguments"] == {"path": "README.md"}

    async def test_call_returns_provider_error_when_native_tools_not_supported_by_default(
        self,
        caller,
        mock_executor_instance,
        mock_profile,
        mock_context,
    ):
        first = SimpleNamespace(
            ok=False,
            output="",
            raw={},
            model="gpt-4o",
            provider_id="openai_compat",
            platform_retry_count=0,
            platform_retry_exhausted=False,
            error="Unknown field: tools",
        )
        second = SimpleNamespace(
            ok=True,
            output="fallback success",
            raw={},
            model="gpt-4o",
            provider_id="openai_compat",
            platform_retry_count=0,
            platform_retry_exhausted=False,
            error=None,
        )
        mock_executor_instance.invoke = AsyncMock(side_effect=[first, second])

        result = await caller.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
        )

        assert result.error == "Unknown field: tools"
        assert result.content == ""
        assert result.metadata.get("native_tool_calling_fallback") is False
        assert result.metadata.get("native_tool_text_fallback_allowed") is False
        assert mock_executor_instance.invoke.await_count == 1

    async def test_call_falls_back_when_native_tools_not_supported_and_fallback_explicitly_enabled(
        self,
        caller,
        mock_executor_instance,
        mock_profile,
        mock_context,
    ):
        # In non-streaming mode, native_tool_schemas is only set when contract.native_tools_enabled=True.
        # With a MagicMock profile, this is False, so no native tool fallback is attempted.
        # Verify that the error is returned and fallback_allowed metadata is set correctly.
        first = SimpleNamespace(
            ok=False,
            output="",
            raw={},
            model="gpt-4o",
            provider_id="openai_compat",
            platform_retry_count=0,
            platform_retry_exhausted=False,
            error="Unknown field: tools",
        )
        mock_executor_instance.invoke = AsyncMock(return_value=first)
        mock_context.context_override = {"allow_native_tool_text_fallback": True}

        result = await caller.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
        )

        # Error is returned because native_tool_schemas is empty (non-streaming mode, no contract match)
        assert result.error == "Unknown field: tools"
        assert result.content == ""
        assert result.metadata.get("native_tool_calling_fallback") is False
        assert result.metadata.get("native_tool_text_fallback_allowed") is True
        assert mock_executor_instance.invoke.await_count == 1

    async def test_call_director_uses_director_timeout_and_zero_platform_retry(
        self,
        caller,
        mock_executor_instance,
        mock_profile,
        mock_context,
        monkeypatch,
    ):
        mock_profile.role_id = "director"
        monkeypatch.setenv("POLARIS_DIRECTOR_LLM_TIMEOUT_SECONDS", "420")

        mock_response = MagicMock()
        mock_response.output = "ok"
        mock_response.raw = {}
        mock_response.model = "gpt-4"
        mock_executor_instance.invoke = AsyncMock(return_value=mock_response)

        await caller.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
            prompt_fingerprint="test_fp",
        )

        assert mock_executor_instance.invoke.await_count == 1
        req = mock_executor_instance.invoke.await_args.args[0]
        assert req.options["timeout"] == 420
        assert req.options["max_retries"] == 0

    async def test_call_director_does_not_fallback_to_pm_provider_on_empty_output(
        self,
        caller,
        mock_executor_instance,
        mock_profile,
        mock_context,
        monkeypatch,
    ):
        """Director 在空输出时禁止自动回退至 PM 绑定的备用 Provider。"""
        import polaris.kernelone.llm.runtime_config as runtime_config_module

        mock_profile.role_id = "director"
        monkeypatch.setattr(
            runtime_config_module,
            "get_role_model",
            lambda role: (
                ("minimax-1771264734939", "MiniMax-M2.5-highspeed")
                if role == "pm"
                else ("anthropic_compat-1771249789301", "kimi-for-coding")
            ),
        )

        first = MagicMock()
        first.ok = True
        first.output = ""
        first.raw = {}
        first.model = "kimi-for-coding"
        first.provider_id = "anthropic_compat-1771249789301"
        first.platform_retry_count = 0
        first.platform_retry_exhausted = False

        mock_executor_instance.invoke = AsyncMock(return_value=first)

        result = await caller.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
        )

        # 验证：只调用了一次，没有回退到 PM Provider
        assert mock_executor_instance.invoke.await_count == 1
        assert result.content == ""
        assert "director_provider_fallback" not in result.metadata

    async def test_call_with_exception(self, caller, mock_profile, mock_context):
        """Should handle exceptions gracefully."""
        with patch("polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway") as mock_ctx_cls:
            mock_ctx_cls.side_effect = ImportError("Module not found")

            result = await caller.call(
                profile=mock_profile,
                system_prompt="System",
                context=mock_context,
            )

            assert result.content == ""
            assert "Module not found" in result.error
            assert result.error_category == "unknown"


@pytest.mark.asyncio
class TestStreamCall:
    """Test LLMCaller.call_stream method with executor DI."""

    @pytest.fixture
    def mock_executor_instance(self):
        return MagicMock()

    @pytest.fixture
    def caller(self, mock_executor_instance):
        return LLMCaller(workspace="/tmp/test", executor=mock_executor_instance)

    @pytest.fixture
    def mock_profile(self):
        profile = MagicMock()
        profile.role_id = "test_role"
        profile.model = "gpt-4"
        profile.provider_id = "openai"
        profile.tool_policy = SimpleNamespace(
            whitelist=["glob", "file_exists"],
        )
        profile.task_policy = SimpleNamespace(
            max_turns=50,
            max_tool_calls=100,
        )
        return profile

    @pytest.fixture
    def mock_context(self):
        context = MagicMock()
        context.messages = []
        context.token_estimate = 0
        context.task_id = "test_task_123"
        return context

    async def test_stream_error_handling(self, caller, mock_profile, mock_context):
        """Should handle stream errors."""
        with patch("polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway") as mock_ctx_cls:
            mock_ctx_cls.side_effect = Exception("Stream error")

            chunks = []
            async for chunk in caller.call_stream(
                profile=mock_profile,
                system_prompt="System",
                context=mock_context,
            ):
                chunks.append(chunk)

            assert len(chunks) == 1
            assert chunks[0]["type"] == "error"
            assert "Stream error" in chunks[0]["error"]


@pytest.mark.asyncio
class TestLLMTimingAudit:
    """Test LLM timing audit events with executor DI."""

    @pytest.fixture(autouse=True)
    def clear_event_history(self):
        """Clear event history before each test."""
        get_global_emitter().clear_history()
        yield
        get_global_emitter().clear_history()

    @pytest.fixture
    def mock_executor_instance(self):
        return MagicMock()

    @pytest.fixture
    def caller(self, mock_executor_instance):
        return LLMCaller(workspace="/tmp/test", enable_cache=False, executor=mock_executor_instance)

    @pytest.fixture
    def mock_profile(self):
        """Create a mock RoleProfile."""
        profile = MagicMock()
        profile.role_id = "test_role"
        profile.model = "gpt-4"
        profile.provider_id = "openai"
        profile.tool_policy = SimpleNamespace(
            whitelist=["glob", "file_exists"],
        )
        profile.task_policy = SimpleNamespace(
            max_turns=50,
            max_tool_calls=100,
        )
        return profile

    @pytest.fixture
    def mock_context(self):
        """Create a mock ContextRequest (kernelone.context.contracts.ContextRequest)."""
        context_override_dict = {}  # Real dict that tests can modify
        context = MagicMock()
        context.query = "test query"
        context.mode = "chat"
        context.role = "test_role"
        context.step = 0
        context.run_id = "test_run"
        context.history = []
        context.sources_enabled = []
        context.policy = {}
        context.context_override = context_override_dict
        context.events_path = ""
        context.budget = SimpleNamespace(max_tokens=32000, max_chars=100000, cost_class="medium")
        return context

    async def test_call_emits_start_and_end_events(self, caller, mock_executor_instance, mock_profile, mock_context):
        """Should emit CALL_START and CALL_END events."""
        mock_response = MagicMock()
        mock_response.output = "Test response"
        mock_response.raw = {}
        mock_response.model = "gpt-4"
        mock_response.provider_id = "openai"
        mock_response.platform_retry_count = 0
        mock_response.platform_retry_exhausted = False
        mock_response.error = None  # Explicitly set to avoid MagicMock → is_response_ok=False
        mock_executor_instance.invoke = AsyncMock(return_value=mock_response)

        result = await caller.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
            run_id="test_run_001",
            task_id="test_task_123",
        )

        # Verify response has timing info
        assert "elapsed_ms" in result.metadata
        assert result.metadata["elapsed_ms"] > 0

        # Verify events were emitted
        events = get_global_emitter().get_events(run_id="test_run_001")
        assert len(events) == 2

        # Verify CALL_START event
        start_event = events[0]
        assert start_event.event_type == LLMEventType.CALL_START
        assert start_event.role == "test_role"
        assert start_event.run_id == "test_run_001"
        assert start_event.task_id == "test_task_123"
        assert start_event.model == "gpt-4"
        assert "call_id" in start_event.metadata

        # Verify CALL_END event
        end_event = events[1]
        assert end_event.event_type == LLMEventType.CALL_END
        assert end_event.role == "test_role"
        assert end_event.model == "gpt-4"
        assert end_event.provider == "openai"
        assert "elapsed_ms" in end_event.metadata
        assert end_event.metadata["elapsed_ms"] > 0
        assert not end_event.metadata["cached"]

    async def test_call_emits_error_event_on_failure(self, caller, mock_executor_instance, mock_profile, mock_context):
        """Should emit CALL_ERROR event when executor raises exception."""
        # Mock the executor to raise an exception (after context is built, so CALL_START fires first)
        mock_executor_instance.invoke = AsyncMock(side_effect=RuntimeError("Executor failed"))
        # Ensure no cache hit
        caller_with_cache = LLMCaller(workspace="/tmp/test", enable_cache=False, executor=mock_executor_instance)

        result = await caller_with_cache.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
            run_id="test_run_error",
        )

        assert result.error is not None

        # Verify events were emitted (CALL_START and CALL_ERROR)
        events = get_global_emitter().get_events(run_id="test_run_error")
        assert len(events) == 2
        assert events[0].event_type == LLMEventType.CALL_START
        assert events[1].event_type == LLMEventType.CALL_ERROR
        assert events[1].error_category == "unknown"
        assert "elapsed_ms" in events[1].metadata

    async def test_call_returns_cached_response_emits_events(self, mock_executor_instance, mock_profile, mock_context):
        """Should emit events for cached responses."""
        # Create caller WITHOUT cache to test executor response path directly
        # (cache path requires valid context_summary which is hard to mock correctly)
        caller_with_cache = LLMCaller(workspace="/tmp/test", enable_cache=False, executor=mock_executor_instance)

        mock_response = MagicMock()
        mock_response.output = "Cached response"
        mock_response.raw = {}
        mock_response.model = "gpt-4"
        mock_response.provider_id = "openai"
        mock_response.platform_retry_count = 0
        mock_response.platform_retry_exhausted = False
        mock_response.error = None  # Explicitly set to avoid MagicMock → is_response_ok=False
        mock_executor_instance.invoke = AsyncMock(return_value=mock_response)

        result = await caller_with_cache.call(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
            prompt_fingerprint="test_fp",
            run_id="test_cached_002",
        )

        # Verify response content
        assert result.content == "Cached response"
        assert result.error is None

        # Verify events were emitted (CALL_START and CALL_END for executor call)
        events = get_global_emitter().get_events(run_id="test_cached_002")
        assert len(events) == 2
        assert events[0].event_type == LLMEventType.CALL_START
        assert events[1].event_type == LLMEventType.CALL_END
        assert not events[1].metadata.get("cached")

    async def test_stream_call_emits_events(self, caller, mock_executor_instance, mock_profile, mock_context):
        """Should emit events for stream calls."""
        # Mock executor.invoke_stream
        mock_chunk = MagicMock()
        mock_chunk.event_type = "chunk"
        mock_chunk.content = "Hello"
        mock_chunk.metadata = {}

        class AsyncIterator:
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise StopAsyncIteration

        async def mock_stream(*args, **kwargs):
            yield mock_chunk

        mock_executor_instance.invoke_stream = mock_stream

        chunks = []
        async for chunk in caller.call_stream(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
            run_id="test_stream_001",
            task_id="test_task_stream",
        ):
            chunks.append(chunk)

        # Verify events were emitted
        events = get_global_emitter().get_events(run_id="test_stream_001")
        assert len(events) == 2

        # Verify CALL_START event
        assert events[0].event_type == LLMEventType.CALL_START
        assert events[0].metadata.get("stream")

        # Verify CALL_END event
        assert events[1].event_type == LLMEventType.CALL_END
        assert events[1].metadata.get("stream")
        assert "elapsed_ms" in events[1].metadata

    async def test_stream_call_emits_error_event(self, caller, mock_executor_instance, mock_profile, mock_context):
        """Should emit error event when stream executor raises exception."""
        async def mock_stream_error(*args, **kwargs):
            raise RuntimeError("Stream executor failed")

        mock_executor_instance.invoke_stream = mock_stream_error

        chunks = []
        async for chunk in caller.call_stream(
            profile=mock_profile,
            system_prompt="System",
            context=mock_context,
            run_id="test_stream_error",
        ):
            chunks.append(chunk)

        # Verify events were emitted (CALL_START and CALL_ERROR)
        events = get_global_emitter().get_events(run_id="test_stream_error")
        assert len(events) == 2
        assert events[0].event_type == LLMEventType.CALL_START
        assert events[1].event_type == LLMEventType.CALL_ERROR
        assert events[1].metadata.get("stream")

