"""Tests for LLM Caller sub-components without existing coverage.

验证：
1. DecisionCaller 的决策阶段调用
2. FinalizationCaller 的收口阶段调用
3. Error handling 的错误分类
4. StreamEngine 的流式处理
5. EventEmitter 的事件发射
6. ProviderFormatter 的格式化
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.decision_caller import DecisionCaller
from polaris.cells.roles.kernel.internal.llm_caller.error_handling import (
    ERROR_CATEGORY_AUTH,
    ERROR_CATEGORY_NETWORK,
    ERROR_CATEGORY_RATE_LIMIT,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_UNKNOWN,
    build_native_tool_unavailable_error,
    build_text_response_fallback_instruction,
    classify_error,
    is_native_tool_calling_unsupported,
    is_response_format_unsupported,
    is_retryable_error,
)
from polaris.cells.roles.kernel.internal.llm_caller.event_emitter import LLMEventEmitter
from polaris.cells.roles.kernel.internal.llm_caller.finalization_caller import FinalizationCaller
from polaris.cells.roles.kernel.internal.llm_caller.provider_formatter import (
    AnnotatedProviderFormatter,
    NativeProviderFormatter,
    create_formatter,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import (
    LLMResponse,
)
from polaris.cells.roles.kernel.internal.llm_caller.stream_engine import StreamEngine

# ============ DecisionCaller Tests ============


@pytest.mark.asyncio
class TestDecisionCaller:
    """测试 DecisionCaller."""

    async def test_call_returns_dict(self) -> None:
        """call 应返回兼容 TransactionKernel 的字典."""
        invoker = Mock()
        invoker.call = AsyncMock(
            return_value=LLMResponse(
                content="decision",
                tool_calls=[{"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}],
                metadata={"model": "claude"},
            )
        )
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "read main.py"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(
            profile=profile,
            system_prompt="sys",
            context=context,
            tool_definitions=[{"name": "read_file"}],
        )

        assert result["content"] == "decision"
        assert len(result["tool_calls"]) == 1
        assert result["model"] == "unknown"

    async def test_call_raises_on_error(self) -> None:
        """LLM 返回 error 时应抛出 RuntimeError."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="", error="LLM failed", error_category="provider"))
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        with pytest.raises(RuntimeError, match="LLM failed"):
            await caller.call(profile=profile, system_prompt="sys", context=context)

    async def test_call_stream_delegates(self) -> None:
        """call_stream 应委托给 invoker.call_stream."""
        invoker = Mock()

        async def _mock_stream():
            yield {"chunk": "1"}

        invoker.call_stream = Mock(return_value=_mock_stream())
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        stream = await caller.call_stream(profile=profile, system_prompt="sys", context=context)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)

        assert len(chunks) == 1
        invoker.call_stream.assert_called_once()


async def async_generator(items: dict[str, Any]) -> Any:
    """Helper to create an async generator from a single item."""
    yield items


# ============ FinalizationCaller Tests ============


