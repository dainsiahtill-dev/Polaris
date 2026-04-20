"""Tests for FailureClass-driven continuation policy (Phase 1.5).

验证：
1. FailureClass 映射到 continuation action
2. can_continue 消费 failure_class
3. 边界情况（None failure_class）
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.public.turn_contracts import (
    FailureClass,
    TurnContinuationMode,
    TurnOutcomeEnvelope,
    TurnResult,
)
from polaris.cells.roles.runtime.internal.continuation_policy import (
    ContinuationPolicy,
    OrchestratorSessionState,
)


class TestFailureClassContinuationMapping:
    """FailureClass 到 continuation action 的映射验证。"""

    def test_contract_violation_stops(self) -> None:
        """CONTRACT_VIOLATION 必须停止。"""
        policy = ContinuationPolicy()
        assert policy._resolve_failure_class(FailureClass.CONTRACT_VIOLATION) == "stop"

    def test_durability_failure_stops_and_help(self) -> None:
        """DURABILITY_FAILURE 必须停止并求助。"""
        policy = ContinuationPolicy()
        assert policy._resolve_failure_class(FailureClass.DURABILITY_FAILURE) == "stop_and_help"

    def test_runtime_failure_continues(self) -> None:
        """RUNTIME_FAILURE 允许继续（带重试预算）。"""
        policy = ContinuationPolicy()
        assert policy._resolve_failure_class(FailureClass.RUNTIME_FAILURE) == "continue"

    def test_insufficient_evidence_continues(self) -> None:
        """INSUFFICIENT_EVIDENCE 允许继续探索。"""
        policy = ContinuationPolicy()
        assert policy._resolve_failure_class(FailureClass.INSUFFICIENT_EVIDENCE) == "continue"

    def test_policy_failure_stops(self) -> None:
        """POLICY_FAILURE 必须停止。"""
        policy = ContinuationPolicy()
        assert policy._resolve_failure_class(FailureClass.POLICY_FAILURE) == "stop"

    def test_none_failure_continues(self) -> None:
        """无 failure_class 时允许继续。"""
        policy = ContinuationPolicy()
        assert policy._resolve_failure_class(None) == "continue"


class TestCanContinueWithFailureClass:
    """can_continue 方法消费 failure_class 验证。"""

    def _make_envelope(
        self,
        failure_class: FailureClass | None = None,
        mode: TurnContinuationMode = TurnContinuationMode.AUTO_CONTINUE,
    ) -> TurnOutcomeEnvelope:
        from polaris.cells.roles.kernel.public.turn_contracts import TurnDecision, TurnDecisionKind, FinalizeMode
        
        decision = TurnDecision(
            turn_id="t1",
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        result = TurnResult(
            turn_id="t1",
            kind="final_answer",
            visible_content="done",
            decision=decision,
        )
        return TurnOutcomeEnvelope(
            turn_result=result,
            continuation_mode=mode,
            failure_class=failure_class,
        )

    def test_contract_violation_blocks_continue(self) -> None:
        """CONTRACT_VIOLATION 阻止继续，即使 mode 是 AUTO_CONTINUE。"""
        policy = ContinuationPolicy()
        state = OrchestratorSessionState(session_id="s1")
        envelope = self._make_envelope(failure_class=FailureClass.CONTRACT_VIOLATION)
        can_continue, reason = policy.can_continue(state, envelope)
        assert can_continue is False
        assert "failure_class" in (reason or "")

    def test_durability_failure_blocks_continue(self) -> None:
        """DURABILITY_FAILURE 阻止继续。"""
        policy = ContinuationPolicy()
        state = OrchestratorSessionState(session_id="s1")
        envelope = self._make_envelope(failure_class=FailureClass.DURABILITY_FAILURE)
        can_continue, reason = policy.can_continue(state, envelope)
        assert can_continue is False
        assert reason == "durability_failure_stop"

    def test_insufficient_evidence_allows_continue(self) -> None:
        """INSUFFICIENT_EVIDENCE 允许继续。"""
        policy = ContinuationPolicy()
        state = OrchestratorSessionState(session_id="s1")
        envelope = self._make_envelope(failure_class=FailureClass.INSUFFICIENT_EVIDENCE)
        can_continue, reason = policy.can_continue(state, envelope)
        assert can_continue is True
        assert reason is None

    def test_no_failure_class_allows_continue(self) -> None:
        """无 failure_class 时正常检查其他条件。"""
        policy = ContinuationPolicy()
        state = OrchestratorSessionState(session_id="s1")
        envelope = self._make_envelope(failure_class=None)
        can_continue, reason = policy.can_continue(state, envelope)
        assert can_continue is True
        assert reason is None
