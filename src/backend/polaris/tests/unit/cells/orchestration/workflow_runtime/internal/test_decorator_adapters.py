"""Tests for workflow_runtime internal decorator_adapters module."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.decorator_adapters import (
    ActivityAPI,
    WorkflowAPI,
    get_activity_api,
    get_workflow_api,
    is_workflow_query_method,
    is_workflow_run_method,
    is_workflow_signal_method,
    workflow,
)


class TestWorkflowAPI:
    def test_defn_decorator(self) -> None:
        @workflow.defn(name="test_workflow")
        class TestWorkflow:
            pass

        # decorator_adapters.WorkflowAPI.defn registers to workflow_registry
        # and returns the class unchanged (no __embedded_workflow_name__ here)
        from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.workflow_registry import (
            get_workflow_registry,
        )

        registry = get_workflow_registry()
        assert registry.has_workflow("test_workflow")

    def test_run_decorator(self) -> None:
        class Wf:
            @workflow.run
            async def run(self) -> None:
                pass

        assert is_workflow_run_method(Wf.run) is True

    def test_query_decorator(self) -> None:
        class Wf:
            @workflow.query(name="status")
            def get_status(self) -> str:
                return "ok"

        assert is_workflow_query_method(Wf.get_status) is True

    def test_signal_decorator(self) -> None:
        class Wf:
            @workflow.signal(name="pause")
            def pause(self) -> None:
                pass

        assert is_workflow_signal_method(Wf.pause) is True

    def test_get_workflow_api(self) -> None:
        api = get_workflow_api()
        assert isinstance(api, WorkflowAPI)


class TestActivityAPI:
    def test_get_activity_api(self) -> None:
        api = get_activity_api()
        assert isinstance(api, ActivityAPI)
