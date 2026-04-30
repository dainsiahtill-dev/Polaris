"""Unit tests for orchestration.workflow_runtime public contracts.

Tests frozen dataclasses, validation, serialisation,
and the WorkflowRuntimeError custom error type.
"""

from __future__ import annotations

import pytest
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

# ---------------------------------------------------------------------------
# StartWorkflowCommandV1
# ---------------------------------------------------------------------------


class TestStartWorkflowCommandV1HappyPath:
    def test_minimal(self) -> None:
        cmd = StartWorkflowCommandV1(
            workflow_id="wf-1",
            workspace="/ws",
            workflow_type="pm",
        )
        assert cmd.workflow_id == "wf-1"
        assert cmd.workspace == "/ws"
        assert cmd.workflow_type == "pm"
        assert cmd.input_payload == {}
        assert cmd.run_id is None

    def test_full(self) -> None:
        cmd = StartWorkflowCommandV1(
            workflow_id="wf-2",
            workspace="/repo",
            workflow_type="director",
            input_payload={"tasks": ["t-1"]},
            run_id="run-1",
        )
        assert cmd.input_payload == {"tasks": ["t-1"]}
        assert cmd.run_id == "run-1"

    def test_whitespace_normalised(self) -> None:
        cmd = StartWorkflowCommandV1(
            workflow_id="  wf-3  ",
            workspace="  /ws  ",
            workflow_type="  qa  ",
        )
        assert cmd.workflow_id == "wf-3"
        assert cmd.workspace == "/ws"
        assert cmd.workflow_type == "qa"

    def test_input_payload_copy(self) -> None:
        original = {"foo": "bar"}
        cmd = StartWorkflowCommandV1(
            workflow_id="wf",
            workspace="/ws",
            workflow_type="pm",
            input_payload=original,
        )
        original.clear()
        assert cmd.input_payload == {"foo": "bar"}


class TestStartWorkflowCommandV1EdgeCases:
    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_id"):
            StartWorkflowCommandV1(workflow_id="", workspace="/ws", workflow_type="pm")

    def test_whitespace_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_id"):
            StartWorkflowCommandV1(workflow_id="   ", workspace="/ws", workflow_type="pm")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            StartWorkflowCommandV1(workflow_id="wf", workspace="", workflow_type="pm")

    def test_empty_workflow_type_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_type"):
            StartWorkflowCommandV1(workflow_id="wf", workspace="/ws", workflow_type="")


# ---------------------------------------------------------------------------
# CancelWorkflowCommandV1
# ---------------------------------------------------------------------------


class TestCancelWorkflowCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = CancelWorkflowCommandV1(
            workflow_id="wf-1",
            workspace="/ws",
            reason="user requested",
        )
        assert cmd.workflow_id == "wf-1"
        assert cmd.workspace == "/ws"
        assert cmd.reason == "user requested"
        assert cmd.requested_by == "system"

    def test_custom_requested_by(self) -> None:
        cmd = CancelWorkflowCommandV1(
            workflow_id="wf-2",
            workspace="/ws",
            reason="timeout",
            requested_by="scheduler",
        )
        assert cmd.requested_by == "scheduler"

    def test_whitespace_normalised(self) -> None:
        cmd = CancelWorkflowCommandV1(
            workflow_id="  wf-3  ",
            workspace="  /ws  ",
            reason="  manual  ",
            requested_by="  user  ",
        )
        assert cmd.workflow_id == "wf-3"
        assert cmd.workspace == "/ws"
        assert cmd.reason == "manual"
        assert cmd.requested_by == "user"


class TestCancelWorkflowCommandV1EdgeCases:
    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_id"):
            CancelWorkflowCommandV1(workflow_id="", workspace="/ws", reason="r")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CancelWorkflowCommandV1(workflow_id="wf", workspace="", reason="r")

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            CancelWorkflowCommandV1(workflow_id="wf", workspace="/ws", reason="")

    def test_empty_requested_by_raises(self) -> None:
        with pytest.raises(ValueError, match="requested_by"):
            CancelWorkflowCommandV1(
                workflow_id="wf",
                workspace="/ws",
                reason="r",
                requested_by="",
            )


# ---------------------------------------------------------------------------
# QueryWorkflowStatusV1
# ---------------------------------------------------------------------------


class TestQueryWorkflowStatusV1HappyPath:
    def test_construction(self) -> None:
        q = QueryWorkflowStatusV1(workflow_id="wf-1", workspace="/ws")
        assert q.workflow_id == "wf-1"
        assert q.workspace == "/ws"

    def test_whitespace_normalised(self) -> None:
        q = QueryWorkflowStatusV1(workflow_id="  wf-2  ", workspace="  /ws  ")
        assert q.workflow_id == "wf-2"
        assert q.workspace == "/ws"


