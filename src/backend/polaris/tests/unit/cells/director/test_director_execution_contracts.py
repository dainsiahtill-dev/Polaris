"""Tests for polaris.cells.director.execution.public contracts.

Covers dataclass validation, error classes, tool helpers, and
public surface imports from the execution cell boundary.
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
from polaris.cells.director.execution.public.tools import (
    ALLOWED_EXECUTION_COMMANDS,
    build_tool_cli_args,
    is_command_allowed,
    is_command_blocked,
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

    def test_whitespace_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(task_id="   ", workspace="/ws", instruction="do it")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(task_id="t1", workspace="", instruction="do it")

    def test_empty_instruction_raises(self) -> None:
        with pytest.raises(ValueError, match="instruction must be a non-empty string"):
            ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="")

    def test_attempt_less_than_one_raises(self) -> None:
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="do it", attempt=0)

    def test_attempt_one_is_valid(self) -> None:
        cmd = ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="do it", attempt=1)
        assert cmd.attempt == 1

    def test_metadata_copied(self) -> None:
        original = {"key": "value"}
        cmd = ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="do it", metadata=original)
        assert cmd.metadata == {"key": "value"}
        original["key"] = "changed"
        assert cmd.metadata == {"key": "value"}

    def test_run_id_optional(self) -> None:
        cmd = ExecuteDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="do it", run_id="r1")
        assert cmd.run_id == "r1"


class TestRetryDirectorTaskCommandV1:
    """Tests for RetryDirectorTaskCommandV1."""

    def test_valid_command(self) -> None:
        cmd = RetryDirectorTaskCommandV1(task_id="t1", workspace="/ws", reason="flaky")
        assert cmd.task_id == "t1"
        assert cmd.workspace == "/ws"
        assert cmd.reason == "flaky"
        assert cmd.max_attempts == 3

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            RetryDirectorTaskCommandV1(task_id="t1", workspace="/ws", reason="")

    def test_max_attempts_less_than_one_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryDirectorTaskCommandV1(task_id="t1", workspace="/ws", reason="flaky", max_attempts=0)

    def test_custom_max_attempts(self) -> None:
        cmd = RetryDirectorTaskCommandV1(task_id="t1", workspace="/ws", reason="flaky", max_attempts=5)
        assert cmd.max_attempts == 5


class TestGetDirectorTaskStatusQueryV1:
    """Tests for GetDirectorTaskStatusQueryV1."""

    def test_valid_query(self) -> None:
        q = GetDirectorTaskStatusQueryV1(task_id="t1", workspace="/ws")
        assert q.task_id == "t1"
        assert q.workspace == "/ws"
        assert q.run_id is None

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetDirectorTaskStatusQueryV1(task_id="", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GetDirectorTaskStatusQueryV1(task_id="t1", workspace="")

    def test_with_run_id(self) -> None:
        q = GetDirectorTaskStatusQueryV1(task_id="t1", workspace="/ws", run_id="r1")
        assert q.run_id == "r1"


class TestDirectorTaskStartedEventV1:
    """Tests for DirectorTaskStartedEventV1."""

    def test_valid_event(self) -> None:
        ev = DirectorTaskStartedEventV1(
            event_id="e1",
            task_id="t1",
            workspace="/ws",
            started_at="2026-01-01T00:00:00Z",
        )
        assert ev.event_id == "e1"
        assert ev.run_id is None

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            DirectorTaskStartedEventV1(event_id="", task_id="t1", workspace="/ws", started_at="2026-01-01T00:00:00Z")

    def test_empty_started_at_raises(self) -> None:
        with pytest.raises(ValueError, match="started_at must be a non-empty string"):
            DirectorTaskStartedEventV1(event_id="e1", task_id="t1", workspace="/ws", started_at="")

    def test_with_run_id(self) -> None:
        ev = DirectorTaskStartedEventV1(
            event_id="e1",
            task_id="t1",
            workspace="/ws",
            started_at="2026-01-01T00:00:00Z",
            run_id="r1",
        )
        assert ev.run_id == "r1"


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
        assert ev.status == "done"
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

    def test_empty_completed_at_raises(self) -> None:
        with pytest.raises(ValueError, match="completed_at must be a non-empty string"):
            DirectorTaskCompletedEventV1(event_id="e1", task_id="t1", workspace="/ws", status="done", completed_at="")

    def test_with_error_fields(self) -> None:
        ev = DirectorTaskCompletedEventV1(
            event_id="e1",
            task_id="t1",
            workspace="/ws",
            status="failed",
            completed_at="2026-01-01T00:00:00Z",
            error_code="E1",
            error_message="boom",
        )
        assert ev.error_code == "E1"
        assert ev.error_message == "boom"


class TestDirectorExecutionResultV1:
    """Tests for DirectorExecutionResultV1."""

    def test_valid_success_result(self) -> None:
        r = DirectorExecutionResultV1(ok=True, task_id="t1", workspace="/ws", status="done")
        assert r.evidence_paths == ()
        assert r.output_summary == ""
        assert r.error_code is None
        assert r.error_message is None

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            DirectorExecutionResultV1(ok=False, task_id="t1", workspace="/ws", status="failed")

    def test_failed_result_with_error_code_ok(self) -> None:
        r = DirectorExecutionResultV1(ok=False, task_id="t1", workspace="/ws", status="failed", error_code="E1")
        assert r.ok is False
        assert r.error_code == "E1"

    def test_failed_result_with_error_message_ok(self) -> None:
        r = DirectorExecutionResultV1(
            ok=False,
            task_id="t1",
            workspace="/ws",
            status="failed",
            error_message="something broke",
        )
        assert r.ok is False
        assert r.error_message == "something broke"

    def test_evidence_paths_coerced_to_tuple(self) -> None:
        r = DirectorExecutionResultV1(
            ok=True,
            task_id="t1",
            workspace="/ws",
            status="done",
            evidence_paths=["a.py", "b.py"],
        )
        assert r.evidence_paths == ("a.py", "b.py")

    def test_output_summary_set(self) -> None:
        r = DirectorExecutionResultV1(ok=True, task_id="t1", workspace="/ws", status="done", output_summary="summary")
        assert r.output_summary == "summary"


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

    def test_none_details_becomes_empty_dict(self) -> None:
        err = DirectorExecutionError("boom", details=None)
        assert err.details == {}

    def test_details_copied(self) -> None:
        original = {"key": "value"}
        err = DirectorExecutionError("boom", details=original)
        assert err.details == {"key": "value"}
        original["key"] = "changed"
        assert err.details == {"key": "value"}


class TestExecutionTools:
    """Tests for execution.public.tools helpers."""

    def test_is_command_allowed_with_allowed(self) -> None:
        assert is_command_allowed("pytest") is True

    def test_is_command_allowed_with_blocked_pattern(self) -> None:
        assert is_command_allowed("rm -rf /") is False

    def test_is_command_blocked(self) -> None:
        assert is_command_blocked("rm -rf /") is True
        assert is_command_blocked("pytest") is False

    def test_allowed_execution_commands_is_frozenset(self) -> None:
        assert isinstance(ALLOWED_EXECUTION_COMMANDS, frozenset)
        assert "pytest" in ALLOWED_EXECUTION_COMMANDS

    def test_build_tool_cli_args_with_list(self) -> None:
        args = build_tool_cli_args("pytest", ["-x", "tests"])
        assert args == ["-x", "tests"]

    def test_build_tool_cli_args_with_string_returns_empty(self) -> None:
        args = build_tool_cli_args("pytest", "-x tests")
        assert args == []

    def test_build_tool_cli_args_with_none_returns_empty(self) -> None:
        args = build_tool_cli_args("pytest", None)
        assert args == []

    def test_build_tool_cli_args_repo_tree(self) -> None:
        args = build_tool_cli_args("repo_tree", {"path": "/ws", "depth": 2})
        assert args == ["/ws", "--depth", "2"]

    def test_build_tool_cli_args_repo_rg(self) -> None:
        args = build_tool_cli_args("repo_rg", {"pattern": "foo", "path": "/ws"})
        assert args == ["foo", "/ws"]

    def test_build_tool_cli_args_unknown_tool(self) -> None:
        args = build_tool_cli_args("unknown_tool", {})
        assert args == []


class TestPublicSurfaceImports:
    """Smoke tests that public surface exports are importable."""

    def test_import_all_from_public_init(self) -> None:
        from polaris.cells.director.execution.public import (
            ALLOWED_EXECUTION_COMMANDS,
            DirectorExecutionError,
            rebind_director_service,
        )

        assert ALLOWED_EXECUTION_COMMANDS is not None
        assert DirectorExecutionError is not None
        assert rebind_director_service is not None
