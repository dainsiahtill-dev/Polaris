"""Compatibility tests for TurnEngine Phase3/Phase4 helper methods."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.conversation_state import ConversationState
from polaris.cells.roles.kernel.internal.output_parser import OutputParser
from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine
from polaris.cells.roles.kernel.public.transcript_ir import UserMessage


class _RegistryStub:
    def __init__(self, profile: Any) -> None:
        self._profile = profile

    def get_profile_or_raise(self, role: str) -> Any:
        assert role == "pm"
        return self._profile


class _KernelStub:
    def __init__(self) -> None:
        self.workspace = "."
        self._output_parser = OutputParser()
        self._prompt_builder = SimpleNamespace(
            build_system_prompt=lambda _p, _a: "fallback-system",
            build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp"),
        )
        self._llm_call_records: list[dict[str, Any]] = []
        self._tool_exec_records: list[dict[str, Any]] = []
        self._profile = SimpleNamespace(
            role_id="pm",
            model="gpt-5",
            version="1.0.0",
            tool_policy=SimpleNamespace(
                whitelist=["read_file", "write_file"],
                blacklist=[],
                policy_id="pm-policy",
                allow_code_write=True,
                allow_command_execution=False,
                allow_file_delete=False,
                max_tool_calls_per_turn=50,
            ),
        )
        self.registry = _RegistryStub(self._profile)

        # FIX: Add mock _tool_gateway for TurnEngine
        self._tool_gateway = SimpleNamespace(
            check_tool_permission=lambda _name, _args: (True, ""),
            increment_execution_count=lambda: None,
            requires_approval=lambda _name, _args, _state=None: False,
        )

    def _build_system_prompt_for_request(self, _profile: Any, _request: Any, _appendix: str) -> str:
        return "state-system"

    def _build_context(self, _profile: Any, request: Any) -> ContextRequest:
        return ContextRequest(
            message=request.message,
            history=request.history,
            task_id=request.task_id,
        )

    async def _llm_call(self, **kwargs: Any) -> Any:
        self._llm_call_records.append(dict(kwargs))
        return SimpleNamespace(
            content="准备读取文件。",
            error=None,
            tool_calls=[
                {
                    "id": "call_readme",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                }
            ],
            tool_call_provider="openai",
            metadata={},
        )

    async def _execute_single_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Match KernelOne._execute_single_tool interface."""
        del context  # stub doesn't need profile/request
        self._tool_exec_records.append({"tool": tool_name, "args": args})
        return {"success": True, "tool": tool_name, "args": dict(args)}

    def _parse_content_and_thinking_tool_calls(
        self,
        content: str,
        thinking: str | None,
        profile: Any,
        native_tool_calls: list[dict[str, Any]] | None,
        native_tool_provider: str,
    ) -> list[Any]:
        del content, thinking, profile, native_tool_calls, native_tool_provider
        return [SimpleNamespace(tool="read_file", args={"path": "README.md"})]


@pytest.mark.asyncio
async def test_turn_engine_compat_methods_are_runnable() -> None:
    kernel = _KernelStub()
    # FIX: 使用新的依赖注入模式，直接传入所有必需服务
    from polaris.cells.roles.kernel.internal.output_parser import OutputParser
    from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder

    mock_llm_caller = SimpleNamespace(call=kernel._llm_call)
    mock_output_parser = OutputParser()
    mock_prompt_builder = PromptBuilder(workspace=".")
    engine = TurnEngine(
        kernel=kernel,
        llm_caller=mock_llm_caller,
        output_parser=mock_output_parser,
        prompt_builder=mock_prompt_builder,
    )

    state = ConversationState.new(role="pm", workspace=".")
    state.system_prompt = "explicit-system"
    state.append_item(UserMessage(content="请总结项目代码"))

    response = await engine._call_model(state)
    assert response.error is None
    assert len(kernel._llm_call_records) == 1
    assert kernel._llm_call_records[0]["system_prompt"] == "explicit-system"

    decoded = engine._decode(response)
    assert decoded["error"] is None
    assert decoded["content"]
    assert len(decoded["tool_calls"]) == 1
    assert decoded["tool_calls"][0]["tool"] == "read_file"

    tool_results = await engine._execute_tools(
        [{"tool": "read_file", "args": {"path": "README.md"}}],
        state,
    )
    assert len(tool_results) == 1
    assert tool_results[0]["success"] is True
    assert len(kernel._tool_exec_records) == 1


def test_turn_engine_maybe_compact_triggers_under_pressure() -> None:
    kernel = _KernelStub()
    # FIX: 使用新的依赖注入模式，直接传入所有必需服务
    from polaris.cells.roles.kernel.internal.output_parser import OutputParser
    from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder

    mock_llm_caller = SimpleNamespace(call=kernel._llm_call)
    mock_output_parser = OutputParser()
    mock_prompt_builder = PromptBuilder(workspace=".")
    engine = TurnEngine(
        kernel=kernel,
        llm_caller=mock_llm_caller,
        output_parser=mock_output_parser,
        prompt_builder=mock_prompt_builder,
    )
    state = ConversationState.new(role="pm", workspace=".")

    assert engine._maybe_compact(state) is False

    state.budgets.turn_count = int(state.budgets.max_turns * 0.9)
    assert engine._maybe_compact(state) is True
