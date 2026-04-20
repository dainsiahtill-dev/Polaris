"""Integration tests for Polaris CLI Router (X1-⑤).

Tests cover:
  - __main__.py subparsers correctness (unified CLI host)
  - CliRouter.route() dispatch
  - cli_compat.py deprecation warnings
  - /role <name> in-app command parsing
  - session list/show/switch/clear in-app command parsing

Squad: Cross-cutting (verifier agent)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from polaris.delivery.cli.cli_compat import (
    _LEGACY_ENTRY_POINTS,
    check_compat,
    emit_compat_warnings,
    warn_if_no_workspace,
    warn_if_old_runtime_mode,
)
from polaris.delivery.cli.cli_router import (
    CliRouter,
    ParsedCommand,
    _resolve_workspace,
    parse_app_command,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def router() -> CliRouter:
    """Return a fresh CliRouter for each test."""
    return CliRouter()


# ---------------------------------------------------------------------------
# Test: CliRouter subparser construction
# ---------------------------------------------------------------------------


class TestCliRouterSubparsers:
    """Test that CliRouter creates a correctly structured ArgumentParser."""

    def test_parser_has_four_subcommands(self, router: CliRouter) -> None:
        """CliRouter parser must expose all four top-level subcommands."""
        parser = router._parser
        subparsers_actions = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)  # type: ignore[attr-defined]
        ]
        assert len(subparsers_actions) == 1, "Expected exactly one subparsers action"
        choices = subparsers_actions[0].choices
        assert set(choices.keys()) == {"chat", "status", "workflow", "test-window"}

    def test_chat_subcommand_has_expected_arguments(self, router: CliRouter) -> None:
        """The 'chat' subcommand must expose role, mode, session-id, session-title."""
        parsed = router.parsed(["chat", "--role", "pm", "--mode", "interactive"])
        assert parsed.command == "chat"
        assert parsed.role == "pm"
        assert parsed.mode == "interactive"

    def test_status_subcommand_accepted(self, router: CliRouter) -> None:
        """The 'status' subcommand must parse without error."""
        parsed = router.parsed(["status", "--role", "architect"])
        assert parsed.command == "status"
        assert parsed.role == "architect"

    def test_workflow_subcommand_has_expected_arguments(self, router: CliRouter) -> None:
        """The 'workflow' subcommand must accept run/status/events/cancel actions."""
        for action in ("run", "status", "events", "cancel"):
            parsed = router.parsed(["workflow", action, "--workflow-id", "wf-123"])
            assert parsed.command == "workflow"
            assert parsed.subcommand == action

    def test_test_window_subcommand_accepted(self, router: CliRouter) -> None:
        """The 'test-window' subcommand must parse."""
        parsed = router.parsed(["test-window", "--role", "qa", "--surface", "tui"])
        assert parsed.command == "test-window"
        assert parsed.role == "qa"

    def test_role_defaults_to_director(self, router: CliRouter) -> None:
        """When --role is omitted, the parsed role must default to 'director'."""
        parsed = router.parsed(["chat"])
        assert parsed.role == "director"

    def test_mode_defaults_to_interactive(self, router: CliRouter) -> None:
        """When --mode is omitted, the parsed mode must default to 'interactive'."""
        parsed = router.parsed(["chat"])
        assert parsed.mode == "interactive"

    def test_workspace_defaults_to_dot(self, router: CliRouter) -> None:
        """When --workspace is omitted, it must default to the current directory."""
        parsed = router.parsed(["chat"])
        assert parsed.workspace == str(Path.cwd().resolve())

    def test_log_level_flag_is_accepted(self, router: CliRouter) -> None:
        """Global --log-level flag must be accepted by parser."""
        parsed = router.parsed(["--log-level", "warn", "chat"])
        assert parsed.command == "chat"
        assert parsed.raw_args.log_level == "warn"

    def test_log_level_flag_is_accepted_after_subcommand(self, router: CliRouter) -> None:
        """--log-level should also be accepted after the subcommand token."""
        parsed = router.parsed(["chat", "--log-level", "error"])
        assert parsed.command == "chat"
        assert parsed.raw_args.log_level == "error"

    def test_workspace_resolved_absolute(self, router: CliRouter) -> None:
        """An explicit --workspace must be resolved to an absolute path.

        Note: --workspace is a top-level argument and must appear BEFORE the
        subcommand name, matching argparse conventions.
        """
        parsed = router.parsed(["--workspace", "/tmp/workspace", "chat"])
        assert Path(parsed.workspace).is_absolute()

    def test_resolve_workspace_recovers_mangled_windows_path(self, router: CliRouter) -> None:
        """_resolve_workspace should recover paths mangled by MSYS bash backslash stripping."""
        import os

        # This test only applies on Windows; on POSIX we assert the normal path resolution.
        resolved = _resolve_workspace(".")
        assert resolved.is_absolute()

        if os.name != "nt":
            # On non-Windows, just ensure it resolves normally.
            assert _resolve_workspace("/tmp").is_absolute()
            return

        # On Windows, a mangled path like C:TempFileServer should raise a clear error
        # when the recovered variants do not exist.
        with pytest.raises(ValueError) as exc_info:
            _resolve_workspace("Z:NonExistentMangledPath")
        assert "mangled by the shell" in str(exc_info.value)
        assert "Use forward slashes" in str(exc_info.value)

    def test_session_id_normalised(self, router: CliRouter) -> None:
        """A non-empty --session-id must be preserved; empty must become None."""
        parsed = router.parsed(["chat", "--session-id", "sess-abc"])
        assert parsed.session_id == "sess-abc"

        parsed_empty = router.parsed(["chat", "--session-id", ""])
        assert parsed_empty.session_id is None

        parsed_missing = router.parsed(["chat"])
        assert parsed_missing.session_id is None

    def test_unknown_subcommand_exits_non_zero(self, router: CliRouter) -> None:
        """An unknown subcommand must result in a non-zero exit code from route()."""
        exit_code = router.route(["notasubcommand"])
        assert exit_code != 0

    def test_chat_oneshot_mode_requires_goal(self, router: CliRouter) -> None:
        """The 'chat oneshot' mode argument --goal must be accepted."""
        parsed = router.parsed(["chat", "--mode", "oneshot", "--goal", "say hello"])
        assert parsed.mode == "oneshot"
        assert parsed.raw_args.goal == "say hello"

    def test_workflow_wait_and_timeout_flags(self, router: CliRouter) -> None:
        """workflow run --wait and --timeout-seconds must be parsed."""
        parsed = router.parsed(
            [
                "workflow",
                "run",
                "pm",
                "--wait",
                "--timeout-seconds",
                "60.0",
                "--max-parallel-tasks",
                "5",
            ]
        )
        assert parsed.raw_args.wait is True
        assert parsed.raw_args.timeout_seconds == 60.0
        assert parsed.raw_args.max_parallel_tasks == 5


# ---------------------------------------------------------------------------
# Test: CliRouter.route() dispatch
# ---------------------------------------------------------------------------


class TestCliRouterDispatch:
    """Test CliRouter.route() exit-code handling."""

    def test_route_returns_zero_for_registered_noop_handler(self, router: CliRouter) -> None:
        """A registered handler returning 0 must produce exit code 0."""

        def noop(_: argparse.Namespace) -> int:
            return 0

        router.register("chat", noop)
        exit_code = router.route(["chat"])
        assert exit_code == 0

    def test_route_returns_handler_error_code(self, router: CliRouter) -> None:
        """A registered handler returning a non-zero int must propagate it."""

        def fail(_: argparse.Namespace) -> int:
            return 42

        router.register("chat", fail)
        exit_code = router.route(["chat"])
        assert exit_code == 42

    def test_route_returns_zero_when_command_has_no_handler(self, router: CliRouter) -> None:
        """A command with no registered handler must return 0 (acknowledged)."""
        # status is registered by argparse but not registered as a handler
        exit_code = router.route(["status"])
        # No handler -> acknowledged (exit 0), not treated as error
        assert exit_code == 0

    def test_route_parses_and_normalises_role(self, router: CliRouter) -> None:
        """route() must normalise role to lowercase."""
        captured: dict = {}

        def capture(args: argparse.Namespace) -> int:
            captured["role"] = str(getattr(args, "role", "")).strip().lower()
            return 0

        router.register("chat", capture)
        router.route(["chat", "--role", "PM"])
        assert captured["role"] == "pm"

    def test_route_parses_session_id(self, router: CliRouter) -> None:
        """route() must expose session_id in parsed result."""
        captured: dict = {}

        def capture(args: argparse.Namespace) -> int:
            captured["session_id"] = args.session_id
            return 0

        router.register("chat", capture)
        router.route(["chat", "--session-id", "my-session-99"])
        assert captured["session_id"] == "my-session-99"


# ---------------------------------------------------------------------------
# Test: cli_compat.py warnings
# ---------------------------------------------------------------------------


class TestCliCompat:
    """Test that cli_compat emits the correct deprecation warnings."""

    def test_legacy_entry_point_warning(self) -> None:
        """DeprecationWarning must be raised for each legacy entry point."""
        for ep in _LEGACY_ENTRY_POINTS:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                emit_compat_warnings([ep])
                assert len(w) == 1, f"Expected 1 warning for '{ep}', got {len(w)}"
                assert issubclass(w[0].category, DeprecationWarning)
                assert ep in str(w[0].message)

    def test_modern_entry_point_no_warning(self) -> None:
        """No warning must be raised for 'polaris-cli' or 'polaris-lazy'."""
        for ep in ("polaris-cli", "polaris-lazy", "python", "/path/to/script.py"):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                emit_compat_warnings([ep])
                assert len(w) == 0, f"Unexpected warning for '{ep}': {w}"

    def test_warn_if_old_runtime_mode(self) -> None:
        """DeprecationWarning must be raised for deprecated runtime modes."""
        for mode in ("rich", "textual", "server"):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                warn_if_old_runtime_mode(mode)
                assert len(w) == 1, f"Expected 1 warning for mode '{mode}'"
                assert issubclass(w[0].category, DeprecationWarning)

    def test_modern_mode_no_warning(self) -> None:
        """No warning must be raised for 'interactive' or 'console'."""
        for mode in ("interactive", "console", "oneshot"):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                warn_if_old_runtime_mode(mode)
                assert len(w) == 0, f"Unexpected warning for mode '{mode}'"

    def test_warn_if_no_workspace_emits(self) -> None:
        """UserWarning must be raised when workspace is absent or empty."""
        for ws in ("", None):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                warn_if_no_workspace(ws)  # type: ignore[arg-type]
                assert len(w) == 1, f"Expected 1 warning for workspace={ws!r}"
                assert issubclass(w[0].category, UserWarning)

    def test_explicit_workspace_no_warning(self) -> None:
        """No warning must be raised when a workspace is explicitly specified."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_no_workspace("/some/workspace")
            assert len(w) == 0

    def test_check_compat_top_level(self) -> None:
        """check_compat() must not raise for a modern entry point."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            check_compat(["polaris-lazy", "chat"])
            assert len(w) == 0

    def test_check_compat_legacy_entry_point(self) -> None:
        """check_compat() must emit DeprecationWarning for legacy entry points."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            check_compat(["polaris-director", "chat"])
            assert len(w) >= 1
            categories = {warning.category for warning in w}
            assert DeprecationWarning in categories


