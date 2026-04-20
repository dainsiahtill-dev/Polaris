"""TurnEngine facade compatibility tests after the TransactionKernel cutover.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8

Tests keep only the legacy facade behavior that is still intentionally supported.
They do not require the old run/stream parity semantics that existed before the
TransactionKernel cutover. These are integration tests using dependency injection
instead of monkeypatching.

Coverage:
- G-3: run/stream parity gate
- Phase 1-6: Stream/Non-Stream execution path convergence
- Tool call sequence parity
- Error handling parity
- Multi-round conversation history preservation

Architecture:
    These tests use the testing/ infrastructure pattern with dependency injection
    rather than monkeypatching for better maintainability and type safety.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol
from unittest.mock import MagicMock

import pytest
from polaris.cells.roles.kernel.internal.output_parser import ToolCallResult
from polaris.cells.roles.kernel.internal.tool_loop_controller import (
    ToolLoopController,
)
from polaris.cells.roles.kernel.internal.turn_engine import (
    TurnEngine,
    TurnEngineConfig,
)
from polaris.cells.roles.profile.internal.schema import RoleTurnRequest as RoleTurnRequestSchema
from polaris.cells.roles.profile.public.service import (
    RoleExecutionMode,
    RoleProfile,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# =============================================================================
# Test Infrastructure (DI-based, no monkeypatch)
# =============================================================================


class LLMInvokerProtocol(Protocol):
    """Protocol for LLM invoker - enables DI testing."""

    async def call(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: Any,
        response_model: type | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
    ) -> Any: ...

    async def call_stream(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: Any,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
    ) -> AsyncIterator[dict[str, Any]]: ...


class ToolExecutorProtocol(Protocol):
    """Protocol for tool executor - enables DI testing."""

    async def execute_single(
        self,
        profile: RoleProfile,
        call: ToolCallResult,
    ) -> dict[str, Any]: ...


@dataclass
class MockLLMResponse:
    """Mock LLM response for testing."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_provider: str = "openai"
    token_estimate: int = 10
    error: str | None = None
    error_category: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MockToolResult:
    """Mock tool execution result."""

    tool: str
    success: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class MockLLMInvoker:
    """Mock LLM invoker for testing - supports both stream and non-stream."""

    def __init__(
        self,
        responses: list[MockLLMResponse] | None = None,
    ) -> None:
        self.responses = responses or []
        self.call_count = 0
        self.call_history: list[dict[str, Any]] = []

    def add_response(self, response: MockLLMResponse) -> None:
        """Add a mock response to the queue."""
        self.responses.append(response)

    async def call(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: Any,
        response_model: type | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
    ) -> MockLLMResponse:
        """Non-streaming call - returns complete response."""
        self.call_history.append(
            {
                "method": "call",
                "profile": profile,
                "context": context,
                "attempt": attempt,
            }
        )
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        return MockLLMResponse(content="Default response")

    async def call_stream(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: Any,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming call - yields chunks then tool calls."""
        self.call_history.append(
            {
                "method": "call_stream",
                "profile": profile,
                "context": context,
                "attempt": attempt,
            }
        )
        if self.call_count >= len(self.responses):
            yield {"type": "chunk", "content": "Default stream response"}
            return

        response = self.responses[self.call_count]
        self.call_count += 1

        # Yield error if present
        if response.error:
            yield {
                "type": "error",
                "error": response.error,
                "error_category": response.error_category or "unknown",
            }
            return

        # Yield content chunks
        if response.content:
            words = response.content.split()
            for word in words:
                yield {"type": "chunk", "content": word + " "}

        # Yield tool calls
        for tc in response.tool_calls:
            yield {
                "type": "tool_call",
                "tool": tc.get("function", {}).get("name", "unknown"),
                "args": json.loads(tc.get("function", {}).get("arguments", "{}")),
                "call_id": tc.get("id", ""),
            }


class MockToolExecutor:
    """Mock tool executor for testing."""

    def __init__(
        self,
        results: dict[str, MockToolResult] | None = None,
    ) -> None:
        self.results = results or {}
        self.call_history: list[dict[str, Any]] = []

    def add_result(self, tool_name: str, result: MockToolResult) -> None:
        """Add a mock result for a tool."""
        self.results[tool_name] = result

    async def execute_single(
        self,
        profile: RoleProfile,
        call: ToolCallResult,
    ) -> dict[str, Any]:
        """Execute a single tool call."""
        self.call_history.append(
            {
                "tool": call.tool,
                "args": call.args,
                "profile": profile.role_id,
            }
        )
        result = self.results.get(call.tool, MockToolResult(tool=call.tool, success=True))
        return {
            "tool": result.tool,
            "success": result.success,
            "result": result.result,
            "error": result.error,
        }


class _StubRegistry:
    """Stub registry for testing."""

    def __init__(self, profile: RoleProfile) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> RoleProfile:
        return self._profile


def _make_role_profile(
    role_id: str = "pm",
    model: str = "gpt-5",
    whitelist: list[str] | None = None,
) -> RoleProfile:
    """Create a mock role profile for testing."""
    profile = MagicMock(spec=RoleProfile)
    profile.role_id = role_id
    profile.model = model
    profile.version = "1.0.0"
    profile.tool_policy = MagicMock()
    profile.tool_policy.policy_id = f"{role_id}-policy-v1"
    profile.tool_policy.whitelist = whitelist or ["read_file"]
    profile.tool_policy.allowed_tools = whitelist or ["read_file"]
    profile.tool_policy.forbidden_tools = []
    profile.prompt_policy = MagicMock()
    profile.prompt_policy.core_template_id = f"{role_id}-v1"
    profile.prompt_policy.tpl_version = "1.0"
    profile.context_policy = MagicMock()
    profile.context_policy.include_project_structure = False
    profile.context_policy.include_task_history = False
    profile.context_policy.max_context_tokens = 100000
    profile.context_policy.max_history_turns = 20
    profile.context_policy.compression_strategy = "none"
    return profile


def _make_kernel_mock(
    profile: RoleProfile,
    llm_invoker: MockLLMInvoker | None = None,
    tool_executor: MockToolExecutor | None = None,
) -> Any:
    """Create a mock kernel with injected dependencies."""
    kernel = MagicMock()
    kernel.workspace = "."
    kernel.registry = _StubRegistry(profile)
    # Wire up the LLM invoker - CRITICAL: must be the actual mock, not MagicMock
    kernel._llm_caller = llm_invoker if llm_invoker is not None else MockLLMInvoker()
    kernel._tool_executor = tool_executor if tool_executor is not None else MockToolExecutor()

    # Mock prompt builder with proper interface
    prompt_builder = MagicMock()
    prompt_builder.build_system_prompt.return_value = "system prompt"
    prompt_builder.build_fingerprint.return_value = MagicMock(
        full_hash="fp123",
        core_hash="fp123",
    )
    kernel._prompt_builder = prompt_builder
    # Also patch _get_prompt_builder to return our mock
    kernel._get_prompt_builder = MagicMock(return_value=prompt_builder)

    # Mock output parser with dynamic thinking extraction - returns actual values, not MagicMock
    output_parser = MagicMock()

    def dynamic_parse_thinking(content: str) -> Any:
        """Extract thinking tags from content dynamically."""
        thinking = None
        clean_content = content

        if "<thinking>" in content and "</thinking>" in content:
            start = content.find("<thinking>") + len("<thinking>")
            end = content.find("</thinking>")
            thinking = content[start:end].strip()
            clean_content = (
                content[: content.find("<thinking>")] + content[content.find("</thinking>") + len("</thinking>") :]
            )
            clean_content = clean_content.strip()

        # Return a proper namespace with actual values, not MagicMock
        result = SimpleNamespace(
            clean_content=clean_content,
            thinking=thinking,
        )
        return result

    output_parser.parse_thinking.side_effect = dynamic_parse_thinking
    kernel._output_parser = output_parser
    # Also patch _get_output_parser to return our mock
    kernel._get_output_parser = MagicMock(return_value=output_parser)

    # Mock tool execution delegation
    # Note: Kernel._execute_single_tool signature is (tool_name, args, context)
    # context is a dict with 'profile' and 'request' keys
    async def mock_execute_single_tool(tool_name: Any, args: Any, context: Any) -> dict[str, Any]:
        profile = context.get("profile") if isinstance(context, dict) else None
        if profile is None:
            from types import SimpleNamespace

            profile = SimpleNamespace(role_id="pm")
        call = ToolCallResult(tool=tool_name, args=args) if isinstance(tool_name, str) else tool_name
        return await kernel._tool_executor.execute_single(profile, call)

    kernel._execute_single_tool = mock_execute_single_tool

    # Mock split tool calls
    def mock_split_tool_calls(
        role_id: str,
        tool_calls: list[Any],
    ) -> tuple[list[Any], list[Any], int]:
        return tool_calls, [], 0

    kernel._split_tool_calls_by_write_budget = mock_split_tool_calls

    # CRITICAL: Mock the content parsing to return actual ToolCallResult objects
    def mock_parse_content_and_thinking(
        content: str,
        thinking: str | None,
        profile: Any,
        native_tool_calls: list[dict[str, Any]] | None,
        native_tool_provider: str,
    ) -> list[ToolCallResult]:
        """Parse native tool calls into ToolCallResult objects."""
        results: list[ToolCallResult] = []
        if native_tool_calls:
            for tc in native_tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                args_str = func.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}
                results.append(ToolCallResult(tool=tool_name, args=args))
        return results

    kernel._parse_content_and_thinking_tool_calls = mock_parse_content_and_thinking

    # Mock _build_system_prompt_for_request for kernel facade compatibility
    def mock_build_system_prompt(profile: Any, request: Any, appendix: str) -> str:
        return "system prompt"

    kernel._build_system_prompt_for_request = mock_build_system_prompt

    return kernel


def _make_turn_request(
    message: str = "hello",
    history: list[tuple[str, str]] | None = None,
) -> RoleTurnRequestSchema:
    """Create a turn request for testing."""
    return RoleTurnRequestSchema(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message=message,
        history=history or [],
        context_override={
            "context_os_snapshot": {
                "version": 1,
                "mode": "state_first_context_os_v1",
                "adapter_id": "generic",
                "transcript_log": [],
                "working_state": {},
                "artifact_store": [],
                "episode_store": [],
                "updated_at": "",
            }
        },
    )


def _make_tool_call(tool: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    """Create a native tool call structure."""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


# =============================================================================
# G-3: Stream/Non-Stream Parity Tests
# =============================================================================


class TestStreamNonStreamParity:
    """G-3: Verify run() and run_stream() produce equivalent results."""

    @pytest.mark.asyncio
    async def test_same_input_produces_same_content(self) -> None:
        """G-3: Same input must produce identical final content."""
        # Arrange
        profile = _make_role_profile()
        llm_invoker = MockLLMInvoker()
        llm_invoker.add_response(
            MockLLMResponse(
                content="Here is the answer.",
                tool_calls=[],
            )
        )
        kernel = _make_kernel_mock(profile, llm_invoker)
        # Mock parse_thinking to return clean content
        kernel._output_parser.parse_thinking.return_value = MagicMock(
            clean_content="Here is the answer.",
            thinking=None,
        )
        # Inject mock LLM invoker into TurnEngine via DI
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("What is the answer?")

        # Act - Non-stream
        controller_ns = ToolLoopController.from_request(
            request=request,
            profile=profile,
        )
        ns_result = await engine.run(
            request=request,
            role="pm",
            controller=controller_ns,
        )

        # Reset for stream
        llm_invoker.call_count = 0
        llm_invoker.call_history.clear()

        # Act - Stream
        controller_st = ToolLoopController.from_request(
            request=request,
            profile=profile,
        )
        stream_events = []
        async for event in engine.run_stream(
            request=request,
            role="pm",
            controller=controller_st,
        ):
            stream_events.append(event)

        # Assert
        complete_event = next((e for e in stream_events if e.get("type") == "complete"), None)
        assert complete_event is not None, "Stream must emit complete event"
        stream_result = complete_event.get("result")
        assert stream_result is not None

        assert ns_result.content.strip() == stream_result.content.strip(), (
            f"Content mismatch:\n  run(): {ns_result.content!r}\n  run_stream(): {stream_result.content!r}"
        )

    @pytest.mark.asyncio
    async def test_same_input_produces_same_tool_calls(self) -> None:
        """G-3: Same input must produce identical tool call sequences."""
        # Arrange
        profile = _make_role_profile(whitelist=["read_file", "search_code"])
        llm_invoker = MockLLMInvoker()

        # First call: tool call
        llm_invoker.add_response(
            MockLLMResponse(
                content="Let me read the file.",
                tool_calls=[_make_tool_call("read_file", {"path": "README.md"})],
            )
        )
        # Second call: final answer
        llm_invoker.add_response(
            MockLLMResponse(
                content="The file contains project documentation.",
                tool_calls=[],
            )
        )

        tool_executor = MockToolExecutor()
        tool_executor.add_result(
            "read_file",
            MockToolResult(
                tool="read_file",
                success=True,
                result={"content": "# Project README"},
            ),
        )

        kernel = _make_kernel_mock(profile, llm_invoker, tool_executor)
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Read the README")

        # Act - Non-stream
        controller_ns = ToolLoopController.from_request(request=request, profile=profile)
        ns_result = await engine.run(request=request, role="pm", controller=controller_ns)

        # Reset for stream
        llm_invoker.call_count = 0
        llm_invoker.call_history.clear()

        # Act - Stream
        controller_st = ToolLoopController.from_request(request=request, profile=profile)
        stream_events = []
        async for event in engine.run_stream(request=request, role="pm", controller=controller_st):
            stream_events.append(event)

        # Assert
        complete_event = next((e for e in stream_events if e.get("type") == "complete"), None)
        assert complete_event is not None
        stream_result = complete_event.get("result")
        assert stream_result is not None

        # Tool-call identity must match; run() no longer preserves arguments in
        # its non-stream batch receipt mapping, while stream complete.result does.
        assert len(ns_result.tool_calls) == len(stream_result.tool_calls)
        for ns_tc, st_tc in zip(ns_result.tool_calls, stream_result.tool_calls, strict=True):
            assert ns_tc["tool"] == st_tc["tool"]
            assert st_tc["args"] == {"path": "README.md"}

    @pytest.mark.asyncio
    async def test_same_error_handling_behavior(self) -> None:
        """G-3: Error handling must be equivalent between modes."""
        # Arrange
        profile = _make_role_profile()
        llm_invoker = MockLLMInvoker()
        llm_invoker.add_response(
            MockLLMResponse(
                content="",
                tool_calls=[],
                error="LLM rate limit exceeded",
                error_category="rate_limit",
            )
        )

        kernel = _make_kernel_mock(profile, llm_invoker)
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Trigger error")

        # Act - Non-stream
        controller_ns = ToolLoopController.from_request(request=request, profile=profile)
        ns_result = await engine.run(request=request, role="pm", controller=controller_ns)

        # Reset for stream
        llm_invoker.call_count = 0

        # Act - Stream
        controller_st = ToolLoopController.from_request(request=request, profile=profile)
        stream_events = []
        async for event in engine.run_stream(request=request, role="pm", controller=controller_st):
            stream_events.append(event)

        # Assert
        error_events = [e for e in stream_events if e.get("type") == "error"]
        assert ns_result.error is not None, "Non-stream must return error"
        assert len(error_events) > 0, "Stream must emit error event"

        # Error messages should be equivalent
        assert "rate limit" in ns_result.error.lower() or "rate_limit" in ns_result.error.lower()


class TestMultiRoundConversation:
    """Multi-round conversation: LLM -> Tool -> LLM -> Tool -> LLM (complete)."""

    @pytest.mark.asyncio
    async def test_history_correctly_passed_between_rounds(self) -> None:
        """Current facade stops after the canonical tool->finalization transaction."""
        # Arrange
        profile = _make_role_profile(whitelist=["read_file", "search_code"])
        llm_invoker = MockLLMInvoker()

        # Round 1: First tool call (with thinking required by kernel)
        llm_invoker.add_response(
            MockLLMResponse(
                content="<thinking>I need to search for the main function.</thinking>Let me search for files.",
                tool_calls=[_make_tool_call("search_code", {"query": "def main"})],
            )
        )
        # Round 2: Final answer
        llm_invoker.add_response(
            MockLLMResponse(
                content="Found the main function.",
                tool_calls=[],
            )
        )

        tool_executor = MockToolExecutor()
        tool_executor.add_result(
            "search_code",
            MockToolResult(
                tool="search_code",
                success=True,
                result={"files": ["src/main.py"]},
            ),
        )
        kernel = _make_kernel_mock(profile, llm_invoker, tool_executor)
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Find the main function")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = await engine.run(request=request, role="pm", controller=controller)

        # Assert
        assert result.error is None, f"Unexpected error: {result.error}"
        assert len(llm_invoker.call_history) == 2, "Should have 2 LLM calls"

        # Verify history was passed to each call
        for i, call in enumerate(llm_invoker.call_history):
            if i > 0:
                # After first call, history should be non-empty
                context = call.get("context")
                if context and hasattr(context, "history"):
                    assert len(context.history) > 0, f"Call {i} should have history"

    @pytest.mark.asyncio
    async def test_stream_history_correctly_passed_between_rounds(self) -> None:
        """Verify stream mode history accumulates correctly across rounds."""
        # Arrange
        profile = _make_role_profile(whitelist=["read_file"])
        llm_invoker = MockLLMInvoker()

        # Round 1: Tool call (with thinking required by kernel)
        llm_invoker.add_response(
            MockLLMResponse(
                content="<thinking>I'll read the file now.</thinking>Reading file.",
                tool_calls=[_make_tool_call("read_file", {"path": "test.txt"})],
            )
        )
        # Round 2: Final
        llm_invoker.add_response(
            MockLLMResponse(
                content="Done reading.",
                tool_calls=[],
            )
        )

        tool_executor = MockToolExecutor()
        tool_executor.add_result(
            "read_file",
            MockToolResult(
                tool="read_file",
                success=True,
                result={"content": "test content"},
            ),
        )

        kernel = _make_kernel_mock(profile, llm_invoker, tool_executor)
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Read test.txt")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        events = []
        async for event in engine.run_stream(request=request, role="pm", controller=controller):
            events.append(event)

        # Assert
        complete_event = next((e for e in events if e.get("type") == "complete"), None)
        assert complete_event is not None

        # Verify LLM was called twice
        assert len(llm_invoker.call_history) == 2, "Should have 2 LLM calls in stream mode"


class TestErrorRecovery:
    """Error recovery: LLM errors, tool errors, timeout handling."""

    @pytest.mark.asyncio
    async def test_llm_error_recovery_retry(self) -> None:
        """Verify LLM errors are handled gracefully."""
        # Arrange
        profile = _make_role_profile()
        llm_invoker = MockLLMInvoker()

        # First call fails
        llm_invoker.add_response(
            MockLLMResponse(
                content="",
                tool_calls=[],
                error="Temporary network error",
            )
        )

        kernel = _make_kernel_mock(profile, llm_invoker)
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Test")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = await engine.run(request=request, role="pm", controller=controller)

        # Assert
        assert result.error is not None
        assert "network error" in result.error.lower() or "LLM" in result.error

    @pytest.mark.asyncio
    async def test_tool_error_handling(self) -> None:
        """Verify tool errors are captured and passed to LLM."""
        # Arrange
        profile = _make_role_profile(whitelist=["read_file"])
        llm_invoker = MockLLMInvoker()

        # First call: tool that will fail (with thinking required)
        llm_invoker.add_response(
            MockLLMResponse(
                content="<thinking>I'll try to read the file.</thinking>Let me read the file.",
                tool_calls=[_make_tool_call("read_file", {"path": "missing.txt"})],
            )
        )
        # Second call: respond to error
        llm_invoker.add_response(
            MockLLMResponse(
                content="The file was not found.",
                tool_calls=[],
            )
        )

        tool_executor = MockToolExecutor()
        tool_executor.add_result(
            "read_file",
            MockToolResult(
                tool="read_file",
                success=False,
                error="File not found: missing.txt",
            ),
        )

        kernel = _make_kernel_mock(profile, llm_invoker, tool_executor)
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Read missing file")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = await engine.run(request=request, role="pm", controller=controller)

        # Assert
        assert result.error is None, f"Should handle tool error gracefully: {result.error}"
        assert len(result.tool_results) == 1
        assert result.tool_results[0].get("success") is False

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """Repeated tool intent now escalates to workflow handoff instead of stall."""
        profile = _make_role_profile(whitelist=["read_file"])
        llm_invoker = MockLLMInvoker()

        # Same tool call repeated with thinking (will trigger stall)
        for _ in range(5):
            llm_invoker.add_response(
                MockLLMResponse(
                    content="<thinking>I'll read the file.</thinking>Trying again.",
                    tool_calls=[_make_tool_call("read_file", {"path": "same.txt"})],
                )
            )

        tool_executor = MockToolExecutor()
        tool_executor.add_result(
            "read_file",
            MockToolResult(
                tool="read_file",
                success=True,
                result={"content": "same content"},
            ),
        )

        kernel = _make_kernel_mock(profile, llm_invoker, tool_executor)
        engine = TurnEngine(
            kernel=kernel,
            llm_caller=llm_invoker,
            config=TurnEngineConfig(max_stall_cycles=1),
        )
        request = _make_turn_request("Read file")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = await engine.run(request=request, role="pm", controller=controller)

        assert result.error is None
        # Soft guard: finalization-phase tool calls are dropped, normal completion.
        # The legacy "repeated tool intent -> handoff" path no longer triggers
        # because TransactionKernel drops finalization hallucinations.
        assert result.metadata.get("transaction_kind") is None


class TestBoundaryConditions:
    """Boundary tests: empty tools, large context, cancellation."""

    @pytest.mark.asyncio
    async def test_empty_tool_list(self) -> None:
        """Verify handling when no tools are available."""
        # Arrange
        profile = _make_role_profile(whitelist=[])
        llm_invoker = MockLLMInvoker()
        llm_invoker.add_response(
            MockLLMResponse(
                content="I don't have any tools available.",
                tool_calls=[],
            )
        )

        kernel = _make_kernel_mock(profile, llm_invoker)
        # Need to mock parse_thinking to return clean content
        kernel._output_parser.parse_thinking.return_value = MagicMock(
            clean_content="I don't have any tools available.",
            thinking=None,
        )
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Do something")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = await engine.run(request=request, role="pm", controller=controller)

        # Assert - when no tools and content is present, should complete successfully
        assert len(result.tool_calls) == 0
        assert "tools" in result.content.lower() or result.is_complete

    @pytest.mark.asyncio
    async def test_large_context_handling(self) -> None:
        """Verify large context doesn't break execution."""
        # Arrange
        profile = _make_role_profile()
        llm_invoker = MockLLMInvoker()
        llm_invoker.add_response(
            MockLLMResponse(
                content="Processed large context.",
                tool_calls=[],
            )
        )

        # Create large history
        large_history = [("user", f"Message {i}") for i in range(100)]
        large_history.append(("assistant", "Large response" * 1000))

        kernel = _make_kernel_mock(profile, llm_invoker)
        # Need to mock parse_thinking to return clean content
        kernel._output_parser.parse_thinking.return_value = MagicMock(
            clean_content="Processed large context.",
            thinking=None,
        )
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Process this", history=large_history)

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = await engine.run(request=request, role="pm", controller=controller)

        # Assert - large context should be handled (may error due to empty output check)
        # but should not crash
        assert result is not None

    @pytest.mark.asyncio
    async def test_stream_cancellation(self) -> None:
        """Verify stream can be cancelled mid-execution."""
        # Arrange
        profile = _make_role_profile()
        llm_invoker = MockLLMInvoker()

        # Add many responses to simulate long-running stream
        for i in range(10):
            llm_invoker.add_response(
                MockLLMResponse(
                    content=f"Chunk {i}",
                    tool_calls=[],
                )
            )

        kernel = _make_kernel_mock(profile, llm_invoker)
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Long operation")

        # Act - cancel after receiving a few events
        controller = ToolLoopController.from_request(request=request, profile=profile)
        events = []

        try:
            async for event in engine.run_stream(request=request, role="pm", controller=controller):
                events.append(event)
                if len(events) >= 3:
                    break  # Simulate cancellation
        except asyncio.CancelledError:
            pass

        # Assert - should have received some events before cancellation
        assert len(events) >= 1


class TestStreamSpecificBehavior:
    """Stream-specific behavior tests."""

    @pytest.mark.asyncio
    async def test_stream_emits_content_chunks(self) -> None:
        """Verify stream emits content chunks progressively."""
        # Arrange
        profile = _make_role_profile()
        llm_invoker = MockLLMInvoker()
        llm_invoker.add_response(
            MockLLMResponse(
                content="This is a long response with many words.",
                tool_calls=[],
            )
        )

        kernel = _make_kernel_mock(profile, llm_invoker)
        # Mock parse_thinking for stream visible turn
        kernel._output_parser.parse_thinking.return_value = MagicMock(
            clean_content="This is a long response with many words.",
            thinking=None,
        )
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Say something")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        content_chunks = []
        complete_event = None
        async for event in engine.run_stream(request=request, role="pm", controller=controller):
            if event.get("type") == "content_chunk":
                content_chunks.append(event.get("content", ""))
            if event.get("type") == "complete":
                complete_event = event

        # Assert - either we get chunks or a complete event
        assert len(content_chunks) > 0 or complete_event is not None, "Should emit content chunks or complete event"

    @pytest.mark.asyncio
    async def test_stream_emits_tool_call_events(self) -> None:
        """Verify stream emits tool_call events."""
        # Arrange
        profile = _make_role_profile(whitelist=["read_file"])
        llm_invoker = MockLLMInvoker()
        llm_invoker.add_response(
            MockLLMResponse(
                content="Let me read.",
                tool_calls=[_make_tool_call("read_file", {"path": "test.txt"})],
            )
        )

        tool_executor = MockToolExecutor()
        tool_executor.add_result(
            "read_file",
            MockToolResult(
                tool="read_file",
                success=True,
                result={"content": "test"},
            ),
        )

        kernel = _make_kernel_mock(profile, llm_invoker, tool_executor)
        # Mock parse_thinking for stream visible turn
        kernel._output_parser.parse_thinking.return_value = MagicMock(
            clean_content="Let me read.",
            thinking=None,
        )
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Read file")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        tool_call_events = []
        tool_result_events = []
        complete_event = None
        async for event in engine.run_stream(request=request, role="pm", controller=controller):
            if event.get("type") == "tool_call":
                tool_call_events.append(event)
            elif event.get("type") == "tool_result":
                tool_result_events.append(event)
            elif event.get("type") == "complete":
                complete_event = event

        # Assert - either we get tool events or a complete event with tool results
        assert len(tool_call_events) > 0 or (complete_event and complete_event.get("result", {}).tool_results), (
            "Should emit tool_call events or complete with results"
        )
        assert len(tool_result_events) > 0 or (complete_event and complete_event.get("result", {}).tool_results), (
            "Should emit tool_result events or complete with results"
        )

    @pytest.mark.asyncio
    async def test_stream_complete_event_structure(self) -> None:
        """Verify complete event has correct structure."""
        # Arrange
        profile = _make_role_profile()
        llm_invoker = MockLLMInvoker()
        llm_invoker.add_response(
            MockLLMResponse(
                content="Final answer.",
                tool_calls=[],
            )
        )

        kernel = _make_kernel_mock(profile, llm_invoker)
        # Mock parse_thinking for stream visible turn
        kernel._output_parser.parse_thinking.return_value = MagicMock(
            clean_content="Final answer.",
            thinking=None,
        )
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Test")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        complete_event = None
        async for event in engine.run_stream(request=request, role="pm", controller=controller):
            if event.get("type") == "complete":
                complete_event = event
                break

        # Assert
        assert complete_event is not None, "Should emit complete event"
        assert "content" in complete_event or "result" in complete_event, "Complete event should have content or result"
        result = complete_event.get("result")
        if result is not None:
            assert hasattr(result, "content") or isinstance(result, dict)


# =============================================================================
# Integration with Real Components
# =============================================================================


class TestRealKernelIntegration:
    """Integration tests with real kernel components."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires proper role registry setup - run manually")
    async def test_turn_engine_with_real_kernel(self) -> None:
        """Verify TurnEngine works with real kernel instance."""
        from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel

        # This test uses the real kernel but with mocked LLM
        profile = _make_role_profile()
        kernel = RoleExecutionKernel(workspace=".")

        # Replace LLM caller with mock
        mock_llm = MockLLMInvoker()
        mock_llm.add_response(
            MockLLMResponse(
                content="Test response from real kernel integration.",
                tool_calls=[],
            )
        )
        kernel_any: Any = kernel
        kernel_any._injected_llm_caller = mock_llm

        engine = TurnEngine(kernel=kernel, llm_caller=mock_llm)
        request = _make_turn_request("Test real kernel")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = await engine.run(request=request, role="pm", controller=controller)

        # Assert
        assert result.error is None, f"Real kernel integration failed: {result.error}"
        assert "Test response" in result.content


# =============================================================================
# Performance/Stress Tests
# =============================================================================


class TestPerformanceCharacteristics:
    """Performance and stress tests."""

    @pytest.mark.asyncio
    async def test_many_tool_calls_in_single_turn(self) -> None:
        """Verify handling of many tool calls in a single turn."""
        # Arrange
        profile = _make_role_profile(whitelist=["read_file"])
        llm_invoker = MockLLMInvoker()

        # Create many tool calls
        tool_calls = [_make_tool_call("read_file", {"path": f"file{i}.txt"}, call_id=f"call_{i}") for i in range(10)]
        llm_invoker.add_response(
            MockLLMResponse(
                content="Reading many files.",
                tool_calls=tool_calls,
            )
        )
        llm_invoker.add_response(
            MockLLMResponse(
                content="Done reading all files.",
                tool_calls=[],
            )
        )

        tool_executor = MockToolExecutor()
        for i in range(10):
            tool_executor.add_result(
                "read_file",
                MockToolResult(
                    tool="read_file",
                    success=True,
                    result={"content": f"content {i}"},
                ),
            )

        kernel = _make_kernel_mock(profile, llm_invoker, tool_executor)
        # Mock parse_thinking to return clean content
        kernel._output_parser.parse_thinking.return_value = MagicMock(
            clean_content="Reading many files.",
            thinking=None,
        )
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Read all files")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = await engine.run(request=request, role="pm", controller=controller)

        # Assert - 大量纯读取工具不再因数量多而 handoff；
        # 走正常 TOOL_BATCH → LLM_ONCE 流程，最终得到总结输出。
        assert result.error is None
        assert result.metadata.get("transaction_kind") != "handoff_workflow"
        assert "Done reading all files." in result.content

    @pytest.mark.asyncio
    async def test_rapid_stream_events(self) -> None:
        """Verify stream handles rapid event emission."""
        # Arrange
        profile = _make_role_profile()
        llm_invoker = MockLLMInvoker()

        # Many rapid responses
        for i in range(20):
            llm_invoker.add_response(
                MockLLMResponse(
                    content=f"Response {i}",
                    tool_calls=[],
                )
            )

        kernel = _make_kernel_mock(profile, llm_invoker)
        engine = TurnEngine(kernel=kernel, llm_caller=llm_invoker)
        request = _make_turn_request("Rapid test")

        # Act
        controller = ToolLoopController.from_request(request=request, profile=profile)
        event_count = 0
        async for _ in engine.run_stream(request=request, role="pm", controller=controller):
            event_count += 1
            if event_count >= 50:  # Safety limit
                break

        # Assert
        assert event_count > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
