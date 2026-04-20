"""Tests for internal/sequential_adapter.py — SequentialEngineAdapter."""

from __future__ import annotations

from polaris.cells.roles.engine.internal.base import EngineContext
from polaris.cells.roles.engine.internal.sequential_adapter import SequentialEngineAdapter


def _make_adapter(**kwargs) -> SequentialEngineAdapter:
    return SequentialEngineAdapter(workspace="/tmp", **kwargs)


class TestSequentialAdapterDefaults:
    """Adapter initialisation."""

    def test_adapter_has_sequential_strategy(self) -> None:
        from polaris.cells.roles.engine.internal.base import EngineStrategy

        adapter = _make_adapter()
        # Sequential adapter uses SEQUENTIAL as its strategy
        assert adapter.strategy == EngineStrategy.SEQUENTIAL


class TestSequentialAdapterExecute:
    """execute() single-shot LLM call."""

    def test_execute_returns_engine_result(self) -> None:
        import asyncio

        adapter = _make_adapter()

        async def run():
            ctx = EngineContext(workspace="/tmp", role="director", task="run tests")
            result = await adapter.execute(ctx)
            return result

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            # Should return an EngineResult-like object
            assert hasattr(result, "success")
            assert hasattr(result, "final_answer")
            assert hasattr(result, "total_steps")
        finally:
            loop.close()

    def test_execute_nonempty_task_succeeds(self) -> None:
        """Non-empty task should always succeed (single-shot, no failure path)."""
        import asyncio

        adapter = _make_adapter()

        async def run():
            ctx = EngineContext(workspace="/tmp", role="director", task="implement feature")
            result = await adapter.execute(ctx)
            return result

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result.success is True
        finally:
            loop.close()


class TestSequentialAdapterPromptBuilding:
    """Sequential adapter prompt building via execute path."""

    def test_adapter_has_strategy(self) -> None:
        """Adapter should expose its strategy."""
        adapter = _make_adapter()
        assert adapter.strategy.value == "sequential"

    def test_adapter_can_continue_respects_budget(self) -> None:
        """can_continue should check budget limits."""
        adapter = _make_adapter()
        assert adapter.can_continue() is True
        adapter._current_step = 100
        assert adapter.can_continue() is False
