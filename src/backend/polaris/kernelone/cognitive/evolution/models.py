"""Evolution Layer - Data Models for Belief Tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TriggerType(str, Enum):
    """Types of events that trigger evolution."""

    USER_CORRECTION = "user_correction"
    PREDICTION_MISMATCH = "prediction_mismatch"
    NEW_INFO = "new_info"
    BETTER_METHOD = "better_method_found"
    HYPOTHESIS_FALSIFIED = "hypothesis_falsified"
    BIAS_DETECTED = "bias_detected"
    SELF_REFLECTION = "self_reflection"


@dataclass(frozen=True)
class Belief:
    """A single belief or knowledge item."""

    belief_id: str
    content: str
    source: str
    source_session: str | None
    confidence: float  # 0.0-1.0
    importance: int  # 1-10
    created_at: str
    verified_at: str | None
    falsified_at: str | None
    supersedes: str | None
    related_rules: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EvolutionRecord:
    """A single evolution event - how beliefs changed."""

    record_id: str
    timestamp: str
    trigger_type: TriggerType
    previous_belief_id: str | None
    previous_confidence: float | None
    new_belief_id: str | None
    new_confidence: float | None
    context: str
    rationale: str
    verification_needed: bool


@dataclass(frozen=True)
class EvolutionState:
    """Current state of the evolution system."""

    evolution_id: str
    calibration_score: float
    beliefs: tuple[Belief, ...] = field(default_factory=tuple)
    update_history: tuple[EvolutionRecord, ...] = field(default_factory=tuple)
    knowledge_gaps: tuple[str, ...] = field(default_factory=tuple)
    version: int = 1


@dataclass(frozen=True)
class KnowledgeGap:
    """Identified gap in knowledge."""

    gap_id: str
    topic: str
    current_knowledge: str
    priority: int  # 1-10
    missing_aspects: tuple[str, ...] = field(default_factory=tuple)
    resources_needed: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BiasMetrics:
    """Tracks cognitive bias exposure."""

    confirmation_bias_exposure: float
    overconfidence_exposure: float
    availability_heuristic_exposure: float
    anchoring_exposure: float
    hindsight_bias_exposure: float
    counter_evidence_seeks: int
    assumption_challenges: int
