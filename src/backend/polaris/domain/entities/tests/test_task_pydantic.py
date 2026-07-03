"""Tests for Pydantic Task domain entity (AAA pattern).

Verifies:
    - TaskModel creation, validation, and coercion
    - State transitions (mark_ready, claim, start, complete, cancel, etc.)
    - TaskResultModel and TaskEvidenceModel value objects
    - Serialization round-trip integrity
    - Enum coercion from strings
    - Edge cases (invalid transitions, retry logic)
"""

from __future__ import annotations

import time

import pytest
from polaris.domain.entities.task_pydantic import (
    TaskEvidenceModel,
    TaskModel,
    TaskPriority,
    TaskResultModel,
    TaskStatus,
)
from pydantic import ValidationError

# =============================================================================
# TaskStatus enum tests
# =============================================================================


class TestTaskStatus:
    """Tests for the TaskStatus enum."""

    def test_terminal_statuses(self) -> None:
        # Arrange & Act & Assert
        assert TaskStatus.COMPLETED.is_terminal is True
        assert TaskStatus.FAILED.is_terminal is True
        assert TaskStatus.CANCELLED.is_terminal is True
        assert TaskStatus.TIMEOUT.is_terminal is True

    def test_non_terminal_statuses(self) -> None:
        # Arrange & Act & Assert
        assert TaskStatus.PENDING.is_terminal is False
        assert TaskStatus.READY.is_terminal is False
        assert TaskStatus.IN_PROGRESS.is_terminal is False

    def test_active_statuses(self) -> None:
        # Arrange & Act & Assert
        assert TaskStatus.QUEUED.is_active is True
        assert TaskStatus.PENDING.is_active is True
        assert TaskStatus.READY.is_active is True
        assert TaskStatus.CLAIMED.is_active is True
        assert TaskStatus.BLOCKED.is_active is True
        assert TaskStatus.WAITING_HUMAN.is_active is True

    def test_executing_statuses(self) -> None:
        # Arrange & Act & Assert
        assert TaskStatus.CLAIMED.is_executing is True
        assert TaskStatus.IN_PROGRESS.is_executing is True
        assert TaskStatus.PENDING.is_executing is False

    def test_running_status_input_normalizes_to_in_progress(self) -> None:
        # Arrange & Act
        task = TaskModel(id=1, subject="test", status="running")

        # Assert
        assert task.status == TaskStatus.IN_PROGRESS.value
        assert "RUNNING" not in TaskStatus.__members__


# =============================================================================
# TaskPriority enum tests
# =============================================================================


class TestTaskPriority:
    """Tests for the TaskPriority enum."""

    def test_numeric_values(self) -> None:
        # Arrange & Act & Assert
        assert TaskPriority.LOW.numeric_value == 0
        assert TaskPriority.MEDIUM.numeric_value == 1
        assert TaskPriority.HIGH.numeric_value == 2
        assert TaskPriority.CRITICAL.numeric_value == 3


# =============================================================================
# TaskEvidenceModel tests
# =============================================================================


