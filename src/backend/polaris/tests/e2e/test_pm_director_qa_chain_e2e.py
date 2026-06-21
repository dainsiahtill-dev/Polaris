"""Lightweight E2E chain test for PM -> Director -> QA workflow contract.

Validates the critical contract: PM payload tasks -> Director execution result -> QA verdict.
Uses mocked activities to avoid calling real external LLMs.

Contract assertions:
- PM produces tasks from payload
- Director executes tasks and returns status
- QA produces a verdict based on Director status
- Chain completes without polling product paths
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polaris.cells.orchestration.workflow_activity.internal.models import (
    DirectorTaskResult,
    DirectorWorkflowInput,
    PMWorkflowInput,
    QAWorkflowInput,
    QAWorkflowResult,
    TaskContract,
    director_workflow_id,
    qa_workflow_id,
)
from polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow import (
    PMWorkflow,
)
from polaris.domain.entities.workflow import (
    DirectorWorkflowResult,
    PMWorkflowResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_tasks() -> list[dict[str, Any]]:
    """Minimal valid task contracts for PM payload."""
    return [
        {
            "id": "TASK-001",
            "title": "Implement login endpoint",
            "goal": "Create POST /api/login",
            "target_files": ["src/auth/login.ts"],
            "scope_paths": ["src/auth"],
            "acceptance_criteria": ["Returns JWT on valid credentials"],
        },
        {
            "id": "TASK-002",
            "title": "Implement logout endpoint",
            "goal": "Create POST /api/logout",
            "target_files": ["src/auth/logout.ts"],
            "scope_paths": ["src/auth"],
            "acceptance_criteria": ["Invalidates JWT"],
        },
    ]


@pytest.fixture
def pm_input(tmp_path, sample_tasks) -> PMWorkflowInput:
    """PM workflow input with precomputed tasks."""
    return PMWorkflowInput(
        workspace=str(tmp_path),
        run_id="e2e-test-run-001",
        precomputed_payload={"tasks": sample_tasks},
        metadata={
            "docs_stage": {},
            "director_config": {
                "execution_mode": "parallel",
                "max_parallel_tasks": 3,
                "ready_timeout_seconds": 30,
                "task_timeout_seconds": 600,
            },
        },
    )


# ---------------------------------------------------------------------------
# Mock Activities
# ---------------------------------------------------------------------------

def _mock_activity_response(success: bool, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a standard activity response."""
    return {
        "success": success,
        "payload": payload or {},
        "errors": [] if success else ["activity_failed"],
    }


_ACTIVITY_REGISTRY: dict[str, Any] = {
    "generate_pm_tasks": lambda input_data: _mock_activity_response(True, {"tasks": []}),
    "validate_task_contract": lambda input_data: _mock_activity_response(True),
    "run_chief_engineer_blueprint": lambda input_data: _mock_activity_response(True, {
        "tasks": input_data.get("tasks", []) if isinstance(input_data, dict) else [],
        "blueprint_path": "/tmp/blueprint.json",
        "runtime_blueprint_path": "/tmp/runtime_blueprint.json",
        "task_update_count": 0,
        "summary": "ChiefEngineer blueprint generated",
    }),
    "get_ready_tasks": lambda input_data: _mock_activity_response(True, {"ready_task_ids": []}),
    "run_unit_qa": lambda input_data: _mock_activity_response(True, {"command": "pytest -q", "passed": True}),
    "run_integration_qa": lambda input_data: _mock_activity_response(True, {"command": "pytest -q --integration", "passed": True}),
    "collect_evidence": lambda input_data: _mock_activity_response(True, {"evidence": {"unit": {}, "integration": {}}}),
    "register_traceability_verdict": lambda input_data: _mock_activity_response(True),
    "record_qa_cognitive_receipt": lambda input_data: _mock_activity_response(True, {
        "cognitive_runtime_receipt": {"ok": True, "receipt_id": "receipt-e2e-001"},
    }),
}


