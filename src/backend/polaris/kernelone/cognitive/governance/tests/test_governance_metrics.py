"""Tests for Governance Metrics - TruthfulnessMetrics, UnderstandingMetrics, EvolutionMetrics, CognitiveMaturityScore."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.governance.evolution_metrics import (
    EvolutionMetrics,
    update_evolution_metrics,
)
from polaris.kernelone.cognitive.governance.maturity_score import (
    CognitiveMaturityScore,
)
from polaris.kernelone.cognitive.governance.truthfulness import (
    TruthfulnessMetrics,
    update_truthfulness_metrics,
)
from polaris.kernelone.cognitive.governance.understanding import (
    UnderstandingMetrics,
    update_understanding_metrics,
)


class TestTruthfulnessMetrics:
    """Tests for TruthfulnessMetrics and updateTruthfulnessMetrics."""

    def test_truthfulness_metrics_default(self) -> None:
        """Verify default TruthfulnessMetrics has expected values."""
        metrics = TruthfulnessMetrics()
        assert metrics.truthfulness_admission_rate == 1.0
        assert metrics.false_consistency_incidents == 0
        assert metrics.total_corrections == 0
        assert metrics.corrections_accepted == 0
        assert metrics.calibration_accuracy == 1.0

    def test_calculate_admission_rate_no_corrections(self) -> None:
        """Admission rate is 1.0 when no corrections needed."""
        metrics = TruthfulnessMetrics()
        assert metrics.calculate_admission_rate() == 1.0

    def test_calculate_admission_rate_with_corrections(self) -> None:
        """Admission rate reflects actual correction acceptance ratio."""
        metrics = TruthfulnessMetrics(
            total_corrections=10,
            corrections_accepted=7,
            corrections_rejected=3,
        )
        assert metrics.calculate_admission_rate() == 0.7

    def test_update_truthfulness_correction_accepted(self) -> None:
        """Test updating metrics when correction is accepted."""
        current = TruthfulnessMetrics()
        updated = update_truthfulness_metrics(current, correction_accepted=True)
        assert updated.total_corrections == 1
        assert updated.corrections_accepted == 1
        assert updated.corrections_rejected == 0

    def test_update_truthfulness_correction_rejected(self) -> None:
        """Test updating metrics when correction is rejected."""
        current = TruthfulnessMetrics()
        updated = update_truthfulness_metrics(current, correction_accepted=False)
        assert updated.total_corrections == 1
        assert updated.corrections_accepted == 0
        assert updated.corrections_rejected == 1
        assert updated.false_consistency_incidents == 1

    def test_update_truthfulness_error_admitted(self) -> None:
        """Test updating metrics when error is admitted."""
        current = TruthfulnessMetrics()
        updated = update_truthfulness_metrics(current, error_admitted=True)
        assert updated.errors_admitted == 1
        assert updated.errors_denied == 0

    def test_update_truthfulness_belief_updated(self) -> None:
        """Test updating metrics when belief is updated."""
        current = TruthfulnessMetrics()
        updated = update_truthfulness_metrics(current, belief_updated=True)
        assert updated.belief_updates_made == 1

    def test_truthfulness_to_dict(self) -> None:
        """Test serialization to dictionary."""
        metrics = TruthfulnessMetrics(
            truthfulness_admission_rate=0.8,
            false_consistency_incidents=2,
            total_corrections=10,
            corrections_accepted=8,
        )
        data = metrics.to_dict()
        assert data["truthfulness_admission_rate"] == 0.8
        assert data["false_consistency_incidents"] == 2
        assert data["total_corrections"] == 10
        assert data["corrections_accepted"] == 8

    def test_truthfulness_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "truthfulness_admission_rate": 0.75,
            "false_consistency_incidents": 3,
            "total_corrections": 20,
            "corrections_accepted": 15,
            "corrections_rejected": 5,
        }
        metrics = TruthfulnessMetrics.from_dict(data)
        assert metrics.truthfulness_admission_rate == 0.75
        assert metrics.false_consistency_incidents == 3
        assert metrics.total_corrections == 20


class TestUnderstandingMetrics:
    """Tests for UnderstandingMetrics and update_understanding_metrics."""

    def test_understanding_metrics_default(self) -> None:
        """Verify default UnderstandingMetrics has expected values."""
        metrics = UnderstandingMetrics()
        assert metrics.intent_inference_accuracy == 1.0
        assert metrics.assumption_verification_rate == 1.0
        assert metrics.surface_understanding_count == 0
        assert metrics.deep_understanding_count == 0

    def test_calculate_understanding_score_default(self) -> None:
        """Default understanding score reflects default intent and assumption scores."""
        metrics = UnderstandingMetrics()
        # intent 1.0 * 0.4 = 0.4, assumption 1.0 * 0.4 = 0.4, no depth bonuses
        assert metrics.calculate_understanding_score() == 0.8

    def test_calculate_understanding_score_with_metrics(self) -> None:
        """Understanding score combines multiple factors."""
        metrics = UnderstandingMetrics(
            intent_inference_accuracy=0.9,
            assumption_verification_rate=0.8,
            deep_understanding_count=2,
            unstated_needs_identified=1,
        )
        score = metrics.calculate_understanding_score()
        # intent 0.9 * 0.4 = 0.36, assumption 0.8 * 0.4 = 0.32, depth 0.04, unstated 0.05
        assert 0.75 < score < 0.8

    def test_update_intent_correct(self) -> None:
        """Test updating metrics when intent is correct."""
        current = UnderstandingMetrics()
        updated = update_understanding_metrics(current, intent_correct=True)
        assert updated.intent_inferences_total == 1
        assert updated.intent_inferences_correct == 1
        assert updated.intent_inference_accuracy == 1.0

    def test_update_intent_incorrect(self) -> None:
        """Test updating metrics when intent is incorrect."""
        current = UnderstandingMetrics()
        updated = update_understanding_metrics(current, intent_correct=False)
        assert updated.intent_inferences_total == 1
        assert updated.intent_inferences_correct == 0
        assert updated.intent_inference_accuracy == 0.0

    def test_update_understanding_depth_surface(self) -> None:
        """Test updating metrics for surface understanding."""
        current = UnderstandingMetrics()
        updated = update_understanding_metrics(current, new_understanding_depth="surface")
        assert updated.surface_understanding_count == 1

    def test_update_understanding_depth_deep(self) -> None:
        """Test updating metrics for deep understanding."""
        current = UnderstandingMetrics()
        updated = update_understanding_metrics(current, new_understanding_depth="deep")
        assert updated.deep_understanding_count == 1

    def test_update_understanding_depth_unstated(self) -> None:
        """Test updating metrics for unstated needs."""
        current = UnderstandingMetrics()
        updated = update_understanding_metrics(current, new_understanding_depth="unstated")
        assert updated.unstated_needs_identified == 1

    def test_update_verification_made_and_honored(self) -> None:
        """Test updating metrics for verification."""
        current = UnderstandingMetrics()
        updated = update_understanding_metrics(current, verification_made=True, verification_honored=True)
        assert updated.verification_requests_made == 1
        assert updated.verification_requests_honored == 1

    def test_understanding_to_dict(self) -> None:
        """Test serialization to dictionary."""
        metrics = UnderstandingMetrics(
            intent_inference_accuracy=0.85,
            deep_understanding_count=5,
        )
        data = metrics.to_dict()
        assert data["intent_inference_accuracy"] == 0.85
        assert data["deep_understanding_count"] == 5

    def test_understanding_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "intent_inference_accuracy": 0.9,
            "intent_inferences_total": 10,
            "intent_inferences_correct": 9,
            "deep_understanding_count": 3,
        }
        metrics = UnderstandingMetrics.from_dict(data)
        assert metrics.intent_inference_accuracy == 0.9
        assert metrics.intent_inferences_total == 10
        assert metrics.deep_understanding_count == 3


class TestEvolutionMetrics:
    """Tests for EvolutionMetrics and update_evolution_metrics."""

    def test_evolution_metrics_default(self) -> None:
        """Verify default EvolutionMetrics has expected values."""
        metrics = EvolutionMetrics()
        assert metrics.mistake_diversity_index == 1.0
        assert metrics.recurrence_rate == 0.0
        assert metrics.total_mistakes == 0
        assert metrics.evolution_velocity == 0.0

    def test_calculate_learning_effectiveness_no_data(self) -> None:
        """Default learning effectiveness reflects perfect diversity and zero recurrence."""
        metrics = EvolutionMetrics()
        # diversity 1.0 * 0.3 = 0.3, recurrence (1-0) * 0.3 = 0.3, no adjustments = 0.4
        # Total = 0.3 + 0.3 + 0.4 = 1.0
        assert metrics.calculate_learning_effectiveness() == 1.0

    def test_calculate_learning_effectiveness_with_data(self) -> None:
        """Learning effectiveness combines diversity, recurrence, and success."""
        metrics = EvolutionMetrics(
            mistake_diversity_index=0.8,
            recurrence_rate=0.2,
            successful_adjustments=8,
            failed_adjustments=2,
        )
        # diversity 0.8 * 0.3 = 0.24, recurrence (1-0.2) * 0.3 = 0.24, success 0.8 * 0.4 = 0.32
        # total = 0.24 + 0.24 + 0.32 = 0.8
        assert metrics.calculate_learning_effectiveness() == 0.8

    def test_calculate_maturity_trend_improving(self) -> None:
        """Maturity trend is improving when success ratio > 0.66."""
        metrics = EvolutionMetrics(successful_adjustments=8, failed_adjustments=2)
        assert metrics.calculate_maturity_trend() == "improving"

    def test_calculate_maturity_trend_degrading(self) -> None:
        """Maturity trend is degrading when success ratio < 0.33."""
        metrics = EvolutionMetrics(successful_adjustments=2, failed_adjustments=8)
        assert metrics.calculate_maturity_trend() == "degrading"

    def test_calculate_maturity_trend_stable(self) -> None:
        """Maturity trend is stable when success ratio between 0.33 and 0.66."""
        metrics = EvolutionMetrics(successful_adjustments=5, failed_adjustments=5)
        assert metrics.calculate_maturity_trend() == "stable"

    def test_update_mistake_made(self) -> None:
        """Test updating metrics when mistake is made."""
        current = EvolutionMetrics()
        updated = update_evolution_metrics(current, mistake_made="syntax_error")
        assert updated.total_mistakes == 1
        assert updated.unique_mistake_types == 1
        assert "syntax_error" in updated.mistake_types_seen

    def test_update_mistake_recurring(self) -> None:
        """Test updating metrics when recurring mistake is made."""
        current = EvolutionMetrics()
        updated = update_evolution_metrics(current, mistake_made="syntax_error", mistake_recurring=True)
        assert updated.total_mistakes == 1
        assert updated.recurring_mistake_count == 1

    def test_update_belief_updated(self) -> None:
        """Test updating metrics when belief is updated."""
        current = EvolutionMetrics()
        updated = update_evolution_metrics(current, belief_updated=True)
        assert updated.beliefs_updated_total == 1

    def test_update_adjustment_success(self) -> None:
        """Test updating metrics for successful adjustment."""
        current = EvolutionMetrics()
        updated = update_evolution_metrics(current, adjustment_outcome="success")
        assert updated.successful_adjustments == 1

    def test_update_adjustment_failed(self) -> None:
        """Test updating metrics for failed adjustment."""
        current = EvolutionMetrics()
        updated = update_evolution_metrics(current, adjustment_outcome="failed")
        assert updated.failed_adjustments == 1

    def test_update_rule_learned(self) -> None:
        """Test updating metrics when rule is learned."""
        current = EvolutionMetrics()
        updated = update_evolution_metrics(current, rule_learned=True)
        assert updated.rules_learned_count == 1

    def test_update_bias_detected(self) -> None:
        """Test updating metrics when bias is detected."""
        current = EvolutionMetrics()
        updated = update_evolution_metrics(current, bias_detected=True)
        assert updated.biases_detected_total == 1

    def test_update_bias_mitigated(self) -> None:
        """Test updating metrics when bias is mitigated."""
        current = EvolutionMetrics()
        updated = update_evolution_metrics(current, bias_mitigated=True)
        assert updated.biases_mitigated_count == 1

    def test_evolution_to_dict(self) -> None:
        """Test serialization to dictionary."""
        metrics = EvolutionMetrics(
            mistake_diversity_index=0.75,
            recurrence_rate=0.15,
            successful_adjustments=10,
        )
        data = metrics.to_dict()
        assert data["mistake_diversity_index"] == 0.75
        assert data["recurrence_rate"] == 0.15
        assert data["successful_adjustments"] == 10

    def test_evolution_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "mistake_diversity_index": 0.9,
            "recurrence_rate": 0.1,
            "total_mistakes": 20,
            "successful_adjustments": 15,
        }
        metrics = EvolutionMetrics.from_dict(data)
        assert metrics.mistake_diversity_index == 0.9
        assert metrics.recurrence_rate == 0.1
        assert metrics.total_mistakes == 20


class TestCognitiveMaturityScore:
    """Tests for CognitiveMaturityScore."""

    def test_cognitive_maturity_score_default(self) -> None:
        """Verify default CognitiveMaturityScore has zero (uncalibrated) values."""
        score = CognitiveMaturityScore()
        assert score.truthfulness_score == 0.0
        assert score.understanding_score == 0.0
        assert score.evolution_score == 0.0
        assert score.overall_score == 0.0

    def test_overall_score_calculation(self) -> None:
        """Overall score is weighted average of component scores."""
        score = CognitiveMaturityScore(
            truthfulness_score=80.0,
            understanding_score=90.0,
            evolution_score=70.0,
        )
        # 80 * 0.35 + 90 * 0.35 + 70 * 0.30 = 28 + 31.5 + 21 = 80.5
        assert score.overall_score == 80.5

    def test_maturity_level_tool(self) -> None:
        """Score 0-20 should be Tool level."""
        score = CognitiveMaturityScore(
            truthfulness_score=15.0,
            understanding_score=15.0,
            evolution_score=15.0,
        )
        assert score.maturity_level == "Tool"

    def test_maturity_level_aware(self) -> None:
        """Score 21-40 should be Aware level."""
        score = CognitiveMaturityScore(
            truthfulness_score=30.0,
            understanding_score=30.0,
            evolution_score=30.0,
        )
        assert score.maturity_level == "Aware"

    def test_maturity_level_reflective(self) -> None:
        """Score 41-60 should be Reflective level."""
        score = CognitiveMaturityScore(
            truthfulness_score=50.0,
            understanding_score=50.0,
            evolution_score=50.0,
        )
        assert score.maturity_level == "Reflective"

    def test_maturity_level_adaptive(self) -> None:
        """Score 61-80 should be Adaptive level."""
        score = CognitiveMaturityScore(
            truthfulness_score=70.0,
            understanding_score=70.0,
            evolution_score=70.0,
        )
        assert score.maturity_level == "Adaptive"

    def test_maturity_level_evolutionary(self) -> None:
        """Score 81-100 should be Evolutionary level."""
        score = CognitiveMaturityScore(
            truthfulness_score=90.0,
            understanding_score=90.0,
            evolution_score=90.0,
        )
        assert score.maturity_level == "Evolutionary"

    def test_maturity_description_exists(self) -> None:
        """Each maturity level should have a description."""
        for level in ["Tool", "Aware", "Reflective", "Adaptive", "Evolutionary"]:
            metrics = CognitiveMaturityScore(
                truthfulness_score=10.0 if level == "Tool" else 50.0,
                understanding_score=10.0 if level == "Tool" else 50.0,
                evolution_score=10.0 if level == "Tool" else 50.0,
            )
            desc = metrics.maturity_description
            assert desc is not None
            assert len(desc) > 0

    def test_from_metrics(self) -> None:
        """Create maturity score from component metrics using from_metrics factory."""
        truthfulness = TruthfulnessMetrics(truthfulness_admission_rate=0.8)
        understanding = UnderstandingMetrics(
            intent_inference_accuracy=0.9,
            deep_understanding_count=2,
            unstated_needs_identified=1,
        )
        evo_metrics = EvolutionMetrics(
            mistake_diversity_index=0.8,
            recurrence_rate=0.2,
            successful_adjustments=7,
            failed_adjustments=3,
        )

        # Use from_metrics factory method
        score = CognitiveMaturityScore.from_metrics(truthfulness, understanding, evo_metrics)

        assert score.truthfulness_score == 80.0  # 0.8 * 100
        # understanding: 0.9*0.4 + 1.0*0.4 + 0.04 + 0.05 = 0.85 -> 85
        assert score.understanding_score == pytest.approx(85.0, abs=1.0)
        assert 75.0 <= score.evolution_score <= 80.0

    def test_with_trend(self) -> None:
        """Test trend calculation between two scores."""
        previous = CognitiveMaturityScore(
            truthfulness_score=70.0,
            understanding_score=80.0,
            evolution_score=60.0,
        )
        current = CognitiveMaturityScore(
            truthfulness_score=80.0,
            understanding_score=85.0,
            evolution_score=65.0,
        )
        trend = current.with_trend(previous)

        assert trend["current_score"] == current.overall_score
        assert trend["previous_score"] == previous.overall_score
        assert trend["change"] > 0  # Improving
        assert trend["trend"] == "improving"
        assert trend["truthfulness_change"] == 10.0

    def test_with_trend_degrading(self) -> None:
        """Test trend calculation for degrading scores."""
        previous = CognitiveMaturityScore(
            truthfulness_score=80.0,
            understanding_score=80.0,
            evolution_score=80.0,
        )
        current = CognitiveMaturityScore(
            truthfulness_score=60.0,
            understanding_score=60.0,
            evolution_score=60.0,
        )
        trend = current.with_trend(previous)

        assert trend["trend"] == "degrading"
        assert trend["change"] < 0

    def test_with_trend_stable(self) -> None:
        """Test trend calculation for stable scores."""
        previous = CognitiveMaturityScore(
            truthfulness_score=75.0,
            understanding_score=75.0,
            evolution_score=75.0,
        )
        current = CognitiveMaturityScore(
            truthfulness_score=74.0,
            understanding_score=74.0,
            evolution_score=74.0,
        )
        trend = current.with_trend(previous)

        assert trend["trend"] == "stable"
        assert abs(trend["change"]) <= 2

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        score = CognitiveMaturityScore(
            truthfulness_score=85.0,
            understanding_score=90.0,
            evolution_score=80.0,
        )
        data = score.to_dict()

        assert "truthfulness_score" in data
        assert "understanding_score" in data
        assert "evolution_score" in data
        assert "overall_score" in data
        assert "maturity_level" in data
        assert "maturity_description" in data
        assert "is_calibrated" in data
        assert data["is_calibrated"] is True  # score > 0

    def test_default(self) -> None:
        """Test default factory method returns uncalibrated zero scores."""
        score = CognitiveMaturityScore.default()
        assert score.overall_score == 0.0
        assert score.maturity_level == "Tool"
        assert score.is_calibrated is False

    def test_weights_sum_to_one(self) -> None:
        """Verify weights sum to 1.0."""
        score = CognitiveMaturityScore()
        total = score.TRUTHFULNESS_WEIGHT + score.UNDERSTANDING_WEIGHT + score.EVOLUTION_WEIGHT
        assert total == 1.0

    def test_is_calibrated_false_when_zero(self) -> None:
        """Uncalibrated (zero) score reports is_calibrated = False."""
        score = CognitiveMaturityScore()
        assert score.is_calibrated is False

    def test_is_calibrated_true_when_nonzero(self) -> None:
        """Any positive overall_score means the score has been calibrated."""
        score = CognitiveMaturityScore(
            truthfulness_score=10.0,
            understanding_score=10.0,
            evolution_score=10.0,
        )
        assert score.overall_score == 10.0
        assert score.is_calibrated is True

    def test_to_dict_includes_is_calibrated(self) -> None:
        """to_dict() must include the is_calibrated field."""
        uncalibrated = CognitiveMaturityScore()
        data = uncalibrated.to_dict()
        assert "is_calibrated" in data
        assert data["is_calibrated"] is False

        calibrated = CognitiveMaturityScore(
            truthfulness_score=50.0,
            understanding_score=50.0,
            evolution_score=50.0,
        )
        data = calibrated.to_dict()
        assert data["is_calibrated"] is True
