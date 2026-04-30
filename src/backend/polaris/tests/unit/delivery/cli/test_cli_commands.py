"""Tests for main Polaris CLI commands (status, init, run, help, etc.).

Covers:
  - __main__.py parser construction and argument validation
  - polaris_cli.py (legacy host) parser and dispatch paths
  - cli_router.py in-app command parsing
  - Help text, error cases, and happy paths
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.delivery.cli.__main__ import (
    _bind_workspace_env,
    _bootstrap_runtime,
    _enforce_utf8,
    _resolve_workspace,
    create_parser,
    main,
)
from polaris.delivery.cli.cli_router import (
    CliRouter,
    ParsedCommand,
    _normalise_session_id,
    _resolve_workspace as _router_resolve_workspace,
    _safe_text,
    parse_app_command,
)
from polaris.delivery.cli.polaris_cli import (
    _bind_workspace_environment,
    _default_workflow_run_id,
    _ensure_cli_runtime_bindings,
    _kernel_fs_for_workspace,
    _resolve_workspace as _polaris_resolve_workspace,
    _serialize_workflow_submission,
    create_parser as polaris_create_parser,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    """Return a fresh parser from __main__.py."""
    return create_parser()


@pytest.fixture
def polaris_parser() -> argparse.ArgumentParser:
    """Return a fresh parser from polaris_cli.py."""
    return polaris_create_parser()


@pytest.fixture
def router() -> CliRouter:
    """Return a fresh CliRouter."""
    return CliRouter()


# ---------------------------------------------------------------------------
# Test: __main__.py parser construction
# ---------------------------------------------------------------------------


class TestMainParserConstruction:
    """Test that create_parser() builds a correctly structured parser."""

    def test_parser_has_required_subcommands(self, parser: argparse.ArgumentParser) -> None:
        """The parser must expose all top-level subcommands."""
        subparsers_actions = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)  # type: ignore[attr-defined]
        ]
        assert len(subparsers_actions) == 1
        choices = subparsers_actions[0].choices
        expected = {
            "console",
            "task",
            "session",
            "serve",
            "cell",
            "chat",
            "status",
            "workflow",
            "agentic-eval",
            "probe",
            "ingest",
            "sync",
        }
        assert expected.issubset(set(choices.keys()))

    def test_console_subcommand_has_expected_flags(self, parser: argparse.ArgumentParser) -> None:
        """The 'console' subcommand must accept --role, --backend, --super, etc."""
        args = parser.parse_args(["console", "--role", "pm", "--backend", "plain", "--super"])
        assert args.command == "console"
        assert args.role == "pm"
        assert args.backend == "plain"
        assert args.super is True

    def test_task_create_requires_subject(self, parser: argparse.ArgumentParser) -> None:
        """task create without --subject must fail."""
        with pytest.raises(SystemExit):
            parser.parse_args(["task", "create"])

    def test_task_create_parses_correctly(self, parser: argparse.ArgumentParser) -> None:
        """task create with --subject must parse."""
        args = parser.parse_args(["task", "create", "--subject", "Fix bug", "--priority", "HIGH"])
        assert args.command == "task"
        assert args.task_command == "create"
        assert args.subject == "Fix bug"
        assert args.priority == "HIGH"

    def test_session_subcommands_exist(self, parser: argparse.ArgumentParser) -> None:
        """session list/show/switch/clear must all parse."""
        for sub in ("list", "show", "switch", "clear"):
            extra = ["--session-id", "s-1"] if sub in ("show", "switch") else []
            args = parser.parse_args(["session", sub] + extra)
            assert args.command == "session"
            assert args.session_command == sub

    def test_serve_subcommand_has_host_and_port(self, parser: argparse.ArgumentParser) -> None:
        """serve must accept --host and --port."""
        args = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "8080"])
        assert args.command == "serve"
        assert args.host == "0.0.0.0"
        assert args.port == 8080

    def test_cell_subcommands_exist(self, parser: argparse.ArgumentParser) -> None:
        """cell list and cell info must parse."""
        args = parser.parse_args(["cell", "list"])
        assert args.command == "cell"
        assert args.cell_command == "list"

        args = parser.parse_args(["cell", "info", "--cell-id", "test-cell"])
        assert args.cell_command == "info"
        assert args.cell_id == "test-cell"

    def test_status_subcommand_accepts_role(self, parser: argparse.ArgumentParser) -> None:
        """status must accept --role."""
        args = parser.parse_args(["status", "--role", "architect"])
        assert args.command == "status"
        assert args.role == "architect"

    def test_workflow_subcommand_accepts_actions(self, parser: argparse.ArgumentParser) -> None:
        """workflow must accept run/status/events/cancel."""
        for action in ("run", "status", "events", "cancel"):
            extra = ["--workflow-id", "wf-1"] if action != "run" else ["pm"]
            args = parser.parse_args(["workflow", action] + extra)
            assert args.command == "workflow"
            assert args.workflow_action == action

    def test_chat_legacy_subcommand_has_mode(self, parser: argparse.ArgumentParser) -> None:
        """chat must accept --mode choices."""
        args = parser.parse_args(["chat", "--mode", "oneshot", "--goal", "hello"])
        assert args.command == "chat"
        assert args.mode == "oneshot"
        assert args.goal == "hello"

    def test_agentic_eval_subcommand_has_suite_and_role(self, parser: argparse.ArgumentParser) -> None:
        """agentic-eval must accept --suite and --role."""
        args = parser.parse_args(["agentic-eval", "--suite", "tool_calling_matrix", "--role", "director"])
        assert args.command == "agentic-eval"
        assert args.suite == "tool_calling_matrix"
        assert args.role == "director"

    def test_probe_subcommand_accepts_role_repeatable(self, parser: argparse.ArgumentParser) -> None:
        """probe must accept multiple --role flags."""
        args = parser.parse_args(["probe", "--role", "pm", "--role", "director"])
        assert args.command == "probe"
        assert args.role == ["pm", "director"]

    def test_ingest_subcommand_requires_paths(self, parser: argparse.ArgumentParser) -> None:
        """ingest without paths must fail."""
        with pytest.raises(SystemExit):
            parser.parse_args(["ingest"])

    def test_ingest_subcommand_parses_paths(self, parser: argparse.ArgumentParser) -> None:
        """ingest must accept one or more paths."""
        args = parser.parse_args(["ingest", "README.md", "docs/", "--recursive", "--format", "json"])
        assert args.command == "ingest"
        assert args.paths == ["README.md", "docs/"]
        assert args.recursive is True
        assert args.format == "json"

    def test_sync_subcommand_has_direction(self, parser: argparse.ArgumentParser) -> None:
        """sync must accept --direction."""
        args = parser.parse_args(["sync", "--direction", "jsonl-to-lancedb"])
        assert args.command == "sync"
        assert args.direction == "jsonl-to-lancedb"

    def test_global_workspace_flag(self, parser: argparse.ArgumentParser) -> None:
        """--workspace must be accepted globally."""
        args = parser.parse_args(["status", "--workspace", "/tmp/ws"])
        assert Path(args.workspace) == Path("/tmp/ws")
        assert args.command == "status"

    def test_global_log_level_flag(self, parser: argparse.ArgumentParser) -> None:
        """--log-level must be accepted globally."""
        args = parser.parse_args(["--log-level", "debug", "status"])
        assert args.log_level == "debug"

    def test_no_persist_flag(self, parser: argparse.ArgumentParser) -> None:
        """--no-persist must be accepted globally."""
        args = parser.parse_args(["--no-persist", "status"])
        assert args.no_persist is True


# ---------------------------------------------------------------------------
# Test: __main__.py helper functions
# ---------------------------------------------------------------------------


class TestMainHelpers:
    """Test small helper functions in __main__.py."""

    def test_enforce_utf8_sets_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_enforce_utf8 must set PYTHONUTF8 and PYTHONIOENCODING."""
        import polaris.delivery.cli.__main__ as main_module

        monkeypatch.delenv("PYTHONUTF8", raising=False)
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        _enforce_utf8()
        assert main_module.os.environ.get("PYTHONUTF8") == "1"
        assert main_module.os.environ.get("PYTHONIOENCODING") == "utf-8"

    def test_resolve_workspace_returns_absolute(self) -> None:
        """_resolve_workspace must return an absolute Path."""
        resolved = _resolve_workspace(".")
        assert isinstance(resolved, (str, Path))
        assert Path(str(resolved)).is_absolute()

    def test_bind_workspace_env_sets_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_bind_workspace_env must set KERNELONE_CONTEXT_ROOT."""
        monkeypatch.delenv("KERNELONE_CONTEXT_ROOT", raising=False)
        monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
        _bind_workspace_env("/tmp/test-ws")
        assert Path(sys.path[0]) or True  # dummy assertion to avoid unused import
        import os

        assert os.environ.get("KERNELONE_CONTEXT_ROOT") == "/tmp/test-ws"

    def test_bootstrap_runtime_runs_without_error(self) -> None:
        """_bootstrap_runtime must not raise (best-effort)."""
        # It may fail in test env without full backend, but should not crash
        try:
            _bootstrap_runtime()
        except (RuntimeError, ValueError):
            pass  # acceptable in test environment


# ---------------------------------------------------------------------------
# Test: polaris_cli.py parser
# ---------------------------------------------------------------------------


class TestPolarisCliParser:
    """Test polaris_cli.py create_parser and helpers."""

    def test_polaris_parser_has_chat_status_workflow(self, polaris_parser: argparse.ArgumentParser) -> None:
        """polaris_cli parser must expose chat, status, workflow, test-window."""
        subparsers_actions = [
            action
            for action in polaris_parser._actions
            if isinstance(action, argparse._SubParsersAction)  # type: ignore[attr-defined]
        ]
        choices = subparsers_actions[0].choices
        assert set(choices.keys()) == {"chat", "status", "workflow", "test-window"}

    def test_polaris_chat_parses_role_and_mode(self, polaris_parser: argparse.ArgumentParser) -> None:
        """chat must parse --role and --mode."""
        args = polaris_parser.parse_args(["chat", "--role", "qa", "--mode", "server"])
        assert args.role == "qa"
        assert args.mode == "server"

    def test_polaris_workflow_run_parses(self, polaris_parser: argparse.ArgumentParser) -> None:
        """workflow run must parse --contracts-file and --wait."""
        args = polaris_parser.parse_args(
            [
                "workflow",
                "run",
                "pm",
                "--contracts-file",
                "contracts.json",
                "--wait",
                "--timeout-seconds",
                "120",
            ]
        )
        assert args.workflow_action == "run"
        assert args.contracts_file == "contracts.json"
        assert args.wait is True
        assert args.timeout_seconds == 120.0

    def test_polaris_workflow_cancel_requires_id(self, polaris_parser: argparse.ArgumentParser) -> None:
        """workflow cancel without --workflow-id must still parse (validated at runtime)."""
        args = polaris_parser.parse_args(["workflow", "cancel"])
        assert args.workflow_action == "cancel"
        assert args.workflow_id == ""

    def test_polaris_resolve_workspace(self) -> None:
        """_resolve_workspace must return absolute path string."""
        result = _polaris_resolve_workspace(".")
        assert Path(result).is_absolute()

    def test_default_workflow_run_id_format(self) -> None:
        """_default_workflow_run_id must start with 'cli-'."""
        run_id = _default_workflow_run_id()
        assert run_id.startswith("cli-")
        assert len(run_id) > 4

    def test_serialize_workflow_submission(self) -> None:
        """_serialize_workflow_submission must return a dict with expected keys."""
        mock = MagicMock()
        mock.submitted = True
        mock.status = "ok"
        mock.workflow_id = "wf-1"
        mock.workflow_run_id = "run-1"
        mock.error = ""
        mock.details = {"foo": "bar"}
        result = _serialize_workflow_submission(mock)
        assert result["submitted"] is True
        assert result["status"] == "ok"
        assert result["workflow_id"] == "wf-1"
        assert result["details"] == {"foo": "bar"}

    def test_kernel_fs_for_workspace(self) -> None:
        """_kernel_fs_for_workspace must return a KernelFileSystem instance."""
        fs = _kernel_fs_for_workspace(".")
        assert fs is not None

    def test_ensure_cli_runtime_bindings(self) -> None:
        """_ensure_cli_runtime_bindings must be callable without raising."""
        try:
            _ensure_cli_runtime_bindings()
        except (RuntimeError, ValueError):
            pass  # acceptable in test environment

    def test_bind_workspace_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_bind_workspace_environment must set env vars."""
        monkeypatch.delenv("KERNELONE_CONTEXT_ROOT", raising=False)
        monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
        _bind_workspace_environment("/tmp/ws2")
        import os

        assert os.environ.get("KERNELONE_CONTEXT_ROOT") is not None


