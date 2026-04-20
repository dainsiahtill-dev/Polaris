"""Truthfulness Metrics - Tracks Law L1: Truthfulness > Consistency."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TruthfulnessMetrics:
    """Metrics for tracking truthfulness (Law L1).

    Law L1: Truthfulness > Consistency
    -宁可承认错误也不维护虚假一致性

    Tracks:
    - truthfulness_admission_rate: How often we admit errors vs maintain false consistency
    - false_consistency_incidents: Times we maintained false consistency
    - calibration_accuracy: How well our confidence matches outcomes
    """

    # Admission vs False Consistency
    truthfulness_admission_rate: float = 1.0  # 0.0-1.0, 1.0 = always truthful
    false_consistency_incidents: int = 0  # Times maintained false consistency

    # Correction tracking
    total_corrections: int = 0
    corrections_accepted: int = 0
    corrections_rejected: int = 0

    # Confidence calibration
    calibration_accuracy: float = 1.0  # How well confidence matches outcomes

    # Additional tracking
    errors_admitted: int = 0
    errors_denied: int = 0
    belief_updates_made: int = 0

    # Trend data
    recent_admission_rates: tuple[float, ...] = field(default_factory=tuple)

    def calculate_admission_rate(self) -> float:
        """Calculate truthfulness admission rate.

        Returns:
            Ratio of corrections accepted to total corrections needed
        """
        if self.total_corrections == 0:
            return 1.0  # No corrections needed = truthful

        return self.corrections_accepted / self.total_corrections

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "truthfulness_admission_rate": self.truthfulness_admission_rate,
            "false_consistency_incidents": self.false_consistency_incidents,
            "total_corrections": self.total_corrections,
            "corrections_accepted": self.corrections_accepted,
            "corrections_rejected": self.corrections_rejected,
            "calibration_accuracy": self.calibration_accuracy,
            "errors_admitted": self.errors_admitted,
            "errors_denied": self.errors_denied,
            "belief_updates_made": self.belief_updates_made,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> TruthfulnessMetrics:
        """Create from dictionary."""
        return TruthfulnessMetrics(
            truthfulness_admission_rate=data.get("truthfulness_admission_rate", 1.0),
            false_consistency_incidents=data.get("false_consistency_incidents", 0),
            total_corrections=data.get("total_corrections", 0),
            corrections_accepted=data.get("corrections_accepted", 0),
            corrections_rejected=data.get("corrections_rejected", 0),
            calibration_accuracy=data.get("calibration_accuracy", 1.0),
            errors_admitted=data.get("errors_admitted", 0),
            errors_denied=data.get("errors_denied", 0),
            belief_updates_made=data.get("belief_updates_made", 0),
        )


def update_truthfulness_metrics(
    current: TruthfulnessMetrics,
    correction_accepted: bool | None = None,
    error_admitted: bool | None = None,
    belief_updated: bool | None = None,
    new_calibration: float | None = None,
) -> TruthfulnessMetrics:
    """Update truthfulness metrics based on events.

    Args:
        current: Current metrics state
        correction_accepted: True if user correction was accepted
        error_admitted: True if an error was explicitly admitted
        belief_updated: True if belief was updated based on new info
        new_calibration: New calibration accuracy value

    Returns:
        Updated TruthfulnessMetrics
    """
    admission_rate = current.truthfulness_admission_rate
    false_incidents = current.false_consistency_incidents
    total_corrections = current.total_corrections
    corrections_accepted = current.corrections_accepted
    corrections_rejected = current.corrections_rejected
    errors_admitted = current.errors_admitted
    errors_denied = current.errors_denied
    belief_updates = current.belief_updates_made
    recent_rates = list(current.recent_admission_rates)

    if correction_accepted is not None:
        total_corrections += 1
        if correction_accepted:
            corrections_accepted += 1
        else:
            corrections_rejected += 1
            false_incidents += 1

    if error_admitted is not None:
        if error_admitted:
            errors_admitted += 1
        else:
            errors_denied += 1

    if belief_updated:
        belief_updates += 1

    # Update admission rate
    if total_corrections > 0:
        admission_rate = corrections_accepted / total_corrections

    # Keep last 10 admission rates for trend
    recent_rates.append(admission_rate)
    recent_rates = recent_rates[-10:]

    return TruthfulnessMetrics(
        truthfulness_admission_rate=admission_rate,
        false_consistency_incidents=false_incidents,
        total_corrections=total_corrections,
        corrections_accepted=corrections_accepted,
        corrections_rejected=corrections_rejected,
        calibration_accuracy=new_calibration if new_calibration is not None else current.calibration_accuracy,
        errors_admitted=errors_admitted,
        errors_denied=errors_denied,
        belief_updates_made=belief_updates,
        recent_admission_rates=tuple(recent_rates),
    )