# ---------------------------------------------------------------------------
# Test: /role <name> command parsing
# ---------------------------------------------------------------------------


class TestRoleCommandParsing:
    """Test parse_app_command for the /role command."""

    def test_role_command_parsed(self) -> None:
        """parse_app_command must return AppCommand(kind='role') for /role input."""
        result = parse_app_command("/role pm")
        assert result is not None
        assert result.kind == "role"
        assert result.normalised_role == "pm"

    def test_role_command_case_insensitive(self) -> None:
        """Role names must be normalised to lowercase."""
        result = parse_app_command("/role QA")
        assert result is not None
        assert result.normalised_role == "qa"

    def test_role_command_strips_whitespace(self) -> None:
        """Leading/trailing whitespace in role name must be stripped."""
        result = parse_app_command("/role  director  ")
        assert result is not None
        assert result.normalised_role == "director"

    def test_role_command_with_space_prefix(self) -> None:
        """A command prefixed with whitespace must still be recognised."""
        result = parse_app_command("  /role architect")
        assert result is not None
        assert result.kind == "role"
        assert result.normalised_role == "architect"

    def test_role_command_empty_role_returns_none(self) -> None:
        """Subcommand /role with no role name must still return an AppCommand (not None).

        The normalised_role is None but kind is still 'role' so that downstream
        code can emit a meaningful \"role name required\" message rather than silently
        ignoring the input.
        """
        result = parse_app_command("/role ")
        assert result is not None, "/role with only whitespace should not return None"
        assert result.kind == "role"
        assert result.normalised_role is None

    def test_role_command_unknown_role_preserved(self) -> None:
        """An unknown role name must still be parsed (validation is downstream)."""
        result = parse_app_command("/role nobody")
        assert result is not None
        assert result.kind == "role"
        assert result.normalised_role == "nobody"

    def test_role_command_all_five_roles(self) -> None:
        """All five canonical roles must be parsed correctly."""
        for role in ("director", "pm", "architect", "chief_engineer", "qa"):
            result = parse_app_command(f"/role {role}")
            assert result is not None, f"Failed to parse /role {role}"
            assert result.kind == "role"
            assert result.normalised_role == role


