"""Tests for workflow_runtime internal decorator_adapters module."""

from __future__ import annotations

import pytest

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

        assert hasattr(TestWorkflow, "__embedded_workflow_name__")

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
