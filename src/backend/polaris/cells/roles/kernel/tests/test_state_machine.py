"""
Tests for Turn State Machine

验证：
1. 正常状态转换
2. 禁止的转换被阻止
3. 终止状态正确识别
"""

import pytest
from polaris.cells.roles.kernel.internal.turn_state_machine import (
    FORBIDDEN_TRANSITIONS,
    InvalidStateTransitionError,
    TurnState,
    TurnStateMachine,
)


class TestValidTransitions:
    """测试正常状态转换"""

    def test_idle_to_context_built(self) -> None:
        sm = TurnStateMachine(turn_id="test_1")
        sm.transition_to(TurnState.CONTEXT_BUILT)
        assert sm.state == TurnState.CONTEXT_BUILT

    def test_full_success_path_none(self) -> None:
        """NONE模式完整路径"""
        sm = TurnStateMachine(turn_id="test_2")

        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTED)
        sm.transition_to(TurnState.COMPLETED)

        assert sm.is_terminal()
        assert not sm.is_failed()

    def test_full_success_path_llm_once(self) -> None:
        """LLM_ONCE模式完整路径"""
        sm = TurnStateMachine(turn_id="test_3")

        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTED)
        sm.transition_to(TurnState.FINALIZATION_REQUESTED)
        sm.transition_to(TurnState.FINALIZATION_RECEIVED)
        sm.transition_to(TurnState.COMPLETED)

        assert sm.is_terminal()

    def test_final_answer_path(self) -> None:
        """直接回答路径"""
        sm = TurnStateMachine(turn_id="test_4")

        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.FINAL_ANSWER_READY)
        sm.transition_to(TurnState.COMPLETED)

        assert sm.is_terminal()

    def test_handoff_path(self) -> None:
        """移交workflow路径"""
        sm = TurnStateMachine(turn_id="test_5")

        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.HANDOFF_WORKFLOW)
        sm.transition_to(TurnState.COMPLETED)

        assert sm.is_terminal()

    def test_ask_user_suspended_path(self) -> None:
        """ASK_USER 路径应该进入 SUSPENDED 终止状态"""
        sm = TurnStateMachine(turn_id="test_ask_user")

        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.SUSPENDED)

        assert sm.is_terminal()
        assert not sm.is_failed()


class TestForbiddenTransitions:
    """测试禁止的状态转换（架构防护）"""

    def test_tool_executed_to_decision_requested_blocked(self) -> None:
        """
        关键测试：TOOL_BATCH_EXECUTED -> DECISION_REQUESTED 必须被阻止

        这是旧架构continuation loop的根源。
        """
        sm = TurnStateMachine(turn_id="test_6")

        # 走到TOOL_BATCH_EXECUTED
        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTED)

        # 尝试回到DECISION_REQUESTED（旧continuation loop行为）
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            sm.transition_to(TurnState.DECISION_REQUESTED)

        assert "FORBIDDEN" in str(exc_info.value)
        assert "continuation loop" in str(exc_info.value).lower()

    def test_tool_executed_to_context_built_blocked(self) -> None:
        """TOOL_BATCH_EXECUTED -> CONTEXT_BUILT 必须被阻止"""
        sm = TurnStateMachine(turn_id="test_7")
        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTED)

        with pytest.raises(InvalidStateTransitionError) as exc_info:
            sm.transition_to(TurnState.CONTEXT_BUILT)

        assert "FORBIDDEN" in str(exc_info.value)

    def test_finalization_to_tool_batch_blocked(self) -> None:
        """
        关键测试：FINALIZATION_REQUESTED -> TOOL_BATCH_EXECUTING 必须被阻止

        llm_once收口禁止再调工具。
        """
        sm = TurnStateMachine(turn_id="test_8")
        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTED)
        sm.transition_to(TurnState.FINALIZATION_REQUESTED)

        with pytest.raises(InvalidStateTransitionError) as exc_info:
            sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)

        assert "FORBIDDEN" in str(exc_info.value)

    def test_skip_phases_blocked(self) -> None:
        """禁止跳过必要阶段"""
        sm = TurnStateMachine(turn_id="test_9")

        with pytest.raises(InvalidStateTransitionError):
            # 不能从IDLE直接跳到TOOL_BATCH_EXECUTING
            sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)


class TestFailurePath:
    """测试失败路径"""

    def test_failure_from_any_state(self) -> None:
        """从任何状态都可以转到FAILED"""
        for state in [TurnState.DECISION_REQUESTED, TurnState.TOOL_BATCH_EXECUTING]:
            sm = TurnStateMachine(turn_id=f"test_fail_{state.name}")
            sm._state = state  # 直接设置用于测试

            sm.transition_to(TurnState.FAILED)
            assert sm.is_terminal()
            assert sm.is_failed()


