"""Tests for polaris.cells.director.execution.public.contracts.

Covers all frozen dataclasses, validation logic in __post_init__,
and the custom error class.
"""

from __future__ import annotations

import pytest
from polaris.cells.director.execution.public.contracts import (
    DirectorExecutionError,
    DirectorExecutionResultV1,
    DirectorTaskCompletedEventV1,
    DirectorTaskStartedEventV1,
    ExecuteDirectorTaskCommandV1,
    GetDirectorTaskStatusQueryV1,
    RetryDirectorTaskCommandV1,
)


class TestExecuteDirectorTaskCommandV1:
    """Tests for ExecuteDirectorTaskCommandV1."""

    def test_valid_command(self) -> None:
        cmd = ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="do it")
        assert cmd.task_id == "t1"
        assert cmd.workspace == "/ws"
        assert cmd.instruction == "do it"
        assert cmd.run_id is None
        assert cmd.attempt == 1
        assert cmd.metadata == {}

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(task_id="", workspace="/ws", instruction="do it")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(task_id="t1", workspace="", instruction="do it")

    def test_empty_instruction_raises(self) -> None:
        with pytest.raises(ValueError, match="instruction must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="")

    def test_attempt_less_than_one_raises(self) -> None:
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="do it", attempt=0)

    def test_metadata_copied(self) -> None:
        original = {"key": "value"}
        cmd = ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="do it", metadata=original)
        assert cmd.metadata == {"key": "value"}
        original["key"] = "changed"
        assert cmd.metadata == {"key": "value"}


class TestRetryDirectorTaskCommandV1:
    """Tests for RetryDirectorTaskCommandV1."""

    def test_valid_command(self) -> None:
        cmd = RetryDirectorTaskCommandV1(task_id="t1", workspace="/ws", reason="flaky")
        assert cmd.max_attempts == 3

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            RetryDirectorTaskCommandV1(task_id="t1", workspace="/ws", reason="")

    def test_max_attempts_less_than_one_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryDirectorTaskCommandV1(task_id="t1", workspace="/ws", reason="flaky", max_attempts=0)


class TestGetDirectorTaskStatusQueryV1:
    """Tests for GetDirectorTaskStatusQueryV1."""

    def test_valid_query(self) -> None:
        q = GetDirectorTaskStatusQueryV1(task_id="t1", workspace="/ws")
        assert q.run_id is None

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetDirectorTaskStatusQueryV1(task_id="", workspace="/ws")


class TestDirectorTaskStartedEventV1:
    """Tests for DirectorTaskStartedEventV1."""

    def test_valid_event(self) -> None:
        ev = DirectorTaskStartedEventV1(event_id="e1", task_id="t1", workspace="/ws", started_at="2026-01-01T00:00:00Z")
        assert ev.run_id is None

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            DirectorTaskStartedEventV1(event_id="", task_id="t1", workspace="/ws", started_at="2026-01-01T00:00:00Z")

    def test_empty_started_at_raises(self) -> None:
        with pytest.raises(ValueError, match="started_at must be a non-empty string"):
            DirectorTaskStartedEventV1(event_id="e1", task_id="t1", workspace="/ws", started_at="")


class TestDirectorTaskCompletedEventV1:
    """Tests for DirectorTaskCompletedEventV1."""

    def test_valid_event(self) -> None:
        ev = DirectorTaskCompletedEventV1(
            event_id="e1",
            task_id="t1",
            workspace="/ws",
            status="done",
            completed_at="2026-01-01T00:00:00Z",
        )
        assert ev.error_code is None
        assert ev.error_message is None

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            DirectorTaskCompletedEventV1(
                event_id="e1",
                task_id="t1",
                workspace="/ws",
                status="",
                completed_at="2026-01-01T00:00:00Z",
            )


class TestDirectorExecutionResultV1:
    """Tests for DirectorExecutionResultV1."""

    def test_valid_success_result(self) -> None:
        r = DirectorExecutionResultV1(ok=True, task_id="t1", workspace="/ws", status="done")
        assert r.evidence_paths == ()
        assert r.output_summary == ""
        assert r.error_code is None

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            DirectorExecutionResultV1(ok=False, task_id="t1", workspace="/ws", status="failed")

    def test_evidence_paths_coerced(self) -> None:
        r = DirectorExecutionResultV1(
            ok=True,
            task_id="t1",
            workspace="/ws",
            status="done",
            evidence_paths=["a.py", "b.py"],
        )
        assert r.evidence_paths == ("a.py", "b.py")


class TestDirectorExecutionError:
    """Tests for DirectorExecutionError."""

    def test_defaults(self) -> None:
        err = DirectorExecutionError("boom")
        assert str(err) == "boom"
        assert err.code == "director_execution_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = DirectorExecutionError("boom", code="E1", details={"k": "v"})
        assert err.code == "E1"
        assert err.details == {"k": "v"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            DirectorExecutionError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            DirectorExecutionError("boom", code="")
