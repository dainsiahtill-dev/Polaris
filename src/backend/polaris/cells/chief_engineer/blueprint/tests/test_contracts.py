"""Tests for Chief Engineer Blueprint Public Contracts.

Tests the public contracts for chief_engineer.blueprint cell including
GenerateTaskBlueprintCommandV1, GetBlueprintStatusQueryV1, TaskBlueprintResultV1,
and related contracts.
"""

from __future__ import annotations

import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ChiefEngineerBlueprintError,
    ChiefEngineerBlueprintErrorV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    TaskBlueprintGeneratedEventV1,
    TaskBlueprintResultV1,
)


class TestGenerateTaskBlueprintCommandV1:
    """Tests for GenerateTaskBlueprintCommandV1 contract."""

    def test_command_required_fields(self) -> None:
        """Test command with required fields only."""
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-001",
            workspace="/workspace",
            objective="Implement user authentication",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/workspace"
        assert cmd.objective == "Implement user authentication"
        assert cmd.run_id is None
        assert cmd.constraints == {}
        assert cmd.context == {}

    def test_command_with_run_id(self) -> None:
        """Test command with run_id."""
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-001",
            workspace="/workspace",
            objective="Implement feature",
            run_id="run-123",
        )
        assert cmd.run_id == "run-123"

    def test_command_with_constraints(self) -> None:
        """Test command with constraints."""
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-001",
            workspace="/workspace",
            objective="Implement feature",
            constraints={"performance": "low_latency", "scale": "1000_rps"},
        )
        assert cmd.constraints == {"performance": "low_latency", "scale": "1000_rps"}

    def test_command_with_context(self) -> None:
        """Test command with context."""
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-001",
            workspace="/workspace",
            objective="Implement feature",
            context={"team_size": 5, "language": "python"},
        )
        assert cmd.context == {"team_size": 5, "language": "python"}

    def test_command_empty_task_id_raises(self) -> None:
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GenerateTaskBlueprintCommandV1(
                task_id="",
                workspace="/workspace",
                objective="Test",
            )

    def test_command_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GenerateTaskBlueprintCommandV1(
                task_id="task-001",
                workspace="",
                objective="Test",
            )

    def test_command_empty_objective_raises(self) -> None:
        """Test that empty objective raises ValueError."""
        with pytest.raises(ValueError, match="objective must be a non-empty string"):
            GenerateTaskBlueprintCommandV1(
                task_id="task-001",
                workspace="/workspace",
                objective="",
            )

    def test_command_whitespace_normalized(self) -> None:
        """Test that whitespace is normalized."""
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="  task-001  ",
            workspace="  /workspace  ",
            objective="  Implement feature  ",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/workspace"
        assert cmd.objective == "Implement feature"


class TestGetBlueprintStatusQueryV1:
    """Tests for GetBlueprintStatusQueryV1 contract."""

    def test_query_required_fields(self) -> None:
        """Test query with required fields only."""
        query = GetBlueprintStatusQueryV1(
            task_id="task-001",
            workspace="/workspace",
        )
        assert query.task_id == "task-001"
        assert query.workspace == "/workspace"
        assert query.run_id is None

    def test_query_with_run_id(self) -> None:
        """Test query with run_id."""
        query = GetBlueprintStatusQueryV1(
            task_id="task-001",
            workspace="/workspace",
            run_id="run-123",
        )
        assert query.run_id == "run-123"

    def test_query_empty_task_id_raises(self) -> None:
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetBlueprintStatusQueryV1(
                task_id="",
                workspace="/workspace",
            )

    def test_query_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GetBlueprintStatusQueryV1(
                task_id="task-001",
                workspace="",
            )