class TestSuspendedPath:
    """测试 SUSPENDED 路径（ASK_USER 的干净终端状态）"""

    def test_suspended_is_terminal(self) -> None:
        """SUSPENDED 必须是终止状态"""
        sm = TurnStateMachine(turn_id="test_suspended")
        sm._state = TurnState.DECISION_DECODED

        sm.transition_to(TurnState.SUSPENDED)
        assert sm.is_terminal()
        assert not sm.is_failed()

    def test_suspended_from_decision_decoded(self) -> None:
        """DECISION_DECODED -> SUSPENDED 必须是合法转换"""
        sm = TurnStateMachine(turn_id="test_suspended_transition")
        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.SUSPENDED)

        assert sm.state == TurnState.SUSPENDED
        assert sm.is_terminal()


class TestMetadataAndHistory:
    """测试元数据和历史记录"""

    def test_transition_metadata(self) -> None:
        sm = TurnStateMachine(turn_id="test_meta")

        sm.transition_to(TurnState.CONTEXT_BUILT, {"ctx_size": 1000})
        sm.transition_to(TurnState.DECISION_REQUESTED, {"model": "claude"})

        assert "ctx_size" in sm._metadata.get("IDLE_to_CONTEXT_BUILT", {})
        assert "model" in sm._metadata.get("CONTEXT_BUILT_to_DECISION_REQUESTED", {})

    def test_history_tracking(self) -> None:
        sm = TurnStateMachine(turn_id="test_history")

        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)

        history = sm.get_history()
        assert len(history) == 3  # IDLE + 2 transitions
        assert history[0][0] == TurnState.IDLE
        assert history[1][0] == TurnState.CONTEXT_BUILT

    def test_duration_tracking(self) -> None:
        import time

        sm = TurnStateMachine(turn_id="test_duration")
        time.sleep(0.01)  # 10ms

        sm.transition_to(TurnState.CONTEXT_BUILT)
        time.sleep(0.01)

        sm.transition_to(TurnState.DECISION_REQUESTED)

        duration = sm.get_duration_ms()
        assert duration >= 20  # 至少20ms


class TestAssertionHelpers:
    """测试断言辅助方法"""

    def test_assert_in_state_success(self) -> None:
        sm = TurnStateMachine(turn_id="test_assert")
        sm.transition_to(TurnState.CONTEXT_BUILT)

        sm.assert_in_state(TurnState.CONTEXT_BUILT)
        sm.assert_in_state({TurnState.CONTEXT_BUILT, TurnState.COMPLETED})

    def test_assert_in_state_failure(self) -> None:
        sm = TurnStateMachine(turn_id="test_assert_fail")
        sm.transition_to(TurnState.CONTEXT_BUILT)

        with pytest.raises(AssertionError):
            sm.assert_in_state(TurnState.COMPLETED)


class TestCanTransition:
    """测试can_transition_to方法"""

    def test_can_transition_positive(self) -> None:
        sm = TurnStateMachine(turn_id="test_can")
        assert sm.can_transition_to(TurnState.CONTEXT_BUILT)

    def test_can_transition_negative_forbidden(self) -> None:
        sm = TurnStateMachine(turn_id="test_cant")
        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        sm.transition_to(TurnState.DECISION_RECEIVED)
        sm.transition_to(TurnState.DECISION_DECODED)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        sm.transition_to(TurnState.TOOL_BATCH_EXECUTED)

        # 不能回到DECISION_REQUESTED
        assert not sm.can_transition_to(TurnState.DECISION_REQUESTED)


class TestForbiddenTransitionsTable:
    """验证禁止转换表包含关键约束"""

    def test_continuation_loop_forbidden(self) -> None:
        """验证continuation loop转换在禁止列表中"""
        assert (TurnState.TOOL_BATCH_EXECUTED, TurnState.DECISION_REQUESTED) in FORBIDDEN_TRANSITIONS
        assert (TurnState.TOOL_BATCH_EXECUTED, TurnState.CONTEXT_BUILT) in FORBIDDEN_TRANSITIONS

    def test_finalization_tool_chain_forbidden(self) -> None:
        """验证finalization后调工具被禁止"""
        assert (TurnState.FINALIZATION_REQUESTED, TurnState.TOOL_BATCH_EXECUTING) in FORBIDDEN_TRANSITIONS
