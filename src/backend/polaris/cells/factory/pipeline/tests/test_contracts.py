"""Tests for Factory Pipeline Public Contracts.

Tests the public contracts for factory.pipeline cell including
StartFactoryRunCommandV1, CancelFactoryRunCommandV1, FactoryRunResultV1,
ProjectionExperimentResultV1, and related contracts.
"""

from __future__ import annotations

import pytest
from polaris.cells.factory.pipeline.public.contracts import (
    CancelFactoryRunCommandV1,
    FactoryPipelineError,
    FactoryRunCompletedEventV1,
    FactoryRunResultV1,
    FactoryRunStartedEventV1,
    GetFactoryRunStatusQueryV1,
    IFactoryPipeline,
    IFactoryProjectionLab,
    ListFactoryRunsQueryV1,
    ProjectionBackMappingRefreshResultV1,
    ProjectionExperimentResultV1,
    ProjectionReprojectionResultV1,
    RefreshProjectionBackMappingCommandV1,
    ReprojectProjectionExperimentCommandV1,
    RunProjectionExperimentCommandV1,
    StartFactoryRunCommandV1,
)


class TestStartFactoryRunCommandV1:
    """Tests for StartFactoryRunCommandV1 contract."""

    def test_command_construction(self) -> None:
        """Test basic command construction."""
        cmd = StartFactoryRunCommandV1(
            workspace="/workspace",
            run_name="test-run",
            stages=("stage1", "stage2"),
        )
        assert cmd.workspace == "/workspace"
        assert cmd.run_name == "test-run"
        assert cmd.stages == ("stage1", "stage2")
        assert cmd.options == {}

    def test_command_with_options(self) -> None:
        """Test command with options."""
        cmd = StartFactoryRunCommandV1(
            workspace="/workspace",
            run_name="test-run",
            stages=("stage1",),
            options={"verbose": True, "parallel": False},
        )
        assert cmd.options == {"verbose": True, "parallel": False}

    def test_command_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            StartFactoryRunCommandV1(
                workspace="",
                run_name="test",
                stages=("stage1",),
            )

    def test_command_empty_run_name_raises(self) -> None:
        """Test that empty run_name raises ValueError."""
        with pytest.raises(ValueError, match="run_name must be a non-empty string"):
            StartFactoryRunCommandV1(
                workspace="/ws",
                run_name="",
                stages=("stage1",),
            )

    def test_command_empty_stages_raises(self) -> None:
        """Test that empty stages raises ValueError."""
        with pytest.raises(ValueError, match="stages must not be empty"):
            StartFactoryRunCommandV1(
                workspace="/ws",
                run_name="test",
                stages=(),
            )

    def test_command_stages_whitespace_filtered(self) -> None:
        """Test that whitespace-only stages are filtered out."""
        cmd = StartFactoryRunCommandV1(
            workspace="/ws",
            run_name="test",
            stages=("stage1", "  ", "stage2"),
        )
        assert cmd.stages == ("stage1", "stage2")

    def test_command_whitespace_normalized(self) -> None:
        """Test that workspace and run_name whitespace is normalized."""
        cmd = StartFactoryRunCommandV1(
            workspace="  /ws  ",
            run_name="  test  ",
            stages=("stage1",),
        )
        assert cmd.workspace == "/ws"
        assert cmd.run_name == "test"
        assert cmd.stages == ("stage1",)

    def test_command_stages_whitespace_preserved(self) -> None:
        """Test that stages whitespace is preserved (only empty filtered)."""
        cmd = StartFactoryRunCommandV1(
            workspace="/ws",
            run_name="test",
            stages=("  stage1  ", "stage2"),
        )
        # stages are not normalized, only empty/whitespace-only values are filtered
        assert cmd.stages == ("  stage1  ", "stage2")


class TestCancelFactoryRunCommandV1:
    """Tests for CancelFactoryRunCommandV1 contract."""

    def test_command_construction(self) -> None:
        """Test basic command construction."""
        cmd = CancelFactoryRunCommandV1(
            workspace="/workspace",
            run_id="run-123",
            reason="User cancelled",
        )
        assert cmd.workspace == "/workspace"
        assert cmd.run_id == "run-123"
        assert cmd.reason == "User cancelled"

    def test_command_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            CancelFactoryRunCommandV1(
                workspace="",
                run_id="run-123",
                reason="test",
            )

    def test_command_empty_run_id_raises(self) -> None:
        """Test that empty run_id raises ValueError."""
        with pytest.raises(ValueError, match="run_id must be a non-empty string"):
            CancelFactoryRunCommandV1(
                workspace="/ws",
                run_id="",
                reason="test",
            )

    def test_command_empty_reason_raises(self) -> None:
        """Test that empty reason raises ValueError."""
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            CancelFactoryRunCommandV1(
                workspace="/ws",
                run_id="run-123",
                reason="",
            )


