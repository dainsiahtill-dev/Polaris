"""Runtime Projection Service - Single Source of Truth for Runtime State.

This module provides a unified runtime projection that consolidates all state sources
(PM local, Director local, workflow archive, engine phase) into a single snapshot.

Field Priority Matrix:
- pm.local = PMService.get_status (authoritative)
- director.local = DirectorService.get_status (authoritative)
- workflow.archive = get_workflow_runtime_status (fallback when local unavailable)
- engine.phase = engine.status.json (phase/detail fallback only)
- snapshot_compat = derived snapshot metadata, NOT source data

状态管理策略（重构后）：
- 投影缓存封装进 ProjectionCache 类，支持实例级隔离。
- 模块级 _default_cache 保持向后兼容；测试可创建独立 ProjectionCache 实例
  避免交叉污染。
- build_runtime_projection 新增可选 cache 参数，支持依赖注入。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from polaris.cells.docs.court_workflow.public.service import map_engine_to_court_state
from polaris.cells.runtime.projection.internal.constants import DEFAULT_WORKSPACE
from polaris.cells.runtime.projection.internal.io_helpers import (
    build_cache_root,
    get_git_status,
    get_lancedb_status,
    read_json,
    resolve_artifact_path,
)
from polaris.cells.runtime.projection.internal.workflow_status import (
    WORKFLOW_PM_TASKS_FILE,
    build_workflow_status_payload,
    build_workflow_task_rows,
    get_workflow_runtime_status,
)
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.memory.integration import (
    get_memory_store,
    get_reflection_store,
    init_anthropomorphic_modules,
)

logger = logging.getLogger(__name__)


class TaskSource(Enum):
    """Task list source selection."""

    WORKFLOW = "workflow"  # Prefer workflow tasks when available
    LOCAL_LIVE = "local_live"  # Local live tasks when workflow unavailable
    NONE = "none"  # No tasks available


@dataclass
class RuntimeProjection:
    """Unified runtime projection container."""

    # Core state sources
    pm_local: dict[str, Any] = field(default_factory=dict)
    director_local: dict[str, Any] = field(default_factory=dict)
    director_merged: dict[str, Any] = field(default_factory=dict)
    workflow_archive: dict[str, Any] | None = None
    engine_fallback: dict[str, Any] | None = None

    # Derived states
    court_state: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] | None = None
    success_stats: dict[str, Any] = field(default_factory=dict)
    anthro_state: dict[str, Any] | None = None
    lancedb: dict[str, Any] = field(default_factory=dict)
    resident: dict[str, Any] | None = None
    task_source: TaskSource = TaskSource.NONE
    task_rows: list[dict[str, Any]] = field(default_factory=list)


def _safe_int(value: Any) -> int:
    """Safely convert value to non-negative integer."""
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError) as exc:
        logger.warning("_safe_int: failed to convert %r: %s", value, exc)
        return 0


def _state_token(payload: dict[str, Any] | None) -> str:
    """Extract state token from payload."""
    if not isinstance(payload, dict):
        return ""
    # Check top-level state first
    token = str(payload.get("state") or "").strip().upper()
    if token:
        return token
    # Check nested status.state
    nested = payload.get("status")
    if isinstance(nested, dict):
        return str(nested.get("state") or "").strip().upper()
    return ""


def _task_totals(payload: dict[str, Any] | None) -> tuple[int, int]:
    """Get (total, active) task counts from payload."""
    if not isinstance(payload, dict):
        return (0, 0)
    # Check both top-level tasks and nested status.tasks
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        status = payload.get("status")
        if isinstance(status, dict):
            tasks = status.get("tasks")
    if not isinstance(tasks, dict):
        return (0, 0)
    total = _safe_int(tasks.get("total"))
    by_status = tasks.get("by_status")
    if not isinstance(by_status, dict):
        return (total, 0)
    active = (
        _safe_int(by_status.get("IN_PROGRESS"))
        + _safe_int(by_status.get("RUNNING"))
        + _safe_int(by_status.get("CLAIMED"))
    )
    return (total, active)


def _workflow_has_live_rows(payload: dict[str, Any] | None) -> bool:
    """Check if workflow has live task rows."""
    if not isinstance(payload, dict):
        return False
    # Check both top-level tasks and nested status.tasks
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        # Check nested in status
        status = payload.get("status")
        if isinstance(status, dict):
            tasks = status.get("tasks")
    if not isinstance(tasks, dict):
        return False
    rows = tasks.get("task_rows")
    if not isinstance(rows, list):
        return False
    live_tokens = {"RUNNING", "IN_PROGRESS", "CLAIMED", "COMPLETED", "FAILED", "BLOCKED"}
    for item in rows:
        if not isinstance(item, dict):
            continue
        token = str(item.get("status") or item.get("state") or "").strip().upper()
        if token in live_tokens:
            return True
    return False


def _parse_engine_updated_at(value: Any) -> float | None:
    """Parse engine status updated_at to epoch timestamp."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except (RuntimeError, ValueError) as exc:
        logger.warning("_parse_timestamp: failed to parse %r: %s", text, exc)
        return None


