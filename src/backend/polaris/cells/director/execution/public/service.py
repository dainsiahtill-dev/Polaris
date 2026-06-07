"""Public service exports for `director.execution` cell.

Backward-compatible facade. Implementation migrated to sub-Cells:
- director.planning  → already migrated (Phase 2)
- director.tasking   → TaskQueueConfig, TaskService, WorkerPoolConfig, WorkerService (Phase 3)
- director.runtime   → PatchApplyEngine, FileApplyService, ExistenceGate (Phase 4, pending)
- director.delivery  → director_cli (Phase 5, pending)
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from threading import Thread
from typing import Any

from polaris.cells.director.execution.internal.director_agent import DirectorAgent
from polaris.cells.director.execution.internal.patch_apply_engine import (
    ApplyIntegrity,
    EditType,
    parse_all_operations,
    parse_full_file_blocks,
    parse_search_replace_blocks,
    validate_before_apply,
)
from polaris.cells.director.execution.logic import extract_defect_ticket, parse_acceptance, write_gate_check
from polaris.cells.director.execution.public.contracts import (
    DirectorExecutionError,
    DirectorExecutionResultV1,
    ExecuteDirectorTaskCommandV1,
)
from polaris.cells.director.execution.service import DirectorConfig, DirectorService, DirectorState
from polaris.cells.director.tasking.public import (
    TaskQueueConfig,
    TaskService,
    WorkerPoolConfig,
    WorkerService,
)
from polaris.domain.entities import Task, TaskResult
from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS


def _run_director_awaitable(awaitable: Awaitable[TaskResult]) -> TaskResult:
    async def _await_result() -> TaskResult:
        return await awaitable

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result())

    result_holder: dict[str, TaskResult] = {}
    error_holder: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_holder["result"] = asyncio.run(_await_result())
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensive thread bridge
            error_holder["error"] = exc

    thread = Thread(target=_runner, name="director-execution-public-service", daemon=True)
    thread.start()
    thread.join()
    if error := error_holder.get("error"):
        raise error
    return result_holder["result"]


def _coerce_timeout_seconds(metadata: dict[str, Any]) -> int:
    raw_timeout = metadata.get("timeout_seconds", DEFAULT_OPERATION_TIMEOUT_SECONDS)
    try:
        timeout_seconds = int(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise DirectorExecutionError(
            "metadata.timeout_seconds must be an integer",
            code="invalid_director_timeout",
            details={"timeout_seconds": raw_timeout},
        ) from exc
    if timeout_seconds <= 0:
        raise DirectorExecutionError(
            "metadata.timeout_seconds must be > 0",
            code="invalid_director_timeout",
            details={"timeout_seconds": raw_timeout},
        )
    return timeout_seconds


def _build_director_task(command: ExecuteDirectorTaskCommandV1) -> Task:
    metadata = dict(command.metadata)
    metadata.setdefault("role_capability_id", "execute_director_task")
    metadata.setdefault("public_contract", "ExecuteDirectorTaskCommandV1")
    metadata.setdefault("director_execution_attempt", command.attempt)
    if command.run_id:
        metadata.setdefault("run_id", command.run_id)
    command_line = str(metadata.get("command") or "").strip() or None
    working_directory = str(metadata.get("working_directory") or command.workspace).strip()
    return Task(
        id=command.task_id,
        subject=command.instruction,
        description=command.instruction,
        owner="director",
        assignee="director",
        role="director",
        command=command_line,
        working_directory=working_directory,
        timeout_seconds=_coerce_timeout_seconds(metadata),
        metadata=metadata,
    )


def execute_director_task(
    command: ExecuteDirectorTaskCommandV1,
    *,
    director_service: Any | None = None,
) -> DirectorExecutionResultV1:
    """Execute a Director task through the `director.execution` public contract."""

    if not isinstance(command, ExecuteDirectorTaskCommandV1):
        raise TypeError("command must be ExecuteDirectorTaskCommandV1")

    try:
        task = _build_director_task(command)
        service = director_service or DirectorService(DirectorConfig(workspace=command.workspace))
        maybe_result = service._execute_task_work(task)
        task_result = _run_director_awaitable(maybe_result) if inspect.isawaitable(maybe_result) else maybe_result
        if not isinstance(task_result, TaskResult):
            raise DirectorExecutionError(
                "Director service returned a non-TaskResult value",
                code="invalid_director_task_result",
                details={"result_type": type(task_result).__name__},
            )
    except DirectorExecutionError as exc:
        return DirectorExecutionResultV1(
            ok=False,
            task_id=command.task_id,
            workspace=command.workspace,
            status="failed",
            run_id=command.run_id,
            error_code=exc.code,
            error_message=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
        return DirectorExecutionResultV1(
            ok=False,
            task_id=command.task_id,
            workspace=command.workspace,
            status="failed",
            run_id=command.run_id,
            error_code="director_execution_failed",
            error_message=str(exc),
        )

    evidence_paths = tuple(evidence.path for evidence in task_result.evidence if evidence.path)
    if task_result.success:
        return DirectorExecutionResultV1(
            ok=True,
            task_id=command.task_id,
            workspace=command.workspace,
            status="completed",
            run_id=command.run_id,
            evidence_paths=evidence_paths,
            output_summary=task_result.output,
        )
    return DirectorExecutionResultV1(
        ok=False,
        task_id=command.task_id,
        workspace=command.workspace,
        status="failed",
        run_id=command.run_id,
        evidence_paths=evidence_paths,
        output_summary=task_result.output,
        error_code="director_task_failed",
        error_message=task_result.error or "director task failed",
    )


__all__ = [
    "ApplyIntegrity",
    "DirectorAgent",
    "DirectorConfig",
    "DirectorExecutionError",
    "DirectorExecutionResultV1",
    "DirectorService",
    "DirectorState",
    "EditType",
    "ExecuteDirectorTaskCommandV1",
    "TaskQueueConfig",
    "TaskService",
    "WorkerPoolConfig",
    "WorkerService",
    "execute_director_task",
    "extract_defect_ticket",
    "parse_acceptance",
    "parse_all_operations",
    "parse_full_file_blocks",
    "parse_search_replace_blocks",
    "validate_before_apply",
    "write_gate_check",
]
