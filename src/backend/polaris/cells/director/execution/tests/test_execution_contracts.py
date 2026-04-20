"""Contract tests for director.execution cell.

Tests the public contracts and service boundaries of the director.execution cell.
"""

from __future__ import annotations

import pytest
from polaris.cells.director.execution.public.contracts import (
    DirectorExecutionError,
    DirectorExecutionResultV1,
    DirectorTaskCompletedEventV1,
    DirectorTaskStartedEventV1,
    ExecuteDirectorTaskCommandV1,
    GetDirectorTaskStatusQueryV1,
    RetryDirectorTaskCommandV1,
)


class TestExecuteDirectorTaskCommandV1:
    """Tests for ExecuteDirectorTaskCommandV1 contract."""

    def test_command_construction(self) -> None:
        """Test basic command construction."""
        cmd = ExecuteDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            instruction="Implement feature",
        )
        assert cmd.task_id == "task-123"
        assert cmd.workspace == "."
        assert cmd.instruction == "Implement feature"
        assert cmd.attempt == 1
        assert cmd.run_id is None

    def test_command_with_run_id(self) -> None:
        """Test command with run_id."""
        cmd = ExecuteDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            instruction="Implement feature",
            run_id="run-456",
            attempt=2,
        )
        assert cmd.run_id == "run-456"
        assert cmd.attempt == 2

    def test_command_empty_task_id_raises(self) -> None:
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(
                task_id="",
                workspace=".",
                instruction="Do something",
            )

    def test_command_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(
                task_id="task-123",
                workspace="",
                instruction="Do something",
            )

    def test_command_empty_instruction_raises(self) -> None:
        """Test that empty instruction raises ValueError."""
        with pytest.raises(ValueError, match="instruction must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(
                task_id="task-123",
                workspace=".",
                instruction="",
            )

    def test_command_invalid_attempt_raises(self) -> None:
        """Test that attempt < 1 raises ValueError."""
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            ExecuteDirectorTaskCommandV1(
                task_id="task-123",
                workspace=".",
                instruction="Do something",
                attempt=0,
            )

    def test_command_default_metadata(self) -> None:
        """Test that metadata defaults to empty dict."""
        cmd = ExecuteDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            instruction="Do something",
        )
        assert cmd.metadata == {}

    def test_command_with_metadata(self) -> None:
        """Test command with metadata."""
        cmd = ExecuteDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            instruction="Do something",
            metadata={"priority": "high", "tags": ["urgent"]},
        )
        assert cmd.metadata == {"priority": "high", "tags": ["urgent"]}