# =============================================================================
# PM Status - Authoritative Source
# =============================================================================


async def get_pm_local_status() -> dict[str, Any]:
    """Get PM status from PMService (authoritative source).

    Returns:
        Dict with keys: running, pid, mode, started_at, workflow
    """
    running = False
    pid = None
    mode = ""
    started_at = None
    workflow_status = None

    pm_status = None
    try:
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container

        container = await get_container()
        pm_service = await container.resolve_async(PMService)
        pm_status = pm_service.get_status()
    except (RuntimeError, ValueError) as e:
        logger.debug("PMService unavailable: %s", e)

    if pm_status and isinstance(pm_status, dict):
        running = bool(pm_status.get("running"))
        pid = pm_status.get("pid")
        mode = str(pm_status.get("mode", ""))
        started_at = pm_status.get("started_at")

    return {
        "running": running,
        "pid": pid,
        "mode": mode or ("workflow" if workflow_status else ""),
        "started_at": started_at,
        "workflow": workflow_status,
    }


# =============================================================================
# Director Status - Authoritative Source (Local)
# =============================================================================


async def get_director_local_status() -> dict[str, Any]:
    """Get Director status from DirectorService (authoritative local source).

    Returns:
        Dict with keys: running, pid, mode, started_at, log_path, source, status
    """
    try:
        from polaris.cells.director.execution.service import DirectorService
        from polaris.infrastructure.di.container import get_container

        container = await get_container()
        di_service = await container.resolve_async(DirectorService)
        v2_status = await di_service.get_status()

        state = str(v2_status.get("state") or "").strip().upper()
        running = state == "RUNNING"

        return {
            "running": running,
            "pid": None,
            "mode": "v2_service",
            "started_at": None,
            "log_path": "",
            "source": "v2_service",
            "status": v2_status if isinstance(v2_status, dict) else None,
        }
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.warning(
            "get_director_local_status: DirectorService unavailable during projection: %s",
            exc,
            exc_info=True,
        )
        return {
            "running": False,
            "pid": None,
            "mode": "",
            "started_at": None,
            "log_path": "",
            "source": "none",
            "status": None,
            "projection_error": str(exc),
        }


def get_workflow_director_status_sync(
    workspace: str,
    cache_root: str,
) -> dict[str, Any] | None:
    """Get Director status from workflow archive (sync version).

    Returns:
        Dict with keys: running, pid, mode, started_at, log_path, source, workflow_id, status
    """
    try:
        workflow_status = get_workflow_runtime_status(workspace, cache_root)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "get_workflow_director_status_sync: workflow archive read failed for workspace=%r: %s",
            workspace,
            exc,
            exc_info=True,
        )
        return None

    if not isinstance(workflow_status, dict):
        return None

    status_payload = build_workflow_status_payload(
        workflow_status,
        workspace=workspace,
        cache_root=cache_root,
    )
    if not isinstance(status_payload, dict):
        return None

    running = str(status_payload.get("state") or "").strip().upper() == "RUNNING"
    return {
        "running": running,
        "pid": None,
        "mode": "workflow",
        "started_at": None,
        "log_path": "",
        "source": "workflow",
        "workflow_id": str(
            workflow_status.get("director_workflow_id") or workflow_status.get("workflow_id") or ""
        ).strip(),
        "status": status_payload,
        "tasks": status_payload.get("tasks") if isinstance(status_payload.get("tasks"), dict) else {},
        "raw_workflow_status": workflow_status,
    }


async def get_workflow_director_status(
    workspace: str,
    cache_root: str,
) -> dict[str, Any] | None:
    """Get Director status from workflow archive (async version)."""
    return await asyncio.to_thread(
        get_workflow_director_status_sync,
        workspace,
        cache_root,
    )


# =============================================================================
# Director Status Merge - Single Implementation
# =============================================================================