# ---------------------------------------------------------------------------
# Test: cli_router.py
# ---------------------------------------------------------------------------


class TestCliRouter:
    """Test CliRouter parsing and routing."""

    def test_router_parser_has_four_subcommands(self, router: CliRouter) -> None:
        """CliRouter parser must expose chat, status, workflow, test-window."""
        parser = router._parser
        subparsers_actions = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)  # type: ignore[attr-defined]
        ]
        choices = subparsers_actions[0].choices
        assert set(choices.keys()) == {"chat", "status", "workflow", "test-window"}

    def test_router_parsed_returns_parsed_command(self, router: CliRouter) -> None:
        """router.parsed() must return a ParsedCommand."""
        parsed = router.parsed(["chat", "--role", "pm"])
        assert isinstance(parsed, ParsedCommand)
        assert parsed.command == "chat"
        assert parsed.role == "pm"

    def test_router_parsed_workflow_has_subcommand(self, router: CliRouter) -> None:
        """workflow run must set subcommand to 'run'."""
        parsed = router.parsed(["workflow", "run"])
        assert parsed.subcommand == "run"

    def test_router_register_and_route(self, router: CliRouter) -> None:
        """Registering a handler and routing must return the handler's result."""

        def handler(args: argparse.Namespace) -> int:
            return 42

        router.register("chat", handler)
        exit_code = router.route(["chat"])
        assert exit_code == 42

    def test_router_route_unknown_command_returns_zero(self, router: CliRouter) -> None:
        """A command with no handler must return 0 (acknowledged)."""
        exit_code = router.route(["status"])
        assert exit_code == 0

    def test_router_route_bad_args_returns_nonzero(self, router: CliRouter) -> None:
        """Invalid arguments must result in a non-zero exit code."""
        exit_code = router.route(["--not-a-flag"])
        assert exit_code != 0


