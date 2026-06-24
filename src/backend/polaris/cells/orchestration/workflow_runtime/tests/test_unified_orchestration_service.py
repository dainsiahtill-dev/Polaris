"""Tests for unified orchestration runtime adapter bridging."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service import (
    _adapter_input_for_role,
    _adapter_task_id_for_role,
)


def test_director_adapter_task_id_prefers_physical_taskboard_id() -> None:
    metadata = {
        "task_id": "TASK-1",
        "pm_task_id": "TASK-1",
        "external_task_id": "TASK-1",
    }

    resolved = _adapter_task_id_for_role(
        role_id="director",
        workflow_task_id="task-0-director",
        role_metadata=metadata,
    )

    assert resolved == "TASK-1"


def test_non_director_adapter_task_id_keeps_workflow_task_id() -> None:
    resolved = _adapter_task_id_for_role(
        role_id="qa",
        workflow_task_id="task-1-qa",
        role_metadata={"task_id": "TASK-1"},
    )

    assert resolved == "task-1-qa"


def test_director_adapter_input_makes_taskboard_id_explicit() -> None:
    metadata = {
        "task_id": "TASK-2",
        "pm_task_id": "TASK-2",
        "external_task_id": "TASK-2",
        "source_task_id": "TASK-2",
    }

    payload = _adapter_input_for_role(
        role_id="director",
        adapter_task_id="TASK-2",
        role_input="Execute PM task TASK-2",
        role_metadata=metadata,
    )

    assert payload["task_id"] == "TASK-2"
    assert payload["pm_task_id"] == "TASK-2"
    assert payload["external_task_id"] == "TASK-2"
    assert payload["source_task_id"] == "TASK-2"
    assert payload["metadata"] == metadata