def merge_director_status(
    local_status: dict[str, Any] | None,
    workflow_status: dict[str, Any] | None,
    workflow_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge Director status with single source of truth logic.

    Priority Rules:
    1. If local Director is running with live tasks AND workflow is stale/missing,
       use local as authoritative
    2. Otherwise use workflow as source of truth
    3. Fill missing fields from the other source

    This is the SINGLE implementation for Director status merging.
    """
    local_payload = local_status if isinstance(local_status, dict) else {}
    workflow_payload = workflow_status if isinstance(workflow_status, dict) else {}

    if not workflow_payload:
        result = dict(local_payload)
        result.setdefault("source", "v2_service" if local_payload else "none")
        # Preserve workflow_id from local if present
        if local_payload.get("workflow_id"):
            result["workflow_id"] = local_payload.get("workflow_id")
        return result

    local_state = _state_token(local_payload)
    workflow_state = _state_token(workflow_payload)
    local_running = bool(local_payload.get("running")) or local_state == "RUNNING"
    workflow_running = workflow_state == "RUNNING"
    local_total, local_active = _task_totals(local_payload)
    workflow_has_live_rows = _workflow_has_live_rows(workflow_payload)

    # Rule 1: Local Director runtime owns real-time task execution state while running
    # If workflow snapshot is queued/stale, do not let it overwrite live task rows
    if local_running and (local_total > 0 or local_active > 0) and (not workflow_running or not workflow_has_live_rows):
        merged_local = dict(local_payload)
        local_metrics: dict[str, Any] = (
            cast("dict[str, Any]", merged_local.get("metrics")) if isinstance(merged_local.get("metrics"), dict) else {}
        )
        workflow_metrics: dict[str, Any] = (
            cast("dict[str, Any]", workflow_payload.get("metrics"))
            if isinstance(workflow_payload.get("metrics"), dict)
            else {}
        )
        workflow_id = str(workflow_metrics.get("workflow_id") or "").strip()
        if workflow_id:
            local_metrics.setdefault("workflow_id", workflow_id)
        if local_metrics:
            merged_local["metrics"] = local_metrics
        merged_local.setdefault("mode", str(local_payload.get("mode") or "v2_service"))
        merged_local["source"] = str(local_payload.get("source") or "v2_service")
        merged_local["running"] = True
        # Preserve workflow_id from workflow
        if workflow_payload.get("workflow_id"):
            merged_local["workflow_id"] = workflow_payload.get("workflow_id")
        return merged_local

    # Rule 2: Use workflow as source of truth
    merged = dict(workflow_payload)
    merged["source"] = "workflow"
    merged["mode"] = "workflow"

    # Merge token_budget
    local_token_budget: dict[str, Any] = (
        cast("dict[str, Any]", local_payload.get("token_budget"))
        if isinstance(local_payload.get("token_budget"), dict)
        else {}
    )
    workflow_token_budget: dict[str, Any] = (
        cast("dict[str, Any]", merged.get("token_budget")) if isinstance(merged.get("token_budget"), dict) else {}
    )
    merged["token_budget"] = {
        **local_token_budget,
        **workflow_token_budget,
    }

    # Merge workers
    local_workers: dict[str, Any] = (
        cast("dict[str, Any]", local_payload.get("workers")) if isinstance(local_payload.get("workers"), dict) else {}
    )
    workflow_workers: dict[str, Any] = (
        cast("dict[str, Any]", merged.get("workers")) if isinstance(merged.get("workers"), dict) else {}
    )
    if workflow_workers:
        merged["workers"] = {**local_workers, **workflow_workers}
    elif local_workers:
        merged["workers"] = local_workers

    # Override state if local is RUNNING
    if local_state == "RUNNING":
        merged["state"] = "RUNNING"

    merged.setdefault("workspace", str(local_payload.get("workspace") or "").strip())

    # Fill missing fields from local
    for key in ("pid", "started_at", "log_path"):
        if merged.get(key) in (None, ""):
            merged[key] = local_payload.get(key)

    merged["running"] = (
        bool(local_payload.get("running")) or bool(workflow_payload.get("running")) or merged.get("state") == "RUNNING"
    )

    return merged


# =============================================================================
# Task List Selection - Single Source
# =============================================================================


def select_task_rows(
    workflow_tasks: list[dict[str, Any]] | None,
    local_status: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], TaskSource]:
    """Select task rows following "二选一，不做跨源混拼" rule.

    Selection Rules:
    1. If workflow rows exist and are valid (non-empty), use workflow tasks
    2. Else if local Director is running and has local task_rows, use local live tasks
    3. Otherwise return empty list

    Returns:
        Tuple of (selected task rows, source indicator)
    """
    # Rule 1: If workflow rows exist and have content, use them
    if workflow_tasks and len(workflow_tasks) > 0:
        return workflow_tasks, TaskSource.WORKFLOW

    # Rule 2: Check local live tasks when workflow unavailable
    if local_status and isinstance(local_status, dict):
        local_running = bool(local_status.get("running"))
        local_state = _state_token(local_status)
        is_running = local_running or local_state == "RUNNING"

        if is_running:
            status = local_status.get("status")
            if isinstance(status, dict):
                tasks = status.get("tasks")
                if isinstance(tasks, dict):
                    local_task_rows = tasks.get("task_rows")
                    if isinstance(local_task_rows, list) and len(local_task_rows) > 0:
                        return local_task_rows, TaskSource.LOCAL_LIVE

    # Rule 3: No tasks available
    return [], TaskSource.NONE


# =============================================================================
# Engine Status - Fallback Only
# =============================================================================


def build_engine_status(
    workspace: str,
    cache_root: str,
    pm_status: dict[str, Any] | None = None,
    director_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build engine status from engine.status.json (fallback only).

    This is NOT an authoritative source - only used for phase/detail fallback.
    """
    path = resolve_artifact_path(workspace, cache_root, "runtime/status/engine.status.json")
    if not path or not os.path.isfile(path):
        return None

    payload = read_json(path)
    if not isinstance(payload, dict):
        return None

    running = bool(payload.get("running"))
    phase = str(payload.get("phase") or "").strip().lower()
    pm_running = bool((pm_status or {}).get("running"))
    director_running = bool((director_status or {}).get("running"))
    updated_epoch = _parse_engine_updated_at(payload.get("updated_at"))

    stale_running = (
        running
        and phase in {"planning", "dispatching", "running", "in_progress"}
        and not pm_running
        and not director_running
        and (updated_epoch is None or (time.time() - float(updated_epoch)) > 15)
    )

    if stale_running:
        # Projection is read-only; mark stale and let state-owner handle cleanup.
        payload = dict(payload)
        payload["stale"] = True
        payload["running"] = False
        payload["phase"] = "failed"
        payload["error"] = str(payload.get("error") or "ENGINE_ORPHANED").strip()
        roles = payload.get("roles")
        if isinstance(roles, dict):
            for role_payload in roles.values():
                if not isinstance(role_payload, dict):
                    continue
                role_payload["running"] = False
                role_status = str(role_payload.get("status") or "").strip().lower()
                if role_status in {"running", "pending", "planning", "dispatching"}:
                    role_payload["status"] = "blocked"
                role_payload["detail"] = str(
                    role_payload.get("detail") or "Recovered from orphaned engine state"
                ).strip()

    payload.setdefault("path", path)
    return payload


# =============================================================================
# Anthropomorphic State
# =============================================================================


def build_anthro_state(state: AppState) -> dict[str, Any] | None:
    """Build memory state (memory, reflection)."""
    try:
        base_dir = state.settings.ramdisk_root or state.settings.workspace or DEFAULT_WORKSPACE
        base_dir_str = str(base_dir) if base_dir else DEFAULT_WORKSPACE
        init_anthropomorphic_modules(base_dir_str)
        mem_store = get_memory_store()
        ref_store = get_reflection_store()
        if not mem_store:
            return None
        last_step = 0
        total_reflections = 0
        if ref_store:
            last_step = ref_store.get_last_reflection_step()
            total_reflections = len(ref_store.reflections)
        recent_errors = mem_store.count_recent_errors(last_step)
        total_memories = len(mem_store.memories)
        return {
            "last_reflection_step": last_step,
            "recent_error_count": recent_errors,
            "total_memories": total_memories,
            "total_reflections": total_reflections,
        }
    except (RuntimeError, ValueError) as exc:
        # Anthropomorphic module is optional; log at debug so it does not flood
        # production logs when the subsystem is simply not initialised.
        logger.debug("build_anthro_state: optional module unavailable: %s", exc)
        return None


def build_resident_state(
    workspace: str,
    *,
    include_details: bool = False,
) -> dict[str, Any] | None:
    """Build resident state snapshot when the resident subsystem is available.

    Phase 1.2: Includes goal_executions projection for real-time progress tracking.
    """
    if not str(workspace or "").strip():
        return None
    try:
        from polaris.cells.resident.autonomy.public.service import get_resident_service

        service = get_resident_service(workspace)
        status = service.get_status(include_details=include_details)

        # Phase 1.2: Add goal execution projections for approved/materialized goals
        try:
            goal_executions = service.list_goal_executions()
            if goal_executions:
                status["goal_executions"] = goal_executions
        except (RuntimeError, ValueError) as exc:
            # list_goal_executions is Phase 1.2 optional extension; log at debug.
            logger.debug(
                "build_resident_state: list_goal_executions unavailable for workspace=%r: %s",
                workspace,
                exc,
            )

        return status
    except (RuntimeError, ValueError) as exc:
        # Resident subsystem is optional - not installed in all deployments.
        logger.debug("build_resident_state: resident subsystem unavailable: %s", exc)
        return None


# =============================================================================
# ProjectionCache 类（封装缓存状态，线程安全）
# =============================================================================


class ProjectionCache:
    """投影缓存，封装 workspace -> projection 映射及 TTL 控制。

    测试可直接实例化独立缓存，避免与默认缓存产生交叉污染。

    Args:
        ttl_seconds: 缓存有效期（秒），默认 2.0。
    """

    def __init__(self, ttl_seconds: float = 2.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[str, Any] = {}

    def get(self, workspace: str) -> RuntimeProjection | None:
        """获取有效缓存，过期则返回 None。"""
        cache_key = str(workspace or "").strip()
        if not cache_key:
            return None
        with self._lock:
            cached = self._store.get(cache_key)
        if cached is None:
            return None
        if (time.time() - cached.get("_timestamp", 0)) > self._ttl:
            return None
        return cached.get("projection")

    def set(self, workspace: str, projection: RuntimeProjection) -> None:
        """写入缓存并打上时间戳。"""
        cache_key = str(workspace or "").strip()
        if not cache_key:
            return
        with self._lock:
            self._store[cache_key] = {
                "projection": projection,
                "_timestamp": time.time(),
            }

    def invalidate(self, workspace: str) -> None:
        """使指定 workspace 的缓存失效。"""
        cache_key = str(workspace or "").strip()
        if not cache_key:
            return
        with self._lock:
            self._store.pop(cache_key, None)

    def clear(self) -> None:
        """清空所有缓存（测试 teardown 用）。"""
        with self._lock:
            self._store.clear()


# 模块级默认缓存实例（向后兼容）
_default_cache = ProjectionCache(ttl_seconds=2.0)


def _get_cached_projection(workspace: str) -> RuntimeProjection | None:
    """Get cached projection if still valid (向后兼容包装)."""
    return _default_cache.get(workspace)


def _set_cached_projection(workspace: str, projection: RuntimeProjection) -> None:
    """Cache projection with timestamp (向后兼容包装)."""
    _default_cache.set(workspace, projection)


def invalidate_projection_cache(workspace: str) -> None:
    """Invalidate projection cache for workspace."""
    _default_cache.invalidate(workspace)


# =============================================================================
# Main Entry Point: Unified Runtime Projection
# =============================================================================


async def build_runtime_projection(
    state: AppState,
    workspace: str,
    cache_root: str,
    *,
    use_cache: bool = True,
    cache: ProjectionCache | None = None,
) -> RuntimeProjection:
    """Build unified runtime projection from all sources.

    This is the SINGLE entry point for generating runtime snapshots.
    All API/WebSocket endpoints should use this function.

    Args:
        state: AppState for additional context
        workspace: Workspace path
        cache_root: Cache root path
        use_cache: Whether to use caching (default True for high-frequency calls)
        cache: Optional custom ProjectionCache instance (default: module-level cache).
               Pass an independent ProjectionCache() in tests to avoid cross-test pollution.
    """
    active_cache = cache if cache is not None else _default_cache

    # Check cache first for high-frequency scenarios
    if use_cache:
        cached = active_cache.get(workspace)
        if cached is not None:
            return cached

    # Step 1: Get PM status (authoritative) - fast (< 10ms)
    pm_status = await get_pm_local_status()

    # Step 2: Get Director local status (authoritative for local) - fast (< 10ms)
    director_local_status = await get_director_local_status()

    # Step 3: Get workflow status (fallback) - potentially slow during Factory execution
    # Use timeout to prevent blocking during high-load scenarios
    workflow_director_status = None
    try:
        workflow_director_status = await asyncio.wait_for(
            get_workflow_director_status(workspace, cache_root),
            timeout=5.0,  # 5 second timeout for workflow status
        )
    except asyncio.TimeoutError:
        # During Factory execution, workflow queries may timeout.
        # Use local Director status as fallback. Log at INFO since this is
        # operationally significant (factory runs may be delayed).
        logger.info(
            "build_runtime_projection: get_workflow_director_status timed out (workspace=%r), "
            "falling back to local Director status only",
            workspace,
        )
        workflow_director_status = None

    # Step 4: Get workflow tasks for task selection
    workflow_tasks: list[dict[str, Any]] = []
    if workflow_director_status:
        try:
            raw_workflow_status = (
                workflow_director_status.get("raw_workflow_status")
                if isinstance(workflow_director_status.get("raw_workflow_status"), dict)
                else workflow_director_status
            )
            workflow_tasks = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: build_workflow_task_rows(
                        raw_workflow_status,
                        workspace=workspace,
                        cache_root=cache_root,
                    )
                ),
                timeout=5.0,  # 5 second timeout for task rows
            )
        except asyncio.TimeoutError:
            logger.warning(
                "build_runtime_projection: build_workflow_task_rows timed out (workspace=%r)",
                workspace,
            )
            workflow_tasks = []
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "build_runtime_projection: build_workflow_task_rows failed (workspace=%r): %s",
                workspace,
                exc,
                exc_info=True,
            )
            workflow_tasks = []

    # Step 5: Merge Director status using single implementation
    merged_director_status = merge_director_status(
        director_local_status,
        workflow_director_status,
        workflow_tasks,
    )

    # Step 6: Select task rows (二选一规则)
    task_rows, task_source = select_task_rows(
        workflow_tasks if isinstance(workflow_tasks, list) else None,
        director_local_status,
    )

    # Step 7: Build engine status (fallback only)
    engine_status = build_engine_status(
        workspace,
        cache_root,
        pm_status=pm_status,
        director_status=merged_director_status,
    )

    # Step 8: Build court state
    court_state = map_engine_to_court_state(
        engine_status=engine_status,
        pm_status=pm_status,
        director_status=merged_director_status,
    )

    # Step 9: Build other payloads (local imports to avoid circular dependencies)
    from .artifacts import build_memory_payload, build_success_stats_payload

    lancedb = get_lancedb_status()

    # These can also be slow during high load - wrap with timeouts
    memory = None
    success_stats = {}
    anthro_state = None
    resident = None

    with contextlib.suppress(asyncio.TimeoutError):
        memory = await asyncio.wait_for(
            asyncio.to_thread(lambda: build_memory_payload(workspace, cache_root)),
            timeout=2.0,
        )

    with contextlib.suppress(asyncio.TimeoutError):
        success_stats = await asyncio.wait_for(
            asyncio.to_thread(lambda: build_success_stats_payload(workspace, cache_root)),
            timeout=2.0,
        )

    with contextlib.suppress(asyncio.TimeoutError):
        anthro_state = await asyncio.wait_for(
            asyncio.to_thread(lambda: build_anthro_state(state)),
            timeout=2.0,
        )

    with contextlib.suppress(asyncio.TimeoutError):
        resident = await asyncio.wait_for(
            asyncio.to_thread(lambda: build_resident_state(workspace)),
            timeout=2.0,
        )

    projection = RuntimeProjection(
        pm_local=pm_status,
        director_local=director_local_status,
        director_merged=merged_director_status,
        workflow_archive=workflow_director_status,
        engine_fallback=engine_status,
        court_state=court_state,
        snapshot={},
        memory=memory,
        success_stats=success_stats,
        anthro_state=anthro_state,
        lancedb=lancedb,
        resident=resident,
        task_source=task_source,
        task_rows=task_rows,
    )
    projection.snapshot = build_snapshot_payload_from_projection(
        projection,
        state=state,
        workspace=workspace,
        cache_root=Path(cache_root) if cache_root else None,
    )

    # Cache the projection for high-frequency scenarios
    if use_cache:
        active_cache.set(workspace, projection)

    return projection


