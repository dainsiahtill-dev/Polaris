"""Unit tests for `delivery/cli` public contracts.

Tests the CliCommandType, ExecutionMode, ExitCode enumerations,
ExecuteCliCommandV1, QueryCliStatusV1, events, CommandResultV1,
error types, and command_type() classifier.
"""

from __future__ import annotations

import pytest
from polaris.cells.delivery.cli.public.contracts import (
    CliCommandCompletedEventV1,
    CliCommandStartedEventV1,
    CliCommandType,
    CommandErrorV1,
    CommandNotFoundError,
    CommandResultV1,
    CommandTimeoutError,
    ExecuteCliCommandV1,
    ExecutionMode,
    ExitCode,
    QueryCliStatusV1,
    WorkspaceNotFoundError,
    WorkspaceNotInitializedError,
)

# ---------------------------------------------------------------------------
# CliCommandType
# ---------------------------------------------------------------------------


class TestCliCommandType:
    def test_pm_commands(self) -> None:
        assert CliCommandType("pm.init") == CliCommandType.PM_INIT
        assert CliCommandType("pm.status") == CliCommandType.PM_STATUS
        assert CliCommandType("pm.requirement") == CliCommandType.PM_REQUIREMENT

    def test_director_commands(self) -> None:
        assert CliCommandType("director.run") == CliCommandType.DIRECTOR_RUN
        assert CliCommandType("director.serve") == CliCommandType.DIRECTOR_SERVE
        assert CliCommandType("director.console") == CliCommandType.DIRECTOR_CONSOLE

    def test_architect_commands(self) -> None:
        assert CliCommandType("architect.analyze") == CliCommandType.ARCHITECT_ANALYZE
        assert CliCommandType("architect.design") == CliCommandType.ARCHITECT_DESIGN

    def test_chief_engineer_commands(self) -> None:
        assert CliCommandType("chief_engineer.analysis") == CliCommandType.CHIEF_ENGINEER_ANALYSIS
        assert CliCommandType("chief_engineer.task") == CliCommandType.CHIEF_ENGINEER_TASK

    def test_generic_unknown_command_type(self) -> None:
        # ExecuteCliCommandV1.command_type() returns GENERIC for unknown commands
        cmd = ExecuteCliCommandV1(command="unknown.custom", workspace="/repo")
        assert cmd.command_type() == CliCommandType.GENERIC

    def test_is_string_enum(self) -> None:
        assert isinstance(CliCommandType.PM_INIT, str)
        assert CliCommandType.PM_INIT == "pm.init"


# ---------------------------------------------------------------------------
# ExecutionMode
# ---------------------------------------------------------------------------


class TestExecutionMode:
    def test_values(self) -> None:
        assert ExecutionMode.MANAGEMENT == "management"
        assert ExecutionMode.ROLE_EXECUTION == "role_execution"
        assert ExecutionMode.DAEMON == "daemon"

    def test_is_string_enum(self) -> None:
        assert isinstance(ExecutionMode.MANAGEMENT, str)


# ---------------------------------------------------------------------------
# ExitCode
# ---------------------------------------------------------------------------


class TestExitCode:
    def test_values(self) -> None:
        assert ExitCode.SUCCESS == 0
        assert ExitCode.GENERAL_ERROR == 1
        assert ExitCode.NOT_INITIALIZED == 2
        assert ExitCode.WORKSPACE_NOT_FOUND == 3
        assert ExitCode.INVALID_ARGS == 4
        assert ExitCode.TIMEOUT == 5
        assert ExitCode.INTERRUPTED == 130


# ---------------------------------------------------------------------------
# ExecuteCliCommandV1
# ---------------------------------------------------------------------------


class TestExecuteCliCommandV1HappyPath:
    def test_management_command_minimal(self) -> None:
        cmd = ExecuteCliCommandV1(command="pm.status", workspace="/repo")
        assert cmd.command == "pm.status"
        assert cmd.workspace == "/repo"
        assert cmd.execution_mode == ExecutionMode.MANAGEMENT
        assert cmd.arguments == {}
        assert cmd.role is None

    def test_role_execution_full(self) -> None:
        cmd = ExecuteCliCommandV1(
            command="director.task",
            workspace="/repo",
            execution_mode=ExecutionMode.ROLE_EXECUTION,
            role="director",
            arguments={"subject": "Implement login"},
            session_id="sess-1",
            timeout_seconds=300,
            metadata={"priority": "high"},
        )
        assert cmd.execution_mode == ExecutionMode.ROLE_EXECUTION
        assert cmd.role == "director"
        assert cmd.arguments == {"subject": "Implement login"}
        assert cmd.session_id == "sess-1"
        assert cmd.timeout_seconds == 300
        assert cmd.metadata == {"priority": "high"}

    def test_arguments_are_copied(self) -> None:
        original = {"subject": "x"}
        cmd = ExecuteCliCommandV1(command="pm.task", workspace="/repo", arguments=original)
        original.clear()
        assert cmd.arguments == {"subject": "x"}

    def test_metadata_are_copied(self) -> None:
        original = {"key": "value"}
        cmd = ExecuteCliCommandV1(command="pm.task", workspace="/repo", metadata=original)
        original.clear()
        assert cmd.metadata == {"key": "value"}


