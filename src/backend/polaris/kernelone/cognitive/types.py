"""Cognitive Life Form Core Types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClarityLevel(int, Enum):
    """5-level clarity scale for thinking-acting separation."""

    FUZZY = 1  # Vague, exploratory
    TENDENCY = 2  # Directional but uncertain
    CERTAIN = 3  # Confident but not verified
    ACTION_ORIENTED = 4  # Ready for action
    FULL_TRANSPARENT = 5  # Fully clear and verifiable


class RiskLevel(int, Enum):
    """L0-L4 risk classification for execution."""

    L0_READONLY = 0  # Read-only, memory ops
    L1_CREATE = 1  # Create new files, no system impact
    L2_MODIFY = 2  # Modify existing, needs rollback
    L3_DELETE = 3  # Delete, cross-system changes
    L4_IRREVERSIBLE = 4  # DB writes, system config


class ExecutionPath(str, Enum):
    """Cognitive execution path selection."""

    BYPASS = "bypass"  # Direct execution, zero cognitive overhead
    FAST_THINK = "fast_think"  # Thinking phase only
    THINKING = "thinking"  # Thinking + light verification
    FULL_PIPE = "full_pipe"  # All cognitive protocols


@dataclass(frozen=True)
class ThinkingOutput:
    """Output from thinking phase."""

    content: str
    confidence: float  # 0.0-1.0
    clarity_level: ClarityLevel
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    uncertainty_factors: tuple[str, ...] = field(default_factory=tuple)
    reasoning_chain: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ActingOutput:
    """Output from acting phase."""

    content: str
    risk_level: RiskLevel
    actions_taken: tuple[str, ...] = field(default_factory=tuple)
    rollback_steps: tuple[str, ...] = field(default_factory=tuple)
    verification_needed: bool = False
    # Error context for Workflow decision making
    error_type: str | None = None
    retryable: bool = True
    blocked_tools: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExecutionRecommendation:
    """Recommendation from cautious execution policy."""

    path: ExecutionPath
    skip_cognitive_pipe: bool
    confidence: float
    risk_level: RiskLevel
    requires_rollback_plan: bool = False
    requires_user_confirmation: bool = False
    blockers: tuple[str, ...] = field(default_factory=tuple)
    uncertainty_threshold_exceeded: bool = False


# ---------------------------------------------------------------------------
# Null Objects for missing / unavailable cognitive subsystem outputs
# ---------------------------------------------------------------------------


class NullSixQuestions:
    """Null object replacing SixQuestionsResult when reasoning is unavailable."""

    assumptions: tuple[Any, ...] = ()
    failure_conditions: tuple[str, ...] = ()
    uncertainty_band: tuple[float, float] = (0.0, 1.0)
    verification_steps: tuple[str, ...] = ()
    devils_advocate: Any = None
    conclusion_probability: float = 0.5
    knowledge_status: str = "guessed"
    cost_of_error: str = "medium"
    severity: str = "medium"
    can_verify: bool = False


class NullReasoningChain:
    """Null object replacing ReasoningChain when reasoning is unavailable."""

    conclusion: str = ""
    blockers: list[str] = []
    six_questions: Any = None
    confidence_level: str = "unknown"
    should_proceed: bool = False
    metadata: dict[str, Any] = {}


class NullMetaCognition:
    """Null object replacing MetaCognitionSnapshot when meta-cognition is unavailable."""

    reasoning_chain_summary: str = ""
    knowledge_gaps: list[str] = []
    output_confidence: float = 0.5
