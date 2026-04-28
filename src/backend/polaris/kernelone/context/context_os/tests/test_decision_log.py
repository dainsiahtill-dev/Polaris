"""Tests for Context Decision Log (ContextOS 3.0 Phase 0)."""

from pathlib import Path

import pytest
from polaris.kernelone.context.context_os.decision_log import (
    AttentionScore,
    ContextDecision,
    ContextDecisionLog,
    ContextDecisionType,
    ProjectionReport,
    ReasonCode,
    create_decision,
)


class TestContextDecisionType:
    """Test ContextDecisionType enum."""

    def test_enum_values(self) -> None:
        assert ContextDecisionType.INCLUDE_FULL.value == "include_full"
        assert ContextDecisionType.EXCLUDE.value == "exclude"
        assert ContextDecisionType.COMPRESS.value == "compress"

    def test_enum_from_value(self) -> None:
        assert ContextDecisionType("include_full") == ContextDecisionType.INCLUDE_FULL


class TestReasonCode:
    """Test ReasonCode enum."""

    def test_enum_values(self) -> None:
        assert ReasonCode.MATCHES_CURRENT_GOAL.value == "MATCHES_CURRENT_GOAL"
        assert ReasonCode.TOKEN_BUDGET_EXCEEDED.value == "TOKEN_BUDGET_EXCEEDED"

    def test_enum_from_value(self) -> None:
        assert ReasonCode("MATCHES_CURRENT_GOAL") == ReasonCode.MATCHES_CURRENT_GOAL


class TestAttentionScore:
    """Test AttentionScore dataclass."""

    def test_default_values(self) -> None:
        score = AttentionScore()
        assert score.semantic_similarity == 0.0
        assert score.recency_score == 0.0
        assert score.contract_overlap == 0.0
        assert score.evidence_weight == 0.0
        assert score.phase_affinity == 0.0
        assert score.user_pin_boost == 0.0
        assert score.final_score == 0.0

    def test_custom_values(self) -> None:
        score = AttentionScore(
            semantic_similarity=0.8,
            recency_score=0.3,
            contract_overlap=0.7,
            evidence_weight=0.5,
            phase_affinity=0.9,
            user_pin_boost=0.1,
            final_score=0.75,
        )
        assert score.semantic_similarity == 0.8
        assert score.final_score == 0.75

    def test_to_dict(self) -> None:
        score = AttentionScore(semantic_similarity=0.8, final_score=0.75)
        result = score.to_dict()
        assert result["semantic"] == 0.8
        assert result["final"] == 0.75
        assert "recency" in result

    def test_frozen(self) -> None:
        score = AttentionScore()
        with pytest.raises(AttributeError):
            score.semantic_similarity = 1.0  # type: ignore


class TestContextDecision:
    """Test ContextDecision dataclass."""

    def test_create_decision(self) -> None:
        decision = ContextDecision(
            timestamp="2026-04-28T12:00:00",
            decision_type=ContextDecisionType.INCLUDE_FULL,
            target_event_id="evt_001",
            reason="included_in_active_window",
            reason_codes=(ReasonCode.FORCED_RECENT,),
        )
        assert decision.decision_type == ContextDecisionType.INCLUDE_FULL
        assert decision.target_event_id == "evt_001"
        assert ReasonCode.FORCED_RECENT in decision.reason_codes

    def test_to_dict(self) -> None:
        decision = ContextDecision(
            timestamp="2026-04-28T12:00:00",
            decision_type=ContextDecisionType.EXCLUDE,
            target_event_id="evt_002",
            reason="token_budget_exceeded",
            reason_codes=(ReasonCode.TOKEN_BUDGET_EXCEEDED,),
            token_budget_before=10000,
            token_budget_after=8000,
            token_cost=2500,
            explanation="Event excluded due to budget constraints.",
        )
        result = decision.to_dict()
        assert result["decision_type"] == "exclude"
        assert result["token_budget_before"] == 10000
        assert result["explanation"] == "Event excluded due to budget constraints."

    def test_to_dict_with_attention_score(self) -> None:
        score = AttentionScore(semantic_similarity=0.8, final_score=0.75)
        decision = ContextDecision(
            timestamp="2026-04-28T12:00:00",
            decision_type=ContextDecisionType.INCLUDE_FULL,
            target_event_id="evt_003",
            reason="high_attention",
            reason_codes=(ReasonCode.MATCHES_CURRENT_GOAL,),
            attention_score=score,
        )
        result = decision.to_dict()
        assert "attention_score" in result
        assert result["attention_score"]["semantic"] == 0.8

    def test_to_dict_with_phase(self) -> None:
        decision = ContextDecision(
            timestamp="2026-04-28T12:00:00",
            decision_type=ContextDecisionType.COMPRESS,
            target_event_id="evt_004",
            reason="phase_affinity_low",
            reason_codes=(ReasonCode.PHASE_AFFINITY_LOW,),
            phase="DEBUGGING",
        )
        result = decision.to_dict()
        assert result["phase"] == "DEBUGGING"

    def test_frozen(self) -> None:
        decision = ContextDecision(
            timestamp="2026-04-28T12:00:00",
            decision_type=ContextDecisionType.INCLUDE_FULL,
            target_event_id="evt_005",
            reason="test",
            reason_codes=(),
        )
        with pytest.raises(AttributeError):
            decision.reason = "changed"  # type: ignore


