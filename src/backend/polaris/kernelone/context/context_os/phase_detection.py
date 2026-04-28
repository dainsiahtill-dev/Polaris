"""Task Phase Detection: deterministic detection of current task phase.

This module implements ContextOS 3.0 Phase 2: Phase-Aware Budgeting.
It detects the current task phase based on deterministic signals from
WorkingState and recent transcript events.

Key Design Principle:
    "Phase detection is deterministic + optional LLM hint + hysteresis."
    Phase cannot be decided by LLM alone.

Phase Transitions:
    INTAKE -> PLANNING, EXPLORATION
    PLANNING -> EXPLORATION, IMPLEMENTATION
    EXPLORATION -> PLANNING, IMPLEMENTATION, DEBUGGING
    IMPLEMENTATION -> VERIFICATION, DEBUGGING, REVIEW
    VERIFICATION -> IMPLEMENTATION, DEBUGGING, REVIEW
    DEBUGGING -> IMPLEMENTATION, EXPLORATION, REVIEW
    REVIEW -> IMPLEMENTATION (requires explicit reopen reason)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskPhase(str, Enum):
    """Task phases for context-aware budgeting."""

    INTAKE = "intake"  # 需求理解/合同读取
    PLANNING = "planning"  # 方案设计
    EXPLORATION = "exploration"  # 代码/文档探索
    IMPLEMENTATION = "implementation"  # 修改代码
    VERIFICATION = "verification"  # 测试/验收
    DEBUGGING = "debugging"  # 失败定位
    REVIEW = "review"  # 总结/审查/交付


@dataclass(frozen=True, slots=True)
class PhaseTransition:
    """Record of a phase transition."""

    from_phase: TaskPhase
    to_phase: TaskPhase
    reason: str
    confidence: float
    timestamp: str


@dataclass(frozen=True, slots=True)
class PhaseDetectionResult:
    """Result of phase detection."""

    phase: TaskPhase
    confidence: float
    reason: str
    reason_codes: tuple[str, ...] = ()
    previous_phase: TaskPhase | None = None
    transition: PhaseTransition | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
            "previous_phase": self.previous_phase.value if self.previous_phase else None,
        }


# Allowed phase transitions
ALLOWED_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.INTAKE: {TaskPhase.PLANNING, TaskPhase.EXPLORATION},
    TaskPhase.PLANNING: {TaskPhase.EXPLORATION, TaskPhase.IMPLEMENTATION},
    TaskPhase.EXPLORATION: {TaskPhase.PLANNING, TaskPhase.IMPLEMENTATION, TaskPhase.DEBUGGING},
    TaskPhase.IMPLEMENTATION: {TaskPhase.VERIFICATION, TaskPhase.DEBUGGING, TaskPhase.REVIEW},
    TaskPhase.VERIFICATION: {TaskPhase.IMPLEMENTATION, TaskPhase.DEBUGGING, TaskPhase.REVIEW},
    TaskPhase.DEBUGGING: {TaskPhase.IMPLEMENTATION, TaskPhase.EXPLORATION, TaskPhase.REVIEW},
    TaskPhase.REVIEW: {TaskPhase.IMPLEMENTATION},  # Reopen requires explicit reason
}

# Minimum turns in a phase before allowing transition (hysteresis)
MINIMUM_PHASE_DURATION = 2

# Confidence threshold for phase transition
PHASE_CONFIDENCE_THRESHOLD = 0.7


class TaskPhaseDetector:
    """Deterministic task phase detector.

    Uses signals from WorkingState and transcript to detect phase.
    Implements hysteresis to prevent phase oscillation.
    """

    def __init__(self) -> None:
        self._current_phase: TaskPhase = TaskPhase.INTAKE
        self._phase_start_time: str = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        self._phase_turn_count: int = 0
        self._transition_history: list[PhaseTransition] = []

    @property
    def current_phase(self) -> TaskPhase:
        """Get current detected phase."""
        return self._current_phase

    @property
    def phase_turn_count(self) -> int:
        """Get number of turns in current phase."""
        return self._phase_turn_count

    def detect_phase(
        self,
        working_state: Any,
        recent_events: tuple[Any, ...] = (),
        llm_hint: str | None = None,
    ) -> PhaseDetectionResult:
        """Detect current task phase based on signals.

        Args:
            working_state: Current WorkingState
            recent_events: Recent transcript events
            llm_hint: Optional LLM suggestion (not authoritative)

        Returns:
            PhaseDetectionResult with detected phase and confidence
        """
        # Collect deterministic signals
        signals = self._collect_signals(working_state, recent_events)

        # Determine candidate phase
        candidate_phase, confidence, reason, reason_codes = self._determine_phase(signals)

        # Apply hysteresis
        final_phase, final_confidence, final_reason = self._apply_hysteresis(candidate_phase, confidence, reason)

        # Record transition if phase changed
        transition = None
        if final_phase != self._current_phase:
            transition = PhaseTransition(
                from_phase=self._current_phase,
                to_phase=final_phase,
                reason=final_reason,
                confidence=final_confidence,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            )
            self._transition_history.append(transition)
            self._current_phase = final_phase
            self._phase_turn_count = 0
            logger.info(
                "Phase transition: %s -> %s (confidence=%.2f, reason=%s)",
                transition.from_phase.value,
                transition.to_phase.value,
                final_confidence,
                final_reason,
            )
        else:
            self._phase_turn_count += 1

        return PhaseDetectionResult(
            phase=final_phase,
            confidence=final_confidence,
            reason=final_reason,
            reason_codes=reason_codes,
            previous_phase=transition.from_phase if transition else None,
            transition=transition,
        )

    def _collect_signals(
        self,
        working_state: Any,
        recent_events: tuple[Any, ...],
    ) -> dict[str, Any]:
        """Collect deterministic signals from WorkingState and events."""
        signals: dict[str, Any] = {
            "has_goal": False,
            "has_plan": False,
            "has_open_loops": False,
            "has_blocked_on": False,
            "has_deliverables": False,
            "recent_error_count": 0,
            "recent_read_only_ratio": 0.0,
            "recent_write_count": 0,
            "recent_tool_calls": [],
            "goal_keywords": [],
        }

        # Analyze WorkingState
        if working_state is not None:
            task_state = getattr(working_state, "task_state", None)
            if task_state is not None:
                signals["has_goal"] = task_state.current_goal is not None
                signals["has_plan"] = len(getattr(task_state, "accepted_plan", ())) > 0
                signals["has_open_loops"] = len(getattr(task_state, "open_loops", ())) > 0
                signals["has_blocked_on"] = len(getattr(task_state, "blocked_on", ())) > 0
                signals["has_deliverables"] = len(getattr(task_state, "deliverables", ())) > 0

                # Extract goal keywords
                if task_state.current_goal is not None:
                    goal_text = str(task_state.current_goal.value or "").lower()
                    signals["goal_keywords"] = self._extract_keywords(goal_text)

        # Analyze recent events
        if recent_events:
            read_only_count = 0
            write_count = 0
            error_count = 0

            for event in recent_events[-10:]:  # Last 10 events
                role = str(getattr(event, "role", "")).lower()
                content = str(getattr(event, "content", "")).lower()

                # Count errors
                if any(kw in content for kw in ("error", "exception", "traceback", "failed")):
                    error_count += 1

                # Count read-only vs write operations
                kind = str(getattr(event, "kind", "")).lower()
                if "tool" in kind:
                    read_only_count += 1
                elif role == "assistant":
                    write_count += 1

            signals["recent_error_count"] = error_count
            signals["recent_write_count"] = write_count
            total = read_only_count + write_count
            signals["recent_read_only_ratio"] = read_only_count / total if total > 0 else 0.0

        return signals

    def _determine_phase(
        self,
        signals: dict[str, Any],
    ) -> tuple[TaskPhase, float, str, tuple[str, ...]]:
        """Determine candidate phase from signals."""
        reasons: list[str] = []
        reason_codes: list[str] = []

        # Debugging: recent errors
        if signals["recent_error_count"] > 0:
            reasons.append(f"Recent errors detected: {signals['recent_error_count']}")
            reason_codes.append("RECENT_ERRORS")
            return TaskPhase.DEBUGGING, 0.9, "; ".join(reasons), tuple(reason_codes)

        # Implementing: has plan + has open loops + recent writes
        if signals["has_plan"] and signals["has_open_loops"] and signals["recent_write_count"] > 0:
            reasons.append("Has accepted plan with open loops and recent writes")
            reason_codes.append("PLAN_WITH_WRITES")
            return TaskPhase.IMPLEMENTATION, 0.85, "; ".join(reasons), tuple(reason_codes)

        # Exploring: high read-only ratio
        if signals["recent_read_only_ratio"] > 0.7:
            reasons.append(f"High read-only ratio: {signals['recent_read_only_ratio']:.2f}")
            reason_codes.append("HIGH_READ_ONLY_RATIO")
            return TaskPhase.EXPLORATION, 0.8, "; ".join(reasons), tuple(reason_codes)

        # Verification: has deliverables
        if signals["has_deliverables"]:
            reasons.append("Has deliverables (verification stage)")
            reason_codes.append("HAS_DELIVERABLES")
            return TaskPhase.VERIFICATION, 0.75, "; ".join(reasons), tuple(reason_codes)

        # Planning: has goal + no plan
        if signals["has_goal"] and not signals["has_plan"]:
            reasons.append("Has goal but no accepted plan yet")
            reason_codes.append("GOAL_WITHOUT_PLAN")
            return TaskPhase.PLANNING, 0.7, "; ".join(reasons), tuple(reason_codes)

        # Intake: no goal
        if not signals["has_goal"]:
            reasons.append("No current goal (intake phase)")
            reason_codes.append("NO_GOAL")
            return TaskPhase.INTAKE, 0.8, "; ".join(reasons), tuple(reason_codes)

        # Default: planning
        reasons.append("Default to planning phase")
        reason_codes.append("DEFAULT")
        return TaskPhase.PLANNING, 0.5, "; ".join(reasons), tuple(reason_codes)

    def _apply_hysteresis(
        self,
        candidate_phase: TaskPhase,
        confidence: float,
        reason: str,
    ) -> tuple[TaskPhase, float, str]:
        """Apply hysteresis to prevent phase oscillation."""
        # If confidence below threshold, keep current phase
        if confidence < PHASE_CONFIDENCE_THRESHOLD:
            return self._current_phase, confidence, f"Low confidence ({confidence:.2f}), keeping current phase"

        # If in current phase too short, keep it
        if self._phase_turn_count < MINIMUM_PHASE_DURATION:
            return (
                self._current_phase,
                confidence,
                f"Minimum phase duration not reached ({self._phase_turn_count}/{MINIMUM_PHASE_DURATION})",
            )

        # Check if transition is allowed
        allowed = ALLOWED_TRANSITIONS.get(self._current_phase, set())
        if candidate_phase not in allowed:
            return (
                self._current_phase,
                confidence,
                f"Transition {self._current_phase.value} -> {candidate_phase.value} not allowed",
            )

        return candidate_phase, confidence, reason

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract keywords from text."""
        keywords = []
        # Debug keywords
        if any(kw in text for kw in ("error", "bug", "fix", "debug", "failure")):
            keywords.append("debug")
        # Implementation keywords
        if any(kw in text for kw in ("implement", "create", "build", "write", "code")):
            keywords.append("implement")
        # Review keywords
        if any(kw in text for kw in ("review", "test", "verify", "validate")):
            keywords.append("review")
        # Planning keywords
        if any(kw in text for kw in ("plan", "design", "architect", "blueprint")):
            keywords.append("plan")
        return keywords
