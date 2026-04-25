"""Tests for workflow_runtime internal runtime_engine workflow_registry module."""

from __future__ import annotations

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.workflow_registry import (
    WorkflowDefinition,
    WorkflowRegistry,
    get_workflow_registry,
    register_workflow,
)


class TestWorkflowDefinition:
    def test_defaults(self) -> None:
        async def handler() -> dict:
            return {}

        defn = WorkflowDefinition(name="wf1", handler=handler)
        assert defn.name == "wf1"
        assert defn.timeout > 0


class TestWorkflowRegistry:
    @pytest.fixture
    def registry(self) -> WorkflowRegistry:
        return WorkflowRegistry()

    def test_register_and_get(self, registry: WorkflowRegistry) -> None:
        async def handler() -> dict:
            return {"ok": True}

        registry.register("wf1", handler, timeout=120)
        defn = registry.get("wf1")
        assert defn is not None
        assert defn.name == "wf1"
        assert defn.timeout == 120

    def test_list_workflows(self, registry: WorkflowRegistry) -> None:
        async def h1() -> dict:
            return {}

        async def h2() -> dict:
            return {}

        registry.register("w1", h1)
        registry.register("w2", h2)
        assert sorted(registry.list_workflows()) == ["w1", "w2"]

    def test_has_workflow(self, registry: WorkflowRegistry) -> None:
        async def h() -> dict:
            return {}

        registry.register("w1", h)
        assert registry.has_workflow("w1") is True
        assert registry.has_workflow("missing") is False


class TestGlobalRegistry:
    def test_get_workflow_registry_singleton(self) -> None:
        r1 = get_workflow_registry()
        r2 = get_workflow_registry()
        assert r1 is r2

    def test_register_workflow_decorator(self) -> None:
        @register_workflow("decorated_wf", timeout=60)
        async def decorated() -> dict:
            return {"ok": True}

        registry = get_workflow_registry()
        assert registry.has_workflow("decorated_wf")