def build_runtime_projection_sync(
    state: AppState,
    workspace: str,
    cache_root: str,
    *,
    use_cache: bool = True,
) -> RuntimeProjection:
    """Synchronous wrapper for build_runtime_projection.

    This function safely handles both sync and async contexts without
    creating nested event loops which can cause deadlocks.

    Args:
        state: AppState for additional context
        workspace: Workspace path
        cache_root: Cache root path
        use_cache: Whether to use caching (default True for high-frequency calls)
    """

    def _run_projection() -> RuntimeProjection:
        return asyncio.run(
            build_runtime_projection(
                state,
                workspace,
                cache_root,
                use_cache=use_cache,
            )
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread - safe to run directly.
        logger.debug("run_projection_async: no running event loop, executing directly (workspace=%r)", workspace)
        return _run_projection()

    # Running inside an event-loop thread. Avoid scheduling back onto the same loop,
    # which can deadlock when callers use this sync bridge from async routes.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_projection)
        return future.result(timeout=35)


# =============================================================================
# Phase 1: RuntimeProjectionService - Single Source of Truth
# =============================================================================


class RuntimeProjectionService:
    """Service for building runtime projections.

    This is the SINGLE entry point for generating runtime snapshots.
    All API/WebSocket endpoints should use this service.
    """

    @staticmethod
    def build(
        workspace: str,
        cache_root: Path | None = None,
        state: AppState | None = None,
        *,
        use_cache: bool = True,
    ) -> RuntimeProjection:
        """Build runtime projection synchronously.

        This is a thin wrapper around build_runtime_projection_sync().
        All projection logic is consolidated in the standalone functions above.

        Args:
            workspace: Workspace path
            cache_root: Optional cache root path
            state: Optional AppState for additional context shared by callers
            use_cache: Whether to use caching (default True for high-frequency calls)

        Returns:
            RuntimeProjection with all state sources
        """
        # Build cache root if not provided
        if cache_root is None:
            cache_root = Path(build_cache_root("", workspace))

        # Use the canonical sync builder - single source of truth
        if state is not None:
            return build_runtime_projection_sync(state, workspace, str(cache_root), use_cache=use_cache)

        # Create minimal AppState if not provided
        from polaris.bootstrap.config import Settings

        minimal_state = AppState(settings=Settings(workspace=Path(workspace)))
        return build_runtime_projection_sync(minimal_state, workspace, str(cache_root), use_cache=use_cache)

    @staticmethod
    async def build_async(
        workspace: str,
        cache_root: Path | None = None,
        state: AppState | None = None,
        *,
        use_cache: bool = True,
    ) -> RuntimeProjection:
        """Build runtime projection in async call sites without sync bridging."""
        if cache_root is None:
            cache_root = Path(build_cache_root("", workspace))

        if state is None:
            from polaris.bootstrap.config import Settings

            state = AppState(settings=Settings(workspace=Path(workspace)))

        return await build_runtime_projection(
            state,
            workspace,
            str(cache_root),
            use_cache=use_cache,
        )


def build_snapshot_payload_from_projection(
    projection: RuntimeProjection, state: Any = None, workspace: str = "", cache_root: Path | None = None
) -> dict[str, Any]:
    """从 projection 生成 /state/snapshot 兼容载荷"""
    from datetime import timezone

    compat = _derive_compat_fields(projection)
    resolved_cache_root = str(cache_root or "").strip()
    if not resolved_cache_root and workspace:
        try:
            settings = getattr(state, "settings", None)
            ramdisk_root = str(getattr(settings, "ramdisk_root", "") or "")
            resolved_cache_root = build_cache_root(ramdisk_root, workspace)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "build_snapshot_payload_from_projection: cache_root resolution failed for workspace=%r: %s",
                workspace,
                exc,
            )
    pm_contract_payload: dict[str, Any] = {}
    if workspace and resolved_cache_root:
        pm_contract_path = resolve_artifact_path(
            workspace,
            resolved_cache_root,
            WORKFLOW_PM_TASKS_FILE,
        )
        loaded_pm_contract = read_json(pm_contract_path)
        if isinstance(loaded_pm_contract, dict):
            pm_contract_payload = dict(loaded_pm_contract)

    tasks: list[dict[str, Any]] = []
    runtime_task_rows = load_runtime_task_rows(workspace)
    if runtime_task_rows:
        tasks = runtime_task_rows
    else:
        tasks = projection.task_rows or select_task_rows_from_projection(projection)
        if not tasks:
            base_tasks = pm_contract_payload.get("tasks")
            if isinstance(base_tasks, list):
                tasks = [dict(item) for item in base_tasks if isinstance(item, dict)]

    pm_state: dict[str, Any] = {}
    if workspace and resolved_cache_root:
        pm_state_path = resolve_artifact_path(
            workspace,
            resolved_cache_root,
            "runtime/state/pm.state.json",
        )
        loaded_pm_state = read_json(pm_state_path)
        if isinstance(loaded_pm_state, dict):
            pm_state = dict(loaded_pm_state)
    director_result: dict[str, Any] = {}
    if workspace and resolved_cache_root:
        director_result_path = resolve_artifact_path(
            workspace,
            resolved_cache_root,
            "runtime/results/director.result.json",
        )
        loaded_director_result = read_json(director_result_path)
        if isinstance(loaded_director_result, dict):
            director_result = loaded_director_result
    if not str(pm_state.get("last_director_status") or "").strip() and compat.get("director_status"):
        pm_state["last_director_status"] = compat["director_status"]
    director_result_status = str(director_result.get("status") or "").strip()
    if director_result_status and str(pm_state.get("last_director_status") or "").strip().lower() in {
        "",
        "idle",
        "pending",
    }:
        pm_state["last_director_status"] = director_result_status
    workflow_completed_tasks = _safe_int(compat.get("workflow_completed_tasks"))
    director_result_successes = _safe_int(
        director_result.get("successes") or director_result.get("completed") or director_result.get("completed_tasks")
    )
    existing_completed_tasks = _safe_int(pm_state.get("completed_task_count"))
    projected_completed_tasks = max(workflow_completed_tasks, director_result_successes)
    if projected_completed_tasks > existing_completed_tasks:
        pm_state["completed_task_count"] = projected_completed_tasks
    elif "completed_task_count" not in pm_state and compat.get("workflow_tasks") is not None:
        pm_state["completed_task_count"] = compat.get("workflow_tasks")

    # Keep git state in the snapshot payload consumed by the current UI.
    try:
        git_status = get_git_status(workspace)
    except (RuntimeError, ValueError) as exc:
        logger.warning("build_snapshot_payload_from_projection: git_status failed: %s", exc)
        git_status = {}

    # Load plan_text and agents_content from files (independent of PM running)
    plan_text = ""
    plan_mtime = None
    agents_content = ""
    agents_mtime = None
    if workspace and resolved_cache_root:
        # Load plan.md
        plan_path = resolve_artifact_path(workspace, resolved_cache_root, "runtime/contracts/plan.md")
        if plan_path and os.path.isfile(plan_path):
            try:
                with open(plan_path, encoding="utf-8") as f:
                    plan_text = f.read()
                plan_mtime = os.path.getmtime(plan_path)
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "build_snapshot_payload_from_projection: failed to read plan.md at %r: %s",
                    plan_path,
                    exc,
                )

        # Load agents.generated.md
        agents_path = resolve_artifact_path(workspace, resolved_cache_root, "runtime/contracts/agents.generated.md")
        if agents_path and os.path.isfile(agents_path):
            try:
                with open(agents_path, encoding="utf-8") as f:
                    agents_content = f.read()
                agents_mtime = os.path.getmtime(agents_path)
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "build_snapshot_payload_from_projection: failed to read agents.generated.md at %r: %s",
                    agents_path,
                    exc,
                )

    return {
        "pm": projection.pm_local,
        "director": projection.director_merged or projection.director_local,
        "workflow": projection.workflow_archive,
        "engine": projection.engine_fallback,
        "run_id": str(compat.get("run_id") or pm_contract_payload.get("run_id") or "").strip(),
        "tasks": tasks,
        "pm_state": pm_state or None,
        "git": git_status,
        "snapshot_compat": compat,
        "resident": projection.resident,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Add plan_text and agents_content
        "plan_text": plan_text,
        "plan_mtime": plan_mtime,
        "agents_content": agents_content,
        "agents_mtime": agents_mtime,
        "plan_text_normalized": False,  # Plan text loaded from file needs normalization
    }


