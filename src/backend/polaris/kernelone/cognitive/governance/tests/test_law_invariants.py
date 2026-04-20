"""Tests for Cognitive Law Invariants."""

from __future__ import annotations

from polaris.kernelone.cognitive.governance.law_invariants import (
    CognitiveLawGuard,
)


class TestL1Truthfulness:
    def test_contradicted_reasoning_high_confidence_is_critical(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l1_truthfulness(
            admitted_uncertainty=False,
            confidence=0.8,
            reasoning_contradicted=True,
        )
        assert v is not None
        assert v.law == "L1"
        assert v.severity == "critical"

    def test_no_violation_when_honest(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l1_truthfulness(
            admitted_uncertainty=True,
            confidence=0.4,
            reasoning_contradicted=False,
        )
        assert v is None

    def test_low_confidence_without_admission_warns(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l1_truthfulness(
            admitted_uncertainty=False,
            confidence=0.3,
            reasoning_contradicted=False,
        )
        assert v is not None
        assert v.severity == "warn"

    def test_high_confidence_no_contradiction_is_ok(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l1_truthfulness(
            admitted_uncertainty=True,
            confidence=0.9,
            reasoning_contradicted=False,
        )
        assert v is None


class TestL2Understanding:
    def test_unknown_intent_bypass_is_critical(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l2_understanding(
            intent_type="unknown",
            uncertainty_score=0.2,
            execution_path="bypass",
        )
        assert v is not None
        assert v.law == "L2"
        assert v.severity == "critical"

    def test_high_uncertainty_not_full_pipe(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l2_understanding(
            intent_type="create_file",
            uncertainty_score=0.7,
            execution_path="fast_think",
        )
        assert v is not None
        assert v.severity == "warn"

    def test_high_uncertainty_full_pipe_is_ok(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l2_understanding(
            intent_type="create_file",
            uncertainty_score=0.7,
            execution_path="full_pipe",
        )
        assert v is None

    def test_low_uncertainty_bypass_is_ok(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l2_understanding(
            intent_type="create_file",
            uncertainty_score=0.2,
            execution_path="bypass",
        )
        assert v is None


class TestL3Evolution:
    def test_error_without_evolution(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l3_evolution(
            has_error=True,
            evolution_recorded=False,
            consecutive_failures=1,
        )
        assert v is not None
        assert v.law == "L3"

    def test_consecutive_failures_escalate(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l3_evolution(
            has_error=True,
            evolution_recorded=False,
            consecutive_failures=3,
        )
        assert v is not None
        assert v.severity == "critical"

    def test_no_violation_when_evolution_recorded(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l3_evolution(
            has_error=True,
            evolution_recorded=True,
            consecutive_failures=1,
        )
        assert v is None

    def test_no_error_no_violation(self) -> None:
        guard = CognitiveLawGuard()
        v = guard.check_l3_evolution(
            has_error=False,
            evolution_recorded=False,
            consecutive_failures=0,
        )
        assert v is None


class TestGuardState:
    def test_violations_recorded(self) -> None:
        guard = CognitiveLawGuard()
        guard.check_l1_truthfulness(
            admitted_uncertainty=False,
            confidence=0.8,
            reasoning_contradicted=True,
        )
        assert len(guard.violations) == 1

    def test_max_violations_window(self) -> None:
        guard = CognitiveLawGuard(max_violations=3)
        for _ in range(5):
            guard.check_l2_understanding(
                intent_type="unknown",
                uncertainty_score=0.2,
                execution_path="bypass",
            )
        assert len(guard.violations) == 3

    def test_critical_count(self) -> None:
        guard = CognitiveLawGuard()
        guard.check_l1_truthfulness(
            admitted_uncertainty=False,
            confidence=0.8,
            reasoning_contradicted=True,
        )
        guard.check_l3_evolution(
            has_error=True,
            evolution_recorded=False,
            consecutive_failures=1,
        )
        assert guard.critical_count == 1

    def test_reset_clears_violations(self) -> None:
        guard = CognitiveLawGuard()
        guard.check_l1_truthfulness(
            admitted_uncertainty=False,
            confidence=0.8,
            reasoning_contradicted=True,
        )
        assert len(guard.violations) == 1
        guard.reset()
        assert len(guard.violations) == 0
        assert guard.critical_count == 0

    def test_violations_returns_copy(self) -> None:
        guard = CognitiveLawGuard()
        guard.check_l1_truthfulness(
            admitted_uncertainty=False,
            confidence=0.8,
            reasoning_contradicted=True,
        )
        v = guard.violations
        v.clear()
        assert len(guard.violations) == 1
