"""Uncertainty Quantification - Drives execution path selection."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.cognitive.perception.models import UncertaintyAssessment


class UncertaintyQuantifier:
    """
    Quantifies uncertainty to determine which execution path to use.

    Path Selection:
    - uncertainty < 0.3: BYPASS (direct execution)
    - uncertainty < 0.6: FAST_THINK (thinking phase only)
    - uncertainty >= 0.6: FULL_PIPE (all cognitive protocols)
    """

    def __init__(
        self,
        bypass_threshold: float = 0.3,
        fast_think_threshold: float = 0.6,
        calibration_window: int = 50,
    ) -> None:
        self._bypass_threshold = bypass_threshold
        self._fast_think_threshold = fast_think_threshold
        self._history: list[tuple[float, bool]] = []  # (predicted, was_correct)
        self._calibration_window = calibration_window

    def record_outcome(self, predicted_uncertainty: float, was_correct: bool) -> None:
        """Record a prediction result for dynamic calibration.

        Args:
            predicted_uncertainty: The uncertainty score that was predicted.
            was_correct: Whether the prediction turned out to be correct.
        """
        self._history.append((predicted_uncertainty, was_correct))
        if len(self._history) > self._calibration_window:
            self._history = self._history[-self._calibration_window :]

    def _apply_calibration(self, raw_uncertainty: float) -> float:
        """Dynamically adjust uncertainty based on historical accuracy.

        Two calibration modes:
        1. Over-conservative: high-uncertainty predictions are frequently correct
           (accuracy > 70%) -> reduce uncertainty.
        2. Over-confident: low-uncertainty predictions are frequently wrong
           (accuracy < 50%) -> increase uncertainty.

        Args:
            raw_uncertainty: The raw uncertainty score before calibration.

        Returns:
            Calibrated uncertainty score, clamped to [0.0, 1.0].
        """
        if len(self._history) < 10:
            return raw_uncertainty  # Insufficient samples

        # --- Over-conservative detection ---
        # High-uncertainty predictions that were actually correct
        high_uncertainty_correct = sum(1 for u, c in self._history if u > 0.6 and c)
        high_uncertainty_total = sum(1 for u, _ in self._history if u > 0.6)

        if high_uncertainty_total > 0:
            calibration_ratio = high_uncertainty_correct / high_uncertainty_total
            # If high-uncertainty predictions are frequently correct -> possibly
            # overly conservative -> reduce uncertainty slightly
            if calibration_ratio > 0.7:
                raw_uncertainty *= 0.9

        # --- Over-confident detection ---
        # Low-uncertainty predictions that turned out wrong
        low_uncertainty_correct = sum(1 for u, c in self._history if u <= 0.3 and c)
        low_uncertainty_total = sum(1 for u, _ in self._history if u <= 0.3)

        if low_uncertainty_total >= 5:
            low_unc_accuracy = low_uncertainty_correct / low_uncertainty_total
            if low_unc_accuracy < 0.5:
                raw_uncertainty = min(raw_uncertainty * 1.2, 1.0)

        return max(0.0, min(1.0, raw_uncertainty))

    def get_calibration_stats(self) -> dict[str, float]:
        """Get calibration statistics from historical outcomes.

        Returns:
            Dictionary with calibration statistics including sample count,
            accuracy ratios, and calibration bias.
        """
        if not self._history:
            return {
                "sample_count": 0.0,
                "overall_accuracy": 0.0,
                "high_uncertainty_accuracy": 0.0,
                "low_uncertainty_accuracy": 0.0,
                "calibration_bias": 0.0,
            }

        total = len(self._history)
        total_correct = sum(1 for _, c in self._history if c)

        high_unc_correct = sum(1 for u, c in self._history if u > 0.6 and c)
        high_unc_total = sum(1 for u, _ in self._history if u > 0.6)

        low_unc_correct = sum(1 for u, c in self._history if u <= 0.6 and c)
        low_unc_total = sum(1 for u, _ in self._history if u <= 0.6)

        overall_accuracy = total_correct / total if total > 0 else 0.0
        high_unc_accuracy = high_unc_correct / high_unc_total if high_unc_total > 0 else 0.0
        low_unc_accuracy = low_unc_correct / low_unc_total if low_unc_total > 0 else 0.0

        # Calibration bias: positive = over-conservative, negative = over-confident
        calibration_bias = high_unc_accuracy - low_unc_accuracy

        return {
            "sample_count": float(total),
            "overall_accuracy": overall_accuracy,
            "high_uncertainty_accuracy": high_unc_accuracy,
            "low_uncertainty_accuracy": low_unc_accuracy,
            "calibration_bias": calibration_bias,
        }

    def quantify(
        self,
        intent_confidence: float,
        context_confidence: float | None = None,
        uncertainty_factors: tuple[str, ...] = (),
        working_state: Any = None,
    ) -> UncertaintyAssessment:
        """Calculate overall uncertainty and recommend execution path."""

        # Base uncertainty = 1 - intent_confidence
        base_uncertainty = 1.0 - intent_confidence

        # Context uncertainty reduces confidence
        if context_confidence is not None:
            context_uncertainty = 1.0 - context_confidence
            base_uncertainty = (base_uncertainty + context_uncertainty) / 2

        # Factor-based adjustments
        factor_penalty = len(uncertainty_factors) * 0.05
        base_uncertainty = min(base_uncertainty + factor_penalty, 1.0)

        # Short context increases uncertainty
        if working_state and len(str(working_state)) < 100:
            base_uncertainty = min(base_uncertainty + 0.1, 1.0)

        # Apply dynamic calibration
        base_uncertainty = self._apply_calibration(base_uncertainty)

        # Calculate confidence interval
        confidence_lower = max(0.0, intent_confidence - base_uncertainty * 0.3)
        confidence_upper = min(1.0, intent_confidence + base_uncertainty * 0.1)

        # Determine recommended action
        if base_uncertainty < self._bypass_threshold:
            action = "bypass"
        elif base_uncertainty < self._fast_think_threshold:
            action = "fast_think"
        else:
            action = "full_pipe"

        return UncertaintyAssessment(
            uncertainty_score=base_uncertainty,
            uncertainty_factors=uncertainty_factors,
            confidence_lower=confidence_lower,
            confidence_upper=confidence_upper,
            recommended_action=action,
        )
