"""Gate 7: Stream / Non-stream parity tests using TransactionKernel directly.

验证：同输入、同上下文、同策略下，stream 与 non-stream 得到相同的 decision/outcome。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TransactionConfig
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent


async def _collect_stream(
    kernel: TransactionKernel, turn_id: str, context: list[dict], tool_definitions: list[dict]
) -> dict[str, Any]:
    events: list[Any] = []
    async for event in kernel.execute_stream(turn_id, context, tool_definitions):
        events.append(event)

    completion = [e for e in events if isinstance(e, CompletionEvent)]
    assert len(completion) == 1
    comp = completion[0]
    return {
        "events": events,
        "duration_ms": comp.duration_ms,
        "llm_calls": comp.llm_calls,
        "tool_calls": comp.tool_calls,
        "status": comp.status,
    }


class TestStreamNonStreamParity:
    @pytest.mark.asyncio
    async def test_final_answer_parity(self) -> None:
        llm = AsyncMock(
            return_value={
                "content": "Final answer.",
                "model": "test-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )
        tool_runtime = AsyncMock()
        kernel = TransactionKernel(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )

        context = [{"role": "user", "content": "say hi"}]
        tools: list[dict] = []

        run_result = await kernel.execute("turn_1", context, tools)
        stream_summary = await _collect_stream(kernel, "turn_1", context, tools)

        assert run_result["kind"] == "final_answer"
        assert stream_summary["status"] == "success"
        assert run_result["metrics"]["llm_calls"] == stream_summary["llm_calls"] == 1
        assert run_result["metrics"]["tool_calls"] == stream_summary["tool_calls"] == 0

    @pytest.mark.asyncio
    async def test_tool_batch_none_mode_parity(self) -> None:
        call_count = 0

        async def tracking_llm(request: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if request.get("tools") is None:
                # 收口阶段：tool_choice=none，禁止返回 tool_calls
                return {
                    "content": "File read successfully.",
                    "tool_calls": [],
                    "model": "test-model",
                    "usage": {"prompt_tokens": 30, "completion_tokens": 5},
                }
            return {
                "content": "Read file.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "main.py"}'},
                    }
                ],
                "model": "test-model",
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            }

        tool_runtime = AsyncMock(return_value={"success": True, "result": "file content"})
        kernel = TransactionKernel(
            llm_provider=tracking_llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )

        context = [{"role": "user", "content": "read main.py"}]
        tools = [{"name": "read_file", "description": "Read a file"}]

        run_result = await kernel.execute("turn_2", context, tools)
        stream_summary = await _collect_stream(kernel, "turn_2", context, tools)

        assert run_result["kind"] == "tool_batch_with_receipt"
        assert stream_summary["status"] == "success"
        assert run_result["metrics"]["llm_calls"] == stream_summary["llm_calls"] == 2
        assert run_result["metrics"]["tool_calls"] == stream_summary["tool_calls"] == 1

    @pytest.mark.asyncio
    async def test_llm_once_mode_parity(self) -> None:
        call_count = 0

        async def tracking_llm(request: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if request.get("tools") is None:
                return {
                    "content": "Summary.",
                    "tool_calls": [],
                    "model": "test-model",
                    "usage": {"prompt_tokens": 30, "completion_tokens": 5},
                }
            return {
                "content": "Read file.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "main.py"}'},
                    }
                ],
                "model": "test-model",
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            }

        tool_runtime = AsyncMock(return_value={"success": True, "result": "file content"})
        kernel = TransactionKernel(
            llm_provider=tracking_llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="document"),
        )

        context = [{"role": "user", "content": "read main.py"}]
        tools = [{"name": "read_file", "description": "Read a file"}]

        run_result = await kernel.execute("turn_3", context, tools)
        stream_summary = await _collect_stream(kernel, "turn_3", context, tools)

        assert run_result["kind"] == "tool_batch_with_receipt"
        assert run_result["finalization"]["mode"] == "llm_once"
        assert stream_summary["status"] == "success"
        assert run_result["metrics"]["llm_calls"] == stream_summary["llm_calls"] == 2

    @pytest.mark.asyncio
    async def test_handoff_workflow_parity(self) -> None:
        llm = AsyncMock(
            return_value={
                "content": "Create PR.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "create_pull_request", "arguments": '{"title": "PR"}'},
                    }
                ],
                "model": "test-model",
                "usage": {"prompt_tokens": 15, "completion_tokens": 8},
            }
        )
        tool_runtime = AsyncMock()
        kernel = TransactionKernel(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="document"),
        )

        context = [{"role": "user", "content": "create pr"}]
        tools = [{"name": "create_pull_request", "description": "Create PR"}]

        run_result = await kernel.execute("turn_4", context, tools)
        stream_summary = await _collect_stream(kernel, "turn_4", context, tools)

        assert run_result["kind"] == "handoff_workflow"
        assert stream_summary["status"] == "handoff"
        assert run_result["metrics"]["llm_calls"] == stream_summary["llm_calls"] == 1
        assert run_result["metrics"]["tool_calls"] == stream_summary["tool_calls"] == 0

    @pytest.mark.asyncio
    async def test_identical_visible_content_parity(self) -> None:
        llm = AsyncMock(
            return_value={
                "content": "Exact visible content.",
                "model": "test-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )
        tool_runtime = AsyncMock()
        kernel = TransactionKernel(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )

        context = [{"role": "user", "content": "test"}]
        tools: list[dict] = []

        run_result = await kernel.execute("turn_5", context, tools)
        stream_summary = await _collect_stream(kernel, "turn_5", context, tools)

        assert run_result["visible_content"] == "Exact visible content."
        assert stream_summary["status"] == "success"
