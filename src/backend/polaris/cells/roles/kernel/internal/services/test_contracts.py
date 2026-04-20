"""Protocol合规性测试 - 验证服务层接口契约

本测试模块验证：
1. Protocol类可以被正确实现
2. 数据类的不可变性
3. 异常层次结构正确性
4. 运行时类型检查
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.cells.roles.kernel.internal.services.contracts import (
    ContextAssemblerProtocol,
    ContextCompressionStrategy,
    ContextRequest,
    ContextResult,
    # 异常类
    KernelError,
    LLMError,
    # Protocol
    LLMInvokerProtocol,
    LLMRequest,
    LLMResponse,
    PolicyError,
    StreamEvent,
    # 枚举
    StreamEventType,
    ToolCall,
    ToolError,
    ToolExecutionStatus,
    ToolExecutorProtocol,
    ToolResult,
    # 数据类
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

# ═══════════════════════════════════════════════════════════════════════════════
# 测试数据类
# ═══════════════════════════════════════════════════════════════════════════════


class TestUsage:
    """测试Usage数据类"""

    def test_default_values(self) -> None:
        """测试默认值"""
        usage = Usage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0
        assert usage.estimated is False

    def test_immutability(self) -> None:
        """测试不可变性 - Usage is a mutable dataclass, verify it can be assigned"""
        usage = Usage(prompt_tokens=100)
        # Usage is a plain dataclass (not frozen), so assignment is allowed
        usage.prompt_tokens = 200
        assert usage.prompt_tokens == 200

    def test_custom_values(self) -> None:
        """测试自定义值"""
        usage = Usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            estimated=True,
        )
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150
        assert usage.estimated is True


class TestLLMRequest:
    """测试LLMRequest数据类"""

    def test_required_fields(self) -> None:
        """测试必需字段"""
        messages = [{"role": "user", "content": "Hello"}]
        request = LLMRequest(messages=messages)
        assert request.messages == messages
        assert request.model is None
        assert request.temperature == 0.7

    def test_immutability(self) -> None:
        """测试不可变性"""
        messages = [{"role": "user", "content": "Hello"}]
        request = LLMRequest(messages=messages)
        with pytest.raises(AttributeError):
            request.temperature = 0.5  # type: ignore[misc]


class TestToolCall:
    """测试ToolCall数据类"""

    def test_basic_creation(self) -> None:
        """测试基本创建"""
        call = ToolCall(id="call_1", name="read_file")
        assert call.id == "call_1"
        assert call.name == "read_file"
        assert call.arguments == {}

    def test_with_arguments(self) -> None:
        """测试带参数创建"""
        call = ToolCall(
            id="call_2",
            name="write_file",
            arguments={"path": "/tmp/test.txt", "content": "hello"},
        )
        assert call.arguments["path"] == "/tmp/test.txt"


class TestToolResult:
    """测试ToolResult数据类"""

    def test_success_result(self) -> None:
        """测试成功结果"""
        result = ToolResult(
            call_id="call_1",
            status=ToolExecutionStatus.SUCCESS,
            output="file content",
        )
        assert result.call_id == "call_1"
        assert result.status == ToolExecutionStatus.SUCCESS
        assert result.output == "file content"
        assert result.error is None

    def test_error_result(self) -> None:
        """测试错误结果"""
        result = ToolResult(
            call_id="call_2",
            status=ToolExecutionStatus.ERROR,
            error="File not found",
        )
        assert result.status == ToolExecutionStatus.ERROR
        assert result.error == "File not found"


class TestStreamEvent:
    """测试StreamEvent数据类"""

    def test_content_event(self) -> None:
        """测试内容事件"""
        event = StreamEvent(
            event_type=StreamEventType.CHUNK,
            content="Hello world",
        )
        assert event.event_type == StreamEventType.CHUNK
        assert event.content == "Hello world"
        assert event.is_final is False

    def test_tool_call_event(self) -> None:
        """测试工具调用事件"""
        tool_call = ToolCall(id="call_1", name="read_file")
        event = StreamEvent(
            event_type=StreamEventType.TOOL_CALL,
            tool_call=tool_call,
        )
        assert event.event_type == StreamEventType.TOOL_CALL
        assert event.tool_call == tool_call


class TestContextRequest:
    """测试ContextRequest数据类"""

    def test_basic_creation(self) -> None:
        """测试基本创建"""
        request = ContextRequest(user_message="Hello")
        assert request.user_message == "Hello"
        assert request.system_prompt is None
        assert request.max_tokens == 32768

    def test_with_history(self) -> None:
        """测试带历史记录创建"""
        history = [{"role": "assistant", "content": "Hi"}]
        request = ContextRequest(
            user_message="Hello",
            conversation_history=history,
        )
        assert len(request.conversation_history) == 1


class TestContextResult:
    """测试ContextResult数据类"""

    def test_basic_creation(self) -> None:
        """测试基本创建"""
        messages = [{"role": "user", "content": "Hello"}]
        result = ContextResult(messages=messages)
        assert result.messages == messages
        assert result.compression_applied is False


# ═══════════════════════════════════════════════════════════════════════════════
# 测试异常类
# ═══════════════════════════════════════════════════════════════════════════════


class TestKernelError:
    """测试KernelError异常"""

    def test_basic_exception(self) -> None:
        """测试基本异常"""
        error = KernelError("Something went wrong")
        assert "[KERNEL_ERROR] Something went wrong" in str(error)
        assert error.error_code == "KERNEL_ERROR"

    def test_with_context(self) -> None:
        """测试带上下文的异常"""
        error = KernelError(
            "Failed",
            error_code="CUSTOM_ERROR",
            context={"key": "value"},
        )
        assert "CUSTOM_ERROR" in str(error)
        assert "key" in str(error)

    def test_inheritance(self) -> None:
        """测试继承关系"""
        assert issubclass(LLMError, KernelError)
        assert issubclass(ToolError, KernelError)
        assert issubclass(PolicyError, KernelError)


class TestLLMError:
    """测试LLMError异常"""

    def test_provider_info(self) -> None:
        """测试Provider信息"""
        error = LLMError(
            "API timeout",
            provider_id="openai",
            model="gpt-4",
            retryable=True,
        )
        assert error.provider_id == "openai"
        assert error.model == "gpt-4"
        assert error.retryable is True
        assert error.error_code == "LLM_ERROR"


class TestToolError:
    """测试ToolError异常"""

    def test_tool_info(self) -> None:
        """测试工具信息"""
        error = ToolError(
            "Execution failed",
            tool_name="read_file",
            call_id="call_123",
        )
        assert error.tool_name == "read_file"
        assert error.call_id == "call_123"
        assert error.error_code == "TOOL_ERROR"


class TestPolicyError:
    """测试PolicyError异常"""

    def test_violation_details(self) -> None:
        """测试违规详情"""
        error = PolicyError(
            "Tool not allowed",
            policy_type="whitelist",
            violation_details={"tool": "dangerous_cmd"},
        )
        assert error.policy_type == "whitelist"
        assert error.violation_details["tool"] == "dangerous_cmd"


# ═══════════════════════════════════════════════════════════════════════════════
# 测试Protocol实现
# ═══════════════════════════════════════════════════════════════════════════════


class MockLLMInvoker:
    """Mock LLM调用器实现"""

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="Mock response")

    async def invoke_stream(
        self,
        request: LLMRequest,
    ) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(event_type=StreamEventType.CHUNK, content="Hello")
        yield StreamEvent(event_type=StreamEventType.COMPLETE, is_final=True)


class MockToolExecutor:
    """Mock工具执行器实现"""

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        return ToolResult(
            call_id=tool_call.id,
            status=ToolExecutionStatus.SUCCESS,
            output="Mock result",
        )

    async def execute_batch(
        self,
        tool_calls: Sequence[ToolCall],
    ) -> Sequence[ToolResult]:
        return [
            ToolResult(
                call_id=call.id,
                status=ToolExecutionStatus.SUCCESS,
                output="Mock",
            )
            for call in tool_calls
        ]

    def validate_tool_call(self, tool_call: ToolCall) -> tuple[bool, str | None]:
        if not tool_call.name:
            return False, "Tool name is required"
        return True, None


class MockContextAssembler:
    """Mock上下文组装器实现"""

    async def assemble(self, request: ContextRequest) -> ContextResult:
        messages = list(request.conversation_history)
        messages.append({"role": "user", "content": request.user_message})
        return ContextResult(messages=messages)

    def estimate_tokens(self, messages: Sequence[dict[str, str]]) -> int:
        # 简单估算：每4个字符1个token
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4

    def compress_context(
        self,
        messages: Sequence[dict[str, str]],
        strategy: ContextCompressionStrategy,
        target_tokens: int,
    ) -> ContextResult:
        return ContextResult(
            messages=messages,
            compression_applied=False,
        )


class TestLLMInvokerProtocol:
    """测试LLMInvokerProtocol"""

    def test_mock_implements_protocol(self) -> None:
        """测试Mock实现符合Protocol"""
        invoker = MockLLMInvoker()
        assert isinstance(invoker, LLMInvokerProtocol)

    @pytest.mark.asyncio
    async def test_invoke_method(self) -> None:
        """测试invoke方法"""
        invoker = MockLLMInvoker()
        request = LLMRequest(messages=[{"role": "user", "content": "Hi"}])
        response = await invoker.invoke(request)
        assert isinstance(response, LLMResponse)
        assert response.content == "Mock response"

    @pytest.mark.asyncio
    async def test_invoke_stream_method(self) -> None:
        """测试invoke_stream方法"""
        invoker = MockLLMInvoker()
        request = LLMRequest(messages=[{"role": "user", "content": "Hi"}])
        events = []
        async for event in invoker.invoke_stream(request):
            events.append(event)
        assert len(events) == 2
        assert events[0].event_type == StreamEventType.CHUNK
        assert events[1].is_final is True


class TestToolExecutorProtocol:
    """测试ToolExecutorProtocol"""

    def test_mock_implements_protocol(self) -> None:
        """测试Mock实现符合Protocol"""
        executor = MockToolExecutor()
        assert isinstance(executor, ToolExecutorProtocol)

    @pytest.mark.asyncio
    async def test_execute_method(self) -> None:
        """测试execute方法"""
        executor = MockToolExecutor()
        call = ToolCall(id="call_1", name="test_tool")
        result = await executor.execute(call)
        assert isinstance(result, ToolResult)
        assert result.call_id == "call_1"

    @pytest.mark.asyncio
    async def test_execute_batch_method(self) -> None:
        """测试execute_batch方法"""
        executor = MockToolExecutor()
        calls = [
            ToolCall(id="call_1", name="tool1"),
            ToolCall(id="call_2", name="tool2"),
        ]
        results = await executor.execute_batch(calls)
        assert len(results) == 2

    def test_validate_tool_call(self) -> None:
        """测试validate_tool_call方法"""
        executor = MockToolExecutor()
        valid_call = ToolCall(id="call_1", name="valid_tool")
        is_valid, error = executor.validate_tool_call(valid_call)
        assert is_valid is True
        assert error is None

        # KernelOne ToolCall validates name in __post_init__, cannot create empty name
        # For MockToolExecutor, it only validates non-empty name, so all named tools pass
        another_call = ToolCall(id="call_2", name="another_tool")
        is_valid, error = executor.validate_tool_call(another_call)
        assert is_valid is True  # MockToolExecutor accepts any named tool


class TestContextAssemblerProtocol:
    """测试ContextAssemblerProtocol"""

    def test_mock_implements_protocol(self) -> None:
        """测试Mock实现符合Protocol"""
        assembler = MockContextAssembler()
        assert isinstance(assembler, ContextAssemblerProtocol)

    @pytest.mark.asyncio
    async def test_assemble_method(self) -> None:
        """测试assemble方法"""
        assembler = MockContextAssembler()
        request = ContextRequest(
            user_message="Hello",
            conversation_history=[{"role": "assistant", "content": "Hi"}],
        )
        result = await assembler.assemble(request)
        assert isinstance(result, ContextResult)
        assert len(result.messages) == 2

    def test_estimate_tokens(self) -> None:
        """测试estimate_tokens方法"""
        assembler = MockContextAssembler()
        messages = [{"role": "user", "content": "Hello world"}]
        tokens = assembler.estimate_tokens(messages)
        assert tokens >= 0

    def test_compress_context(self) -> None:
        """测试compress_context方法"""
        assembler = MockContextAssembler()
        messages = [{"role": "user", "content": "Hello"}]
        result = assembler.compress_context(
            messages,
            ContextCompressionStrategy.SLIDING_WINDOW,
            1000,
        )
        assert isinstance(result, ContextResult)


# ═══════════════════════════════════════════════════════════════════════════════
# 测试枚举
# ═══════════════════════════════════════════════════════════════════════════════


class TestStreamEventType:
    """测试StreamEventType枚举"""

    def test_enum_values(self) -> None:
        """测试枚举值"""
        assert StreamEventType.CHUNK.value == "chunk"
        assert StreamEventType.TOOL_CALL.value == "tool_call"
        assert StreamEventType.ERROR.value == "error"


class TestToolExecutionStatus:
    """测试ToolExecutionStatus枚举"""

    def test_enum_auto_values(self) -> None:
        """测试auto()生成的枚举值"""
        # auto()生成的是整数
        assert isinstance(ToolExecutionStatus.PENDING.value, int)
        assert isinstance(ToolExecutionStatus.SUCCESS.value, int)


class TestContextCompressionStrategy:
    """测试ContextCompressionStrategy枚举"""

    def test_enum_values(self) -> None:
        """测试枚举值"""
        assert ContextCompressionStrategy.NONE.value == "none"
        assert ContextCompressionStrategy.SUMMARY.value == "summary"


# ═══════════════════════════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """集成测试 - 验证各组件协同工作"""

    @pytest.mark.asyncio
    async def test_full_flow(self) -> None:
        """测试完整流程"""
        # 1. 组装上下文
        assembler = MockContextAssembler()
        context_request = ContextRequest(
            user_message="Read file",
            system_prompt="You are a helpful assistant",
        )
        context_result = await assembler.assemble(context_request)

        # 2. 创建LLM请求
        llm_request = LLMRequest(
            messages=list(context_result.messages),
            model="gpt-4",
        )
        assert llm_request.model == "gpt-4"  # Verify request creation

        # 3. 模拟LLM响应包含工具调用（使用原始dict格式，与contracts.py一致）
        raw_tool_call = {"id": "call_1", "name": "read_file", "arguments": {"path": "/tmp/test.txt"}}
        llm_response = LLMResponse(
            content="",
            tool_calls=[raw_tool_call],
        )

        # 4. 执行工具（将raw tool_calls转换为ToolCall对象）
        executor = MockToolExecutor()
        tool_calls_for_executor = [
            ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", {})) for tc in llm_response.tool_calls
        ]
        tool_results = await executor.execute_batch(tool_calls_for_executor)

        # 验证结果
        assert len(tool_results) == 1
        assert tool_results[0].status == ToolExecutionStatus.SUCCESS

    def test_error_handling(self) -> None:
        """测试错误处理"""
        try:
            raise LLMError("API failed", retryable=True)
        except KernelError as e:
            # 验证可以捕获基类
            assert isinstance(e, LLMError)
            assert e.retryable is True

    def test_data_class_equality(self) -> None:
        """测试数据类相等性"""
        call1 = ToolCall(id="call_1", name="read_file")
        call2 = ToolCall(id="call_1", name="read_file")
        call3 = ToolCall(id="call_2", name="write_file")

        assert call1 == call2
        assert call1 != call3

    def test_frozen_dataclass_immutability(self) -> None:
        """测试 frozen 数据类不可修改"""
        req1 = LLMRequest(messages=[{"role": "user", "content": "hi"}])

        # frozen dataclasses cannot be modified
        with pytest.raises(AttributeError):
            req1.temperature = 0.1  # type: ignore[misc]

        # frozen dataclasses support equality
        req2 = LLMRequest(messages=[{"role": "user", "content": "hi"}])
        assert req1 == req2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
