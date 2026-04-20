"""Value Alignment Service - 4-Dimension Evaluation Matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.security.dangerous_patterns import is_dangerous_command as _is_dangerous


class ValueDimension(str, Enum):
    """Four dimensions of value evaluation."""

    USER_LONG_TERM = "user_long_term"
    SYSTEM_INTEGRITY = "system_integrity"
    OTHERS_IMPACT = "others_impact"
    FUTURE_AMPLIFICATION = "future_amplification"


@dataclass(frozen=True)
class ValueEvaluation:
    """Result of value alignment evaluation."""

    dimension: ValueDimension
    score: float  # 0.0-1.0, higher = more aligned
    verdict: str = ""  # APPROVED | CONDITIONAL | REJECTED | ESCALATE
    concerns: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ValueAlignmentResult:
    """Complete value alignment result with 4D evaluation."""

    evaluations: tuple[ValueEvaluation, ...]
    overall_score: float  # Weighted average
    stranger_test_passed: bool  # "Would you explain this to a stranger?"
    final_verdict: str = ""  # APPROVED | CONDITIONAL | REJECTED
    conflicts: tuple[str, ...] = field(default_factory=tuple)
    escalation_required: bool = False


class ValueAlignmentService:
    """
    Implements 4-dimensional value evaluation matrix.

    Dimensions:
    1. User Long-term Interest: Is this good for the user in the long run?
    2. System Integrity: Does this maintain system health and security?
    3. Others Impact: How does this affect non-participants?
    4. Future Amplification: Would 1000x amplification make this still okay?

    Priority: User Long-term > System Integrity > Others > Future

    Stranger Test: "Am I willing to explain why I did this to a stranger?"
    """

    def __init__(self) -> None:
        self._evaluation_history: list[ValueAlignmentResult] = []

    async def evaluate(
        self,
        action: str,
        context: str = "",
        user_intent: str = "",
    ) -> ValueAlignmentResult:
        """
        Evaluate an action against the 4D value matrix.
        """
        evaluations = []

        # Dimension 1: User Long-term (weight: 0.35)
        user_eval = await self._evaluate_user_long_term(action, user_intent)
        evaluations.append(user_eval)

        # Dimension 2: System Integrity (weight: 0.30)
        system_eval = await self._evaluate_system_integrity(action)
        evaluations.append(system_eval)

        # Dimension 3: Others Impact (weight: 0.20)
        others_eval = await self._evaluate_others_impact(action)
        evaluations.append(others_eval)

        # Dimension 4: Future Amplification (weight: 0.15)
        future_eval = await self._evaluate_future_amplification(action)
        evaluations.append(future_eval)

        # Calculate weighted overall score
        weights = {
            ValueDimension.USER_LONG_TERM: 0.35,
            ValueDimension.SYSTEM_INTEGRITY: 0.30,
            ValueDimension.OTHERS_IMPACT: 0.20,
            ValueDimension.FUTURE_AMPLIFICATION: 0.15,
        }

        overall = sum(e.score * weights[e.dimension] for e in evaluations)

        # Stranger test
        stranger_passed = overall >= 0.6

        # Identify conflicts
        conflicts = self._identify_conflicts(evaluations)

        # Determine final verdict
        rejected_evals = [e for e in evaluations if e.verdict == "REJECTED"]
        conditional_evals = [e for e in evaluations if e.verdict == "CONDITIONAL"]

        if rejected_evals:
            final_verdict = "REJECTED"
        elif conditional_evals or overall < 0.7:
            final_verdict = "CONDITIONAL"
        else:
            final_verdict = "APPROVED"

        escalation = any(e.verdict == "ESCALATE" for e in evaluations) or overall < 0.5

        result = ValueAlignmentResult(
            evaluations=tuple(evaluations),
            overall_score=overall,
            stranger_test_passed=stranger_passed,
            conflicts=conflicts,
            final_verdict=final_verdict,
            escalation_required=escalation,
        )

        self._evaluation_history.append(result)
        return result

    async def _evaluate_user_long_term(
        self,
        action: str,
        user_intent: str,
    ) -> ValueEvaluation:
        """Dimension 1: User Long-term Interest."""
        concerns = []
        score = 0.8  # Default positive

        # Check for patterns that harm long-term user interest
        harmful_patterns = [
            ("quick fix", "May create technical debt"),
            ("ignore warning", "May cause future issues"),
            ("skip test", "Reduces reliability"),
            ("delete backup", "Removes safety net"),
        ]

        action_lower = action.lower()
        for pattern, concern in harmful_patterns:
            if pattern in action_lower:
                concerns.append(f"Long-term concern: {concern}")
                score -= 0.2

        verdict = "APPROVED" if score >= 0.7 else "CONDITIONAL" if score >= 0.5 else "REJECTED"

        return ValueEvaluation(
            dimension=ValueDimension.USER_LONG_TERM,
            score=max(0.0, min(1.0, score)),
            concerns=tuple(concerns),
            verdict=verdict,
        )

    async def _evaluate_system_integrity(self, action: str) -> ValueEvaluation:
        """Dimension 2: System Integrity."""
        concerns = []
        score = 0.9

        # First check canonical dangerous patterns (P1-2)
        if _is_dangerous(action):
            return ValueEvaluation(
                dimension=ValueDimension.SYSTEM_INTEGRITY,
                score=0.0,  # Not aligned at all - dangerous command
                concerns=("Dangerous command detected by canonical patterns",),
                verdict="REJECTED",
            )

        # Secondary check: Patterns that threaten system integrity
        risky_patterns = [
            ("delete system", "Threatens system integrity"),
            ("bypass auth", "Security risk"),
            ("disable audit", "Reduces observability"),
            ("sudo", "Elevated privileges"),
            ("chmod 777", "World writable permissions"),
            ("drop database", "Destroys data"),
        ]

        action_lower = action.lower()
        for pattern, concern in risky_patterns:
            if pattern in action_lower:
                concerns.append(f"Integrity concern: {concern}")
                score -= 0.3

        verdict = "APPROVED" if score >= 0.7 else "CONDITIONAL" if score >= 0.5 else "ESCALATE"

        return ValueEvaluation(
            dimension=ValueDimension.SYSTEM_INTEGRITY,
            score=max(0.0, min(1.0, score)),
            concerns=tuple(concerns),
            verdict=verdict,
        )

    async def _evaluate_others_impact(self, action: str, context: dict[str, Any] | None = None) -> ValueEvaluation:
        """Dimension 3: Others Impact."""
        concerns = []
        score = 1.0  # Start aligned and decrease for concerning factors

        # High-impact actions that should be rejected or reviewed
        # Critical keywords: actions that affect all users or many users
        critical_keywords = ["broadcast", "all users", "everyone", "org-wide", "company-wide"]
        high_impact_keywords = ["notify", "email", "slack", "webhook", "mass", "bulk"]

        action_lower = action.lower()

        # Check for critical impact actions first - these override to REJECTED
        for kw in critical_keywords:
            if kw in action_lower:
                concerns.append(f"Critical impact on others: {kw}")
                score = 0.0  # Critical: mark as not aligned

        # Decrease score for high-impact keywords
        for kw in high_impact_keywords:
            if kw in action_lower:
                score = max(0.0, score - 0.2)
                concerns.append(f"High impact: {kw}")

        # Shared resource access decreases alignment
        shared_keywords = ["shared", "team", "public", "config", "org", "workspace", ".github", "infra"]
        for kw in shared_keywords:
            if kw in action_lower:
                score = max(0.0, score - 0.15)
                concerns.append(f"Shared resource: {kw}")

        # Determine verdict based on final score
        if score <= 0.2:
            verdict = "REJECTED"
        elif score <= 0.5:
            verdict = "REVIEW"
        else:
            verdict = "APPROVED"

        return ValueEvaluation(
            dimension=ValueDimension.OTHERS_IMPACT,
            score=max(0.0, min(1.0, score)),
            concerns=tuple(concerns),
            verdict=verdict,
        )

    async def _evaluate_future_amplification(self, action: str) -> ValueEvaluation:
        """Dimension 4: Future Amplification (1000x test)."""
        concerns = []
        score = 0.8

        # If this were done 1000x times, would it still be okay?
        amplification_concerns = [
            ("create file", "1000 files created - disk space impact"),
            ("api call", "1000 API calls - rate limit risk"),
            ("log", "1000 logs - storage bloat"),
        ]

        action_lower = action.lower()
        for pattern, concern in amplification_concerns:
            if pattern in action_lower:
                concerns.append(f"Amplification: {concern}")
                score -= 0.15

        return ValueEvaluation(
            dimension=ValueDimension.FUTURE_AMPLIFICATION,
            score=max(0.0, min(1.0, score)),
            concerns=tuple(concerns),
            verdict="APPROVED" if score >= 0.7 else "CONDITIONAL",
        )

    def _identify_conflicts(self, evaluations: list[ValueEvaluation]) -> tuple[str, ...]:
        """Identify conflicts between value dimensions."""
        conflicts = []

        # Check if user long-term conflicts with system integrity
        user_eval = next((e for e in evaluations if e.dimension == ValueDimension.USER_LONG_TERM), None)
        system_eval = next((e for e in evaluations if e.dimension == ValueDimension.SYSTEM_INTEGRITY), None)

        if user_eval and system_eval and user_eval.score > 0.7 and system_eval.score < 0.5:
            conflicts.append("User benefit conflicts with system integrity")

        return tuple(conflicts)
