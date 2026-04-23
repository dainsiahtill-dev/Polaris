"""Taskboard integration module for Polaris engine.

This module handles taskboard-based task selection and runtime management,
replacing traditional batch scheduling with dependency-aware taskboard queues.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.pm.tasks import build_taskboard_sync_payload

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_ROLE_TASKBOARD_MODULE = "_polaris_role_taskboard_mainline"
_TASKBOARD_PRIORITY_LEVELS = {
    0: "CRITICAL",
    1: "HIGH",
    2: "HIGH",
    3: "MEDIUM",
    4: "MEDIUM",
}


def _taskboard_mainline_enabled() -> bool:
    """Check if taskboard mainline is enabled."""
    token = str(os.environ.get("KERNELONE_DISABLE_TASKBOARD_MAINLINE", "0")).strip().lower()
    return token not in {"1", "true", "yes", "on"}


def _load_role_taskboard_module() -> Any | None:
    """Load the role taskboard module dynamically."""
    module_path = Path(__file__).resolve().parents[2] / "core" / "polaris_loop" / "role_agent" / "taskboard.py"
    if not module_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(_ROLE_TASKBOARD_MODULE, str(module_path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _taskboard_priority_enum(module: Any, priority: int) -> Any:
    """Get taskboard priority enum from module."""
    level = int(priority or 0)
    bucket = _TASKBOARD_PRIORITY_LEVELS.get(level)
    if bucket is None:
        bucket = "LOW" if level >= 7 else "MEDIUM"
    try:
        return getattr(module.TaskPriority, bucket)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to get TaskPriority.%s from module, falling back to MEDIUM: %s",
            bucket,
            exc,
        )
        return module.TaskPriority.MEDIUM


def _build_taskboard_runtime(
    *,
    workspace_full: str,
    run_id: str,
    director_tasks: Sequence[dict[str, Any]],
    max_workers: int,
) -> dict[str, Any]:
    """Build taskboard runtime with board and worker configuration."""
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    if not _taskboard_mainline_enabled():
        return {}
    module = _load_role_taskboard_module()
    if module is None:
        return {}
    payload = build_taskboard_sync_payload(list(director_tasks))
    if not payload:
        return {}

    taskboard_root = (
        Path(workspace_full).resolve()
        / get_workspace_metadata_dir_name()
        / "runtime"
        / "state"
        / "taskboard_mainline"
        / (str(run_id or "run").strip() or "run")
    )
    try:
        board = module.create_taskboard(root_dir=taskboard_root)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to create taskboard from module, skipping taskboard integration: %s",
            exc,
        )
        return {}

    pm_id_to_board_id: dict[str, int] = {}
    board_id_to_task: dict[int, dict[str, Any]] = {}

    # First pass: create all tasks with metadata.
    for entry in payload:
        pm_task_id = str(entry.get("task_id") or "").strip()
        if not pm_task_id:
            continue
        source_task = next(
            (
                item
                for item in director_tasks
                if isinstance(item, dict) and str(item.get("id") or "").strip() == pm_task_id
            ),
            None,
        )
        if not isinstance(source_task, dict):
            continue
        created = board.create(
            subject=str(entry.get("title") or pm_task_id),
            description=str(entry.get("goal") or "").strip(),
            priority=_taskboard_priority_enum(module, int(entry.get("priority") or 5)),
            owner="PM",
            blocked_by=[],
            metadata={
                "pm_task_id": pm_task_id,
                "fingerprint": str(entry.get("metadata", {}).get("fingerprint") or "").strip(),
                "dependencies": list(entry.get("dependencies") or []),
            },
        )
        pm_id_to_board_id[pm_task_id] = int(created.id)
        board_id_to_task[int(created.id)] = source_task

    # Second pass: hydrate dependencies now that all ids are known.
    for entry in payload:
        pm_task_id = str(entry.get("task_id") or "").strip()
        board_id = pm_id_to_board_id.get(pm_task_id)
        if not board_id:
            continue
        deps = [
            pm_id_to_board_id[dep_id] for dep_id in (entry.get("dependencies") or []) if dep_id in pm_id_to_board_id
        ]
        task_obj = board.get(board_id)
        if task_obj is None:
            continue
        task_obj.blocked_by = list(dict.fromkeys(deps))
        task_obj.status = module.TaskStatus.BLOCKED if task_obj.blocked_by else module.TaskStatus.PENDING
        # Keep parent -> children relation for unblocking behavior.
        for dep_id in task_obj.blocked_by:
            dep_obj = board.get(dep_id)
            if dep_obj is None:
                continue
            if board_id not in dep_obj.blocks:
                dep_obj.blocks.append(board_id)
                board._save_task(dep_obj)  # type: ignore[attr-defined]
        board._save_task(task_obj)  # type: ignore[attr-defined]

    workers = [f"director-worker-{index + 1}" for index in range(max(1, int(max_workers or 1)))]
    return {
        "module": module,
        "board": board,
        "taskboard_root": str(taskboard_root),
        "workers": workers,
        "worker_index": 0,
        "board_id_to_task": board_id_to_task,
        "pm_id_to_board_id": pm_id_to_board_id,
    }


def _select_taskboard_ready_batch(
    runtime: dict[str, Any],
    max_workers: int,
    dispatched_board_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Select ready batch of tasks from taskboard."""
    board = runtime.get("board")
    module = runtime.get("module")
    if board is None or module is None:
        return []
    ready = board.list_ready()
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
    for task_obj in ready:
        if len(selected) >= selected_limit:
            break
        board_id = int(getattr(task_obj, "id", 0) or 0)
        if dispatched_board_ids and board_id in dispatched_board_ids:
            continue
        source_task = board_id_to_task.get(board_id)
        if not isinstance(source_task, dict):
            continue
        worker_id = workers[worker_index % len(workers)]
        worker_index += 1
        if not bool(board.claim(board_id, worker_id)):
            continue
        selected.append(
            {
                "board_id": board_id,
                "worker_id": worker_id,
                "task": source_task,
            }
        )
    runtime["worker_index"] = worker_index
    return selected


__all__ = [
    "_build_taskboard_runtime",
    "_load_role_taskboard_module",
    "_select_taskboard_ready_batch",
    "_taskboard_mainline_enabled",
    "_taskboard_priority_enum",
]
