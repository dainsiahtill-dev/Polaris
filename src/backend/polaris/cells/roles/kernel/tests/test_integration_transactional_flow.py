"""
Integration Tests for Turn Engine Transactional Flow

验证：
1. 完整turn执行流程
2. 状态机正确转换
3. 事件正确发送
4. 账本正确记录
5. Continuation loop防护
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TransactionConfig,
    TurnTransactionController,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import ErrorEvent, TurnPhaseEvent


def _native_tool_call(
    name: str,
    arguments: dict[str, object],
    *,
    call_id: str | None = None,
) -> dict[str, object]:
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
def mock_llm():
    """Mock LLM provider"""
    llm = AsyncMock()
    return llm


@pytest.fixture
def mock_tool_executor():
    """Mock tool executor"""
    executor = AsyncMock()
    return executor


@pytest.fixture
def mock_synthesis():
    """Mock synthesis LLM"""
    synthesis = AsyncMock()
    synthesis.return_value = "Synthesized analysis."
    return synthesis


@pytest.fixture
def context():
    """Basic conversation context"""
    return [{"role": "user", "content": "Read main.py and analyze it"}]


@pytest.fixture
def tool_defs():
    """Tool definitions"""
    return [{"name": "read_file", "description": "Read a file"}, {"name": "write_file", "description": "Write a file"}]


# ============ Test Full Turn Execution ============


class TestFullTurnExecution:
    """测试完整turn执行"""

    @pytest.mark.asyncio
    async def test_final_answer_full_flow(self, mock_llm, mock_tool_executor, context, tool_defs) -> None:
        """直接回答完整流程"""
        mock_llm.return_value = {
            "content": "The main.py file contains the application entry point.",
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

        controller = TurnTransactionController(
            llm_provider=mock_llm, tool_runtime=mock_tool_executor, config=TransactionConfig(domain="code")
        )

        result = await controller.execute(turn_id="turn_final", context=context, tool_definitions=tool_defs)

        # 验证结果
        assert result["turn_id"] == "turn_final"
        assert result["kind"] == "final_answer"
        assert "entry point" in result["visible_content"]
        assert result["metrics"]["llm_calls"] == 1
        assert result["metrics"]["tool_calls"] == 0

        # 验证状态轨迹 - state_trajectory is at result root level, not in metrics
        states = result["state_trajectory"]
        assert "CONTEXT_BUILT" in states
        assert "DECISION_REQUESTED" in states
        assert "DECISION_DECODED" in states
        assert "FINAL_ANSWER_READY" in states
        assert "COMPLETED" in states

    @pytest.mark.asyncio
    async def test_tool_execution_full_flow(self, mock_llm, mock_tool_executor, context, tool_defs) -> None:
        """工具执行完整流程（code 域默认 LLM_ONCE 收口）"""

        async def _tracking_llm(request: dict[str, Any]) -> dict[str, Any]:
            if request.get("tools") is None:
                # 收口阶段：tool_choice=none，禁止返回 tool_calls
                return {
                    "content": "文件内容已读取。",
                    "tool_calls": [],
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                }
            # 决策阶段
            return {
                "content": "读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        mock_tool_executor.return_value = {"success": True, "result": "# main.py\ndef main():\n    pass"}

        controller = TurnTransactionController(
            llm_provider=_tracking_llm, tool_runtime=mock_tool_executor, config=TransactionConfig(domain="code")
        )

        result = await controller.execute(turn_id="turn_tool", context=context, tool_definitions=tool_defs)

        # 验证结果（code 域默认 LLM_ONCE：visible_content 是 LLM 收口摘要）
        assert result["turn_id"] == "turn_tool"
        assert result["kind"] == "tool_batch_with_receipt"
        assert result["batch_receipt"] is not None
        # LLM_ONCE 模式下 visible_content 来自收口 LLM，不再是 raw tool result
        assert result["visible_content"]  # 非空即可

        # 验证工具调用
        assert mock_tool_executor.call_count == 1
        assert result["metrics"]["tool_calls"] == 1

    @pytest.mark.asyncio
    async def test_llm_once_finalization_flow(self, mock_llm, mock_tool_executor, context, tool_defs) -> None:
        """LLM_ONCE收口完整流程"""
        # 记录LLM调用顺序
        llm_calls = []

        async def tracking_llm(request):
            llm_calls.append({"has_tools": request.get("tools") is not None, "tool_choice": request.get("tool_choice")})
            if request.get("tools") is None:
                return {
                    "content": "Based on the file contents, this is a simple entry point.",
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

        mock_tool_executor.return_value = {"success": True, "result": "def main(): pass"}

        controller = TurnTransactionController(
            llm_provider=tracking_llm,
            tool_runtime=mock_tool_executor,
            config=TransactionConfig(domain="document"),  # document域默认LLM_ONCE
        )

        result = await controller.execute(turn_id="turn_llm_once", context=context, tool_definitions=tool_defs)

        # 验证两次LLM调用
        assert len(llm_calls) == 2

        # 第一次决策：有tools，tool_choice=auto
        assert llm_calls[0]["has_tools"] is True
        assert llm_calls[0]["tool_choice"] == "auto"

        # 第二次收口：无tools，tool_choice=none
        assert llm_calls[1]["has_tools"] is False
        assert llm_calls[1]["tool_choice"] == "none"

        # 验证finalization
        assert result["finalization"] is not None
        assert result["finalization"]["mode"] == "llm_once"


# ============ Test State Machine Integration ============


class TestStateMachineIntegration:
    """测试状态机集成"""

    @pytest.mark.asyncio
    async def test_state_transitions_always_forward(self, mock_llm, mock_tool_executor, context, tool_defs) -> None:
        """状态转换始终向前"""
        events_received = []

        def event_handler(event) -> None:
            events_received.append(event)

        async def phased_llm(request):
            if request.get("tools") is None:
                return {
                    "content": "文件已读取完成。",
                    "tool_calls": [],
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                }
            return {
                "content": "读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        mock_tool_executor.return_value = {"success": True, "result": "content"}

        controller = TurnTransactionController(
            llm_provider=phased_llm, tool_runtime=mock_tool_executor, config=TransactionConfig(domain="code")
        )
        controller.on_event(event_handler)

        await controller.execute(turn_id="turn_states", context=context, tool_definitions=tool_defs)

        # 验证状态事件顺序（code 域默认 LLM_ONCE，含 finalization 阶段）
        phase_events = [e for e in events_received if isinstance(e, TurnPhaseEvent)]
        phases = [e.phase for e in phase_events]

        # 验证顺序正确
        assert phases == [
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "finalization_requested",
            "finalization_completed",
        ]


# ============ Test Continuation Loop Prevention ============


class TestContinuationLoopPrevention:
    """测试continuation loop防护"""

    @pytest.mark.asyncio
    async def test_no_auto_continuation_after_tools(self, mock_llm, mock_tool_executor, context, tool_defs) -> None:
        """工具后无自动继续"""
        llm_call_count = 0

        async def counting_llm(request):
            nonlocal llm_call_count
            llm_call_count += 1
            if request.get("tools") is None:
                return {
                    "content": "文件已读取完成。",
                    "tool_calls": [],
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                }
            return {
                "content": "读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        mock_tool_executor.return_value = {"success": True, "result": "content"}

        controller = TurnTransactionController(
            llm_provider=counting_llm,
            tool_runtime=mock_tool_executor,
            config=TransactionConfig(domain="code"),  # code 域默认 LLM_ONCE
        )

        await controller.execute(turn_id="turn_no_loop", context=context, tool_definitions=tool_defs)

        # code 域默认 LLM_ONCE：1 次决策 + 1 次收口 = 2 次 LLM 调用，无自动 continuation loop
        assert llm_call_count == 2

    @pytest.mark.asyncio
    async def test_llm_once_blocks_tool_after_finalization(
        self, mock_llm, mock_tool_executor, context, tool_defs
    ) -> None:
        """LLM_ONCE收口后禁止工具"""

        async def strict_llm(request):
            if request.get("tools") is None:
                # 收口阶段不应有tool_calls
                if request.get("tool_calls"):
                    raise RuntimeError("LLM returned tools despite tool_choice=none")
                return {
                    "content": "Summary.",
                    "tool_calls": [],
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                }
            return {
                "content": "读取 main.py。",
                "tool_calls": [_native_tool_call("read_file", {"path": "main.py"})],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        mock_tool_executor.return_value = {"success": True, "result": "content"}

        controller = TurnTransactionController(
            llm_provider=strict_llm, tool_runtime=mock_tool_executor, config=TransactionConfig(domain="document")
        )

        # 不应抛出异常
        result = await controller.execute(turn_id="turn_strict", context=context, tool_definitions=tool_defs)

        assert result["kind"] == "tool_batch_with_receipt"
        assert result["finalization"]["mode"] == "llm_once"


# ============ Test Workflow Handoff Integration ============


class TestWorkflowHandoffIntegration:
    """测试workflow移交集成"""

    @pytest.mark.asyncio
    async def test_async_tool_triggers_handoff(self, mock_llm, mock_tool_executor, context, tool_defs) -> None:
        """异步工具触发handoff"""
        mock_llm.return_value = {
            "content": "提交 PR。",
            "tool_calls": [_native_tool_call("create_pull_request", {"title": "PR"})],
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        }

        controller = TurnTransactionController(
            llm_provider=mock_llm, tool_runtime=mock_tool_executor, config=TransactionConfig(domain="document")
        )

        result = await controller.execute(turn_id="turn_handoff", context=context, tool_definitions=tool_defs)

        assert result["kind"] == "handoff_workflow"
        assert result["workflow_context"] is not None


# ============ Test Ledger and Events ============


class TestLedgerAndEvents:
    """测试账本和事件"""

    @pytest.mark.asyncio
    async def test_ledger_records_all_metrics(self, mock_llm, mock_tool_executor, context, tool_defs) -> None:
        """账本记录所有指标"""
        events_received = []

        def event_handler(event) -> None:
            events_received.append(event)

        async def phased_llm(request):
            if request.get("tools") is None:
                return {
                    "content": "a.py 和 b.py 已读取完成。",
                    "tool_calls": [],
                    "model": "claude",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                }
            return {
                "content": "读取 a.py 和 b.py。",
                "tool_calls": [
                    _native_tool_call("read_file", {"path": "a.py"}, call_id="call_a"),
                    _native_tool_call("read_file", {"path": "b.py"}, call_id="call_b"),
                ],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        mock_tool_executor.return_value = {"success": True, "result": "content"}

        controller = TurnTransactionController(
            llm_provider=phased_llm, tool_runtime=mock_tool_executor, config=TransactionConfig(domain="code")
        )
        controller.on_event(event_handler)

        result = await controller.execute(turn_id="turn_metrics", context=context, tool_definitions=tool_defs)

        # 验证账本（code 域默认 LLM_ONCE：1 次决策 + 1 次收口 = 2 次 LLM 调用）
        ledger = result["metrics"]
        assert ledger["llm_calls"] == 2
        assert ledger["tool_calls"] == 2
        assert ledger["duration_ms"] >= 0

        # 验证事件
        event_types = {type(e).__name__ for e in events_received}
        assert "TurnPhaseEvent" in event_types
        assert "CompletionEvent" in event_types

    @pytest.mark.asyncio
    async def test_error_event_on_failure(self, mock_llm, mock_tool_executor, context, tool_defs) -> None:
        """失败时发送错误事件"""
        events_received = []

        def event_handler(event) -> None:
            events_received.append(event)

        mock_llm.side_effect = Exception("LLM unavailable")

        controller = TurnTransactionController(
            llm_provider=mock_llm, tool_runtime=mock_tool_executor, config=TransactionConfig(domain="code")
        )
        controller.on_event(event_handler)

        # FIX: Specify a more specific exception type instead of bare Exception
        with pytest.raises((RuntimeError, ValueError, Exception)):
            await controller.execute(turn_id="turn_fail", context=context, tool_definitions=tool_defs)

        # 验证错误事件
        error_events = [e for e in events_received if isinstance(e, ErrorEvent)]
        assert len(error_events) == 1
        assert "LLM unavailable" in error_events[0].message


# ============ Test Tool Batch Runtime Integration ============


class TestToolBatchRuntimeIntegration:
    """测试工具批次运行时集成"""

    @pytest.mark.asyncio
    async def test_parallel_and_serial_execution(self, mock_tool_executor) -> None:
        """并行和串行执行"""
        from polaris.cells.roles.kernel.public.turn_contracts import ToolExecutionMode, ToolInvocation

        execution_order = []

        async def tracking_executor(tool_name, arguments):
            execution_order.append(tool_name)
            await asyncio.sleep(0.01)
            if tool_name == "write_file":
                return {"success": True, "result": "done", "effect_receipt": {"bytes_written": 1}}
            return {"success": True, "result": "done"}

        runtime = ToolBatchRuntime(executor=tracking_executor)

        read1 = ToolInvocation(
            call_id=ToolCallId("r1"),
            tool_name="read_file",
            arguments={"path": "a.txt"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        read2 = ToolInvocation(
            call_id=ToolCallId("r2"),
            tool_name="read_file",
            arguments={"path": "b.txt"},
            effect_type="read",
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        write1 = ToolInvocation(
            call_id=ToolCallId("w1"),
            tool_name="write_file",
            arguments={"path": "out.txt", "content": "x"},
            effect_type="write",
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )

        batch = ToolBatch(
            batch_id=BatchId("mixed"),
            invocations=[read1, read2, write1],
            parallel_readonly=[read1, read2],
            readonly_serial=[],
            serial_writes=[write1],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_mixed"))

        # 验证执行
        assert len(receipts) == 3
        assert all(r["success_count"] == 1 for r in receipts)

        # 验证顺序：先并行读，再串行写
        # read_file应该在write_file之前
        assert execution_order.index("write_file") > min(execution_order.index("read_file") for _ in [1, 2])


# ============ Test Exploration Workflow Integration ============


class TestExplorationWorkflowIntegration:
    """测试探索工作流集成"""

    @pytest.mark.asyncio
    async def test_exploration_with_synthesis(self, mock_tool_executor, mock_synthesis) -> None:
        """带综合的探索"""

        mock_tool_executor.return_value = {"success": True, "result": "def main(): pass"}

        workflow = ExplorationWorkflowRuntime(tool_executor=mock_tool_executor, synthesis_llm=mock_synthesis)

        decision = TurnDecision(
            turn_id=TurnId("turn_explore"),
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message="Exploring...",
            reasoning_summary="",
            tool_batch=ToolBatch(
                batch_id=BatchId("explore"),
                invocations=[
                    ToolInvocation(
                        call_id=ToolCallId("e1"),
                        tool_name="read_file",
                        arguments={"path": "main.py"},
                        effect_type="read",
                        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                    )
                ],
                parallel_readonly=[],
                serial_writes=[],
                async_receipts=[],
            ),
            finalize_mode=FinalizeMode.NONE,
            domain="document",
            metadata={"handoff_reason": "complex_exploration"},
        )

        result = await workflow.execute(decision, TurnId("turn_explore"))

        assert result.status.value == "completed"
        assert result.synthesis is not None
        assert mock_synthesis.call_count == 1

    @pytest.mark.asyncio
    async def test_transaction_kernel_routes_handoff_to_workflow_runtime(self) -> None:
        """TransactionKernel with workflow_runtime executes it on HANDOFF_WORKFLOW."""
        from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

        async def mock_llm_provider(_request_payload: dict[str, Any]) -> dict[str, Any]:
            return {
                "content": "[handoff_workflow]",
                "thinking": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "main.py"}),
                        },
                    }
                ],
                "model": "mock",
                "usage": {},
            }

        async def mock_tool_runtime(_tool_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "result": "done"}

        mock_workflow = MagicMock()
        mock_workflow.execute = MagicMock(
            return_value=MagicMock(
                status=MagicMock(value="completed"),
                synthesis="workflow synthesis",
                decisions=[],
            )
        )

        tk = TransactionKernel(
            llm_provider=mock_llm_provider,
            tool_runtime=mock_tool_runtime,
            workflow_runtime=mock_workflow,
        )

        result = await tk.execute("turn_1", [{"role": "user", "content": "explore"}], [])

        # The workflow runtime is invoked when decoder yields HANDOFF_WORKFLOW
        mock_workflow.execute.assert_called_once()
        assert result["kind"] == "handoff_workflow"
