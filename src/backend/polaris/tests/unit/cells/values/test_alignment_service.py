"""Unit tests for ValueAlignmentService and related contracts."""

from __future__ import annotations

import pytest

from polaris.cells.values.alignment_service import (
    ValueAlignmentResult,
    ValueAlignmentService,
    ValueDimension,
    ValueEvaluation,
)


class TestValueDimension:
    """Tests for ValueDimension enum."""

    def test_members(self) -> None:
        assert ValueDimension.USER_LONG_TERM.value == "user_long_term"
        assert ValueDimension.SYSTEM_INTEGRITY.value == "system_integrity"
        assert ValueDimension.OTHERS_IMPACT.value == "others_impact"
        assert ValueDimension.FUTURE_AMPLIFICATION.value == "future_amplification"

    def test_is_str_enum(self) -> None:
        assert isinstance(ValueDimension.USER_LONG_TERM, str)


class TestValueEvaluation:
    """Tests for ValueEvaluation dataclass."""

    def test_defaults(self) -> None:
        ev = ValueEvaluation(
            dimension=ValueDimension.USER_LONG_TERM,
            score=0.8,
        )
        assert ev.verdict == ""
        assert ev.concerns == ()

    def test_frozen(self) -> None:
        ev = ValueEvaluation(
            dimension=ValueDimension.USER_LONG_TERM,
            score=0.8,
        )
        with pytest.raises(AttributeError):
            ev.score = 0.5  # type: ignore[misc]


class TestValueAlignmentResult:
    """Tests for ValueAlignmentResult dataclass."""

    def test_defaults(self) -> None:
        result = ValueAlignmentResult(
            evaluations=(),
            overall_score=0.5,
            stranger_test_passed=True,
        )
        assert result.final_verdict == ""
        assert result.conflicts == ()
        assert result.escalation_required is False

    def test_frozen(self) -> None:
        result = ValueAlignmentResult(
            evaluations=(),
            overall_score=0.5,
            stranger_test_passed=True,
        )
        with pytest.raises(AttributeError):
            result.overall_score = 0.9  # type: ignore[misc]


class TestValueAlignmentService:
    """Tests for ValueAlignmentService."""

    @pytest.fixture
    def service(self) -> ValueAlignmentService:
        return ValueAlignmentService()

    @pytest.mark.asyncio
    async def test_evaluate_safe_action(self, service: ValueAlignmentService) -> None:
        result = await service.evaluate(action="read file", context="test", user_intent="explore")
        assert isinstance(result, ValueAlignmentResult)
        assert 0.0 <= result.overall_score <= 1.0
        assert result.final_verdict in ("APPROVED", "CONDITIONAL", "REJECTED")
        assert len(result.evaluations) == 4
        dimensions = {e.dimension for e in result.evaluations}
        assert dimensions == set(ValueDimension)

    @pytest.mark.asyncio
    async def test_evaluate_dangerous_action_rejected(self, service: ValueAlignmentService) -> None:
        result = await service.evaluate(action="rm -rf /", context="", user_intent="cleanup")
        assert result.final_verdict == "REJECTED"
        system_eval = next(
            (e for e in result.evaluations if e.dimension == ValueDimension.SYSTEM_INTEGRITY),
            None,
        )
        assert system_eval is not None
        assert system_eval.verdict == "REJECTED"
        assert system_eval.score == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_quick_fix_conditional(self, service: ValueAlignmentService) -> None:
        result = await service.evaluate(action="quick fix for bug", context="", user_intent="")
        user_eval = next(
            (e for e in result.evaluations if e.dimension == ValueDimension.USER_LONG_TERM),
            None,
        )
        assert user_eval is not None
        assert user_eval.score < 0.8
        assert "technical debt" in str(user_eval.concerns).lower()

    @pytest.mark.asyncio
    async def test_evaluate_others_impact_broadcast_rejected(self, service: ValueAlignmentService) -> None:
        result = await service.evaluate(action="broadcast message to all users", context="", user_intent="")
        others_eval = next(
            (e for e in result.evaluations if e.dimension == ValueDimension.OTHERS_IMPACT),
            None,
        )
        assert others_eval is not None
        assert others_eval.verdict == "REJECTED"
        assert others_eval.score == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_stranger_test(self, service: ValueAlignmentService) -> None:
        result = await service.evaluate(action="read file")
        assert result.stranger_test_passed == (result.overall_score >= 0.6)

    @pytest.mark.asyncio
    async def test_evaluate_history_accumulates(self, service: ValueAlignmentService) -> None:
        await service.evaluate(action="read file")
        await service.evaluate(action="write file")
        assert len(service._evaluation_history) == 2

    @pytest.mark.asyncio
    async def test_evaluate_future_amplification(self, service: ValueAlignmentService) -> None:
        result = await service.evaluate(action="create file")
        future_eval = next(
            (e for e in result.evaluations if e.dimension == ValueDimension.FUTURE_AMPLIFICATION),
            None,
        )
        assert future_eval is not None
        assert future_eval.score < 0.8
        assert "disk space" in str(future_eval.concerns).lower()

    @pytest.mark.asyncio
    async def test_evaluate_system_integrity_risky_patterns(self, service: ValueAlignmentService) -> None:
        result = await service.evaluate(action="sudo chmod 777 /etc/passwd")
        system_eval = next(
            (e for e in result.evaluations if e.dimension == ValueDimension.SYSTEM_INTEGRITY),
            None,
        )
        assert system_eval is not None
        assert system_eval.score < 0.7
        assert any("chmod 777" in c for c in system_eval.concerns)