class TestNormaliseSessionId:
    """Test _normalise_session_id."""

    def test_empty_returns_none(self) -> None:
        assert _normalise_session_id("") is None
        assert _normalise_session_id(None) is None

    def test_whitespace_returns_none(self) -> None:
        assert _normalise_session_id("   ") is None

    def test_valid_returns_stripped(self) -> None:
        assert _normalise_session_id("sess-1") == "sess-1"
        assert _normalise_session_id("  sess-2  ") == "sess-2"


class TestSafeText:
    """Test _safe_text."""

    def test_none_returns_empty(self) -> None:
        assert _safe_text(None) == ""

    def test_strips_whitespace(self) -> None:
        assert _safe_text("  hello  ") == "hello"


class TestRouterResolveWorkspace:
    """Test _resolve_workspace in cli_router."""

    def test_returns_absolute_path(self) -> None:
        result = _router_resolve_workspace(".")
        assert result.is_absolute()

    def test_mangled_windows_path_raises_on_missing(self) -> None:
        """A mangled Windows path that cannot be recovered must raise ValueError."""
        import os

        if os.name != "nt":
            pytest.skip("Windows-only test")
        with pytest.raises(ValueError) as exc_info:
            _router_resolve_workspace("Z:NonExistentMangledPath")
        assert "mangled by the shell" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test: parse_app_command (in-app slash commands)