class TestTaskBlueprintGeneratedEventV1:
    """Tests for TaskBlueprintGeneratedEventV1 contract."""

    def test_event_required_fields(self) -> None:
        """Test event with required fields only."""
        event = TaskBlueprintGeneratedEventV1(
            event_id="evt-001",
            task_id="task-001",
            workspace="/workspace",
            blueprint_path="/workspace/blueprints/task-001.md",
            generated_at="2024-01-01T00:00:00Z",
        )
        assert event.event_id == "evt-001"
        assert event.task_id == "task-001"
        assert event.workspace == "/workspace"
        assert event.blueprint_path == "/workspace/blueprints/task-001.md"
        assert event.generated_at == "2024-01-01T00:00:00Z"
        assert event.risk_level is None

    def test_event_with_risk_level(self) -> None:
        """Test event with risk level."""
        event = TaskBlueprintGeneratedEventV1(
            event_id="evt-001",
            task_id="task-001",
            workspace="/workspace",
            blueprint_path="/workspace/blueprints/task-001.md",
            generated_at="2024-01-01T00:00:00Z",
            risk_level="high",
        )
        assert event.risk_level == "high"

    def test_event_empty_event_id_raises(self) -> None:
        """Test that empty event_id raises ValueError."""
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            TaskBlueprintGeneratedEventV1(
                event_id="",
                task_id="task-001",
                workspace="/workspace",
                blueprint_path="/path",
                generated_at="2024-01-01T00:00:00Z",
            )

    def test_event_empty_task_id_raises(self) -> None:
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            TaskBlueprintGeneratedEventV1(
                event_id="evt-001",
                task_id="",
                workspace="/workspace",
                blueprint_path="/path",
                generated_at="2024-01-01T00:00:00Z",
            )

    def test_event_empty_blueprint_path_raises(self) -> None:
        """Test that empty blueprint_path raises ValueError."""
        with pytest.raises(ValueError, match="blueprint_path must be a non-empty string"):
            TaskBlueprintGeneratedEventV1(
                event_id="evt-001",
                task_id="task-001",
                workspace="/workspace",
                blueprint_path="",
                generated_at="2024-01-01T00:00:00Z",
            )

    def test_event_empty_generated_at_raises(self) -> None:
        """Test that empty generated_at raises ValueError."""
        with pytest.raises(ValueError, match="generated_at must be a non-empty string"):
            TaskBlueprintGeneratedEventV1(
                event_id="evt-001",
                task_id="task-001",
                workspace="/workspace",
                blueprint_path="/path",
                generated_at="",
            )


class TestTaskBlueprintResultV1:
    """Tests for TaskBlueprintResultV1 contract."""

    def test_success_result(self) -> None:
        """Test successful result construction."""
        result = TaskBlueprintResultV1(
            ok=True,
            task_id="task-001",
            workspace="/workspace",
            status="completed",
        )
        assert result.ok is True
        assert result.status == "completed"
        assert result.blueprint_path is None
        assert result.summary == ""
        assert result.recommendations == ()
        assert result.risks == ()

    def test_result_with_blueprint_path(self) -> None:
        """Test result with blueprint path."""
        result = TaskBlueprintResultV1(
            ok=True,
            task_id="task-001",
            workspace="/workspace",
            status="completed",
            blueprint_path="/workspace/blueprints/task-001.md",
        )
        assert result.blueprint_path == "/workspace/blueprints/task-001.md"

    def test_result_with_summary(self) -> None:
        """Test result with summary."""
        result = TaskBlueprintResultV1(
            ok=True,
            task_id="task-001",
            workspace="/workspace",
            status="completed",
            summary="Blueprint generated with 5 tasks and 3 milestones",
        )
        assert result.summary == "Blueprint generated with 5 tasks and 3 milestones"

    def test_result_with_recommendations(self) -> None:
        """Test result with recommendations."""
        result = TaskBlueprintResultV1(
            ok=True,
            task_id="task-001",
            workspace="/workspace",
            status="completed",
            recommendations=[
                "Add integration tests",
                "Include API documentation",
            ],
        )
        assert result.recommendations == (
            "Add integration tests",
            "Include API documentation",
        )

    def test_result_with_risks(self) -> None:
        """Test result with risks."""
        result = TaskBlueprintResultV1(
            ok=True,
            task_id="task-001",
            workspace="/workspace",
            status="completed",
            risks=["High complexity", "Tight deadline"],
        )
        assert result.risks == ("High complexity", "Tight deadline")

    def test_result_failed(self) -> None:
        """Test failed result construction."""
        result = TaskBlueprintResultV1(
            ok=False,
            task_id="task-001",
            workspace="/workspace",
            status="failed",
        )
        assert result.ok is False
        assert result.status == "failed"

    def test_result_empty_task_id_raises(self) -> None:
        """Test that empty task_id raises ValueError."""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            TaskBlueprintResultV1(
                ok=True,
                task_id="",
                workspace="/workspace",
                status="completed",
            )

    def test_result_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            TaskBlueprintResultV1(
                ok=True,
                task_id="task-001",
                workspace="",
                status="completed",
            )

    def test_result_empty_status_raises(self) -> None:
        """Test that empty status raises ValueError."""
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            TaskBlueprintResultV1(
                ok=True,
                task_id="task-001",
                workspace="/workspace",
                status="",
            )