class TestGetFactoryRunStatusQueryV1:
    """Tests for GetFactoryRunStatusQueryV1 contract."""

    def test_query_construction(self) -> None:
        """Test basic query construction."""
        query = GetFactoryRunStatusQueryV1(
            workspace="/workspace",
            run_id="run-123",
        )
        assert query.workspace == "/workspace"
        assert query.run_id == "run-123"

    def test_query_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GetFactoryRunStatusQueryV1(workspace="", run_id="run-123")

    def test_query_empty_run_id_raises(self) -> None:
        """Test that empty run_id raises ValueError."""
        with pytest.raises(ValueError, match="run_id must be a non-empty string"):
            GetFactoryRunStatusQueryV1(workspace="/ws", run_id="")


class TestListFactoryRunsQueryV1:
    """Tests for ListFactoryRunsQueryV1 contract."""

    def test_query_defaults(self) -> None:
        """Test query default values."""
        query = ListFactoryRunsQueryV1(workspace="/workspace")
        assert query.limit == 50
        assert query.offset == 0

    def test_query_custom_pagination(self) -> None:
        """Test query with custom pagination."""
        query = ListFactoryRunsQueryV1(
            workspace="/workspace",
            limit=100,
            offset=200,
        )
        assert query.limit == 100
        assert query.offset == 200

    def test_query_limit_less_than_one_raises(self) -> None:
        """Test that limit < 1 raises ValueError."""
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ListFactoryRunsQueryV1(workspace="/ws", limit=0)

    def test_query_negative_offset_raises(self) -> None:
        """Test that negative offset raises ValueError."""
        with pytest.raises(ValueError, match="offset must be >= 0"):
            ListFactoryRunsQueryV1(workspace="/ws", offset=-1)


class TestRunProjectionExperimentCommandV1:
    """Tests for RunProjectionExperimentCommandV1 contract."""

    def test_command_defaults(self) -> None:
        """Test command default values."""
        cmd = RunProjectionExperimentCommandV1(
            workspace="/workspace",
            scenario_id="scenario-123",
            requirement="Design a new feature",
        )
        assert cmd.project_slug == "projection_lab"
        assert cmd.use_pm_llm is True
        assert cmd.run_verification is True
        assert cmd.overwrite is False

    def test_command_custom_options(self) -> None:
        """Test command with custom options."""
        cmd = RunProjectionExperimentCommandV1(
            workspace="/workspace",
            scenario_id="scenario-123",
            requirement="Design a new feature",
            project_slug="custom_project",
            use_pm_llm=False,
            run_verification=False,
            overwrite=True,
        )
        assert cmd.project_slug == "custom_project"
        assert cmd.use_pm_llm is False
        assert cmd.run_verification is False
        assert cmd.overwrite is True

    def test_command_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            RunProjectionExperimentCommandV1(
                workspace="",
                scenario_id="s-1",
                requirement="req",
            )

    def test_command_empty_scenario_id_raises(self) -> None:
        """Test that empty scenario_id raises ValueError."""
        with pytest.raises(ValueError, match="scenario_id must be a non-empty string"):
            RunProjectionExperimentCommandV1(
                workspace="/ws",
                scenario_id="",
                requirement="req",
            )

    def test_command_empty_requirement_raises(self) -> None:
        """Test that empty requirement raises ValueError."""
        with pytest.raises(ValueError, match="requirement must be a non-empty string"):
            RunProjectionExperimentCommandV1(
                workspace="/ws",
                scenario_id="s-1",
                requirement="",
            )

    def test_boolean_fields_normalized(self) -> None:
        """Test that boolean fields are normalized."""
        cmd = RunProjectionExperimentCommandV1(
            workspace="/ws",
            scenario_id="s-1",
            requirement="req",
            use_pm_llm="yes",  # Should be cast to bool
            run_verification=0,
            overwrite=1,
        )
        assert cmd.use_pm_llm is True
        assert cmd.run_verification is False
        assert cmd.overwrite is True


