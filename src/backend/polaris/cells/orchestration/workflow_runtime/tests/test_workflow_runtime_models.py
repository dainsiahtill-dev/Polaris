"""Tests for polaris.cells.orchestration.workflow_runtime.internal.models module.

This module tests the shared data contracts for workflow orchestration.
"""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.models import (
    DirectorTaskInput,
    DirectorTaskResult,
    DirectorWorkflowInput,
    DirectorWorkflowResult,
    ExecutionEvent,
    PMWorkflowInput,
    PMWorkflowResult,
    QAWorkflowInput,
    QAWorkflowResult,
    TaskContract,
    TaskExecutionStatus,
    TaskFailureRecord,
    director_workflow_id,
    pm_workflow_id,
    qa_workflow_id,
    utc_now_iso,
)


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_utc_now_iso_returns_string(self) -> None:
        """utc_now_iso returns ISO format string."""
        result = utc_now_iso()
        assert isinstance(result, str)
        # Should be parseable as ISO format
        assert "T" in result
        assert "+" in result or "Z" in result or result.endswith("+00:00")

    def test_pm_workflow_id_with_run_id(self) -> None:
        """pm_workflow_id generates correct format with run_id."""
        result = pm_workflow_id("run-123")
        assert result == "polaris-pm-run-123"

    def test_pm_workflow_id_with_empty_string(self) -> None:
        """pm_workflow_id uses adhoc for empty string."""
        result = pm_workflow_id("")
        assert result == "polaris-pm-adhoc"

    def test_pm_workflow_id_with_whitespace(self) -> None:
        """pm_workflow_id normalizes whitespace."""
        result = pm_workflow_id("  run-456  ")
        assert result == "polaris-pm-run-456"

    def test_director_workflow_id_with_run_id(self) -> None:
        """director_workflow_id generates correct format with run_id."""
        result = director_workflow_id("run-123")
        assert result == "polaris-director-run-123"

    def test_director_workflow_id_with_empty_string(self) -> None:
        """director_workflow_id uses adhoc for empty string."""
        result = director_workflow_id("")
        assert result == "polaris-director-adhoc"

    def test_qa_workflow_id_with_run_id(self) -> None:
        """qa_workflow_id generates correct format with run_id."""
        result = qa_workflow_id("run-123")
        assert result == "polaris-qa-run-123"

    def test_qa_workflow_id_with_empty_string(self) -> None:
        """qa_workflow_id uses adhoc for empty string."""
        result = qa_workflow_id("")
        assert result == "polaris-qa-adhoc"

    def test_qa_workflow_id_with_none(self) -> None:
        """qa_workflow_id handles None value."""
        result = qa_workflow_id(None)  # type: ignore[arg-type]
        assert result == "polaris-qa-adhoc"


class TestExecutionEvent:
    """Tests for ExecutionEvent dataclass."""

    def test_create_with_required_fields(self) -> None:
        """ExecutionEvent.create works with required fields."""
        event = ExecutionEvent.create(stage="init", message="Started")
        assert event.stage == "init"
        assert event.message == "Started"
        assert event.timestamp is not None

    def test_create_with_details(self) -> None:
        """ExecutionEvent.create accepts details dict."""
        details = {"key": "value", "count": 42}
        event = ExecutionEvent.create(stage="test", message="Test", details=details)
        assert event.details == details

    def test_create_normalizes_whitespace(self) -> None:
        """ExecutionEvent.create normalizes stage and message whitespace."""
        event = ExecutionEvent.create(stage="  stage  ", message="  msg  ")
        assert event.stage == "stage"
        assert event.message == "msg"

    def test_to_dict(self) -> None:
        """ExecutionEvent.to_dict returns dict representation."""
        event = ExecutionEvent.create(stage="test", message="Test message")
        result = event.to_dict()
        assert isinstance(result, dict)
        assert result["stage"] == "test"
        assert result["message"] == "Test message"
        assert "timestamp" in result


