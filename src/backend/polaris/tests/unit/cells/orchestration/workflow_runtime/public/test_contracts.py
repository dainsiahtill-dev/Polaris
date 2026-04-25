"""Tests for workflow_runtime public contracts module."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.public.contracts import (
    CancelWorkflowCommandV1,
    QueryWorkflowEventsV1,
    QueryWorkflowStatusV1,
    StartWorkflowCommandV1,
    WorkflowExecutionCompletedEventV1,
    WorkflowExecutionResultV1,
    WorkflowExecutionStartedEventV1,
    WorkflowRuntimeError,
)

import pytest


class TestStartWorkflowCommandV1:
    def test_post_init_normalizes(self) -> None:
        cmd = StartWorkflowCommandV1(
            workflow_id="wf1",
            workspace="/tmp",
            workflow_type="pm",
            input_payload={"a": 1},
        )
        assert cmd.workflow_id == "wf1"
        assert cmd.workspace == "/tmp"
        assert cmd.input_payload == {"a": 1}

    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            StartWorkflowCommandV1(workflow_id="  ", workspace="/tmp", workflow_type="pm")


class TestCancelWorkflowCommandV1:
    def test_creation(self) -> None:
        cmd = CancelWorkflowCommandV1(workflow_id="wf1", workspace="/tmp", reason="test")
        assert cmd.reason == "test"
        assert cmd.requested_by == "system"

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            CancelWorkflowCommandV1(workflow_id="wf1", workspace="/tmp", reason="")


class TestQueryWorkflowStatusV1:
    def test_creation(self) -> None:
        cmd = QueryWorkflowStatusV1(workflow_id="wf1", workspace="/tmp")
        assert cmd.workflow_id == "wf1"


class TestQueryWorkflowEventsV1:
    def test_limit_validation(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            QueryWorkflowEventsV1(workflow_id="wf1", workspace="/tmp", limit=0)

    def test_offset_validation(self) -> None:
        with pytest.raises(ValueError, match="offset"):
            QueryWorkflowEventsV1(workflow_id="wf1", workspace="/tmp", offset=-1)


class TestWorkflowExecutionStartedEventV1:
    def test_creation(self) -> None:
        evt = WorkflowExecutionStartedEventV1(
            event_id="e1",
            workflow_id="wf1",
            workspace="/tmp",
            started_at="2024-01-01T00:00:00",
        )
        assert evt.event_id == "e1"


class TestWorkflowExecutionCompletedEventV1:
    def test_creation(self) -> None:
        evt = WorkflowExecutionCompletedEventV1(
            event_id="e1",
            workflow_id="wf1",
            workspace="/tmp",
            status="completed",
            completed_at="2024-01-01T00:00:00",
        )
        assert evt.status == "completed"


class TestWorkflowExecutionResultV1:
    def test_post_init(self) -> None:
        result = WorkflowExecutionResultV1(
            ok=True,
            workflow_id="wf1",
            workspace="/tmp",
            status="completed",
        )
        assert result.metrics == {}


class TestWorkflowRuntimeError:
    def test_error_code_and_details(self) -> None:
        err = WorkflowRuntimeError("boom", code="E123", details={"key": "val"})
        assert str(err) == "boom"
        assert err.code == "E123"
        assert err.details == {"key": "val"}