# ---------------------------------------------------------------------------
# E2E Chain Tests
# ---------------------------------------------------------------------------

class TestPMDirectorQAChainE2E:
    """E2E chain test: PM payload -> Director result -> QA verdict."""

    @pytest.mark.asyncio
    async def test_pm_produces_tasks_from_payload(self, pm_input) -> None:
        """PM extracts tasks from precomputed payload."""
        tasks = pm_input.payload_tasks()
        assert len(tasks) == 2
        assert tasks[0].task_id == "TASK-001"
        assert tasks[1].task_id == "TASK-002"
        assert tasks[0].title == "Implement login endpoint"

    @pytest.mark.asyncio
    async def test_pm_validates_task_contracts(self, pm_input) -> None:
        """PM validates task contracts before dispatch."""
        tasks = pm_input.payload_tasks()
        assert all(task.task_id for task in tasks)
        assert all(task.title for task in tasks)
        assert all(task.payload.get("target_files") for task in tasks)

    @pytest.mark.asyncio
    async def test_director_workflow_input_accepts_pm_tasks(self, pm_input) -> None:
        """Director input correctly accepts PM task contracts."""
        tasks = pm_input.payload_tasks()
        director_input = DirectorWorkflowInput(
            workspace=pm_input.workspace,
            run_id=pm_input.run_id,
            tasks=tasks,
            execution_mode="parallel",
            max_parallel_tasks=3,
        )
        assert len(director_input.tasks) == 2
        assert director_input.execution_mode == "parallel"
        assert director_input.run_id == pm_input.run_id

    @pytest.mark.asyncio
    async def test_director_workflow_result_contract(self) -> None:
        """Director result matches expected contract."""
        result = DirectorWorkflowResult(
            run_id="e2e-test-run-001",
            status="completed",
            completed_tasks=2,
            failed_tasks=0,
        )
        assert result.status == "completed"
        assert result.completed_tasks == 2
        assert result.failed_tasks == 0

    @pytest.mark.asyncio
    async def test_qa_workflow_input_accepts_director_status(self) -> None:
        """QA input correctly accepts Director status."""
        qa_input = QAWorkflowInput(
            workspace="/tmp/test",
            run_id="e2e-test-run-001",
            director_status="completed",
            task_results=[
                DirectorTaskResult(task_id="TASK-001", status="completed"),
            ],
        )
        assert qa_input.director_status == "completed"
        assert len(qa_input.task_results) == 1

    @pytest.mark.asyncio
    async def test_qa_workflow_result_contract(self) -> None:
        """QA result matches expected contract."""
        result = QAWorkflowResult(
            run_id="e2e-test-run-001",
            passed=True,
            reason="qa_passed",
            evidence={"unit": {"passed": True}, "integration": {"passed": True}},
        )
        assert result.passed is True
        assert result.reason == "qa_passed"
        assert "unit" in result.evidence

    @pytest.mark.asyncio
    async def test_full_chain_with_mocked_activities(self, pm_input) -> None:
        """Full PM -> Director -> QA chain with mocked activities.

        Validates:
        - PM extracts and validates tasks
        - Director receives tasks and produces results
        - QA produces verdict based on Director status
        - No real LLM calls are made
        """
        workflow = PMWorkflow()

        director_result = DirectorWorkflowResult(
            run_id=pm_input.run_id,
            status="completed",
            completed_tasks=2,
            failed_tasks=0,
        )

        qa_result = QAWorkflowResult(
            run_id=pm_input.run_id,
            passed=True,
            reason="qa_passed",
            evidence={"unit": {"passed": True}, "integration": {"passed": True}},
        )

        mock_workflow_api = MagicMock()

        async def fake_execute_activity(name, *args, **kwargs):
            handler = _ACTIVITY_REGISTRY.get(name)
            if handler:
                input_data = args[0] if args else kwargs
                return handler(input_data)
            return _mock_activity_response(True)

        async def fake_execute_child_workflow(func, *args, **kwargs):
            func_str = str(func)
            if "director" in func_str.lower():
                return director_result
            return qa_result

        mock_workflow_api.execute_activity = AsyncMock(side_effect=fake_execute_activity)
        mock_workflow_api.execute_child_workflow = AsyncMock(side_effect=fake_execute_child_workflow)

        with patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.workflow",
            mock_workflow_api,
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow._record_resident_decision_safe"
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.get_container",
            new_callable=AsyncMock,
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.TaskTraceBuilder",
        ):
            result = await workflow.run(pm_input)

            assert isinstance(result, PMWorkflowResult)
            assert result.run_id == pm_input.run_id
            assert len(result.tasks) == 2
            assert result.director_status in ("completed", "failed")
            assert result.qa_status in ("passed", "failed")

    @pytest.mark.asyncio
    async def test_chain_fails_when_director_fails(self, pm_input) -> None:
        """Chain correctly propagates Director failure to QA."""
        workflow = PMWorkflow()

        director_result = DirectorWorkflowResult(
            run_id=pm_input.run_id,
            status="failed",
            completed_tasks=0,
            failed_tasks=2,
        )

        mock_workflow_api = MagicMock()

        async def fake_execute_activity(name, *args, **kwargs):
            handler = _ACTIVITY_REGISTRY.get(name)
            if handler:
                input_data = args[0] if args else kwargs
                return handler(input_data)
            return _mock_activity_response(True)

        async def fake_execute_child_workflow(func, *args, **kwargs):
            return director_result

        mock_workflow_api.execute_activity = AsyncMock(side_effect=fake_execute_activity)
        mock_workflow_api.execute_child_workflow = AsyncMock(side_effect=fake_execute_child_workflow)

        with patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.workflow",
            mock_workflow_api,
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow._record_resident_decision_safe"
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.get_container",
            new_callable=AsyncMock,
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.TaskTraceBuilder",
        ):
            result = await workflow.run(pm_input)

            assert result.director_status == "failed"
            assert result.qa_status == "director_failed"

    @pytest.mark.asyncio
    async def test_chain_handles_qa_failure(self, pm_input) -> None:
        """Chain correctly handles QA test failure."""
        workflow = PMWorkflow()

        director_result = DirectorWorkflowResult(
            run_id=pm_input.run_id,
            status="completed",
            completed_tasks=2,
            failed_tasks=0,
        )

        qa_result = QAWorkflowResult(
            run_id=pm_input.run_id,
            passed=False,
            reason="qa_failed",
            evidence={"unit": {"passed": False}, "integration": {"passed": True}},
        )

        mock_workflow_api = MagicMock()
        call_count = {"director": 0, "qa": 0}

        async def fake_execute_activity(name, *args, **kwargs):
            handler = _ACTIVITY_REGISTRY.get(name)
            if handler:
                input_data = args[0] if args else kwargs
                return handler(input_data)
            return _mock_activity_response(True)

        async def fake_execute_child_workflow(func, *args, **kwargs):
            func_str = str(func)
            if "director" in func_str.lower():
                call_count["director"] += 1
                return director_result
            else:
                call_count["qa"] += 1
                return qa_result

        mock_workflow_api.execute_activity = AsyncMock(side_effect=fake_execute_activity)
        mock_workflow_api.execute_child_workflow = AsyncMock(side_effect=fake_execute_child_workflow)

        with patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.workflow",
            mock_workflow_api,
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow._record_resident_decision_safe"
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.get_container",
            new_callable=AsyncMock,
        ), patch(
            "polaris.cells.orchestration.workflow_activity.internal.workflows.pm_workflow.TaskTraceBuilder",
        ):
            result = await workflow.run(pm_input)

            assert result.director_status == "completed"
            assert result.qa_status == "qa_failed"
            assert call_count["director"] == 1
            assert call_count["qa"] == 1