class TestRefreshProjectionBackMappingCommandV1:
    """Tests for RefreshProjectionBackMappingCommandV1 contract."""

    def test_command_construction(self) -> None:
        """Test basic command construction."""
        cmd = RefreshProjectionBackMappingCommandV1(
            workspace="/workspace",
            experiment_id="exp-123",
        )
        assert cmd.workspace == "/workspace"
        assert cmd.experiment_id == "exp-123"

    def test_command_empty_workspace_raises(self) -> None:
        """Test that empty workspace raises ValueError."""
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            RefreshProjectionBackMappingCommandV1(
                workspace="",
                experiment_id="exp-1",
            )


class TestReprojectProjectionExperimentCommandV1:
    """Tests for ReprojectProjectionExperimentCommandV1 contract."""

    def test_command_defaults(self) -> None:
        """Test command default values."""
        cmd = ReprojectProjectionExperimentCommandV1(
            workspace="/workspace",
            experiment_id="exp-123",
            requirement="Modified requirement",
        )
        assert cmd.use_pm_llm is True
        assert cmd.run_verification is True

    def test_command_custom_options(self) -> None:
        """Test command with custom options."""
        cmd = ReprojectProjectionExperimentCommandV1(
            workspace="/workspace",
            experiment_id="exp-123",
            requirement="Modified requirement",
            use_pm_llm=False,
            run_verification=False,
        )
        assert cmd.use_pm_llm is False
        assert cmd.run_verification is False


class TestFactoryRunStartedEventV1:
    """Tests for FactoryRunStartedEventV1 contract."""

    def test_event_construction(self) -> None:
        """Test basic event construction."""
        event = FactoryRunStartedEventV1(
            event_id="evt-001",
            workspace="/workspace",
            run_id="run-123",
            started_at="2024-01-01T00:00:00Z",
        )
        assert event.event_id == "evt-001"
        assert event.workspace == "/workspace"
        assert event.run_id == "run-123"
        assert event.started_at == "2024-01-01T00:00:00Z"


class TestFactoryRunCompletedEventV1:
    """Tests for FactoryRunCompletedEventV1 contract."""

    def test_event_success(self) -> None:
        """Test success event construction."""
        event = FactoryRunCompletedEventV1(
            event_id="evt-002",
            workspace="/workspace",
            run_id="run-123",
            status="completed",
            completed_at="2024-01-01T01:00:00Z",
        )
        assert event.status == "completed"
        assert event.error_message is None

    def test_event_with_error(self) -> None:
        """Test event with error message."""
        event = FactoryRunCompletedEventV1(
            event_id="evt-002",
            workspace="/workspace",
            run_id="run-123",
            status="failed",
            completed_at="2024-01-01T01:00:00Z",
            error_message="Build failed",
        )
        assert event.status == "failed"
        assert event.error_message == "Build failed"


class TestFactoryRunResultV1:
    """Tests for FactoryRunResultV1 contract."""

    def test_result_success(self) -> None:
        """Test success result construction."""
        result = FactoryRunResultV1(
            ok=True,
            workspace="/workspace",
            run_id="run-123",
            status="completed",
        )
        assert result.ok is True
        assert result.status == "completed"
        assert result.completed_stages == ()
        assert result.artifact_paths == ()

    def test_result_with_artifacts(self) -> None:
        """Test result with completed stages and artifacts."""
        result = FactoryRunResultV1(
            ok=True,
            workspace="/workspace",
            run_id="run-123",
            status="completed",
            completed_stages=("stage1", "stage2"),
            artifact_paths=("/artifact1", "/artifact2"),
        )
        assert result.completed_stages == ("stage1", "stage2")
        assert result.artifact_paths == ("/artifact1", "/artifact2")

    def test_result_failed(self) -> None:
        """Test failed result construction."""
        result = FactoryRunResultV1(
            ok=False,
            workspace="/workspace",
            run_id="run-123",
            status="failed",
        )
        assert result.ok is False

    def test_result_whitespace_filtered(self) -> None:
        """Test that whitespace artifacts are filtered."""
        result = FactoryRunResultV1(
            ok=True,
            workspace="/workspace",
            run_id="run-123",
            status="completed",
            artifact_paths=("/artifact1", "  ", "/artifact2"),
        )
        assert result.artifact_paths == ("/artifact1", "/artifact2")


