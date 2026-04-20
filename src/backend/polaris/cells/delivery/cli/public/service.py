"""Public service for `delivery.cli` cell.

This module provides the canonical execution service for CLI commands.
It dispatches management commands directly and routes role-execution commands
through RoleRuntimeService facade — never implementing its own tool loop.

Architecture:
    Host (pm_cli.py, cli_thin.py, director_service.py)
      → CliExecutionService.execute_command()
        → MANAGEMENT mode: direct handler (no LLM)
        → ROLE_EXECUTION mode: RoleRuntimeService facade → RoleExecutionKernel
        → DAEMON mode: CliExecutionService (background loop)

All imports from this module use the public contract types defined in
``polaris.cells.delivery.cli.public.contracts``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.delivery.cli.public.contracts import (
    CommandErrorV1,
    CommandNotFoundError,
    CommandResultV1,
    CommandTimeoutError,
    ExecuteCliCommandV1,
    ExecutionMode,
    QueryCliStatusV1,
    WorkspaceNotFoundError,
)
from polaris.kernelone.utils.time_utils import utc_now_iso_compact

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependency at module level.
# RoleRuntimeService lives in roles.runtime.public.service.
_ROLE_RUNTIME_SERVICE: Any = None


def _get_role_runtime_service() -> Any:
    global _ROLE_RUNTIME_SERVICE
    if _ROLE_RUNTIME_SERVICE is None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        _ROLE_RUNTIME_SERVICE = RoleRuntimeService()
    return _ROLE_RUNTIME_SERVICE


# ── Management command registry ────────────────────────────────────────────────

# Handlers registered at import time. Each handler receives (workspace, arguments)
# and returns a dict with at least "ok": bool and "output": str.
_MANAGEMENT_HANDLERS: dict[str, Callable[[Path, dict[str, Any]], dict[str, Any]]] = {}


def register_management_handler(command: str, handler: Callable[[Path, dict[str, Any]], dict[str, Any]]) -> None:
    """Register a handler for a management command.

    Handler signature: (workspace: Path, arguments: dict) -> dict

    The returned dict must contain at least:
        - ok: bool
        - output: str

    Optional fields:
        - structured: dict (forwarded to CommandResultV1.structured)
        - error_code: str
        - error_message: str
    """
    _MANAGEMENT_HANDLERS[command] = handler


def _get_management_handler(command: str) -> Callable[[Path, dict[str, Any]], dict[str, Any]] | None:
    # Exact match
    if command in _MANAGEMENT_HANDLERS:
        return _MANAGEMENT_HANDLERS[command]
    # Prefix match: "pm.requirement.add" → "pm.requirement"
    parts = command.split(".")
    if len(parts) >= 2:
        prefix = f"{parts[0]}.{parts[1]}"
        if prefix in _MANAGEMENT_HANDLERS:
            return _MANAGEMENT_HANDLERS[prefix]
    return None


# ── CliExecutionService ───────────────────────────────────────────────────────


class CliExecutionService:
    """Canonical CLI execution service.

    Dispatches CLI commands based on their ``execution_mode``:

    - ``MANAGEMENT``: direct handler from _MANAGEMENT_HANDLERS registry
    - ``ROLE_EXECUTION``: delegates to RoleRuntimeService facade
    - ``DAEMON``: runs in background, emitting events (future)
    """

    def __init__(self) -> None:
        self._active_sessions: dict[str, dict[str, Any]] = {}

    # ── Public execution API ────────────────────────────────────────────────────

    async def execute_command(
        self,
        command: ExecuteCliCommandV1,
    ) -> CommandResultV1:
        """Execute a CLI command and return a structured result.

        Args:
            command: The CLI command to execute.

        Returns:
            CommandResultV1 with ok, exit_code, output, structured, etc.

        Raises:
            WorkspaceNotFoundError: workspace path does not exist
            CommandNotFoundError: no handler registered for command
            CommandTimeoutError: execution exceeded timeout_seconds
            CommandErrorV1: other execution error
        """
        workspace = Path(command.workspace)
        _validate_workspace_exists(workspace, command.command)
        started_at = _utc_now()
        event_id = str(uuid.uuid4())
        session_id = command.session_id or event_id

        if session_id:
            self._active_sessions[session_id] = {
                "command": command.command,
                "started_at": started_at,
                "workspace": str(workspace),
            }

        logger.info(
            "cli execution start: command=%s workspace=%s mode=%s session_id=%s",
            command.command,
            workspace,
            command.execution_mode.value,
            session_id,
        )

        t0 = time.monotonic()
        try:
            if command.execution_mode == ExecutionMode.MANAGEMENT:
                result = await self._execute_management(workspace, command)
            elif command.execution_mode == ExecutionMode.ROLE_EXECUTION:
                result = await self._execute_role(workspace, command)
            elif command.execution_mode == ExecutionMode.DAEMON:
                result = await self._execute_daemon(workspace, command)
            else:
                raise CommandErrorV1(
                    f"Unknown execution mode: {command.execution_mode}",
                    code="unknown_execution_mode",
                )
        finally:
            if session_id in self._active_sessions:
                del self._active_sessions[session_id]

        duration_ms = int((time.monotonic() - t0) * 1000)
        exit_code = result.get("exit_code", 0 if result.get("ok") else 1)

        logger.info(
            "cli execution done: command=%s ok=%s exit_code=%s duration_ms=%s",
            command.command,
            result.get("ok"),
            exit_code,
            duration_ms,
        )

        return CommandResultV1(
            ok=result.get("ok", False),
            exit_code=exit_code,
            command=command.command,
            workspace=str(workspace),
            output=result.get("output", ""),
            structured=result.get("structured", {}),
            duration_ms=duration_ms,
            session_id=session_id,
            error_code=result.get("error_code"),
            error_message=result.get("error_message"),
        )

    async def execute_command_sync(
        self,
        command: ExecuteCliCommandV1,
    ) -> int:
        """Execute a CLI command and return the exit code.

        Convenience wrapper for host layers that need only the exit code.

        Returns:
            Exit code (0 = success, non-zero = failure)
        """
        result = await self.execute_command(command)
        return result.exit_code

    async def get_status(
        self,
        query: QueryCliStatusV1,
    ) -> dict[str, Any]:
        """Return a status snapshot for the CLI subsystem."""
        workspace = Path(query.workspace)
        _validate_workspace_exists(workspace, "<query>")

        return {
            "workspace": str(workspace),
            "active_sessions": (list(self._active_sessions.keys()) if query.include_active_sessions else []),
            "registered_commands": (list(_MANAGEMENT_HANDLERS.keys()) if query.include_commands else []),
            "checked_at": _utc_now(),
        }

    # ── Internal execution paths ───────────────────────────────────────────────

    async def _execute_management(
        self,
        workspace: Path,
        command: ExecuteCliCommandV1,
    ) -> dict[str, Any]:
        """Execute a management command via registered handler."""
        handler = _get_management_handler(command.command)
        if handler is None:
            raise CommandNotFoundError(command.command)

        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(workspace, dict(command.arguments))
            else:
                result = handler(workspace, dict(command.arguments))
        except CommandErrorV1:
            raise
        except (RuntimeError, ValueError) as exc:
            logger.exception("management handler failed: command=%s", command.command)
            raise CommandErrorV1(
                f"Handler failed: {exc}",
                code="handler_error",
                details={"command": command.command, "exception": str(exc)},
            ) from exc

        return dict(result)

    async def _execute_role(
        self,
        workspace: Path,
        command: ExecuteCliCommandV1,
    ) -> dict[str, Any]:
        """Execute a role-execution command via RoleRuntimeService facade.

        This method MUST NOT implement its own tool loop. All tool execution
        is handled by RoleExecutionKernel inside RoleRuntimeService.
        """
        if not command.role:
            raise CommandErrorV1(
                "role is required for ROLE_EXECUTION mode",
                code="missing_role",
            )

        role_runtime = _get_role_runtime_service()

        # Build the role session message from command arguments
        user_message = (
            command.arguments.get("message")
            or command.arguments.get("user_message")
            or self._build_role_message(command)
        )

        from polaris.cells.roles.runtime.public.contracts import (
            ExecuteRoleSessionCommandV1,
        )

        role_command = ExecuteRoleSessionCommandV1(
            role=command.role,
            session_id=command.session_id or f"cli-{uuid.uuid4().hex[:8]}",
            workspace=str(workspace),
            user_message=user_message,
            history=(),
            stream=False,
        )

        try:
            timeout = command.timeout_seconds
            if timeout:
                result_payload = await asyncio.wait_for(
                    role_runtime.execute_role_session(role_command),
                    timeout=timeout,
                )
            else:
                result_payload = await role_runtime.execute_role_session(role_command)
        except asyncio.TimeoutError as exc:
            raise CommandTimeoutError(command.command, command.timeout_seconds or 0) from exc
        except (RuntimeError, ValueError) as exc:
            logger.exception(
                "role execution failed: command=%s role=%s",
                command.command,
                command.role,
            )
            raise CommandErrorV1(
                f"Role execution failed: {exc}",
                code="role_execution_error",
                details={"command": command.command, "role": command.role},
            ) from exc

        # Extract output text from RoleExecutionResultV1 or dict
        output = _extract_role_output(result_payload)

        return {
            "ok": True,
            "output": output,
            "structured": {
                "role": command.role,
                "session_id": role_command.session_id,
                "result": result_payload,
            },
        }

    async def _execute_daemon(
        self,
        workspace: Path,
        command: ExecuteCliCommandV1,
    ) -> dict[str, Any]:
        """Execute a daemon-mode command (runs in background).

        Currently a placeholder. Real daemon execution would start a
        long-running server process and return immediately with a handle.
        """
        raise CommandErrorV1(
            "DAEMON mode is not yet implemented",
            code="daemon_not_implemented",
            exit_code=1,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_role_message(command: ExecuteCliCommandV1) -> str:
        """Build a role-session user message from CLI arguments.

        Subclasses / callers may override this to provide richer context.
        """
        lines = [
            f"Command: {command.command}",
            f"Workspace: {command.workspace}",
        ]
        args = dict(command.arguments)
        if args:
            lines.append("Arguments:")
            for k, v in args.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)


# ── Utilities ─────────────────────────────────────────────────────────────────


def _validate_workspace_exists(workspace: Path, command: str) -> None:
    if not workspace.exists():
        raise WorkspaceNotFoundError(str(workspace))


# Backward compatibility alias
_utc_now = utc_now_iso_compact


def _extract_role_output(result_payload: Any) -> str:
    """Extract plain text output from a RoleRuntimeService result."""
    if isinstance(result_payload, dict):
        return str(
            result_payload.get("output") or result_payload.get("text") or result_payload.get("response") or ""
        ).strip()
    return str(result_payload or "").strip()


# ── PM management handler registration ─────────────────────────────────────────


def _pm_handler_adapter(
    func: Any,
    workspace: Path,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Bridge a PM CLI sync command function to the management handler contract.

    The handler contract requires: (workspace: Path, arguments: dict) -> dict
    PM CLI commands expect: (args: argparse.Namespace) -> int
    This adapter converts the Cell contract to the PM CLI contract.
    """

    class _FakeArgs:
        """Fake Namespace that exposes arguments as attributes for pm_cli commands."""

        def __init__(self, workspace: str, arguments: dict[str, Any]) -> None:
            self.workspace = workspace
            for k, v in arguments.items():
                setattr(self, k, v)

    fake_args = _FakeArgs(str(workspace), arguments)
    try:
        exit_code = func(fake_args)
        return {
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "output": "",
        }
    except (RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "exit_code": 1,
            "output": "",
            "error_code": "handler_error",
            "error_message": str(exc),
        }