class TestProjectionReport:
    """Test ProjectionReport dataclass."""

    def test_create_report(self) -> None:
        report = ProjectionReport(
            projection_id="ctxproj_abc123",
            run_id="run_001",
            turn_id="turn_001",
            timestamp="2026-04-28T12:00:00",
            phase="IMPLEMENTATION",
            input_token_budget=100000,
            reserved_output_tokens=18000,
            reserved_tool_tokens=10000,
            candidate_count=50,
            included_count=30,
            compressed_count=10,
            stubbed_count=5,
            excluded_count=5,
        )
        assert report.projection_id == "ctxproj_abc123"
        assert report.included_count == 30

    def test_to_dict(self) -> None:
        report = ProjectionReport(
            projection_id="ctxproj_def456",
            run_id="run_002",
            turn_id="turn_002",
            timestamp="2026-04-28T12:00:00",
            candidate_count=100,
            included_count=60,
            compressed_count=20,
            stubbed_count=10,
            excluded_count=10,
            projection_duration_ms=45.5,
            stage_durations_ms={"WindowCollector": 12.3, "BudgetPlanner": 5.1},
        )
        result = report.to_dict()
        assert result["projection_id"] == "ctxproj_def456"
        assert result["projection_duration_ms"] == 45.5
        assert result["stage_durations_ms"]["WindowCollector"] == 12.3

    def test_to_dict_with_decisions(self) -> None:
        decision = ContextDecision(
            timestamp="2026-04-28T12:00:00",
            decision_type=ContextDecisionType.INCLUDE_FULL,
            target_event_id="evt_001",
            reason="test",
            reason_codes=(),
        )
        report = ProjectionReport(
            projection_id="ctxproj_ghi789",
            run_id="run_003",
            turn_id="turn_003",
            timestamp="2026-04-28T12:00:00",
            decisions=(decision,),
        )
        result = report.to_dict()
        assert len(result["decisions"]) == 1
        assert result["decisions"][0]["target_event_id"] == "evt_001"


