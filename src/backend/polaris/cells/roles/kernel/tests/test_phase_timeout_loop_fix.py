"""回归测试 — FIX-20250422-v2: 阶段超时无限循环修复。

根因分析：
1. _check_intent_mismatch 在 PHASE_TIMEOUT 时返回 True（语义反转），
   导致 finalization 被阻断，触发 continue_multi_turn 而非终止。
2. tool_batch_count 每 turn 重置为 0，使宽限期检查 (<= 2) 永远通过。
3. CONTENT_GATHERED 阶段缺少预执行硬阻断，LLM 忽略后置错误 receipt。

本文件覆盖全部三个根因的回归场景。
"""

from __future__ import annotations

from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.phase_manager import Phase, PhaseManager
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import ErrorEvent


@pytest.fixture
def mock_emit_event() -> Any:
    return lambda event: None


@pytest.fixture
def mock_guard_assert() -> Any:
    return lambda **kw: None


def _make_executor(
    mock_emit_event: Any,
    mock_guard_assert: Any,
    *,
    guard_mode: Literal["strict", "warn", "off"] = "warn",
) -> ToolBatchExecutor:
    return ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode=guard_mode),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )


def _make_state_machine(turn_id: str) -> TurnStateMachine:
    sm = TurnStateMachine(turn_id=turn_id)
    sm.transition_to(TurnState.CONTEXT_BUILT)
    sm.transition_to(TurnState.DECISION_REQUESTED)
    sm.transition_to(TurnState.DECISION_RECEIVED)
    sm.transition_to(TurnState.DECISION_DECODED)
    return sm


def _make_ledger_in_content_gathered(
    turn_id: str,
    *,
    turns_in_phase: int = 3,
    materialize: bool = True,
) -> TurnLedger:
    """创建一个处于 CONTENT_GATHERED 阶段的 ledger（模拟多 turn 持久化）。"""
    ledger = TurnLedger(turn_id=turn_id)
    if materialize:
        ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    # 模拟 PhaseManager 已跨 turn 持久化到 CONTENT_GATHERED 阶段
    ledger.phase_manager._current_phase = Phase.CONTENT_GATHERED
    ledger.phase_manager._turns_in_current_phase = turns_in_phase
    return ledger


def _make_tool_batch_decision(
    turn_id: str,
    batch_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> TurnDecision:
    """构造用于测试的 TurnDecision（TOOL_BATCH 类型）。"""
    invocation = ToolInvocation(
        call_id=ToolCallId(f"call_{tool_name}"),
        tool_name=tool_name,
        arguments=arguments or {},
        effect_type=ToolEffectType.READ if tool_name in {"read_file", "glob", "repo_rg"} else ToolEffectType.WRITE,
        execution_mode=(
            ToolExecutionMode.READONLY_PARALLEL
            if tool_name in {"read_file", "glob", "repo_rg"}
            else ToolExecutionMode.WRITE_SERIAL
        ),
    )
    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata={"workspace": "."},
        tool_batch=ToolBatch(
            batch_id=BatchId(batch_id),
            invocations=[invocation],
        ),
    )


# ---------------------------------------------------------------------------
# Bug 1: _check_intent_mismatch PHASE_TIMEOUT 语义反转
# ---------------------------------------------------------------------------


