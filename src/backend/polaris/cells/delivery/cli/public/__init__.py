"""Public interface for ``polaris.cells.delivery.cli`` cell."""

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
from polaris.cells.delivery.cli.public.service import (
    CliExecutionService,
    get_cli_service,
    register_management_handler,
    register_pm_management_handlers,
)

__all__ = [
    "CliCommandCompletedEventV1",
    "CliCommandStartedEventV1",
    # Contracts
    "CliCommandType",
    # Service
    "CliExecutionService",
    "CommandErrorV1",
    "CommandNotFoundError",
    "CommandNotInitializedError",
    "CommandResultV1",
    "CommandTimeoutError",
    "ExecuteCliCommandV1",
    "ExecutionMode",
    "ExitCode",
    "QueryCliStatusV1",
    "WorkspaceNotFoundError",
    "WorkspaceNotInitializedError",
    "get_cli_service",
    "register_management_handler",
    "register_pm_management_handlers",
]
