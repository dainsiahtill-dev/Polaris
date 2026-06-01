"""Tests for PM workflow child workflow timeout policy."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_activity.internal.workflows import (
    director_task_workflow as workflow_activity_director_task_workflow,
    director_workflow as workflow_activity_director_workflow,
    pm_workflow as workflow_activity_pm_workflow,
)
from polaris.cells.orchestration.workflow_runtime.internal.models import TaskContract
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.workflows import (
    director_task_workflow as workflow_runtime_director_task_workflow,
    director_workflow as workflow_runtime_director_workflow,
    pm_workflow as workflow_runtime_pm_workflow,
)


def test_runtime_pm_director_child_timeout_scales_with_task_count() -> None:
    timeout_seconds = workflow_runtime_pm_workflow._director_child_workflow_timeout_seconds(
        {"task_timeout_seconds": 900, "ready_timeout_seconds": 30},
        task_count=3,
    )
    assert timeout_seconds == 2850


def test_activity_pm_director_child_timeout_scales_with_task_count() -> None:
    timeout_seconds = workflow_activity_pm_workflow._director_child_workflow_timeout_seconds(
        {"task_timeout_seconds": 900, "ready_timeout_seconds": 30},
        task_count=3,
    )
    assert timeout_seconds == 2850


def test_director_child_timeout_is_capped_at_global_workflow_timeout() -> None:
    runtime_timeout = workflow_runtime_pm_workflow._director_child_workflow_timeout_seconds(
        {"task_timeout_seconds": 3600, "ready_timeout_seconds": 30},
        task_count=3,
    )
    activity_timeout = workflow_activity_pm_workflow._director_child_workflow_timeout_seconds(
        {"task_timeout_seconds": 3600, "ready_timeout_seconds": 30},
        task_count=3,
    )
    assert runtime_timeout == 3600
    assert activity_timeout == 3600


def test_director_dependency_parser_accepts_dependencies_alias() -> None:
    task = TaskContract(
        task_id="T02",
        title="Dependent task",
        payload={"id": "T02", "title": "Dependent task", "dependencies": ["T01"]},
    )

    assert workflow_runtime_director_workflow._task_dependencies(task) == {"T01"}
    assert workflow_activity_director_workflow._task_dependencies(task) == {"T01"}


def test_director_task_timeout_scales_with_scope_only_round_estimate() -> None:
    task = TaskContract(
        task_id="T01",
        title="Scope-only Director task",
        payload={
            "id": "T01",
            "title": "Scope-only Director task",
            "target_files": [],
            "scope_paths": [
                "src/auth",
                "src/middleware",
                "src/api",
                "tests",
                "src/api/tasks",
                "tests/integration",
            ],
        },
    )

    assert workflow_runtime_director_workflow._task_run_timeout_seconds(task, 900) == 3600
    assert workflow_activity_director_workflow._task_run_timeout_seconds(task, 900) == 3600


def test_director_task_timeout_keeps_larger_configured_budget() -> None:
    task = TaskContract(
        task_id="T01",
        title="Small Director task",
        payload={"id": "T01", "title": "Small Director task", "target_files": ["src/app.ts"]},
    )

    assert workflow_runtime_director_workflow._task_run_timeout_seconds(task, 3600) == 3600
    assert workflow_activity_director_workflow._task_run_timeout_seconds(task, 3600) == 3600


def test_director_implementation_phase_timeout_scales_with_target_files() -> None:
    task = TaskContract(
        task_id="T01",
        title="Five round Director task",
        payload={
            "id": "T01",
            "title": "Five round Director task",
            "target_files": [
                "src/schemas/task_contract_v1.schema.json",
                "src/gateway/middleware/validateTaskContract.ts",
                "src/api/v1/tasks/router.ts",
                "tests/unit/task_contract_validation.test.ts",
                "tests/integration/tasks_contract_api.test.ts",
            ],
        },
    )

    assert workflow_runtime_director_task_workflow._task_phase_timeout_seconds(task, "implement", 900) == 3330
    assert workflow_activity_director_task_workflow._task_phase_timeout_seconds(task, "implement", 900) == 3330


def test_director_non_implementation_phase_keeps_base_timeout() -> None:
    task = TaskContract(
        task_id="T01",
        title="Validation task",
        payload={"id": "T01", "title": "Validation task", "target_files": ["src/app.ts"]},
    )

    assert workflow_runtime_director_task_workflow._task_phase_timeout_seconds(task, "validate", 900) == 900
    assert workflow_activity_director_task_workflow._task_phase_timeout_seconds(task, "validate", 900) == 900
