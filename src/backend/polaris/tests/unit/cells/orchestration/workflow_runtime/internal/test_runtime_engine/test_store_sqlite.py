"""Tests for workflow_runtime internal runtime_engine store_sqlite shim module."""

from __future__ import annotations

import asyncio

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.embedded.store_sqlite import (
    SqliteRuntimeStore,
    WorkflowEvent,
    WorkflowEventVersionConflictError,
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


@pytest.mark.asyncio
async def test_append_event_compare_and_swap_rejects_stale_cursor(tmp_path) -> None:
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    await store.create_execution("project-loop-1", "project_completion_convergence", {})

    committed = await store.append_event(
        "project-loop-1",
        "cursor_advanced",
        {"state": "residuals_classified"},
        expected_previous_seq=1,
    )

    assert committed.seq == 2
    with pytest.raises(WorkflowEventVersionConflictError) as stale:
        await store.append_event(
            "project-loop-1",
            "cursor_advanced",
            {"state": "next_leaf_published"},
            expected_previous_seq=1,
        )

    assert stale.value.workflow_id == "project-loop-1"
    assert stale.value.expected_previous_seq == 1
    assert stale.value.actual_previous_seq == 2
    assert [event.seq for event in await store.get_events("project-loop-1")] == [1, 2]


@pytest.mark.asyncio
async def test_append_event_compare_and_swap_is_cross_instance_atomic(tmp_path) -> None:
    db_path = str(tmp_path / "runtime.db")
    first_store = SqliteRuntimeStore(db_path, workspace=str(tmp_path))
    second_store = SqliteRuntimeStore(db_path, workspace=str(tmp_path))
    await first_store.create_execution("project-loop-1", "project_completion_convergence", {})

    results = await asyncio.gather(
        first_store.append_event(
            "project-loop-1",
            "cursor_advanced",
            {"writer": "first"},
            expected_previous_seq=1,
        ),
        second_store.append_event(
            "project-loop-1",
            "cursor_advanced",
            {"writer": "second"},
            expected_previous_seq=1,
        ),
        return_exceptions=True,
    )

    committed = [result for result in results if isinstance(result, WorkflowEvent)]
    rejected = [result for result in results if isinstance(result, WorkflowEventVersionConflictError)]
    assert len(committed) == 1
    assert len(rejected) == 1
    assert committed[0].seq == 2
    assert rejected[0].actual_previous_seq == 2
    assert [event.seq for event in await first_store.get_events("project-loop-1")] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_version", [True, -1, 1.5, "1"])
async def test_append_event_compare_and_swap_rejects_invalid_versions(
    tmp_path,
    invalid_version: object,
) -> None:
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    await store.create_execution("project-loop-1", "project_completion_convergence", {})

    with pytest.raises(ValueError, match="expected_previous_seq"):
        await store.append_event(
            "project-loop-1",
            "cursor_advanced",
            {"state": "residuals_classified"},
            expected_previous_seq=invalid_version,  # type: ignore[arg-type]
        )

    assert [event.seq for event in await store.get_events("project-loop-1")] == [1]
