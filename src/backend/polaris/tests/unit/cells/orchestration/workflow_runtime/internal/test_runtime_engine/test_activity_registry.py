"""Tests for workflow_runtime internal runtime_engine activity_registry module."""

from __future__ import annotations

import pytest

from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.activity_registry import (
    ActivityDefinition,
    ActivityRegistry,
    get_activity_registry,
    register_activity,
)


class TestActivityDefinition:
    def test_defaults(self) -> None:
        async def handler() -> str:
            return "ok"

        defn = ActivityDefinition(name="act1", handler=handler)
        assert defn.name == "act1"
        assert defn.timeout > 0


class TestActivityRegistry:
    @pytest.fixture
    def registry(self) -> ActivityRegistry:
        return ActivityRegistry()

    def test_register_and_get(self, registry: ActivityRegistry) -> None:
        async def handler() -> str:
            return "ok"

        registry.register("act1", handler, timeout=60)
        defn = registry.get("act1")
        assert defn is not None
        assert defn.name == "act1"
        assert defn.timeout == 60

    def test_list_activities(self, registry: ActivityRegistry) -> None:
        async def h1() -> None:
            pass

        async def h2() -> None:
            pass

        registry.register("a1", h1)
        registry.register("a2", h2)
        assert sorted(registry.list_activities()) == ["a1", "a2"]

    def test_has_activity(self, registry: ActivityRegistry) -> None:
        async def h() -> None:
            pass

        registry.register("a1", h)
        assert registry.has_activity("a1") is True
        assert registry.has_activity("missing") is False


class TestGlobalRegistry:
    def test_get_activity_registry_singleton(self) -> None:
        r1 = get_activity_registry()
        r2 = get_activity_registry()
        assert r1 is r2

    def test_register_activity_decorator(self) -> None:
        @register_activity("decorated_act", timeout=30)
        async def decorated() -> str:
            return "ok"

        registry = get_activity_registry()
        assert registry.has_activity("decorated_act")