def _derive_compat_fields(projection: RuntimeProjection) -> dict[str, Any]:
    """Derive snapshot metadata fields from the current projection."""

    def _director_state(payload: dict[str, Any]) -> str:
        status_value = payload.get("status")
        if isinstance(status_value, dict):
            nested_state = str(status_value.get("state") or "").strip()
            if nested_state:
                return nested_state
        elif status_value:
            return str(status_value).strip()
        state_token = str(payload.get("state") or "").strip()
        if state_token:
            return state_token
        return "running" if bool(payload.get("running")) else "idle"

    def _director_tasks(payload: dict[str, Any]) -> dict[str, Any]:
        direct_tasks = payload.get("tasks")
        if isinstance(direct_tasks, dict):
            return direct_tasks
        status_value = payload.get("status")
        nested_tasks = status_value.get("tasks") if isinstance(status_value, dict) else None
        return nested_tasks if isinstance(nested_tasks, dict) else {}

    def _completed_task_count(task_rows: list[dict[str, Any]], tasks_payload: dict[str, Any]) -> int:
        by_status = tasks_payload.get("by_status")
        if isinstance(by_status, dict):
            completed = _safe_int(by_status.get("COMPLETED") or by_status.get("completed"))
            if completed > 0:
                return completed
        return len(
            [
                item
                for item in task_rows
                if str(item.get("status") or item.get("state") or "").strip().upper() == "COMPLETED"
            ]
        )

    compat: dict[str, Any] = {}

    # PM status from pm_local
    pm_payload = projection.pm_local
    if pm_payload:
        compat["pm_status"] = pm_payload.get("status") or ("running" if pm_payload.get("running") else "idle")
        compat["pm_current_task"] = pm_payload.get("current_task_id") or pm_payload.get("task_id")

    # Director status from merged projection first, local fallback second.
    director_payload = projection.director_merged or projection.director_local
    if director_payload:
        compat["director_status"] = _director_state(director_payload)
        tasks_payload = _director_tasks(director_payload)
        by_status_raw = tasks_payload.get("by_status")
        by_status: dict[str, Any] = by_status_raw if isinstance(by_status_raw, dict) else {}
        compat["director_active"] = (
            director_payload.get("active_tasks")
            or tasks_payload.get("active")
            or _safe_int(by_status.get("IN_PROGRESS"))
            + _safe_int(by_status.get("RUNNING"))
            + _safe_int(by_status.get("CLAIMED"))
        )

    # Workflow archive precedence
    if projection.workflow_archive:
        compat["workflow_loaded"] = True
        workflow_task_rows = [item for item in projection.task_rows if isinstance(item, dict)]
        workflow_tasks_payload = _director_tasks(projection.workflow_archive)
        compat["workflow_tasks"] = len(workflow_task_rows) or _safe_int(workflow_tasks_payload.get("total"))
        compat["workflow_completed_tasks"] = _completed_task_count(workflow_task_rows, workflow_tasks_payload)
        # Include run_id from workflow if available
        if "run_id" in projection.workflow_archive:
            compat["run_id"] = projection.workflow_archive["run_id"]
        elif "workflow_id" in projection.workflow_archive:
            compat["run_id"] = projection.workflow_archive["workflow_id"]

    return compat


