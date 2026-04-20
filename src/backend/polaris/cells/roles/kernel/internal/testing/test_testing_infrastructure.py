"""Self-tests for the testing infrastructure.

These tests verify that the fake implementations work correctly.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.testing import (
    FakeContextAssembler,
    FakeLLMExhaustedError,
    FakeLLMInvoker,
    FakeToolExecutor,
    FakeToolNotFoundError,
    HarnessConfigurationError,
    KernelTestHarness,
    LLMResponseBuilder,
)


class TestFakeLLMInvoker:
    """Tests for FakeLLMInvoker."""

    @pytest.mark.asyncio
    async def test_basic_response(self) -> None:
        """Test basic response enqueueing and retrieval."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response({"content": "Hello, world!"})

        response = await fake_llm.call(
            profile=None,
            system_prompt="Test",
            context=None,
        )

        assert response.content == "Hello, world!"
        assert response.is_success is True
        assert fake_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_responses(self) -> None:
        """Test multiple responses in sequence."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_responses(
            [
                {"content": "First"},
                {"content": "Second"},
                {"content": "Third"},
            ]
        )

        for expected in ["First", "Second", "Third"]:
            response = await fake_llm.call(None, "", None)
            assert response.content == expected

        assert fake_llm.call_count == 3

    @pytest.mark.asyncio
    async def test_response_with_tool_calls(self) -> None:
        """Test response with tool calls."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response(
            {
                "content": "I'll read the file.",
                "tool_calls": [
                    {"tool": "read_file", "args": {"path": "test.py"}},
                ],
            }
        )

        response = await fake_llm.call(None, "", None)

        assert response.content == "I'll read the file."
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0]["tool"] == "read_file"
        assert response.has_tool_calls is True

    @pytest.mark.asyncio
    async def test_error_response(self) -> None:
        """Test error response handling."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response(
            {
                "content": "",
                "error": "API Error",
                "error_category": "network",
            }
        )

        response = await fake_llm.call(None, "", None)

        assert response.error == "API Error"
        assert response.error_category == "network"
        assert response.is_success is False

    @pytest.mark.asyncio
    async def test_exception_injection(self) -> None:
        """Test injecting exceptions at specific calls."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response({"content": "First"})
        fake_llm.enqueue_exception(ValueError("Simulated error"), at_call=1)

        # First call succeeds
        response = await fake_llm.call(None, "", None)
        assert response.content == "First"

        # Second call raises - exception is not consumed, it stays at position 1
        with pytest.raises(ValueError, match="Simulated error"):
            await fake_llm.call(None, "", None)

        # After exception, no more responses available
        with pytest.raises(FakeLLMExhaustedError):
            await fake_llm.call(None, "", None)

    @pytest.mark.asyncio
    async def test_exhaustion_error(self) -> None:
        """Test error when responses are exhausted."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response({"content": "Only response"})

        # First call succeeds
        await fake_llm.call(None, "", None)

        # Second call raises exhaustion error
        with pytest.raises(FakeLLMExhaustedError):
            await fake_llm.call(None, "", None)

    @pytest.mark.asyncio
    async def test_call_recording(self) -> None:
        """Test that calls are recorded correctly."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response({"content": "Test"})

        class MockProfile:
            role_id = "test_role"

        await fake_llm.call(
            profile=MockProfile(),
            system_prompt="System prompt",
            context={"message": "Hello"},
            temperature=0.5,
            max_tokens=100,
        )

        records = fake_llm.call_records
        assert len(records) == 1
        assert records[0].system_prompt == "System prompt"
        assert records[0].temperature == 0.5
        assert records[0].max_tokens == 100

    @pytest.mark.asyncio
    async def test_streaming_response(self) -> None:
        """Test streaming response generation."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response(
            {
                "content": "Hello world",
                "thinking": "Let me think",
            }
        )

        events = []
        async for event in fake_llm.call_stream(None, "", None):
            events.append(event)

        # Should have thinking chunks, content chunks, and complete
        event_types = [e["type"] for e in events]
        assert "reasoning_chunk" in event_types
        assert "chunk" in event_types
        assert "complete" in event_types

    def test_reset(self) -> None:
        """Test reset functionality."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response({"content": "Test"})

        fake_llm.reset()

        assert fake_llm.call_count == 0
        assert len(fake_llm.call_records) == 0

    @pytest.mark.asyncio
    async def test_assert_call_count(self) -> None:
        """Test assert_call_count helper."""
        fake_llm = FakeLLMInvoker()
        fake_llm.enqueue_response({"content": "Test"})

        await fake_llm.call(None, "", None)

        fake_llm.assert_call_count(1)

        with pytest.raises(AssertionError):
            fake_llm.assert_call_count(2)


