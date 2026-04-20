"""Understanding Metrics - Tracks Law L2: Understanding > Execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UnderstandingMetrics:
    """Metrics for tracking understanding (Law L2).

    Law L2: Understanding > Execution
    - 必须理解意图后才能执行，必须在执行中监控理解

    Tracks:
    - intent_inference_accuracy: How often we correctly inferred intent
    - assumption_verification_rate: How often we verified assumptions
    - surface/deep/unstated understanding levels
    - verification request honoring
    """

    # Intent inference accuracy
    intent_inference_accuracy: float = 1.0  # 0.0-1.0
    intent_inferences_total: int = 0
    intent_inferences_correct: int = 0

    # Assumption verification
    assumption_verification_rate: float = 1.0  # 0.0-1.0
    assumptions_total: int = 0
    assumptions_verified: int = 0

    # Understanding depth
    surface_understanding_count: int = 0
    deep_understanding_count: int = 0
    unstated_needs_identified: int = 0

    # Verification tracking
    verification_requests_made: int = 0
    verification_requests_honored: int = 0

    # Misunderstanding tracking
    misunderstandings_detected: int = 0
    corrections_due_to_misunderstanding: int = 0

    # Confidence tracking
    average_understanding_confidence: float = 1.0

    def calculate_understanding_score(self) -> float:
        """Calculate overall understanding score.

        Combines intent inference, assumption verification, and depth metrics.

        Returns:
            Weighted score from 0.0-1.0
        """
        intent_score = self.intent_inference_accuracy
        assumption_score = self.assumption_verification_rate

        # Depth bonus (up to 0.1)
        depth_bonus = min(0.1, self.deep_understanding_count * 0.02)
        unstated_bonus = min(0.1, self.unstated_needs_identified * 0.05)

        return min(1.0, (intent_score * 0.4) + (assumption_score * 0.4) + depth_bonus + unstated_bonus)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "intent_inference_accuracy": self.intent_inference_accuracy,
            "intent_inferences_total": self.intent_inferences_total,
            "intent_inferences_correct": self.intent_inferences_correct,
            "assumption_verification_rate": self.assumption_verification_rate,
            "assumptions_total": self.assumptions_total,
            "assumptions_verified": self.assumptions_verified,
            "surface_understanding_count": self.surface_understanding_count,
            "deep_understanding_count": self.deep_understanding_count,
            "unstated_needs_identified": self.unstated_needs_identified,
            "verification_requests_made": self.verification_requests_made,
            "verification_requests_honored": self.verification_requests_honored,
            "misunderstandings_detected": self.misunderstandings_detected,
            "corrections_due_to_misunderstanding": self.corrections_due_to_misunderstanding,
            "average_understanding_confidence": self.average_understanding_confidence,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> UnderstandingMetrics:
        """Create from dictionary."""
        return UnderstandingMetrics(
            intent_inference_accuracy=data.get("intent_inference_accuracy", 1.0),
            intent_inferences_total=data.get("intent_inferences_total", 0),
            intent_inferences_correct=data.get("intent_inferences_correct", 0),
            assumption_verification_rate=data.get("assumption_verification_rate", 1.0),
            assumptions_total=data.get("assumptions_total", 0),
            assumptions_verified=data.get("assumptions_verified", 0),
            surface_understanding_count=data.get("surface_understanding_count", 0),
            deep_understanding_count=data.get("deep_understanding_count", 0),
            unstated_needs_identified=data.get("unstated_needs_identified", 0),
            verification_requests_made=data.get("verification_requests_made", 0),
            verification_requests_honored=data.get("verification_requests_honored", 0),
            misunderstandings_detected=data.get("misunderstandings_detected", 0),
            corrections_due_to_misunderstanding=data.get("corrections_due_to_misunderstanding", 0),
            average_understanding_confidence=data.get("average_understanding_confidence", 1.0),
        )


def update_understanding_metrics(
    current: UnderstandingMetrics,
    intent_correct: bool | None = None,
    assumption_verified: bool | None = None,
    new_understanding_depth: str | None = None,  # "surface", "deep", or "unstated"
    verification_made: bool | None = None,
    verification_honored: bool | None = None,
    misunderstanding_detected: bool | None = None,
    correction_made: bool | None = None,
    new_confidence: float | None = None,
) -> UnderstandingMetrics:
    """Update understanding metrics based on events.

    Args:
        current: Current metrics state
        intent_correct: True if intent inference was correct
        assumption_verified: True if assumption was verified
        new_understanding_depth: Level of understanding achieved
        verification_made: True if verification was requested
        verification_honored: True if verification request was honored
        misunderstanding_detected: True if misunderstanding was detected
        correction_made: True if correction was made due to misunderstanding
        new_confidence: New understanding confidence value

    Returns:
        Updated UnderstandingMetrics
    """
    intent_total = current.intent_inferences_total
    intent_correct_count = current.intent_inferences_correct
    assumption_total = current.assumptions_total
    assumption_verified_count = current.assumptions_verified

    surface_count = current.surface_understanding_count
    deep_count = current.deep_understanding_count
    unstated_count = current.unstated_needs_identified

    verification_made_count = current.verification_requests_made
    verification_honored_count = current.verification_requests_honored

    misunderstanding_count = current.misunderstandings_detected
    correction_count = current.corrections_due_to_misunderstanding

    # Update based on events
    if intent_correct is not None:
        intent_total += 1
        if intent_correct:
            intent_correct_count += 1

    if assumption_verified is not None:
        assumption_total += 1
        if assumption_verified:
            assumption_verified_count += 1

    if new_understanding_depth == "surface":
        surface_count += 1
    elif new_understanding_depth == "deep":
        deep_count += 1
    elif new_understanding_depth == "unstated":
        unstated_count += 1

    if verification_made:
        verification_made_count += 1
    if verification_honored:
        verification_honored_count += 1

    if misunderstanding_detected:
        misunderstanding_count += 1
    if correction_made:
        correction_count += 1

    # Calculate rates
    intent_accuracy = intent_correct_count / intent_total if intent_total > 0 else 1.0
    assumption_rate = assumption_verified_count / assumption_total if assumption_total > 0 else 1.0

    return UnderstandingMetrics(
        intent_inference_accuracy=intent_accuracy,
        intent_inferences_total=intent_total,
        intent_inferences_correct=intent_correct_count,
        assumption_verification_rate=assumption_rate,
        assumptions_total=assumption_total,
        assumptions_verified=assumption_verified_count,
        surface_understanding_count=surface_count,
        deep_understanding_count=deep_count,
        unstated_needs_identified=unstated_count,
        verification_requests_made=verification_made_count,
        verification_requests_honored=verification_honored_count,
        misunderstandings_detected=misunderstanding_count,
        corrections_due_to_misunderstanding=correction_count,
        average_understanding_confidence=new_confidence
        if new_confidence is not None
        else current.average_understanding_confidence,
    )