# ---------------------------------------------------------------------------
# Test: session list/show/switch/clear parsing
# ---------------------------------------------------------------------------


class TestSessionCommandParsing:
    """Test parse_app_command for /session subcommands."""

    @staticmethod
    def _assert_session_subcommand(text: str, expected_sub: str) -> None:
        result = parse_app_command(text)
        assert result is not None, f"Failed to parse: {text!r}"
        assert result.kind == "session", f"Expected kind='session' for {text!r}"
        assert result.subcommand == expected_sub, (
            f"For {text!r}: expected subcommand={expected_sub!r}, got {result.subcommand!r}"
        )

    def test_session_list(self) -> None:
        """parse_app_command must recognise /session list."""
        self._assert_session_subcommand("/session list", "list")

    def test_session_show(self) -> None:
        """parse_app_command must recognise /session show."""
        self._assert_session_subcommand("/session show", "show")

    def test_session_switch(self) -> None:
        """parse_app_command must recognise /session switch."""
        self._assert_session_subcommand("/session switch sess-42", "switch")
        result = parse_app_command("/session switch my-session")
        assert result is not None
        assert result.raw_value == "switch my-session"

    def test_session_clear(self) -> None:
        """parse_app_command must recognise /session clear."""
        self._assert_session_subcommand("/session clear", "clear")

    def test_session_case_insensitive(self) -> None:
        """Session subcommand names must be case-insensitive."""
        self._assert_session_subcommand("/session LIST", "list")
        self._assert_session_subcommand("/session Clear", "clear")

    def test_session_unknown_subcommand(self) -> None:
        """An unknown session subcommand must return subcommand=None (not raise)."""
        result = parse_app_command("/session unknown-cmd")
        assert result is not None
        assert result.kind == "session"
        assert result.subcommand is None

    def test_session_empty_subcommand(self) -> None:
        """/session with no subcommand must return subcommand=None."""
        result = parse_app_command("/session")
        assert result is not None
        assert result.kind == "session"
        assert result.subcommand is None


