"""Tests for Director workflow evidence event recording.

Verifies that evidence events are properly recorded during workflow execution,
including resident decisions and workflow state events.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDirectorWorkflowEvidenceEvents:
    """Tests for evidence event recording in DirectorWorkflow."""

    @pytest.mark.asyncio
    async def test_workflow_records_started_event(self) -> None:
        """Workflow should record director_started event."""
        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        mock_workflow_api = MagicMock()
        mock_workflow_api.execute_activity = AsyncMock(return_value={"payload": {"tasks": []}})

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

        # Verify director_started event was recorded
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
    async def test_workflow_records_completed_event(self) -> None:
        """Workflow should record director_completed event."""
        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        mock_workflow_api = MagicMock()
        mock_workflow_api.execute_activity = AsyncMock(return_value={"payload": {"tasks": []}})

        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-2",
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

        # Verify director_completed event was recorded
        mock_record.assert_any_call(
            stage="director_completed",
            message="Director workflow completed",
            details={
                "run_id": "test-run-2",
                "completed_tasks": 0,
                "failed_tasks": 0,
            },
        )

    @pytest.mark.asyncio
    async def test_workflow_records_resident_decision_on_start(self) -> None:
        """Workflow should record resident decision on start."""
        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        mock_workflow_api = MagicMock()
        mock_workflow_api.execute_activity = AsyncMock(return_value={"payload": {"tasks": []}})

        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-3",
            tasks=[],
            execution_mode="parallel",
            max_parallel_tasks=3,
        )

        with (
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow.workflow",
                mock_workflow_api,
            ),
            patch.object(workflow, "_record_event"),
            patch.object(workflow, "_set_task_status"),
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow._record_resident_decision_safe"
            ) as mock_resident,
        ):
            await workflow.run(workflow_input)

        # Verify resident decision was recorded for workflow start
        mock_resident.assert_any_call(
            "/tmp/test",
            {
                "run_id": "test-run-3",
                "actor": "director",
                "stage": "workflow_start",
                "summary": "Director workflow started in parallel mode",
                "strategy_tags": ["parallel_dispatch", "workflow_fanout"],
                "expected_outcome": {"status": "tasks_queued", "success": True},
                "actual_outcome": {
                    "status": "tasks_queued",
                    "success": True,
                    "task_count": 0,
                    "max_parallel_tasks": 3,
                },
                "verdict": "success",
                "evidence_refs": ["runtime/contracts/plan.md"],
                "context_refs": [],
                "confidence": 0.75,
            },
        )

    @pytest.mark.asyncio
    async def test_workflow_records_resident_decision_on_completion(self) -> None:
        """Workflow should record resident decision on completion."""
        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        mock_workflow_api = MagicMock()
        mock_workflow_api.execute_activity = AsyncMock(return_value={"payload": {"tasks": []}})

        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-4",
            tasks=[],
            execution_mode="serial",
            max_parallel_tasks=1,
        )

        with (
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow.workflow",
                mock_workflow_api,
            ),
            patch.object(workflow, "_record_event"),
            patch.object(workflow, "_set_task_status"),
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow._record_resident_decision_safe"
            ) as mock_resident,
        ):
            await workflow.run(workflow_input)

        # Verify resident decision was recorded for workflow completion
        mock_resident.assert_any_call(
            "/tmp/test",
            {
                "run_id": "test-run-4",
                "actor": "director",
                "stage": "workflow_completion",
                "summary": "Director workflow completed",
                "strategy_tags": ["serial_dispatch", "workflow_completion"],
                "expected_outcome": {"status": "completed", "success": True},
                "actual_outcome": {
                    "status": "completed",
                    "success": True,
                    "completed_tasks": 0,
                    "failed_tasks": 0,
                },
                "verdict": "success",
                "evidence_refs": [
                    "runtime/results/director.result.json",
                    "runtime/status/engine.status.json",
                ],
                "context_refs": [],
                "confidence": 0.87,
            },
        )

    @pytest.mark.asyncio
    async def test_workflow_records_dispatch_cycle_events(self) -> None:
        """Workflow should record dispatch cycle events when tasks are present."""
        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
            TaskContract,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        mock_workflow_api = MagicMock()
        # Return a task in the ready tasks payload
        mock_workflow_api.execute_activity = AsyncMock(
            return_value={
                "payload": {
                    "tasks": [
                        {"id": "task-1", "title": "Task 1"},
                    ],
                },
            }
        )

        # Mock child workflow to return completed
        mock_workflow_api.execute_child_workflow = AsyncMock(
            return_value={
                "task_id": "task-1",
                "status": "completed",
                "completed_phases": ["prepare", "validate"],
                "errors": [],
                "metadata": {},
            }
        )

        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-5",
            tasks=[TaskContract(task_id="task-1", title="Task 1")],
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
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow._record_resident_decision_safe"
            ),
        ):
            await workflow.run(workflow_input)

        # Verify dispatch cycle events were recorded
        dispatch_events = [
            call for call in mock_record.call_args_list if call[1].get("stage") == "director_dispatch_cycle"
        ]
        assert len(dispatch_events) > 0

        # Verify batch selected event
        batch_events = [
            call for call in mock_record.call_args_list if call[1].get("stage") == "director_batch_selected"
        ]
        assert len(batch_events) > 0

        # Verify batch completed event
        batch_completed_events = [
            call for call in mock_record.call_args_list if call[1].get("stage") == "director_batch_completed"
        ]
        assert len(batch_completed_events) > 0

    @pytest.mark.asyncio
    async def test_workflow_records_resident_decision_on_task_failure(self) -> None:
        """Workflow should record resident decision when task fails."""
        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
            TaskContract,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        mock_workflow_api = MagicMock()
        # Return a task in the ready tasks payload
        mock_workflow_api.execute_activity = AsyncMock(
            return_value={
                "payload": {
                    "tasks": [
                        {"id": "task-1", "title": "Task 1"},
                    ],
                },
            }
        )

        # Mock child workflow to return failed
        mock_workflow_api.execute_child_workflow = AsyncMock(
            return_value={
                "task_id": "task-1",
                "status": "failed",
                "completed_phases": [],
                "errors": ["Task execution failed"],
                "metadata": {},
            }
        )

        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-6",
            tasks=[TaskContract(task_id="task-1", title="Task 1")],
            execution_mode="parallel",
            max_parallel_tasks=3,
        )

        with (
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow.workflow",
                mock_workflow_api,
            ),
            patch.object(workflow, "_record_event"),
            patch.object(workflow, "_set_task_status"),
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow._record_resident_decision_safe"
            ) as mock_resident,
        ):
            await workflow.run(workflow_input)

        # Verify resident decision was recorded for task failure
        failure_decisions = [
            call
            for call in mock_resident.call_args_list
            if call.args[1].get("stage") == "task_execution" and call.args[1].get("verdict") == "failure"
        ]
        assert len(failure_decisions) > 0

    @pytest.mark.asyncio
    async def test_workflow_records_resident_decision_on_task_success(self) -> None:
        """Workflow should record resident decision when task succeeds."""
        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
            TaskContract,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        mock_workflow_api = MagicMock()
        # Return a task in the ready tasks payload
        mock_workflow_api.execute_activity = AsyncMock(
            return_value={
                "payload": {
                    "tasks": [
                        {"id": "task-1", "title": "Task 1"},
                    ],
                },
            }
        )

        # Mock child workflow to return completed
        mock_workflow_api.execute_child_workflow = AsyncMock(
            return_value={
                "task_id": "task-1",
                "status": "completed",
                "completed_phases": ["prepare", "validate", "implement"],
                "errors": [],
                "metadata": {},
            }
        )

        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-7",
            tasks=[TaskContract(task_id="task-1", title="Task 1")],
            execution_mode="parallel",
            max_parallel_tasks=3,
        )

        with (
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow.workflow",
                mock_workflow_api,
            ),
            patch.object(workflow, "_record_event"),
            patch.object(workflow, "_set_task_status"),
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow._record_resident_decision_safe"
            ) as mock_resident,
        ):
            await workflow.run(workflow_input)

        # Verify resident decision was recorded for task success
        success_decisions = [
            call
            for call in mock_resident.call_args_list
            if call.args[1].get("stage") == "task_execution" and call.args[1].get("verdict") == "success"
        ]
        assert len(success_decisions) > 0

    @pytest.mark.asyncio
    async def test_workflow_records_resident_decision_on_dependency_block(self) -> None:
        """Workflow should record resident decision when task is blocked by dependency."""
        from polaris.cells.orchestration.workflow_activity.internal.models import (
            DirectorWorkflowInput,
            TaskContract,
        )
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            DirectorWorkflow,
        )

        workflow = DirectorWorkflow()

        mock_workflow_api = MagicMock()
        # Return two tasks, one dependent on the other
        mock_workflow_api.execute_activity = AsyncMock(
            return_value={
                "payload": {
                    "tasks": [
                        {"id": "task-1", "title": "Task 1"},
                        {"id": "task-2", "title": "Task 2", "dependencies": ["task-1"]},
                    ],
                },
            }
        )

        # Mock child workflow to fail for task-1
        mock_workflow_api.execute_child_workflow = AsyncMock(
            return_value={
                "task_id": "task-1",
                "status": "failed",
                "completed_phases": [],
                "errors": ["Task 1 failed"],
                "metadata": {},
            }
        )

        workflow_input = DirectorWorkflowInput(
            workspace="/tmp/test",
            run_id="test-run-8",
            tasks=[
                TaskContract(task_id="task-1", title="Task 1"),
                TaskContract(task_id="task-2", title="Task 2", payload={"dependencies": ["task-1"]}),
            ],
            execution_mode="parallel",
            max_parallel_tasks=3,
        )

        with (
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow.workflow",
                mock_workflow_api,
            ),
            patch.object(workflow, "_record_event"),
            patch.object(workflow, "_set_task_status"),
            patch(
                "polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow._record_resident_decision_safe"
            ) as mock_resident,
        ):
            await workflow.run(workflow_input)

        # Verify resident decision was recorded for dependency block
        block_decisions = [
            call for call in mock_resident.call_args_list if call.args[1].get("stage") == "dependency_block"
        ]
        assert len(block_decisions) > 0


class TestRecordResidentDecisionSafe:
    """Tests for _record_resident_decision_safe function."""

    def test_records_decision_successfully(self) -> None:
        """Should record decision when resident module is available."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _record_resident_decision_safe,
        )

        with patch("polaris.cells.resident.autonomy.public.service.record_resident_decision") as mock_record:
            _record_resident_decision_safe(
                "/tmp/test",
                {"run_id": "test-run", "stage": "test_stage"},
            )

        mock_record.assert_called_once_with(
            "/tmp/test",
            {"run_id": "test-run", "stage": "test_stage"},
        )

    def test_handles_runtime_error_gracefully(self) -> None:
        """Should handle runtime error gracefully."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _record_resident_decision_safe,
        )

        with patch(
            "polaris.cells.resident.autonomy.public.service.record_resident_decision",
            side_effect=RuntimeError("Runtime error"),
        ):
            # Should not raise
            _record_resident_decision_safe(
                "/tmp/test",
                {"run_id": "test-run", "stage": "test_stage"},
            )

    def test_handles_value_error_gracefully(self) -> None:
        """Should handle value error gracefully."""
        from polaris.cells.orchestration.workflow_activity.internal.workflows.director_workflow import (
            _record_resident_decision_safe,
        )

        with patch(
            "polaris.cells.resident.autonomy.public.service.record_resident_decision",
            side_effect=ValueError("Value error"),
        ):
            # Should not raise
            _record_resident_decision_safe(
                "/tmp/test",
                {"run_id": "test-run", "stage": "test_stage"},
            )