def select_task_rows_from_projection(projection: RuntimeProjection) -> list[dict[str, Any]]:
    """Select task rows using priority rules:

    1. If workflow archive has tasks: use workflow rows
    2. If workflow missing + local running: use local live rows
    3. If workflow stale + local terminal snapshot has tasks: keep local rows
    4. All unavailable: fallback to empty
    """
    workflow_tasks: list[dict[str, Any]] = []
    if projection.workflow_archive and isinstance(projection.workflow_archive.get("tasks"), list):
        workflow_tasks = projection.workflow_archive["tasks"]
    if workflow_tasks:
        return workflow_tasks

    local_payload = projection.director_local if isinstance(projection.director_local, dict) else {}
    local_task_rows = local_payload.get("task_rows")
    if not isinstance(local_task_rows, list):
        status_payload = local_payload.get("status")
        tasks_payload = status_payload.get("tasks") if isinstance(status_payload, dict) else {}
        local_task_rows = tasks_payload.get("task_rows") if isinstance(tasks_payload, dict) else None
    filtered_local_rows: list[dict[str, Any]] = [row for row in (local_task_rows or []) if isinstance(row, dict)]

    local_active_tasks = local_payload.get("active_tasks")
    if local_active_tasks is None:
        status_payload = local_payload.get("status")
        tasks_payload = status_payload.get("tasks") if isinstance(status_payload, dict) else {}
        if isinstance(tasks_payload, dict):
            local_active_tasks = tasks_payload.get("active")
    try:
        active_count = int(local_active_tasks or 0)
    except (TypeError, ValueError) as exc:
        # Data inconsistency from workspace projection — active_count is malformed.
        # Fall back to 0 but log as WARNING since this may cause the downstream
        # task-selection logic to incorrectly believe no tasks are active (WS事件丢失 root cause).
        # workspace is not available in this function - use placeholder
        logger.warning(
            "build_runtime_projection: active_count=%r is not parseable: %s. "
            "Defaulting to 0; downstream task selection may undercount.",
            local_active_tasks,
            exc,
        )
        active_count = 0

    local_running = bool(local_payload.get("running")) or _state_token(local_payload) == "RUNNING"
    if filtered_local_rows and (local_running or active_count > 0):
        return filtered_local_rows
    if filtered_local_rows:
        # Director 已结束时仍需保留本地任务快照，避免任务证据在终态被清空。
        return filtered_local_rows

    selected, _ = select_task_rows(workflow_tasks, local_payload)
    return selected


def load_runtime_task_rows(workspace: str) -> list[dict[str, Any]]:
    """Load canonical runtime task rows from runtime.state_owner storage."""
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []
    try:
        return TaskRuntimeService(workspace_token).list_task_rows()
    except (RuntimeError, ValueError) as exc:
        logger.debug("failed to load runtime task rows: %s", exc)
        return []