class TestContextDecisionLog:
    """Test ContextDecisionLog class."""

    def test_create_log(self) -> None:
        log = ContextDecisionLog()
        assert log.count == 0
        assert log.included_count == 0
        assert log.excluded_count == 0

    def test_record_decision(self) -> None:
        log = ContextDecisionLog()
        decision = create_decision(
            decision_type=ContextDecisionType.INCLUDE_FULL,
            target_event_id="evt_001",
            reason="test",
            reason_codes=(ReasonCode.FORCED_RECENT,),
        )
        log.record(decision)
        assert log.count == 1
        assert log.included_count == 1

    def test_record_many(self) -> None:
        log = ContextDecisionLog()
        decisions = (
            create_decision(
                decision_type=ContextDecisionType.INCLUDE_FULL,
                target_event_id="evt_001",
                reason="test",
                reason_codes=(),
            ),
            create_decision(
                decision_type=ContextDecisionType.EXCLUDE,
                target_event_id="evt_002",
                reason="test",
                reason_codes=(),
            ),
            create_decision(
                decision_type=ContextDecisionType.COMPRESS,
                target_event_id="evt_003",
                reason="test",
                reason_codes=(),
            ),
        )
        log.record_many(decisions)
        assert log.count == 3
        assert log.included_count == 1
        assert log.excluded_count == 1
        assert log.compressed_count == 1

    def test_get_decisions_by_event_id(self) -> None:
        log = ContextDecisionLog()
        log.record(
            create_decision(
                decision_type=ContextDecisionType.INCLUDE_FULL,
                target_event_id="evt_001",
                reason="test1",
                reason_codes=(),
            )
        )
        log.record(
            create_decision(
                decision_type=ContextDecisionType.EXCLUDE,
                target_event_id="evt_002",
                reason="test2",
                reason_codes=(),
            )
        )
        log.record(
            create_decision(
                decision_type=ContextDecisionType.INCLUDE_FULL,
                target_event_id="evt_001",
                reason="test3",
                reason_codes=(),
            )
        )

        results = log.get_decisions(event_id="evt_001")
        assert len(results) == 2
        assert all(d.target_event_id == "evt_001" for d in results)

    def test_get_decisions_by_type(self) -> None:
        log = ContextDecisionLog()
        log.record(
            create_decision(
                decision_type=ContextDecisionType.INCLUDE_FULL,
                target_event_id="evt_001",
                reason="test",
                reason_codes=(),
            )
        )
        log.record(
            create_decision(
                decision_type=ContextDecisionType.EXCLUDE,
                target_event_id="evt_002",
                reason="test",
                reason_codes=(),
            )
        )

        results = log.get_decisions(decision_type=ContextDecisionType.EXCLUDE)
        assert len(results) == 1
        assert results[0].decision_type == ContextDecisionType.EXCLUDE

    def test_get_decisions_by_reason_code(self) -> None:
        log = ContextDecisionLog()
        log.record(
            create_decision(
                decision_type=ContextDecisionType.INCLUDE_FULL,
                target_event_id="evt_001",
                reason="test",
                reason_codes=(ReasonCode.FORCED_RECENT,),
            )
        )
        log.record(
            create_decision(
                decision_type=ContextDecisionType.EXCLUDE,
                target_event_id="evt_002",
                reason="test",
                reason_codes=(ReasonCode.TOKEN_BUDGET_EXCEEDED,),
            )
        )

        results = log.get_decisions(reason_code=ReasonCode.TOKEN_BUDGET_EXCEEDED)
        assert len(results) == 1
        assert ReasonCode.TOKEN_BUDGET_EXCEEDED in results[0].reason_codes

    def test_get_last_n(self) -> None:
        log = ContextDecisionLog()
        for i in range(10):
            log.record(
                create_decision(
                    decision_type=ContextDecisionType.INCLUDE_FULL,
                    target_event_id=f"evt_{i:03d}",
                    reason="test",
                    reason_codes=(),
                )
            )

        results = log.get_last_n(3)
        assert len(results) == 3
        assert results[-1].target_event_id == "evt_009"

    def test_clear(self) -> None:
        log = ContextDecisionLog()
        log.record(
            create_decision(
                decision_type=ContextDecisionType.INCLUDE_FULL,
                target_event_id="evt_001",
                reason="test",
                reason_codes=(),
            )
        )
        assert log.count == 1
        log.clear()
        assert log.count == 0

    def test_max_decisions(self) -> None:
        log = ContextDecisionLog(max_decisions=5)
        for i in range(10):
            log.record(
                create_decision(
                    decision_type=ContextDecisionType.INCLUDE_FULL,
                    target_event_id=f"evt_{i:03d}",
                    reason="test",
                    reason_codes=(),
                )
            )
        assert log.count == 5

    def test_build_projection_report(self) -> None:
        log = ContextDecisionLog()
        log.record(
            create_decision(
                decision_type=ContextDecisionType.INCLUDE_FULL,
                target_event_id="evt_001",
                reason="test",
                reason_codes=(),
            )
        )
        log.record(
            create_decision(
                decision_type=ContextDecisionType.EXCLUDE,
                target_event_id="evt_002",
                reason="test",
                reason_codes=(),
            )
        )
        log.record(
            create_decision(
                decision_type=ContextDecisionType.COMPRESS,
                target_event_id="evt_003",
                reason="test",
                reason_codes=(),
            )
        )

        report = log.build_projection_report(
            projection_id="ctxproj_test",
            run_id="run_001",
            turn_id="turn_001",
        )
        assert report.projection_id == "ctxproj_test"
        assert report.candidate_count == 3
        assert report.included_count == 1
        assert report.excluded_count == 1
        assert report.compressed_count == 1

    def test_to_jsonl(self, tmp_path: Path) -> None:
        log = ContextDecisionLog()
        log.record(
            create_decision(
                decision_type=ContextDecisionType.INCLUDE_FULL,
                target_event_id="evt_001",
                reason="test",
                reason_codes=(ReasonCode.FORCED_RECENT,),
            )
        )

        filepath = tmp_path / "decisions.jsonl"
        log.to_jsonl(filepath)

        assert filepath.exists()
        content = filepath.read_text(encoding="utf-8")
        assert "evt_001" in content
        assert "include_full" in content


class TestCreateDecision:
    """Test create_decision factory function."""

    def test_create_decision(self) -> None:
        decision = create_decision(
            decision_type=ContextDecisionType.INCLUDE_FULL,
            target_event_id="evt_001",
            reason="test_reason",
            reason_codes=(ReasonCode.FORCED_RECENT,),
        )
        assert decision.decision_type == ContextDecisionType.INCLUDE_FULL
        assert decision.target_event_id == "evt_001"
        assert decision.reason == "test_reason"
        assert ReasonCode.FORCED_RECENT in decision.reason_codes
        assert decision.timestamp  # Should be non-empty

    def test_create_decision_with_kwargs(self) -> None:
        decision = create_decision(
            decision_type=ContextDecisionType.EXCLUDE,
            target_event_id="evt_002",
            reason="budget_exceeded",
            reason_codes=(ReasonCode.TOKEN_BUDGET_EXCEEDED,),
            token_budget_before=10000,
            token_budget_after=8000,
            explanation="Test explanation",
        )
        assert decision.token_budget_before == 10000
        assert decision.token_budget_after == 8000
        assert decision.explanation == "Test explanation"
