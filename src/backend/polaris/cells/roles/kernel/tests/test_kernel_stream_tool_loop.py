"""Tests for transcript-driven streaming tool loops in RoleExecutionKernel."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest

# Note: Tests that need specific stall behavior should set env vars themselves.
# The module-level fixture was removed because it interfered with tests that
# explicitly test stall detection behavior (e.g., max_stall_cycles=0).


@pytest.fixture(autouse=True)
def _patch_model_catalog(monkeypatch):
    """Patch ModelCatalog.resolve to avoid filesystem lookups for fake models."""
    from polaris.kernelone.llm.engine.model_catalog import ModelCatalog
    from polaris.kernelone.llm.shared_contracts import ModelSpec

    fake_spec = ModelSpec(
        provider_id="openai",
        provider_type="openai",
        model="gpt-5",
        max_context_tokens=128000,
        max_output_tokens=4096,
        supports_tools=True,
    )
    monkeypatch.setattr(ModelCatalog, "resolve", lambda self, provider_id, model, **kw: fake_spec)


class _StubRegistry:
    def __init__(self, profile: object) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> object:
        return self._profile


def _build_kernel() -> RoleExecutionKernel:
    # FIX: Add context_policy for RoleContextGateway compatibility
    context_policy = SimpleNamespace(
        max_context_tokens=100000,
        max_history_turns=20,
        compression_strategy="none",
        include_project_structure=False,
        include_task_history=False,
    )

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

    profile = SimpleNamespace(
        role_id="pm",
        model="gpt-5",
        provider_id="openai",
        version="1.0.0",
        tool_policy=tool_policy,
        context_policy=context_policy,
    )
    kernel = RoleExecutionKernel(workspace=".", registry=_StubRegistry(profile))  # type: ignore[arg-type]
    kernel_any: Any = kernel
    # Inject mock prompt builder to avoid AttributeError when tests monkeypatch it
    kernel_any._prompt_builder = SimpleNamespace(
        build_fingerprint=lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream"),
        build_system_prompt=lambda _profile, _appendix: "system-prompt",
        build_retry_prompt=lambda _base, _quality_dict, _attempt: "system-prompt",
    )

    # FIX: Initialize _injected_llm_caller for proper dependency injection
    # The kernel uses _injected_llm_caller first if available, falling back to _llm_caller
    async def _noop_call(**kw):
        return SimpleNamespace(content="", tool_calls=[], error=None)

    async def _noop_call_stream(**kw):
        return
        yield  # make it an async generator

    kernel_any._injected_llm_caller = SimpleNamespace(call=_noop_call, call_stream=_noop_call_stream)
    return kernel


def _openai_native_tool_call(path: str, *, call_id: str = "call_readme") -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": f'{{"path": "{path}"}}',
        },
    }


def test_stream_continues_after_tool_results_with_transcript_context(monkeypatch) -> None:
    kernel = _build_kernel()
    stream_contexts: list[dict[str, object]] = []
    call_contexts: list[dict[str, object]] = []

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(*, context, **_kwargs):
        stream_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        yield {"type": "reasoning_chunk", "content": "需要先读取README了解项目结构"}
        yield {"type": "chunk", "content": "先读取关键文件。\n"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
            "call_id": "call_readme",
            "metadata": {
                "provider_id": "openai",
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "README.md"},
                    "call_id": "call_readme",
                },
            },
        }

    async def _fake_call(*, context, **_kwargs):
        call_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        return SimpleNamespace(content="这是最终总结", tool_calls=[], error=None, metadata={})

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    async def _fake_execute_single_tool(tool_name, args, context):
        return {
            "success": True,
            "tool": tool_name,
            "result": {"path": "README.md", "bytes": 128},
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="帮我阅读并总结代码",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    event_types = [str(item.get("type") or "") for item in events]

    assert len(stream_contexts) == 1
    assert stream_contexts[0]["message"] == "帮我阅读并总结代码"
    # Current turn user message should not be duplicated in history.
    history = stream_contexts[0]["history"]
    assert isinstance(history, list)
    assert ("user", "帮我阅读并总结代码") not in history
    assert len(call_contexts) == 1
    assert call_contexts[0]["message"] == "帮我阅读并总结代码"
    call_history = call_contexts[0]["history"]
    assert isinstance(call_history, list)
    # TransactionKernel 将 tool results 汇总为 user 角色的 finalization 消息，
    # 而非旧 TurnEngine 的独立 tool 角色消息。
    assert any(
        (entry[0] if isinstance(entry, tuple) else entry.get("role")) == "user"
        and "FINALIZATION" in str(entry[1] if isinstance(entry, tuple) else entry.get("content", ""))
        for entry in call_history
    )

    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "complete" in event_types

    complete_event = next(item for item in events if str(item.get("type") or "") == "complete")
    assert str(complete_event.get("content") or "") == "这是最终总结"


def test_stream_executes_native_tool_calls_without_text_wrapper(monkeypatch) -> None:
    kernel = _build_kernel()
    stream_contexts: list[dict[str, object]] = []
    call_contexts: list[dict[str, object]] = []

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream-native"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(*, context, **_kwargs):
        stream_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        yield {"type": "reasoning_chunk", "content": "直接调用read_file读取README"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
            "call_id": "call_readme",
            "metadata": {
                "provider_id": "openai",
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "README.md"},
                    "call_id": "call_readme",
                },
            },
        }

    async def _fake_call(*, context, **_kwargs):
        call_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        return SimpleNamespace(content="这是原生工具调用后的总结", tool_calls=[], error=None, metadata={})

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    async def _fake_execute_single_tool(tool_name, args, context):
        return {
            "success": True,
            "tool": tool_name,
            "result": {"path": "README.md", "bytes": 256},
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="直接读取并总结 README",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    event_types = [str(item.get("type") or "") for item in events]

    assert len(stream_contexts) == 1
    assert event_types.count("tool_call") == 1
    assert event_types.count("tool_result") == 1
    assert "complete" in event_types

    complete_event = next(item for item in events if str(item.get("type") or "") == "complete")
    assert str(complete_event.get("content") or "") == "这是原生工具调用后的总结"
    assert call_contexts[0]["message"] == "直接读取并总结 README"
    call_history = call_contexts[0]["history"]
    assert isinstance(call_history, list)
    # TransactionKernel 将 tool results 汇总为 user 角色的 finalization 消息
    assert any(
        (entry[0] if isinstance(entry, tuple) else entry.get("role")) == "user"
        and "FINALIZATION" in str(entry[1] if isinstance(entry, tuple) else entry.get("content", ""))
        for entry in call_history
    )


def test_stream_executes_normalized_tool_calls_even_with_anthropic_provider_metadata(monkeypatch) -> None:
    kernel = _build_kernel()
    stream_contexts: list[dict[str, object]] = []
    call_contexts: list[dict[str, object]] = []

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream-anthropic-compat"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(*, context, **_kwargs):
        stream_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        yield {"type": "reasoning_chunk", "content": "通过Anthropic兼容接口调用read_file"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
            "call_id": "call_readme",
            "metadata": {
                "provider_id": "anthropic_compat-1771249789301",
                "provider": "anthropic_compat",
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "README.md"},
                    "call_id": "call_readme",
                },
            },
        }

    async def _fake_call(*, context, **_kwargs):
        call_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        return SimpleNamespace(content="这是兼容 Anthropic 流后的总结", tool_calls=[], error=None, metadata={})

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    executed_calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute_single_tool(tool_name, args, context):
        executed_calls.append((str(tool_name or ""), dict(args or {})))
        return {
            "success": True,
            "tool": tool_name,
            "result": {"path": "README.md", "bytes": 256},
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="直接读取并总结 README",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    event_types = [str(item.get("type") or "") for item in events]

    assert len(stream_contexts) == 1
    assert executed_calls == [("read_file", {"path": "README.md"})]
    assert event_types.count("tool_call") == 1
    assert event_types.count("tool_result") == 1
    assert "complete" in event_types

    complete_event = next(item for item in events if str(item.get("type") or "") == "complete")
    assert str(complete_event.get("content") or "") == "这是兼容 Anthropic 流后的总结"
    assert call_contexts[0]["message"] == "直接读取并总结 README"
    call_history = call_contexts[0]["history"]
    assert isinstance(call_history, list)
    # TransactionKernel 将 tool results 汇总为 user 角色的 finalization 消息
    assert any(
        (entry[0] if isinstance(entry, tuple) else entry.get("role")) == "user"
        and "FINALIZATION" in str(entry[1] if isinstance(entry, tuple) else entry.get("content", ""))
        for entry in call_history
    )


def test_stream_repeated_identical_tool_cycle_emits_safety_error(monkeypatch) -> None:
    """TransactionKernel single-turn semantics: one failed tool call + finalization.

    New architecture has no multi-turn stall loop.
    """
    kernel = _build_kernel()

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(**_kwargs):
        yield {"type": "reasoning_chunk", "content": "尝试读取missing.py"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "missing.py"},
            "call_id": "call_missing",
            "metadata": {
                "provider_id": "openai",
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "missing.py"},
                    "call_id": "call_missing",
                },
            },
        }

    async def _fake_call(**_kwargs):
        return SimpleNamespace(
            content="文件 missing.py 不存在。",
            tool_calls=[],
            error=None,
            metadata={},
        )

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    async def _fake_execute_single_tool(tool_name, args, context):
        return {
            "success": False,
            "tool": tool_name,
            "error": "File not found: missing.py",
            "result": {"ok": False, "error": "File not found: missing.py"},
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="继续",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    errors = [item for item in events if str(item.get("type") or "") == "error"]
    tool_calls = [item for item in events if str(item.get("type") or "") == "tool_call"]
    tool_results = [item for item in events if str(item.get("type") or "") == "tool_result"]

    assert not errors
    assert len(tool_calls) == 1
    assert len(tool_results) == 1
    assert any(str(item.get("type") or "") == "complete" for item in events)


def test_stream_compacts_large_tool_receipts_in_transcript(monkeypatch) -> None:
    """Large tool results complete successfully under TransactionKernel."""
    kernel = _build_kernel()
    stream_contexts: list[dict[str, object]] = []
    call_contexts: list[dict[str, object]] = []

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(*, context, **_kwargs):
        stream_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        yield {"type": "reasoning_chunk", "content": "读取大文件README分析内容"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
            "call_id": "call_readme",
            "metadata": {
                "provider_id": "openai",
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "README.md"},
                    "call_id": "call_readme",
                },
            },
        }

    async def _fake_call(*, context, **_kwargs):
        call_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        return SimpleNamespace(content="分析完成", tool_calls=[], error=None, metadata={})

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    huge_content = "A" * 50000

    async def _fake_execute_single_tool(tool_name, args, context):
        return {
            "success": True,
            "tool": tool_name,
            "result": {
                "file": "README.md",
                "content": huge_content,
                "truncated": False,
            },
            "raw_result": {
                "ok": True,
                "result": {
                    "file": "README.md",
                    "content": huge_content,
                },
            },
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="读取并总结 README",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    assert any(str(item.get("type") or "") == "complete" for item in events)
    assert len(stream_contexts) == 1
    assert len(call_contexts) == 1


def test_stream_keeps_read_file_receipt_when_context_budget_allows(monkeypatch) -> None:
    """Root-cause regression: avoid repeated read_file loops on medium files.

    TransactionKernel ensures single-turn execution with one tool call + finalization.
    """
    kernel = _build_kernel()
    stream_contexts: list[dict[str, object]] = []
    marker = "FILE_TAIL_MARKER_9f7b"

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(*, context, **_kwargs):
        stream_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        yield {"type": "reasoning_chunk", "content": "读取server.py了解项目结构"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "src/server.py"},
            "call_id": "call_server",
            "metadata": {
                "provider_id": "openai",
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "src/server.py"},
                    "call_id": "call_server",
                },
            },
        }

    async def _fake_call(*, context, **_kwargs):
        history = list(getattr(context, "history", []) or [])
        # TransactionKernel 将 tool results 汇总为 user 角色的 finalization 消息
        all_receipts = " ".join(
            str(entry[1] if isinstance(entry, tuple) else entry.get("content", "")) for entry in history
        )
        if marker in all_receipts:
            return SimpleNamespace(content="总结完成", tool_calls=[], error=None, metadata={})
        return SimpleNamespace(content="未找到标记", tool_calls=[], error=None, metadata={})

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)
    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    #  marker 放在内容开头，避免被 finalization context 的 3000 字截断截掉
    medium_content = marker + ("A" * 5000)

    async def _fake_execute_single_tool(tool_name, args, context):
        return {
            "success": True,
            "tool": tool_name,
            "result": {
                "file": "src/server.py",
                "content": medium_content,
                "truncated": False,
            },
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="总结这个项目代码",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    errors = [item for item in events if str(item.get("type") or "") == "error"]
    complete = [item for item in events if str(item.get("type") or "") == "complete"]

    assert not errors, f"unexpected stream errors: {errors!r}"
    assert complete
    assert len(stream_contexts) == 1
    assert complete[-1].get("content") == "总结完成"


def test_stream_examples_inside_code_blocks_do_not_execute(monkeypatch) -> None:
    kernel = _build_kernel()

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(**_kwargs):
        yield {
            "type": "chunk",
            "content": (
                "## 可用工具清单\n\n"
                "```text\n"
                "[EXECUTE_COMMAND]\n"
                'command: "python -m pytest tests/ -v"\n'
                "[/EXECUTE_COMMAND]\n"
                "```\n\n"
                "```text\n"
                "[READ_FILE]\n"
                'path: "src/main.py"\n'
                "[/READ_FILE]\n"
                "```"
            ),
        }

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="你能调用哪些工具",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    event_types = [str(item.get("type") or "") for item in events]

    assert "tool_call" not in event_types
    assert "tool_result" not in event_types

    content_chunks = [item for item in events if str(item.get("type") or "") == "content_chunk"]
    complete_event = next((item for item in events if str(item.get("type") or "") == "complete"), None)
    assert complete_event is not None
    all_content = "".join(str(item.get("content", "")) for item in content_chunks)
    assert "可用工具清单" in all_content


def test_stream_thinking_only_response_emits_explicit_error(monkeypatch) -> None:
    """When model returns only thinking, stream must end with explicit error."""
    kernel = _build_kernel()

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-thinking-only"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(**_kwargs):
        yield {
            "type": "chunk",
            "content": "<thinking>先分析一下</thinking",
        }

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="给我结论",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    errors = [item for item in events if str(item.get("type") or "") == "error"]
    complete = [item for item in events if str(item.get("type") or "") == "complete"]

    assert errors, "thinking-only response must emit error"
    # NOTE: The fake stream does not set a `thinking` field, so the decoder sees
    # reasoning_summary=None and falls back to the generic "no visible output"
    # suspended_reason. In production, a provider that extracts thinking into
    # response.thinking would yield the "thinking-only" variant.
    assert "awaiting user clarification" in str(errors[-1].get("error") or "")
    assert not complete


def test_stream_blank_response_emits_explicit_error(monkeypatch) -> None:
    """Blank stream output must fail instead of completing silently."""
    kernel = _build_kernel()

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-stream-blank"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )

    async def _fake_call_stream(**_kwargs):
        if False:
            yield {}

    monkeypatch.setattr(kernel._injected_llm_caller, "call_stream", _fake_call_stream)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="给我结论",
        history=[],
        context_override={},
    )

    async def _collect() -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        async for event in kernel.run_stream("pm", request):
            events.append(event)
        return events

    events: list[dict[str, object]] = asyncio.run(_collect())
    errors = [item for item in events if str(item.get("type") or "") == "error"]
    complete = [item for item in events if str(item.get("type") or "") == "complete"]

    assert errors, "blank response must emit error"
    assert "model returned no visible output or tool calls" in str(errors[-1].get("error") or "")
    assert not complete


# =============================================================================
# Non-streaming kernel.run() regression tests
# =============================================================================


def test_run_continues_after_tool_results_with_transcript_context(monkeypatch) -> None:
    """Non-streaming equivalent of test_stream_continues_after_tool_results_with_transcript_context.

    Verifies that kernel.run():
    1. Accumulates history correctly across two LLM calls
    2. Injects tool results into the transcript before the second call
    3. Returns a RoleTurnResult with the correct content, tool_calls, and tool_results
    """
    kernel = _build_kernel()
    captured_contexts: list[dict[str, object]] = []

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-run", core_hash="fp-run"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_retry_prompt",
        lambda _base, _quality_dict, _attempt: "system-prompt",
    )

    async def _fake_call(*, context, **_kwargs):
        captured_contexts.append(
            {
                "message": str(getattr(context, "message", "") or ""),
                "history": list(getattr(context, "history", []) or []),
            }
        )
        if len(captured_contexts) == 1:
            return SimpleNamespace(
                content="<thinking>需要先读取README了解项目结构</thinking>先读取关键文件。\n",
                tool_calls=[_openai_native_tool_call("README.md")],
                tool_call_provider="openai",
                token_estimate=50,
                error=None,
                error_category=None,
                metadata={},
            )
        return SimpleNamespace(
            content="这是最终总结",
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=20,
            error=None,
            error_category=None,
            metadata={},
        )

    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    async def _fake_execute_single_tool(tool_name, args, context):
        """Mock _execute_single_tool (used by TurnEngine) - returns tool results without I/O."""
        return {
            "success": True,
            "tool": tool_name,
            "result": {"path": "README.md", "bytes": 128},
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="帮我阅读并总结代码",
        history=[],
        context_override={},
        validate_output=False,
    )

    result = asyncio.run(kernel.run("pm", request))

    assert len(captured_contexts) == 2
    assert captured_contexts[0]["message"] == "帮我阅读并总结代码"
    # Current turn user message should not be duplicated in history.
    first_history = captured_contexts[0]["history"]
    assert isinstance(first_history, list)
    assert ("user", "帮我阅读并总结代码") not in first_history
    assert captured_contexts[1]["message"] == "帮我阅读并总结代码"
    second_history = captured_contexts[1]["history"]
    assert isinstance(second_history, list)
    # TransactionKernel 将 tool results 汇总为 user 角色的 finalization 消息
    assert any(
        (entry[0] if isinstance(entry, tuple) else entry.get("role")) == "user"
        and "FINALIZATION" in str(entry[1] if isinstance(entry, tuple) else entry.get("content", ""))
        for entry in second_history
    )

    # RoleTurnResult assertions
    assert result.content == "这是最终总结"
    assert result.is_complete is True
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "read_file"
    assert len(result.tool_results) == 1
    assert result.tool_results[0]["tool"] == "read_file"


def test_run_repeated_identical_tool_cycle_does_not_trigger_stall(monkeypatch) -> None:
    """Single-turn run(): one failed tool call + finalization completes normally.

    TransactionKernel has no multi-turn stall loop.
    """
    kernel = _build_kernel()
    call_count = [0]

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-run-safety", core_hash="fp-run-safety"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_retry_prompt",
        lambda _base, _quality_dict, _attempt: "system-prompt",
    )

    async def _fake_call(**_kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return SimpleNamespace(
                content="读取 missing.py。",
                tool_calls=[_openai_native_tool_call("missing.py", call_id="call_missing")],
                tool_call_provider="openai",
                token_estimate=30,
                error=None,
                error_category=None,
                metadata={},
            )
        return SimpleNamespace(
            content="文件 missing.py 不存在。",
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=10,
            error=None,
            error_category=None,
            metadata={},
        )

    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    async def _fake_execute_single_tool(tool_name, args, context):
        return {
            "success": False,
            "tool": tool_name,
            "error": "File not found: missing.py",
            "result": {"ok": False, "error": "File not found: missing.py"},
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="继续",
        history=[],
        context_override={},
        validate_output=False,
    )

    result = asyncio.run(kernel.run("pm", request))

    assert result.error is None
    assert result.is_complete is True
    assert call_count[0] == 2
    assert len(result.tool_calls) == 1
    assert len(result.tool_results) == 1


def test_run_examples_inside_code_blocks_do_not_execute(monkeypatch) -> None:
    """Non-streaming equivalent of test_stream_examples_inside_code_blocks_do_not_execute.

    Verifies that tool call examples inside code blocks (e.g. ```[TOOL_CALL]...[/TOOL_CALL]```)
    are not extracted as executable calls in the non-streaming path.
    The CanonicalToolCallParser protects code-block spans, so no tool execution occurs.
    """
    kernel = _build_kernel()
    tool_executed = False

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-run-codeblock", core_hash="fp-run-codeblock"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_retry_prompt",
        lambda _base, _quality_dict, _attempt: "system-prompt",
    )

    async def _fake_call(**_kwargs):
        return SimpleNamespace(
            content=(
                "## 可用工具清单\n\n"
                "```text\n"
                "[EXECUTE_COMMAND]\n"
                'command: "python -m pytest tests/ -v"\n'
                "[/EXECUTE_COMMAND]\n"
                "```\n\n"
                "```text\n"
                "[READ_FILE]\n"
                'path: "src/main.py"\n'
                "[/READ_FILE]\n"
                "```"
            ),
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=80,
            error=None,
            error_category=None,
            metadata={},
        )

    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    async def _fake_execute_single_tool(tool_name, args, context):
        nonlocal tool_executed
        tool_executed = True
        return {
            "success": False,
            "tool": tool_name,
            "error": "Should not execute",
            "result": {},
        }

    monkeypatch.setattr(kernel, "_execute_single_tool", _fake_execute_single_tool)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="你能调用哪些工具",
        history=[],
        context_override={},
        validate_output=False,
    )

    result = asyncio.run(kernel.run("pm", request))

    # No tool should have been executed (code-block examples are protected)
    assert tool_executed is False
    # Final result should contain the original content
    assert "可用工具清单" in result.content
    # No tool_calls or tool_results in the result
    assert len(result.tool_calls) == 0
    assert len(result.tool_results) == 0
    # No error
    assert result.error is None
    assert result.is_complete is True


def test_run_thinking_only_response_returns_explicit_error(monkeypatch) -> None:
    """Non-streaming path should not silently accept thinking-only output."""
    kernel = _build_kernel()

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-run-thinking-only", core_hash="fp-run-thinking-only"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_retry_prompt",
        lambda _base, _quality_dict, _attempt: "system-prompt",
    )

    async def _fake_call(**_kwargs):
        return SimpleNamespace(
            content="<thinking>先分析一下</thinking",
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=24,
            error=None,
            error_category=None,
            metadata={},
        )

    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="给我结论",
        history=[],
        context_override={},
        validate_output=False,
    )

    result = asyncio.run(kernel.run("pm", request))

    assert result.error is not None
    # NOTE: The fake LLM does not set a `thinking` field, so the decoder sees
    # reasoning_summary=None and falls back to the generic "no visible output"
    # suspended_reason. In production, a provider that extracts thinking into
    # response.thinking would yield the "thinking-only" variant.
    assert "awaiting user clarification" in result.error
    assert result.is_complete is False


def test_run_blank_response_returns_explicit_error(monkeypatch) -> None:
    """Non-streaming path should reject blank output with no tools."""
    kernel = _build_kernel()

    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_fingerprint",
        lambda _profile, _appendix: SimpleNamespace(full_hash="fp-run-blank", core_hash="fp-run-blank"),
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_system_prompt",
        lambda _profile, _appendix: "system-prompt",
    )
    monkeypatch.setattr(
        kernel._prompt_builder,
        "build_retry_prompt",
        lambda _base, _quality_dict, _attempt: "system-prompt",
    )

    async def _fake_call(**_kwargs):
        return SimpleNamespace(
            content="",
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=0,
            error=None,
            error_category=None,
            metadata={},
        )

    monkeypatch.setattr(kernel._injected_llm_caller, "call", _fake_call)

    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message="给我结论",
        history=[],
        context_override={},
        validate_output=False,
    )

    result = asyncio.run(kernel.run("pm", request))

    assert result.error is not None
    assert "model returned no visible output or tool calls" in result.error
    assert result.is_complete is False


def test_parse_content_and_thinking_tool_calls_ignores_thinking_wrappers() -> None:
    """thinking wrappers must never become executable tool calls."""
    kernel = _build_kernel()
    profile = kernel.registry.get_profile_or_raise("pm")

    tool_calls = kernel._parse_content_and_thinking_tool_calls(
        content="这里没有工具调用。",
        thinking='[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
        profile=profile,
        native_tool_calls=None,
        native_tool_provider="auto",
    )

    assert tool_calls == []


def test_parse_content_and_thinking_tool_calls_keeps_native_only() -> None:
    """native tool calls remain valid, thinking wrappers are ignored."""
    kernel = _build_kernel()
    profile = kernel.registry.get_profile_or_raise("pm")

    tool_calls = kernel._parse_content_and_thinking_tool_calls(
        content="",
        thinking='[TOOL_CALL]{"tool":"read_file","arguments":{"path":"SHOULD_NOT_RUN.md"}}[/TOOL_CALL]',
        profile=profile,
        native_tool_calls=[
            {
                "id": "native_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path":"README.md"}',
                },
            }
        ],
        native_tool_provider="openai",
    )

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "read_file"
    assert tool_calls[0].args.get("path") == "README.md"


def test_parse_content_and_thinking_tool_calls_accepts_openai_shape_with_anthropic_hint() -> None:
    """provider hint must not discard a valid native payload shape."""
    kernel = _build_kernel()
    profile = kernel.registry.get_profile_or_raise("pm")

    tool_calls = kernel._parse_content_and_thinking_tool_calls(
        content="",
        thinking="",
        profile=profile,
        native_tool_calls=[_openai_native_tool_call("README.md")],
        native_tool_provider="anthropic",
    )

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "read_file"
    assert tool_calls[0].args == {"path": "README.md"}
