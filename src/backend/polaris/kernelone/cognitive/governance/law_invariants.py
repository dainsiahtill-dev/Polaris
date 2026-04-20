"""Cognitive Law Invariants - Runtime enforcement of three core laws.

L1: Truthfulness > Consistency -- rather admit mistakes than maintain false consistency
L2: Understanding > Execution -- must understand intent before executing
L3: Evolution > Correctness -- no "forever correct", only "continuously trending correct"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LawViolation:
    """A single recorded law violation."""

    law: str  # "L1", "L2", "L3"
    description: str
    severity: str  # "warn" | "critical"
    context: dict = field(default_factory=dict)


class CognitiveLawGuard:
    """Runtime law enforcement guard.

    All checks are *passive*: they log violations and record telemetry but
    never raise or block execution.  The caller decides whether to act on
    the returned ``LawViolation``.
    """

    def __init__(self, max_violations: int = 10) -> None:
        self._violations: list[LawViolation] = []
        self._max_violations = max_violations

    # ------------------------------------------------------------------
    # L1: Truthfulness > Consistency
    # ------------------------------------------------------------------

    def check_l1_truthfulness(
        self,
        *,
        admitted_uncertainty: bool,
        confidence: float,
        reasoning_contradicted: bool,
    ) -> LawViolation | None:
        """Check L1: Truthfulness > Consistency.

        Violations:
        - Reasoning contradicted but confidence remains high (critical).
        - Low confidence without uncertainty admission (warn).
        """
        if reasoning_contradicted and confidence > 0.7:
            violation = LawViolation(
                law="L1",
                description="Reasoning contradicted but confidence remains high -- violates Truthfulness > Consistency",
                severity="critical",
                context={"confidence": confidence, "contradicted": True},
            )
            self._record(violation)
            return violation

        if not admitted_uncertainty and confidence < 0.5:
            violation = LawViolation(
                law="L1",
                description="Low confidence without uncertainty admission -- may violate truthfulness",
                severity="warn",
                context={"confidence": confidence, "admitted_uncertainty": False},
            )
            self._record(violation)
            return violation

        return None

    # ------------------------------------------------------------------
    # L2: Understanding > Execution
    # ------------------------------------------------------------------

    def check_l2_understanding(
        self,
        *,
        intent_type: str,
        uncertainty_score: float,
        execution_path: str,
    ) -> LawViolation | None:
        """Check L2: Understanding > Execution.

        Violations:
        - Unknown intent executed via bypass (critical).
        - High uncertainty but non-full_pipe path (warn).
        """
        if intent_type == "unknown" and execution_path == "bypass":
            violation = LawViolation(
                law="L2",
                description="Executing with unknown intent -- violates Understanding > Execution",
                severity="critical",
                context={"intent_type": intent_type, "path": execution_path},
            )
            self._record(violation)
            return violation

        if uncertainty_score >= 0.6 and execution_path != "full_pipe":
            violation = LawViolation(
                law="L2",
                description=f"High uncertainty ({uncertainty_score:.2f}) but non-full_pipe path -- may violate Understanding > Execution",
                severity="warn",
                context={"uncertainty": uncertainty_score, "path": execution_path},
            )
            self._record(violation)
            return violation

        return None

    # ------------------------------------------------------------------
    # L3: Evolution > Correctness
    # ------------------------------------------------------------------

    def check_l3_evolution(
        self,
        *,
        has_error: bool,
        evolution_recorded: bool,
        consecutive_failures: int,
    ) -> LawViolation | None:
        """Check L3: Evolution > Correctness.

        Violations:
        - Error without evolution trigger (critical if 3+ consecutive failures).
        """
        if has_error and not evolution_recorded:
            severity = "critical" if consecutive_failures >= 3 else "warn"
            violation = LawViolation(
                law="L3",
                description=f"Error occurred but evolution not triggered ({consecutive_failures} consecutive) -- violates Evolution > Correctness",
                severity=severity,
                context={"has_error": has_error, "consecutive": consecutive_failures},
            )
            self._record(violation)
            return violation

        return None

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _record(self, violation: LawViolation) -> None:
        """Record a violation and emit a log entry."""
        self._violations.append(violation)
        if len(self._violations) > self._max_violations:
            self._violations = self._violations[-self._max_violations :]

        if violation.severity == "critical":
            logger.warning(
                "Cognitive Law Violation [%s]: %s",
                violation.law,
                violation.description,
            )
        else:
            logger.info(
                "Cognitive Law Check [%s]: %s",
                violation.law,
                violation.description,
            )

    @property
    def violations(self) -> list[LawViolation]:
        return list(self._violations)

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self._violations if v.severity == "critical")

    def reset(self) -> None:
        self._violations.clear()