class TestLLMResponseBuilder:
    """Tests for LLMResponseBuilder."""

    def test_basic_build(self) -> None:
        """Test basic response building."""
        response = (
            LLMResponseBuilder().with_content("Hello").with_thinking("Thinking...").with_token_estimate(10).build()
        )

        assert response["content"] == "Hello"
        assert response["thinking"] == "Thinking..."
        assert response["token_estimate"] == 10

    def test_with_tool_calls(self) -> None:
        """Test building response with tool calls."""
        response = (
            LLMResponseBuilder()
            .with_content("I'll help")
            .with_tool_call("read_file", {"path": "test.py"}, call_id="call_1")
            .with_tool_call("write_file", {"path": "out.py", "content": "x"})
            .build()
        )

        assert len(response["tool_calls"]) == 2
        assert response["tool_calls"][0]["tool"] == "read_file"
        assert response["tool_calls"][0]["call_id"] == "call_1"

    def test_with_error(self) -> None:
        """Test building error response."""
        response = LLMResponseBuilder().with_error("API failed", category="network").build()

        assert response["error"] == "API failed"
        assert response["error_category"] == "network"

    def test_with_metadata(self) -> None:
        """Test building response with metadata."""
        response = (
            LLMResponseBuilder()
            .with_content("Hi")
            .with_metadata({"model": "gpt-4"})
            .with_metadata({"provider": "openai"})
            .build()
        )

        assert response["metadata"]["model"] == "gpt-4"
        assert response["metadata"]["provider"] == "openai"


class TestFakeToolExecutor:
    """Tests for FakeToolExecutor."""

    def test_static_result_registration(self) -> None:
        """Test registering tool with static result."""
        executor = FakeToolExecutor()
        executor.register_tool_with_result("read_file", {"success": True, "content": "data"})

        result = executor.execute_sync("read_file", {"path": "test.py"})

        assert result["success"] is True
        assert result["content"] == "data"

    def test_handler_registration(self) -> None:
        """Test registering tool with handler function."""
        executor = FakeToolExecutor()
        executor.register_tool(
            "multiply",
            lambda args: {"success": True, "result": args["a"] * args["b"]},
        )

        result = executor.execute_sync("multiply", {"a": 3, "b": 4})

        assert result["result"] == 12

    def test_tool_not_found(self) -> None:
        """Test error for unregistered tool."""
        executor = FakeToolExecutor()

        with pytest.raises(FakeToolNotFoundError):
            executor.execute_sync("unknown_tool", {})

    def test_default_result(self) -> None:
        """Test default result for unregistered tools."""
        executor = FakeToolExecutor()
        executor.set_default_result({"success": False, "error": "Not implemented"})

        result = executor.execute_sync("unknown_tool", {"arg": "value"})

        assert result["success"] is False
        assert result["tool"] == "unknown_tool"

    def test_call_recording(self) -> None:
        """Test that calls are recorded."""
        executor = FakeToolExecutor()
        executor.register_tool_with_result("read_file", {"success": True})

        executor.execute_sync("read_file", {"path": "a.py"})
        executor.execute_sync("read_file", {"path": "b.py"})

        assert executor.call_count == 2
        assert executor.call_records[0].args["path"] == "a.py"
        assert executor.call_records[1].args["path"] == "b.py"

    def test_approval_check(self) -> None:
        """Test approval requirement checking."""
        executor = FakeToolExecutor()
        executor.register_tool_with_result(
            "write_file",
            {"success": True},
            requires_approval=True,
        )
        executor.register_tool_with_result(
            "read_file",
            {"success": True},
            requires_approval=False,
        )

        assert executor.requires_approval("write_file") is True
        assert executor.requires_approval("read_file") is False

    def test_global_approval_policy(self) -> None:
        """Test global approval policy override."""
        executor = FakeToolExecutor()
        executor.register_tool_with_result("any_tool", {"success": True})

        # Set global policy that requires approval for write operations
        executor.set_global_approval_policy(
            lambda name, args: name.startswith("write_") or args.get("dangerous", False)
        )

        assert executor.requires_approval("write_file") is True
        assert executor.requires_approval("read_file") is False
        assert executor.requires_approval("other", {"dangerous": True}) is True

    def test_assert_called(self) -> None:
        """Test assert_called helper."""
        executor = FakeToolExecutor()
        executor.register_tool_with_result("read_file", {"success": True})

        executor.execute_sync("read_file", {"path": "test.py"})

        executor.assert_called("read_file")
        executor.assert_called("read_file", times=1)

        with pytest.raises(AssertionError):
            executor.assert_called("write_file")

    def test_assert_called_with(self) -> None:
        """Test assert_called_with helper."""
        executor = FakeToolExecutor()
        executor.register_tool_with_result("read_file", {"success": True})

        executor.execute_sync("read_file", {"path": "test.py", "limit": 100})

        executor.assert_called_with("read_file", path="test.py")
        executor.assert_called_with("read_file", limit=100)

        with pytest.raises(AssertionError):
            executor.assert_called_with("read_file", path="wrong.py")

    def test_bulk_registration(self) -> None:
        """Test registering multiple tools from dict."""
        executor = FakeToolExecutor()
        executor.register_tools_from_dict(
            {
                "read_file": {"success": True, "content": "file"},
                "list_dir": {"success": True, "entries": []},
            }
        )

        assert "read_file" in executor.registered_tools
        assert "list_dir" in executor.registered_tools


