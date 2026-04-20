"""Evolution Metrics - Tracks Law L3: Evolution > Correctness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvolutionMetrics:
    """Metrics for tracking evolution (Law L3).

    Law L3: Evolution > Correctness
    - 没有"永远正确"，只有"持续趋向正确"

    Tracks:
    - mistake_diversity_index: How varied our mistakes are (higher = learning more)
    - recurrence_rate: How often we repeat the same mistakes
    - evolution_velocity: Rate of belief updates per session
    - learning effectiveness
    """

    # Mistake tracking
    mistake_diversity_index: float = 1.0  # 0.0-1.0, higher = more diverse mistakes
    recurrence_rate: float = 0.0  # 0.0-1.0, lower = better (less repetition)
    total_mistakes: int = 0
    unique_mistake_types: int = 0
    recurring_mistake_count: int = 0

    # Evolution velocity
    evolution_velocity: float = 0.0  # Beliefs updated per session
    beliefs_updated_total: int = 0
    sessions_count: int = 1

    # Adjustment tracking
    successful_adjustments: int = 0
    failed_adjustments: int = 0
    neutral_adjustments: int = 0

    # Learning metrics
    rules_learned_count: int = 0
    patterns_identified_count: int = 0
    knowledge_gaps_filled: int = 0
    boundaries_refined_count: int = 0

    # Bias detection
    biases_detected_total: int = 0
    biases_mitigated_count: int = 0

    # Mistake type tracking (for diversity calculation)
    mistake_types_seen: tuple[str, ...] = field(default_factory=tuple)

    def calculate_maturity_trend(self) -> str:
        """Calculate whether maturity is improving, stable, or degrading.

        Returns:
            "improving", "stable", or "degrading"
        """
        total_adjustments = self.successful_adjustments + self.failed_adjustments

        if total_adjustments == 0:
            return "stable"

        success_ratio = self.successful_adjustments / total_adjustments

        if success_ratio > 0.66:
            return "improving"
        elif success_ratio < 0.33:
            return "degrading"
        return "stable"

    def calculate_learning_effectiveness(self) -> float:
        """Calculate overall learning effectiveness score.

        Returns:
            Score from 0.0-1.0
        """
        # Diversity bonus (up to 0.3)
        diversity_score = self.mistake_diversity_index * 0.3

        # Recurrence penalty (up to 0.3, lower recurrence = higher score)
        recurrence_score = (1.0 - self.recurrence_rate) * 0.3

        # Success rate (up to 0.4)
        total_adjustments = self.successful_adjustments + self.failed_adjustments
        success_score = (self.successful_adjustments / total_adjustments) * 0.4 if total_adjustments > 0 else 0.4

        return min(1.0, diversity_score + recurrence_score + success_score)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "mistake_diversity_index": self.mistake_diversity_index,
            "recurrence_rate": self.recurrence_rate,
            "total_mistakes": self.total_mistakes,
            "unique_mistake_types": self.unique_mistake_types,
            "recurring_mistake_count": self.recurring_mistake_count,
            "evolution_velocity": self.evolution_velocity,
            "beliefs_updated_total": self.beliefs_updated_total,
            "sessions_count": self.sessions_count,
            "successful_adjustments": self.successful_adjustments,
            "failed_adjustments": self.failed_adjustments,
            "neutral_adjustments": self.neutral_adjustments,
            "rules_learned_count": self.rules_learned_count,
            "patterns_identified_count": self.patterns_identified_count,
            "knowledge_gaps_filled": self.knowledge_gaps_filled,
            "boundaries_refined_count": self.boundaries_refined_count,
            "biases_detected_total": self.biases_detected_total,
            "biases_mitigated_count": self.biases_mitigated_count,
            "mistake_types_seen": list(self.mistake_types_seen),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EvolutionMetrics:
        """Create from dictionary."""
        return EvolutionMetrics(
            mistake_diversity_index=data.get("mistake_diversity_index", 1.0),
            recurrence_rate=data.get("recurrence_rate", 0.0),
            total_mistakes=data.get("total_mistakes", 0),
            unique_mistake_types=data.get("unique_mistake_types", 0),
            recurring_mistake_count=data.get("recurring_mistake_count", 0),
            evolution_velocity=data.get("evolution_velocity", 0.0),
            beliefs_updated_total=data.get("beliefs_updated_total", 0),
            sessions_count=data.get("sessions_count", 1),
            successful_adjustments=data.get("successful_adjustments", 0),
            failed_adjustments=data.get("failed_adjustments", 0),
            neutral_adjustments=data.get("neutral_adjustments", 0),
            rules_learned_count=data.get("rules_learned_count", 0),
            patterns_identified_count=data.get("patterns_identified_count", 0),
            knowledge_gaps_filled=data.get("knowledge_gaps_filled", 0),
            boundaries_refined_count=data.get("boundaries_refined_count", 0),
            biases_detected_total=data.get("biases_detected_total", 0),
            biases_mitigated_count=data.get("biases_mitigated_count", 0),
            mistake_types_seen=tuple(data.get("mistake_types_seen", [])),
        )


def update_evolution_metrics(
    current: EvolutionMetrics,
    mistake_made: str | None = None,
    mistake_recurring: bool | None = None,
    belief_updated: bool | None = None,
    adjustment_outcome: str | None = None,  # "success", "failed", or "neutral"
    rule_learned: bool | None = None,
    pattern_found: bool | None = None,
    gap_filled: bool | None = None,
    boundary_refined: bool | None = None,
    bias_detected: bool | None = None,
    bias_mitigated: bool | None = None,
) -> EvolutionMetrics:
    """Update evolution metrics based on events.

    Args:
        current: Current metrics state
        mistake_made: Type of mistake made (if any)
        mistake_recurring: True if this is a recurring mistake
        belief_updated: True if a belief was updated
        adjustment_outcome: Outcome of adjustment attempt
        rule_learned: True if a rule was learned
        pattern_found: True if a pattern was identified
        gap_filled: True if a knowledge gap was filled
        boundary_refined: True if a boundary was refined
        bias_detected: True if a bias was detected
        bias_mitigated: True if a bias was mitigated

    Returns:
        Updated EvolutionMetrics
    """
    total_mistakes = current.total_mistakes
    unique_types = set(current.mistake_types_seen)
    recurring_count = current.recurring_mistake_count

    beliefs_updated = current.beliefs_updated_total
    successful = current.successful_adjustments
    failed = current.failed_adjustments
    neutral = current.neutral_adjustments

    rules = current.rules_learned_count
    patterns = current.patterns_identified_count
    gaps = current.knowledge_gaps_filled
    boundaries = current.boundaries_refined_count

    biases_total = current.biases_detected_total
    biases_mitigated = current.biases_mitigated_count

    # Update mistake tracking
    if mistake_made:
        total_mistakes += 1
        unique_types.add(mistake_made)

        if mistake_recurring:
            recurring_count += 1

    # Update belief updates
    if belief_updated:
        beliefs_updated += 1

    # Update adjustment outcomes
    if adjustment_outcome == "success":
        successful += 1
    elif adjustment_outcome == "failed":
        failed += 1
    elif adjustment_outcome == "neutral":
        neutral += 1

    # Update learning counts
    if rule_learned:
        rules += 1
    if pattern_found:
        patterns += 1
    if gap_filled:
        gaps += 1
    if boundary_refined:
        boundaries += 1

    # Update bias tracking
    if bias_detected:
        biases_total += 1
    if bias_mitigated:
        biases_mitigated += 1

    # Calculate diversity index (unique types / total mistakes, capped at 1.0)
    diversity_index = min(1.0, len(unique_types) / max(1, total_mistakes))

    # Calculate recurrence rate (recurring mistakes / total mistakes)
    recurrence_rate = recurring_count / max(1, total_mistakes)

    # Calculate evolution velocity
    velocity = beliefs_updated / max(1, current.sessions_count)

    return EvolutionMetrics(
        mistake_diversity_index=diversity_index,
        recurrence_rate=recurrence_rate,
        total_mistakes=total_mistakes,
        unique_mistake_types=len(unique_types),
        recurring_mistake_count=recurring_count,
        evolution_velocity=velocity,
        beliefs_updated_total=beliefs_updated,
        sessions_count=current.sessions_count,
        successful_adjustments=successful,
        failed_adjustments=failed,
        neutral_adjustments=neutral,
        rules_learned_count=rules,
        patterns_identified_count=patterns,
        knowledge_gaps_filled=gaps,
        boundaries_refined_count=boundaries,
        biases_detected_total=biases_total,
        biases_mitigated_count=biases_mitigated,
        mistake_types_seen=tuple(unique_types),
    )