class TestRetryDirectorTaskCommandV1:
    """Tests for RetryDirectorTaskCommandV1 contract."""

    def test_retry_command_construction(self) -> None:
        """Test retry command construction."""
        cmd = RetryDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            reason="Previous attempt failed",
        )
        assert cmd.task_id == "task-123"
        assert cmd.reason == "Previous attempt failed"
        assert cmd.max_attempts == 3

    def test_retry_command_custom_max_attempts(self) -> None:
        """Test retry command with custom max_attempts."""
        cmd = RetryDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            reason="Retry",
            max_attempts=5,
        )
        assert cmd.max_attempts == 5

    def test_retry_command_invalid_max_attempts(self) -> None:
        """Test that max_attempts < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryDirectorTaskCommandV1(
                task_id="task-123",
                workspace=".",
                reason="Retry",
                max_attempts=0,
            )


class TestGetDirectorTaskStatusQueryV1:
    """Tests for GetDirectorTaskStatusQueryV1 contract."""

    def test_status_query_construction(self) -> None:
        """Test status query construction."""
        query = GetDirectorTaskStatusQueryV1(
            task_id="task-123",
            workspace=".",
        )
        assert query.task_id == "task-123"
        assert query.workspace == "."
        assert query.run_id is None

    def test_status_query_with_run_id(self) -> None:
        """Test status query with run_id."""
        query = GetDirectorTaskStatusQueryV1(
            task_id="task-123",
            workspace=".",
            run_id="run-456",
        )
        assert query.run_id == "run-456"


class TestDirectorTaskStartedEventV1:
    """Tests for DirectorTaskStartedEventV1 contract."""

    def test_started_event_construction(self) -> None:
        """Test started event construction."""
        event = DirectorTaskStartedEventV1(
            event_id="evt-001",
            task_id="task-123",
            workspace=".",
            started_at="2024-01-01T00:00:00Z",
        )
        assert event.event_id == "evt-001"
        assert event.task_id == "task-123"
        assert event.started_at == "2024-01-01T00:00:00Z"

    def test_started_event_with_run_id(self) -> None:
        """Test started event with run_id."""
        event = DirectorTaskStartedEventV1(
            event_id="evt-001",
            task_id="task-123",
            workspace=".",
            started_at="2024-01-01T00:00:00Z",
            run_id="run-456",
        )
        assert event.run_id == "run-456"


class TestDirectorTaskCompletedEventV1:
    """Tests for DirectorTaskCompletedEventV1 contract."""

    def test_completed_event_construction(self) -> None:
        """Test completed event construction."""
        event = DirectorTaskCompletedEventV1(
            event_id="evt-002",
            task_id="task-123",
            workspace=".",
            status="completed",
            completed_at="2024-01-01T01:00:00Z",
        )
        assert event.status == "completed"
        assert event.completed_at == "2024-01-01T01:00:00Z"

    def test_completed_event_failed(self) -> None:
        """Test completed event for failed task."""
        event = DirectorTaskCompletedEventV1(
            event_id="evt-002",
            task_id="task-123",
            workspace=".",
            status="failed",
            completed_at="2024-01-01T01:00:00Z",
            error_code="TIMEOUT",
            error_message="Task timed out",
        )
        assert event.status == "failed"
        assert event.error_code == "TIMEOUT"
        assert event.error_message == "Task timed out"


class TestDirectorExecutionResultV1:
    """Tests for DirectorExecutionResultV1 contract."""

    def test_success_result(self) -> None:
        """Test successful result construction."""
        result = DirectorExecutionResultV1(
            ok=True,
            task_id="task-123",
            workspace=".",
            status="completed",
        )
        assert result.ok is True
        assert result.status == "completed"

    def test_success_result_with_evidence(self) -> None:
        """Test successful result with evidence paths."""
        result = DirectorExecutionResultV1(
            ok=True,
            task_id="task-123",
            workspace=".",
            status="completed",
            evidence_paths=("/path/to/evidence1", "/path/to/evidence2"),
            output_summary="Task completed successfully",
        )
        assert result.evidence_paths == ("/path/to/evidence1", "/path/to/evidence2")
        assert result.output_summary == "Task completed successfully"

    def test_failed_result_requires_error(self) -> None:
        """Test that failed result requires error_code or error_message."""
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            DirectorExecutionResultV1(
                ok=False,
                task_id="task-123",
                workspace=".",
                status="failed",
            )

    def test_failed_result_with_error_code(self) -> None:
        """Test failed result with error_code."""
        result = DirectorExecutionResultV1(
            ok=False,
            task_id="task-123",
            workspace=".",
            status="failed",
            error_code="EXECUTION_ERROR",
        )
        assert result.ok is False
        assert result.error_code == "EXECUTION_ERROR"

    def test_failed_result_with_error_message(self) -> None:
        """Test failed result with error_message."""
        result = DirectorExecutionResultV1(
            ok=False,
            task_id="task-123",
            workspace=".",
            status="failed",
            error_message="Something went wrong",
        )
        assert result.error_message == "Something went wrong"


class TestDirectorExecutionError:
    """Tests for DirectorExecutionError contract."""

    def test_error_basic(self) -> None:
        """Test basic error construction."""
        err = DirectorExecutionError("Task execution failed")
        assert str(err) == "Task execution failed"
        assert err.code == "director_execution_error"
        assert err.details == {}

    def test_error_with_code(self) -> None:
        """Test error with custom code."""
        err = DirectorExecutionError("Failed", code="CUSTOM_ERROR")
        assert err.code == "CUSTOM_ERROR"

    def test_error_with_details(self) -> None:
        """Test error with details."""
        err = DirectorExecutionError(
            "Failed",
            details={"task_id": "task-123", "attempt": 3},
        )
        assert err.details == {"task_id": "task-123", "attempt": 3}

    def test_error_empty_message_raises(self) -> None:
        """Test that empty message raises ValueError."""
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            DirectorExecutionError("")


class TestTaskLifecycle:
    """Tests for task lifecycle scenarios."""

    def test_task_lifecycle_flow(self) -> None:
        """Test complete task lifecycle from start to completion."""
        # Create execution command
        cmd = ExecuteDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            instruction="Implement feature",
        )

        # Task started event
        started_event = DirectorTaskStartedEventV1(
            event_id="evt-001",
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            started_at="2024-01-01T00:00:00Z",
            run_id="run-456",
        )

        # Task completed event
        completed_event = DirectorTaskCompletedEventV1(
            event_id="evt-002",
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            status="completed",
            completed_at="2024-01-01T01:00:00Z",
            run_id="run-456",
        )

        # Success result
        result = DirectorExecutionResultV1(
            ok=True,
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            status="completed",
            run_id="run-456",
        )

        assert started_event.task_id == completed_event.task_id == result.task_id

    def test_task_retry_flow(self) -> None:
        """Test task retry flow."""
        # Initial attempt
        cmd1 = ExecuteDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            instruction="Implement feature",
            attempt=1,
        )

        # Retry command
        retry_cmd = RetryDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            reason="Previous attempt failed",
            max_attempts=3,
        )

        # Second attempt
        cmd2 = ExecuteDirectorTaskCommandV1(
            task_id="task-123",
            workspace=".",
            instruction="Implement feature",
            attempt=2,
        )

        assert cmd1.task_id == retry_cmd.task_id == cmd2.task_id
        assert cmd2.attempt == cmd1.attempt + 1