class TestProjectionExperimentResultV1:
    """Tests for ProjectionExperimentResultV1 contract."""

    def test_result_construction(self) -> None:
        """Test basic result construction."""
        result = ProjectionExperimentResultV1(
            ok=True,
            workspace="/workspace",
            experiment_id="exp-123",
            scenario_id="scenario-1",
            project_root="/projects/test",
        )
        assert result.ok is True
        assert result.verification_ok is False
        assert result.generated_files == ()

    def test_result_with_files_and_cells(self) -> None:
        """Test result with generated files and impacted cells."""
        result = ProjectionExperimentResultV1(
            ok=True,
            workspace="/workspace",
            experiment_id="exp-123",
            scenario_id="scenario-1",
            project_root="/projects/test",
            generated_files=("/file1.py", "/file2.py"),
            cell_ids=("cell-1", "cell-2"),
            verification_ok=True,
            summary="Generated 2 files",
        )
        assert result.generated_files == ("/file1.py", "/file2.py")
        assert result.cell_ids == ("cell-1", "cell-2")
        assert result.verification_ok is True
        assert result.summary == "Generated 2 files"

    def test_result_to_dict(self) -> None:
        """Test result serialization to dict."""
        result = ProjectionExperimentResultV1(
            ok=True,
            workspace="/workspace",
            experiment_id="exp-123",
            scenario_id="scenario-1",
            project_root="/projects/test",
        )
        data = result.to_dict()
        assert isinstance(data, dict)
        assert data["ok"] is True
        assert data["experiment_id"] == "exp-123"
        assert data["generated_files"] == []


class TestProjectionBackMappingRefreshResultV1:
    """Tests for ProjectionBackMappingRefreshResultV1 contract."""

    def test_result_construction(self) -> None:
        """Test basic result construction."""
        result = ProjectionBackMappingRefreshResultV1(
            workspace="/workspace",
            experiment_id="exp-123",
            project_root="/projects/test",
        )
        assert result.changed_files == ()
        assert result.added_symbols == ()
        assert result.removed_symbols == ()
        assert result.modified_symbols == ()
        assert result.impacted_cell_ids == ()

    def test_result_with_symbols(self) -> None:
        """Test result with symbol changes."""
        result = ProjectionBackMappingRefreshResultV1(
            workspace="/workspace",
            experiment_id="exp-123",
            project_root="/projects/test",
            changed_files=[
                {"path": "/file1.py", "change_type": "modified"},
            ],
            added_symbols=[
                {"qualified_name": "TestClass.new_method", "type": "method"},
            ],
            impacted_cell_ids=("cell-1",),
            mapping_strategy="tree_sitter",
        )
        assert len(result.changed_files) == 1
        assert len(result.added_symbols) == 1
        assert result.added_symbols[0]["qualified_name"] == "TestClass.new_method"
        assert result.impacted_cell_ids == ("cell-1",)
        assert result.mapping_strategy == "tree_sitter"

    def test_result_to_dict(self) -> None:
        """Test result serialization to dict."""
        result = ProjectionBackMappingRefreshResultV1(
            workspace="/workspace",
            experiment_id="exp-123",
            project_root="/projects/test",
            added_symbols=[
                {"qualified_name": "Test.func", "type": "function"},
            ],
        )
        data = result.to_dict()
        assert isinstance(data, dict)
        assert len(data["added_symbols"]) == 1


class TestProjectionReprojectionResultV1:
    """Tests for ProjectionReprojectionResultV1 contract."""

    def test_result_construction(self) -> None:
        """Test basic result construction."""
        result = ProjectionReprojectionResultV1(
            ok=True,
            workspace="/workspace",
            experiment_id="exp-123",
            scenario_id="scenario-1",
            project_root="/projects/test",
        )
        assert result.ok is True
        assert result.verification_ok is False
        assert result.rewritten_files == ()

    def test_result_with_rewrites(self) -> None:
        """Test result with rewritten files."""
        result = ProjectionReprojectionResultV1(
            ok=True,
            workspace="/workspace",
            experiment_id="exp-123",
            scenario_id="scenario-1",
            project_root="/projects/test",
            rewritten_files=("/file1.py",),
            artifact_paths=("/artifact1",),
            impacted_cell_ids=("cell-1",),
            verification_ok=True,
        )
        assert result.rewritten_files == ("/file1.py",)
        assert result.artifact_paths == ("/artifact1",)
        assert result.verification_ok is True

    def test_result_to_dict(self) -> None:
        """Test result serialization to dict."""
        result = ProjectionReprojectionResultV1(
            ok=True,
            workspace="/workspace",
            experiment_id="exp-123",
            scenario_id="scenario-1",
            project_root="/projects/test",
        )
        data = result.to_dict()
        assert isinstance(data, dict)
        assert data["ok"] is True


