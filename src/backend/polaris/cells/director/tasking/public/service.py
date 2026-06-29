"""Public service handlers for `director.tasking` cell.

G4-pattern wiring (mirrors `resident.autonomy/public/service.py`): the public
Command/Query contracts were declared but had no handler — external consumers
could construct `CreateTaskCommandV1` yet nothing accepted it, while the rich
internal `TaskService` was reachable only through CLI/runtime composition.
These handlers close that architectural gap by delegating 1:1 to the existing
internal implementation; no new business logic lives here.

TaskService keeps task state in memory, so handlers share one service instance
per resolved workspace — create/status/cancel/result form a coherent surface
through this module. Runtime components that construct their own TaskService
(bootstrap assembly, DirectorService) are intentionally untouched.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.director.tasking.internal.execution_profile import (
    resolve_director_execution_profile,
)
from polaris.cells.director.tasking.internal.task_lifecycle_service import (
    TaskQueueConfig,
    TaskService,
)
from polaris.cells.director.tasking.public.contracts import (
    CancelTaskCommandV1,
    CreateTaskCommandV1,
    TaskCreatedResultV1,
    TaskResultQueryV1,
    TaskResultResultV1,
    TaskStatusQueryV1,
    TaskStatusResultV1,
)
from polaris.domain.entities.task import Task, TaskPriority, TaskStatus

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_UNASSIGNED_TASK_ID = "unassigned"

_services: dict[str, TaskService] = {}
_services_lock = threading.Lock()


def _run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Bridge async TaskService calls into sync contract handlers."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


def get_task_service(workspace: str) -> TaskService:
    """Return the shared per-workspace TaskService used by contract handlers."""
    key = str(Path(str(workspace or ".")).resolve())
    with _services_lock:
        service = _services.get(key)
        if service is None:
            service = TaskService(config=TaskQueueConfig(), workspace=key)
            _services[key] = service
        return service


def reset_task_services() -> None:
    """Test hook: drop all cached per-workspace services."""
    with _services_lock:
        _services.clear()


def _coerce_priority(raw: str) -> TaskPriority:
    try:
        return TaskPriority(str(raw or "medium").strip().lower())
    except ValueError:
        return TaskPriority.MEDIUM


def _task_to_dict(task: Task) -> dict[str, Any]:
    to_dict = getattr(task, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    return {
        "id": str(task.id),
        "subject": task.subject,
        "status": str(getattr(task.status, "value", task.status)),
        "priority": str(getattr(task.priority, "value", task.priority)),
    }


def create_task(command: CreateTaskCommandV1) -> TaskCreatedResultV1:
    """Handle CreateTaskCommandV1 → internal TaskService.create_task."""
    service = get_task_service(command.workspace)
    try:
        task = _run_async(
            service.create_task(
                subject=command.subject,
                description=command.description,
                command=command.command,
                priority=_coerce_priority(command.priority),
                blocked_by=list(command.blocked_by or []),
                timeout_seconds=command.timeout_seconds,
                metadata=dict(command.metadata or {}),
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("create_task failed: %s", exc)
        return TaskCreatedResultV1(
            ok=False,
            task_id=_UNASSIGNED_TASK_ID,
            workspace=command.workspace,
            subject=command.subject,
            status="error",
            error_code="task_create_failed",
            error_message=str(exc),
        )
    return TaskCreatedResultV1(
        ok=True,
        task_id=str(task.id),
        workspace=command.workspace,
        subject=task.subject,
        status=str(getattr(task.status, "value", task.status)),
    )


def cancel_task(command: CancelTaskCommandV1) -> TaskStatusResultV1:
    """Handle CancelTaskCommandV1 → internal TaskService.cancel_task."""
    service = get_task_service(command.workspace)
    task = _run_async(service.get_task(command.task_id))
    if task is None:
        return TaskStatusResultV1(
            ok=False,
            workspace=command.workspace,
            error_code="task_not_found",
            error_message=f"task {command.task_id} not found",
        )
    cancelled = _run_async(service.cancel_task(command.task_id, command.reason))
    refreshed = _run_async(service.get_task(command.task_id)) or task
    if not cancelled:
        return TaskStatusResultV1(
            ok=False,
            workspace=command.workspace,
            tasks=[_task_to_dict(refreshed)],
            count=1,
            error_code="task_not_cancellable",
            error_message=(f"task {command.task_id} is {getattr(refreshed.status, 'value', refreshed.status)}"),
        )
    return TaskStatusResultV1(
        ok=True,
        workspace=command.workspace,
        tasks=[_task_to_dict(refreshed)],
        count=1,
    )


def query_task_status(query: TaskStatusQueryV1) -> TaskStatusResultV1:
    """Handle TaskStatusQueryV1 → internal TaskService.get_task/get_tasks."""
    service = get_task_service(query.workspace)
    if query.task_id:
        task = _run_async(service.get_task(query.task_id))
        if task is None:
            return TaskStatusResultV1(
                ok=False,
                workspace=query.workspace,
                error_code="task_not_found",
                error_message=f"task {query.task_id} not found",
            )
        return TaskStatusResultV1(ok=True, workspace=query.workspace, tasks=[_task_to_dict(task)], count=1)

    status_filter: TaskStatus | None = None
    if query.status:
        try:
            status_filter = TaskStatus(str(query.status).strip().lower())
        except ValueError:
            return TaskStatusResultV1(
                ok=False,
                workspace=query.workspace,
                error_code="invalid_status_filter",
                error_message=f"unknown status: {query.status}",
            )
    tasks = _run_async(service.get_tasks(status=status_filter))
    limit = max(1, int(query.limit or 50))
    selected = tasks[:limit]
    return TaskStatusResultV1(
        ok=True,
        workspace=query.workspace,
        tasks=[_task_to_dict(task) for task in selected],
        count=len(selected),
    )


def query_task_result(query: TaskResultQueryV1) -> TaskResultResultV1:
    """Handle TaskResultQueryV1 → task terminal state + result payload."""
    service = get_task_service(query.workspace)
    task = _run_async(service.get_task(query.task_id))
    if task is None:
        return TaskResultResultV1(
            ok=False,
            task_id=query.task_id,
            workspace=query.workspace,
            error_code="task_not_found",
            error_message=f"task {query.task_id} not found",
        )

    terminal_success: bool | None = None
    if task.status == TaskStatus.COMPLETED:
        terminal_success = True
    elif task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.TIMEOUT):
        terminal_success = False

    result = getattr(task, "result", None) or getattr(task, "_result", None)
    output = str(getattr(result, "output", "") or "") or str(getattr(task, "result_summary", "") or "")
    duration_raw = getattr(result, "duration_ms", None)
    duration_ms = int(duration_raw) if isinstance(duration_raw, (int, float)) and duration_raw > 0 else None
    error = getattr(result, "error", None) or getattr(task, "error_message", None)
    evidence_refs = getattr(task, "evidence_refs", None) or []
    evidence = [item if isinstance(item, dict) else {"ref": str(item)} for item in evidence_refs]

    return TaskResultResultV1(
        ok=True,
        task_id=query.task_id,
        workspace=query.workspace,
        success=terminal_success,
        output=output,
        error=str(error) if error else None,
        duration_ms=duration_ms,
        evidence=evidence,
        error_code=None,
        error_message=None,
    )


def build_director_execution_profile_snapshot(
    *,
    subject: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
    target_files: list[str] | tuple[str, ...] | None = None,
    scope_paths: list[str] | tuple[str, ...] | None = None,
    workspace: str = "",
) -> dict[str, Any]:
    """Return the canonical Director execution profile payload and hash.

    Chief Engineer handoff uses this public Director tasking surface to freeze
    the same profile that Director later consumes. That prevents CE and
    Director from independently re-classifying a task and drifting on
    temperature, output protocol, or scope bindings.
    """

    profile = resolve_director_execution_profile(
        subject=subject,
        description=description,
        metadata=dict(metadata or {}),
        target_files=target_files,
        scope_paths=scope_paths,
        workspace=workspace,
    )
    payload = profile.to_dict()
    profile_hash = stable_hash(payload)
    return {
        "schema_version": "director.execution_profile_snapshot.v1",
        "profile": payload,
        "profile_hash": profile_hash,
        "profile_ref": f"director.tasking.execution_profile:{profile_hash[:24]}",
    }


__all__ = [
    "build_director_execution_profile_snapshot",
    "cancel_task",
    "create_task",
    "get_task_service",
    "query_task_result",
    "query_task_status",
    "reset_task_services",
]
