"""Unit tests for UncertaintyQuantifier - Dynamic calibration feedback loop."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.perception.uncertainty import UncertaintyQuantifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feed_history(
    quantifier: UncertaintyQuantifier,
    records: list[tuple[float, bool]],
) -> None:
    """Bulk-feed prediction records into the quantifier."""
    for predicted_uncertainty, was_correct in records:
        quantifier.record_outcome(predicted_uncertainty, was_correct)


# ---------------------------------------------------------------------------
# Calibration adjustment tests
# ---------------------------------------------------------------------------


class TestCalibrationOverconfidentAdjustment:
    """Low uncertainty + low correctness should push uncertainty UP."""

    def test_low_unc_with_poor_accuracy_raises_uncertainty(self) -> None:
        quantifier = UncertaintyQuantifier()
        # 10 samples: all low-uncertainty (0.1), mostly wrong (8/10 incorrect)
        records = [(0.1, False)] * 8 + [(0.1, True)] * 2
        _feed_history(quantifier, records)

        raw = 0.25
        calibrated = quantifier._apply_calibration(raw)

        # Over-confident correction: raw * 1.2 = 0.30
        assert calibrated == pytest.approx(0.30, abs=1e-6)

    def test_calibration_stats_reflect_overconfidence(self) -> None:
        quantifier = UncertaintyQuantifier()
        records = [(0.1, False)] * 8 + [(0.1, True)] * 2
        _feed_history(quantifier, records)

        stats = quantifier.get_calibration_stats()
        assert stats["sample_count"] == 10.0
        assert stats["low_uncertainty_accuracy"] == pytest.approx(0.2, abs=1e-6)


class TestCalibrationConservativeAdjustment:
    """High uncertainty + high correctness should push uncertainty DOWN."""

    def test_high_unc_with_good_accuracy_lowers_uncertainty(self) -> None:
        quantifier = UncertaintyQuantifier()
        # 10 samples: all high-uncertainty (0.8), mostly correct (8/10)
        records = [(0.8, True)] * 8 + [(0.8, False)] * 2
        _feed_history(quantifier, records)

        raw = 0.7
        calibrated = quantifier._apply_calibration(raw)

        # Over-conservative correction: raw * 0.9 = 0.63
        assert calibrated == pytest.approx(0.63, abs=1e-6)

    def test_mixed_high_unc_no_adjustment_when_accuracy_moderate(self) -> None:
        quantifier = UncertaintyQuantifier()
        # 10 samples: high-uncertainty, exactly 50% correct (ratio 0.5 < 0.7)
        records = [(0.8, True)] * 5 + [(0.8, False)] * 5
        _feed_history(quantifier, records)

        raw = 0.7
        calibrated = quantifier._apply_calibration(raw)

        # No over-conservative correction, no over-confident correction
        assert calibrated == pytest.approx(0.7, abs=1e-6)


class TestCalibrationInsufficientSamples:
    """Fewer than 10 samples => no calibration adjustment."""

    def test_nine_samples_no_adjustment(self) -> None:
        quantifier = UncertaintyQuantifier()
        # Only 9 samples -- below threshold
        records = [(0.8, True)] * 9
        _feed_history(quantifier, records)

        raw = 0.5
        calibrated = quantifier._apply_calibration(raw)
        assert calibrated == pytest.approx(0.5, abs=1e-6)

    def test_empty_history_no_adjustment(self) -> None:
        quantifier = UncertaintyQuantifier()
        raw = 0.4
        calibrated = quantifier._apply_calibration(raw)
        assert calibrated == pytest.approx(0.4, abs=1e-6)


class TestRecordOutcomeWindow:
    """History is automatically trimmed to calibration_window."""

    def test_history_trimmed_to_window(self) -> None:
        window = 20
        quantifier = UncertaintyQuantifier(calibration_window=window)

        # Insert 30 records
        for i in range(30):
            quantifier.record_outcome(0.5, was_correct=bool(i % 2))

        assert len(quantifier._history) == window

    def test_history_keeps_latest_records(self) -> None:
        window = 5
        quantifier = UncertaintyQuantifier(calibration_window=window)

        for i in range(10):
            quantifier.record_outcome(float(i) / 10.0, was_correct=True)

        # Last 5 records should remain: indices 5-9
        uncertainties = [u for u, _ in quantifier._history]
        assert uncertainties == pytest.approx([0.5, 0.6, 0.7, 0.8, 0.9], abs=1e-6)

    def test_default_window_is_50(self) -> None:
        quantifier = UncertaintyQuantifier()
        assert quantifier._calibration_window == 50

    def test_small_window_zero_below_threshold(self) -> None:
        """With window=3, 10 inserts => only 3 remain, below _apply_calibration
        minimum of 10, so calibration is a no-op."""
        quantifier = UncertaintyQuantifier(calibration_window=3)
        for _ in range(10):
            quantifier.record_outcome(0.1, was_correct=False)

        raw = 0.5
        calibrated = quantifier._apply_calibration(raw)
        assert calibrated == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------------------
# Integration: quantifier.quantify sees calibrated result
# ---------------------------------------------------------------------------


class TestQuantifyUsesCalibration:
    """Verify that quantify() produces a calibrated uncertainty score."""

    def test_overconfident_history_increases_quantified_uncertainty(self) -> None:
        quantifier = UncertaintyQuantifier()
        # Feed over-confident history: low uncertainty predictions that were wrong
        records = [(0.1, False)] * 8 + [(0.1, True)] * 2
        _feed_history(quantifier, records)

        # High intent confidence => low raw uncertainty, but calibration bumps it up
        assessment = quantifier.quantify(intent_confidence=0.9)
        # raw = 1.0 - 0.9 = 0.1; calibrated = 0.1 * 1.2 = 0.12
        assert assessment.uncertainty_score == pytest.approx(0.12, abs=1e-6)

    def test_conservative_history_decreases_quantified_uncertainty(self) -> None:
        quantifier = UncertaintyQuantifier()
        # Feed over-conservative history: high uncertainty predictions that were correct
        records = [(0.8, True)] * 8 + [(0.8, False)] * 2
        _feed_history(quantifier, records)

        # Moderate intent confidence => moderate raw uncertainty
        assessment = quantifier.quantify(intent_confidence=0.4)
        # raw = 1.0 - 0.4 = 0.6; calibrated = 0.6 * 0.9 = 0.54
        assert assessment.uncertainty_score == pytest.approx(0.54, abs=1e-6)
