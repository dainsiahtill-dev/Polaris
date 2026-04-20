"""Polaris CLI Router — central dispatch layer for Polaris CLI subcommands.

Architecture:
  - CliRouter.route() is the single dispatch surface for all CLI commands.
  - Commands are registered via subparsers (from create_parser()).
  - Session commands (/role, /session, /new-session) are parsed by the
    terminal console command parser, not by this router.
  - This module provides the argparse-level dispatch, not in-app render behavior.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from polaris.delivery.cli.cli_compat import emit_compat_warnings, warn_if_no_workspace
from polaris.delivery.cli.logging_policy import CLI_LOG_LEVEL_CHOICES, configure_cli_logging
from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS

logger: logging.Logger = logging.getLogger(__name__)


class ParsedCommand(NamedTuple):
    """Result of parsing and routing a CLI invocation."""

    command: str
    """Top-level subcommand name (e.g. 'chat', 'status', 'workflow')."""

    subcommand: str | None
    """Optional sub-subcommand (e.g. 'run', 'cancel' within 'workflow')."""

    workspace: str
    """Resolved workspace path."""

    raw_args: argparse.Namespace
    """The raw parsed argparse namespace."""

    role: str
    """Role argument, normalised (default: 'director')."""

    mode: str
    """Runtime mode (e.g. 'interactive', 'console')."""

    session_id: str | None
    """Session ID if specified."""


# Subcommand handlers signature: (args: argparse.Namespace) -> int | None
CommandHandler = Callable[[argparse.Namespace], int | None]


def _resolve_workspace(workspace: str) -> Path:
    r"""Resolve workspace path with defensive handling for mangled Windows paths.

    MSYS/Git Bash on Windows strips backslashes from unquoted paths such as
    ``C:\Temp\FileServer``, turning them into ``C:TempFileServer``. This
    function detects that pattern, attempts recovery by probing likely
    absolute-path variants, and raises a clear error when recovery fails.
    """
    original = workspace.strip()
    candidate = Path(original).resolve()

    if os.name == "nt" and len(original) >= 3:
        drive = original[0]
        if (
            drive.isalpha()
            and original[1] == ":"
            and original[2] not in ("/", "\\")
            and "/" not in original
            and "\\" not in original
        ):
            recovered = Path(original.replace(":", ":\\", 1))
            if recovered.exists():
                return recovered.resolve()
            recovered = Path(f"{drive}:\\{original[2:]}")
            if recovered.exists():
                return recovered.resolve()
            raise ValueError(
                f"Workspace path appears mangled by the shell: {original!r}. "
                f"Use forward slashes (e.g., {drive}:/temp/fileserver) or quote the path."
            )

    return candidate


class CliRouter:
    """Central dispatcher for Polaris CLI commands.

    Responsibility:
      - Register and route subcommands from argparse.
      - Normalise arguments (workspace resolution, role/mode defaults).
      - Emit compatibility warnings for legacy entry points.
      - Delegate to command-specific handlers and return exit codes.
    """

    def __init__(self) -> None:
        self._parser = self._build_parser()
        self._handlers: dict[str, CommandHandler] = {}
        self._workspace: str = "."
        self._role: str = "director"
        self._mode: str = "interactive"
        self._session_id: str | None = None

    # ------------------------------------------------------------------
    # Parser construction
    # ------------------------------------------------------------------

    @staticmethod
    def _add_log_level_argument(
        parser: argparse.ArgumentParser,
        *,
        default: str | None | object = None,
    ) -> None:
        parser.add_argument(
            "--log-level",
            choices=CLI_LOG_LEVEL_CHOICES,
            default=default,
            help=(
                "CLI logging level. Supports debug/info/warn/warning/error/critical (or env POLARIS_CLI_LOG_LEVEL)."
            ),
        )

    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        """Build the top-level ArgumentParser with all subcommands."""
        parser = argparse.ArgumentParser(
            prog="polaris-cli",
            description="Polaris unified host: one host, multi-role, multi-mode",
        )
        parser.add_argument(
            "--workspace",
            "-w",
            type=str,
            default=".",
            help="Workspace directory",
        )
        CliRouter._add_log_level_argument(parser, default=None)
        subparsers = parser.add_subparsers(dest="command", required=True)

        # chat subcommand
        chat = subparsers.add_parser("chat", help="Run a role through the canonical Polaris host")
        CliRouter._add_log_level_argument(chat, default=argparse.SUPPRESS)
        chat.add_argument("--role", type=str, default="director", help="Role id")
        chat.add_argument(
            "--mode",
            choices=["interactive", "oneshot", "server", "console"],
            default="interactive",
            help="Host mode",
        )
        chat.add_argument("--goal", type=str, default="", help="Goal for oneshot mode")
        chat.add_argument("--host", type=str, default="127.0.0.1", help="Server bind host")
        chat.add_argument("--port", type=int, default=50000, help="Server bind port")
        chat.add_argument(
            "--backend",
            choices=["auto", "textual", "rich", "plain"],
            default="auto",
            help="Console backend (legacy textual/rich values are compatibility aliases)",
        )
        chat.add_argument("--session-id", type=str, default="", help="Session to reuse")
        chat.add_argument("--session-title", type=str, default="", help="New session title")

        # status subcommand
        status = subparsers.add_parser("status", help="Query runtime status")
        CliRouter._add_log_level_argument(status, default=argparse.SUPPRESS)
        status.add_argument("--role", type=str, default="", help="Optional role filter")

        # workflow subcommand
        wf = subparsers.add_parser("workflow", help="Run or inspect Polaris workflow")
        CliRouter._add_log_level_argument(wf, default=argparse.SUPPRESS)
        wf.add_argument(
            "workflow_action",
            choices=["run", "status", "events", "cancel"],
            help="Workflow action",
        )
        wf.add_argument("workflow_target", nargs="?", default="", help="Workflow target (default: pm)")
        wf.add_argument("--workflow-id", type=str, default="", help="Workflow id")
        wf.add_argument("--contracts-file", type=str, default="runtime/contracts/pm_tasks.contract.json")
        wf.add_argument("--run-id", type=str, default="", help="Explicit workflow run id")
        wf.add_argument("--message", type=str, default="", help="Operator note")
        wf.add_argument(
            "--wait",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Wait for workflow completion",
        )
        wf.add_argument("--timeout-seconds", type=float, default=300.0)
        wf.add_argument("--event-limit", type=int, default=100)
        wf.add_argument("--reason", type=str, default="operator_cancelled")
        wf.add_argument(
            "--execution-mode",
            choices=["parallel", "serial"],
            default="parallel",
        )
        wf.add_argument("--max-parallel-tasks", type=int, default=3)
        wf.add_argument("--ready-timeout-seconds", type=int, default=30)
        wf.add_argument("--task-timeout-seconds", type=int, default=MAX_WORKFLOW_TIMEOUT_SECONDS)

        # test-window subcommand
        tw = subparsers.add_parser(
            "test-window",
            help=argparse.SUPPRESS,
            description="Compatibility-only legacy role test window",
        )
        CliRouter._add_log_level_argument(tw, default=argparse.SUPPRESS)
        tw.add_argument("--role", type=str, default="director", help="Role id")
        tw.add_argument("--surface", choices=["tui"], default="tui")

        return parser

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def route(self, argv: list[str]) -> int:
        """Parse and dispatch CLI arguments.

        Args:
            argv: Command-line arguments (not including program name).

        Returns:
            Exit code (0 = success, non-zero = failure).
        """
        emit_compat_warnings(argv)

        try:
            args = self._parser.parse_args(argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1

        # Normalise workspace
        try:
            configure_cli_logging(getattr(args, "log_level", None))
        except ValueError as exc:
            logger.error("Invalid --log-level: %s", exc)
            return 2

        self._workspace = str(_resolve_workspace(str(args.workspace or ".")))
        self._role = str(getattr(args, "role", None) or "director").strip().lower() or "director"
        self._mode = str(getattr(args, "mode", None) or "interactive").strip().lower() or "interactive"
        self._session_id = _normalise_session_id(getattr(args, "session_id", None))

        warn_if_no_workspace(args.workspace)

        command = str(args.command or "").strip().lower()
        handler = self._handlers.get(command)

        if handler is not None:
            try:
                result = handler(args)
                return result if isinstance(result, int) else 0
            except (RuntimeError, ValueError) as exc:  # pragma: no cover — defensive dispatch
                logger.error("Command %s raised: %s", command, exc)
                return 1

        # No handler registered — acknowledge the command exists (subparsers enforce this)
        logger.debug("No handler registered for command '%s'; acknowledging.", command)
        return 0

    def parsed(self, argv: list[str]) -> ParsedCommand:
        """Parse arguments without executing, returning a structured result.

        Args:
            argv: Command-line arguments (not including program name).

        Returns:
            ParsedCommand with all normalised fields.

        Raises:
            SystemExit: If parsing fails.
        """
        args = self._parser.parse_args(argv)

        from pathlib import Path

        workspace = str(Path(args.workspace or ".").resolve())
        role = str(getattr(args, "role", None) or "director").strip().lower() or "director"
        mode = str(getattr(args, "mode", None) or "interactive").strip().lower() or "interactive"
        session_id = _normalise_session_id(getattr(args, "session_id", None))

        subcommand: str | None = None
        if hasattr(args, "workflow_action") and args.workflow_action:
            subcommand = str(args.workflow_action).strip().lower()

        return ParsedCommand(
            command=str(args.command or "").strip().lower(),
            subcommand=subcommand,
            workspace=workspace,
            raw_args=args,
            role=role,
            mode=mode,
            session_id=session_id,
        )

    def register(self, command: str, handler: CommandHandler) -> None:
        """Register a handler for a subcommand.

        Args:
            command: Subcommand name (e.g. 'chat', 'status').
            handler: Callable that receives the parsed args and returns an exit code.
        """
        self._handlers[command] = handler


def _normalise_session_id(value: str | None) -> str | None:
    """Return a stripped, non-empty session id or None."""
    token = str(value or "").strip()
    return token if token else None


def _safe_text(value: object | None) -> str:
    """Strip whitespace from a value, returning empty string for None."""
    return str(value or "").strip()


# ------------------------------------------------------------------
# Console app command parsing (in-app, not argparse-level)
# ------------------------------------------------------------------

_SESSION_COMMANDS = frozenset(["list", "show", "switch", "clear"])


class AppCommand(NamedTuple):
    """Result of parsing an in-app slash command (typed by the user)."""

    kind: str
    """Command kind: 'role', 'session', 'new-session', etc."""

    subcommand: str | None
    """For 'session', the subcommand: 'list', 'show', 'switch', 'clear'."""

    raw_value: str
    """The raw argument string after the command token."""

    normalised_role: str | None
    """For 'role' commands, the normalised role name."""


def parse_app_command(text: str) -> AppCommand | None:
    """Parse an in-app input command starting with '/'.

    Supported commands:
      /role <name>          — switch to role <name>
      /session <sub> <arg>   — session subcommand (list|show|switch|clear)
      /new-session [<title>] — create a new session
      /sidebar, /sb          — toggle sidebar
      /refresh, /r           — refresh

    Args:
        text: Raw user input text (may include leading whitespace).

    Returns:
        AppCommand if the text starts with '/' and matches a known command,
        None otherwise.
    """
    # Check command prefixes on the raw input first so trailing spaces in prefixes
    # like '/role ' are preserved.  Then strip for the leading-whitespace guard.
    raw = str(text)
    if not raw.strip().startswith("/"):
        return None
    message = raw.strip()

    if message in {"/refresh", "/r"}:
        return AppCommand(kind="refresh", subcommand=None, raw_value="", normalised_role=None)

    if message.startswith("/new-session"):
        title = message[len("/new-session") :].strip()
        return AppCommand(kind="new-session", subcommand=None, raw_value=title, normalised_role=None)

    if message.startswith("/session"):
        raw_sess = raw[len("/session") :].strip()
        parts = raw_sess.split(None, 1)
        sub = parts[0].lower() if parts else ""
        return AppCommand(
            kind="session",
            subcommand=sub if sub in _SESSION_COMMANDS else None,
            raw_value=raw_sess,
            normalised_role=None,
        )

    if raw.startswith("/role "):
        raw_role = raw[len("/role ") :]
        role = _safe_text(raw_role).lower()
        return AppCommand(
            kind="role",
            subcommand=None,
            raw_value=raw_role,
            normalised_role=role if role else None,
        )

    if message.startswith("/role"):  # /role without trailing space (e.g. "/rolepm" edge case)
        remainder = message[len("/role") :].strip()
        if remainder:
            role2 = _safe_text(remainder).lower()
            return AppCommand(
                kind="role", subcommand=None, raw_value=remainder, normalised_role=role2 if role2 else None
            )

    if message in {"/sidebar", "/sb"}:
        return AppCommand(kind="sidebar", subcommand=None, raw_value="", normalised_role=None)

    return None