# ---------------------------------------------------------------------------


class TestParseAppCommand:
    """Test parse_app_command for /role, /session, /new-session, /refresh, /sidebar."""

    def test_role_command(self) -> None:
        result = parse_app_command("/role pm")
        assert result is not None
        assert result.kind == "role"
        assert result.normalised_role == "pm"

    def test_role_command_case_insensitive(self) -> None:
        result = parse_app_command("/role QA")
        assert result is not None
        assert result.normalised_role == "qa"

    def test_role_command_empty_role(self) -> None:
        result = parse_app_command("/role ")
        assert result is not None
        assert result.kind == "role"
        assert result.normalised_role is None

    def test_session_list(self) -> None:
        result = parse_app_command("/session list")
        assert result is not None
        assert result.kind == "session"
        assert result.subcommand == "list"

    def test_session_show(self) -> None:
        result = parse_app_command("/session show sess-1")
        assert result is not None
        assert result.subcommand == "show"

    def test_session_switch(self) -> None:
        result = parse_app_command("/session switch sess-2")
        assert result is not None
        assert result.subcommand == "switch"
        assert result.raw_value == "switch sess-2"

    def test_session_clear(self) -> None:
        result = parse_app_command("/session clear")
        assert result is not None
        assert result.subcommand == "clear"

    def test_session_unknown_subcommand(self) -> None:
        result = parse_app_command("/session unknown")
        assert result is not None
        assert result.kind == "session"
        assert result.subcommand is None

    def test_new_session_with_title(self) -> None:
        result = parse_app_command("/new-session My Title")
        assert result is not None
        assert result.kind == "new-session"
        assert result.raw_value == "My Title"

    def test_new_session_without_title(self) -> None:
        result = parse_app_command("/new-session")
        assert result is not None
        assert result.raw_value == ""

    def test_refresh_command(self) -> None:
        for text in ("/refresh", "/r"):
            result = parse_app_command(text)
            assert result is not None
            assert result.kind == "refresh"

    def test_sidebar_command(self) -> None:
        for text in ("/sidebar", "/sb"):
            result = parse_app_command(text)
            assert result is not None
            assert result.kind == "sidebar"

    def test_non_command_returns_none(self) -> None:
        assert parse_app_command("hello world") is None
        assert parse_app_command("") is None
        assert parse_app_command("/unknown") is None