class TestTaskExecutionStatus:
    """Tests for TaskExecutionStatus dataclass."""

    def test_construction(self) -> None:
        """TaskExecutionStatus can be constructed."""
        status = TaskExecutionStatus(task_id="task-1", state="running")
        assert status.task_id == "task-1"
        assert status.state == "running"

    def test_construction_with_all_fields(self) -> None:
        """TaskExecutionStatus accepts all optional fields."""
        status = TaskExecutionStatus(
            task_id="task-1",
            state="completed",
            summary="Done",
            updated_at="2024-01-01T00:00:00+00:00",
            metadata={"key": "value"},
        )
        assert status.summary == "Done"
        assert status.metadata == {"key": "value"}

    def test_to_dict(self) -> None:
        """TaskExecutionStatus.to_dict returns dict representation."""
        status = TaskExecutionStatus(task_id="task-1", state="pending")
        result = status.to_dict()
        assert result["task_id"] == "task-1"
        assert result["state"] == "pending"


class TestTaskContract:
    """Tests for TaskContract dataclass."""

    def test_construction(self) -> None:
        """TaskContract can be constructed."""
        contract = TaskContract(task_id="task-1", title="Test Task")
        assert contract.task_id == "task-1"
        assert contract.title == "Test Task"

    def test_from_mapping_basic(self) -> None:
        """TaskContract.from_mapping works with basic dict."""
        raw = {"id": "task-1", "title": "My Task", "description": "Desc"}
        contract = TaskContract.from_mapping(raw)
        assert contract.task_id == "task-1"
        assert contract.title == "My Task"
        assert contract.goal == "Desc"

    def test_from_mapping_with_goal_field(self) -> None:
        """TaskContract.from_mapping prefers goal over description."""
        raw = {"id": "task-1", "title": "T", "goal": "Goal", "description": "Desc"}
        contract = TaskContract.from_mapping(raw)
        assert contract.goal == "Goal"

    def test_from_mapping_non_dict(self) -> None:
        """TaskContract.from_mapping handles non-dict input."""
        contract = TaskContract.from_mapping("not a dict")
        assert contract.task_id == ""
        assert contract.payload == {}

    def test_to_dict(self) -> None:
        """TaskContract.to_dict returns dict with id and title."""
        contract = TaskContract(task_id="task-1", title="Test", goal="Goal")
        result = contract.to_dict()
        assert result["id"] == "task-1"
        assert result["title"] == "Test"
        assert result["goal"] == "Goal"

    def test_to_dict_without_goal(self) -> None:
        """TaskContract.to_dict omits goal if empty."""
        contract = TaskContract(task_id="task-1", title="Test")
        result = contract.to_dict()
        assert "goal" not in result


class TestPMWorkflowInput:
    """Tests for PMWorkflowInput dataclass."""

    def test_construction(self) -> None:
        """PMWorkflowInput can be constructed."""
        inp = PMWorkflowInput(workspace="/ws", run_id="run-1")
        assert inp.workspace == "/ws"
        assert inp.run_id == "run-1"

    def test_workflow_id_property(self) -> None:
        """PMWorkflowInput.workflow_id returns correct format."""
        inp = PMWorkflowInput(workspace="/ws", run_id="run-1")
        assert inp.workflow_id == "polaris-pm-run-1"

    def test_from_mapping_basic(self) -> None:
        """PMWorkflowInput.from_mapping works with basic dict."""
        raw = {"workspace": "/ws", "run_id": "run-1"}
        inp = PMWorkflowInput.from_mapping(raw)
        assert inp.workspace == "/ws"
        assert inp.run_id == "run-1"

    def test_from_mapping_with_tasks(self) -> None:
        """PMWorkflowInput.from_mapping extracts tasks."""
        raw = {
            "workspace": "/ws",
            "run_id": "run-1",
            "precomputed_payload": {"tasks": [{"id": "t1", "title": "Task 1"}]},
        }
        inp = PMWorkflowInput.from_mapping(raw)
        tasks = inp.payload_tasks()
        assert len(tasks) == 1
        assert tasks[0].task_id == "t1"

    def test_payload_tasks_empty_when_no_tasks(self) -> None:
        """payload_tasks returns empty list when no tasks."""
        inp = PMWorkflowInput(workspace="/ws", run_id="run-1")
        assert inp.payload_tasks() == []