class TestFakeContextAssembler:
    """Tests for FakeContextAssembler."""

    def test_default_result(self) -> None:
        """Test default result configuration."""
        assembler = FakeContextAssembler()
        assembler.set_default_result(
            messages=[{"role": "user", "content": "Hello"}],
            token_estimate=10,
        )

        result = assembler.build_context(None)

        assert len(result.messages) == 1
        assert result.token_estimate == 10

    def test_build_count_tracking(self) -> None:
        """Test build count tracking."""
        assembler = FakeContextAssembler()

        assembler.build_context(None)
        assembler.build_context(None)

        assert assembler.build_count == 2

    def test_system_context_building(self) -> None:
        """Test system context building."""
        assembler = FakeContextAssembler()

        result = assembler.build_system_context("Base prompt", "Appendix")

        assert "Base prompt" in result
        assert "Appendix" in result

    def test_custom_system_template(self) -> None:
        """Test custom system context template."""
        assembler = FakeContextAssembler()
        assembler.set_system_context_template("[SYS]{base_prompt}[/SYS][APP]{appendix}[/APP]")

        result = assembler.build_system_context("Base", "App")

        assert "[SYS]Base[/SYS]" in result
        assert "[APP]App[/APP]" in result

    def test_request_handler(self) -> None:
        """Test request-specific handlers."""
        assembler = FakeContextAssembler()

        # Handler for specific request pattern
        def is_special_request(req):
            return hasattr(req, "special") and req.special

        assembler.add_request_handler(
            is_special_request,
            assembler.create_simple_result("Special!", token_estimate=5),
        )

        # Default for other requests
        assembler.set_default_result(
            messages=[{"role": "user", "content": "Normal"}],
            token_estimate=10,
        )

        class SpecialRequest:
            special = True

        class NormalRequest:
            special = False

        special_result = assembler.build_context(SpecialRequest())
        normal_result = assembler.build_context(NormalRequest())

        assert special_result.token_estimate == 5
        assert normal_result.token_estimate == 10

    def test_create_simple_result(self) -> None:
        """Test create_simple_result helper."""
        assembler = FakeContextAssembler()

        result = assembler.create_simple_result(
            user_message="Hello",
            system_message="System",
            token_estimate=20,
        )

        assert len(result.messages) == 2
        assert result.messages[0]["role"] == "system"
        assert result.messages[1]["role"] == "user"
        assert result.token_estimate == 20


