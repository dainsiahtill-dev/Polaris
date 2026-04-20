from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculative_executor import (
    SpeculativeExecutor,
)
from polaris.cells.roles.kernel.internal.speculative_flags import (
    is_speculative_execution_enabled,
)
from polaris.cells.roles.kernel.internal.stream_shadow_engine import (
    StreamShadowEngine,
)
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TransactionConfig,
    TurnTransactionController,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
)
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent


def _sample_invocation() -> ToolInvocation:
    return ToolInvocation(
        call_id=ToolCallId("spec_call_1"),
        tool_name="read_file",
        arguments={"path": "README.md"},
        effect_type=ToolEffectType.READ,
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
    )


def test_speculative_flag_defaults_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_SPECULATIVE_EXECUTION", raising=False)
    monkeypatch.delenv("KERNELONE_ENABLE_SPECULATIVE_EXECUTION", raising=False)
    assert is_speculative_execution_enabled() is False


def test_speculative_flag_prefers_primary_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "true")
    monkeypatch.setenv("KERNELONE_ENABLE_SPECULATIVE_EXECUTION", "false")
    assert is_speculative_execution_enabled() is True


def test_speculative_flag_compat_env_when_primary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_SPECULATIVE_EXECUTION", raising=False)
    monkeypatch.setenv("KERNELONE_ENABLE_SPECULATIVE_EXECUTION", "1")
    assert is_speculative_execution_enabled() is True


@pytest.mark.asyncio
async def test_speculative_executor_disabled_short_circuits() -> None:
    runtime = cast(Any, SimpleNamespace(execute_batch=AsyncMock()))
    executor = SpeculativeExecutor(runtime, enabled=False)

    result = await executor.speculate(_sample_invocation())

    assert result == {
        "enabled": False,
        "result": None,
        "error": "speculative_execution_disabled",
    }
    runtime.execute_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_speculative_executor_enabled_executes_batch() -> None:
    tool_result = SimpleNamespace(status="success", result={"path": "README.md"})
    receipt = SimpleNamespace(results=[tool_result])
    runtime = cast(Any, SimpleNamespace(execute_batch=AsyncMock(return_value=[receipt])))
    executor = SpeculativeExecutor(runtime, enabled=True)

    result = await executor.speculate(_sample_invocation())

    assert executor.enabled is True
    assert result == {
        "enabled": True,
        "result": {"path": "README.md"},
        "error": None,
    }
    runtime.execute_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_shadow_engine_reports_executor_enablement() -> None:
    runtime = cast(Any, SimpleNamespace(execute_batch=AsyncMock()))
    executor = SpeculativeExecutor(runtime, enabled=True)
    engine = StreamShadowEngine(executor)

    engine.consume_delta("<tool_call>")
    result = await engine.speculate_from_buffer()

    assert result["enabled"] is True
    assert result["buffer_length"] > 0


@pytest.mark.asyncio
async def test_stream_controller_speculates_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "true")

    async def stream_provider(_request_payload: dict[str, Any]):
        yield {"type": "reasoning_chunk", "content": "思考中"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
            "call_id": "call_readme",
            "metadata": {
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "README.md"},
                    "call_id": "call_readme",
                }
            },
        }

    llm_provider = AsyncMock(
        return_value={
            "content": "Done.",
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    tool_runtime = AsyncMock(return_value={"success": True, "result": {"path": "README.md"}})
    controller = TurnTransactionController(
        llm_provider=llm_provider,
        tool_runtime=tool_runtime,
        config=TransactionConfig(domain="code"),
        llm_provider_stream=stream_provider,
    )

    completion_event: CompletionEvent | None = None
    async for event in controller.execute_stream(
        turn_id="turn_spec_enabled",
        context=[{"role": "user", "content": "read me"}],
        tool_definitions=[{"name": "read_file", "parameters": {}}],
    ):
        if isinstance(event, CompletionEvent):
            completion_event = event

    # 1x speculative read; canonical execution ADOPTed (no extra tool_runtime call)
    assert tool_runtime.await_count == 1
    assert completion_event is not None
    monitoring = cast(dict[str, float], completion_event.monitoring or {})
    assert monitoring["speculative.hit_rate"] == 1.0
    assert monitoring["speculative.false_positive_rate"] == 0.0


@pytest.mark.asyncio
async def test_stream_controller_no_speculation_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "false")

    async def stream_provider(_request_payload: dict[str, Any]):
        yield {"type": "content_chunk", "content": "先读文件"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "README.md"},
            "call_id": "call_readme",
            "metadata": {
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "README.md"},
                    "call_id": "call_readme",
                }
            },
        }

    llm_provider = AsyncMock(
        return_value={
            "content": "Done.",
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    tool_runtime = AsyncMock(return_value={"success": True, "result": {"path": "README.md"}})
    controller = TurnTransactionController(
        llm_provider=llm_provider,
        tool_runtime=tool_runtime,
        config=TransactionConfig(domain="code"),
        llm_provider_stream=stream_provider,
    )

    completion_event: CompletionEvent | None = None
    async for event in controller.execute_stream(
        turn_id="turn_spec_disabled",
        context=[{"role": "user", "content": "read me"}],
        tool_definitions=[{"name": "read_file", "parameters": {}}],
    ):
        if isinstance(event, CompletionEvent):
            completion_event = event

    assert tool_runtime.await_count == 1
    assert completion_event is not None
    monitoring = cast(dict[str, float], completion_event.monitoring or {})
    assert monitoring["speculative.hit_rate"] == 0.0
    assert monitoring["speculative.false_positive_rate"] == 0.0