class TestDirectorWorkflowInput:
    """Tests for DirectorWorkflowInput dataclass."""

    def test_construction(self) -> None:
        """DirectorWorkflowInput can be constructed."""
        task = TaskContract(task_id="t1", title="Task 1")
        inp = DirectorWorkflowInput(
            workspace="/ws",
            run_id="run-1",
            tasks=[task],
        )
        assert inp.workspace == "/ws"
        assert inp.run_id == "run-1"
        assert len(inp.tasks) == 1

    def test_from_mapping_with_task_contract(self) -> None:
        """DirectorWorkflowInput.from_mapping accepts TaskContract objects."""
        task = TaskContract(task_id="t1", title="Task 1")
        raw = {
            "workspace": "/ws",
            "run_id": "run-1",
            "tasks": [task],  # Already a TaskContract
        }
        inp = DirectorWorkflowInput.from_mapping(raw)
        assert len(inp.tasks) == 1
        assert inp.tasks[0].task_id == "t1"

    def test_from_mapping_captures_execution_mode(self) -> None:
        """DirectorWorkflowInput.from_mapping captures execution_mode."""
        raw = {
            "workspace": "/ws",
            "run_id": "run-1",
            "tasks": [],
            "execution_mode": "sequential",
        }
        inp = DirectorWorkflowInput.from_mapping(raw)
        assert inp.execution_mode == "sequential"

    def test_from_mapping_captures_director_config(self) -> None:
        """DirectorWorkflowInput.from_mapping reads from director_config."""
        raw = {
            "workspace": "/ws",
            "run_id": "run-1",
            "tasks": [],
            "metadata": {"director_config": {"execution_mode": "parallel"}},
        }
        inp = DirectorWorkflowInput.from_mapping(raw)
        assert inp.execution_mode == "parallel"


class TestDirectorTaskInput:
    """Tests for DirectorTaskInput dataclass."""

    def test_construction(self) -> None:
        """DirectorTaskInput can be constructed."""
        task = TaskContract(task_id="t1", title="Task 1")
        inp = DirectorTaskInput(workspace="/ws", run_id="run-1", task=task)
        assert inp.workspace == "/ws"
        assert inp.task.task_id == "t1"

    def test_from_mapping_with_phases(self) -> None:
        """DirectorTaskInput.from_mapping extracts phases."""
        raw = {
            "workspace": "/ws",
            "run_id": "run-1",
            "task": {"id": "t1", "title": "Task 1"},
            "phases": ["plan", "implement", "verify"],
        }
        inp = DirectorTaskInput.from_mapping(raw)
        assert inp.phases == ["plan", "implement", "verify"]


class TestDirectorTaskResult:
    """Tests for DirectorTaskResult dataclass."""

    def test_construction(self) -> None:
        """DirectorTaskResult can be constructed."""
        result = DirectorTaskResult(task_id="t1", status="completed")
        assert result.task_id == "t1"
        assert result.status == "completed"

    def test_from_mapping_with_completed_phases(self) -> None:
        """DirectorTaskResult.from_mapping extracts completed_phases."""
        raw = {
            "task_id": "t1",
            "status": "completed",
            "completed_phases": ["plan", "implement"],
            "errors": [],
        }
        result = DirectorTaskResult.from_mapping(raw)
        assert result.completed_phases == ["plan", "implement"]

    def test_from_mapping_filters_empty_phases(self) -> None:
        """DirectorTaskResult.from_mapping filters empty phases."""
        raw = {
            "task_id": "t1",
            "status": "completed",
            "completed_phases": ["plan", "", "  ", "implement"],
        }
        result = DirectorTaskResult.from_mapping(raw)
        assert result.completed_phases == ["plan", "implement"]