class TestFactoryPipelineError:
    """Tests for FactoryPipelineError exception."""

    def test_error_default_code(self) -> None:
        """Test error with default code."""
        err = FactoryPipelineError("Pipeline failed")
        assert str(err) == "Pipeline failed"
        assert err.code == "factory_pipeline_error"
        assert err.details == {}

    def test_error_custom_code(self) -> None:
        """Test error with custom code."""
        err = FactoryPipelineError(
            "Pipeline failed",
            code="PIPELINE_TIMEOUT",
        )
        assert err.code == "PIPELINE_TIMEOUT"

    def test_error_with_details(self) -> None:
        """Test error with details."""
        err = FactoryPipelineError(
            "Pipeline failed",
            details={"stage": "build", "error": "syntax"},
        )
        assert err.details == {"stage": "build", "error": "syntax"}

    def test_error_empty_message_raises(self) -> None:
        """Test that empty message raises ValueError."""
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            FactoryPipelineError("")

    def test_error_empty_code_raises(self) -> None:
        """Test that empty code raises ValueError."""
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            FactoryPipelineError("msg", code="")


class TestIFactoryPipelineProtocol:
    """Tests for IFactoryPipeline protocol."""

    def test_protocol_exists(self) -> None:
        """Test protocol can be imported."""
        assert hasattr(IFactoryPipeline, "run_pipeline")


class TestIFactoryProjectionLabProtocol:
    """Tests for IFactoryProjectionLab protocol."""

    def test_protocol_exists(self) -> None:
        """Test protocol can be imported."""
        assert hasattr(IFactoryProjectionLab, "run_projection_experiment")


class TestFactoryPipelineLifecycle:
    """Tests for complete factory pipeline lifecycle scenarios."""

    def test_pipeline_lifecycle(self) -> None:
        """Test complete pipeline run lifecycle."""
        # Start pipeline
        start_cmd = StartFactoryRunCommandV1(
            workspace="/workspace",
            run_name="build-run",
            stages=("prepare", "build", "test"),
            options={"parallel": False},
        )

        # Query status
        status_query = GetFactoryRunStatusQueryV1(
            workspace=start_cmd.workspace,
            run_id="run-001",
        )

        # Started event
        started_event = FactoryRunStartedEventV1(
            event_id="evt-001",
            workspace=start_cmd.workspace,
            run_id="run-001",
            started_at="2024-01-01T00:00:00Z",
        )

        # Completed event
        completed_event = FactoryRunCompletedEventV1(
            event_id="evt-002",
            workspace=start_cmd.workspace,
            run_id="run-001",
            status="completed",
            completed_at="2024-01-01T00:05:00Z",
        )

        # Final result
        result = FactoryRunResultV1(
            ok=True,
            workspace=start_cmd.workspace,
            run_id="run-001",
            status="completed",
            completed_stages=("prepare", "build", "test"),
            artifact_paths=("/artifact/output.zip",),
        )

        # Verify consistency
        assert start_cmd.workspace == status_query.workspace == started_event.workspace
        assert result.run_id == started_event.run_id
        assert result.ok is True
        assert len(result.completed_stages) == 3

    def test_projection_experiment_lifecycle(self) -> None:
        """Test complete projection experiment lifecycle."""
        # Run experiment
        exp_cmd = RunProjectionExperimentCommandV1(
            workspace="/workspace",
            scenario_id="scenario-auth",
            requirement="Implement authentication module",
            project_slug="auth_lab",
            use_pm_llm=True,
            run_verification=True,
        )

        # Refresh back-mapping
        refresh_cmd = RefreshProjectionBackMappingCommandV1(
            workspace=exp_cmd.workspace,
            experiment_id="exp-001",
        )

        # Reproject
        reproject_cmd = ReprojectProjectionExperimentCommandV1(
            workspace=exp_cmd.workspace,
            experiment_id="exp-001",
            requirement="Updated: Add OAuth support",
        )

        # Verify consistency
        assert exp_cmd.workspace == refresh_cmd.workspace
        assert refresh_cmd.experiment_id == reproject_cmd.experiment_id