@pytest.mark.asyncio
class TestFinalizationCaller:
    """测试 FinalizationCaller."""

    async def test_call_returns_dict(self) -> None:
        """call 应返回兼容 TransactionKernel 的字典."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="final answer", metadata={"model": "claude"}))
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(profile=profile, system_prompt="sys", context=context)

        assert result["content"] == "final answer"
        assert result["tool_calls"] == []
        assert result["model"] == "unknown"

    async def test_call_raises_on_error(self) -> None:
        """LLM 返回 error 时应抛出 RuntimeError."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="", error="finalization failed"))
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        with pytest.raises(RuntimeError, match="finalization failed"):
            await caller.call(profile=profile, system_prompt="sys", context=context)

    def test_override_prebuilt_system_prompt(self) -> None:
        """应替换 prebuilt messages 中的 system prompt."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = {
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "old"},
                {"role": "user", "content": "hi"},
            ]
        }

        new_context = caller._override_prebuilt_system_prompt(context, "new prompt")

        override = new_context.context_override or {}
        messages = override["_transaction_kernel_prebuilt_messages"]
        assert messages[0]["content"] == "new prompt"
        assert messages[1]["content"] == "hi"

    def test_build_finalization_prompt_for_execution(self) -> None:
        """执行类请求应生成执行型提示词."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "write a file"
        context.history = ()
        context.task_id = None
        context.context_override = {"domain": "code"}

        prompt = caller._build_finalization_system_prompt(profile=profile, context=context)
        assert "FINAL ANSWER" in prompt
        assert "落地" in prompt or "执行" in prompt

    def test_build_finalization_prompt_for_analysis(self) -> None:
        """分析类请求应生成分析型提示词."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "architect"
        context = Mock()
        context.message = "explain this code"
        context.history = ()
        context.task_id = None
        context.context_override = {"domain": "code"}

        prompt = caller._build_finalization_system_prompt(profile=profile, context=context)
        assert "FINAL ANSWER" in prompt


# ============ Error Handling Tests ============


class TestClassifyError:
    """测试 classify_error."""

    def test_timeout_classification(self) -> None:
        """超时错误应分类为 timeout."""
        assert classify_error("Request timeout") == ERROR_CATEGORY_TIMEOUT
        assert classify_error("timed out") == ERROR_CATEGORY_TIMEOUT

    def test_rate_limit_classification(self) -> None:
        """429 错误应分类为 rate_limit."""
        assert classify_error("429 Too Many Requests") == ERROR_CATEGORY_RATE_LIMIT
        assert classify_error("rate limit exceeded") == ERROR_CATEGORY_RATE_LIMIT

    def test_network_classification(self) -> None:
        """网络错误应分类为 network."""
        assert classify_error("Connection refused") == ERROR_CATEGORY_NETWORK
        assert classify_error("DNS resolution failed") == ERROR_CATEGORY_NETWORK

    def test_auth_classification(self) -> None:
        """认证错误应分类为 auth."""
        assert classify_error("Unauthorized: invalid api key") == ERROR_CATEGORY_AUTH

    def test_unknown_fallback(self) -> None:
        """未知错误应分类为 unknown."""
        assert classify_error("something weird") == ERROR_CATEGORY_UNKNOWN

    def test_empty_string(self) -> None:
        """空字符串应分类为 unknown."""
        assert classify_error("") == ERROR_CATEGORY_UNKNOWN


class TestIsRetryableError:
    """测试 is_retryable_error."""

    def test_timeout_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_TIMEOUT) is True

    def test_network_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_NETWORK) is True

    def test_rate_limit_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_RATE_LIMIT) is True

    def test_auth_is_not_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_AUTH) is False

    def test_unknown_is_not_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_UNKNOWN) is False


class TestIsNativeToolCallingUnsupported:
    """测试 is_native_tool_calling_unsupported."""

    def test_tools_not_allowed(self) -> None:
        """tools not allowed 应被识别."""
        assert is_native_tool_calling_unsupported("tools is not allowed") is True

    def test_unknown_field(self) -> None:
        """unknown field 应被识别."""
        assert is_native_tool_calling_unsupported("unknown field: tools") is True

    def test_function_calling_not_supported(self) -> None:
        """function calling not supported 应被识别."""
        assert is_native_tool_calling_unsupported("function calling not supported") is True

    def test_normal_error(self) -> None:
        """普通错误不应被识别."""
        assert is_native_tool_calling_unsupported("model overloaded") is False

    def test_empty_string(self) -> None:
        """空字符串不应被识别."""
        assert is_native_tool_calling_unsupported("") is False


class TestIsResponseFormatUnsupported:
    """测试 is_response_format_unsupported."""

    def test_response_format_keyword(self) -> None:
        """response_format 关键字应被识别."""
        assert is_response_format_unsupported("unsupported parameter: response_format") is True

    def test_json_schema_keyword(self) -> None:
        """json_schema 关键字应被识别."""
        assert is_response_format_unsupported("does not support json schema") is True

    def test_normal_error(self) -> None:
        """普通错误不应被识别."""
        assert is_response_format_unsupported("model overloaded") is False


class TestBuildNativeToolUnavailableError:
    """测试 build_native_tool_unavailable_error."""

    def test_builds_error_message(self) -> None:
        """应构建包含 provider/model/tools 信息的错误消息."""
        profile = Mock()
        profile.provider_id = "test-provider"
        profile.model = "test-model"
        tp = Mock()
        tp.whitelist = ["read_file", "write_file"]
        profile.tool_policy = tp

        msg = build_native_tool_unavailable_error(profile)
        assert "native_tool_calling_unavailable" in msg
        assert "test-provider" in msg
        assert "test-model" in msg
        assert "read_file" in msg

    def test_empty_whitelist(self) -> None:
        """空白名单时应使用默认文本."""
        profile = Mock()
        profile.provider_id = "p"
        profile.model = "m"
        tp = Mock()
        tp.whitelist = []
        profile.tool_policy = tp

        msg = build_native_tool_unavailable_error(profile)
        assert "authorized_tools" in msg


class TestBuildTextResponseFallbackInstruction:
    """测试 build_text_response_fallback_instruction."""

    def test_includes_schema_name(self) -> None:
        """应包含 schema 名称."""

        class FakeModel:
            __name__ = "TestSchema"

            @classmethod
            def model_json_schema(cls) -> dict[str, Any]:
                return {"type": "object"}

        instruction = build_text_response_fallback_instruction(FakeModel)
        assert "TestSchema" in instruction or "FakeModel" in instruction


# ============ LLMEventEmitter Tests ============


class TestLLMEventEmitterInit:
    """测试 LLMEventEmitter 初始化."""

    def test_init(self) -> None:
        """基本初始化."""
        emitter = LLMEventEmitter(workspace="/ws")
        assert emitter.workspace == "/ws"


class TestLLMEventEmitterEmitCallStartEvent:
    """测试 emit_call_start_event."""

    def test_emits_with_basic_params(self) -> None:
        """基本参数应能发射事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_start_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["role"] == "director"
            assert kwargs["run_id"] == "run_1"

    def test_emits_with_event_emitter_override(self) -> None:
        """传入 event_emitter 时应使用其方法."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_start_event = Mock()

        emitter.emit_call_start_event(
            event_emitter=custom_emitter,
            role="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
        )
        custom_emitter._emit_call_start_event.assert_called_once()


class TestLLMEventEmitterEmitCallErrorEvent:
    """测试 emit_call_error_event."""

    def test_emits_error_event(self) -> None:
        """错误事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_error_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                error_category="timeout",
                error_message="timed out",
                call_id="call_1",
                elapsed_ms=1000.0,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["error_category"] == "timeout"
            assert kwargs["error_message"] == "timed out"


