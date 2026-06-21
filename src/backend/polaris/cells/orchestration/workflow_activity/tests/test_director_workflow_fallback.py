"""Tests for Director workflow fallback path logging."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from polaris.cells.orchestration.workflow_activity.internal.models import TaskContract
from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
    _extract_ready_tasks,
)


@pytest.fixture
def sample_task_contracts() -> list[TaskContract]:
    """Return a list of sample task contracts for testing."""
    return [
        TaskContract(task_id="task-1", title="Task 1"),
        TaskContract(task_id="task-2", title="Task 2"),
    ]


def test_extract_ready_tasks_with_valid_payload(
    sample_task_contracts: list[TaskContract],
) -> None:
    """Test that valid payload extracts tasks correctly."""
    payload = {
        "payload": {
            "tasks": [
                {"task_id": "task-1", "title": "Task 1"},
                {"task_id": "task-2", "title": "Task 2"},
            ],
        },
    }
    result = _extract_ready_tasks(payload, fallback=sample_task_contracts)
    assert len(result) == 2
    assert result[0].task_id == "task-1"
    assert result[1].task_id == "task-2"


def test_extract_ready_tasks_with_invalid_payload_uses_fallback(
    sample_task_contracts: list[TaskContract],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that invalid payload uses fallback and logs warning."""
    payload: Any = None
    with caplog.at_level(logging.WARNING):
        result = _extract_ready_tasks(payload, fallback=sample_task_contracts)

    assert len(result) == 2
    assert result[0].task_id == "task-1"
    assert "payload is not a dict" in caplog.text


def test_extract_ready_tasks_with_missing_payload_key_uses_fallback(
    sample_task_contracts: list[TaskContract],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that missing payload key uses fallback and logs warning."""
    with caplog.at_level(logging.WARNING):
        result = _extract_ready_tasks({}, fallback=sample_task_contracts)

    assert len(result) == 2
    assert result[0].task_id == "task-1"
    assert "activity_payload is not a dict" in caplog.text


def test_extract_ready_tasks_with_empty_tasks_uses_fallback(
    sample_task_contracts: list[TaskContract],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that empty tasks list uses fallback and logs warning."""
    payload: dict[str, Any] = {
        "payload": {
            "tasks": [],
        },
    }
    with caplog.at_level(logging.WARNING):
        result = _extract_ready_tasks(payload, fallback=sample_task_contracts)

    assert len(result) == 2
    assert result[0].task_id == "task-1"
    assert "no valid tasks extracted from payload" in caplog.text


def test_extract_ready_tasks_with_invalid_task_items_uses_fallback(
    sample_task_contracts: list[TaskContract],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that invalid task items uses fallback and logs warning."""
    payload = {
        "payload": {
            "tasks": [
                {"invalid": "data"},
                {"also_invalid": "data"},
            ],
        },
    }
    with caplog.at_level(logging.WARNING):
        result = _extract_ready_tasks(payload, fallback=sample_task_contracts)

    assert len(result) == 2
    assert result[0].task_id == "task-1"
    assert "no valid tasks extracted from payload" in caplog.text


def test_extract_ready_tasks_with_partial_valid_tasks(
    sample_task_contracts: list[TaskContract],
) -> None:
    """Test that partial valid tasks are extracted correctly."""
    payload = {
        "payload": {
            "tasks": [
                {"id": "task-1", "title": "Task 1"},
                {"invalid": "data"},
                {"id": "task-3", "title": "Task 3"},
            ],
        },
    }
    result = _extract_ready_tasks(payload, fallback=sample_task_contracts)
    assert len(result) == 2
    assert result[0].task_id == "task-1"
    assert result[1].task_id == "task-3"


def test_extract_ready_tasks_fallback_not_mutated(
    sample_task_contracts: list[TaskContract],
) -> None:
    """Test that fallback list is not mutated."""
    original_fallback = list(sample_task_contracts)
    payload = None
    result = _extract_ready_tasks(payload, fallback=sample_task_contracts)

    assert len(result) == len(original_fallback)
    assert result is not sample_task_contracts  # should be a copy
    for i, contract in enumerate(result):
        assert contract.task_id == original_fallback[i].task_id