class TestQAWorkflowInput:
    """Tests for QAWorkflowInput dataclass."""

    def test_construction(self) -> None:
        """QAWorkflowInput can be constructed."""
        inp = QAWorkflowInput(
            workspace="/ws",
            run_id="run-1",
            director_status="completed",
        )
        assert inp.workspace == "/ws"
        assert inp.director_status == "completed"

    def test_from_mapping_with_task_results(self) -> None:
        """QAWorkflowInput.from_mapping extracts task_results."""
        raw = {
            "workspace": "/ws",
            "run_id": "run-1",
            "director_status": "completed",
            "task_results": [
                {"task_id": "t1", "status": "completed"},
                {"task_id": "t2", "status": "failed"},
            ],
        }
        inp = QAWorkflowInput.from_mapping(raw)
        assert len(inp.task_results) == 2


class TestQAWorkflowResult:
    """Tests for QAWorkflowResult dataclass."""

    def test_construction(self) -> None:
        """QAWorkflowResult can be constructed."""
        result = QAWorkflowResult(run_id="run-1", passed=True, reason="All tests pass")
        assert result.run_id == "run-1"
        assert result.passed is True
        assert result.reason == "All tests pass"

    def test_construction_with_evidence(self) -> None:
        """QAWorkflowResult accepts evidence dict."""
        result = QAWorkflowResult(
            run_id="run-1",
            passed=False,
            reason="Failures found",
            evidence={"failed_tests": ["test_a", "test_b"]},
        )
        assert result.evidence == {"failed_tests": ["test_a", "test_b"]}


class TestTaskFailureRecord:
    """Tests for TaskFailureRecord dataclass."""

    def test_construction(self) -> None:
        """TaskFailureRecord can be constructed."""
        record = TaskFailureRecord(
            task_id="t1",
            error_message="Failed",
            error_category="SYSTEM_TIMEOUT",
            retryable=True,
            max_retries=3,
            recovery_strategy="backoff",
        )
        assert record.task_id == "t1"
        assert record.error_category == "SYSTEM_TIMEOUT"
        assert record.retryable is True

    def test_to_dict(self) -> None:
        """TaskFailureRecord.to_dict returns dict representation."""
        record = TaskFailureRecord(
            task_id="t1",
            error_message="Failed",
            error_category="SYSTEM_TIMEOUT",
            retryable=True,
            max_retries=3,
            recovery_strategy="backoff",
        )
        result = record.to_dict()
        assert result["task_id"] == "t1"
        assert result["error_category"] == "SYSTEM_TIMEOUT"
        assert result["retryable"] is True


class TestPMWorkflowResult:
    """Tests for PMWorkflowResult dataclass."""

    def test_construction(self) -> None:
        """PMWorkflowResult can be constructed."""
        result = PMWorkflowResult(
            run_id="run-1",
            tasks=[],
            director_status="completed",
            qa_status="pending",
        )
        assert result.run_id == "run-1"
        assert result.tasks == []
        assert result.director_status == "completed"
        assert result.qa_status == "pending"

    def test_construction_with_metadata(self) -> None:
        """PMWorkflowResult accepts metadata."""
        result = PMWorkflowResult(
            run_id="run-1",
            tasks=[{"id": "t1"}],
            director_status="running",
            qa_status="not_started",
            metadata={"key": "value"},
        )
        assert result.metadata == {"key": "value"}


class TestDirectorWorkflowResult:
    """Tests for DirectorWorkflowResult dataclass."""

    def test_construction(self) -> None:
        """DirectorWorkflowResult can be constructed."""
        result = DirectorWorkflowResult(
            run_id="run-1",
            status="completed",
            completed_tasks=3,
            failed_tasks=0,
        )
        assert result.run_id == "run-1"
        assert result.status == "completed"
        assert result.completed_tasks == 3
        assert result.failed_tasks == 0

    def test_construction_with_metadata(self) -> None:
        """DirectorWorkflowResult accepts metadata."""
        result = DirectorWorkflowResult(
            run_id="run-1",
            status="completed",
            completed_tasks=3,
            failed_tasks=0,
            metadata={"processed": True},
        )
        assert result.metadata == {"processed": True}