class TestCheckIntentMismatchPhaseTimeout:
    """验证 PHASE_TIMEOUT 时 _check_intent_mismatch 返回 False（允许 finalization）。"""

    def test_phase_timeout_allows_finalization(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """PHASE_TIMEOUT 设置后，_check_intent_mismatch 必须返回 False。

        False = "不阻断 LLM_ONCE finalization" → turn 正常结束。
        旧代码返回 True（阻断 finalization → 无限 continue_multi_turn 循环）。
        """
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        ledger = TurnLedger(turn_id="turn_phase_timeout")
        ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
        ledger.mutation_obligation.mark_blocked(
            BlockedReason.PHASE_TIMEOUT,
            detail="test: phase timeout after 4 turns in content_gathered",
        )

        result = executor._check_intent_mismatch(
            ledger,
            [{"call_id": "c1", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
            "进一步完善 Session Orchestrator 相关代码",
        )

        assert result is False, (
            "_check_intent_mismatch must return False when PHASE_TIMEOUT is set, "
            "allowing LLM_ONCE finalization to break the loop"
        )

    def test_should_block_returns_false_on_phase_timeout(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """组合检查：_should_block_llm_once_finalization 在 PHASE_TIMEOUT 时返回 False。

        这是 _check_materialize_contract + _check_intent_mismatch 的 OR 结果。
        两者都应返回 False → 不阻断 finalization。
        """
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        ledger = TurnLedger(turn_id="turn_combined_timeout")
        ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
        ledger.mutation_obligation.mark_blocked(
            BlockedReason.PHASE_TIMEOUT,
            detail="test: combined check",
        )

        result = executor._should_block_llm_once_finalization(
            ledger,
            [{"call_id": "c1", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
            "进一步完善代码",
        )

        assert result is False, "_should_block_llm_once_finalization must return False when PHASE_TIMEOUT is set"

    def test_no_phase_timeout_still_blocks_when_expected(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """无 PHASE_TIMEOUT 且 mutation 未满足时，应正常阻断 finalization。"""
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        ledger = TurnLedger(turn_id="turn_normal_block")
        ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
        # 不设 PHASE_TIMEOUT，mutation 未满足

        result = executor._check_materialize_contract(
            ledger,
            [{"call_id": "c1", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
        )

        assert result is True, (
            "Without PHASE_TIMEOUT and no write tools, _check_materialize_contract should block finalization"
        )


# ---------------------------------------------------------------------------
# Bug 2: tool_batch_count 每 turn 重置 — 宽限期永远不过期
# ---------------------------------------------------------------------------


class TestSessionLevelPhaseCounter:
    """验证使用 session 级 PhaseManager 计数器替代 per-turn tool_batch_count。"""

    def test_intent_mismatch_uses_session_turns_in_phase(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """当 session 级 turns_in_phase > 2 时，不应再允许探索。

        旧代码用 per-turn tool_batch_count（总是 0-1），宽限期永远不过期。
        新代码用 PhaseManager._turns_in_current_phase（跨 turn 持久化）。
        """
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        ledger = TurnLedger(turn_id="turn_session_counter")
        ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.ANALYZE_ONLY, requires_mutation=False))
        # 模拟已在某阶段停留 4 个 turn（远超宽限期 2）
        ledger.phase_manager._turns_in_current_phase = 4

        result = executor._check_intent_mismatch(
            ledger,
            [{"call_id": "c1", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
            "请修改这个文件的代码",
        )

        # turns_in_phase=4 > 2 → 不应允许继续探索 → 应阻断
        assert result is True, (
            "When session_turns_in_phase > 2, _check_intent_mismatch should block "
            "(return True) to prevent infinite exploration"
        )

    def test_intent_mismatch_allows_early_exploration(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """当 session 级 turns_in_phase <= 2 时，应允许探索。"""
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        ledger = TurnLedger(turn_id="turn_early_exploration")
        ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.ANALYZE_ONLY, requires_mutation=False))
        ledger.phase_manager._turns_in_current_phase = 1

        result = executor._check_intent_mismatch(
            ledger,
            [{"call_id": "c1", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
            "请修改这个文件",
        )

        # turns_in_phase=1 <= 2 → 允许探索 → 不阻断
        assert result is False, (
            "When session_turns_in_phase <= 2, _check_intent_mismatch should allow exploration (return False)"
        )


# ---------------------------------------------------------------------------
# Bug 3: CONTENT_GATHERED 预执行硬阻断
# ---------------------------------------------------------------------------


class TestContentGatheredPreExecutionBlock:
    """验证 CONTENT_GATHERED 阶段的预执行硬阻断。"""

    @pytest.mark.asyncio
    async def test_content_gathered_blocks_read_only_batch_after_threshold(
        self, mock_emit_event: Any, mock_guard_assert: Any
    ) -> None:
        """CONTENT_GATHERED + MATERIALIZE + 无写工具 + turns >= 2 → 硬阻断。"""
        captured_events: list[Any] = []
        executor = ToolBatchExecutor(
            tool_runtime=AsyncMock(),
            config=TransactionConfig(mutation_guard_mode="warn"),
            emit_event=lambda event: captured_events.append(event),
            guard_assert_single_tool_batch=mock_guard_assert,
            finalization_handler=AsyncMock(),
            handoff_handler=AsyncMock(),
        )
        decision = _make_tool_batch_decision(
            "turn_cg_block", "batch_cg_block", "read_file", {"file": "orchestrator.py"}
        )
        sm = _make_state_machine("turn_cg_block")
        ledger = _make_ledger_in_content_gathered("turn_cg_block", turns_in_phase=3)
        context = [{"role": "user", "content": "进一步完善 Session Orchestrator 相关代码"}]

        with pytest.raises(
            RuntimeError, match=r"CONTENT_GATHERED phase timeout|CONTENT_GATHERED phase requires write tools"
        ):
            await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)

        assert any(
            isinstance(ev, ErrorEvent) and ev.error_type == "content_gathered_write_required" for ev in captured_events
        )

    @pytest.mark.asyncio
    async def test_content_gathered_allows_write_batch(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """CONTENT_GATHERED + MATERIALIZE + 有写工具 → 不阻断。"""
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        decision = _make_tool_batch_decision(
            "turn_cg_write", "batch_cg_write", "edit_file", {"file": "orchestrator.py", "content": "fixed"}
        )
        sm = _make_state_machine("turn_cg_write")
        ledger = _make_ledger_in_content_gathered("turn_cg_write", turns_in_phase=5)
        context = [{"role": "user", "content": "完善代码"}]

        # 不应抛异常（有写工具）
        result = await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)
        assert result.get("turn_id") == "turn_cg_write"

    @pytest.mark.asyncio
    async def test_content_gathered_allows_read_before_threshold(
        self, mock_emit_event: Any, mock_guard_assert: Any
    ) -> None:
        """CONTENT_GATHERED + turns_in_phase < 2 → 允许读取（宽限期内）。"""
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        decision = _make_tool_batch_decision(
            "turn_cg_early", "batch_cg_early", "read_file", {"file": "orchestrator.py"}
        )
        sm = _make_state_machine("turn_cg_early")
        # turns_in_phase=1 < 2 → 在宽限期内
        ledger = _make_ledger_in_content_gathered("turn_cg_early", turns_in_phase=1)
        context = [{"role": "user", "content": "完善代码"}]

        # 不应抛异常（宽限期内）
        result = await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)
        assert result.get("turn_id") == "turn_cg_early"

    @pytest.mark.asyncio
    async def test_content_gathered_no_block_without_materialize(
        self, mock_emit_event: Any, mock_guard_assert: Any
    ) -> None:
        """CONTENT_GATHERED + ANALYZE_ONLY → 不阻断（非 mutation 模式）。"""
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        decision = _make_tool_batch_decision(
            "turn_cg_analyze", "batch_cg_analyze", "read_file", {"file": "orchestrator.py"}
        )
        sm = _make_state_machine("turn_cg_analyze")
        ledger = _make_ledger_in_content_gathered("turn_cg_analyze", turns_in_phase=5, materialize=False)
        context = [{"role": "user", "content": "分析一下这个文件"}]

        # 不应抛异常（非 MATERIALIZE 模式）
        result = await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)
        assert result.get("turn_id") == "turn_cg_analyze"


# ---------------------------------------------------------------------------
# PhaseManager 单元测试补充
# ---------------------------------------------------------------------------


class TestPhaseManagerTimeout:
    """验证 PhaseManager 的超时计数跨 turn 正确工作。"""

    def test_turns_in_phase_increments_on_exploration(self) -> None:
        """同类工具不推进阶段，但增加停留计数。"""
        from polaris.cells.roles.kernel.internal.transaction.phase_manager import ToolResult

        pm = PhaseManager()
        pm.transition([ToolResult("glob")])
        assert pm.current_phase == Phase.EXPLORING
        assert pm._turns_in_current_phase == 1

        pm.transition([ToolResult("repo_rg")])
        assert pm.current_phase == Phase.EXPLORING
        assert pm._turns_in_current_phase == 2

    def test_turns_in_phase_resets_on_phase_change(self) -> None:
        """阶段变更时停留计数重置为 1。"""
        from polaris.cells.roles.kernel.internal.transaction.phase_manager import ToolResult

        pm = PhaseManager()
        pm.transition([ToolResult("glob")])
        pm.transition([ToolResult("repo_rg")])
        assert pm._turns_in_current_phase == 2

        # 真正读取文件 → 跳到 CONTENT_GATHERED
        pm.transition([ToolResult("read_file", bytes_read=1024)])
        assert pm.current_phase == Phase.CONTENT_GATHERED
        assert pm._turns_in_current_phase == 1

    def test_is_phase_timeout_triggers_after_max_turns(self) -> None:
        """超过 max_turns_per_phase 后 is_phase_timeout 返回 True。"""
        from polaris.cells.roles.kernel.internal.transaction.phase_manager import ToolResult

        pm = PhaseManager()
        pm._max_turns_per_phase = 3

        # 进入 CONTENT_GATHERED
        pm.transition([ToolResult("read_file", bytes_read=100)])
        assert pm.current_phase == Phase.CONTENT_GATHERED

        # 在 CONTENT_GATHERED 停留 4 个 turn（> max 3）
        pm.transition([ToolResult("read_file", bytes_read=200)])
        pm.transition([ToolResult("read_file", bytes_read=300)])
        pm.transition([ToolResult("read_file", bytes_read=400)])

        is_timeout, msg = pm.is_phase_timeout()
        assert is_timeout is True
        assert "content_gathered" in msg

    def test_serialization_preserves_turns_in_phase(self) -> None:
        """序列化/反序列化保留 turns_in_current_phase。"""
        pm = PhaseManager()
        pm._current_phase = Phase.CONTENT_GATHERED
        pm._turns_in_current_phase = 5

        data = pm.to_dict()
        restored = PhaseManager.from_dict(data)

        assert restored.current_phase == Phase.CONTENT_GATHERED
        assert restored._turns_in_current_phase == 5

    def test_auxiliary_session_patch_write_does_not_advance_to_implementing(self) -> None:
        """SESSION_PATCH.md 辅助写入不得推进到 IMPLEMENTING。"""
        from polaris.cells.roles.kernel.internal.transaction.phase_manager import ToolResult

        pm = PhaseManager()
        pm.transition([ToolResult("read_file", bytes_read=128)])
        assert pm.current_phase == Phase.CONTENT_GATHERED

        pm.transition(
            [
                ToolResult.from_batch_result(
                    {
                        "tool_name": "append_to_file",
                        "status": "success",
                        "arguments": {"file": "SESSION_PATCH.md", "content": "note"},
                        "result": {"effect_receipt": {"file": "SESSION_PATCH.md"}},
                    }
                )
            ]
        )

        assert pm.current_phase == Phase.CONTENT_GATHERED

    def test_dot_polaris_runtime_write_is_not_authoritative(self) -> None:
        """`.polaris/**` 运行时写入不得满足 authoritative write。"""
        from polaris.cells.roles.kernel.internal.transaction.phase_manager import (
            ToolResult,
            has_authoritative_write_receipt,
        )

        result = ToolResult.from_batch_result(
            {
                "tool_name": "write_file",
                "status": "success",
                "arguments": {"file": "X:/.polaris/projects/backend/runtime/tasks/task_1.json"},
                "result": {"file": "X:/.polaris/projects/backend/runtime/tasks/task_1.json"},
            }
        )

        assert result.is_write is True
        assert result.is_authoritative_write is False
        assert (
            has_authoritative_write_receipt(
                {
                    "results": [
                        {
                            "tool_name": "write_file",
                            "status": "success",
                            "arguments": {"file": "X:/.polaris/projects/backend/runtime/tasks/task_1.json"},
                            "result": {"file": "X:/.polaris/projects/backend/runtime/tasks/task_1.json"},
                        }
                    ]
                }
            )
            is False
        )
