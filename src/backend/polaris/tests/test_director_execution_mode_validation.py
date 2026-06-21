"""Tests for Director workflow execution mode validation.

Verifies that execution mode is properly validated and does not silently
fallback between parallel and serial modes.
"""

from __future__ import annotations

import pytest


class TestExecutionModeValidation:
    """Tests for _execution_mode function."""

    def test_parallel_mode_returns_parallel(self) -> None:
        """Explicit 'parallel' should return 'parallel'."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode("parallel") == "parallel"

    def test_serial_mode_returns_serial(self) -> None:
        """Explicit 'serial' should return 'serial'."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode("serial") == "serial"

    def test_sequential_mode_returns_serial(self) -> None:
        """'sequential' should return 'serial'."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode("sequential") == "serial"

    def test_empty_string_defaults_to_parallel(self) -> None:
        """Empty string should default to 'parallel'."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode("") == "parallel"

    def test_none_defaults_to_parallel(self) -> None:
        """None should default to 'parallel'."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode(None) == "parallel"

    def test_invalid_mode_defaults_to_parallel(self) -> None:
        """Invalid mode should default to 'parallel'."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode("invalid") == "parallel"
        assert _execution_mode("async") == "parallel"
        assert _execution_mode("concurrent") == "parallel"

    def test_case_insensitive_parallel(self) -> None:
        """Parallel should be case-insensitive."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode("PARALLEL") == "parallel"
        assert _execution_mode("Parallel") == "parallel"
        assert _execution_mode("pArAlLeL") == "parallel"

    def test_case_insensitive_serial(self) -> None:
        """Serial should be case-insensitive."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode("SERIAL") == "serial"
        assert _execution_mode("Serial") == "serial"
        assert _execution_mode("sErIaL") == "serial"

    def test_case_insensitive_sequential(self) -> None:
        """Sequential should be case-insensitive."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _execution_mode,
        )

        assert _execution_mode("SEQUENTIAL") == "serial"
        assert _execution_mode("Sequential") == "serial"


class TestDirectorWorkflowExecutionMode:
    """Tests for DirectorWorkflow execution mode handling."""

    @pytest.mark.asyncio
    async def test_parallel_mode_does_not_fallback_to_serial(self) -> None:
        """Parallel mode should not silently fallback to serial."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        # Mock the workflow API
        mock_workflow_api = MagicMock()
        mock_workflow_api.execute_activity = AsyncMock(return_value={"payload": {"tasks": []}})
        mock_workflow_api.execute_child_workflow = AsyncMock()

        # Create input with parallel mode
        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-1",
            tasks=[],
            execution_mode="parallel",
            max_parallel_tasks=3,
        )

        with (
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow.workflow",
                mock_workflow_api,
            ),
            patch.object(workflow, "_record_event") as mock_record,
            patch.object(workflow, "_set_task_status"),
        ):
            await workflow.run(workflow_input)

        # Verify that the workflow started with parallel mode
        mock_record.assert_any_call(
            stage="director_started",
            message="Director workflow started",
            details={
                "run_id": "test-run-1",
                "task_count": 0,
                "execution_mode": "parallel",
                "max_parallel_tasks": 3,
            },
        )

    @pytest.mark.asyncio
    async def test_serial_mode_does_not_fallback_to_parallel(self) -> None:
        """Serial mode should not silently fallback to parallel."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        # Mock the workflow API
        mock_workflow_api = MagicMock()
        mock_workflow_api.execute_activity = AsyncMock(return_value={"payload": {"tasks": []}})
        mock_workflow_api.execute_child_workflow = AsyncMock()

        # Create input with serial mode
        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-2",
            tasks=[],
            execution_mode="serial",
            max_parallel_tasks=3,
        )

        with (
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow.workflow",
                mock_workflow_api,
            ),
            patch.object(workflow, "_record_event") as mock_record,
            patch.object(workflow, "_set_task_status"),
        ):
            await workflow.run(workflow_input)

        # Verify that the workflow started with serial mode
        mock_record.assert_any_call(
            stage="director_started",
            message="Director workflow started",
            details={
                "run_id": "test-run-2",
                "task_count": 0,
                "execution_mode": "serial",
                "max_parallel_tasks": 1,  # serial mode forces 1
            },
        )

    @pytest.mark.asyncio
    async def test_invalid_execution_mode_defaults_to_parallel(self) -> None:
        """Invalid execution mode should default to parallel."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        # Mock the workflow API
        mock_workflow_api = MagicMock()
        mock_workflow_api.execute_activity = AsyncMock(return_value={"payload": {"tasks": []}})
        mock_workflow_api.execute_child_workflow = AsyncMock()

        # Create input with invalid mode
        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-3",
            tasks=[],
            execution_mode="invalid_mode",
            max_parallel_tasks=3,
        )

        with (
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow.workflow",
                mock_workflow_api,
            ),
            patch.object(workflow, "_record_event") as mock_record,
            patch.object(workflow, "_set_task_status"),
        ):
            await workflow.run(workflow_input)

        # Verify that the workflow defaulted to parallel mode
        mock_record.assert_any_call(
            stage="director_started",
            message="Director workflow started",
            details={
                "run_id": "test-run-3",
                "task_count": 0,
                "execution_mode": "parallel",  # defaulted
                "max_parallel_tasks": 3,
            },
        )


class TestDirectorOrchestratorExecutionMode:
    """Tests for DirectorOrchestrator execution mode handling."""

    def test_default_execution_mode_is_parallel(self) -> None:
        """Default execution mode should be parallel."""
        from polaris.application.orchestration.director_orchestrator import DirectorExecutionConfig

        config = DirectorExecutionConfig(workspace="/tmp/test")
        assert config.execution_mode == "parallel"

    def test_invalid_execution_mode_defaults_to_parallel(self) -> None:
        """Invalid execution mode should default to parallel."""
        from polaris.application.orchestration.director_orchestrator import DirectorExecutionConfig

        config = DirectorExecutionConfig(workspace="/tmp/test", execution_mode="invalid")
        assert config.execution_mode == "parallel"

    def test_parallel_execution_mode_preserved(self) -> None:
        """Parallel execution mode should be preserved."""
        from polaris.application.orchestration.director_orchestrator import DirectorExecutionConfig

        config = DirectorExecutionConfig(workspace="/tmp/test", execution_mode="parallel")
        assert config.execution_mode == "parallel"

    def test_serial_execution_mode_preserved(self) -> None:
        """Serial execution mode should be preserved."""
        from polaris.application.orchestration.director_orchestrator import DirectorExecutionConfig

        config = DirectorExecutionConfig(workspace="/tmp/test", execution_mode="serial")
        assert config.execution_mode == "serial"

    def test_max_workers_validation(self) -> None:
        """max_workers should be validated to be at least 1."""
        from polaris.application.orchestration.director_orchestrator import DirectorExecutionConfig

        config = DirectorExecutionConfig(workspace="/tmp/test", max_workers=0)
        assert config.max_workers == 1

        config = DirectorExecutionConfig(workspace="/tmp/test", max_workers=-1)
        assert config.max_workers == 1

        config = DirectorExecutionConfig(workspace="/tmp/test", max_workers=5)
        assert config.max_workers == 5
