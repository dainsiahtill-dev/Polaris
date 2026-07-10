"""Public service export for the ``runtime.task_runtime`` cell.

Primary implementation lives in
``polaris.cells.runtime.task_runtime.internal.service``.
"""

from __future__ import annotations

from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService, reset_runtime_task_records

from .contracts import (
    OwnerReworkExecutionPreparationResultV1,
    PrepareOwnerReworkExecutionCommandV1,
)


def prepare_owner_rework_execution(
    command: PrepareOwnerReworkExecutionCommandV1,
) -> OwnerReworkExecutionPreparationResultV1:
    """Prepare one already-claimed TaskMarket owner-rework task for execution.

    TaskMarket decides orchestration, dependency readiness, and the claim
    lease. This public boundary delegates only TaskRuntime's execution-row and
    session transition to the runtime owner cell.
    """

    runtime = TaskRuntimeService(command.authorization.workspace)
    return runtime.prepare_owner_rework_execution(command)


__all__ = ["TaskRuntimeService", "prepare_owner_rework_execution", "reset_runtime_task_records"]
