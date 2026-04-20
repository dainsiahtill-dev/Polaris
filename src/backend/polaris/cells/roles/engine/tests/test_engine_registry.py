"""Tests for internal/registry.py — EngineRegistry and global singleton."""

from __future__ import annotations

import pytest
from polaris.cells.roles.engine.internal.base import (
    BaseEngine,
    EngineContext,
    EngineResult,
    EngineStatus,
    EngineStrategy,
)
from polaris.cells.roles.engine.internal.registry import (
    EngineRegistry,
    get_engine,
    get_engine_registry,
    register_engine,
)


class _DummyEngine(BaseEngine):
    """Minimal concrete BaseEngine for testing registry."""

    @property
    def strategy(self) -> EngineStrategy:
        return EngineStrategy.REACT

    async def execute(self, context: EngineContext, initial_message: str = "") -> EngineResult:
        return self._create_result(success=True, final_answer="done", termination_reason="task_completed")

    async def step(self, context: EngineContext):
        from polaris.cells.roles.engine.internal.base import StepResult

        return StepResult(step_index=0, status=EngineStatus.COMPLETED)

    def can_continue(self) -> bool:
        return False


class _PlanSolveDummy(BaseEngine):
    @property
    def strategy(self) -> EngineStrategy:
        return EngineStrategy.PLAN_SOLVE

    async def execute(self, context: EngineContext, initial_message: str = "") -> EngineResult:
        return self._create_result(success=True, final_answer="done", termination_reason="task_completed")

    async def step(self, context: EngineContext):
        from polaris.cells.roles.engine.internal.base import StepResult

        return StepResult(step_index=0, status=EngineStatus.COMPLETED)

    def can_continue(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# EngineRegistry — lifecycle
# ---------------------------------------------------------------------------


def test_engine_registry_starts_empty() -> None:
    registry = EngineRegistry()
    assert registry.list_strategies() == []


def test_engine_registry_register_stores_strategy() -> None:
    registry = EngineRegistry()
    registry.register(_DummyEngine)
    strategies = registry.list_strategies()
    assert EngineStrategy.REACT in strategies


def test_engine_registry_register_same_strategy_twice() -> None:
    """Re-registering the same strategy should be idempotent (not raise)."""
    registry = EngineRegistry()
    registry.register(_DummyEngine)
    # Should not raise
    registry.register(_DummyEngine)
    # Only one entry
    assert len(registry.list_strategies()) == 1


def test_engine_registry_register_requires_base_engine_subclass() -> None:
    registry = EngineRegistry()

    class NotAnEngine:
        pass

    with pytest.raises(TypeError, match="must be a subclass of BaseEngine"):
        registry.register(NotAnEngine)  # type: ignore[arg-type]


def test_engine_registry_get_returns_instance() -> None:
    registry = EngineRegistry()
    registry.register(_DummyEngine)
    instance = registry.get(EngineStrategy.REACT)
    assert instance is not None
    assert isinstance(instance, _DummyEngine)


def test_engine_registry_get_unknown_returns_none() -> None:
    registry = EngineRegistry()
    assert registry.get(EngineStrategy.REACT) is None


def test_engine_registry_get_returns_cached_instance() -> None:
    """Multiple calls to get() should return the same object."""
    registry = EngineRegistry()
    registry.register(_DummyEngine)
    a = registry.get(EngineStrategy.REACT)
    b = registry.get(EngineStrategy.REACT)
    assert a is b


def test_engine_registry_unregister() -> None:
    registry = EngineRegistry()
    registry.register(_DummyEngine)
    result = registry.unregister(EngineStrategy.REACT)
    assert result is True
    assert registry.get(EngineStrategy.REACT) is None


def test_engine_registry_unregister_unknown_is_noop() -> None:
    registry = EngineRegistry()
    result = registry.unregister(EngineStrategy.REACT)
    assert result is True  # always True per implementation


def test_engine_registry_clear() -> None:
    registry = EngineRegistry()
    registry.register(_DummyEngine)
    registry.register(_PlanSolveDummy)
    registry.clear()
    assert registry.list_strategies() == []


def test_engine_registry_register_with_kwargs() -> None:
    """register() passes kwargs to the engine constructor."""
    registry = EngineRegistry()
    registry.register(_DummyEngine, workspace="/test")
    instance = registry.get(EngineStrategy.REACT)
    assert instance is not None
    assert instance.workspace == "/test"


def test_engine_registry_register_instance() -> None:
    """register_instance() stores an existing instance."""
    registry = EngineRegistry()
    instance = _DummyEngine(workspace="/ws")
    registry.register_instance(instance)
    retrieved = registry.get(EngineStrategy.REACT)
    assert retrieved is instance


def test_engine_registry_get_or_create() -> None:
    registry = EngineRegistry()
    registry.register(_DummyEngine, workspace="/ws")
    instance = registry.get_or_create(EngineStrategy.REACT)
    assert isinstance(instance, _DummyEngine)


def test_engine_registry_get_or_create_unknown_raises() -> None:
    registry = EngineRegistry()
    with pytest.raises(ValueError, match="No engine registered"):
        registry.get_or_create(EngineStrategy.REACT)


def test_engine_registry_get_or_create_with_override_kwargs() -> None:
    registry = EngineRegistry()
    registry.register(_DummyEngine, workspace="/default")
    instance = registry.get_or_create(EngineStrategy.REACT, workspace="/override")
    assert instance.workspace == "/override"


# ---------------------------------------------------------------------------
# Global singleton helpers
# ---------------------------------------------------------------------------


def test_register_engine_convenience() -> None:
    """register_engine() convenience function should register on global registry."""
    # Start from a clean global registry
    get_engine_registry().clear()

    register_engine(_PlanSolveDummy)
    strategies = get_engine_registry().list_strategies()
    assert EngineStrategy.PLAN_SOLVE in strategies


def test_get_engine_convenience() -> None:
    """get_engine() convenience function should retrieve from global registry."""
    get_engine_registry().clear()
    register_engine(_DummyEngine)
    instance = get_engine(EngineStrategy.REACT)
    assert instance is not None
    assert isinstance(instance, _DummyEngine)


def test_get_engine_unknown_returns_none() -> None:
    get_engine_registry().clear()
    result = get_engine(EngineStrategy.REACT)
    assert result is None


def test_get_engine_registry_is_singleton() -> None:
    """Two calls return the same registry object."""
    a = get_engine_registry()
    b = get_engine_registry()
    assert a is b


def test_global_registry_cleared_between_tests() -> None:
    """Each test should work with a clean global registry."""
    get_engine_registry().clear()
    snapshot = get_engine_registry().list_strategies()
    assert len(snapshot) == 0
