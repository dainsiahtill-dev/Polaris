"""Tests for workflow_runtime internal workflow_client module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.orchestration.workflow_runtime.internal.models import PMWorkflowInput
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import (
    WorkflowSubmissionResult,
    WorkflowUnavailableError,
    _normalize_workflow_input,
    cancel_workflow_sync,
    describe_workflow_sync,
    query_workflow_sync,
    submit_pm_workflow_sync,
    wait_for_workflow_completion_sync,
)


class TestNormalizeWorkflowInput:
    def test_pm_input_passthrough(self) -> None:
        inp = PMWorkflowInput(workspace="/tmp", run_id="r1")
        assert _normalize_workflow_input(inp) is inp

    def test_dict_conversion(self) -> None:
        raw = {"workspace": "/tmp", "run_id": "r1"}
        result = _normalize_workflow_input(raw)
        assert result.workspace == "/tmp"
        assert result.run_id == "r1"

    def test_invalid_type(self) -> None:
        with pytest.raises(TypeError):
            _normalize_workflow_input("invalid")


class TestSubmitPmWorkflowSync:
    def test_disabled(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.workflow_client.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=False),
        ):
            result = submit_pm_workflow_sync(PMWorkflowInput(workspace="/tmp", run_id="r1"))
            assert result.submitted is False
            assert result.status == "disabled"

    def test_invalid_input(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.workflow_client.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=True),
        ):
            result = submit_pm_workflow_sync({"workspace": "", "run_id": ""})
            assert result.submitted is False
            assert result.status == "invalid_request"


class TestDescribeWorkflowSync:
    def test_disabled(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.workflow_client.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=False),
        ):
            result = describe_workflow_sync("w1")
            assert result["ok"] is False
            assert result["error"] == "workflow_runtime_disabled"


class TestQueryWorkflowSync:
    def test_disabled(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.workflow_client.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=False),
        ):
            result = query_workflow_sync("w1", "q1")
            assert result["ok"] is False
            assert result["error"] == "workflow_runtime_disabled"


class TestCancelWorkflowSync:
    def test_disabled(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.workflow_client.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=False),
        ):
            result = cancel_workflow_sync("w1")
            assert result["ok"] is False
            assert result["error"] == "workflow_runtime_disabled"

    def test_empty_workflow_id(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.workflow_client.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=True),
        ):
            result = cancel_workflow_sync("")
            assert result["ok"] is False
            assert result["error"] == "workflow_id_required"


class TestWaitForWorkflowCompletionSync:
    def test_disabled(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.workflow_client.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=False),
        ):
            result = wait_for_workflow_completion_sync("w1")
            assert result["ok"] is False
            assert result["error"] == "workflow_runtime_disabled"

    def test_empty_workflow_id(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.workflow_client.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=True),
        ):
            result = wait_for_workflow_completion_sync("")
            assert result["ok"] is False
            assert result["error"] == "workflow_id_required"
