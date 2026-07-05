"""Task-runtime integration module for Polaris engine.

The public PM engine keeps the historical ``taskboard`` function names for
call-site stability, but the implementation is backed by
``TaskRuntimeService`` row/session APIs.  Raw TaskBoard objects are private to
the ``runtime.task_runtime`` cell and must not be dynamically loaded here.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.cli.pm.tasks import build_taskboard_sync_payload

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def _taskboard_mainline_enabled() -> bool:
    """Check if taskboard mainline is enabled."""
    token = str(os.environ.get("KERNELONE_DISABLE_TASKBOARD_MAINLINE", "0")).strip().lower()
    return token not in {"1", "true", "yes", "on"}


def _task_runtime_priority(priority: int) -> int:
    """Map PM priority buckets to the runtime task-row priority scale."""
    level = int(priority or 0)
    if level <= 0:
        return 3
    if level <= 2:
        return 2
    if level <= 6:
        return 1
    return 0


def _task_runtime_workspace(*, workspace_full: str, run_id: str) -> Path:
    """Resolve the isolated task-runtime workspace used by PM mainline dispatch."""
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    return (
        Path(workspace_full).resolve()
        / get_workspace_metadata_dir_name()
        / "runtime"
        / "state"
        / "taskboard_mainline"
        / (str(run_id or "run").strip() or "run")
    )


def _task_row_id(row: dict[str, Any] | None) -> int:
    """Extract an integer task-runtime row id from a row projection."""
    if not isinstance(row, dict):
        return 0
    try:
        return int(row.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _task_runtime_session_id(result: dict[str, Any] | None) -> str:
    """Extract the session id from a task-runtime transition result."""
    if not isinstance(result, dict):
        return ""
    session = result.get("session")
    if isinstance(session, dict):
        return str(session.get("session_id") or "").strip()
    return str(getattr(session, "session_id", "") or "").strip()


def _record_task_runtime_transition_failure(
    runtime: dict[str, Any],
    *,
    board_id: int,
    pm_status: str,
    reason: str,
    transition_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a failed TaskRuntime transition to the PM runtime projection."""

    normalized_reason = str(reason or "task_runtime_transition_failed").strip()
    if not normalized_reason:
        normalized_reason = "task_runtime_transition_failed"
    failure = {
        "success": False,
        "board_id": int(board_id or 0),
        "pm_status": str(pm_status or "").strip(),
        "reason": normalized_reason,
        "transition_result": dict(transition_result or {}),
    }
    existing = runtime.get("task_runtime_transition_failures")
    failures = existing if isinstance(existing, list) else []
    failures.append(failure)
    runtime["task_runtime_transition_failures"] = failures
    return failure


def _taskboard_runtime_transition_failures(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    """Return recorded TaskRuntime transition failures from the PM runtime projection."""

    failures = runtime.get("task_runtime_transition_failures")
    if not isinstance(failures, list):
        return []
    return [dict(item) for item in failures if isinstance(item, dict)]


def _source_task_by_pm_id(director_tasks: Sequence[dict[str, Any]], pm_task_id: str) -> dict[str, Any] | None:
    """Find the original Director task payload for one PM task id."""
    for item in director_tasks:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == pm_task_id:
            return item
    return None


def _build_taskboard_runtime(
    *,
    workspace_full: str,
    run_id: str,
    director_tasks: Sequence[dict[str, Any]],
    max_workers: int,
) -> dict[str, Any]:
    """Build the PM mainline dispatch runtime from task-runtime row APIs."""
    if not _taskboard_mainline_enabled():
        return {}
    payload = build_taskboard_sync_payload(list(director_tasks))
    if not payload:
        return {}

    task_runtime_workspace = _task_runtime_workspace(workspace_full=workspace_full, run_id=run_id)
    try:
        task_runtime = TaskRuntimeService(str(task_runtime_workspace))
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to create task-runtime PM mainline workspace, skipping integration: %s",
            exc,
        )
        return {}

    pm_id_to_row_id: dict[str, int] = {}
    row_id_to_task: dict[int, dict[str, Any]] = {}

    for entry in payload:
        pm_task_id = str(entry.get("task_id") or "").strip()
        if not pm_task_id:
            continue
        source_task = _source_task_by_pm_id(director_tasks, pm_task_id)
        if not isinstance(source_task, dict):
            continue
        row = task_runtime.create_task_row(
            subject=str(entry.get("title") or pm_task_id),
            description=str(entry.get("goal") or "").strip(),
            priority=_task_runtime_priority(int(entry.get("priority") or 5)),
            owner="PM",
            blocked_by=[],
            metadata={
                "pm_task_id": pm_task_id,
                "fingerprint": str(entry.get("metadata", {}).get("fingerprint") or "").strip(),
                "dependencies": list(entry.get("dependencies") or []),
                "source": "delivery.cli.pm.taskboard_mainline",
            },
        )
        row_id = _task_row_id(row)
        if row_id <= 0:
            continue
        pm_id_to_row_id[pm_task_id] = row_id
        row_id_to_task[row_id] = source_task

    for entry in payload:
        pm_task_id = str(entry.get("task_id") or "").strip()
        row_id_raw = pm_id_to_row_id.get(pm_task_id)
        if row_id_raw is None or row_id_raw <= 0:
            continue
        row_id = int(row_id_raw)
        deps = [
            pm_id_to_row_id[dep_id]
            for dep_id in (entry.get("dependencies") or [])
            if dep_id in pm_id_to_row_id
        ]
        task_runtime.update_task_row(
            row_id,
            blocked_by=list(dict.fromkeys(deps)),
            metadata={"resolved_depends_on_task_ids": list(dict.fromkeys(deps))},
        )

    workers = [f"director-worker-{index + 1}" for index in range(max(1, int(max_workers or 1)))]
    return {
        "task_runtime": task_runtime,
        "run_id": str(run_id or "").strip(),
        "task_runtime_workspace": str(task_runtime_workspace),
        "taskboard_root": str(task_runtime_workspace),
        "workers": workers,
        "worker_index": 0,
        "board_id_to_task": row_id_to_task,
        "pm_id_to_board_id": pm_id_to_row_id,
    }


