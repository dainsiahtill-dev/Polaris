"""Tests for internal/plan_solve.py — PlanSolveEngine two-phase logic."""

from __future__ import annotations

from polaris.cells.roles.engine.internal.base import EngineContext
from polaris.cells.roles.engine.internal.plan_solve import PlanSolveEngine


def _make_engine(**kwargs) -> PlanSolveEngine:
    return PlanSolveEngine(workspace="/tmp", **kwargs)


def _ctx() -> EngineContext:
    return EngineContext(workspace="/tmp", role="director", task="implement login")


class TestPlanSolvePhaseTransitions:
    """Plan-then-execute phase transitions."""

    def test_engine_starts_in_planning_phase(self) -> None:
        """Engine init should set _phase to 'planning'."""
        engine = _make_engine()
        assert engine._phase == "planning"
        assert engine._current_plan_index == 0
        assert engine._plan == []

    def test_engine_strategy(self) -> None:
        from polaris.cells.roles.engine.internal.base import EngineStrategy

        engine = _make_engine()
        assert engine.strategy == EngineStrategy.PLAN_SOLVE


class TestPlanSolveCanContinue:
    """can_continue guards."""

    def test_can_continue_stops_at_max_steps(self) -> None:
        engine = _make_engine()
        engine._current_step = engine.budget.max_steps
        assert engine.can_continue() is False

    def test_can_continue_stops_when_plan_exhausted_in_executing(self) -> None:
        """When in executing phase and plan index >= plan length, cannot continue."""
        engine = _make_engine()
        engine._phase = "executing"
        engine._current_plan_index = 99
        engine._plan = ["step1", "step2"]
        # can_continue: phase=="executing" AND _current_plan_index >= len(_plan)
        # 99 >= 2 -> False -> cannot continue
        assert engine.can_continue() is False

    def test_can_continue_stops_on_completed_status(self) -> None:
        engine = _make_engine()
        from polaris.cells.roles.engine.internal.base import EngineStatus

        engine._status = EngineStatus.COMPLETED
        assert engine.can_continue() is False


class TestPlanSolveParsing:
    """_parse_plan_response and _parse_exec_response."""

    def test_parse_plan_response_valid_json(self) -> None:
        engine = _make_engine()
        raw = '{"analysis":"test","plan":["step one","step two"]}'
        result = engine._parse_plan_response(raw)
        assert isinstance(result, dict)
        assert "plan" in result
        assert len(result["plan"]) == 2

    def test_parse_plan_response_fallback_to_numbered_lines(self) -> None:
        """Non-JSON plan text should fall back to line-based extraction."""
        engine = _make_engine()
        raw = "1. step one\n2. step two\n3. step three"
        result = engine._parse_plan_response(raw)
        assert isinstance(result, dict)
        assert "plan" in result
        assert len(result["plan"]) == 3

    def test_parse_plan_response_totally_invalid_returns_empty(self) -> None:
        engine = _make_engine()
        result = engine._parse_plan_response("!!! nowhere")
        assert result == {"plan": [], "analysis": "无法解析"}

    def test_parse_exec_response_valid_json(self) -> None:
        engine = _make_engine()
        raw = '{"thought":"think","action":"finish","completed":true}'
        result = engine._parse_exec_response(raw)
        assert result["action"] == "finish"
        assert result["completed"] is True

    def test_parse_exec_response_invalid_returns_default_finish(self) -> None:
        engine = _make_engine()
        raw = "not json at all"
        result = engine._parse_exec_response(raw)
        # Falls back to default finish
        assert result["action"] == "finish"


class TestPlanSolveExecutePhase:
    """_executing_step edge case: empty plan immediately returns COMPLETED."""

    def test_executing_step_returns_completed_when_plan_empty(self) -> None:
        """When _current_plan_index >= len(_plan), returns COMPLETED status."""
        import asyncio

        engine = _make_engine()
        engine._phase = "executing"
        engine._current_plan_index = 0
        engine._plan = []  # empty plan

        async def run():
            return await engine._executing_step(_ctx(), 0)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            from polaris.cells.roles.engine.internal.base import EngineStatus

            assert result.status == EngineStatus.COMPLETED
        finally:
            loop.close()


class TestPlanSolveBuildPartialAnswer:
    """_build_partial_answer."""

    def test_build_partial_answer_with_plan(self) -> None:
        engine = _make_engine()
        engine._plan = ["step1", "step2", "step3"]
        engine._current_plan_index = 1
        # Use placeholders for steps (None is intentional for testing)
        engine._steps = [None, None]  # type: ignore[list-item]
        partial = engine._build_partial_answer()
        assert isinstance(partial, str)
        assert "1/3" in partial or "step" in partial.lower()

    def test_build_partial_answer_empty_plan(self) -> None:
        engine = _make_engine()
        engine._plan = []
        engine._steps = []
        partial = engine._build_partial_answer()
        assert isinstance(partial, str)
        assert len(partial) > 0
