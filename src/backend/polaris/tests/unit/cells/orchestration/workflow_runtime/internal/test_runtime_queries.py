"""Tests for workflow_runtime internal runtime_queries module."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.runtime_queries import WorkflowQueryState


class TestWorkflowQueryState:
    def test_initial_state(self) -> None:
        state = WorkflowQueryState()
        assert state._stage == "idle"
        assert state._history == []
        assert state._task_statuses == {}

    def test_record_event(self) -> None:
        state = WorkflowQueryState()
        state._record_event(stage="plan", message="started")
        assert state._stage == "plan"
        assert len(state._history) == 1

    def test_set_task_status(self) -> None:
        state = WorkflowQueryState()
        state._set_task_status("t1", "running", summary="ok")
        assert "t1" in state._task_statuses
        assert state._task_statuses["t1"].state == "running"

    def test_set_task_status_empty_id(self) -> None:
        state = WorkflowQueryState()
        state._set_task_status("", "running")
        assert state._task_statuses == {}

    def test_get_task_status(self) -> None:
        state = WorkflowQueryState()
        state._set_task_status("t1", "running")
        result = state.get_task_status("t1")
        assert result is not None
        assert result["state"] == "running"

    def test_get_task_status_missing(self) -> None:
        state = WorkflowQueryState()
        assert state.get_task_status("missing") is None

    def test_get_execution_history(self) -> None:
        state = WorkflowQueryState()
        state._record_event(stage="plan", message="m1")
        state._record_event(stage="exec", message="m2")
        history = state.get_execution_history()
        assert len(history) == 2

    def test_get_runtime_snapshot(self) -> None:
        state = WorkflowQueryState()
        state._record_event(stage="plan", message="m1")
        snapshot = state.get_runtime_snapshot()
        assert snapshot["stage"] == "plan"
        assert len(snapshot["history"]) == 1