# ---------------------------------------------------------------------------
# Test: other in-app commands
# ---------------------------------------------------------------------------


class TestOtherAppCommands:
    """Test parse_app_command for /refresh, /new-session, /sidebar."""

    def test_refresh_command(self) -> None:
        """/refresh and /r must be recognised."""
        for text in ("/refresh", "/r"):
            result = parse_app_command(text)
            assert result is not None, f"Failed to parse {text!r}"
            assert result.kind == "refresh"
            assert result.raw_value == ""

    def test_new_session_command(self) -> None:
        """/new-session must be recognised with optional title."""
        result = parse_app_command("/new-session My Title Here")
        assert result is not None
        assert result.kind == "new-session"
        assert result.raw_value == "My Title Here"

        result_no_title = parse_app_command("/new-session")
        assert result_no_title is not None
        assert result_no_title.kind == "new-session"
        assert result_no_title.raw_value == ""

    def test_sidebar_command(self) -> None:
        """/sidebar and /sb must be recognised."""
        for text in ("/sidebar", "/sb"):
            result = parse_app_command(text)
            assert result is not None, f"Failed to parse {text!r}"
            assert result.kind == "sidebar"

    def test_non_command_returns_none(self) -> None:
        """Plain text not starting with '/' must return None."""
        assert parse_app_command("hello world") is None
        assert parse_app_command("") is None
        assert parse_app_command("/only-slash") is None  # unknown command


