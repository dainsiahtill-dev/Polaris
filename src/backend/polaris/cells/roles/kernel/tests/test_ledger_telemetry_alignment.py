"""Gate 9: Ledger / telemetry alignment tests.

验证：
- phase events 与 ledger 状态一致
- audit ledger 可完整导出
- truth log 与 ledger 可对齐
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TransactionConfig,
    TurnTransactionController,
)
from polaris.cells.roles.kernel.public.turn_events import TurnPhaseEvent
from polaris.kernelone.context.truth_log_service import TruthLogService


class TestLedgerTelemetryAlignment:
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
        controller = TurnTransactionController(
            llm_provider=llm,
            tool_runtime=tool_runtime,
            config=TransactionConfig(domain="document"),
        )

        result = await controller.execute(
            "turn_6", [{"role": "user", "content": "pr"}], [{"name": "create_pull_request"}]
        )

        assert result["kind"] == "handoff_workflow"
        assert result["metrics"]["tool_calls"] == 0
        assert "HANDOFF_WORKFLOW" in result["state_trajectory"]
        assert "COMPLETED" in result["state_trajectory"]
