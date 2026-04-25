"""Tests for workflow_runtime internal models module."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.orchestration.workflow_runtime.internal.models import (
    DirectorTaskInput,
    DirectorTaskResult,
    DirectorWorkflowInput,
    ExecutionEvent,
    PMWorkflowInput,
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


class TestWorkflowIdHelpers:
    def test_pm_workflow_id(self) -> None:
        assert pm_workflow_id("run-1") == "polaris-pm-run-1"
        assert pm_workflow_id("") == "polaris-pm-adhoc"

    def test_director_workflow_id(self) -> None:
        assert director_workflow_id("run-1") == "polaris-director-run-1"

    def test_qa_workflow_id(self) -> None:
        assert qa_workflow_id("run-1") == "polaris-qa-run-1"


class TestUtcNowIso:
    def test_returns_iso_string(self) -> None:
        result = utc_now_iso()
        assert isinstance(result, str)
        assert result.endswith("+00:00")

    def test_uses_workflow_api_now(self) -> None:
        mock_api = MagicMock()
        mock_api.now.return_value = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.models.get_workflow_api",
            return_value=mock_api,
        ):
            result = utc_now_iso()
            assert isinstance(result, str)


class TestExecutionEvent:
    def test_create_and_to_dict(self) -> None:
        event = ExecutionEvent.create(stage="plan", message="hello", details={"k": "v"})
        assert event.stage == "plan"
        assert event.message == "hello"
        assert event.details == {"k": "v"}
        d = event.to_dict()
        assert d["stage"] == "plan"
        assert d["message"] == "hello"
        assert d["details"] == {"k": "v"}

    def test_frozen(self) -> None:
        event = ExecutionEvent.create(stage="s", message="m")
        with pytest.raises(FrozenInstanceError):
            event.stage = "x"  # type: ignore[misc]


class TestTaskExecutionStatus:
    def test_to_dict(self) -> None:
        status = TaskExecutionStatus(task_id="t1", state="running")
        d = status.to_dict()
        assert d["task_id"] == "t1"
        assert d["state"] == "running"


class TestTaskContract:
    def test_from_mapping(self) -> None:
        raw = {"id": "t1", "title": "Task One", "goal": "do it"}
        contract = TaskContract.from_mapping(raw)
        assert contract.task_id == "t1"
        assert contract.title == "Task One"
        assert contract.goal == "do it"

    def test_to_dict(self) -> None:
        contract = TaskContract(task_id="t1", title="T", goal="G", payload={"extra": 1})
        d = contract.to_dict()
        assert d["id"] == "t1"
        assert d["title"] == "T"
        assert d["goal"] == "G"
        assert d["extra"] == 1


class TestPMWorkflowInput:
    def test_from_mapping(self) -> None:
        raw = {
            "workspace": "/tmp/ws",
            "run_id": "r1",
            "precomputed_payload": {"tasks": [{"id": "t1", "title": "T"}]},
        }
        inp = PMWorkflowInput.from_mapping(raw)
        assert inp.workspace == "/tmp/ws"
        assert inp.run_id == "r1"
        tasks = inp.payload_tasks()
        assert len(tasks) == 1
        assert tasks[0].task_id == "t1"

    def test_workflow_id(self) -> None:
        inp = PMWorkflowInput(workspace=".", run_id="r1")
        assert inp.workflow_id == pm_workflow_id("r1")


class TestDirectorWorkflowInput:
    def test_from_mapping_defaults(self) -> None:
        raw = {"workspace": "/tmp/ws", "run_id": "r1", "tasks": [{"id": "t1", "title": "T"}]}
        inp = DirectorWorkflowInput.from_mapping(raw)
        assert inp.workspace == "/tmp/ws"
        assert inp.execution_mode == "parallel"
        assert inp.max_parallel_tasks == 3

    def test_from_mapping_with_director_config(self) -> None:
        raw = {
            "workspace": "/tmp/ws",
            "run_id": "r1",
            "tasks": [],
            "metadata": {
                "director_config": {
                    "execution_mode": "serial",
                    "max_parallel_tasks": 1,
                }
            },
        }
        inp = DirectorWorkflowInput.from_mapping(raw)
        assert inp.execution_mode == "serial"
        assert inp.max_parallel_tasks == 1


class TestDirectorTaskInput:
    def test_from_mapping(self) -> None:
        raw = {
            "workspace": "/tmp/ws",
            "run_id": "r1",
            "task": {"id": "t1", "title": "T"},
            "phases": ["plan", "exec"],
        }
        inp = DirectorTaskInput.from_mapping(raw)
        assert inp.task.task_id == "t1"
        assert inp.phases == ["plan", "exec"]


class TestDirectorTaskResult:
    def test_from_mapping(self) -> None:
        raw = {"task_id": "t1", "status": "completed", "completed_phases": ["plan"]}
        result = DirectorTaskResult.from_mapping(raw)
        assert result.task_id == "t1"
        assert result.completed_phases == ["plan"]


class TestQAWorkflowInput:
    def test_from_mapping(self) -> None:
        raw = {
            "workspace": "/tmp/ws",
            "run_id": "r1",
            "director_status": "completed",
            "task_results": [{"task_id": "t1", "status": "ok"}],
        }
        inp = QAWorkflowInput.from_mapping(raw)
        assert inp.director_status == "completed"
        assert len(inp.task_results) == 1


class TestQAWorkflowResult:
    def test_creation(self) -> None:
        result = QAWorkflowResult(run_id="r1", passed=True, reason="all good")
        assert result.passed is True


class TestTaskFailureRecord:
    def test_to_dict(self) -> None:
        record = TaskFailureRecord(
            task_id="t1",
            error_message="boom",
            error_category="runtime",
            retryable=False,
            max_retries=0,
            recovery_strategy="abort",
        )
        d = record.to_dict()
        assert d["task_id"] == "t1"
        assert d["error_category"] == "runtime"
