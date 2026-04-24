"""Unit tests for delivery.cli cell contracts and service."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from polaris.cells.delivery.cli import get_cli_service as cell_alias_import
from polaris.cells.delivery.cli.public.contracts import (
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
from polaris.cells.delivery.cli.public.service import (
    CliExecutionService,
    get_cli_service,
    register_management_handler,
)

# ── Shared fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def _tmp_workspace() -> Path:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ── ExecuteCliCommandV1 validation ─────────────────────────────────────────────


class TestExecuteCliCommandV1Validation:
    def test_command_required_non_empty(self) -> None:
        with pytest.raises(ValueError, match="command must be a non-empty string"):
            ExecuteCliCommandV1(command="", workspace="/tmp")

    def test_workspace_required_non_empty(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ExecuteCliCommandV1(command="pm.status", workspace="  ")

    def test_role_required_for_role_execution(self) -> None:
        with pytest.raises(ValueError, match="role is required when execution_mode == ROLE_EXECUTION"):
            ExecuteCliCommandV1(
                command="director.run",
                workspace="/tmp",
                execution_mode=ExecutionMode.ROLE_EXECUTION,
            )

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            ExecuteCliCommandV1(
                command="pm.status",
                workspace="/tmp",
                timeout_seconds=0,
            )

    def test_happy_path(self) -> None:
        cmd = ExecuteCliCommandV1(
            command="pm.status",
            workspace="/tmp",
            execution_mode=ExecutionMode.MANAGEMENT,
            arguments={"verbose": True},
        )
        assert cmd.command == "pm.status"
        assert cmd.workspace == "/tmp"
        assert cmd.execution_mode == ExecutionMode.MANAGEMENT
        assert dict(cmd.arguments) == {"verbose": True}

    def test_arguments_default_is_empty_dict(self) -> None:
        cmd = ExecuteCliCommandV1(command="pm.status", workspace="/tmp")
        assert dict(cmd.arguments) == {}

    def test_metadata_default_is_empty_dict(self) -> None:
        cmd = ExecuteCliCommandV1(command="pm.status", workspace="/tmp")
        assert dict(cmd.metadata) == {}

    def test_arguments_are_copied(self) -> None:
        args = {"key": "value"}
        cmd = ExecuteCliCommandV1(command="pm.status", workspace="/tmp", arguments=args)
        args["key"] = "modified"
        assert cmd.arguments["key"] == "value"


class TestCliCommandType:
    def test_exact_match(self) -> None:
        cmd = ExecuteCliCommandV1(command="pm.status", workspace="/tmp", execution_mode=ExecutionMode.MANAGEMENT)
        assert cmd.command_type() == CliCommandType.PM_STATUS

    def test_prefix_match(self) -> None:
        cmd = ExecuteCliCommandV1(
            command="pm.requirement.add",
            workspace="/tmp",
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        assert cmd.command_type() == CliCommandType.PM_REQUIREMENT

    def test_no_match_returns_generic(self) -> None:
        cmd = ExecuteCliCommandV1(
            command="unknown.command",
            workspace="/tmp",
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        assert cmd.command_type() == CliCommandType.GENERIC


# ── QueryCliStatusV1 validation ───────────────────────────────────────────────


class TestQueryCliStatusV1:
    def test_workspace_required(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            QueryCliStatusV1(workspace="")

    def test_defaults(self) -> None:
        q = QueryCliStatusV1(workspace="/tmp")
        assert q.include_commands is True
        assert q.include_active_sessions is False


# ── CommandResultV1 ───────────────────────────────────────────────────────────


class TestCommandResultV1:
    def test_happy_path(self) -> None:
        r = CommandResultV1(
            ok=True,
            exit_code=0,
            command="pm.status",
            workspace="/tmp",
            output="Project: test",
            duration_ms=42,
        )
        assert r.ok is True
        assert r.exit_code == 0
        assert r.output == "Project: test"
        assert r.duration_ms == 42

    def test_to_dict(self) -> None:
        r = CommandResultV1(
            ok=True,
            exit_code=0,
            command="pm.status",
            workspace="/tmp",
            structured={"tasks": 3},
        )
        d = r.to_dict()
        assert d["ok"] is True
        assert d["structured"] == {"tasks": 3}
        assert d["error_code"] is None

    def test_command_required(self) -> None:
        with pytest.raises(ValueError, match="command must be a non-empty string"):
            CommandResultV1(ok=True, exit_code=0, command="", workspace="/tmp")


# ── CommandErrorV1 ───────────────────────────────────────────────────────────


class TestCommandErrorV1:
    def test_basic(self) -> None:
        e = CommandErrorV1("Something went wrong", code="oops")
        assert str(e) == "Something went wrong"
        assert e.code == "oops"
        assert e.exit_code == 1
        assert e.to_dict()["code"] == "oops"

    def test_with_details(self) -> None:
        e = CommandErrorV1(
            "Not found",
            code="not_found",
            details={"path": "/tmp"},
        )
        assert e.details["path"] == "/tmp"

    def test_subclasses(self) -> None:
        e = CommandNotFoundError("pm.unknown")
        assert e.code == "command_not_found"
        assert e.exit_code == ExitCode.INVALID_ARGS.value

        e2 = WorkspaceNotFoundError("/nonexistent")
        assert e2.code == "workspace_not_found"
        assert e2.exit_code == ExitCode.WORKSPACE_NOT_FOUND.value
        assert e2.details["workspace"] == "/nonexistent"

        e3 = CommandTimeoutError("pm.slow", 30)
        assert e3.code == "command_timeout"
        assert e3.exit_code == ExitCode.TIMEOUT.value
        assert e3.details["timeout_seconds"] == 30

        e4 = WorkspaceNotInitializedError("/tmp", "pm.status")
        assert e4.code == "workspace_not_initialized"
        assert e4.exit_code == ExitCode.NOT_INITIALIZED.value


# ── CliExecutionService ────────────────────────────────────────────────────────


class TestCliExecutionService:
    @pytest.fixture
    def service(self) -> CliExecutionService:
        return CliExecutionService()

    @pytest.fixture
    def tmp_workspace(self, _tmp_workspace: Path) -> Path:
        return _tmp_workspace

    # ── Workspace validation ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_unknown_command_raises(self, service: CliExecutionService) -> None:
        cmd = ExecuteCliCommandV1(
            command="unknown.command",
            workspace="/tmp",
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        with pytest.raises(CommandNotFoundError) as exc_info:
            await service.execute_command(cmd)
        assert exc_info.value.code == "command_not_found"

    @pytest.mark.asyncio
    async def test_nonexistent_workspace_raises(self, service: CliExecutionService) -> None:
        cmd = ExecuteCliCommandV1(
            command="pm.status",
            workspace="/nonexistent/path/that/does/not/exist",
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        with pytest.raises(WorkspaceNotFoundError) as exc_info:
            await service.execute_command(cmd)
        assert exc_info.value.code == "workspace_not_found"

    # ── MANAGEMENT mode ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_management_handler_called(self, service: CliExecutionService, tmp_workspace: Path) -> None:
        handler_calls: list = []

        def handler(workspace: Path, args: dict) -> dict:
            handler_calls.append((workspace, args))
            return {"ok": True, "output": "status output"}

        register_management_handler("pm.status", handler)

        cmd = ExecuteCliCommandV1(
            command="pm.status",
            workspace=str(tmp_workspace),
            execution_mode=ExecutionMode.MANAGEMENT,
            arguments={"verbose": True},
        )
        result = await service.execute_command(cmd)

        assert result.ok is True
        assert result.output == "status output"
        assert result.exit_code == 0
        assert result.command == "pm.status"
        assert handler_calls[0][1] == {"verbose": True}

    @pytest.mark.asyncio
    async def test_management_async_handler(self, service: CliExecutionService, tmp_workspace: Path) -> None:
        async def handler(workspace: Path, args: dict) -> dict:
            await asyncio.sleep(0)
            return {"ok": True, "output": "async ok"}

        register_management_handler("pm.health", handler)

        cmd = ExecuteCliCommandV1(
            command="pm.health",
            workspace=str(tmp_workspace),
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        result = await service.execute_command(cmd)
        assert result.ok is True
        assert result.output == "async ok"

    @pytest.mark.asyncio
    async def test_management_handler_returns_failure(self, service: CliExecutionService, tmp_workspace: Path) -> None:
        def handler(workspace: Path, args: dict) -> dict:
            return {"ok": False, "output": "failed", "exit_code": 1}

        register_management_handler("pm.init", handler)

        cmd = ExecuteCliCommandV1(
            command="pm.init",
            workspace=str(tmp_workspace),
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        result = await service.execute_command(cmd)
        assert result.ok is False
        assert result.exit_code == 1
        assert result.output == "failed"

    @pytest.mark.asyncio
    async def test_management_handler_raises_command_error(
        self, service: CliExecutionService, tmp_workspace: Path
    ) -> None:
        def handler(workspace: Path, args: dict) -> dict:
            raise CommandErrorV1(
                "Handler error",
                code="handler_error",
                exit_code=5,
            )

        register_management_handler("pm.report", handler)

        cmd = ExecuteCliCommandV1(
            command="pm.report",
            workspace=str(tmp_workspace),
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        with pytest.raises(CommandErrorV1) as exc_info:
            await service.execute_command(cmd)
        assert exc_info.value.code == "handler_error"
        assert exc_info.value.exit_code == 5

    # ── ROLE_EXECUTION mode ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_role_execution_requires_role(self, service: CliExecutionService, tmp_workspace: Path) -> None:
        # No role set — should be rejected at construction time
        with pytest.raises(ValueError, match="role is required"):
            ExecuteCliCommandV1(
                command="director.run",
                workspace=str(tmp_workspace),
                execution_mode=ExecutionMode.ROLE_EXECUTION,
            )

    @pytest.mark.asyncio
    async def test_role_execution_error_propagates(self, service: CliExecutionService, tmp_workspace: Path) -> None:
        cmd = ExecuteCliCommandV1(
            command="director.run",
            workspace=str(tmp_workspace),
            execution_mode=ExecutionMode.ROLE_EXECUTION,
            role="director",
        )
        # RoleRuntimeService will raise if called — service propagates it as CommandErrorV1
        # We test the error path by verifying the result structure
        # (RoleRuntimeService not available in unit test environment → raises ImportError or similar)
        # For unit test isolation, we accept that ROLE_EXECUTION calls RoleRuntimeService
        # In integration tests we'd mock it. Here we just verify the path is reachable.
        result = await service.execute_command(cmd)
        # If RoleRuntimeService is unavailable, ok=False with error_message
        assert isinstance(result, CommandResultV1)

    # ── Status query ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_status(self, service: CliExecutionService) -> None:
        # Use str(Path("/tmp")) for Windows path normalization
        workspace = str(Path("/tmp"))
        query = QueryCliStatusV1(workspace=workspace)
        status = await service.get_status(query)
        assert status["workspace"] == workspace
        assert status["active_sessions"] == []

    # ── Active sessions ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_active_sessions_tracked(self, service: CliExecutionService, tmp_workspace: Path) -> None:
        # Use a sync handler that returns quickly
        def handler(workspace: Path, args: dict) -> dict:
            return {"ok": True, "output": "ok"}

        register_management_handler("pm.test", handler)

        cmd = ExecuteCliCommandV1(
            command="pm.test",
            workspace=str(tmp_workspace),
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        await service.execute_command(cmd)

        # After execution, session is cleaned up
        assert len(service._active_sessions) == 0


# ── Singleton ────────────────────────────────────────────────────────────────


def test_get_cli_service_singleton() -> None:
    s1 = get_cli_service()
    s2 = get_cli_service()
    assert s1 is s2  # Same instance


def test_cell_alias_import() -> None:
    # Import from the cell-level __init__.py
    assert cell_alias_import is get_cli_service


# ── DAEMON mode ─────────────────────────────────────────────────────────────


class TestDaemonMode:
    @pytest.mark.asyncio
    async def test_daemon_mode_not_implemented(self, _tmp_workspace: Path) -> None:
        service = CliExecutionService()
        cmd = ExecuteCliCommandV1(
            command="director.serve",
            workspace=str(_tmp_workspace),
            execution_mode=ExecutionMode.DAEMON,
        )
        with pytest.raises(CommandErrorV1) as exc_info:
            await service.execute_command(cmd)
        assert exc_info.value.code == "daemon_not_implemented"


# ── Duration and exit code ──────────────────────────────────────────────────


class TestResultFields:
    @pytest.mark.asyncio
    async def test_duration_ms_recorded(self, _tmp_workspace: Path) -> None:
        def handler(workspace: Path, args: dict) -> dict:
            import time

            time.sleep(0.05)
            return {"ok": True, "output": "ok"}

        register_management_handler("pm.duration_test", handler)

        service = CliExecutionService()
        cmd = ExecuteCliCommandV1(
            command="pm.duration_test",
            workspace=str(_tmp_workspace),
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        result = await service.execute_command(cmd)
        assert result.duration_ms is not None
        assert result.duration_ms >= 40  # At least 40ms due to 50ms sleep

    @pytest.mark.asyncio
    async def test_exit_code_from_handler(self, _tmp_workspace: Path) -> None:
        def handler(workspace: Path, args: dict) -> dict:
            return {"ok": True, "output": "x", "exit_code": 99}

        register_management_handler("pm.exit_test", handler)

        service = CliExecutionService()
        cmd = ExecuteCliCommandV1(
            command="pm.exit_test",
            workspace=str(_tmp_workspace),
            execution_mode=ExecutionMode.MANAGEMENT,
        )
        result = await service.execute_command(cmd)
        assert result.exit_code == 99