# ---------------------------------------------------------------------------
# Test: __main__.py entry point (subprocess smoke test)
# ---------------------------------------------------------------------------


class TestMainEntryPoint:
    """Smoke-test the unified CLI ``python -m polaris.delivery.cli`` entry point."""

    def test_main_module_importable(self) -> None:
        """The __main__.py module must be importable without error."""
        import polaris.delivery.cli.__main__ as m

        assert hasattr(m, "main")
        assert callable(m.main)

    def test_main_shows_help(self) -> None:
        """python -m polaris.delivery.cli --help must exit with code 0."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "polaris.delivery.cli",
                "--help",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "Polaris unified CLI" in result.stdout or "commands" in result.stdout

    def test_main_rejects_unknown_option(self) -> None:
        """Unified CLI with an unknown option must exit with a non-zero code."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "polaris.delivery.cli",
                "--not-a-real-option",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert result.returncode != 0

    def test_main_chat_help_accepts_role_option(self) -> None:
        """`chat --help` must expose the role option in unified CLI."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "polaris.delivery.cli",
                "chat",
                "--help",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "--role" in result.stdout

    def test_main_enforce_utf8_sets_valid_pythonioencoding(self, monkeypatch) -> None:
        """_enforce_utf8 must set PYTHONIOENCODING to a valid codec name."""
        import polaris.delivery.cli.__main__ as main_module

        monkeypatch.delenv("PYTHONUTF8", raising=False)
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        main_module._enforce_utf8()

        assert str(main_module.os.environ.get("PYTHONUTF8") or "") == "1"
        assert str(main_module.os.environ.get("PYTHONIOENCODING") or "").lower() == "utf-8"


# ---------------------------------------------------------------------------
# Test: ParsedCommand NamedTuple structure
# ---------------------------------------------------------------------------


class TestParsedCommandNamedTuple:
    """Verify the fields and types of the ParsedCommand NamedTuple."""

    def test_parsed_command_fields(self, router: CliRouter) -> None:
        """ParsedCommand must expose all required fields.

        Note: --workspace is a global arg and must come before the subcommand.
        """
        # Use a relative path so it resolves correctly on both Unix and Windows.
        parsed = router.parsed(
            [
                "--workspace",
                ".",
                "chat",
                "--role",
                "architect",
                "--mode",
                "interactive",
                "--session-id",
                "sess-1",
            ]
        )
        assert isinstance(parsed, ParsedCommand)
        assert parsed.command == "chat"
        assert Path(parsed.workspace).is_absolute(), f"Expected absolute path, got: {parsed.workspace}"
        assert parsed.role == "architect"
        assert parsed.mode == "interactive"
        assert parsed.session_id == "sess-1"
        assert parsed.subcommand is None
        assert hasattr(parsed, "raw_args")

    def test_parsed_command_workflow_has_subcommand(self, router: CliRouter) -> None:
        """workflow run must set ParsedCommand.subcommand to 'run'."""
        parsed = router.parsed(["workflow", "run"])
        assert parsed.command == "workflow"
        assert parsed.subcommand == "run"
