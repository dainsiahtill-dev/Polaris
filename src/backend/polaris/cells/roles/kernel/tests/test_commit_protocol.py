"""Tests for Commit Protocol hardening (Phase 1 P0-2).

验证：
1. Pre-commit validation 7 项检查
2. Durable commit protocol critical section
3. Post-commit seal
4. 防双重提交
5. 验证失败路径
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.public.turn_contracts import (
    CommitReceipt,
    FinalizeMode,
    OutcomeStatus,
    ResolutionCode,
    SealedTurn,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)


class TestPreCommitValidation:
    """Pre-commit validation 7 项检查验证。"""

    def test_single_decision_pass(self) -> None:
        """单个 decision 通过验证。"""
        ledger = TurnLedger(turn_id="t1")
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        ledger.record_decision(decision)
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.passed is True
        assert report.checks["single_decision"] is True

    def test_single_decision_fail_multiple(self) -> None:
        """多个 decision 失败验证。"""
        ledger = TurnLedger(turn_id="t1")
        decision1 = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="tools",
            finalize_mode=FinalizeMode.LLM_ONCE,
            domain="code",
        )
        decision2 = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            finalize_mode=FinalizeMode.NONE,
            domain="code",
        )
        ledger.record_decision(decision1)
        ledger.record_decision(decision2)
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.passed is False
        assert report.checks["single_decision"] is False
        assert "expected 1 decision" in report.errors[0]

    def test_single_tool_batch_pass(self) -> None:
        """0 个 tool batch 通过验证。"""
        ledger = TurnLedger(turn_id="t1")
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.checks["single_tool_batch"] is True

    def test_single_tool_batch_fail(self) -> None:
        """2 个 tool batch 失败验证。"""
        ledger = TurnLedger(turn_id="t1")
        ledger.tool_batch_count = 2
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.checks["single_tool_batch"] is False

    def test_no_hidden_continuation_pass(self) -> None:
        """无隐藏连续通过验证。"""
        ledger = TurnLedger(turn_id="t1")
        ledger.state_history.append(("DECISION_REQUESTED", 1000))
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.checks["no_hidden_continuation"] is True

    def test_no_hidden_continuation_fail(self) -> None:
        """多次 DECISION_REQUESTED 失败验证。"""
        ledger = TurnLedger(turn_id="t1")
        ledger.state_history.append(("DECISION_REQUESTED", 1000))
        ledger.state_history.append(("DECISION_REQUESTED", 2000))
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.checks["no_hidden_continuation"] is False

    def test_receipts_integrity_with_tools(self) -> None:
        """有工具执行记录通过验证。"""
        ledger = TurnLedger(turn_id="t1")
        ledger.record_tool_execution("read_file", "c1", "success", 100)
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.checks["receipts_integrity"] is True

    def test_outcome_status_legal(self) -> None:
        """合法的 outcome status 通过验证。"""
        ledger = TurnLedger(turn_id="t1")
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.checks["outcome_status_legal"] is True

    def test_outcome_status_illegal(self) -> None:
        """非法的 outcome status 失败验证。"""
        ledger = TurnLedger(turn_id="t1")
        snapshot: dict[str, Any] = {"version": "invalid"}  # type: ignore[dict-item]
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.checks["outcome_status_legal"] is False

    def test_all_checks_pass(self) -> None:
        """所有检查通过时 report.passed 为 True。"""
        ledger = TurnLedger(turn_id="t1")
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        ledger.record_decision(decision)
        snapshot: dict[str, Any] = {"version": 1}
        report = RoleExecutionKernel._pre_commit_validate(ledger, snapshot, "t1")
        assert report.passed is True
        assert len(report.errors) == 0
        assert set(report.checks.keys()) == {
            "single_decision",
            "single_tool_batch",
            "no_hidden_continuation",
            "receipts_integrity",
            "artifact_refs_valid",
            "budget_balance",
            "outcome_status_legal",
        }


class TestCommitProtocol:
    """Durable commit protocol 验证。"""

    def test_commit_returns_receipt(self) -> None:
        """Commit 成功返回 CommitReceipt。"""
        request = MagicMock()
        request.context_override = {
            "context_os_snapshot": {
                "snapshot_id": "snap_001",
                "version": 1,
                "transcript_log": [],
            }
        }
        ledger = TurnLedger(turn_id="t1")
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        ledger.record_decision(decision)
        receipt = RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,
            turn_id="t1",
            turn_history=[("user", "hello")],
            turn_events_metadata=[{"role": "user", "content": "hello", "event_id": "e1", "kind": "user_turn"}],
            tool_results=[],
            ledger=ledger,
        )
        assert receipt is not None
        assert isinstance(receipt, CommitReceipt)
        assert receipt.turn_id == TurnId("t1")
        assert receipt.validation_passed is True

    def test_idempotency_skips_duplicate(self) -> None:
        """同一 turn_id 第二次 commit 返回 None。"""
        request = MagicMock()
        request.context_override = {
            "context_os_snapshot": {
                "snapshot_id": "snap_001",
                "version": 1,
                "transcript_log": [],
                "last_commit_turn_id": "t1",
            }
        }
        ledger = TurnLedger(turn_id="t1")
        receipt = RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,
            turn_id="t1",
            turn_history=[],
            turn_events_metadata=[],
            tool_results=[],
            ledger=ledger,
        )
        assert receipt is None

    def test_validation_failure_returns_none(self) -> None:
        """验证失败返回 None。"""
        request = MagicMock()
        request.context_override = {
            "context_os_snapshot": {
                "snapshot_id": "snap_001",
                "version": 1,
                "transcript_log": [],
            }
        }
        ledger = TurnLedger(turn_id="t1")
        ledger.tool_batch_count = 2  # 违反 single_tool_batch
        receipt = RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,
            turn_id="t1",
            turn_history=[],
            turn_events_metadata=[],
            tool_results=[],
            ledger=ledger,
        )
        assert receipt is None

    def test_critical_path_updates_snapshot(self) -> None:
        """Critical path 正确更新 snapshot。"""
        request = MagicMock()
        snapshot = {
            "snapshot_id": "snap_001",
            "version": 1,
            "transcript_log": [],
        }
        request.context_override = {"context_os_snapshot": snapshot}
        ledger = TurnLedger(turn_id="t1")
        decision = TurnDecision(
            turn_id=TurnId("t1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )
        ledger.record_decision(decision)
        RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,
            turn_id="t1",
            turn_history=[("user", "hello")],
            turn_events_metadata=[{"role": "user", "content": "hello", "event_id": "e1", "kind": "user_turn"}],
            tool_results=[],
            ledger=ledger,
        )
        assert snapshot["version"] == 2
        assert snapshot["last_commit_turn_id"] == "t1"
        assert len(snapshot["transcript_log"]) == 1

    def test_missing_context_override_returns_none(self) -> None:
        """缺少 context_override 返回 None。"""
        request = MagicMock()
        request.context_override = None
        receipt = RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,
            turn_id="t1",
            turn_history=[],
            turn_events_metadata=[],
            tool_results=[],
        )
        assert receipt is None


class TestPostCommitSeal:
    """Post-commit seal 验证。"""

    def test_seal_creation(self) -> None:
        """SealedTurn 正确生成。"""
        commit = CommitReceipt(
            turn_id=TurnId("t1"),
            snapshot_id="snap_001",
            truthlog_seq_range=(0, 1),
            sealed_at="2026-04-21T10:00:00Z",
            validation_passed=True,
        )
        sealed = RoleExecutionKernel._post_commit_seal(
            commit_receipt=commit,
            outcome_status="completed",
            resolution_code="completed",
            parent_snapshot_id="snap_000",
        )
        assert isinstance(sealed, SealedTurn)
        assert sealed.turn_id == TurnId("t1")
        assert sealed.outcome_status == OutcomeStatus.COMPLETED
        assert sealed.resolution_code == ResolutionCode.COMPLETED
        assert sealed.parent_snapshot_id == "snap_000"

    def test_seal_with_failure(self) -> None:
        """失败状态的 seal 生成。"""
        commit = CommitReceipt(
            turn_id=TurnId("t1"),
            snapshot_id="snap_001",
            truthlog_seq_range=(0, 0),
            sealed_at="2026-04-21T10:00:00Z",
            validation_passed=True,
        )
        sealed = RoleExecutionKernel._post_commit_seal(
            commit_receipt=commit,
            outcome_status="failed",
            resolution_code="fail_closed",
        )
        assert sealed.outcome_status == OutcomeStatus.FAILED
        assert sealed.resolution_code == ResolutionCode.FAIL_CLOSED
        assert sealed.parent_snapshot_id is None
