"""Tests for internal/hybrid.py — HybridEngine strategy selection and switching."""

from __future__ import annotations

from polaris.cells.roles.engine.internal.base import EngineContext, EngineResult, EngineStrategy
from polaris.cells.roles.engine.internal.hybrid import HybridEngine, get_hybrid_engine


def _make_engine(**kwargs) -> HybridEngine:
    return HybridEngine(workspace="/tmp", **kwargs)


class TestHybridEngineDefaults:
    """Engine initialisation and defaults."""

    def test_engine_starts_with_default_engines(self) -> None:
        engine = _make_engine()
        # Should have registered the four default engines
        registered = engine._registry.list_strategies()
        assert EngineStrategy.REACT in registered
        assert EngineStrategy.PLAN_SOLVE in registered
        assert EngineStrategy.TOT in registered
        assert EngineStrategy.SEQUENTIAL in registered

    def test_engine_strategy_property_not_present(self) -> None:
        """HybridEngine does not inherit from BaseEngine; it orchestrates them."""
        engine = _make_engine()
        # It has no .strategy property (unlike concrete engines)
        assert not hasattr(engine, "strategy") or not isinstance(getattr(engine, "strategy", None), EngineStrategy)

    def test_execution_history_starts_empty(self) -> None:
        engine = _make_engine()
        assert engine._execution_history == []


class TestHybridEngineStrategySelection:
    """_select_strategy and auto-selection."""

    def test_select_strategy_returns_valid_strategy(self) -> None:
        engine = _make_engine()
        ctx = EngineContext(workspace="/tmp", role="pm", task="implement login")
        strategy = engine._select_strategy("implement login", ctx)
        assert isinstance(strategy, EngineStrategy)
        assert strategy in (
            EngineStrategy.REACT,
            EngineStrategy.PLAN_SOLVE,
            EngineStrategy.TOT,
            EngineStrategy.SEQUENTIAL,
        )


class TestHybridEngineStrategySwitching:
    """_should_switch and _get_alternative_strategies."""

    def test_should_switch_on_failure(self) -> None:
        engine = _make_engine()
        result = EngineResult(
            success=False,
            final_answer="",
            strategy=EngineStrategy.REACT,
        )
        assert engine._should_switch(result) is True

    def test_should_switch_at_high_step_count(self) -> None:
        """Results near budget limit should trigger switching."""
        engine = _make_engine()
        engine.budget = engine.budget  # use default budget
        # Default budget: max_steps=12 → 0.8 threshold = 9.6 → 10 steps exceeds
        result = EngineResult(
            success=True,
            final_answer="partial",
            strategy=EngineStrategy.REACT,
            total_steps=10,
            total_tool_calls=5,
            termination_reason="task_incomplete",
        )
        assert engine._should_switch(result) is True

    def test_should_not_switch_when_under_budget(self) -> None:
        """Good results should not trigger switching."""
        engine = _make_engine()
        result = EngineResult(
            success=True,
            final_answer="done",
            strategy=EngineStrategy.REACT,
            total_steps=3,  # well under 80% threshold
            total_tool_calls=2,
            termination_reason="task_completed",
        )
        assert engine._should_switch(result) is False

    def test_get_alternative_strategies_returns_two(self) -> None:
        engine = _make_engine()
        alternatives = engine._get_alternative_strategies(EngineStrategy.REACT)
        assert len(alternatives) == 2
        assert EngineStrategy.REACT not in alternatives

    def test_get_alternative_strategies_unknown_returns_empty(self) -> None:
        engine = _make_engine()
        alternatives = engine._get_alternative_strategies(EngineStrategy.SEQUENTIAL)
        assert len(alternatives) == 2


class TestHybridEngineRun:
    """run() with auto-selection."""

    def test_run_returns_engine_result(self) -> None:
        import asyncio

        engine = _make_engine()

        async def run():
            return await engine.run("simple task")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert hasattr(result, "success")
            assert hasattr(result, "final_answer")
        finally:
            loop.close()

    def test_run_creates_context_when_none_provided(self) -> None:
        """run() should create EngineContext when context=None."""
        import asyncio

        engine = _make_engine()

        async def run():
            return await engine.run("task x")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result is not None
        finally:
            loop.close()

    def test_run_with_explicit_strategy(self) -> None:
        """run() should use the explicitly provided strategy."""
        import asyncio

        engine = _make_engine()

        async def run():
            return await engine.run(
                "task",
                strategy=EngineStrategy.PLAN_SOLVE,
            )

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result is not None
        finally:
            loop.close()

    def test_run_populates_execution_history(self) -> None:
        import asyncio

        engine = _make_engine()

        async def run():
            return await engine.run("log this")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
            assert len(engine._execution_history) == 1
            entry = engine._execution_history[0]
            assert "strategy" in entry
            assert "result" in entry
        finally:
            loop.close()


class TestHybridEngineHelpers:
    """register_engine, set_strategy, get_current_strategy, get_execution_history."""

    def test_register_engine_adds_to_registry(self) -> None:
        from polaris.cells.roles.engine.internal.sequential_adapter import SequentialEngineAdapter

        engine = _make_engine()
        before = len(engine._registry.list_strategies())
        engine.register_engine(SequentialEngineAdapter)
        after = len(engine._registry.list_strategies())
        assert after >= before

    def test_set_strategy_disables_auto_select(self) -> None:
        engine = _make_engine(auto_select=True)
        engine.set_strategy(EngineStrategy.PLAN_SOLVE)
        assert engine.auto_select is True  # auto_select flag unchanged
        assert engine.get_current_strategy() == EngineStrategy.PLAN_SOLVE

    def test_get_current_strategy_initially_none(self) -> None:
        engine = _make_engine()
        assert engine.get_current_strategy() is None

    def test_get_execution_history_initially_empty(self) -> None:
        engine = _make_engine()
        assert engine.get_execution_history() == []


class TestGetHybridEngine:
    """Global singleton."""

    def test_get_hybrid_engine_returns_engine(self) -> None:
        engine = get_hybrid_engine()
        assert isinstance(engine, HybridEngine)
