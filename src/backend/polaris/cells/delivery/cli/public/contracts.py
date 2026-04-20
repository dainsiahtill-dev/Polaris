"""Public contracts for `delivery.cli` cell.

The contracts in this module define the stable boundary for CLI command execution,
status queries, and result/error payloads consumed by the host-layer entry points
(polaris/delivery/cli/{pm,director}/*).

Architecture contract:
    Host layer (pm_cli.py, cli_thin.py, director_service.py)
      → CliExecutionService (this cell's public service)
        → RoleRuntimeService facade  [for role-execution commands]
        → direct handler              [for management commands]

CLI commands that invoke role agents (director, architect, chief_engineer)
MUST route through RoleRuntimeService facade — they must NOT implement their own
tool loop or directly instantiate LLM providers.

Management commands (status, init, requirement_*, task_*) that do not involve
LLM execution MAY be handled directly by CliExecutionService.

Import from this module for all cross-Cell CLI contract types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

# ── Validators ────────────────────────────────────────────────────────────────


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


# ── Enumerations ─────────────────────────────────────────────────────────────


class CliCommandType(str, Enum):
    """Top-level CLI command categories.

    These map to the major subsystems under polaris/delivery/cli/.
    Sub-command strings (e.g. "pm.init", "director.serve") are passed
    as the ``command`` field of ExecuteCliCommandV1.
    """

    # Project Management
    PM_INIT = "pm.init"
    PM_STATUS = "pm.status"
    PM_REQUIREMENT = "pm.requirement"
    PM_TASK = "pm.task"
    PM_HEALTH = "pm.health"
    PM_REPORT = "pm.report"
    PM_COVERAGE = "pm.coverage"
    PM_API_SERVER = "pm.api_server"
    PM_DOCUMENT = "pm.document"
    PM_TASK_HISTORY = "pm.task_history"

    # Director
    DIRECTOR_RUN = "director.run"
    DIRECTOR_SERVE = "director.serve"
    DIRECTOR_STATUS = "director.status"
    DIRECTOR_TASK = "director.task"
    DIRECTOR_WORKER = "director.worker"
    DIRECTOR_CONSOLE = "director.console"

    # Architect
    ARCHITECT_ANALYZE = "architect.analyze"
    ARCHITECT_DESIGN = "architect.design"

    # Chief Engineer
    CHIEF_ENGINEER_ANALYSIS = "chief_engineer.analysis"
    CHIEF_ENGINEER_TASK = "chief_engineer.task"

    # Generic
    GENERIC = "generic"


class ExecutionMode(str, Enum):
    """CLI execution mode — determines routing strategy within CliExecutionService."""

    # Management command (no LLM, no role agent, no tool loop)
    MANAGEMENT = "management"
    # Role-execution command (routes through RoleRuntimeService facade)
    ROLE_EXECUTION = "role_execution"
    # Serve/daemon mode
    DAEMON = "daemon"


class ExitCode(int, Enum):
    """Standard CLI exit codes."""

    SUCCESS = 0
    GENERAL_ERROR = 1
    NOT_INITIALIZED = 2
    WORKSPACE_NOT_FOUND = 3
    INVALID_ARGS = 4
    TIMEOUT = 5
    INTERRUPTED = 130  # 128 + SIGINT(2)


# ── Commands ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExecuteCliCommandV1:
    """Execute a single CLI command.

    This is the primary command contract consumed by CliExecutionService.
    It covers both management commands (status, init, requirement_*) and
    role-execution commands (architect.analyze, director.run, etc.).

    For role-execution commands, CliExecutionService MUST delegate to
    RoleRuntimeService.execute_role_session() and MUST NOT implement
    its own tool loop.

    Examples:
        Execute a PM status check::

            ExecuteCliCommandV1(
                command="pm.status",
                workspace="/path/to/repo",
                execution_mode=ExecutionMode.MANAGEMENT,
            )

        Execute a Director task::

            ExecuteCliCommandV1(
                command="director.task.execute",
                workspace="/path/to/repo",
                execution_mode=ExecutionMode.ROLE_EXECUTION,
                role="director",
                arguments={"subject": "Implement login", "description": "..."},
                session_id="director-task-1",
            )
    """

    command: str
    workspace: str
    execution_mode: ExecutionMode = ExecutionMode.MANAGEMENT
    arguments: Mapping[str, Any] = field(default_factory=dict)
    role: str | None = None  # required when execution_mode == ROLE_EXECUTION
    session_id: str | None = None
    timeout_seconds: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", _require_non_empty("command", self.command))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "arguments", _to_dict_copy(self.arguments))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.execution_mode == ExecutionMode.ROLE_EXECUTION and not self.role:
            raise ValueError("role is required when execution_mode == ROLE_EXECUTION")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")

    def command_type(self) -> CliCommandType:
        """Return the classified command type, or CliCommandType.GENERIC."""
        # Try exact match first
        try:
            return CliCommandType(self.command)
        except ValueError:
            pass
        # Try prefix match (e.g. "pm.requirement.add" -> "pm.requirement")
        prefix = self.command.split(".")[0] + "." + self.command.split(".")[1]
        try:
            return CliCommandType(prefix)
        except ValueError:
            pass
        return CliCommandType.GENERIC


@dataclass(frozen=True)
class QueryCliStatusV1:
    """Query the status of the CLI subsystem for a workspace."""

    workspace: str
    include_commands: bool = True
    include_active_sessions: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


# ── Events ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CliCommandStartedEventV1:
    """Event emitted when CliExecutionService starts a command."""

    event_id: str
    command: str
    workspace: str
    execution_mode: ExecutionMode
    started_at: str  # ISO-8601
    session_id: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "command", _require_non_empty("command", self.command))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "started_at", _require_non_empty("started_at", self.started_at))


@dataclass(frozen=True)
class CliCommandCompletedEventV1:
    """Event emitted when CliExecutionService completes a command."""

    event_id: str
    command: str
    workspace: str
    status: str  # "success" | "failure" | "timeout" | "interrupted"
    exit_code: int
    completed_at: str  # ISO-8601
    duration_ms: int | None = None
    session_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "command", _require_non_empty("command", self.command))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


# ── Results ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CommandResultV1:
    """Unified result returned by CliExecutionService for all CLI commands.

    All fields are optional to accommodate commands that produce no structured output
    (e.g. health checks that only print to stdout).
    """

    ok: bool
    exit_code: int
    command: str
    workspace: str
    output: str = ""
    structured: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None
    session_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", _require_non_empty("command", self.command))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "output", str(self.output))
        object.__setattr__(self, "structured", _to_dict_copy(self.structured))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON serialization."""
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "command": self.command,
            "workspace": self.workspace,
            "output": self.output,
            "structured": dict(self.structured),
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