def register_pm_management_handlers(pm_cli_module: Any) -> None:
    """Register all PM CLI management handlers with the Cell's handler registry.

    This function bridges the gap documented in ``cell.yaml``'s verification.gaps:
    "Management handler registry is not yet wired to actual pm_cli.py / cli_thin.py."

    It imports the command functions from the PM CLI module and registers
    adapter wrappers that conform to the ``(workspace, arguments) -> dict`` contract
    expected by ``CliExecutionService._execute_management``.

    Call this once at import time in ``polaris/delivery/cli/pm/pm_cli.py``::

        from polaris.cells.delivery.cli.public.service import register_pm_management_handlers
        import polaris.delivery.cli.pm.pm_cli as _pm_cli
        register_pm_management_handlers(_pm_cli)

    Registered command strings (exact match):
        pm.init, pm.status, pm.health, pm.report, pm.coverage,
        pm.api_server,
        pm.requirement.add, pm.requirement.list, pm.requirement.status,
        pm.task.add, pm.task.list, pm.task.assign, pm.task.complete, pm.task.history,
        pm.document.list, pm.document.show

    Args:
        pm_cli_module: The Python module containing PM CLI command functions
                       (e.g. ``polaris.delivery.cli.pm.pm_cli``). Must expose
                       ``cmd_init``, ``cmd_status``, ``cmd_requirement_add``, etc.
    """
    mapping: dict[str, str] = {
        "pm.init": "cmd_init",
        "pm.status": "cmd_status",
        "pm.health": "cmd_health",
        "pm.report": "cmd_report",
        "pm.coverage": "cmd_coverage",
        "pm.api_server": "cmd_api_server",
        "pm.requirement.add": "cmd_requirement_add",
        "pm.requirement.list": "cmd_requirement_list",
        "pm.requirement.status": "cmd_requirement_status",
        "pm.task.add": "cmd_task_add",
        "pm.task.list": "cmd_task_list",
        "pm.task.assign": "cmd_task_assign",
        "pm.task.complete": "cmd_task_complete",
        "pm.task.history": "cmd_task_history",
        "pm.document.list": "cmd_document_list",
        "pm.document.show": "cmd_document_show",
    }

    for command_str, func_name in mapping.items():
        raw_func = getattr(pm_cli_module, func_name, None)
        if raw_func is None:
            logger.warning(
                "PM CLI module %s does not expose %s; skipping registration",
                pm_cli_module.__name__,
                func_name,
            )
            continue

        def make_adapter(fn: Any) -> Any:
            """Create a handler adapter that is non-capturing for each loop iteration."""

            def adapter(workspace: Path, arguments: dict[str, Any]) -> dict[str, Any]:
                return _pm_handler_adapter(fn, workspace, arguments)

            return adapter

        register_management_handler(command_str, make_adapter(raw_func))
        logger.debug("registered management handler: command=%s func=%s", command_str, func_name)


# ── Module-level singleton ────────────────────────────────────────────────────

_service: CliExecutionService | None = None


def get_cli_service() -> CliExecutionService:
    """Return the module-level CliExecutionService singleton."""
    global _service
    if _service is None:
        _service = CliExecutionService()
    return _service