class TestExecuteCliCommandV1EdgeCases:
    def test_empty_command_raises(self) -> None:
        with pytest.raises(ValueError, match="command"):
            ExecuteCliCommandV1(command="", workspace="/repo")

    def test_whitespace_command_raises(self) -> None:
        with pytest.raises(ValueError, match="command"):
            ExecuteCliCommandV1(command="   ", workspace="/repo")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            ExecuteCliCommandV1(command="pm.status", workspace="")

    def test_role_execution_without_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role"):
            ExecuteCliCommandV1(
                command="director.task",
                workspace="/repo",
                execution_mode=ExecutionMode.ROLE_EXECUTION,
            )

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            ExecuteCliCommandV1(command="pm.status", workspace="/repo", timeout_seconds=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            ExecuteCliCommandV1(command="pm.status", workspace="/repo", timeout_seconds=-1)


# ---------------------------------------------------------------------------
# ExecuteCliCommandV1.command_type()
# ---------------------------------------------------------------------------


class TestExecuteCliCommandV1CommandType:
    def test_exact_pm_init(self) -> None:
        cmd = ExecuteCliCommandV1(command="pm.init", workspace="/repo")
        assert cmd.command_type() == CliCommandType.PM_INIT

    def test_exact_director_run(self) -> None:
        cmd = ExecuteCliCommandV1(command="director.run", workspace="/repo")
        assert cmd.command_type() == CliCommandType.DIRECTOR_RUN

    def test_prefix_match_pm_requirement(self) -> None:
        cmd = ExecuteCliCommandV1(command="pm.requirement.add", workspace="/repo")
        assert cmd.command_type() == CliCommandType.PM_REQUIREMENT

    def test_prefix_match_director_task(self) -> None:
        cmd = ExecuteCliCommandV1(command="director.task.execute", workspace="/repo")
        assert cmd.command_type() == CliCommandType.DIRECTOR_TASK

    def test_unknown_command_returns_generic(self) -> None:
        cmd = ExecuteCliCommandV1(command="unknown.custom", workspace="/repo")
        assert cmd.command_type() == CliCommandType.GENERIC


# ---------------------------------------------------------------------------
# QueryCliStatusV1
# ---------------------------------------------------------------------------


class TestQueryCliStatusV1HappyPath:
    def test_defaults(self) -> None:
        q = QueryCliStatusV1(workspace="/repo")
        assert q.workspace == "/repo"
        assert q.include_commands is True
        assert q.include_active_sessions is False


class TestQueryCliStatusV1EdgeCases:
    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            QueryCliStatusV1(workspace="")


# ---------------------------------------------------------------------------
# CliCommandStartedEventV1
# ---------------------------------------------------------------------------


class TestCliCommandStartedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = CliCommandStartedEventV1(
            event_id="evt-1",
            command="pm.status",
            workspace="/repo",
            execution_mode=ExecutionMode.MANAGEMENT,
            started_at="2026-03-24T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.command == "pm.status"
        assert evt.session_id is None


class TestCliCommandStartedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            CliCommandStartedEventV1(
                event_id="",
                command="pm.status",
                workspace="/repo",
                execution_mode=ExecutionMode.MANAGEMENT,
                started_at="2026-03-24T10:00:00Z",
            )

    def test_empty_command_raises(self) -> None:
        with pytest.raises(ValueError, match="command"):
            CliCommandStartedEventV1(
                event_id="e1",
                command="",
                workspace="/repo",
                execution_mode=ExecutionMode.MANAGEMENT,
                started_at="2026-03-24T10:00:00Z",
            )


# ---------------------------------------------------------------------------
# CliCommandCompletedEventV1
# ---------------------------------------------------------------------------


class TestCliCommandCompletedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = CliCommandCompletedEventV1(
            event_id="evt-1",
            command="pm.status",
            workspace="/repo",
            status="success",
            exit_code=0,
            completed_at="2026-03-24T10:01:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.status == "success"
        assert evt.duration_ms is None

    def test_with_duration(self) -> None:
        evt = CliCommandCompletedEventV1(
            event_id="evt-1",
            command="director.run",
            workspace="/repo",
            status="success",
            exit_code=0,
            completed_at="2026-03-24T10:01:00Z",
            duration_ms=5000,
            error_code="timeout",
            error_message="Command timed out",
        )
        assert evt.duration_ms == 5000
        assert evt.error_code == "timeout"


class TestCliCommandCompletedEventV1EdgeCases:
    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            CliCommandCompletedEventV1(
                event_id="e1",
                command="pm.status",
                workspace="/repo",
                status="",
                exit_code=0,
                completed_at="2026-03-24T10:01:00Z",
            )


# ---------------------------------------------------------------------------
# CommandResultV1
# ---------------------------------------------------------------------------


class TestCommandResultV1HappyPath:
    def test_success(self) -> None:
        res = CommandResultV1(
            ok=True,
            exit_code=0,
            command="pm.status",
            workspace="/repo",
            output="All tasks up to date",
        )
        assert res.ok is True
        assert res.exit_code == 0
        assert res.output == "All tasks up to date"
        assert res.structured == {}

    def test_failure(self) -> None:
        res = CommandResultV1(
            ok=False,
            exit_code=1,
            command="director.run",
            workspace="/repo",
            error_code="run_failed",
            error_message="Task execution failed",
        )
        assert res.ok is False
        assert res.error_code == "run_failed"

    def test_to_dict(self) -> None:
        res = CommandResultV1(
            ok=True,
            exit_code=0,
            command="pm.status",
            workspace="/repo",
        )
        d = res.to_dict()
        assert d["ok"] is True
        assert d["exit_code"] == 0
        assert d["command"] == "pm.status"
        assert isinstance(d["structured"], dict)


class TestCommandResultV1EdgeCases:
    def test_empty_command_raises(self) -> None:
        with pytest.raises(ValueError, match="command"):
            CommandResultV1(ok=True, exit_code=0, command="", workspace="/repo")

    def test_output_coerced_to_str(self) -> None:
        res = CommandResultV1(
            ok=True,
            exit_code=0,
            command="pm.status",
            workspace="/repo",
            output=12345,  # type: ignore[arg-type]
        )
        assert res.output == "12345"


# ---------------------------------------------------------------------------
# CommandErrorV1
# ---------------------------------------------------------------------------


class TestCommandErrorV1:
    def test_default_values(self) -> None:
        err = CommandErrorV1("CLI error")
        assert str(err) == "CLI error"
        assert err.code == "cli_error"
        assert err.details == {}
        assert err.exit_code == 1

    def test_custom_code_and_details(self) -> None:
        err = CommandErrorV1(
            "timeout",
            code="command_timeout",
            details={"timeout_seconds": 300},
            exit_code=5,
        )
        assert err.code == "command_timeout"
        assert err.details == {"timeout_seconds": 300}
        assert err.exit_code == 5

    def test_to_dict(self) -> None:
        err = CommandErrorV1("error", code="test", details={"k": "v"})
        d = err.to_dict()
        assert d["code"] == "test"
        assert d["message"] == "error"
        assert d["details"] == {"k": "v"}
        assert d["exit_code"] == 1

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            CommandErrorV1("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            CommandErrorV1("error", code="  ")


# ---------------------------------------------------------------------------
# CommandNotFoundError
# ---------------------------------------------------------------------------


class TestCommandNotFoundError:
    def test_properties(self) -> None:
        err = CommandNotFoundError("pm.xyz")
        assert str(err) == "Unknown CLI command: pm.xyz"
        assert err.code == "command_not_found"
        assert err.exit_code == ExitCode.INVALID_ARGS.value
        assert err.details == {"command": "pm.xyz"}


# ---------------------------------------------------------------------------
# CommandTimeoutError
# ---------------------------------------------------------------------------


class TestCommandTimeoutError:
    def test_properties(self) -> None:
        err = CommandTimeoutError("director.run", 300)
        assert "300s" in str(err)
        assert err.code == "command_timeout"
        assert err.exit_code == ExitCode.TIMEOUT.value
        assert err.details == {"command": "director.run", "timeout_seconds": 300}


# ---------------------------------------------------------------------------
# WorkspaceNotFoundError
# ---------------------------------------------------------------------------


class TestWorkspaceNotFoundError:
    def test_properties(self) -> None:
        err = WorkspaceNotFoundError("/nonexistent")
        assert "does not exist" in str(err)
        assert err.code == "workspace_not_found"
        assert err.exit_code == ExitCode.WORKSPACE_NOT_FOUND.value
        assert err.details == {"workspace": "/nonexistent"}


# ---------------------------------------------------------------------------
# WorkspaceNotInitializedError
# ---------------------------------------------------------------------------


class TestWorkspaceNotInitializedError:
    def test_properties(self) -> None:
        err = WorkspaceNotInitializedError("/repo", "pm.status")
        assert "not initialized" in str(err)
        # command is stored in details, not in the display message
        assert err.code == "workspace_not_initialized"
        assert err.exit_code == ExitCode.NOT_INITIALIZED.value
        assert err.details == {"workspace": "/repo", "command": "pm.status"}
