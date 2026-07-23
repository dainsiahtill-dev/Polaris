"""Gate 9: Ledger / telemetry alignment tests.

验证：
- phase events 与 ledger 状态一致
- audit ledger 可完整导出
- truth log 与 ledger 可对齐
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, TurnPhaseEvent
from polaris.kernelone.context.truth_log_service import TruthLogService


@contextmanager
def _registered_async_tool() -> Iterator[str]:
    """Expose one real async ToolSpec without leaking registry state across tests."""
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    token = ToolSpecRegistry._state_var.set(ToolSpecRegistry._get_state())
    name = "enqueue_background_job"
    try:
        ToolSpecRegistry.register(
            name,
            {
                "description": "Enqueue an asynchronous background job",
                "category": "async",
                "arguments": [{"name": "title", "type": "string", "required": True}],
                "arg_aliases": {"title": "title"},
            },
            strict=True,
        )
        yield name
    finally:
        ToolSpecRegistry._state_var.reset(token)


class TestLedgerTelemetryAlignment:
    def test_llm_call_metadata_is_preserved(self) -> None:
        ledger = TurnLedger(turn_id="turn_audit")
        audit = {"ok": True, "prompt_digest": "digest1234"}

        ledger.record_llm_call(
            phase="decision",
            model="test-model",
            tokens_in=12,
            tokens_out=4,
            metadata={"context_os_audit": audit},
        )

        assert ledger.llm_calls[0]["metadata"]["context_os_audit"] == audit

    @pytest.mark.asyncio
    async def test_execute_result_carries_ledger_context_os_audit_for_tool_batches(self) -> None:
        audit = {"ok": True, "prompt_digest": "digest1234"}
        llm = AsyncMock(
            return_value={
                "content": "Read file.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "main.py"}'},
                    }
                ],
                "model": "test-model",
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "context_os_audit": audit,
                },
            }
        )
        tool_runtime = AsyncMock(return_value={"success": True, "result": "content"})
        controller = TurnTransactionController(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )
        from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode

        controller.decoder._default_finalize = FinalizeMode.NONE

        result = await controller.execute(
            "turn_audit_tool", [{"role": "user", "content": "read"}], [{"name": "read_file"}]
        )

        ledger = result["ledger"]
        assert isinstance(ledger, TurnLedger)
        assert ledger.llm_calls[0]["metadata"]["context_os_audit"] == audit

    @pytest.mark.asyncio
    async def test_execute_result_carries_final_request_audit_metadata(self) -> None:
        final_audit = {
            "schema_version": "llm.final_request_context_audit.v1",
            "final_request_token_estimate": 48000,
        }
        llm = AsyncMock(
            return_value={
                "content": "Final answer.",
                "model": "test-model",
                "usage": {
                    "usage": {"prompt_tokens": 120, "completion_tokens": 24},
                    "final_request_context_audit": final_audit,
                    "context_snapshot_ref": "runtime/contexts/aa/bbbb.json",
                },
            }
        )
        tool_runtime = AsyncMock(return_value={"success": True, "result": "content"})
        controller = TurnTransactionController(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )

        result = await controller.execute("turn_final_audit", [{"role": "user", "content": "answer"}], [])

        metadata = result["llm_response_metadata"]
        assert metadata["final_request_context_audit"] == final_audit
        assert metadata["context_snapshot_ref"] == "runtime/contexts/aa/bbbb.json"
        assert result["ledger"].llm_calls[0]["metadata"]["final_request_context_audit"] == final_audit

    @pytest.mark.asyncio
    async def test_execute_result_projects_native_tool_usage_metadata(self) -> None:
        llm = AsyncMock(
            return_value={
                "content": "Final answer.",
                "model": "test-model",
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "native_tool_calls_count": 2,
                    "decision_caller_native_tool_calls_count": 2,
                    "native_tool_call_names": ["read_file", "write_file"],
                    "tool_call_provider": "openai",
                },
            }
        )
        controller = TurnTransactionController(
            llm_provider=llm,
            tool_runtime=AsyncMock(),
            config=TransactionConfig(domain="code"),
        )

        result = await controller.execute("turn_native_usage", [{"role": "user", "content": "answer"}], [])

        metadata = result["llm_response_metadata"]
        assert metadata["native_tool_calls_count"] == 2
        assert metadata["decision_caller_native_tool_calls_count"] == 2
        assert metadata["native_tool_call_names"] == ["read_file", "write_file"]
        assert metadata["tool_call_provider"] == "openai"
        assert result["ledger"].llm_calls[0]["metadata"]["native_tool_calls_count"] == 2

    @pytest.mark.asyncio
    async def test_execute_stream_carries_provider_context_os_audit_into_completion_monitoring(self) -> None:
        audit = {
            "ok": True,
            "expected": True,
            "source": "test",
            "prompt_digest": "stream123",
        }

        async def llm_stream(_request: dict[str, object]):
            yield {"type": "chunk", "content": "Final answer."}
            yield {
                "type": "context_metadata",
                "model": "test-stream-model",
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                },
                "context_os_audit": audit,
            }

        controller = TurnTransactionController(
            llm_provider=AsyncMock(),
            llm_provider_stream=llm_stream,
            tool_runtime=AsyncMock(),
            config=TransactionConfig(domain="document"),
        )

        events: list[object] = []
        async for event in controller.execute_stream("turn_stream_audit", [{"role": "user", "content": "say hi"}], []):
            events.append(event)

        completion = next(event for event in events if isinstance(event, CompletionEvent))
        monitoring = completion.monitoring or {}
        context_os_audit = monitoring["context_os_audit"]

        assert context_os_audit["ok"] is True
        assert context_os_audit["llm_call_count"] == 1
        assert context_os_audit["latest"]["prompt_digest"] == "stream123"

    @pytest.mark.asyncio
    async def test_phase_events_match_ledger_states(self) -> None:
        llm = AsyncMock(
            return_value={
                "content": "Final answer.",
                "model": "test-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )
        tool_runtime = AsyncMock()
        controller = TurnTransactionController(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )

        events: list[object] = []
        controller.on_event(lambda e: events.append(e))

        result = await controller.execute("turn_1", [{"role": "user", "content": "hi"}], [])

        phase_events = [e for e in events if isinstance(e, TurnPhaseEvent)]
        phases = [e.phase for e in phase_events]
        ledger_states = result["state_trajectory"]

        assert "decision_requested" in phases
        assert "decision_completed" in phases
        assert "CONTEXT_BUILT" in ledger_states
        assert "DECISION_REQUESTED" in ledger_states
        assert "COMPLETED" in ledger_states

    @pytest.mark.asyncio
    async def test_audit_ledger_exports_all_fields(self) -> None:
        llm = AsyncMock(
            return_value={
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
        )
        tool_runtime = AsyncMock(return_value={"success": True, "result": "content"})
        controller = TurnTransactionController(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )
        # 强制使用 NONE 模式，确保只调用一次 LLM
        from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode

        controller.decoder._default_finalize = FinalizeMode.NONE

        result = await controller.execute("turn_2", [{"role": "user", "content": "read"}], [{"name": "read_file"}])

        assert result["metrics"]["llm_calls"] == 1
        assert result["metrics"]["tool_calls"] == 1
        assert result["metrics"]["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_llm_once_ledger_records_two_calls(self) -> None:
        call_count = 0

        async def tracking_llm(request: dict[str, object]) -> dict[str, object]:
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

        tool_runtime = AsyncMock(return_value={"success": True, "result": "content"})
        controller = TurnTransactionController(
            llm_provider=tracking_llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="document"),
        )

        result = await controller.execute("turn_3", [{"role": "user", "content": "read"}], [{"name": "read_file"}])

        assert result["metrics"]["llm_calls"] == 2
        assert result["finalization"]["mode"] == "llm_once"

    @pytest.mark.asyncio
    async def test_truth_log_aligns_with_ledger_decisions(self) -> None:
        log = TruthLogService()
        llm = AsyncMock(
            return_value={
                "content": "Final answer.",
                "model": "test-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )
        tool_runtime = AsyncMock()
        controller = TurnTransactionController(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )

        result = await controller.execute("turn_4", [{"role": "user", "content": "hi"}], [])

        log.append(
            {
                "turn_id": result["turn_id"],
                "kind": result["decision"]["kind"],
                "llm_calls": result["metrics"]["llm_calls"],
                "tool_calls": result["metrics"]["tool_calls"],
            }
        )

        replayed = log.replay()
        assert replayed[0]["turn_id"] == "turn_4"
        assert replayed[0]["kind"] == "final_answer"
        assert replayed[0]["llm_calls"] == 1
        assert replayed[0]["tool_calls"] == 0

    @pytest.mark.asyncio
    async def test_tool_batch_events_align_with_ledger(self) -> None:
        llm = AsyncMock(
            return_value={
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
        )
        tool_runtime = AsyncMock(return_value={"success": True, "result": "content"})
        controller = TurnTransactionController(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="code"),
        )

        events: list[object] = []
        controller.on_event(lambda e: events.append(e))

        result = await controller.execute("turn_5", [{"role": "user", "content": "read"}], [{"name": "read_file"}])

        phase_events = [e for e in events if isinstance(e, TurnPhaseEvent)]
        phases = [e.phase for e in phase_events]

        assert "tool_batch_started" in phases
        assert "tool_batch_completed" in phases
        assert result["metrics"]["tool_calls"] == 1
        assert "TOOL_BATCH_EXECUTING" in result["state_trajectory"]
        assert "TOOL_BATCH_EXECUTED" in result["state_trajectory"]

    @pytest.mark.asyncio
    async def test_handoff_ledger_records_zero_tools(self) -> None:
        with _registered_async_tool() as async_tool:
            llm = AsyncMock(
                return_value={
                    "content": "Queue work.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": async_tool, "arguments": '{"title": "job"}'},
                        }
                    ],
                    "model": "test-model",
                    "usage": {"prompt_tokens": 15, "completion_tokens": 8},
                }
            )
            tool_runtime = AsyncMock()
            controller = TurnTransactionController(
                llm_provider=llm,
                tool_runtime=tool_runtime,
                config=TransactionConfig(domain="document"),
            )

            result = await controller.execute("turn_6", [{"role": "user", "content": "queue"}], [{"name": async_tool}])

        assert result["kind"] == "handoff_workflow"
        assert result["metrics"]["tool_calls"] == 0
        assert "HANDOFF_WORKFLOW" in result["state_trajectory"]
        assert "COMPLETED" in result["state_trajectory"]
