"""``polaris.cells.delivery.cli`` — CLI delivery cell.

This cell owns the CLI execution contracts and the CliExecutionService
implementation. It does NOT own the actual CLI entry points under
``polaris/delivery/cli/`` — those are owned by the delivery.host layer.

Import contracts from here; do not import from internal modules.
"""

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
]