class TestKernelTestHarness:
    """Tests for KernelTestHarness."""

    def test_basic_construction(self) -> None:
        """Test basic harness construction."""
        harness = KernelTestHarness()
        harness.with_workspace("/tmp/test")
        harness.with_role("architect")

        assert harness.config.workspace == "/tmp/test"
        assert harness.config.role == "architect"

    def test_fluent_api(self) -> None:
        """Test fluent API chaining."""
        harness = KernelTestHarness().with_workspace("/tmp/test").with_role("pm").with_structured_output(True)

        assert harness.config.workspace == "/tmp/test"
        assert harness.config.role == "pm"
        assert harness.config.use_structured_output is True

    def test_llm_configuration(self) -> None:
        """Test LLM configuration."""
        harness = (
            KernelTestHarness()
            .with_fake_llm()
            .with_llm_response({"content": "Hello"})
            .with_llm_response({"content": "World"})
        )

        assert harness.fake_llm is not None
        assert len(harness._fake_llm._responses) == 2  # type: ignore[union-attr]

    def test_tool_configuration(self) -> None:
        """Test tool configuration."""
        harness = (
            KernelTestHarness()
            .with_fake_tools()
            .with_tool_result("read_file", {"success": True})
            .with_tool_handler("custom", lambda args: {"result": "ok"})
        )

        assert harness.fake_tools is not None
        assert "read_file" in harness.fake_tools.registered_tools  # type: ignore[union-attr]

    def test_context_configuration(self) -> None:
        """Test context configuration."""
        harness = KernelTestHarness().with_fake_context().with_context_messages([{"role": "user", "content": "Hi"}])

        assert harness.fake_context is not None

    def test_reset(self) -> None:
        """Test harness reset."""
        harness = KernelTestHarness().with_fake_llm([{"content": "Test"}]).with_fake_tools({"tool": {"success": True}})

        harness.reset()

        assert harness.fake_llm.call_count == 0  # type: ignore[union-attr]
        assert harness._built is False

    def test_double_build_error(self) -> None:
        """Test that building twice raises error."""
        harness = KernelTestHarness().with_fake_llm().with_fake_tools()

        # First build should succeed
        kernel = harness.build()
        assert kernel is not None

        # Second build should raise
        with pytest.raises(HarnessConfigurationError):
            harness.build()


class TestIntegration:
    """Integration tests showing typical usage patterns."""

    @pytest.mark.asyncio
    async def test_simple_tool_execution_flow(self) -> None:
        """Test a simple tool execution flow."""
        # Set up harness
        harness = (
            KernelTestHarness()
            .with_fake_llm()
            .with_llm_response(
                LLMResponseBuilder()
                .with_content("I'll read the file.")
                .with_tool_call("read_file", {"path": "config.py"})
                .build()
            )
            .with_fake_tools()
            .with_tool_result("read_file", {"success": True, "content": "CONFIG = {}"})
        )

        # Get fake components for verification
        fake_llm = harness.fake_llm
        assert fake_llm is not None, "fake_llm should be set after with_fake_llm()"
        fake_tools = harness.fake_tools

        # Simulate execution
        llm_response = await fake_llm.call(None, "", None)
        assert llm_response.has_tool_calls

        tool_name = llm_response.tool_calls[0]["tool"]
        tool_args = llm_response.tool_calls[0]["args"]
        result = fake_tools.execute_sync(tool_name, tool_args)  # type: ignore[union-attr]

        assert result["content"] == "CONFIG = {}"

        # Verify calls
        fake_llm.assert_call_count(1)
        fake_tools.assert_called("read_file")  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self) -> None:
        """Test multi-turn conversation simulation."""
        harness = (
            KernelTestHarness()
            .with_fake_llm()
            .with_llm_response(LLMResponseBuilder().with_content("What file?").build())
            .with_llm_response(
                LLMResponseBuilder().with_content("Found it.").with_tool_call("read_file", {"path": "main.py"}).build()
            )
            .with_llm_response(LLMResponseBuilder().with_content("Done!").build())
        )

        fake_llm = harness.fake_llm
        assert fake_llm is not None, "fake_llm should be set after with_fake_llm()"

        # Simulate multi-turn
        responses = []
        for _ in range(3):
            resp = await fake_llm.call(None, "", None)
            responses.append(resp.content)

        assert responses == ["What file?", "Found it.", "Done!"]
        fake_llm.assert_call_count(3)

    def test_complex_tool_setup(self) -> None:
        """Test complex tool setup with handlers."""
        harness = (
            KernelTestHarness()
            .with_fake_tools()
            .with_tool_handler(
                "calculate",
                lambda args: {
                    "success": True,
                    "result": args["a"] + args["b"],
                },
            )
            .with_tool_handler(
                "validate",
                lambda args: {
                    "success": len(args["input"]) > 0,
                    "valid": len(args["input"]) > 0,
                },
            )
        )

        fake_tools = harness.fake_tools

        calc_result = fake_tools.execute_sync("calculate", {"a": 1, "b": 2})  # type: ignore[union-attr]
        assert calc_result["result"] == 3

        validate_result = fake_tools.execute_sync("validate", {"input": "test"})  # type: ignore[union-attr]
        assert validate_result["valid"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
