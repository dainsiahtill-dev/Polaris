"""Tests for workflow_runtime internal workflow_client module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.orchestration.workflow_runtime.internal import workflow_client
from polaris.cells.orchestration.workflow_runtime.internal.models import PMWorkflowInput
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import (
    _normalize_workflow_input,
    _submit_pm_workflow_async,
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

    @pytest.mark.asyncio
    async def test_wait_until_complete_keeps_runtime_loop_alive_until_terminal(self, monkeypatch) -> None:
        class FakeAdapter:
            _running = False

            def __init__(self) -> None:
                self.describe_calls = 0

            async def start(self) -> None:
                self._running = True

            async def submit_workflow(self, workflow_name, workflow_id, payload):
                return SimpleNamespace(
                    workflow_id=workflow_id,
                    run_id=workflow_id,
                    status="running",
                    result={"mode": "legacy"},
                    error="",
                )

            async def describe_workflow(self, workflow_id):
                self.describe_calls += 1
                if self.describe_calls < 2:
                    return {"workflow_id": workflow_id, "status": "running", "result": {}}
                return {
                    "workflow_id": workflow_id,
                    "status": "completed",
                    "result": {"status": "completed", "ok": True},
                }

        adapter = FakeAdapter()

        async def fake_get_adapter() -> FakeAdapter:
            return adapter

        monkeypatch.setattr(workflow_client, "get_adapter", fake_get_adapter)

        result = await _submit_pm_workflow_async(
            PMWorkflowInput(workspace="/tmp/ws", run_id="run-1"),
            wait_until_complete=True,
            timeout_seconds=2,
            poll_interval_seconds=0.01,
        )

        assert result.submitted is True
        assert result.status == "completed"
        assert adapter.describe_calls >= 2
        assert result.details["final"]["status"] == "completed"


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