class TestLLMEventEmitterEmitCallEndEvent:
    """测试 emit_call_end_event."""

    def test_emits_end_event(self) -> None:
        """结束事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["completion_tokens"] == 50


class TestLLMEventEmitterEmitCallRetryEvent:
    """测试 emit_call_retry_event."""

    def test_emits_retry_event(self) -> None:
        """重试事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_retry_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=1,
                model="claude",
                call_id="call_1",
                retry_decision="backoff",
                backoff_seconds=2.0,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["retry_decision"] == "backoff"
            assert kwargs["backoff_seconds"] == 2.0


# ============ ProviderFormatter Tests ============


class TestCreateFormatter:
    """测试 create_formatter."""

    def test_openai_formatter(self) -> None:
        """openai 应返回 NativeProviderFormatter."""
        fmt = create_formatter("openai")
        assert isinstance(fmt, NativeProviderFormatter)

    def test_anthropic_formatter(self) -> None:
        """anthropic 应返回 NativeProviderFormatter."""
        fmt = create_formatter("anthropic")
        assert isinstance(fmt, NativeProviderFormatter)

    def test_annotated_formatter(self) -> None:
        """annotated 应返回 AnnotatedProviderFormatter."""
        fmt = create_formatter("annotated")
        assert isinstance(fmt, AnnotatedProviderFormatter)

    def test_unknown_defaults_to_annotated(self) -> None:
        """未知 provider 应默认返回 AnnotatedProviderFormatter."""
        fmt = create_formatter("unknown")
        assert isinstance(fmt, AnnotatedProviderFormatter)


class TestNativeProviderFormatter:
    """测试 NativeProviderFormatter."""

    def test_format_tools_passes_through(self) -> None:
        """原生格式化应直接透传."""
        fmt = NativeProviderFormatter()
        tools = [{"name": "read_file"}]
        assert fmt.format_tools(tools, "openai") == tools

    def test_format_messages_passes_through(self) -> None:
        """原生格式化应直接透传消息."""
        fmt = NativeProviderFormatter()
        from unittest.mock import Mock

        event = Mock()
        event.role = "user"
        event.content = "hello"
        messages: list[Any] = [event]
        assert fmt.format_messages(messages) == [{"role": "user", "content": "hello"}]


class TestAnnotatedProviderFormatter:
    """测试 AnnotatedProviderFormatter."""

    def test_format_tools_passes_through(self) -> None:
        """应直接透传工具 schema."""
        fmt = AnnotatedProviderFormatter()
        tools = [{"name": "read_file", "description": "Read a file"}]
        result = fmt.format_tools(tools, "openai")
        assert len(result) == 1
        assert result[0]["name"] == "read_file"

    def test_format_messages_passes_through(self) -> None:
        """应直接透传消息."""
        fmt = AnnotatedProviderFormatter()
        from unittest.mock import Mock

        event = Mock()
        event.role = "user"
        event.content = "hello"
        messages: list[Any] = [event]
        assert fmt.format_messages(messages) == [{"role": "user", "content": "hello"}]


# ============ StreamEngine Tests ============


class TestStreamEngineInit:
    """测试 StreamEngine 初始化."""

    def test_init(self) -> None:
        """基本初始化."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )
        assert engine.workspace == "/ws"


@pytest.mark.asyncio
class TestStreamEngineRunStream:
    """测试 StreamEngine.run_stream."""

    async def test_cancel_before_invoke(self) -> None:
        """取消标志设置时应立即抛出 CancelledError."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {"stream_cancelled": True}

        profile = Mock()
        profile.role_id = "director"

        prepared = Mock()
        prepared.messages = []
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = None

        with pytest.raises(asyncio.CancelledError):
            async for _event in engine.run_stream(
                profile=profile,
                prepared=prepared,
                context=context,
                start_time=0.0,
                role_id="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                event_emitter=None,
                turn_round=0,
            ):
                pass

    async def test_empty_stream(self) -> None:
        """空流应正常完成."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False

        profile = Mock()
        profile.role_id = "director"

        prepared = Mock()
        prepared.messages = []
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = None

        # Mock executor to return empty stream
        mock_executor = Mock()

        async def _empty_stream(_request):
            return
            yield

        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events = []
        async for event in engine.run_stream(
            profile=profile,
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        # Should have at least context_metadata event
        assert any(e.get("type") == "context_metadata" for e in events)


async def async_empty_generator() -> Any:
    """Helper: empty async generator."""
    if False:
        yield  # Make it a generator
