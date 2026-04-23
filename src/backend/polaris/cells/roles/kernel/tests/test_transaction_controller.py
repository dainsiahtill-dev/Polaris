# ruff: noqa: B017
"""
Tests for Turn Transaction Controller

验证：
1. 正常turn执行流程
2. LLM_ONCE finalization强制tool_choice=none
3. 禁止continuation loop
4. 工具并行/串行执行
5. workflow handoff
6. 错误处理
"""

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from polaris.cells.roles.kernel.internal.metrics import MetricsCollector
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    CompletionEvent,
    TransactionConfig,
    TurnTransactionController,
)
from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode, TurnDecisionKind
from polaris.cells.storage.layout.public.service import resolve_polaris_roots


def _native_tool_call(
    name: str,
    arguments: dict[str, object],
    *,
    call_id: str | None = None,
) -> dict[str, object]:
    """Build an OpenAI-style native function-call payload for tests."""
    return {
        "id": call_id or f"call_{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


# ============ Fixtures ============


@pytest.fixture
def mock_llm_provider():
    """Mock LLM provider"""
    provider = AsyncMock()
    return provider


@pytest.fixture
def mock_tool_runtime():
    """Mock tool runtime"""
    runtime = AsyncMock()
    return runtime


@pytest.fixture
def controller(mock_llm_provider, mock_tool_runtime):
    """Create controller with mocks - code domain now defaults to LLM_ONCE"""
    config = TransactionConfig(domain="code")  # code domain defaults to LLM_ONCE
    return TurnTransactionController(llm_provider=mock_llm_provider, tool_runtime=mock_tool_runtime, config=config)


@pytest.fixture
def basic_context():
    """Basic conversation context"""
    return [{"role": "user", "content": "Read main.py and tell me its contents"}]


@pytest.fixture
def basic_tool_definitions():
    """Basic tool definitions"""
    return [{"name": "read_file", "description": "Read a file", "parameters": {}}]


# ============ Test Final Answer Path ============


class TestFinalAnswerPath:
    """测试直接回答路径"""

    @pytest.mark.asyncio
    async def test_final_answer_turn(
        self, controller, mock_llm_provider, basic_context, basic_tool_definitions
    ) -> None:
        """直接回答turn完整流程"""
        # LLM返回直接回答
        mock_llm_provider.return_value = {
            "content": "The main.py file contains the entry point.",
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

        result = await controller.execute(
            turn_id="turn_1", context=basic_context, tool_definitions=basic_tool_definitions
        )

        assert result["turn_id"] == "turn_1"
        # 使用正确的key: visible_content
        assert "entry point" in result["visible_content"]
        assert result["metrics"]["llm_calls"] == 1
        # 无工具调用时tool_executions为空
        assert result["metrics"]["tool_calls"] == 0

        # 状态轨迹 - state_trajectory is at result root level, not in metrics
        states = result["state_trajectory"]
        assert "CONTEXT_BUILT" in states
        assert "DECISION_DECODED" in states
        assert "FINAL_ANSWER_READY" in states
        assert "COMPLETED" in states

    @pytest.mark.asyncio
    async def test_mutation_request_without_write_tools_downgrades_to_propose_patch(
        self, mock_llm_provider, mock_tool_runtime
    ) -> None:
        mock_llm_provider.return_value = {
            "content": "planning-only response",
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        }
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider,
            tool_runtime=mock_tool_runtime,
            config=TransactionConfig(domain="code"),
        )
        state_machine = TurnStateMachine(turn_id="turn_readonly_downgrade")
        ledger = TurnLedger(turn_id="turn_readonly_downgrade")

        result = await controller._execute_turn(
            turn_id="turn_readonly_downgrade",
            context=[{"role": "user", "content": "请进一步完善 Session Orchestrator"}],
            tool_definitions=[
                {"type": "function", "function": {"name": "read_file"}},
                {"type": "function", "function": {"name": "glob"}},
            ],
            state_machine=state_machine,
            ledger=ledger,
            stream=False,
        )

        assert result["kind"] == "final_answer"
        assert ledger.delivery_contract.mode == DeliveryMode.PROPOSE_PATCH
        assert any(flag.get("type") == "DELIVERY_CONTRACT_DOWNGRADED_NO_WRITE_TOOLS" for flag in ledger.anomaly_flags)

    @pytest.mark.asyncio
    async def test_final_answer_no_llm_continuation(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """直接回答后禁止LLM继续（continuation loop防护）"""
        mock_llm_provider.return_value = {
            "content": "Final answer without tools.",
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

        await controller.execute(
            turn_id="turn_no_continuation", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # 只能调用一次LLM
        assert mock_llm_provider.call_count == 1
        # 工具从未被调用
        assert mock_tool_runtime.call_count == 0


# ============ Test Tool Batch Execution ============


class TestToolBatchExecution:
    """测试工具批次执行"""

    @pytest.mark.asyncio
    async def test_single_read_tool(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """单读文件工具 - code域默认LLM_ONCE"""
        mock_llm_provider.side_effect = [
            {
                "content": "我先读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
            {
                "content": "The file contains a hello world program.",
                "tool_calls": [],
                "model": "claude",
                "usage": {"prompt_tokens": 200, "completion_tokens": 50},
            },
        ]

        # 工具返回结果
        mock_tool_runtime.return_value = {"success": True, "result": "# main.py\nprint('hello')"}

        result = await controller.execute(
            turn_id="turn_tool_1", context=basic_context, tool_definitions=basic_tool_definitions
        )

        assert result["turn_id"] == "turn_tool_1"
        # 使用batch_receipt
        assert result["batch_receipt"] is not None
        # 验证工具结果在visible_content中（LLM_ONCE收口后的摘要）
        assert "hello" in result["visible_content"]

    @pytest.mark.asyncio
    async def test_multiple_readonly_parallel(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """多个只读工具并行执行 - code域默认LLM_ONCE"""
        # LLM返回多个只读工具
        mock_llm_provider.side_effect = [
            {
                "content": "并行读取两个文件。",
                "tool_calls": [
                    _native_tool_call("read_file", {"path": "a.py"}, call_id="call_a"),
                    _native_tool_call("read_file", {"path": "b.py"}, call_id="call_b"),
                ],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
            {
                "content": "Summary of both files.",
                "tool_calls": [],
                "model": "claude",
                "usage": {"prompt_tokens": 200, "completion_tokens": 50},
            },
        ]

        mock_tool_runtime.return_value = {"success": True, "result": "file content"}

        result = await controller.execute(
            turn_id="turn_parallel", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # LLM_ONCE收口后的可见内容
        assert "Summary" in result["visible_content"]
        # code域默认LLM_ONCE：决策 + 收口 = 2次LLM调用
        assert mock_llm_provider.call_count == 2

    @pytest.mark.asyncio
    async def test_write_tool_serial(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """写工具串行执行 - code域默认LLM_ONCE"""
        mock_llm_provider.side_effect = [
            {
                "content": "写入 out.py。",
                "tool_calls": [_native_tool_call("write_file", {"path": "out.py", "content": "x"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
            {
                "content": "File written successfully.",
                "tool_calls": [],
                "model": "claude",
                "usage": {"prompt_tokens": 200, "completion_tokens": 50},
            },
        ]

        mock_tool_runtime.return_value = {
            "success": True,
            "result": "written",
            "effect_receipt": {"file": "out.py", "operation": "create"},
        }

        # 使用包含 mutation 意图的 context，避免被 delivery-mode-filter 过滤
        mutation_context = [{"role": "user", "content": "写入 out.py 文件"}]

        # 提供包含 write 工具的 tool_definitions，避免 MATERIALIZE_CHANGES 被降级为 PROPOSE_PATCH
        write_tool_definitions = [
            {"name": "read_file", "description": "Read a file", "parameters": {}},
            {"name": "write_file", "description": "Write a file", "parameters": {}},
        ]

        result = await controller.execute(
            turn_id="turn_write", context=mutation_context, tool_definitions=write_tool_definitions
        )

        assert result["batch_receipt"] is not None
        assert "write_file" in result["visible_content"]


# ============ Test LLM_ONCE Finalization ============


class TestLLMOnceFinalization:
    """测试LLM_ONCE收口"""

    @pytest.mark.asyncio
    async def test_llm_once_forces_tool_choice_none(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """
        关键测试：LLM_ONCE收口时强制tool_choice=none

        这确保LLM在收口阶段不能触发新工具，从而防止continuation loop
        """
        # 创建document域的controller
        config = TransactionConfig(domain="document")
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider, tool_runtime=mock_tool_runtime, config=config
        )

        call_order = []

        async def tracking_provider(request):
            call_order.append(
                {
                    "phase": "finalization" if request.get("tools") is None else "decision",
                    "tool_choice": request.get("tool_choice"),
                    "tools_provided": request.get("tools") is not None,
                }
            )
            if request.get("tools") is None:
                # 收口阶段 - 不能返回tool_calls
                return {
                    "content": "Summary of file contents.",
                    "tool_calls": [],  # 重要：不返回tool_calls
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 100},
                }
            return {
                "content": "先读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        controller.llm_provider = tracking_provider
        mock_tool_runtime.return_value = {"success": True, "result": "file content"}

        # 使用document域，默认为LLM_ONCE
        result = await controller.execute(
            turn_id="turn_llm_once", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # 验证：两次LLM调用
        assert len(call_order) == 2

        # 第一次（决策）：有tools，tool_choice=auto
        assert call_order[0]["tools_provided"] is True
        assert call_order[0]["tool_choice"] == "auto"

        # 第二次（收口）：无tools，tool_choice=none
        assert call_order[1]["tools_provided"] is False
        assert call_order[1]["tool_choice"] == "none"

        # 收口内容包含总结
        assert "Summary" in result["visible_content"] or "file content" in result["visible_content"]

    @pytest.mark.asyncio
    async def test_llm_once_rejects_tool_calls_in_response(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """LLM_ONCE收口时LLM不应返回工具调用，违规则走 protocol panic handoff"""
        # 创建document域的controller
        config = TransactionConfig(domain="document")
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider, tool_runtime=mock_tool_runtime, config=config
        )

        async def provider_with_violation(request):
            if request.get("tools") is None:
                # 收口阶段返回工具调用（违规）
                return {
                    "content": "Let me call another tool",
                    "tool_calls": [{"id": "call_violation", "function": {"name": "bad_tool", "arguments": "{}"}}],
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                }

            return {
                "content": "先读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        controller.llm_provider = provider_with_violation
        mock_tool_runtime.return_value = {"success": True, "result": "content"}

        result = await controller.execute(
            turn_id="turn_violation", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # Soft guard: tool calls during finalization are dropped, normal completion proceeds
        assert result["kind"] == "tool_batch_with_receipt"


# ============ Test NONE Finalize Mode ============


class TestNoneFinalizeMode:
    """测试NONE finalization（工具结果即答案）"""

    @pytest.mark.asyncio
    async def test_none_finalize_no_second_llm_call(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """NONE模式：工具结果就是最终答案，不再调用LLM"""
        # 强制使用NONE模式
        controller.decoder._default_finalize = FinalizeMode.NONE

        mock_llm_provider.return_value = {
            "content": "读取 main.py。",
            "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        }

        mock_tool_runtime.return_value = {"success": True, "result": "main file content"}

        result = await controller.execute(
            turn_id="turn_none", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # 只调用一次LLM
        assert mock_llm_provider.call_count == 1

        # 工具结果直接作为答案
        assert "main file content" in result["visible_content"]


# ============ Test Workflow Handoff ============


class TestWorkflowHandoff:
    """测试workflow移交"""

    @pytest.mark.asyncio
    async def test_handoff_triggered_by_async_tool(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """异步工具触发handoff"""
        mock_llm_provider.return_value = {
            "content": "提交 PR。",
            "tool_calls": [_native_tool_call("create_pull_request", {"title": "PR"})],
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        }

        result = await controller.execute(
            turn_id="turn_async", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # 移交到workflow
        assert result["kind"] == "handoff_workflow"
        # workflow_context存在且携带异步 receipt 的 handoff 语义
        assert result["workflow_context"] is not None
        assert result["workflow_context"]["handoff_reason"] == "async_operation"
        assert result["workflow_context"]["initial_tools"] == ["create_pull_request"]
        assert result["metrics"]["workflow.handoff_rate"] == 1.0
        # FIX: 比较字符串值而非枚举值
        assert result["decision"]["kind"] == TurnDecisionKind.HANDOFF_WORKFLOW.value
        # At handoff time, pending_async_receipts is empty because tools haven't executed yet
        # The tool_batch info is preserved in recoverable_context for later execution
        recoverable = result["workflow_context"]["recoverable_context"]
        assert "tool_batch" in recoverable
        assert recoverable["tool_batch"] is not None
        assert len(recoverable["tool_batch"].get("async_receipts", [])) == 1
        assert recoverable["tool_batch"].get("async_receipts", [])[0]["tool_name"] == "create_pull_request"

        # handoff 路径不能偷偷执行工具，也不能继续发起第二轮 LLM
        assert mock_tool_runtime.call_count == 0
        assert mock_llm_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_many_tools_go_through_tool_batch(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """大量纯读取工具走 TOOL_BATCH + LLM_ONCE，不再因数量多而 handoff。

        此前 6 个 read_file 会因 handoff_threshold_tools=5 被错误 handoff 到
        ExplorationWorkflowRuntime（synthesis_llm=None 导致无输出）。
        修复后纯读取批次始终走正常 TOOL_BATCH 流程。
        """
        mock_llm_provider.return_value = {
            "content": "需要读取多份文件。",
            "tool_calls": [
                _native_tool_call("read_file", {"path": f"file{i}.py"}, call_id=f"call_{i}") for i in range(6)
            ],
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        }

        result = await controller.execute(
            turn_id="turn_many", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # 应为 TOOL_BATCH 而非 HANDOFF_WORKFLOW
        assert result["kind"] == "tool_batch_with_receipt"
        assert result["decision"]["kind"] == TurnDecisionKind.TOOL_BATCH.value
        # 纯读取工具的 finalize_mode 应为 LLM_ONCE（需要 LLM 总结）
        assert result["decision"]["finalize_mode"] == FinalizeMode.LLM_ONCE.value
        # 工具应被实际执行
        assert mock_tool_runtime.call_count == 6


# ============ Test Continuation Loop Prevention ============


class TestContinuationLoopPrevention:
    """测试continuation loop防护"""

    @pytest.mark.asyncio
    async def test_no_loop_after_tool_execution(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """工具执行后不会自动继续（禁止continuation loop）"""
        call_count = 0

        async def counting_provider(request):
            nonlocal call_count
            call_count += 1
            # code域默认LLM_ONCE：决策 + 收口 = 2次，禁止第三次（continuation loop）
            if call_count == 1:
                return {
                    "content": "读取 main.py。",
                    "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                    "model": "claude",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30},
                }
            return {
                "content": "Final answer.",
                "tool_calls": [],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }

        controller.llm_provider = counting_provider
        mock_tool_runtime.return_value = {"success": True, "result": "done"}

        await controller.execute(turn_id="turn_no_loop", context=basic_context, tool_definitions=basic_tool_definitions)

        # LLM_ONCE模式下调用2次（决策 + 收口），但不会出现第三次（continuation loop）
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_state_machine_blocks_backward_transitions(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """状态机阻止向后转换"""
        from polaris.cells.roles.kernel.internal.turn_state_machine import InvalidStateTransitionError, TurnStateMachine

        sm = TurnStateMachine(turn_id="test_backward")

        # 走到TOOL_BATCH_EXECUTED
        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTED)

        # 尝试回到DECISION_REQUESTED应该失败
        with pytest.raises(InvalidStateTransitionError):
            sm.transition_to(TurnState.DECISION_REQUESTED)


# ============ Test Error Handling ============


class TestErrorHandling:
    """测试错误处理"""

    @pytest.mark.asyncio
    async def test_llm_failure_records_error(
        self, controller, mock_llm_provider, basic_context, basic_tool_definitions
    ) -> None:
        """LLM调用失败"""
        mock_llm_provider.side_effect = Exception("LLM unavailable")

        with pytest.raises(Exception):
            await controller.execute(
                turn_id="turn_llm_fail", context=basic_context, tool_definitions=basic_tool_definitions
            )

    @pytest.mark.asyncio
    async def test_tool_failure_continues(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """工具执行失败但turn继续 - code域默认LLM_ONCE"""
        mock_llm_provider.side_effect = [
            {
                "content": "读取 missing.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "missing.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
            {
                "content": "The tool execution failed.",
                "tool_calls": [],
                "model": "claude",
                "usage": {"prompt_tokens": 200, "completion_tokens": 50},
            },
        ]

        mock_tool_runtime.side_effect = Exception("File not found")

        result = await controller.execute(
            turn_id="turn_tool_fail", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # turn仍然完成 - state_trajectory is at result root level, not in metrics
        assert "COMPLETED" in result["state_trajectory"]


# ============ Test Ledger and Events ============


class TestLedgerAndEvents:
    """测试账本和事件"""

    @pytest.mark.asyncio
    async def test_ledger_tracks_all_calls(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """账本记录所有调用"""
        mock_llm_provider.side_effect = [
            {
                "content": "读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
            {
                "content": "Summary of file content.",
                "tool_calls": [],
                "model": "claude",
                "usage": {"prompt_tokens": 200, "completion_tokens": 50},
            },
        ]

        mock_tool_runtime.return_value = {"success": True, "result": "content"}

        result = await controller.execute(
            turn_id="turn_ledger", context=basic_context, tool_definitions=basic_tool_definitions
        )

        metrics = result["metrics"]
        # code域默认LLM_ONCE：决策 + 收口 = 2次LLM调用
        assert metrics["llm_calls"] == 2
        assert metrics["tool_calls"] == 1
        assert metrics["duration_ms"] >= 0  # May be 0 in fast tests
        assert metrics["transaction_kernel.violation_count"] == 0.0
        assert metrics["turn.single_batch_ratio"] == 1.0
        assert metrics["workflow.handoff_rate"] == 0.0
        assert metrics["kernel_guard.assert_fail_rate"] == 0.0
        assert metrics["speculative.hit_rate"] == 0.0
        assert metrics["speculative.false_positive_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_events_emitted(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """事件正确发送"""
        events_received = []

        def event_handler(event) -> None:
            events_received.append(event)

        controller.on_event(event_handler)

        mock_llm_provider.return_value = {
            "content": "Direct answer.",
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

        await controller.execute(turn_id="turn_events", context=basic_context, tool_definitions=basic_tool_definitions)

        # 检查关键事件
        event_types = [type(e).__name__ for e in events_received]
        assert "TurnPhaseEvent" in event_types
        assert "CompletionEvent" in event_types

    @pytest.mark.asyncio
    async def test_transaction_metrics_recorded_to_collector(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """Turn 完成后 Phase 7 指标被写入全局 MetricsCollector。"""
        MetricsCollector.reset()

        mock_llm_provider.side_effect = [
            {
                "content": "读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
            {
                "content": "Summary of file content.",
                "tool_calls": [],
                "model": "claude",
                "usage": {"prompt_tokens": 200, "completion_tokens": 50},
            },
        ]
        mock_tool_runtime.return_value = {"success": True, "result": "content"}

        result = await controller.execute(
            turn_id="turn_metrics_collector", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # Verify result metrics exist
        assert result["metrics"]["transaction_kernel.violation_count"] == 0.0
        assert result["metrics"]["turn.single_batch_ratio"] == 1.0

        # Verify global collector was updated
        from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector

        collector = get_metrics_collector()
        prom_text = collector.get_prometheus_format()
        assert "transaction_kernel_violation_count_total" in prom_text
        assert "turn_single_batch_ratio" in prom_text


# ============ Test Domain-Based Policies ============


class TestDomainPolicies:
    """测试领域策略"""

    @pytest.mark.asyncio
    async def test_document_domain_defaults_llm_once(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """document域默认LLM_ONCE"""
        config = TransactionConfig(domain="document")
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider, tool_runtime=mock_tool_runtime, config=config
        )

        call_count = 0

        async def tracking_provider(request):
            nonlocal call_count
            call_count += 1
            if request.get("tools") is None:
                return {
                    "content": "Summary.",
                    "tool_calls": [],
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 100},
                }
            return {
                "content": "先读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        controller.llm_provider = tracking_provider
        mock_tool_runtime.return_value = {"success": True, "result": "content"}

        await controller.execute(turn_id="turn_doc", context=basic_context, tool_definitions=basic_tool_definitions)

        # document域应该调用2次LLM（决策 + 收口）
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_code_domain_defaults_llm_once(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """code域默认LLM_ONCE"""
        config = TransactionConfig(domain="code")
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider, tool_runtime=mock_tool_runtime, config=config
        )

        call_count = 0

        async def tracking_provider(request):
            nonlocal call_count
            call_count += 1
            if request.get("tools") is None:
                return {
                    "content": "Summary of file contents.",
                    "tool_calls": [],
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 100},
                }
            return {
                "content": "读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        controller.llm_provider = tracking_provider
        mock_tool_runtime.return_value = {"success": True, "result": "content"}

        await controller.execute(turn_id="turn_code", context=basic_context, tool_definitions=basic_tool_definitions)

        # code域默认LLM_ONCE：决策 + 收口 = 2次LLM调用
        assert call_count == 2


# ============ Test Streaming ============


class TestStreaming:
    """测试流式执行"""

    @pytest.mark.asyncio
    async def test_stream_execute_returns_iterator(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """流式执行返回事件迭代器"""
        mock_llm_provider.return_value = {
            "content": "Streamed answer.",
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

        events = []
        async for event in controller.execute_stream(
            turn_id="turn_stream", context=basic_context, tool_definitions=basic_tool_definitions
        ):
            events.append(event)

        # 有完成事件
        assert any(isinstance(e, CompletionEvent) for e in events)

    @pytest.mark.asyncio
    async def test_stream_execute_uses_provided_turn_request_id(
        self, controller, mock_llm_provider, basic_context, basic_tool_definitions
    ) -> None:
        """execute_stream 显式传入 turn_request_id 时应贯穿所有 TurnEvent。"""
        mock_llm_provider.return_value = {
            "content": "Streamed answer with explicit request id.",
            "model": "claude",
            "usage": {"prompt_tokens": 80, "completion_tokens": 20},
        }

        request_id = "req_explicit_123"
        events: list[object] = []
        async for event in controller.execute_stream(
            turn_id="turn_stream_explicit_request_id",
            context=basic_context,
            tool_definitions=basic_tool_definitions,
            turn_request_id=request_id,
        ):
            events.append(event)

        assert events
        assert any(isinstance(e, CompletionEvent) for e in events)
        assert all(getattr(event, "turn_request_id", None) == request_id for event in events)

    @pytest.mark.asyncio
    async def test_stream_execute_auto_generates_turn_request_id(
        self, controller, mock_llm_provider, basic_context, basic_tool_definitions
    ) -> None:
        """execute_stream 未传 turn_request_id 时应自动生成且在同次流内稳定。"""
        mock_llm_provider.return_value = {
            "content": "Streamed answer with generated request id.",
            "model": "claude",
            "usage": {"prompt_tokens": 80, "completion_tokens": 20},
        }

        events: list[object] = []
        async for event in controller.execute_stream(
            turn_id="turn_stream_auto_request_id",
            context=basic_context,
            tool_definitions=basic_tool_definitions,
        ):
            events.append(event)

        assert events
        request_ids = {getattr(event, "turn_request_id", None) for event in events}
        assert len(request_ids) == 1
        generated_request_id = request_ids.pop()
        assert generated_request_id is not None
        assert generated_request_id.startswith("turnreq_")

    @pytest.mark.asyncio
    async def test_stream_execute_attaches_span_lineage(
        self, controller, mock_llm_provider, basic_context, basic_tool_definitions
    ) -> None:
        """execute_stream 应为每个事件注入 span_id，并透传 parent_span_id。"""
        mock_llm_provider.return_value = {
            "content": "Streamed answer with span lineage.",
            "model": "claude",
            "usage": {"prompt_tokens": 80, "completion_tokens": 20},
        }
        parent_span_id = "root_span_parent_1"

        events: list[object] = []
        async for event in controller.execute_stream(
            turn_id="turn_stream_span_lineage",
            context=basic_context,
            tool_definitions=basic_tool_definitions,
            parent_span_id=parent_span_id,
        ):
            events.append(event)

        assert events
        span_ids = [getattr(event, "span_id", None) for event in events]
        assert all(isinstance(span_id, str) and span_id.startswith("span_") for span_id in span_ids)
        assert len(set(span_ids)) == len(span_ids)
        assert all(getattr(event, "parent_span_id", None) == parent_span_id for event in events)

    @pytest.mark.asyncio
    async def test_stream_execute_records_truthlog_events(
        self,
        controller,
        mock_llm_provider,
        basic_tool_definitions,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """execute_stream 应写入 turn truthlog，并保持 turn_request_id 可追踪。"""
        mock_llm_provider.return_value = {
            "content": "Streamed answer with truthlog.",
            "model": "claude",
            "usage": {"prompt_tokens": 80, "completion_tokens": 20},
        }
        case_root = Path(__file__).resolve().parent / "_truthlog_stream_cases" / f"case_{uuid4().hex}"
        workspace_root = case_root / "workspace"
        runtime_base = case_root / "runtime_base"
        workspace_root.mkdir(parents=True, exist_ok=True)
        runtime_base.mkdir(parents=True, exist_ok=True)
        try:
            monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_base))
            monkeypatch.setenv("KERNELONE_HOME", str(case_root / "home"))
            context = [
                {
                    "role": "user",
                    "content": "Read main.py and summarize.",
                    "metadata": {"workspace": str(workspace_root)},
                }
            ]
            request_id = "req_truthlog_1"
            events: list[object] = []
            async for event in controller.execute_stream(
                turn_id="turn_stream_truthlog",
                context=context,
                tool_definitions=basic_tool_definitions,
                turn_request_id=request_id,
            ):
                events.append(event)

            assert events
            assert any(isinstance(event, CompletionEvent) for event in events)

            runtime_root = Path(resolve_polaris_roots(str(workspace_root)).runtime_root)
            log_path = runtime_root / "events" / "kernel.turn.truthlog.events.jsonl"
            assert log_path.exists()
            lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            assert lines
            rows = [json.loads(line) for line in lines]
            assert any(isinstance(row, dict) and row.get("event_type") == "CompletionEvent" for row in rows)
            request_ids = {str(row.get("turn_request_id", "")) for row in rows}
            assert request_ids == {request_id}
            turn_ids = {str(row.get("turn_id", "")) for row in rows}
            assert turn_ids == {"turn_stream_truthlog"}
            payloads = [row.get("payload") for row in rows if isinstance(row, dict)]
            assert all(isinstance(payload, dict) for payload in payloads)
            assert all(str(payload.get("span_id", "")).startswith("span_") for payload in payloads if payload)
            assert all(
                str(payload.get("parent_span_id", "")).startswith("turnspan_") for payload in payloads if payload
            )
        finally:
            shutil.rmtree(case_root, ignore_errors=True)


class TestMonkeypatchPropagation:
    """验证 llm_provider property 变更会传播到子模块（facade monkeypatch 穿透）。"""

    def test_propagates_to_finalization_handler(self, mock_llm_provider, mock_tool_runtime):
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider,
            tool_runtime=mock_tool_runtime,
            config=TransactionConfig(domain="code"),
        )
        new_provider = AsyncMock()
        controller.llm_provider = new_provider
        assert controller._finalization_handler.llm_provider is new_provider

    def test_skips_retry_orchestrator_when_no_attr(self, mock_llm_provider, mock_tool_runtime):
        """RetryOrchestrator 没有 llm_provider 属性，setter 应静默跳过不抛异常。"""
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider,
            tool_runtime=mock_tool_runtime,
            config=TransactionConfig(domain="code"),
        )
        new_provider = AsyncMock()
        # 不应抛出 AttributeError
        controller.llm_provider = new_provider
        assert controller.llm_provider is new_provider


class TestProtocolPanicHandoff:
    """验证 finalize 阶段 LLM 违反 tool_choice=none 时软守卫行为（丢弃幻觉 tool_calls）。"""

    @pytest.mark.asyncio
    async def test_finalize_tool_reentry_soft_guard_drops_tools(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """LLM 在 LLM_ONCE 收口阶段返回 tool_calls 被软守卫丢弃，正常完成。"""
        call_count = 0

        async def panic_provider(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 决策阶段：返回一个读工具
                return {
                    "content": "读取 main.py。",
                    "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                    "model": "claude",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30},
                }
            # 收口阶段：违反 tool_choice=none，返回 tool_calls（被软守卫丢弃）
            return {
                "content": "我再调用一个工具。",
                "tool_calls": [_native_tool_call("write_file", {"path": "out.py", "content": "x"})],
                "model": "claude",
                "usage": {"prompt_tokens": 200, "completion_tokens": 30},
            }

        controller = TurnTransactionController(
            llm_provider=panic_provider,
            tool_runtime=mock_tool_runtime,
            config=TransactionConfig(domain="code"),
        )
        mock_tool_runtime.return_value = {"success": True, "result": "content"}

        result = await controller.execute(
            turn_id="turn_panic", context=basic_context, tool_definitions=basic_tool_definitions
        )

        # Soft guard: dropped hallucinated tool calls, normal completion
        assert result["kind"] == "tool_batch_with_receipt"
        # No workflow handoff in soft-guard mode
        assert result.get("workflow_context") is None
        # 决策 + 收口 = 2 次 LLM 调用
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_finalize_tool_reentry_includes_receipts(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """软守卫下已执行工具的 receipts 仍保留在结果中。"""
        call_count = 0

        async def panic_provider(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "content": "读取 main.py。",
                    "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                    "model": "claude",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30},
                }
            return {
                "content": "我再调用一个工具。",
                "tool_calls": [_native_tool_call("write_file", {"path": "out.py", "content": "x"})],
                "model": "claude",
                "usage": {"prompt_tokens": 200, "completion_tokens": 30},
            }

        controller = TurnTransactionController(
            llm_provider=panic_provider,
            tool_runtime=mock_tool_runtime,
            config=TransactionConfig(domain="code"),
        )
        mock_tool_runtime.return_value = {"success": True, "result": "file content here"}

        result = await controller.execute(
            turn_id="turn_panic_receipts", context=basic_context, tool_definitions=basic_tool_definitions
        )

        assert result["kind"] == "tool_batch_with_receipt"
        # batch_receipt 应存在，因为决策阶段工具已执行
        assert result.get("batch_receipt") is not None
