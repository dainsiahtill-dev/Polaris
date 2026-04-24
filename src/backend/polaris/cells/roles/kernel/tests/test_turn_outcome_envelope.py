"""Tests for TurnOutcomeEnvelope schema (Phase 1 P0-1).

验证：
1. TurnOutcome 模型正确性和约束
2. ContinuationHint 派生投影和可重建性
3. TurnLedger.to_turn_outcome() 转换逻辑
4. 命名约束（无认知隐喻）
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.public.turn_contracts import (
    CommitReceipt,
    ContinuationHint,
    FailureClass,
    FinalizeMode,
    OutcomeStatus,
    ResolutionCode,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
    TurnOutcome,
)


class TestTurnOutcomeSchema:
    """TurnOutcome 模型 schema 验证。"""

    def test_turn_outcome_creation(self) -> None:
        """基本 TurnOutcome 创建。"""
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="hello",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        outcome = TurnOutcome(
            turn_id=TurnId("t1"),
            run_id="run_001",
            decision=decision,
            outcome_status=OutcomeStatus.COMPLETED,
            resolution_code=ResolutionCode.COMPLETED,
        )
        assert outcome.turn_id == TurnId("t1")
        assert outcome.outcome_status == OutcomeStatus.COMPLETED
        assert outcome.resolution_code == ResolutionCode.COMPLETED

    def test_turn_outcome_with_commit(self) -> None:
        """带 commit ref 的 TurnOutcome。"""
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="using tools",
            finalize_mode=FinalizeMode.LLM_ONCE,
            domain="code",
        )
        commit = CommitReceipt(
            turn_id=TurnId("t1"),
            snapshot_id="snap_123",
            truthlog_seq_range=(100, 110),
            sealed_at="2026-04-21T10:00:00Z",
            validation_passed=True,
        )
        outcome = TurnOutcome(
            turn_id=TurnId("t1"),
            run_id="run_001",
            decision=decision,
            outcome_status=OutcomeStatus.COMPLETED,
            resolution_code=ResolutionCode.COMPLETED,
            commit_ref=commit,
        )
        assert outcome.commit_ref is not None
        assert outcome.commit_ref.snapshot_id == "snap_123"
        assert outcome.commit_ref.truthlog_seq_range == (100, 110)

    def test_turn_outcome_failure_classification(self) -> None:
        """FailureClass 正确映射到 OutcomeStatus。"""
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="error",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        outcome = TurnOutcome(
            turn_id=TurnId("t1"),
            run_id="run_001",
            decision=decision,
            outcome_status=OutcomeStatus.PANIC,
            resolution_code=ResolutionCode.FAIL_CLOSED,
            failure_class=FailureClass.CONTRACT_VIOLATION,
        )
        assert outcome.failure_class == FailureClass.CONTRACT_VIOLATION
        assert outcome.outcome_status == OutcomeStatus.PANIC
        assert outcome.resolution_code == ResolutionCode.FAIL_CLOSED

    def test_turn_outcome_dict_compatibility(self) -> None:
        """FrozenMappingModel dict 兼容接口。"""
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="test",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        outcome = TurnOutcome(
            turn_id=TurnId("t1"),
            run_id="run_001",
            decision=decision,
            outcome_status=OutcomeStatus.COMPLETED,
            resolution_code=ResolutionCode.COMPLETED,
        )
        # dict-like compatibility
        assert outcome["turn_id"] == TurnId("t1")
        assert outcome.get("outcome_status") == OutcomeStatus.COMPLETED
        assert "resolution_code" in outcome
        d = outcome.to_dict()
        assert d["turn_id"] == "t1"
        assert d["outcome_status"] == "completed"

    def test_turn_outcome_summary_dict(self) -> None:
        """to_summary_dict 轻量投影。"""
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="test",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        commit = CommitReceipt(
            turn_id=TurnId("t1"),
            snapshot_id="snap_123",
            truthlog_seq_range=(100, 110),
            sealed_at="2026-04-21T10:00:00Z",
            validation_passed=True,
        )
        outcome = TurnOutcome(
            turn_id=TurnId("t1"),
            run_id="run_001",
            decision=decision,
            outcome_status=OutcomeStatus.COMPLETED,
            resolution_code=ResolutionCode.COMPLETED,
            commit_ref=commit,
        )
        summary = outcome.to_summary_dict()
        assert summary["turn_id"] == "t1"
        assert summary["outcome_status"] == "completed"
        assert summary["resolution_code"] == "completed"
        assert summary["commit_snapshot_id"] == "snap_123"

    def test_outcome_status_is_enum_not_free_text(self) -> None:
        """outcome_status 必须是枚举值（非法字符串会失败）。"""
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="test",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        # 尝试传入非法字符串应该失败
        with pytest.raises(ValueError):
            TurnOutcome(
                turn_id=TurnId("t1"),
                run_id="run_001",
                decision=decision,
                outcome_status="invalid_status",  # type: ignore[arg-type]
                resolution_code=ResolutionCode.COMPLETED,
            )


class TestContinuationHint:
    """ContinuationHint 派生投影验证。"""

    def test_continuation_hint_is_derived(self) -> None:
        """derived 字段必须为 True。"""
        hint = ContinuationHint(
            goal_progress_summary="making progress",
            new_refs=["ref1"],
            blocked_reason="",
            continuation_hint="explore",
        )
        assert hint.derived is True

    def test_rebuild_from_snapshot(self) -> None:
        """rebuild_from 方法证明可重建性。"""
        snapshot = {
            "goal_progress_summary": "progress",
            "new_refs": ["a", "b"],
            "blocked_reason": "waiting",
            "continuation_hint": "stop",
        }
        truthlog: list[dict] = []
        hint = ContinuationHint.rebuild_from(snapshot, truthlog)
        assert hint.derived is True
        assert hint.goal_progress_summary == "progress"
        assert hint.new_refs == ["a", "b"]
        assert hint.continuation_hint == "stop"

    def test_rebuild_from_empty(self) -> None:
        """从空 snapshot 重建。"""
        hint = ContinuationHint.rebuild_from({}, [])
        assert hint.derived is True
        assert hint.goal_progress_summary is None
        assert hint.new_refs == []


class TestTurnLedgerToOutcome:
    """TurnLedger.to_turn_outcome() 转换验证。"""

    def test_completed_turn(self) -> None:
        """正常完成的 turn。"""
        ledger = TurnLedger(turn_id="t1")
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        ledger.record_decision(decision)
        outcome = ledger.to_turn_outcome(
            run_id="run_001",
            decision=decision,
        )
        assert outcome.turn_id == TurnId("t1")
        assert outcome.outcome_status == OutcomeStatus.COMPLETED
        assert outcome.resolution_code == ResolutionCode.COMPLETED
        assert outcome.failure_class is None
        assert outcome.continuation_hint is not None
        assert outcome.continuation_hint.continuation_hint == "stop"

    def test_tool_batch_turn(self) -> None:
        """工具批次 turn。"""
        ledger = TurnLedger(turn_id="t1")
        ledger.tool_batch_count = 1
        ledger.record_tool_execution("read_file", "c1", "success", 100)
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="tools",
            finalize_mode=FinalizeMode.LLM_ONCE,
            domain="code",
        )
        ledger.record_decision(decision)
        outcome = ledger.to_turn_outcome(
            run_id="run_001",
            decision=decision,
        )
        assert outcome.outcome_status == OutcomeStatus.COMPLETED
        assert outcome.continuation_hint is not None
        assert outcome.continuation_hint.continuation_hint == "explore"
        assert "read_file" in outcome.continuation_hint.new_refs

    def test_contract_violation_turn(self) -> None:
        """Contract violation 导致 PANIC。"""
        ledger = TurnLedger(turn_id="t1")
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="error",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        ledger.record_decision(decision)
        outcome = ledger.to_turn_outcome(
            run_id="run_001",
            decision=decision,
            failure_class=FailureClass.CONTRACT_VIOLATION,
        )
        assert outcome.outcome_status == OutcomeStatus.PANIC
        assert outcome.resolution_code == ResolutionCode.FAIL_CLOSED
        assert outcome.failure_class == FailureClass.CONTRACT_VIOLATION

    def test_handoff_workflow_turn(self) -> None:
        """Handoff workflow turn。"""
        ledger = TurnLedger(turn_id="t1")
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message="handoff",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        ledger.record_decision(decision)
        outcome = ledger.to_turn_outcome(
            run_id="run_001",
            decision=decision,
        )
        assert outcome.outcome_status == OutcomeStatus.HANDED_OFF
        assert outcome.resolution_code == ResolutionCode.HANDOFF_WORKFLOW

    def test_ledger_is_audit_source_not_consumer(self) -> None:
        """Ledger 是审计源，不能直接作为业务结果消费。"""
        ledger = TurnLedger(turn_id="t1")
        # ledger 本身不是 TurnOutcome，必须通过 to_turn_outcome() 转换
        assert not hasattr(ledger, "outcome_status")
        assert hasattr(ledger, "to_turn_outcome")


class TestNamingConstraints:
    """命名约束验证：禁止认知隐喻进入运行时命名。"""

    def test_no_cognitive_metaphors_in_class_names(self) -> None:
        """类名中不出现认知隐喻。"""
        # 这些类名应该存在（工程命名）
        assert hasattr(__import__("polaris.cells.roles.kernel.public.turn_contracts", fromlist=[""]), "TurnOutcome")
        assert hasattr(__import__("polaris.cells.roles.kernel.public.turn_contracts", fromlist=[""]), "CommitReceipt")
        assert hasattr(__import__("polaris.cells.roles.kernel.public.turn_contracts", fromlist=[""]), "SealedTurn")

        # 这些类名不应该存在（认知隐喻）
        module = __import__("polaris.cells.roles.kernel.public.turn_contracts", fromlist=[""])
        assert not hasattr(module, "HeartbeatResult")
        assert not hasattr(module, "NeuralSeal")
        assert not hasattr(module, "HippocampusCommit")
        assert not hasattr(module, "CognitivePulse")

    def test_field_names_are_engineering_terms(self) -> None:
        """字段名使用工程术语。"""
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="test",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        outcome = TurnOutcome(
            turn_id=TurnId("t1"),
            run_id="run_001",
            decision=decision,
            outcome_status=OutcomeStatus.COMPLETED,
            resolution_code=ResolutionCode.COMPLETED,
        )
        # 验证字段名是工程术语
        fields = outcome.keys()
        assert "turn_id" in fields
        assert "outcome_status" in fields
        assert "resolution_code" in fields
        assert "commit_ref" in fields
        # 不应有隐喻字段
        assert "heartbeat" not in fields
        assert "neural_state" not in fields
        assert "cognitive_pulse" not in fields