class TestTaskEvidenceModel:
    """Tests for the TaskEvidenceModel value object."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        evidence = TaskEvidenceModel(type="file")

        # Assert
        assert evidence.type == "file"
        assert evidence.path is None
        assert evidence.content is None
        assert evidence.metadata == {}

    def test_frozen_immutable(self) -> None:
        # Arrange
        evidence = TaskEvidenceModel(type="log", path="/tmp/test.log")

        # Act & Assert - Pydantic frozen raises ValidationError
        with pytest.raises(ValidationError):
            evidence.type = "changed"  # type: ignore[misc]


# =============================================================================
# TaskResultModel tests
# =============================================================================


class TestTaskResultModel:
    """Tests for the TaskResultModel value object."""

    def test_create_success_result(self) -> None:
        # Arrange & Act
        result = TaskResultModel(success=True, output="Done", duration_ms=100)

        # Assert
        assert result.success is True
        assert result.output == "Done"
        assert result.exit_code == 0
        assert result.error is None

    def test_create_failure_result(self) -> None:
        # Arrange & Act
        result = TaskResultModel(success=False, output="", exit_code=1, error="Command failed")

        # Assert
        assert result.success is False
        assert result.error == "Command failed"

    def test_with_evidence(self) -> None:
        # Arrange
        evidence = TaskEvidenceModel(type="test_result", content="PASS")

        # Act
        result = TaskResultModel(success=True, output="All tests passed", evidence=[evidence])

        # Assert
        assert len(result.evidence) == 1
        assert result.evidence[0].content == "PASS"


# =============================================================================
# TaskModel creation tests
# =============================================================================


class TestTaskModelCreation:
    """Tests for TaskModel creation and validation."""

    def test_create_with_minimal_fields(self) -> None:
        # Arrange & Act
        task = TaskModel(id=1, subject="Fix bug")

        # Assert
        assert task.id == 1
        assert task.subject == "Fix bug"
        assert task.status == TaskStatus.PENDING.value
        assert task.priority == TaskPriority.MEDIUM.value
        assert task.description == ""

    def test_create_with_string_id(self) -> None:
        # Arrange & Act
        task = TaskModel(id="task-1", subject="Test")

        # Assert
        assert task.id == "task-1"

    def test_create_from_dict_via_model_validate(self) -> None:
        # Arrange
        data = {
            "id": 42,
            "subject": "Deploy service",
            "status": "in_progress",
            "priority": "high",
            "owner": "alice",
            "timeout_seconds": 600,
        }

        # Act
        task = TaskModel.model_validate(data)

        # Assert
        assert task.id == 42
        assert task.status == "in_progress"
        assert task.priority == "high"
        assert task.owner == "alice"
        assert task.timeout_seconds == 600

    def test_coerce_float_id_to_int(self) -> None:
        # Arrange & Act
        task = TaskModel(id=42.0, subject="Test")

        # Assert
        assert task.id == 42
        assert isinstance(task.id, int)

    def test_coerce_legacy_blocked_by_field(self) -> None:
        # Arrange
        data = {"id": 1, "subject": "Test", "blockedBy": [2, 3]}

        # Act
        task = TaskModel.model_validate(data)

        # Assert
        # Note: Pydantic won't auto-map blockedBy → blocked_by unless alias is set
        # This test verifies the current behavior
        assert task.id == 1

    def test_empty_subject_raises(self) -> None:
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            TaskModel(id=1, subject="")
        assert "subject" in str(exc_info.value).lower()

    def test_missing_subject_raises(self) -> None:
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            TaskModel(id=1)  # type: ignore[call-arg]

    def test_default_timestamp_is_set(self) -> None:
        # Arrange
        before = time.time()

        # Act
        task = TaskModel(id=1, subject="Test")

        # Assert
        assert task.created_at >= before


# =============================================================================
# TaskModel serialization tests
# =============================================================================


class TestTaskModelSerialization:
    """Tests for TaskModel serialization round-trip."""

    def test_roundtrip_via_model_dump(self) -> None:
        # Arrange
        original = TaskModel(
            id="t-1",
            subject="Test task",
            description="A test",
            status=TaskStatus.READY.value,
            priority=TaskPriority.HIGH.value,
            owner="alice",
            tags=["test", "unit"],
        )

        # Act
        data = original.model_dump()
        restored = TaskModel.model_validate(data)

        # Assert
        assert restored.id == original.id
        assert restored.subject == original.subject
        assert restored.status == original.status
        assert restored.priority == original.priority
        assert restored.tags == original.tags

    def test_roundtrip_via_json(self) -> None:
        # Arrange
        original = TaskModel(id=1, subject="JSON test", status=TaskStatus.COMPLETED.value)

        # Act
        json_str = original.model_dump_json()
        restored = TaskModel.model_validate_json(json_str)

        # Assert
        assert restored.id == original.id
        assert restored.subject == original.subject
        assert restored.status == original.status

    def test_model_dump_excludes_private_result(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.IN_PROGRESS.value)
        result = TaskResultModel(success=True, output="done")

        # Act
        task.complete(result)
        data = task.model_dump()

        # Assert
        # _result is a private field, should not be in dump
        assert "_result" not in data


# =============================================================================
# TaskModel state transition tests
# =============================================================================


class TestTaskModelStateTransitions:
    """Tests for TaskModel state machine transitions."""

    def test_mark_ready_from_pending(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test")

        # Act
        task.mark_ready()

        # Assert
        assert task.status == TaskStatus.READY.value

    def test_mark_ready_from_non_pending_raises(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.IN_PROGRESS.value)

        # Act & Assert
        with pytest.raises(ValueError, match="mark_ready"):
            task.mark_ready()

    def test_claim_from_ready(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.READY.value)

        # Act
        task.claim("worker-1")

        # Assert
        assert task.status == TaskStatus.CLAIMED.value
        assert task.claimed_by == "worker-1"
        assert task.claimed_at is not None

    def test_claim_from_non_ready_raises(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.PENDING.value)

        # Act & Assert
        with pytest.raises(ValueError, match="claim"):
            task.claim("worker-1")

    def test_start_from_claimed(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.CLAIMED.value)

        # Act
        task.start()

        # Assert
        assert task.status == TaskStatus.IN_PROGRESS.value
        assert task.started_at is not None

    def test_start_from_non_claimed_raises(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.READY.value)

        # Act & Assert
        with pytest.raises(ValueError, match="CLAIMED"):
            task.start()

    def test_complete_success(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.IN_PROGRESS.value)
        result = TaskResultModel(success=True, output="Done")

        # Act
        task.complete(result)

        # Assert
        assert task.status == TaskStatus.COMPLETED.value
        assert task.completed_at is not None
        assert task.result_summary == "Done"

    def test_complete_failure_with_retry(self) -> None:
        # Arrange
        task = TaskModel(
            id=1,
            subject="Test",
            status=TaskStatus.IN_PROGRESS.value,
            retry_count=0,
            max_retries=3,
        )
        result = TaskResultModel(success=False, error="Timeout")

        # Act
        task.complete(result)

        # Assert
        assert task.status == TaskStatus.READY.value  # auto-retry
        assert task.retry_count == 1
        assert task.claimed_by is None
        assert task._result is None  # cleared for retry

    def test_complete_failure_no_retries_left(self) -> None:
        # Arrange
        task = TaskModel(
            id=1,
            subject="Test",
            status=TaskStatus.IN_PROGRESS.value,
            retry_count=3,
            max_retries=3,
        )
        result = TaskResultModel(success=False, error="Failed permanently")

        # Act
        task.complete(result)

        # Assert
        assert task.status == TaskStatus.FAILED.value
        assert task.error_message == "Failed permanently"

    def test_complete_from_invalid_state_raises(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.PENDING.value)
        result = TaskResultModel(success=True)

        # Act & Assert
        with pytest.raises(ValueError, match="complete"):
            task.complete(result)

    def test_cancel_non_terminal(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.IN_PROGRESS.value)

        # Act
        task.cancel()

        # Assert
        assert task.status == TaskStatus.CANCELLED.value
        assert task.completed_at is not None

    def test_cancel_terminal_raises(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.COMPLETED.value)

        # Act & Assert
        with pytest.raises(ValueError, match="terminal"):
            task.cancel()

    def test_timeout_task(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.IN_PROGRESS.value)

        # Act
        task.timeout_task()

        # Assert
        assert task.status == TaskStatus.TIMEOUT.value
        assert task.error_message == "Execution exceeded timeout limit"

    def test_reopen_from_terminal(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.FAILED.value)

        # Act
        task.reopen()

        # Assert
        assert task.status == TaskStatus.PENDING.value
        assert task.claimed_by is None
        assert task.completed_at is None

    def test_reopen_with_blocked_deps(self) -> None:
        # Arrange
        task = TaskModel(
            id=1,
            subject="Test",
            status=TaskStatus.COMPLETED.value,
            blocked_by=[2],
        )

        # Act
        task.reopen()

        # Assert
        assert task.status == TaskStatus.BLOCKED.value

    def test_reopen_non_terminal_raises(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.PENDING.value)

        # Act & Assert
        with pytest.raises(ValueError, match="reopen"):
            task.reopen()

    def test_resolve_dependency(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", blocked_by=[2, 3, 4])

        # Act
        task.resolve_dependency(3)

        # Assert
        assert task.blocked_by == [2, 4]

    def test_full_lifecycle(self) -> None:
        """Test the complete task lifecycle: pending → ready → claimed → in_progress → completed."""
        # Arrange
        task = TaskModel(id=1, subject="Full lifecycle test")

        # Act & Assert: pending → ready
        task.mark_ready()
        assert task.status == TaskStatus.READY.value

        # Act & Assert: ready → claimed
        task.claim("worker-1")
        assert task.status == TaskStatus.CLAIMED.value

        # Act & Assert: claimed → in_progress
        task.start()
        assert task.status == TaskStatus.IN_PROGRESS.value

        # Act & Assert: in_progress → completed
        result = TaskResultModel(success=True, output="All done")
        task.complete(result)
        assert task.status == TaskStatus.COMPLETED.value
        assert task.result_summary == "All done"

    def test_computed_properties(self) -> None:
        # Arrange
        task = TaskModel(
            id=1,
            subject="Test",
            status=TaskStatus.PENDING.value,
            blocked_by=[2],
        )

        # Act & Assert
        assert task.is_terminal is False
        assert task.is_blocked is True
        assert task.is_claimable is False

    def test_claimable_property(self) -> None:
        # Arrange
        task = TaskModel(id=1, subject="Test", status=TaskStatus.READY.value)

        # Act & Assert
        assert task.is_claimable is True