class TestQueryWorkflowStatusV1EdgeCases:
    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_id"):
            QueryWorkflowStatusV1(workflow_id="", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            QueryWorkflowStatusV1(workflow_id="wf", workspace="")


# ---------------------------------------------------------------------------
# QueryWorkflowEventsV1
# ---------------------------------------------------------------------------


class TestQueryWorkflowEventsV1HappyPath:
    def test_construction(self) -> None:
        q = QueryWorkflowEventsV1(workflow_id="wf-1", workspace="/ws")
        assert q.workflow_id == "wf-1"
        assert q.workspace == "/ws"
        assert q.limit == 100
        assert q.offset == 0

    def test_custom_limit_and_offset(self) -> None:
        q = QueryWorkflowEventsV1(
            workflow_id="wf-2",
            workspace="/ws",
            limit=50,
            offset=10,
        )
        assert q.limit == 50
        assert q.offset == 10

    def test_whitespace_normalised(self) -> None:
        q = QueryWorkflowEventsV1(
            workflow_id="  wf-3  ",
            workspace="  /ws  ",
        )
        assert q.workflow_id == "wf-3"
        assert q.workspace == "/ws"


class TestQueryWorkflowEventsV1EdgeCases:
    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_id"):
            QueryWorkflowEventsV1(workflow_id="", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            QueryWorkflowEventsV1(workflow_id="wf", workspace="")

    def test_zero_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            QueryWorkflowEventsV1(workflow_id="wf", workspace="/ws", limit=0)

    def test_negative_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            QueryWorkflowEventsV1(workflow_id="wf", workspace="/ws", limit=-1)

    def test_negative_offset_raises(self) -> None:
        with pytest.raises(ValueError, match="offset"):
            QueryWorkflowEventsV1(workflow_id="wf", workspace="/ws", offset=-1)


# ---------------------------------------------------------------------------
# WorkflowExecutionStartedEventV1
# ---------------------------------------------------------------------------


class TestWorkflowExecutionStartedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = WorkflowExecutionStartedEventV1(
            event_id="evt-1",
            workflow_id="wf-1",
            workspace="/ws",
            started_at="2026-03-23T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.workflow_id == "wf-1"
        assert evt.workspace == "/ws"
        assert evt.started_at == "2026-03-23T10:00:00Z"
        assert evt.run_id is None

    def test_with_run_id(self) -> None:
        evt = WorkflowExecutionStartedEventV1(
            event_id="evt-2",
            workflow_id="wf-2",
            workspace="/ws",
            started_at="2026-01-01T00:00:00Z",
            run_id="run-1",
        )
        assert evt.run_id == "run-1"

    def test_whitespace_normalised(self) -> None:
        evt = WorkflowExecutionStartedEventV1(
            event_id="  evt-3  ",
            workflow_id="  wf-3  ",
            workspace="  /ws  ",
            started_at="  2026-01-01T00:00:00Z  ",
        )
        assert evt.event_id == "evt-3"
        assert evt.workflow_id == "wf-3"
        assert evt.workspace == "/ws"
        assert evt.started_at == "2026-01-01T00:00:00Z"


class TestWorkflowExecutionStartedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            WorkflowExecutionStartedEventV1(
                event_id="",
                workflow_id="wf",
                workspace="/ws",
                started_at="t",
            )

    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_id"):
            WorkflowExecutionStartedEventV1(
                event_id="e",
                workflow_id="",
                workspace="/ws",
                started_at="t",
            )

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            WorkflowExecutionStartedEventV1(
                event_id="e",
                workflow_id="wf",
                workspace="",
                started_at="t",
            )

    def test_empty_started_at_raises(self) -> None:
        with pytest.raises(ValueError, match="started_at"):
            WorkflowExecutionStartedEventV1(
                event_id="e",
                workflow_id="wf",
                workspace="/ws",
                started_at="",
            )


# ---------------------------------------------------------------------------
# WorkflowExecutionCompletedEventV1
# ---------------------------------------------------------------------------


class TestWorkflowExecutionCompletedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = WorkflowExecutionCompletedEventV1(
            event_id="evt-1",
            workflow_id="wf-1",
            workspace="/ws",
            status="completed",
            completed_at="2026-03-23T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.workflow_id == "wf-1"
        assert evt.status == "completed"
        assert evt.completed_at == "2026-03-23T10:00:00Z"
        assert evt.run_id is None
        assert evt.error_code is None
        assert evt.error_message is None

    def test_with_optional_fields(self) -> None:
        evt = WorkflowExecutionCompletedEventV1(
            event_id="evt-2",
            workflow_id="wf-2",
            workspace="/ws",
            status="failed",
            completed_at="2026-01-01T00:00:00Z",
            run_id="run-1",
            error_code="TIMEOUT",
            error_message="Execution timed out",
        )
        assert evt.run_id == "run-1"
        assert evt.error_code == "TIMEOUT"
        assert evt.error_message == "Execution timed out"

    def test_whitespace_normalised(self) -> None:
        evt = WorkflowExecutionCompletedEventV1(
            event_id="  evt-3  ",
            workflow_id="  wf-3  ",
            workspace="  /ws  ",
            status="  completed  ",
            completed_at="  2026-01-01T00:00:00Z  ",
        )
        assert evt.event_id == "evt-3"
        assert evt.workflow_id == "wf-3"
        assert evt.status == "completed"
        assert evt.completed_at == "2026-01-01T00:00:00Z"


class TestWorkflowExecutionCompletedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            WorkflowExecutionCompletedEventV1(
                event_id="",
                workflow_id="wf",
                workspace="/ws",
                status="s",
                completed_at="t",
            )

    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_id"):
            WorkflowExecutionCompletedEventV1(
                event_id="e",
                workflow_id="",
                workspace="/ws",
                status="s",
                completed_at="t",
            )

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            WorkflowExecutionCompletedEventV1(
                event_id="e",
                workflow_id="wf",
                workspace="",
                status="s",
                completed_at="t",
            )

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            WorkflowExecutionCompletedEventV1(
                event_id="e",
                workflow_id="wf",
                workspace="/ws",
                status="",
                completed_at="t",
            )

    def test_empty_completed_at_raises(self) -> None:
        with pytest.raises(ValueError, match="completed_at"):
            WorkflowExecutionCompletedEventV1(
                event_id="e",
                workflow_id="wf",
                workspace="/ws",
                status="s",
                completed_at="",
            )


# ---------------------------------------------------------------------------
# WorkflowExecutionResultV1
# ---------------------------------------------------------------------------


class TestWorkflowExecutionResultV1HappyPath:
    def test_success(self) -> None:
        res = WorkflowExecutionResultV1(
            ok=True,
            workflow_id="wf-1",
            workspace="/ws",
            status="completed",
            current_step="step-5",
            metrics={"duration_ms": 1200},
        )
        assert res.ok is True
        assert res.workflow_id == "wf-1"
        assert res.status == "completed"
        assert res.current_step == "step-5"
        assert res.metrics == {"duration_ms": 1200}

    def test_failure(self) -> None:
        res = WorkflowExecutionResultV1(
            ok=False,
            workflow_id="wf-2",
            workspace="/ws",
            status="failed",
        )
        assert res.ok is False
        assert res.current_step is None
        assert res.metrics == {}

    def test_whitespace_normalised(self) -> None:
        res = WorkflowExecutionResultV1(
            ok=True,
            workflow_id="  wf-3  ",
            workspace="  /ws  ",
            status="  running  ",
        )
        assert res.workflow_id == "wf-3"
        assert res.workspace == "/ws"
        assert res.status == "running"

    def test_metrics_copy(self) -> None:
        original = {"foo": "bar"}
        res = WorkflowExecutionResultV1(
            ok=True,
            workflow_id="wf",
            workspace="/ws",
            status="ok",
            metrics=original,
        )
        original.clear()
        assert res.metrics == {"foo": "bar"}


class TestWorkflowExecutionResultV1EdgeCases:
    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ValueError, match="workflow_id"):
            WorkflowExecutionResultV1(ok=True, workflow_id="", workspace="/ws", status="ok")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            WorkflowExecutionResultV1(ok=True, workflow_id="wf", workspace="", status="ok")

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            WorkflowExecutionResultV1(ok=True, workflow_id="wf", workspace="/ws", status="")


# ---------------------------------------------------------------------------
# WorkflowRuntimeError
# ---------------------------------------------------------------------------


class TestWorkflowRuntimeError:
    def test_default_values(self) -> None:
        err = WorkflowRuntimeError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.code == "workflow_runtime_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = WorkflowRuntimeError(
            "Contract invalid",
            code="CONTRACT_INVALID",
            details={"workflow_id": "wf-1"},
        )
        assert str(err) == "Contract invalid"
        assert err.code == "CONTRACT_INVALID"
        assert err.details == {"workflow_id": "wf-1"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            WorkflowRuntimeError("")

    def test_whitespace_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            WorkflowRuntimeError("   ")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            WorkflowRuntimeError("error", code="")

    def test_whitespace_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            WorkflowRuntimeError("error", code="   ")

    def test_details_copy(self) -> None:
        original = {"key": "value"}
        err = WorkflowRuntimeError("x", details=original)
        original.clear()
        assert err.details == {"key": "value"}

    def test_inherits_runtime_error(self) -> None:
        err = WorkflowRuntimeError("boom")
        assert isinstance(err, RuntimeError)
