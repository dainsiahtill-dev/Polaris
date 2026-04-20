"""Public boundary for `director.execution` cell."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

# Lazy import to avoid circular import at module level.
# DirectorService will be imported inside rebind_director_service when needed.
_rebind_director_service: Any = None

logger = logging.getLogger(__name__)


def _get_rebind_director_service() -> Any:
    """Lazily import rebind_director_service to avoid circular import issues."""
    global _rebind_director_service
    if _rebind_director_service is None:
        # Import from bootstrap where the function is defined
        from polaris.bootstrap.assembly import rebind_director_service

        _rebind_director_service = rebind_director_service
    return _rebind_director_service


async def rebind_director_service(workspace: str | Path) -> Any:
    """Recreate DirectorService singleton for the target workspace.

    This function is re-exported from the public cell boundary to allow
    delivery layer to import from cells rather than bootstrap directly.
    The actual implementation lives in polaris.bootstrap.assembly.
    """
    func = _get_rebind_director_service()
    return await func(workspace)


from polaris.cells.director.execution.public.contracts import (
    DirectorExecutionError,
    DirectorExecutionResultV1,
    DirectorTaskCompletedEventV1,
    DirectorTaskStartedEventV1,
    ExecuteDirectorTaskCommandV1,
    GetDirectorTaskStatusQueryV1,
    RetryDirectorTaskCommandV1,
)

# Import public service types - this import is AFTER rebind_director_service
# definition to avoid circular import issues
from polaris.cells.director.execution.public.service import (
    DirectorConfig,
    DirectorService,
    DirectorState,
    TaskQueueConfig,
    TaskService,
    WorkerPoolConfig,
    WorkerService,
    extract_defect_ticket,
    parse_acceptance,
    write_gate_check,
)
from polaris.cells.director.execution.public.tools import (
    ALLOWED_EXECUTION_COMMANDS,
    build_tool_cli_args,
    is_command_allowed,
    is_command_blocked,
)

__all__ = [
    "ALLOWED_EXECUTION_COMMANDS",
    "DirectorConfig",
    "DirectorExecutionError",
    "DirectorExecutionResultV1",
    "DirectorService",
    "DirectorState",
    "DirectorTaskCompletedEventV1",
    "DirectorTaskStartedEventV1",
    "ExecuteDirectorTaskCommandV1",
    "GetDirectorTaskStatusQueryV1",
    "RetryDirectorTaskCommandV1",
    "TaskQueueConfig",
    "TaskService",
    "WorkerPoolConfig",
    "WorkerService",
    "build_tool_cli_args",
    "extract_defect_ticket",
    "is_command_allowed",
    "is_command_blocked",
    "parse_acceptance",
    "rebind_director_service",
    "write_gate_check",
]