# ── Errors ───────────────────────────────────────────────────────────────────


class CommandErrorV1(Exception):
    """Structured error raised by CliExecutionService.

    Carries a machine-readable ``code`` and optional structured ``details``
    to allow callers to take corrective action rather than parsing stderr.

    Exit codes for common errors are also provided via the ``exit_code`` property.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "cli_error",
        details: Mapping[str, Any] | None = None,
        exit_code: int = 1,
    ) -> None:
        normalized_message = _require_non_empty("message", message)
        super().__init__(normalized_message)
        self.code = _require_non_empty("code", code)
        self.details: dict[str, Any] = _to_dict_copy(details)
        self.exit_code = exit_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": dict(self.details),
            "exit_code": self.exit_code,
        }


class WorkspaceNotFoundError(CommandErrorV1):
    """Raised when the workspace path does not exist."""

    def __init__(self, workspace: str) -> None:
        super().__init__(
            f"Workspace does not exist: {workspace}",
            code="workspace_not_found",
            details={"workspace": workspace},
            exit_code=ExitCode.WORKSPACE_NOT_FOUND.value,
        )


class CommandNotFoundError(CommandErrorV1):
    """Raised when the command string does not match any known handler."""

    def __init__(self, command: str) -> None:
        super().__init__(
            f"Unknown CLI command: {command}",
            code="command_not_found",
            details={"command": command},
            exit_code=ExitCode.INVALID_ARGS.value,
        )


class CommandTimeoutError(CommandErrorV1):
    """Raised when command execution exceeds the configured timeout."""

    def __init__(self, command: str, timeout_seconds: int) -> None:
        super().__init__(
            f"Command timed out after {timeout_seconds}s: {command}",
            code="command_timeout",
            details={"command": command, "timeout_seconds": timeout_seconds},
            exit_code=ExitCode.TIMEOUT.value,
        )


class WorkspaceNotInitializedError(CommandErrorV1):
    """Raised when a PM command is run before ``pm init``."""

    def __init__(self, workspace: str, command: str) -> None:
        super().__init__(
            f"PM not initialized at workspace: {workspace}. Run 'pm init' first.",
            code="workspace_not_initialized",
            details={"workspace": workspace, "command": command},
            exit_code=ExitCode.NOT_INITIALIZED.value,
        )


# ── Public exports ────────────────────────────────────────────────────────────

__all__ = [
    "CliCommandCompletedEventV1",
    # ── Events ─────────────────────────────────────────────────────────────
    "CliCommandStartedEventV1",
    # ── Enumerations ─────────────────────────────────────────────────────────
    "CliCommandType",
    # ── Errors ──────────────────────────────────────────────────────────────
    "CommandErrorV1",
    "CommandNotFoundError",
    # ── Results ─────────────────────────────────────────────────────────────
    "CommandResultV1",
    "CommandTimeoutError",
    # ── Commands ────────────────────────────────────────────────────────────
    "ExecuteCliCommandV1",
    "ExecutionMode",
    "ExitCode",
    "QueryCliStatusV1",
    "WorkspaceNotFoundError",
    "WorkspaceNotInitializedError",
]
