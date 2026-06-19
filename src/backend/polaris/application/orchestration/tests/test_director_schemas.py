"""Tests for Director orchestration Pydantic DTOs (AAA pattern).

Verifies:
    - Schema validation and coercion from raw dicts
    - Frozen immutability of result schemas
    - Field defaults and required field enforcement
    - Round-trip serialize/deserialize integrity
"""

from __future__ import annotations

import pytest
from polaris.application.orchestration.director_schemas import (
    DirectorAdapterInput,
    DirectorIterationSchema,
    DirectorSubmitResponse,
    DirectorTaskResultSchema,
    DirectorTaskSchema,
    DirectorWorkflowSubmission,
    DirectorWorkflowWaitResult,
)
from pydantic import ValidationError

# =============================================================================
# DirectorTaskSchema tests
# =============================================================================


class TestDirectorTaskSchema:
    """Tests for the DirectorTaskSchema DTO."""

    def test_create_with_required_fields_only(self) -> None:
        # Arrange
        data = {"id": "task-1", "subject": "Fix bug"}

        # Act
        task = DirectorTaskSchema.model_validate(data)

        # Assert
        assert task.id == "task-1"
        assert task.subject == "Fix bug"
        assert task.description == ""
        assert task.status == "pending"
        assert task.priority == "medium"

    def test_create_with_all_fields(self) -> None:
        # Arrange
        data = {
            "id": "task-2",
            "subject": "Add feature",
            "description": "Implement X",
            "status": "in_progress",
            "priority": "high",
            "owner": "alice",
            "assignee": "bob",
            "role": "developer",
            "metadata": {"source": "pm", "priority_num": 2},
        }

        # Act
        task = DirectorTaskSchema.model_validate(data)

        # Assert
        assert task.owner == "alice"
        assert task.assignee == "bob"
        assert task.metadata["source"] == "pm"

    def test_missing_required_field_raises(self) -> None:
        # Arrange
        data = {"id": "task-3"}  # missing subject

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            DirectorTaskSchema.model_validate(data)
        assert "subject" in str(exc_info.value).lower()

    def test_extra_fields_are_ignored(self) -> None:
        # Arrange
        data = {
            "id": "task-4",
            "subject": "Test",
            "unknown_field": "should_be_ignored",
        }

        # Act
        task = DirectorTaskSchema.model_validate(data)

        # Assert
        assert task.id == "task-4"
        assert not hasattr(task, "unknown_field")

    def test_roundtrip_serialization(self) -> None:
        # Arrange
        original = DirectorTaskSchema(id="t-1", subject="Test", status="ready")

        # Act
        data = original.model_dump()
        restored = DirectorTaskSchema.model_validate(data)

        # Assert
        assert restored.id == original.id
        assert restored.subject == original.subject
        assert restored.status == original.status


# =============================================================================
# DirectorTaskResultSchema tests
# =============================================================================


class TestDirectorTaskResultSchema:
    """Tests for the DirectorTaskResultSchema DTO."""

    def test_frozen_immutable(self) -> None:
        # Arrange
        result = DirectorTaskResultSchema(task_id="t-1", subject="Test", success=True, status="completed")

        # Act & Assert - Pydantic frozen raises ValidationError
        with pytest.raises(ValidationError):
            result.success = False  # type: ignore[misc]

    def test_valid_status_values(self) -> None:
        # Arrange & Act & Assert
        for status in ("completed", "failed", "skipped"):
            result = DirectorTaskResultSchema(task_id="t-1", subject="Test", success=True, status=status)
            assert result.status == status

    def test_invalid_status_raises(self) -> None:
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            DirectorTaskResultSchema(
                task_id="t-1",
                subject="Test",
                success=True,
                status="invalid_status",  # type: ignore[arg-type]
            )


# =============================================================================
# DirectorIterationSchema tests
# =============================================================================


class TestDirectorIterationSchema:
    """Tests for the DirectorIterationSchema DTO."""

    def test_create_with_defaults(self) -> None:
        # Arrange & Act
        iteration = DirectorIterationSchema(success=True, iteration=1)

        # Assert
        assert iteration.tasks_processed == 0
        assert iteration.tasks_succeeded == 0
        assert iteration.tasks_failed == 0
        assert iteration.results == []
        assert iteration.notes == ""

    def test_create_with_results(self) -> None:
        # Arrange
        results = [
            DirectorTaskResultSchema(task_id="t-1", subject="A", success=True, status="completed"),
            DirectorTaskResultSchema(task_id="t-2", subject="B", success=False, status="failed"),
        ]

        # Act
        iteration = DirectorIterationSchema(
            success=True,
            iteration=1,
            tasks_processed=2,
            tasks_succeeded=1,
            tasks_failed=1,
            results=results,
        )

        # Assert
        assert len(iteration.results) == 2
        assert iteration.results[0].task_id == "t-1"
        assert iteration.results[1].success is False


# =============================================================================
# DirectorSubmitResponse tests
# =============================================================================


class TestDirectorSubmitResponse:
    """Tests for the DirectorSubmitResponse DTO."""

    def test_default_status_is_submitted(self) -> None:
        # Arrange & Act
        resp = DirectorSubmitResponse(id="t-1", subject="Test")

        # Assert
        assert resp.status == "submitted"

    def test_frozen(self) -> None:
        # Arrange
        resp = DirectorSubmitResponse(id="t-1", subject="Test")

        # Act & Assert - Pydantic frozen raises ValidationError
        with pytest.raises(ValidationError):
            resp.id = "changed"  # type: ignore[misc]


# =============================================================================
# DirectorWorkflowSubmission tests
# =============================================================================


class TestDirectorWorkflowSubmission:
    """Tests for the DirectorWorkflowSubmission DTO."""

    def test_create_with_all_fields(self) -> None:
        # Arrange & Act
        sub = DirectorWorkflowSubmission(
            submitted=True,
            status="running",
            workflow_id="wf-1",
            workflow_run_id="run-1",
            error="",
        )

        # Assert
        assert sub.submitted is True
        assert sub.workflow_id == "wf-1"

    def test_create_with_error(self) -> None:
        # Arrange & Act
        sub = DirectorWorkflowSubmission(submitted=False, status="failed", error="timeout")

        # Assert
        assert sub.submitted is False
        assert sub.error == "timeout"


# =============================================================================
# DirectorWorkflowWaitResult tests
# =============================================================================


class TestDirectorWorkflowWaitResult:
    """Tests for the DirectorWorkflowWaitResult DTO."""

    def test_defaults(self) -> None:
        # Arrange & Act
        result = DirectorWorkflowWaitResult()

        # Assert
        assert result.status == ""
        assert result.error == ""


# =============================================================================
# DirectorAdapterInput tests
# =============================================================================


class TestDirectorAdapterInput:
    """Tests for the DirectorAdapterInput DTO."""

    def test_create_with_required_fields(self) -> None:
        # Arrange
        task = DirectorTaskSchema(id="t-1", subject="Test")
        data = {
            "task_id": "t-1",
            "pm_task_id": "pm-1",
            "id": "t-1",
            "subject": "Test",
            "task": task.model_dump(),
        }

        # Act
        adapter_input = DirectorAdapterInput.model_validate(data)

        # Assert
        assert adapter_input.task_id == "t-1"
        assert adapter_input.pm_task_id == "pm-1"
        assert adapter_input.task.id == "t-1"
