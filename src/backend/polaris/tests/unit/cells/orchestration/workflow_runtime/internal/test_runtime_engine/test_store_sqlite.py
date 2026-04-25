"""Tests for workflow_runtime internal runtime_engine store_sqlite shim module."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.embedded.store_sqlite import (
    SqliteRuntimeStore,
    WorkflowEvent,
    WorkflowExecution,
    WorkflowTaskState,
)


class TestReexports:
    def test_sqlite_runtime_store_is_class(self) -> None:
        assert isinstance(SqliteRuntimeStore, type)

    def test_workflow_event_is_class(self) -> None:
        assert isinstance(WorkflowEvent, type)

    def test_workflow_execution_is_class(self) -> None:
        assert isinstance(WorkflowExecution, type)

    def test_workflow_task_state_is_class(self) -> None:
        assert isinstance(WorkflowTaskState, type)