def _select_taskboard_ready_batch(
    runtime: dict[str, Any],
    max_workers: int,
    dispatched_board_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Select and claim a ready batch of task-runtime rows."""
    task_runtime = runtime.get("task_runtime")
    if not isinstance(task_runtime, TaskRuntimeService):
        return []
    ready = task_runtime.list_ready_task_rows()
    if not isinstance(ready, list) or not ready:
        return []

    workers = runtime.get("workers")
    if not isinstance(workers, list) or not workers:
        workers = ["director-worker-1"]
        runtime["workers"] = workers
    worker_index = int(runtime.get("worker_index") or 0)
    board_id_to_task = runtime.get("board_id_to_task")
    if not isinstance(board_id_to_task, dict):
        return []

    selected: list[dict[str, Any]] = []
    selected_limit = max(1, int(max_workers or 1))
    for task_row in ready:
        if len(selected) >= selected_limit:
            break
        board_id = _task_row_id(task_row if isinstance(task_row, dict) else None)
        if dispatched_board_ids and board_id in dispatched_board_ids:
            continue
        source_task = board_id_to_task.get(board_id)
        if not isinstance(source_task, dict):
            continue
        worker_id = workers[worker_index % len(workers)]
        worker_index += 1
        claim_result = task_runtime.claim_execution(
            board_id,
            worker_id=worker_id,
            role_id="Director",
            run_id=str(runtime.get("run_id") or "").strip(),
            selection_source="delivery.cli.pm.taskboard_mainline",
            metadata={"pm_dispatch_worker_id": worker_id},
        )
        if not bool(isinstance(claim_result, dict) and claim_result.get("success") is True):
            continue
        selected.append(
            {
                "board_id": board_id,
                "worker_id": worker_id,
                "task": source_task,
                "task_runtime_session_id": _task_runtime_session_id(claim_result),
            }
        )
    runtime["worker_index"] = worker_index
    return selected


def _finalize_taskboard_runtime_entry(
    runtime: dict[str, Any],
    *,
    board_id: int,
    session_id: str,
    pm_status: str,
    metadata: dict[str, Any] | None = None,
    result_summary: str = "",
    failure_detail: str = "",
) -> dict[str, Any]:
    """Finalize one PM mainline task-runtime row from the Director outcome."""
    task_runtime = runtime.get("task_runtime")
    if not isinstance(task_runtime, TaskRuntimeService) or int(board_id or 0) <= 0:
        return {"success": False, "reason": "task_runtime_unavailable"}
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        update_result = task_runtime.update_task_row(
            board_id,
            metadata=dict(metadata or {}),
        )
        return _record_task_runtime_transition_failure(
            runtime,
            board_id=board_id,
            pm_status=pm_status,
            reason="session_missing",
            transition_result=update_result if isinstance(update_result, dict) else {},
        )

    status_token = str(pm_status or "").strip().lower()
    transition_metadata = dict(metadata or {})
    if status_token == "done":
        result = task_runtime.complete_execution(
            board_id,
            session_id=normalized_session_id,
            result_summary=result_summary,
            metadata=transition_metadata,
        )
    elif status_token == "needs_continue":
        result = task_runtime.suspend_execution(
            board_id,
            session_id=normalized_session_id,
            reason=result_summary or "Director requested follow-up work",
            metadata=transition_metadata,
        )
    else:
        result = task_runtime.fail_execution(
            board_id,
            session_id=normalized_session_id,
            error=failure_detail or result_summary or f"Director task ended with PM status {status_token or 'unknown'}",
            metadata=transition_metadata,
        )
    if isinstance(result, dict) and result.get("success") is True:
        return result
    transition_result = result if isinstance(result, dict) else {}
    return _record_task_runtime_transition_failure(
        runtime,
        board_id=board_id,
        pm_status=pm_status,
        reason=str(transition_result.get("reason") or "task_runtime_transition_failed"),
        transition_result=transition_result,
    )


__all__ = [
    "_build_taskboard_runtime",
    "_finalize_taskboard_runtime_entry",
    "_select_taskboard_ready_batch",
    "_taskboard_mainline_enabled",
    "_taskboard_runtime_transition_failures",
]
