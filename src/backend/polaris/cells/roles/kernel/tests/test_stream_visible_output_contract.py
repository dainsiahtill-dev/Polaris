"""Regression tests for sanitized user-visible streaming output."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest


class _StubRegistry:
    def __init__(self, profile: object) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> object:
        return self._profile


def _build_kernel() -> RoleExecutionKernel:
    profile = SimpleNamespace(
        role_id="pm",
        model="gpt-5",
        provider_id="openai",
        version="1.0.0",
        tool_policy=SimpleNamespace(
            policy_id="pm-policy-v1",
            whitelist=["read_file", "write_file"],
            blacklist=[],
            allow_code_write=True,
            allow_command_execution=False,
            allow_file_delete=False,
            max_tool_calls_per_turn=10,
        ),
        context_policy=SimpleNamespace(
            max_context_tokens=128000,
            max_history_turns=20,
            compression_strategy="none",
            include_project_structure=False,
            include_task_history=False,
        ),
    )

    # 创建 mock LLM caller 用于测试
    class _MockLLMCaller:
        async def call(self, **kwargs: Any) -> Any:
            return SimpleNamespace(content="mock response", error=None, metadata={})

        async def call_stream(self, **kwargs: Any) -> Any:
            yield {"type": "chunk", "content": "mock stream"}

    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    kernel._injected_llm_caller = _MockLLMCaller()  # type: ignore[assignment]
    return kernel


def _patch_prompt_builder(kernel: RoleExecutionKernel) -> None:
    # 使用 _get_prompt_builder() 注入 mock，兼容 DI 架构
    mock_prompt_builder = SimpleNamespace(
        build_fingerprint=lambda _profile, _appendix: SimpleNamespace(full_hash="fp-visible-stream"),
        build_system_prompt=lambda _profile, _appendix: "system-prompt",
        build_retry_prompt=lambda _profile, _appendix, _error, _history, _attempt: "retry-prompt",
    )
    kernel._injected_prompt_builder = mock_prompt_builder  # type: ignore[assignment]


def _make_request(message: str = "阅读并总结项目") -> RoleTurnRequest:
    return RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message=message,
        history=[],
        context_override={},
    )


def test_stream_emits_only_sanitized_visible_content(monkeypatch) -> None:
    kernel = _build_kernel()
    _patch_prompt_builder(kernel)

    async def _fake_call_stream(*, context: Any, **_kwargs: Any):
        message = str(getattr(context, "message", "") or "")
        if message:
            yield {
                "type": "chunk",
                "content": "<thinking>先规划读取步骤</thinking>\n先读取 README。\n",
            }

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)

    async def _collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in kernel.run_stream("pm", _make_request()):
            events.append(event)
        return events

    events = asyncio.run(_collect())
    visible_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "content_chunk"
    ]
    thinking_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "thinking_chunk"
    ]
    complete_event = next(event for event in events if str(event.get("type") or "") == "complete")

    assert visible_chunks == ["先读取 README。"]
    assert all("[TOOL_CALL]" not in chunk for chunk in visible_chunks)
    assert all("<thinking>" not in chunk for chunk in visible_chunks)
    assert thinking_chunks == ["先规划读取步骤"]
    assert str(complete_event.get("content") or "") == "先读取 README。"


def test_stream_preserves_provider_reasoning_without_content_leak(monkeypatch) -> None:
    kernel = _build_kernel()
    _patch_prompt_builder(kernel)

    async def _fake_call_stream(**_kwargs: Any):
        yield {"type": "reasoning_chunk", "content": "先分析目录结构。"}
        yield {"type": "chunk", "content": "这是最终回答。"}

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)

    async def _collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in kernel.run_stream("pm", _make_request("你好")):
            events.append(event)
        return events

    events = asyncio.run(_collect())
    visible_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "content_chunk"
    ]
    thinking_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "thinking_chunk"
    ]
    complete_event = next(event for event in events if str(event.get("type") or "") == "complete")

    assert visible_chunks == ["这是最终回答。"]
    assert thinking_chunks == ["先分析目录结构。"]
    assert str(complete_event.get("content") or "") == "这是最终回答。"


def test_stream_emits_incremental_visible_deltas(monkeypatch) -> None:
    kernel = _build_kernel()
    _patch_prompt_builder(kernel)

    async def _fake_call_stream(**_kwargs: Any):
        yield {"type": "reasoning_chunk", "content": "先"}
        yield {"type": "reasoning_chunk", "content": "分析。"}
        yield {"type": "chunk", "content": "这是"}
        yield {"type": "chunk", "content": "实时"}
        yield {"type": "chunk", "content": "推送。"}

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)

    async def _collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in kernel.run_stream("pm", _make_request("你好")):
            events.append(event)
        return events

    events = asyncio.run(_collect())
    content_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "content_chunk"
    ]
    thinking_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "thinking_chunk"
    ]
    complete_event = next(event for event in events if str(event.get("type") or "") == "complete")

    # NOTE: _BracketToolWrapperFilter 会将增量 chunk 缓冲合并，
    # 这是为了跨 chunk 检测 bracket wrappers 的设计行为
    rendered_content = "".join(content_chunks)
    rendered_thinking = "".join(thinking_chunks)
    assert rendered_content == "这是实时推送。"
    assert rendered_thinking == "先分析。"
    assert rendered_content == str(complete_event.get("content") or "")
    assert rendered_thinking == str(complete_event.get("thinking") or "")


def test_stream_avoids_per_chunk_full_rematerialization(monkeypatch) -> None:
    kernel = _build_kernel()
    _patch_prompt_builder(kernel)

    async def _fake_call_stream(**_kwargs: Any):
        for _ in range(40):
            yield {"type": "chunk", "content": "x"}

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)

    # New architecture: stream is handled directly in RoleExecutionKernel._execute_transaction_kernel_stream
    # via StreamEventHandler.process_stream, not through TurnEngine._materialize_stream_visible_turn.
    from polaris.cells.roles.kernel.internal.turn_engine.stream_handler import StreamEventHandler

    call_counter = {"count": 0}
    original = StreamEventHandler.process_stream

    async def _wrapped(self: StreamEventHandler, *args: Any, **kwargs: Any):
        call_counter["count"] += 1
        async for item in original(self, *args, **kwargs):
            yield item

    monkeypatch.setattr(StreamEventHandler, "process_stream", _wrapped)

    async def _collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in kernel.run_stream("pm", _make_request("你好")):
            events.append(event)
        return events

    events = asyncio.run(_collect())
    complete_event = next(event for event in events if str(event.get("type") or "") == "complete")
    assert str(complete_event.get("content") or "") == "x" * 40
    assert call_counter["count"] == 1


def test_stream_strips_split_bracket_tool_wrappers(monkeypatch) -> None:
    kernel = _build_kernel()
    _patch_prompt_builder(kernel)

    async def _fake_call_stream(**_kwargs: Any):
        yield {"type": "chunk", "content": "前缀 "}
        yield {"type": "chunk", "content": "[TOOL_"}
        yield {
            "type": "chunk",
            "content": 'CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_',
        }
        yield {"type": "chunk", "content": "CALL] 后缀"}

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel, "_parse_content_and_thinking_tool_calls", lambda *_a, **_k: [])

    async def _collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in kernel.run_stream("pm", _make_request("你好")):
            events.append(event)
        return events

    events = asyncio.run(_collect())
    visible_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "content_chunk"
    ]
    complete_event = next(event for event in events if str(event.get("type") or "") == "complete")
    rendered = "".join(visible_chunks)
    assert "[TOOL_CALL]" not in rendered
    assert "[/TOOL_CALL]" not in rendered
    assert rendered == "前缀  后缀"
    assert str(complete_event.get("content") or "") == "前缀  后缀"


def test_stream_strips_output_wrappers_from_visible_content(monkeypatch) -> None:
    kernel = _build_kernel()
    _patch_prompt_builder(kernel)

    async def _fake_call_stream(**_kwargs: Any):
        yield {"type": "chunk", "content": "<output>这是"}
        yield {"type": "chunk", "content": "最终"}
        yield {"type": "chunk", "content": "回答。</output>"}

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel, "_parse_content_and_thinking_tool_calls", lambda *_a, **_k: [])

    async def _collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in kernel.run_stream("pm", _make_request("你好")):
            events.append(event)
        return events

    events = asyncio.run(_collect())
    content_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "content_chunk"
    ]
    complete_event = next(event for event in events if str(event.get("type") or "") == "complete")
    rendered = "".join(content_chunks)

    assert "<output>" not in rendered
    assert "</output>" not in rendered
    assert rendered == "这是最终回答。"
    assert str(complete_event.get("content") or "") == "这是最终回答。"


def test_stream_handles_bracket_tool_wrappers_in_stream(monkeypatch) -> None:
    """Test that bracket-style [TOOL_CALL] wrappers are stripped from stream.

    Note: _BracketToolWrapperFilter only handles bracket-style wrappers.
    XML-style <function_calls> tags are handled separately by OutputParser.
    """
    kernel = _build_kernel()
    _patch_prompt_builder(kernel)

    async def _fake_call_stream(**_kwargs: Any):
        yield {"type": "chunk", "content": "前缀 "}
        yield {
            "type": "chunk",
            "content": ('[TOOL_CALL]{"tool":"write_file","arguments":{"path":"a.py","content":"x"}}[/TOOL_CALL]'),
        }
        yield {"type": "chunk", "content": " 后缀"}

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel, "_parse_content_and_thinking_tool_calls", lambda *_a, **_k: [])

    async def _collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in kernel.run_stream("pm", _make_request("你好")):
            events.append(event)
        return events

    events = asyncio.run(_collect())
    content_chunks = [
        str(event.get("content") or "") for event in events if str(event.get("type") or "") == "content_chunk"
    ]
    rendered = "".join(content_chunks)
    # Bracket wrappers are stripped by _BracketToolWrapperFilter
    assert "[TOOL_CALL]" not in rendered
    assert "[/TOOL_CALL]" not in rendered
    assert "前缀" in rendered
    assert "后缀" in rendered
