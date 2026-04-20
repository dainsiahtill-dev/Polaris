"""Critical Thinking Engine - Data Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Assumption:
    """A single assumption in the reasoning chain."""

    id: str
    text: str
    confidence: float  # 0.0-1.0
    conditions_for_failure: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)
    is_hidden: bool = True
    source: str = "keyword"  # keyword | intent_type | llm


@dataclass(frozen=True, slots=True)
class DevilsAdvocateResult:
    """Result of devil's advocate counterargument analysis."""

    strength: float  # 0.0-1.0
    remaining_uncertainty: float  # 0.0-1.0
    counter_arguments: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SixQuestionsResult:
    """Result of Six Questions analysis."""

    # Default fields first (with defaults)
    # Q1: Assumptions
    assumptions: tuple[Assumption, ...] = field(default_factory=tuple)
    # Q2: Failure conditions
    failure_conditions: tuple[str, ...] = field(default_factory=tuple)
    uncertainty_band: tuple[float, float] = field(default=(0.0, 1.0))
    verification_steps: tuple[str, ...] = field(default_factory=tuple)
    # Non-default fields after (no defaults)
    # Q3: Devil's advocate
    devils_advocate: DevilsAdvocateResult | None = None
    # Q4: Probability
    conclusion_probability: float = 0.5
    knowledge_status: str = "guessed"  # known | inferred | guessed
    # Q5: Cost of error
    cost_of_error: str = "medium"
    severity: str = "medium"  # low | medium | high | critical
    # Q6: Verification
    can_verify: bool = False


@dataclass(frozen=True, slots=True)
class ReasoningChain:
    """Complete reasoning chain with uncertainty tracking."""

    conclusion: str
    six_questions: SixQuestionsResult
    confidence_level: str  # high | medium | low | unknown
    should_proceed: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
