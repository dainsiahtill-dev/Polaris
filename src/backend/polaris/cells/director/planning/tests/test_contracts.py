"""Tests for Director Planning Public Contracts.

Tests the public contracts for director.planning cell including
PlanDirectorTaskCommandV1, GetDirectorStatusQueryV1, DirectorPlanningResultV1,
and related contracts.
"""

from __future__ import annotations

import pytest
from polaris.cells.director.planning.public.contracts import (
    DirectorPlanningError,
    DirectorPlanningResultV1,
    GetDirectorStatusQueryV1,
    PlanDirectorTaskCommandV1,
)


class TestPlanDirectorTaskCommandV1:
    """Tests for PlanDirectorTaskCommandV1 contract."""

    def test_command_required_fields(self) -> None:
        """Test command with required fields only."""
        cmd = PlanDirectorTaskCommandV1(
            task_id="task-001",
            workspace="/workspace",
            instruction="Implement user authentication",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/workspace"
        assert cmd.instruction == "Implement user authentication"
        assert cmd.run_id is None
        assert cmd.attempt == 1
        assert cmd.metadata == {}

    def test_command_with_run_id(self) -> None:
        """Test command with run_id."""
        cmd = PlanDirectorTaskCommandV1(
            task_id="task-001",
            workspace="/workspace",
            instruction="Implement feature",
            run_id="run-123",
        )
        assert cmd.run_id == "run-123"

    def test_command_with_attempt(self) -> None:
        """Test command with attempt number."""
        cmd = PlanDirectorTaskCommandV1(
            task_id="task-001",
            workspace="/workspace",
            instruction="Implement feature",
            attempt=3,
        )
        assert cmd.attempt == 3

    def test_command_with_metadata(self) -> None:
        """Test command with metadata."""
        cmd = PlanDirectorTaskCommandV1(
            task_id="task-001",
            workspace="/workspace",
            instruction="Implement feature",
            metadata={"priority": "high", "tags": ["urgent"]},
        )
        assert cmd.metadata == {"priority": "high", "tags": ["urgent"]}

    def test_command_empty_task_id_raises(self) -> None:
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            PlanDirectorTaskCommandV1(
                task_id="",
                workspace="/workspace",
                instruction="Test",
            )

    def test_command_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            PlanDirectorTaskCommandV1(
                task_id="task-001",
                workspace="",
                instruction="Test",
            )

    def test_command_empty_instruction_raises(self) -> None:
        """Test that empty instruction raises ValueError."""
        with pytest.raises(ValueError, match="instruction must be a non-empty string"):
            PlanDirectorTaskCommandV1(
                task_id="task-001",
                workspace="/workspace",
                instruction="",
            )

    def test_command_invalid_attempt_raises(self) -> None:
        """Test that attempt < 1 raises ValueError."""
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            PlanDirectorTaskCommandV1(
                task_id="task-001",
                workspace="/workspace",
                instruction="Test",
                attempt=0,
            )

    def test_command_whitespace_normalized(self) -> None:
        """Test that whitespace is normalized."""
        cmd = PlanDirectorTaskCommandV1(
            task_id="  task-001  ",
            workspace="  /workspace  ",
            instruction="  Implement feature  ",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/workspace"
        assert cmd.instruction == "Implement feature"


class TestGetDirectorStatusQueryV1:
    """Tests for GetDirectorStatusQueryV1 contract."""

    def test_query_required_fields(self) -> None:
        """Test query with required fields only."""
        query = GetDirectorStatusQueryV1(
            task_id="task-001",
            workspace="/workspace",
        )
        assert query.task_id == "task-001"
        assert query.workspace == "/workspace"
        assert query.run_id is None

    def test_query_with_run_id(self) -> None:
        """Test query with run_id."""
        query = GetDirectorStatusQueryV1(
            task_id="task-001",
            workspace="/workspace",
            run_id="run-123",
        )
        assert query.run_id == "run-123"

    def test_query_empty_task_id_raises(self) -> None:
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetDirectorStatusQueryV1(
                task_id="",
                workspace="/workspace",
            )

    def test_query_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GetDirectorStatusQueryV1(
                task_id="task-001",
                workspace="",
            )


class TestDirectorPlanningResultV1:
    """Tests for DirectorPlanningResultV1 contract."""

    def test_success_result(self) -> None:
        """Test successful result construction."""
        result = DirectorPlanningResultV1(
            ok=True,
            task_id="task-001",
            workspace="/workspace",
            status="completed",
        )
        assert result.ok is True
        assert result.status == "completed"
        assert result.run_id is None
        assert result.plan_summary == ""
        assert result.error_code is None
        assert result.error_message is None

    def test_result_with_run_id(self) -> None:
        """Test result with run_id."""
        result = DirectorPlanningResultV1(
            ok=True,
            task_id="task-001",
            workspace="/workspace",
            status="completed",
            run_id="run-123",
        )
        assert result.run_id == "run-123"

    def test_result_with_plan_summary(self) -> None:
        """Test result with plan summary."""
        result = DirectorPlanningResultV1(
            ok=True,
            task_id="task-001",
            workspace="/workspace",
            status="completed",
            plan_summary="Planned 5 tasks with 2 milestones",
        )
        assert result.plan_summary == "Planned 5 tasks with 2 milestones"

    def test_failed_result_with_error_code(self) -> None:
        """Test failed result with error code."""
        result = DirectorPlanningResultV1(
            ok=False,
            task_id="task-001",
            workspace="/workspace",
            status="failed",
            error_code="PLANNING_TIMEOUT",
        )
        assert result.ok is False
        assert result.error_code == "PLANNING_TIMEOUT"

    def test_failed_result_with_error_message(self) -> None:
        """Test failed result with error message."""
        result = DirectorPlanningResultV1(
            ok=False,
            task_id="task-001",
            workspace="/workspace",
            status="failed",
            error_message="Planning exceeded time limit",
        )
        assert result.ok is False
        assert result.error_message == "Planning exceeded time limit"

    def test_failed_result_requires_error(self) -> None:
        """Test that failed result requires error_code or error_message."""
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            DirectorPlanningResultV1(
                ok=False,
                task_id="task-001",
                workspace="/workspace",
                status="failed",
            )

    def test_result_empty_task_id_raises(self) -> None:
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            DirectorPlanningResultV1(
                ok=True,
                task_id="",
                workspace="/workspace",
                status="completed",
            )

    def test_result_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            DirectorPlanningResultV1(
                ok=True,
                task_id="task-001",
                workspace="",
                status="completed",
            )

    def test_result_empty_status_raises(self) -> None:
        """Test that empty status raises ValueError."""
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            DirectorPlanningResultV1(
                ok=True,
                task_id="task-001",
                workspace="/workspace",
                status="",
            )


class TestDirectorPlanningError:
    """Tests for DirectorPlanningError contract."""

    def test_error_default_code(self) -> None:
        """Test error with default code."""
        err = DirectorPlanningError("Planning failed")
        assert str(err) == "Planning failed"
        assert err.code == "director_planning_error"
        assert err.details == {}

    def test_error_custom_code(self) -> None:
        """Test error with custom code."""
        err = DirectorPlanningError(
            "Planning validation failed",
            code="VALIDATION_ERROR",
        )
        assert err.code == "VALIDATION_ERROR"

    def test_error_with_details(self) -> None:
        """Test error with details."""
        err = DirectorPlanningError(
            "Planning failed",
            details={"task_id": "task-001", "phase": "analysis"},
        )
        assert err.details == {"task_id": "task-001", "phase": "analysis"}

    def test_error_empty_message_raises(self) -> None:
        """Test that empty message raises ValueError."""
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            DirectorPlanningError("")

    def test_error_empty_code_raises(self) -> None:
        """Test that empty code raises ValueError."""
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            DirectorPlanningError("msg", code="")


class TestDirectorPlanningLifecycle:
    """Tests for complete director planning lifecycle scenarios."""

    def test_successful_planning_lifecycle(self) -> None:
        """Test complete successful planning lifecycle."""
        # Create command
        cmd = PlanDirectorTaskCommandV1(
            task_id="task-auth-001",
            workspace="/workspace",
            instruction="Implement JWT authentication system",
            run_id="run-001",
            attempt=1,
            metadata={"priority": "high", "context": "greenfield"},
        )

        # Query status
        status_query = GetDirectorStatusQueryV1(
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            run_id=cmd.run_id,
        )

        # Final result
        result = DirectorPlanningResultV1(
            ok=True,
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            status="completed",
            run_id=cmd.run_id,
            plan_summary="Planned 8 tasks across 3 milestones: auth setup, JWT implementation, integration tests",
        )

        # Verify consistency
        assert cmd.task_id == status_query.task_id == result.task_id
        assert cmd.workspace == status_query.workspace == result.workspace
        assert cmd.run_id == status_query.run_id == result.run_id
        assert result.ok is True
        assert "8 tasks" in result.plan_summary

    def test_retry_planning_lifecycle(self) -> None:
        """Test planning retry lifecycle."""
        # Initial attempt
        cmd1 = PlanDirectorTaskCommandV1(
            task_id="task-001",
            workspace="/workspace",
            instruction="Implement feature",
            attempt=1,
        )

        # Failed result
        result1 = DirectorPlanningResultV1(
            ok=False,
            task_id=cmd1.task_id,
            workspace=cmd1.workspace,
            status="failed",
            error_code="TIMEOUT",
            error_message="Planning exceeded time limit",
        )

        # Retry attempt
        cmd2 = PlanDirectorTaskCommandV1(
            task_id=cmd1.task_id,
            workspace=cmd1.workspace,
            instruction=cmd1.instruction,
            attempt=2,
        )

        # Successful result
        result2 = DirectorPlanningResultV1(
            ok=True,
            task_id=cmd2.task_id,
            workspace=cmd2.workspace,
            status="completed",
            plan_summary="Planned 5 tasks",
        )

        # Verify retry chain
        assert cmd1.task_id == result1.task_id == cmd2.task_id == result2.task_id
        assert cmd2.attempt == 2
        assert result1.ok is False
        assert result2.ok is True

    def test_error_propagation(self) -> None:
        """Test error is properly constructed and raised."""
        try:
            raise DirectorPlanningError(
                "Instruction validation failed",
                code="INVALID_INSTRUCTION",
                details={"instruction": "", "reason": "empty"},
            )
        except DirectorPlanningError as e:
            assert str(e) == "Instruction validation failed"
            assert e.code == "INVALID_INSTRUCTION"
            assert e.details["reason"] == "empty"
