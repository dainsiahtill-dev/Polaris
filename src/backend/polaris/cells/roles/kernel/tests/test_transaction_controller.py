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
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from polaris.cells.roles.kernel.internal.metrics import MetricsCollector
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryContract, DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.delivery_intent_resolver import (
    enforce_explicit_materialize_delivery_marker,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    WRITE_FILE_AUTOFILL_EVIDENCE_KEY,
    WRITE_FILE_DUPLICATE_REJECTION_KEY,
    ToolBatchExecutor,
    annotate_autofilled_write_receipts,
    diff_write_file_autofill_evidence,
    fill_content_only_write_file_from_remaining_targets,
    split_write_file_duplicate_content_rejections,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode, TurnDecision, TurnDecisionKind
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent
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


class TestExplicitDeliveryModeMarker:
    def test_materialize_marker_overrides_analyze_contract(self) -> None:
        contract = enforce_explicit_materialize_delivery_marker(
            "[mode:materialize]\nCreate worker_1.txt",
            DeliveryContract(
                mode=DeliveryMode.ANALYZE_ONLY,
                requires_mutation=False,
                requires_verification=False,
                allow_inline_code=True,
                allow_patch_proposal=False,
            ),
        )

        assert contract.mode == DeliveryMode.MATERIALIZE_CHANGES
        assert contract.requires_mutation is True
        assert contract.allow_patch_proposal is False

    def test_plain_message_keeps_existing_mode(self) -> None:
        original = DeliveryContract(
            mode=DeliveryMode.ANALYZE_ONLY,
            requires_mutation=False,
            requires_verification=False,
            allow_inline_code=True,
            allow_patch_proposal=False,
        )
        contract = enforce_explicit_materialize_delivery_marker("Analyze the architecture", original)

        assert contract is original
        assert contract.mode == DeliveryMode.ANALYZE_ONLY
        assert contract.requires_mutation is False


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
    async def test_no_tool_definitions_suppresses_decoded_tool_batch(
        self, mock_llm_provider, mock_tool_runtime
    ) -> None:
        mock_llm_provider.return_value = {
            "content": "# AGENTS.md\n\nProject guidance draft.",
            "tool_calls": [_native_tool_call("repo_tree", {"path": "."})],
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        }
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider,
            tool_runtime=mock_tool_runtime,
            config=TransactionConfig(domain="document"),
        )
        state_machine = TurnStateMachine(turn_id="turn_text_only")
        ledger = TurnLedger(turn_id="turn_text_only")

        result = await controller._execute_turn(
            turn_id="turn_text_only",
            context=[{"role": "user", "content": "Generate AGENTS.md content; do not call tools"}],
            tool_definitions=[],
            state_machine=state_machine,
            ledger=ledger,
            stream=False,
        )

        assert result["kind"] == "final_answer"
        assert "Project guidance draft" in result["visible_content"]
        assert mock_tool_runtime.call_count == 0
        assert any(flag.get("type") == "TEXT_ONLY_TOOL_BATCH_SUPPRESSED" for flag in ledger.anomaly_flags)

    @pytest.mark.asyncio
    async def test_native_tool_calls_that_never_decode_fail_closed(self, mock_llm_provider, mock_tool_runtime) -> None:
        invalid_tool_call = {
            "id": "call_bad_read",
            "type": "function",
            "function": {"name": "read_file", "arguments": "[]"},
        }
        mock_llm_provider.side_effect = [
            {
                "content": "",
                "tool_calls": [invalid_tool_call],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
            {
                "content": "",
                "tool_calls": [invalid_tool_call],
                "model": "claude",
                "usage": {"prompt_tokens": 120, "completion_tokens": 10},
            },
        ]
        controller = TurnTransactionController(
            llm_provider=mock_llm_provider,
            tool_runtime=mock_tool_runtime,
            config=TransactionConfig(domain="code"),
        )
        state_machine = TurnStateMachine(turn_id="turn_bad_native_tool")
        ledger = TurnLedger(turn_id="turn_bad_native_tool")

        with pytest.raises(RuntimeError, match="tool_dispatch_dropped"):
            await controller._execute_turn(
                turn_id="turn_bad_native_tool",
                context=[{"role": "user", "content": "Read main.py"}],
                tool_definitions=[{"type": "function", "function": {"name": "read_file"}}],
                state_machine=state_machine,
                ledger=ledger,
                stream=False,
            )

        assert mock_llm_provider.call_count == 2
        assert mock_tool_runtime.call_count == 0
        dropped_flags = [
            item
            for item in ledger.anomaly_flags
            if isinstance(item, dict) and item.get("type") == "TOOL_DISPATCH_DROPPED"
        ]
        assert len(dropped_flags) == 1
        assert dropped_flags[0]["provider_response_hash"]
        lifecycle = dropped_flags[0]["tool_call_lifecycle_receipt"]
        assert lifecycle["native_tool_calls_count"] == dropped_flags[0]["native_tool_calls_count"]
        assert lifecycle["provider_response_hash"] == dropped_flags[0]["provider_response_hash"]
        assert lifecycle["dispatch_status"] == "dropped"

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
    async def test_decoded_tool_batch_without_receipt_fails_closed(
        self,
        controller,
        mock_llm_provider,
        monkeypatch,
        basic_context,
        basic_tool_definitions,
    ) -> None:
        mock_llm_provider.return_value = {
            "content": "",
            "tool_calls": [
                _native_tool_call("read_file", {"path": "main.py"}, call_id="call_read_main"),
                _native_tool_call("read_file", {"path": "config.py"}, call_id="call_read_config"),
            ],
            "model": "claude",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 30,
                "native_tool_call_envelopes": [
                    {"envelope_id": "env-read-main", "tool_name": "read_file", "call_id": "call_read_main"},
                    {"envelope_id": "env-read-config", "tool_name": "read_file", "call_id": "call_read_config"},
                ],
            },
        }

        class EmptyBatchRuntime:
            async def execute_batch(self, *_args: Any, **_kwargs: Any) -> list[Any]:
                return []

        monkeypatch.setattr(
            controller._tool_batch_executor,
            "_build_tool_batch_runtime",
            lambda *_args, **_kwargs: EmptyBatchRuntime(),
        )
        state_machine = TurnStateMachine(turn_id="turn_missing_batch_receipt")
        ledger = TurnLedger(turn_id="turn_missing_batch_receipt")

        with pytest.raises(RuntimeError, match="tool_dispatch_dropped"):
            await controller._execute_turn(
                turn_id="turn_missing_batch_receipt",
                context=basic_context,
                tool_definitions=basic_tool_definitions,
                state_machine=state_machine,
                ledger=ledger,
                stream=False,
            )

        dropped_flags = [
            item
            for item in ledger.anomaly_flags
            if isinstance(item, dict) and item.get("type") == "TOOL_DISPATCH_DROPPED"
        ]
        assert len(dropped_flags) == 1
        assert dropped_flags[0]["native_tool_calls_count"] == 2
        assert dropped_flags[0]["decoded_tool_calls_count"] == 2
        assert dropped_flags[0]["dispatched_tool_calls_count"] == 0
        assert len(dropped_flags[0]["native_tool_call_envelopes"]) == 2
        assert dropped_flags[0]["dropped_tool_calls"][0]["tool_name"] == "read_file"
        lifecycle = dropped_flags[0]["tool_call_lifecycle_receipt"]
        assert lifecycle["native_tool_calls_count"] == dropped_flags[0]["native_tool_calls_count"]
        assert lifecycle["decoded_tool_calls_count"] == dropped_flags[0]["decoded_tool_calls_count"]
        assert lifecycle["dropped_tool_calls"] == dropped_flags[0]["dropped_tool_calls"]
        failure_evidence = dropped_flags[0]["failure_evidence"][0]
        assert failure_evidence["failure_class"] == "TOOL_DISPATCH_DROPPED"
        assert failure_evidence["responsible_layer"] == "execution_control_plane"
        assert failure_evidence["metadata"]["source"] == "tool_call_lifecycle_receipt.v1"
        assert failure_evidence["metadata"]["decoded_tool_calls_count"] == 2

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

        assert result["kind"] == "finalization_tool_calls_blocked"
        assert result["finalization"]["mode"] == "blocked"
        assert result["finalization"]["tool_calls_blocked"] is True
        assert result["finalization"]["workflow_reason"] == "finalization_tool_calls_blocked"
        assert result["finalization"]["blocked_tool_calls"] == [
            {
                "reason": "finalization_tool_calls_blocked",
                "tool_name": "bad_tool",
                "call_id": "call_violation",
            }
        ]


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
    async def test_unregistered_async_looking_tool_fails_closed(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """A tool absent from the captured narrowed set cannot manufacture a handoff."""
        mock_llm_provider.return_value = {
            "content": "提交 PR。",
            "tool_calls": [_native_tool_call("create_pull_request", {"title": "PR"})],
            "model": "claude",
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        }

        with pytest.raises(RuntimeError, match="outside narrowed set: create_pull_request"):
            await controller.execute(
                turn_id="turn_async", context=basic_context, tool_definitions=basic_tool_definitions
            )

        # An unregistered call must never reach the physical tool runtime.
        assert mock_tool_runtime.call_count == 0

    @pytest.mark.asyncio
    async def test_many_tools_go_through_tool_batch(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """大量纯读取工具走 TOOL_BATCH + LLM_ONCE，不再因数量多而 handoff。

        此前 6 个 read_file 会因 handoff_threshold_tools=5 被错误 handoff 到
        ExplorationWorkflowRuntime（synthesis_llm=None 导致无输出）。
        修复后纯读取批次始终走正常 TOOL_BATCH 流程。
        """
        mock_llm_provider.side_effect = [
            {
                "content": "需要读取多份文件。",
                "tool_calls": [
                    _native_tool_call("read_file", {"path": f"file{i}.py"}, call_id=f"call_{i}") for i in range(6)
                ],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
            {
                "content": "已读取并汇总多份文件。",
                "tool_calls": [],
                "model": "claude",
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
        ]

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
    async def test_tool_failure_fails_closed(
        self, controller, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """全失败工具批次必须停止 turn，不能进入 LLM_ONCE finalization。"""
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

        with pytest.raises(RuntimeError, match="tool_dispatch_failed"):
            await controller.execute(
                turn_id="turn_tool_fail", context=basic_context, tool_definitions=basic_tool_definitions
            )

        assert mock_llm_provider.await_count == 1


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
    """Verify finalization tool-call re-entry fails closed with prior receipts preserved."""

    @pytest.mark.asyncio
    async def test_finalize_tool_reentry_is_blocked(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """LLM_ONCE finalization tool calls become a blocked transaction result."""
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
            # 收口阶段：违反 tool_choice=none，返回 tool_calls（必须 fail-closed）
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

        assert result["kind"] == "finalization_tool_calls_blocked"
        assert result["finalization"]["tool_calls_blocked"] is True
        assert result["finalization"]["workflow_reason"] == "finalization_tool_calls_blocked"
        # 决策 + 收口 = 2 次 LLM 调用
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_finalize_native_tool_reentry_is_blocked(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """Finalization must block provider-native tool calls even without the tool_calls alias."""
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
                "native_tool_calls": [_native_tool_call("write_file", {"path": "out.py", "content": "x"})],
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
            turn_id="turn_native_panic", context=basic_context, tool_definitions=basic_tool_definitions
        )

        assert result["kind"] == "finalization_tool_calls_blocked"
        assert result["finalization"]["tool_calls_blocked"] is True
        assert result["finalization"]["tool_names"] == ["write_file"]
        assert result["finalization"]["workflow_reason"] == "finalization_tool_calls_blocked"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_finalize_tool_reentry_includes_receipts(
        self, mock_llm_provider, mock_tool_runtime, basic_context, basic_tool_definitions
    ) -> None:
        """Finalization tool re-entry is blocked while preserving prior receipts."""
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

        assert result["kind"] == "finalization_tool_calls_blocked"
        # batch_receipt 应存在，因为决策阶段工具已执行
        assert result.get("batch_receipt") is not None


# ============ Test write_file target autofill guards ============


def _decoded_state_machine(turn_id: str) -> TurnStateMachine:
    state_machine = TurnStateMachine(turn_id=turn_id)
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    return state_machine


class TestWriteFileTargetAutofillGuards:
    """fill_content_only_write_file_from_remaining_targets 的 fail-closed 守卫。

    回归背景：弱模型重发一个内容属于已认领目标、但漏掉 file 参数的重复
    write_file 时，旧实现会把它猜测性填到唯一剩余的 contract target 上，
    以错误内容静默覆盖那个文件。修复后：重复内容 → 结构化 teaching error
    （不写任何文件）；真正的单目标填充仍然工作并携带 autofill 审计证据。
    """

    def test_duplicate_content_fileless_retry_is_rejected_not_filled(self) -> None:
        """(a) 重复内容的 file-less retry 不得被猜到剩余目标上。"""
        invocations: list[Any] = [
            {
                "call_id": "write_app",
                "tool_name": "write_file",
                "arguments": {"file": "src/app.py", "content": "print('app')\n"},
            },
            {
                "call_id": "write_retry",
                "tool_name": "write_file",
                "arguments": {"content": "print('app')\n"},
            },
        ]

        filled = fill_content_only_write_file_from_remaining_targets(
            invocations,
            target_files=("src/app.py", "README.md"),
        )

        # 不得把重复内容填到 README.md
        assert "file" not in filled[1]["arguments"]
        rejection = filled[1][WRITE_FILE_DUPLICATE_REJECTION_KEY]
        assert rejection["duplicate_of"] == "src/app.py"

        dispatchable, rejections = split_write_file_duplicate_content_rejections(filled)
        assert [inv["call_id"] for inv in dispatchable] == ["write_app"]
        assert len(rejections) == 1
        assert rejections[0]["call_id"] == "write_retry"
        assert rejections[0]["status"] == "error"
        assert "duplicate_content_write_rejected" in str(rejections[0]["error"])
        assert "src/app.py" in str(rejections[0]["error"])

    def test_duplicate_detection_survives_trivial_whitespace_differences(self) -> None:
        """CRLF/行尾空格/首尾空行差异不影响重复判定。"""
        invocations: list[Any] = [
            {
                "call_id": "write_app",
                "tool_name": "write_file",
                "arguments": {"file": "src/app.py", "content": "def main():\n    pass\n"},
            },
            {
                "call_id": "write_retry",
                "tool_name": "write_file",
                "arguments": {"content": "def main():\r\n    pass  \r\n\r\n"},
            },
        ]

        filled = fill_content_only_write_file_from_remaining_targets(
            invocations,
            target_files=("src/app.py", "README.md"),
        )

        assert "file" not in filled[1]["arguments"]
        assert filled[1][WRITE_FILE_DUPLICATE_REJECTION_KEY]["duplicate_of"] == "src/app.py"

    def test_legitimate_single_fill_still_fills_and_records_evidence(self) -> None:
        """(b) 合法单一剩余目标仍然填充，且证据可注入 receipt。"""
        invocations: list[Any] = [
            {
                "call_id": "write_app",
                "tool_name": "write_file",
                "arguments": {"file": "src/app.py", "content": "print('app')\n"},
            },
            {
                "call_id": "write_readme",
                "tool_name": "write_file",
                "arguments": {"content": "# README\n"},
            },
        ]

        filled = fill_content_only_write_file_from_remaining_targets(
            invocations,
            target_files=("src/app.py", "README.md"),
        )

        assert filled[1]["arguments"]["file"] == "README.md"
        assert filled[1]["arguments"]["content"] == "# README\n"

        dispatchable, rejections = split_write_file_duplicate_content_rejections(filled)
        assert rejections == []
        assert len(dispatchable) == 2

        evidence_map = diff_write_file_autofill_evidence(invocations, dispatchable)
        assert evidence_map == {
            "write_readme": {
                "assigned_path": "README.md",
                "basis": "sole_remaining_contract_target",
            }
        }

        receipts: list[dict[str, Any]] = [
            {
                "batch_id": "batch_fill",
                "turn_id": "turn_fill",
                "results": [
                    {"call_id": "write_app", "tool_name": "write_file", "status": "success"},
                    {"call_id": "write_readme", "tool_name": "write_file", "status": "success"},
                ],
                "raw_results": [],
                "success_count": 2,
                "failure_count": 0,
            }
        ]
        annotate_autofilled_write_receipts(receipts, evidence_map)
        assert WRITE_FILE_AUTOFILL_EVIDENCE_KEY not in receipts[0]["results"][0]
        assert receipts[0]["results"][1][WRITE_FILE_AUTOFILL_EVIDENCE_KEY] == {
            "assigned_path": "README.md",
            "basis": "sole_remaining_contract_target",
        }

    def test_no_fill_when_zero_remaining_targets(self) -> None:
        """(c) 所有 contract target 已被认领时不填充、不拒绝（新内容非重复）。"""
        invocations: list[Any] = [
            {
                "call_id": "write_readme",
                "tool_name": "write_file",
                "arguments": {"file": "README.md", "content": "# README\n"},
            },
            {
                "call_id": "write_orphan",
                "tool_name": "write_file",
                "arguments": {"content": "print('other')\n"},
            },
        ]

        filled = fill_content_only_write_file_from_remaining_targets(
            invocations,
            target_files=("README.md",),
        )

        assert "file" not in filled[1]["arguments"]
        assert WRITE_FILE_DUPLICATE_REJECTION_KEY not in filled[1]
        assert WRITE_FILE_AUTOFILL_EVIDENCE_KEY not in filled[1]

    def test_no_fill_when_multiple_remaining_targets(self) -> None:
        """(c) 多个剩余目标保持 fail-closed：不填充、不加标记。"""
        invocations: list[Any] = [
            {
                "call_id": "write_unknown",
                "tool_name": "write_file",
                "arguments": {"content": "# Project\n"},
            }
        ]

        filled = fill_content_only_write_file_from_remaining_targets(
            invocations,
            target_files=("README.md", "CHANGELOG.md"),
        )

        assert "file" not in filled[0]["arguments"]
        assert WRITE_FILE_DUPLICATE_REJECTION_KEY not in filled[0]
        assert WRITE_FILE_AUTOFILL_EVIDENCE_KEY not in filled[0]

    @pytest.mark.asyncio
    async def test_execute_tool_batch_rejects_duplicate_and_preserves_original_receipt(self, tmp_path: Path) -> None:
        """端到端：重复 write 不 dispatch，teaching error 进 receipt，原写保留。"""
        tool_runtime = AsyncMock(
            return_value={
                "success": True,
                "result": "written",
                "effect_receipt": {"file": "src/app.py", "operation": "create"},
            }
        )
        executor = ToolBatchExecutor(
            tool_runtime=tool_runtime,
            config=TransactionConfig(mutation_guard_mode="warn"),
            emit_event=lambda event: None,
            guard_assert_single_tool_batch=lambda **kw: None,
            finalization_handler=AsyncMock(),
            handoff_handler=AsyncMock(),
        )
        turn_id = "turn_duplicate_write_reject"
        ledger = TurnLedger(turn_id=turn_id)
        ledger.modification_contract.target_files = ["src/app.py", "README.md"]
        decision = cast(
            TurnDecision,
            {
                "turn_id": turn_id,
                "metadata": {"workspace": str(tmp_path)},
                "finalize_mode": "none",
                "tool_batch": {
                    "batch_id": "batch_duplicate_write",
                    "invocations": [
                        {
                            "call_id": "call_original",
                            "tool_name": "write_file",
                            "arguments": {"file": "src/app.py", "content": "print('app')\n"},
                            "execution_mode": "write_serial",
                            "effect_type": "write",
                        },
                        {
                            "call_id": "call_duplicate",
                            "tool_name": "write_file",
                            "arguments": {"content": "print('app')\n"},
                            "execution_mode": "write_serial",
                            "effect_type": "write",
                        },
                    ],
                },
            },
        )
        context = [{"role": "user", "content": "Write src/app.py and README.md"}]

        result = await executor.execute_tool_batch(
            decision,
            _decoded_state_machine(turn_id),
            ledger,
            context,
            stream=False,
        )

        # 只有原始写被 dispatch；重复写绝不能落到 README.md 上
        assert tool_runtime.await_count == 1
        assert tool_runtime.await_args is not None
        dispatched_args = tool_runtime.await_args.args
        assert dispatched_args[0] == "write_file"
        assert dispatched_args[1].get("file") == "src/app.py"

        batch_receipt = result["batch_receipt"]
        assert batch_receipt is not None
        results_by_call_id = {
            str(item.get("call_id")): item for item in batch_receipt.get("results", []) if isinstance(item, dict)
        }
        # 原始写 receipt 保留为成功
        assert results_by_call_id["call_original"]["status"] == "success"
        # 重复写以结构化 teaching error 呈现，且没有 effect
        duplicate_result = results_by_call_id["call_duplicate"]
        assert duplicate_result["status"] == "error"
        assert "duplicate_content_write_rejected" in str(duplicate_result.get("error"))
        assert duplicate_result.get("effect_receipt") is None
        assert duplicate_result[WRITE_FILE_DUPLICATE_REJECTION_KEY]["duplicate_of"] == "src/app.py"

        # ledger 留下可审计 anomaly flag
        rejection_flags = [
            item
            for item in ledger.anomaly_flags
            if isinstance(item, dict) and item.get("type") == "WRITE_FILE_DUPLICATE_CONTENT_REJECTED"
        ]
        assert len(rejection_flags) == 1
        assert rejection_flags[0]["rejected_call_ids"] == ["call_duplicate"]

    @pytest.mark.asyncio
    async def test_execute_tool_batch_autofill_receipt_carries_evidence(self, tmp_path: Path) -> None:
        """端到端：合法单目标填充照常 dispatch，且 receipt 带 autofill 证据。"""
        tool_runtime = AsyncMock(
            return_value={
                "success": True,
                "result": "written",
                "effect_receipt": {"operation": "create"},
            }
        )
        executor = ToolBatchExecutor(
            tool_runtime=tool_runtime,
            config=TransactionConfig(mutation_guard_mode="warn"),
            emit_event=lambda event: None,
            guard_assert_single_tool_batch=lambda **kw: None,
            finalization_handler=AsyncMock(),
            handoff_handler=AsyncMock(),
        )
        turn_id = "turn_autofill_write_evidence"
        ledger = TurnLedger(turn_id=turn_id)
        ledger.modification_contract.target_files = ["src/app.py", "README.md"]
        decision = cast(
            TurnDecision,
            {
                "turn_id": turn_id,
                "metadata": {"workspace": str(tmp_path)},
                "finalize_mode": "none",
                "tool_batch": {
                    "batch_id": "batch_autofill_write",
                    "invocations": [
                        {
                            "call_id": "call_app",
                            "tool_name": "write_file",
                            "arguments": {"file": "src/app.py", "content": "print('app')\n"},
                            "execution_mode": "write_serial",
                            "effect_type": "write",
                        },
                        {
                            "call_id": "call_readme",
                            "tool_name": "write_file",
                            "arguments": {"content": "# README\n"},
                            "execution_mode": "write_serial",
                            "effect_type": "write",
                        },
                    ],
                },
            },
        )
        context = [{"role": "user", "content": "Write src/app.py and README.md"}]

        result = await executor.execute_tool_batch(
            decision,
            _decoded_state_machine(turn_id),
            ledger,
            context,
            stream=False,
        )

        # 两个写都被 dispatch，file-less 写被填到唯一剩余目标 README.md
        assert tool_runtime.await_count == 2
        dispatched_files = [call.args[1].get("file") for call in tool_runtime.await_args_list]
        assert dispatched_files == ["src/app.py", "README.md"]

        batch_receipt = result["batch_receipt"]
        assert batch_receipt is not None
        results_by_call_id = {
            str(item.get("call_id")): item for item in batch_receipt.get("results", []) if isinstance(item, dict)
        }
        assert results_by_call_id["call_readme"]["status"] == "success"
        assert results_by_call_id["call_readme"][WRITE_FILE_AUTOFILL_EVIDENCE_KEY] == {
            "assigned_path": "README.md",
            "basis": "sole_remaining_contract_target",
        }
        # 模型显式给出 file 的写不携带 autofill 证据
        assert WRITE_FILE_AUTOFILL_EVIDENCE_KEY not in results_by_call_id["call_app"]
