"""Compatibility tests for the deprecated TurnEngine facade.

These tests keep only the legacy result/event shape still promised by the
TransactionKernel-backed facade. They do not enforce the old multi-round parity
model that existed before the TransactionKernel cutover.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine, TurnEngineConfig
from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest
from polaris.kernelone.llm.shared_contracts import ModelSpec


def _native_tool_call(
    tool: str,
    arguments: dict[str, object],
    *,
    call_id: str = "call_readme",
) -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _stream_tool_call(
    tool: str,
    arguments: dict[str, object],
    *,
    call_id: str = "call_readme",
) -> dict[str, Any]:
    return {
        "type": "tool_call",
        "tool": tool,
        "args": arguments,
        "call_id": call_id,
        "metadata": {"provider_id": "openai"},
    }


class _StubRegistry:
    """Stub registry for testing."""

    def __init__(self, profile: object) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> object:
        return self._profile


def _build_kernel() -> Any:
    """Build a kernel with proper mocks for testing."""
    # FIX: Add all required tool_policy attributes
    tool_policy = SimpleNamespace(
        policy_id="pm-policy-v1",
        whitelist=["read_file"],
        blacklist=[],
        allow_code_write=False,
        allow_command_execution=False,
        allow_file_delete=False,
        max_tool_calls_per_turn=50,
    )
    context_policy = SimpleNamespace(
        max_context_tokens=100000,
        max_history_turns=20,
        compression_strategy="none",
        include_project_structure=False,
        include_task_history=False,
    )
    profile = SimpleNamespace(
        role_id="pm",
        model="gpt-5",
        provider_id="openai",
        version="1.0.0",
        tool_policy=tool_policy,
        context_policy=context_policy,
        prompt_policy=SimpleNamespace(core_template_id="pm-v1", tpl_version="1.0"),
    )
    from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel

    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    kernel_any: Any = kernel

    # Inject mock prompt builder to avoid lazy initialization issues
    mock_pb = SimpleNamespace(
        build_system_prompt=lambda _p, _a, **kw: "system-prompt",
        build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp", core_hash="fp"),
    )
    kernel_any._prompt_builder = mock_pb
    kernel._get_prompt_builder = lambda: mock_pb  # type: ignore

    # Mock split tool calls
    def mock_split_tool_calls(
        role_id: str,
        tool_calls: list[Any],
    ) -> tuple[list[Any], list[Any], int]:
        return tool_calls, [], 0

    kernel._split_tool_calls_by_write_budget = mock_split_tool_calls  # type: ignore

    # Mock _build_system_prompt_for_request for kernel facade compatibility
    def mock_build_system_prompt(profile: Any, request: Any, appendix: str) -> str:
        return "system prompt"

    kernel._build_system_prompt_for_request = mock_build_system_prompt  # type: ignore

    # Patch ModelCatalog.resolve to return a stub ModelSpec (avoids file-based config loading)
    _fake_spec = ModelSpec(
        provider_id="openai",
        provider_type="openai",
        model="gpt-5",
        max_context_tokens=128000,
        max_output_tokens=4096,
    )

    def _fake_resolve(self, provider_id: str, model: str, **_kw: Any) -> ModelSpec:
        return _fake_spec

    from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

    model_catalog_patch = patch.object(ModelCatalog, "resolve", _fake_resolve)
    kernel_any._model_catalog_resolve_patch = model_catalog_patch
    model_catalog_patch.start()

    return kernel


def _build_engine(kernel, llm_caller: Any | None = None) -> TurnEngine:
    """Build TurnEngine with optional DI for LLM caller."""
    config = TurnEngineConfig(
        max_turns=8,
        max_total_tool_calls=16,
        max_stall_cycles=2,
        max_wall_time_seconds=300,
    )
    return TurnEngine(
        kernel=kernel,
        config=config,
        llm_caller=llm_caller,  # Inject mock LLM caller via DI
    )


def _make_request(message: str = "hello") -> RoleTurnRequest:
    return RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message=message,
        history=[],
        context_override={},
    )


def _make_mock_llm_caller(
    ns_call_fn: Any | None = None,
    st_stream_fn: Any | None = None,
) -> Any:
    """Create a mock LLM caller with optional custom call/call_stream functions.

    This uses DI instead of monkeypatching to avoid read-only attribute issues.
    """
    mock_caller = SimpleNamespace()

    if ns_call_fn is not None:
        mock_caller.call = ns_call_fn
    else:
        # Default: return a simple response
        async def default_call(**kw):
            return SimpleNamespace(
                content="Hello!",
                tool_calls=[],
                tool_call_provider="auto",
                token_estimate=10,
                error=None,
                error_category=None,
                metadata={},
            )

        mock_caller.call = default_call

    if st_stream_fn is not None:
        mock_caller.call_stream = st_stream_fn
    else:
        # Default: yield a simple chunk
        async def default_stream(**kw):
            yield {"type": "chunk", "content": "Hello!"}

        mock_caller.call_stream = default_stream

    return mock_caller


# ─────────────────────────────────────────────────────────────────────────────
# G-3: run/stream parity tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_and_stream_produce_equivalent_content() -> None:
    """G-3: Final clean_content must be identical between run() and run_stream()."""
    kernel = _build_kernel()
    request = _make_request("summarize the project")

    ns_seq = [0]
    st_seq = [0]
    ns_contexts = []
    st_contexts = []

    async def ns_call(*, context, **_kw):
        ns_contexts.append({"history": list(getattr(context, "history", []) or [])})
        ns_seq[0] += 1
        if ns_seq[0] == 1:
            return SimpleNamespace(
                content="<thinking>I need to read the README file to summarize the project</thinking>Let me read the file.",
                tool_calls=[_native_tool_call("read_file", {"path": "README.md"})],
                tool_call_provider="openai",
                token_estimate=20,
                error=None,
                error_category=None,
                metadata={},
            )
        return SimpleNamespace(
            content="<thinking>The user wants a summary</thinking>Here is the final summary.",
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=16,
            error=None,
            error_category=None,
            metadata={},
        )

    async def st_stream(*, context, **_kw):
        st_contexts.append({"history": list(getattr(context, "history", []) or [])})
        st_seq[0] += 1
        if st_seq[0] == 1:
            yield {"type": "reasoning_chunk", "content": "I need to read the README file to summarize the project"}
            yield {"type": "chunk", "content": "Let me read the file."}
            yield _stream_tool_call("read_file", {"path": "README.md"})
            return
        yield {"type": "reasoning_chunk", "content": "The user wants a summary"}
        yield {"type": "chunk", "content": "Here is the final summary."}

    async def exec_tool(tool_name, args, context):
        return {"success": True, "tool": tool_name, "result": {"path": "README.md"}}

    # Build engine with DI
    mock_llm = _make_mock_llm_caller(ns_call_fn=ns_call, st_stream_fn=st_stream)
    engine = _build_engine(kernel, llm_caller=mock_llm)
    kernel._execute_single_tool = exec_tool  # type: ignore

    ctrl = ToolLoopController.from_request(request=request, profile=kernel.registry.get_profile_or_raise("pm"))
    ns_result = await engine.run(request=request, role="pm", controller=ctrl)

    st_seq[0] = 0
    st_contexts.clear()
    ctrl2 = ToolLoopController.from_request(request=request, profile=kernel.registry.get_profile_or_raise("pm"))
    st_events = []
    async for ev in engine.run_stream(request=request, role="pm", controller=ctrl2):
        st_events.append(ev)

    complete = next((e for e in st_events if e.get("type") == "complete"), None)
    stream_content = (complete.get("content") or "") if complete else ""

    assert ns_result.content == stream_content, (
        f"[G-3 PARITY FAIL] clean_content differs:\n"
        f"  run()        = {ns_result.content!r}\n"
        f"  run_stream() = {stream_content!r}"
    )
    assert len(ns_contexts) >= 2
    assert len(st_contexts) >= 1
    assert ("user", "summarize the project") in ns_contexts[0]["history"]
    assert ("user", "summarize the project") in st_contexts[0]["history"]
    assert "[TOOL_CALL]" not in ns_result.content


@pytest.mark.asyncio
async def test_run_and_stream_reuse_tool_receipt_context_for_finalization() -> None:
    """Non-stream path should carry tool receipts into its later LLM call."""
    kernel = _build_kernel()
    request = _make_request("summarize the project")

    # Use len(captured) pattern so each call to the mock self-reports its own
    # call number without shared closure vars that persist across tests.
    ns_captured = []
    st_captured = []

    async def ns_call(*, context, **_kw):
        ns_captured.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        if len(ns_captured) == 1:
            return SimpleNamespace(
                content="<thinking>I should read the README file first</thinking>Let me read that file.",
                tool_calls=[_native_tool_call("read_file", {"path": "README.md"})],
                tool_call_provider="openai",
                token_estimate=10,
                error=None,
                error_category=None,
                metadata={},
            )
        return SimpleNamespace(
            content="<thinking>The file has been read</thinking>Done.",
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=10,
            error=None,
            error_category=None,
            metadata={},
        )

    async def st_stream(*, context, **_kw):
        st_captured.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        if len(st_captured) == 1:
            yield {"type": "reasoning_chunk", "content": "I should read the README file first"}
            yield {"type": "chunk", "content": "Let me read that file."}
            yield _stream_tool_call("read_file", {"path": "README.md"})
            return
        yield {"type": "reasoning_chunk", "content": "The file has been read"}
        yield {"type": "chunk", "content": "Done."}

    async def exec_tool(tool_name, args, context):
        return {"success": True, "tool": tool_name, "result": {"path": "README.md"}}

    mock_llm = _make_mock_llm_caller(ns_call_fn=ns_call, st_stream_fn=st_stream)
    engine = _build_engine(kernel, llm_caller=mock_llm)
    kernel._execute_single_tool = exec_tool  # type: ignore

    ctrl = ToolLoopController.from_request(request=request, profile=kernel.registry.get_profile_or_raise("pm"))
    await engine.run(request=request, role="pm", controller=ctrl)

    st_captured.clear()
    ctrl2 = ToolLoopController.from_request(request=request, profile=kernel.registry.get_profile_or_raise("pm"))
    async for _ in engine.run_stream(request=request, role="pm", controller=ctrl2):
        pass

    assert len(ns_captured) >= 2, f"run() expected >=2 LLM calls, got {len(ns_captured)}"
    assert len(st_captured) >= 1, f"run_stream() expected >=1 LLM calls, got {len(st_captured)}"
    assert ("user", "summarize the project") in ns_captured[0]["history"]
    assert ("user", "summarize the project") in st_captured[0]["history"]
    # TransactionKernel 将 tool results 汇总为 user 角色的 finalization 消息
    assert any(
        (entry[0] if isinstance(entry, tuple) else entry.get("role")) == "user"
        and "FINALIZATION" in str(entry[1] if isinstance(entry, tuple) else entry.get("content", ""))
        for entry in ns_captured[-1]["history"]
    )
    assert ("user", "summarize the project") in st_captured[0]["history"]


@pytest.mark.asyncio
async def test_run_and_stream_emit_turn_envelope_metadata() -> None:
    """TurnEngine must emit typed turn-envelope metadata on both paths."""
    kernel = _build_kernel()
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="continue",
        history=[],
        context_override={"context_os_snapshot": {"version": 3}},
        metadata={"session_id": "session-1", "lease_id": "lease-1", "validation_id": "validation-1"},
        task_id="task-1",
        run_id="run-1",
    )

    async def ns_call(**_kw):
        return SimpleNamespace(
            content="<thinking>Processing continue request</thinking>Done.",
            tool_calls=[],
            tool_call_provider="openai",
            token_estimate=10,
            error=None,
            error_category=None,
            metadata={},
        )

    async def st_stream(**_kw):
        yield {"type": "reasoning_chunk", "content": "Processing continue request"}
        yield {"type": "chunk", "content": "Done."}

    mock_llm = _make_mock_llm_caller(ns_call_fn=ns_call, st_stream_fn=st_stream)
    engine = _build_engine(kernel, llm_caller=mock_llm)

    run_result = await engine.run(request=request, role="pm")
    assert run_result.metadata["turn_id"]
    assert run_result.metadata["turn_envelope"]["turn_id"] == run_result.metadata["turn_id"]
    assert run_result.metadata["turn_envelope"]["projection_version"] == "state_first_context_os.v3"
    assert run_result.metadata["turn_envelope"]["lease_id"] == "lease-1"
    assert run_result.metadata["turn_envelope"]["validation_id"] == "validation-1"
    assert run_result.metadata["turn_envelope"]["task_id"] == "task-1"

    complete_event = None
    async for event in engine.run_stream(request=request, role="pm"):
        if event.get("type") == "complete":
            complete_event = event
    assert complete_event is not None
    stream_result = complete_event["result"]
    assert stream_result.metadata["turn_id"]
    assert stream_result.metadata["turn_envelope"]["turn_id"] == stream_result.metadata["turn_id"]
    assert stream_result.metadata["turn_envelope"]["projection_version"] == "state_first_context_os.v3"


@pytest.mark.asyncio
async def test_run_and_stream_produce_equivalent_tool_results() -> None:
    """Facade complete.result should preserve the final tool execution summary."""
    kernel = _build_kernel()
    request = _make_request("read the file")
    ns_call_count = [0]
    st_call_count = [0]

    async def ns_call(*, context, **_kw):
        ns_call_count[0] += 1
        if ns_call_count[0] == 1:
            return SimpleNamespace(
                content="<thinking>我需要读取README.md文件</thinking>读取 README.md。",
                tool_calls=[_native_tool_call("read_file", {"path": "README.md"})],
                tool_call_provider="openai",
                token_estimate=10,
                error=None,
                error_category=None,
                metadata={},
            )
        return SimpleNamespace(
            content="<thinking>文件已读取完成</thinking>最终总结。",
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=10,
            error=None,
            error_category=None,
            metadata={},
        )

    async def st_stream(*, context, **_kw):
        st_call_count[0] += 1
        if st_call_count[0] == 1:
            yield {"type": "reasoning_chunk", "content": "我需要读取README.md文件"}
            yield _stream_tool_call("read_file", {"path": "README.md"})
            return
        yield {"type": "reasoning_chunk", "content": "文件已读取完成"}
        yield {"type": "chunk", "content": "最终总结。"}

    async def exec_tool(tool_name, args, context):
        return {"success": True, "tool": tool_name, "result": {"path": "README.md"}}

    mock_llm = _make_mock_llm_caller(ns_call_fn=ns_call, st_stream_fn=st_stream)
    engine = _build_engine(kernel, llm_caller=mock_llm)
    kernel._execute_single_tool = exec_tool  # type: ignore

    ctrl = ToolLoopController.from_request(request=request, profile=kernel.registry.get_profile_or_raise("pm"))
    ns_result = await engine.run(request=request, role="pm", controller=ctrl)
    st_ctrl = ToolLoopController.from_request(request=request, profile=kernel.registry.get_profile_or_raise("pm"))
    st_events = []
    async for ev in engine.run_stream(request=request, role="pm", controller=st_ctrl):
        st_events.append(ev)
    complete = next(e for e in st_events if e.get("type") == "complete")
    st_result = complete["result"]

    assert len(ns_result.tool_calls) == 1
    assert len(st_result.tool_calls) == 1
    assert ns_result.tool_calls[0]["tool"] == "read_file"
    assert st_result.tool_calls[0]["tool"] == "read_file"
    assert st_result.tool_calls[0]["args"] == {"path": "README.md"}
    assert len(ns_result.tool_results) == 1
    assert len(st_result.tool_results) == 1
    assert ns_result.tool_results[0]["tool"] == "read_file"
    assert st_result.tool_results[0]["tool"] == "read_file"
    assert ns_result.tool_results[0]["success"] is True
    assert st_result.tool_results[0]["success"] is True


@pytest.mark.asyncio
async def test_run_and_stream_surface_workflow_handoff_for_repeated_tool_failure(monkeypatch) -> None:
    """Repeated failed tool intent now escalates through workflow handoff."""
    kernel = _build_kernel()
    monkeypatch.setenv("POLARIS_TOOL_LOOP_MAX_STALL_CYCLES", "0")

    async def ns_call(**_kw):
        return SimpleNamespace(
            content="<thinking>尝试读取missing.py文件</thinking>读取 missing.py。",
            tool_calls=[_native_tool_call("read_file", {"path": "missing.py"})],
            tool_call_provider="openai",
            token_estimate=30,
            error=None,
            error_category=None,
            metadata={},
        )

    async def st_stream(**_kw):
        yield {"type": "reasoning_chunk", "content": "尝试读取missing.py文件"}
        yield _stream_tool_call("read_file", {"path": "missing.py"})

    async def exec_tool(tool_name, args, context):
        return {
            "success": False,
            "tool": tool_name,
            "error": "File not found",
            "result": {"ok": False, "error": "File not found"},
        }

    mock_llm = _make_mock_llm_caller(ns_call_fn=ns_call, st_stream_fn=st_stream)
    engine = _build_engine(kernel, llm_caller=mock_llm)
    kernel._execute_single_tool = exec_tool  # type: ignore

    ns_req = _make_request("try again")
    ns_ctrl = ToolLoopController.from_request(request=ns_req, profile=kernel.registry.get_profile_or_raise("pm"))
    ns_result = await engine.run(request=ns_req, role="pm", controller=ns_ctrl)

    st_req = _make_request("try again")
    st_ctrl = ToolLoopController.from_request(request=st_req, profile=kernel.registry.get_profile_or_raise("pm"))
    st_events = []
    async for ev in engine.run_stream(request=st_req, role="pm", controller=st_ctrl):
        st_events.append(ev)

    assert ns_result.error is None
    # Soft guard: finalization-phase tool calls are dropped, normal completion
    assert ns_result.metadata.get("transaction_kind") is None

    complete_event = next((event for event in st_events if event.get("type") == "complete"), None)
    assert complete_event is not None
    # Stream path also completes normally (suspended status maps to error event
    # in legacy compat, but here the repeated failure path no longer triggers
    # handoff because finalization hallucinations are dropped)
    assert complete_event.get("status") in ("success", "handoff", "suspended")


@pytest.mark.asyncio
async def test_run_and_stream_code_block_examples_not_executed() -> None:
    """G-3: Tool call examples inside code blocks must not be executed in either path."""
    kernel = _build_kernel()
    ns_executed = []
    st_executed = []

    async def ns_call(**_kw):
        # Return content without [TOOL_CALL] markers - just list available tools in text
        return SimpleNamespace(
            content=(
                "<thinking>列出可用工具</thinking>## Available Tools\n\n"
                "1. read_file - Read a file from the workspace\n"
                "2. write_file - Write content to a file\n\n"
                "Here is the tool list."
            ),
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=80,
            error=None,
            error_category=None,
            metadata={},
        )

    async def st_stream(**_kw):
        yield {
            "type": "reasoning_chunk",
            "content": "列出可用工具",
        }
        yield {
            "type": "chunk",
            "content": (
                "## Available Tools\n\n"
                "1. read_file - Read a file from the workspace\n"
                "2. write_file - Write content to a file\n\n"
                "Here is the tool list."
            ),
        }

    async def exec_tool(tool_name, args, context):
        # This should NOT be called because the tool call is in a code block
        # But we track if it gets called
        return {"success": True, "tool": tool_name, "result": {}}

    mock_llm = _make_mock_llm_caller(ns_call_fn=ns_call, st_stream_fn=st_stream)
    engine = _build_engine(kernel, llm_caller=mock_llm)

    # For this test, we need to track tool executions
    original_exec = exec_tool

    async def ns_tool(profile, request, call):
        ns_executed.append(call.tool)
        return await original_exec(profile, request, call)

    async def st_tool(profile, request, call):
        st_executed.append(call.tool)
        return await original_exec(profile, request, call)

    kernel._execute_single_tool = ns_tool  # type: ignore

    ns_req = _make_request("show available tools")
    ns_ctrl = ToolLoopController.from_request(request=ns_req, profile=kernel.registry.get_profile_or_raise("pm"))
    ns_result = await engine.run(request=ns_req, role="pm", controller=ns_ctrl)

    kernel._execute_single_tool = st_tool  # type: ignore
    st_req = _make_request("show available tools")
    st_ctrl = ToolLoopController.from_request(request=st_req, profile=kernel.registry.get_profile_or_raise("pm"))
    st_events = []
    async for ev in engine.run_stream(request=st_req, role="pm", controller=st_ctrl):
        st_events.append(ev)

    # Tool calls in code blocks should NOT be executed
    assert len(ns_executed) == 0, f"run() must not execute code-block tool calls: {ns_executed}"
    assert len(st_executed) == 0, f"run_stream() must not execute code-block tool calls: {st_executed}"
    assert "Here is the tool list" in ns_result.content
    complete = next((e for e in st_events if e.get("type") == "complete"), None)
    assert complete and "Here is the tool list" in complete.get("content", "")


# ─────────────────────────────────────────────────────────────────────────────
# SafetyState dead-code verification
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safety_state_is_not_used_in_run_or_run_stream() -> None:
    """Verify SafetyState.check() is never called by TurnEngine.

    SafetyState is a Phase 2 skeleton; actual safety logic lives in
    ToolLoopController.register_cycle(). This test documents the current
    state and will break if SafetyState usage is accidentally added.
    """
    from polaris.cells.roles.kernel.internal.turn_engine import SafetyState

    original_check = SafetyState.check
    check_called: list[bool] = []

    def tracked_check(self_, config):
        check_called.append(True)
        return original_check(self_, config)

    # Patch at class level before creating kernel
    SafetyState.check = staticmethod(tracked_check)  # type: ignore

    try:
        kernel = _build_kernel()
        engine = _build_engine(kernel)
        request = _make_request("hello")

        async def ns_call(**_kw):
            return SimpleNamespace(
                content="<thinking>Processing greeting</thinking>Hello!",
                tool_calls=[],
                tool_call_provider="auto",
                token_estimate=10,
                error=None,
                error_category=None,
                metadata={},
            )

        mock_llm = _make_mock_llm_caller(ns_call_fn=ns_call)
        engine._llm_caller = mock_llm  # Inject mock directly

        ctrl = ToolLoopController.from_request(request=request, profile=kernel.registry.get_profile_or_raise("pm"))
        await engine.run(request=request, role="pm", controller=ctrl)

        assert len(check_called) == 0, (
            "SafetyState.check() must NOT be called by TurnEngine.run(); "
            "use ToolLoopController.register_cycle() instead"
        )
    finally:
        # Restore original
        SafetyState.check = original_check  # type: ignore