class TestChiefEngineerBlueprintErrorV1:
    """Tests for ChiefEngineerBlueprintErrorV1 contract."""

    def test_error_default_code(self) -> None:
        """Test error with default code."""
        err = ChiefEngineerBlueprintErrorV1("Blueprint generation failed")
        assert str(err) == "Blueprint generation failed"
        assert err.code == "chief_engineer_blueprint_error"
        assert err.details == {}

    def test_error_custom_code(self) -> None:
        """Test error with custom code."""
        err = ChiefEngineerBlueprintErrorV1(
            "Blueprint validation failed",
            code="VALIDATION_ERROR",
        )
        assert err.code == "VALIDATION_ERROR"

    def test_error_with_details(self) -> None:
        """Test error with details."""
        err = ChiefEngineerBlueprintErrorV1(
            "Blueprint generation failed",
            details={"task_id": "task-001", "phase": "planning"},
        )
        assert err.details == {"task_id": "task-001", "phase": "planning"}

    def test_error_empty_message_raises(self) -> None:
        """Test that empty message raises ValueError."""
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            ChiefEngineerBlueprintErrorV1("")

    def test_error_empty_code_raises(self) -> None:
        """Test that empty code raises ValueError."""
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            ChiefEngineerBlueprintErrorV1("msg", code="")


class TestChiefEngineerBlueprintErrorAlias:
    """Tests for backward-compatible alias."""

    def test_alias_is_same_class(self) -> None:
        """Test that alias is the same class."""
        assert ChiefEngineerBlueprintError is ChiefEngineerBlueprintErrorV1

    def test_alias_instantiation(self) -> None:
        """Test alias instantiation."""
        err = ChiefEngineerBlueprintError("alias test")
        assert isinstance(err, ChiefEngineerBlueprintErrorV1)
        assert isinstance(err, ChiefEngineerBlueprintError)
        assert err.code == "chief_engineer_blueprint_error"


class TestBlueprintLifecycle:
    """Tests for complete blueprint generation lifecycle scenarios."""

    def test_successful_blueprint_lifecycle(self) -> None:
        """Test complete successful blueprint generation lifecycle."""
        # Create command
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-auth-001",
            workspace="/workspace",
            objective="Implement JWT authentication system",
            run_id="run-001",
            constraints={"security": "high", "performance": "low_latency"},
            context={"team_size": 3, "language": "python"},
        )

        # Query status
        status_query = GetBlueprintStatusQueryV1(
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            run_id=cmd.run_id,
        )

        # Blueprint generated event
        event = TaskBlueprintGeneratedEventV1(
            event_id="evt-001",
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            blueprint_path="/workspace/blueprints/task-auth-001.md",
            generated_at="2024-01-01T00:00:00Z",
            risk_level="medium",
        )

        # Final result
        result = TaskBlueprintResultV1(
            ok=True,
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            status="completed",
            blueprint_path=event.blueprint_path,
            summary="Generated blueprint with 8 tasks, 3 milestones, and 2 risks identified",
            recommendations=[
                "Add rate limiting to auth endpoints",
                "Include OAuth2 support",
            ],
            risks=["Complex token refresh logic", "Session management complexity"],
        )

        # Verify consistency
        assert cmd.task_id == status_query.task_id == event.task_id == result.task_id
        assert cmd.workspace == status_query.workspace == event.workspace == result.workspace
        assert cmd.run_id == status_query.run_id
        assert result.ok is True
        assert result.blueprint_path == event.blueprint_path
        assert len(result.recommendations) == 2
        assert len(result.risks) == 2

    def test_failed_blueprint_lifecycle(self) -> None:
        """Test complete failed blueprint generation lifecycle."""
        # Create command
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-002",
            workspace="/workspace",
            objective="Implement feature",
        )

        # Query status
        status_query = GetBlueprintStatusQueryV1(
            task_id=cmd.task_id,
            workspace=cmd.workspace,
        )

        # Failed result
        result = TaskBlueprintResultV1(
            ok=False,
            task_id=cmd.task_id,
            workspace=cmd.workspace,
            status="failed",
        )

        # Verify consistency
        assert cmd.task_id == status_query.task_id == result.task_id
        assert result.ok is False

    def test_error_propagation(self) -> None:
        """Test error is properly constructed and raised."""
        try:
            raise ChiefEngineerBlueprintError(
                "Objective validation failed",
                code="INVALID_OBJECTIVE",
                details={"objective": "", "reason": "empty"},
            )
        except ChiefEngineerBlueprintError as e:
            assert str(e) == "Objective validation failed"
            assert e.code == "INVALID_OBJECTIVE"
            assert e.details["reason"] == "empty"
