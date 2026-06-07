"""Tests for Director workflow timeout ownership."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_activity.internal.workflows import (
    director_task_workflow as workflow_activity_director_task_workflow,
)
from polaris.cells.orchestration.workflow_runtime.internal.models import TaskContract
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.workflows import (
    director_task_workflow as workflow_runtime_director_task_workflow,
)


def _large_implementation_task() -> TaskContract:
    return TaskContract(
        task_id="T01",
        title="Large implementation task",
        payload={
            "id": "T01",
            "title": "Large implementation task",
            "target_files": [f"src/module_{index}.py" for index in range(20)],
        },
    )


def test_runtime_director_task_timeout_caps_at_global_workflow_budget() -> None:
    assert (
        workflow_runtime_director_task_workflow._task_phase_timeout_seconds(
            _large_implementation_task(),
            "implement",
            900,
        )
        == workflow_runtime_director_task_workflow._DIRECTOR_TASK_TIMEOUT_MAX_SECONDS
    )


def test_activity_director_task_timeout_caps_at_global_workflow_budget() -> None:
    assert (
        workflow_activity_director_task_workflow._task_phase_timeout_seconds(
            _large_implementation_task(),
            "implement",
            900,
        )
        == workflow_activity_director_task_workflow._DIRECTOR_TASK_TIMEOUT_MAX_SECONDS
    )


def test_non_implementation_phase_keeps_configured_base_timeout() -> None:
    task = TaskContract(
        task_id="T02",
        title="Validation task",
        payload={"id": "T02", "title": "Validation task", "target_files": ["src/app.py"]},
    )

    assert workflow_runtime_director_task_workflow._task_phase_timeout_seconds(task, "validate", 900) == 900
    assert workflow_activity_director_task_workflow._task_phase_timeout_seconds(task, "validate", 900) == 900