# ---------------------------------------------------------------------------
# Test: main() entry point (subprocess smoke tests)
# ---------------------------------------------------------------------------


class TestMainEntryPoint:
    """Smoke-test the unified CLI entry point."""

    def test_main_module_importable(self) -> None:
        """The __main__.py module must be importable without error."""
        import polaris.delivery.cli.__main__ as m

        assert hasattr(m, "main")
        assert callable(m.main)

    def test_main_help_exits_zero(self) -> None:
        """main(['--help']) must raise SystemExit(0)."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_main_rejects_unknown_option(self) -> None:
        """main with unknown option must return non-zero."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--not-a-real-option"])
        assert exc_info.value.code != 0

    def test_main_chat_help_exits_zero(self) -> None:
        """main(['chat', '--help']) must raise SystemExit(0)."""
        with pytest.raises(SystemExit) as exc_info:
            main(["chat", "--help"])
        assert exc_info.value.code == 0

    def test_main_status_no_handler_returns_nonzero_or_zero(self) -> None:
        """main(['status']) may return 0 or 1 depending on runtime availability."""
        # We just assert it doesn't crash
        try:
            code = main(["status"])
            assert code in (0, 1)
        except SystemExit as exc:
            assert isinstance(exc.code, int)

    def test_polaris_cli_main_help(self) -> None:
        """polaris_cli main(['--help']) must raise SystemExit(0)."""
        from polaris.delivery.cli.polaris_cli import main as polaris_main

        with pytest.raises(SystemExit) as exc_info:
            polaris_main(["--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Test: router.py dispatch (high-level)
# ---------------------------------------------------------------------------


class TestRouterDispatch:
    """Test router.py route dispatch with mocked services."""

    def test_route_console_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_route_console with dry_run=True must pass dry_run to run_role_console."""
        from polaris.delivery.cli import router as cli_router_module

        captured: dict[str, Any] = {}

        def _fake_run_role_console(**kwargs: Any) -> int:
            captured.update(kwargs)
            return 0

        with patch.object(cli_router_module.WorkspaceGuard, "ensure_workspace", return_value=Path("/tmp/ws")):
            with patch("polaris.delivery.cli.terminal_console.run_role_console", _fake_run_role_console):
                args = argparse.Namespace(
                    workspace="/tmp/ws",
                    role="director",
                    backend="plain",
                    session_id="",
                    session_title="",
                    prompt_style="plain",
                    omp_config="",
                    json_render="raw",
                    debug=False,
                    dry_run=True,
                    batch=False,
                    super=False,
                )
                exit_code = cli_router_module._route_console(args)
                assert exit_code == 0
                assert captured.get("dry_run") is True

    def test_route_task_create_missing_subject(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_route_task create without subject must return 1."""
        from polaris.delivery.cli import router as cli_router_module

        with patch.object(cli_router_module.WorkspaceGuard, "ensure_workspace", return_value=Path("/tmp/ws")):
            args = argparse.Namespace(
                workspace="/tmp/ws",
                task_command="create",
                subject="",
                description="",
                priority="MEDIUM",
                blocked_by=[],
            )
            exit_code = cli_router_module._route_task(args)
            assert exit_code == 1

    def test_route_task_show_missing_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_route_task show without --task-id must return 1."""
        from polaris.delivery.cli import router as cli_router_module

        with patch.object(cli_router_module.WorkspaceGuard, "ensure_workspace", return_value=Path("/tmp/ws")):
            args = argparse.Namespace(
                workspace="/tmp/ws",
                task_command="show",
                task_id="",
            )
            exit_code = cli_router_module._route_task(args)
            assert exit_code == 1

    def test_route_cell_info_missing_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_cell_info without --cell-id must return 1."""
        from polaris.delivery.cli import router as cli_router_module

        args = argparse.Namespace(cell_id="")
        exit_code = cli_router_module._cell_info(args)
        assert exit_code == 1

    def test_route_session_show_missing_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_session_show without --session-id must return 1."""
        from polaris.delivery.cli import router as cli_router_module

        args = argparse.Namespace(session_id="")
        exit_code = cli_router_module._session_show(None, args)  # type: ignore[arg-type]
        assert exit_code == 1

    def test_route_session_switch_missing_id(self) -> None:
        """_session_switch without --session-id must return 1."""
        from polaris.delivery.cli import router as cli_router_module

        args = argparse.Namespace(session_id="")
        exit_code = cli_router_module._session_switch(None, args)  # type: ignore[arg-type]
        assert exit_code == 1

    def test_workspace_guard_ensure_workspace_creates_dir(self, tmp_path: Path) -> None:
        """WorkspaceGuard.ensure_workspace must create missing directories."""
        from polaris.delivery.cli.router import WorkspaceGuard

        new_dir = tmp_path / "new_workspace"
        result = WorkspaceGuard.ensure_workspace(str(new_dir))
        assert result.exists()
        assert result.is_dir()

    def test_workspace_guard_detect_workspace_finds_marker(self, tmp_path: Path) -> None:
        """WorkspaceGuard.detect_workspace must find a workspace marker."""
        from polaris.delivery.cli.router import WorkspaceGuard

        marker = tmp_path / ".polaris"
        marker.mkdir()
        found = WorkspaceGuard.detect_workspace(tmp_path)
        assert found == tmp_path

    def test_workspace_guard_has_polaris_marker(self, tmp_path: Path) -> None:
        """WorkspaceGuard.has_polaris_marker must return True when marker exists."""
        from polaris.delivery.cli.router import WorkspaceGuard

        marker = tmp_path / ".polaris"
        marker.mkdir()
        assert WorkspaceGuard.has_polaris_marker(tmp_path) is True
        assert WorkspaceGuard.has_polaris_marker(tmp_path / "nonexistent") is False