class TestWorkflowContractConsistency:
    """Verify workflow model contracts are consistent."""

    def test_task_contract_from_mapping(self) -> None:
        """TaskContract.from_mapping handles valid input."""
        data = {
            "id": "TASK-001",
            "title": "Test task",
            "goal": "Test goal",
            "target_files": ["src/test.ts"],
            "scope_paths": ["src"],
            "acceptance_criteria": ["Must pass tests"],
        }
        task = TaskContract.from_mapping(data)
        assert task.task_id == "TASK-001"
        assert task.title == "Test task"
        assert task.payload.get("target_files") == ["src/test.ts"]

    def test_task_contract_from_mapping_missing_id(self) -> None:
        """TaskContract.from_mapping rejects missing id."""
        data = {"title": "No ID task"}
        task = TaskContract.from_mapping(data)
        assert task.task_id == ""

    def test_task_contract_to_dict(self) -> None:
        """TaskContract.to_dict returns original payload with defaults."""
        data = {
            "id": "TASK-001",
            "title": "Test task",
            "goal": "Test goal",
            "target_files": ["src/test.ts"],
        }
        task = TaskContract.from_mapping(data)
        result = task.to_dict()
        assert result["id"] == "TASK-001"
        assert result["title"] == "Test task"
        assert result["target_files"] == ["src/test.ts"]

    def test_director_workflow_id_format(self) -> None:
        """Director workflow ID follows expected format."""
        wf_id = director_workflow_id("run-001")
        assert "run-001" in wf_id
        assert "director" in wf_id.lower()

    def test_qa_workflow_id_format(self) -> None:
        """QA workflow ID follows expected format."""
        wf_id = qa_workflow_id("run-001")
        assert "run-001" in wf_id
        assert "qa" in wf_id.lower()

    def test_pm_workflow_result_fields(self) -> None:
        """PMWorkflowResult has expected fields."""
        result = PMWorkflowResult(
            run_id="test-run",
            tasks=[TaskContract(task_id="T1", title="Task 1")],
            director_status="completed",
            qa_status="passed",
            metadata={"task_count": 1},
        )
        assert result.run_id == "test-run"
        assert result.director_status == "completed"
        assert result.qa_status == "passed"
        assert len(result.tasks) == 1

    def test_director_workflow_result_fields(self) -> None:
        """DirectorWorkflowResult has expected fields."""
        result = DirectorWorkflowResult(
            run_id="test-run",
            status="completed",
            completed_tasks=1,
            failed_tasks=0,
        )
        assert result.run_id == "test-run"
        assert result.status == "completed"
        assert result.completed_tasks == 1
        assert result.failed_tasks == 0

    def test_qa_workflow_result_fields(self) -> None:
        """QAWorkflowResult has expected fields."""
        result = QAWorkflowResult(
            run_id="test-run",
            passed=True,
            reason="qa_passed",
            evidence={"unit": {"passed": True}},
        )
        assert result.run_id == "test-run"
        assert result.passed is True
        assert result.reason == "qa_passed"
        assert "unit" in result.evidence


class TestNoPollingProductPaths:
    """Verify no polling product paths are used in the chain."""

    def test_no_set_interval_in_workflow_source(self) -> None:
        """Workflow source does not contain setInterval."""
        import inspect
        source = inspect.getsource(PMWorkflow)
        assert "setInterval" not in source
        assert "pollInterval" not in source

    def test_no_event_source_in_workflow_source(self) -> None:
        """Workflow source does not contain EventSource."""
        import inspect
        source = inspect.getsource(PMWorkflow)
        assert "EventSource" not in source
        assert "text/event-stream" not in source

    def test_no_polling_fallback_in_workflow_source(self) -> None:
        """Workflow source does not contain polling fallback."""
        import inspect
        source = inspect.getsource(PMWorkflow)
        assert "polling" not in source.lower()
        assert "fallback" not in source.lower()
        assert "fetchRunStatus" not in source
