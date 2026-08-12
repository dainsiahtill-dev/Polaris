"""Runtime Projection Service - Single Source of Truth for Runtime State.

This package is the lossless successor of the former ``runtime_projection_service``
module. It re-exports every previously-public symbol from the same import path so
that ``import ...runtime_projection_service`` and
``from ...runtime_projection_service import X`` keep resolving identically for all
external importers.

This package provides a unified runtime projection that consolidates all state sources
(PM local, Director local, workflow archive, engine phase) into a single snapshot.

Field Priority Matrix:
- pm.local = PMService.get_status (authoritative)
- director.local = DirectorService.get_status (authoritative)
- workflow.archive = get_workflow_runtime_status (fallback when local unavailable)
- engine.phase = engine.status.json (phase/detail fallback only)
- snapshot_derived = derived snapshot metadata, NOT source data

状态管理策略（重构后）：
- 投影缓存封装进 ProjectionCache 类，支持实例级隔离。
- 模块级 _default_cache 保持既有调用入口稳定；测试可创建独立 ProjectionCache 实例
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

from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    project_tool_lifecycle_failure_status,
    read_run_ledger_projection,
)
from polaris.cells.docs.court_workflow.public.service import map_engine_to_court_state
from polaris.cells.qa.audit_verdict.public import (
    QA_DEFAULT_TASK_BOUNDARY_FAILURE_CLASS,
    normalize_qa_failure_class,
    project_qa_failure_execution_state,
)
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
    get_workflow_runtime_status,
)
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.cells.workspace.integrity.public.service import read_workspace_status, workspace_has_docs
from polaris.kernelone.memory.integration import (
    get_memory_store,
    get_reflection_store,
    init_anthropomorphic_modules,
)

from ._helpers import (
    _TASK_OBSERVABLE_FIELDS,
    _TASK_OBSERVABLE_KEY_SUFFIXES,
    _TASK_OBSERVABLE_LIST_MAX_ITEMS,
    _TASK_OBSERVABLE_METADATA_FIELDS,
    _TASK_OBSERVABLE_SUMMARY_SCHEMA,
    _TASK_OBSERVABLE_TEXT_MAX_CHARS,
    _TASK_OBSERVABLE_UNSUPPORTED,
    _active_orchestration_state,
    _bounded_task_observable_value,
    _candidate_runtime_roots,
    _datetime_attr_isoformat,
    _dedupe_text_values,
    _director_status_for_runtime_task_row,
    _enrich_tasks_with_factory_blueprints,
    _enum_value,
    _int_value,
    _is_director_orchestration_snapshot,
    _is_pm_contract_path,
    _is_task_observable_key,
    _orchestration_task_rows,
    _ordered_string_union,
    _parse_engine_updated_at,
    _project_observable_task_row,
    _project_task_error_fields,
    _project_task_observable_fields,
    _read_factory_blueprints_by_task,
    _read_factory_latest_plan_tasks,
    _read_first_json_candidate,
    _read_first_text_candidate,
    _resolve_runtime_artifact_candidates,
    _runtime_root_from_pm_contract_path,
    _safe_int,
    _state_token,
    _task_totals,
    _timestamp_sort_value,
    _workflow_has_live_rows,
    _workspace_artifact_candidates,
    _workspace_tokens_match,
)
from ._models import RuntimeProjection, TaskSource, logger
from ._run_ledger import (
    _apply_run_ledger_director_status_overlay,
    _apply_run_ledger_task_rows_overlay,
    _extract_run_ledger_run_id,
    _latest_task_boundary,
    _read_run_ledger_projection_for_run,
    _row_task_id,
    _task_boundary_execution_state,
    _task_boundary_row_from_latest,
    _task_boundary_status_projection,
)
from ._snapshot_derive import _derive_projection_fields, _workspace_readiness_projection

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

    status_payload = dict(pm_status) if isinstance(pm_status, dict) else {}
    status_payload.update(
        {
            "running": running,
            "pid": pid,
            "mode": mode or ("workflow" if workflow_status else ""),
            "started_at": started_at,
            "workflow": workflow_status,
        }
    )
    return status_payload


# =============================================================================
# Director Status - Authoritative Source (Local)
# =============================================================================


async def get_director_local_status(workspace: str = DEFAULT_WORKSPACE) -> dict[str, Any]:
    """Get Director status from the bootstrap-bound owner observation port.

    Returns:
        Dict with keys: running, pid, mode, started_at, log_path, source, status
    """
    try:
        from polaris.cells.runtime.projection.internal.director_status_owner import (
            observe_director_status_owner,
        )

        observation = await observe_director_status_owner(workspace)
        v2_status = observation.status if observation.available else None
        if not isinstance(v2_status, dict):
            raise ValueError("Director status owner explicitly unavailable")

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
            "get_director_local_status: Director status owner unavailable during projection: %s",
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
            "projection_error": (f"{exc.error_code}: {exc}" if hasattr(exc, "error_code") else str(exc)),
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
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError) as exc:
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


def _runtime_task_rows_payload(workspace: str) -> dict[str, Any]:
    rows = load_runtime_task_rows(workspace)
    by_status: dict[str, int] = {}
    ready_queue_size = 0
    for row in rows:
        status_token = _director_status_for_runtime_task_row(row)
        by_status[status_token] = by_status.get(status_token, 0) + 1
        if str(row.get("status") or "").strip().lower() in {"pending", "ready"} and not row.get("blocked_by"):
            ready_queue_size += 1
    return {
        "total": len(rows),
        "by_status": by_status,
        "ready_queue_size": ready_queue_size,
        "task_rows": rows,
        "projection_source": "runtime.task_runtime",
    }


async def _list_recent_orchestration_runs(workspace: str, limit: int = 50) -> list[Any]:
    from polaris.cells.runtime.projection.internal.workflow_runtime_owner import (
        workflow_runtime_projection_owner_port,
    )

    return await workflow_runtime_projection_owner_port().list_runs(
        workspace=workspace,
        limit=limit,
    )


async def get_active_director_orchestration_status(workspace: str) -> dict[str, Any] | None:
    """Return active Director run state from the unified orchestration service."""

    try:
        snapshots = await _list_recent_orchestration_runs(workspace)
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.debug(
            "get_active_director_orchestration_status: list_runs failed for workspace=%r: %s",
            workspace,
            exc,
            exc_info=True,
        )
        return None

    active_statuses = {"pending", "running", "retrying", "blocked"}
    candidates: list[Any] = []
    for snapshot in snapshots:
        if not _is_director_orchestration_snapshot(snapshot, workspace):
            continue
        status_token = _enum_value(getattr(snapshot, "status", ""))
        if status_token.lower() in active_statuses:
            candidates.append(snapshot)

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: max(
            _timestamp_sort_value(getattr(item, "updated_at", None)),
            _timestamp_sort_value(getattr(item, "created_at", None)),
        ),
        reverse=True,
    )
    snapshot = candidates[0]
    status_token = _enum_value(getattr(snapshot, "status", ""))
    state, running = _active_orchestration_state(status_token)
    workflow_task_rows, workflow_by_status = _orchestration_task_rows(snapshot)
    run_id = str(getattr(snapshot, "run_id", "") or "").strip()
    workspace_token = str(getattr(snapshot, "workspace", "") or workspace or "").strip()
    tasks_payload = _runtime_task_rows_payload(workspace_token)
    workflow_tasks_payload = {
        "total": len(workflow_task_rows),
        "by_status": workflow_by_status,
        "task_rows": workflow_task_rows,
        "projection_source": "orchestration.workflow_runtime",
    }
    return {
        "running": running,
        "pid": None,
        "mode": "workflow",
        "started_at": _datetime_attr_isoformat(snapshot, "created_at"),
        "updated_at": _datetime_attr_isoformat(snapshot, "updated_at"),
        "log_path": "",
        "source": "orchestration_run",
        "workflow_id": run_id,
        "run_id": run_id,
        "state": state,
        "workspace": workspace_token,
        "status": {
            "state": state,
            "run_status": status_token.lower(),
            "workflow_id": run_id,
            "run_id": run_id,
            "current_phase": _enum_value(getattr(snapshot, "current_phase", "")),
            "overall_progress": getattr(snapshot, "overall_progress", 0.0),
            "tasks": tasks_payload,
        },
        "tasks": tasks_payload,
        "raw_workflow_status": {
            "source": "orchestration_run",
            "running": running,
            "workflow_id": run_id,
            "workflow_status": status_token.lower(),
            "workflow_tasks": workflow_tasks_payload,
        },
    }


# =============================================================================
# Director Status Merge - Single Implementation
# =============================================================================


def merge_director_status(
    local_status: dict[str, Any] | None,
    workflow_status: dict[str, Any] | None,
    workflow_tasks: list[dict[str, Any]] | None = None,
    run_ledger_projection: dict[str, Any] | None = None,
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
        return _apply_run_ledger_director_status_overlay(result, run_ledger_projection)

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
        return _apply_run_ledger_director_status_overlay(merged_local, run_ledger_projection)

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

    return _apply_run_ledger_director_status_overlay(merged, run_ledger_projection)


# =============================================================================
# Task List Selection - Single Source
# =============================================================================


def select_task_rows(
    runtime_projection_rows: list[dict[str, Any]] | None,
    local_status: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], TaskSource]:
    """Select live task rows from execution-control projections.

    Selection Rules:
    1. If local Director exposes runtime.task_runtime rows, use them.
    2. Else if the active orchestration projection exposes runtime.task_runtime
       rows, use them.
    3. Else if local Director is running and has local task_rows, use local live tasks.
    4. Otherwise return empty list.

    Workflow archive rows are historical evidence.  They must remain available
    under workflow_archive/raw_workflow_status, but they are not live execution
    task facts and must not populate RuntimeProjection.task_rows.

    Returns:
        Tuple of (selected task rows, source indicator)
    """
    if local_status and isinstance(local_status, dict):
        status = local_status.get("status")
        tasks = status.get("tasks") if isinstance(status, dict) else None
        if isinstance(tasks, dict):
            local_task_rows = tasks.get("task_rows")
            if (
                tasks.get("projection_source") == "runtime.task_runtime"
                and isinstance(local_task_rows, list)
                and len(local_task_rows) > 0
            ):
                return local_task_rows, TaskSource.TASK_RUNTIME

    if runtime_projection_rows and len(runtime_projection_rows) > 0:
        return runtime_projection_rows, TaskSource.TASK_RUNTIME

    # Rule 3: Check legacy local live tasks when task-runtime projection is unavailable.
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

    # Rule 4: No live execution-control tasks available.
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
    error_code = str(payload.get("error") or "").strip().upper()
    recovery_code = str(payload.get("recovery_code") or "").strip().upper()
    orphaned_recovered = error_code == "ENGINE_ORPHANED" or recovery_code == "ENGINE_ORPHANED"

    stale_running = (
        running
        and phase in {"planning", "dispatching", "running", "in_progress"}
        and not pm_running
        and not director_running
        and (updated_epoch is None or (time.time() - float(updated_epoch)) > 15)
    )

    if stale_running or orphaned_recovered:
        # Projection is read-only; stale "running" without a live owner means the
        # previous process disappeared after persisting an in-flight phase. This
        # is a recovered idle state, not a fresh execution failure.
        payload = dict(payload)
        payload["stale"] = True if stale_running else bool(payload.get("stale"))
        payload["orphaned"] = True
        payload["running"] = False
        payload["phase"] = "idle"
        payload["error"] = ""
        payload["recovery_code"] = "ENGINE_ORPHANED"
        roles = payload.get("roles")
        if isinstance(roles, dict):
            for role_payload in roles.values():
                if not isinstance(role_payload, dict):
                    continue
                role_payload["running"] = False
                role_status = str(role_payload.get("status") or "").strip().lower()
                if role_status in {"running", "pending", "planning", "dispatching", "blocked", "failed"}:
                    role_payload["status"] = "idle"
                role_payload["detail"] = str(role_payload.get("detail") or "Recovered stale engine state").strip()

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
    except (AttributeError, ImportError, RuntimeError, ValueError) as exc:
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
        except (AttributeError, RuntimeError, ValueError) as exc:
            # list_goal_executions is Phase 1.2 optional extension; log at debug.
            logger.debug(
                "build_resident_state: list_goal_executions unavailable for workspace=%r: %s",
                workspace,
                exc,
            )

        return status
    except (AttributeError, ImportError, RuntimeError, ValueError) as exc:
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


# 模块级默认缓存实例（既有调用入口）
_default_cache = ProjectionCache(ttl_seconds=2.0)


def _get_cached_projection(workspace: str) -> RuntimeProjection | None:
    """Get cached projection if still valid."""
    return _default_cache.get(workspace)


def _set_cached_projection(workspace: str, projection: RuntimeProjection) -> None:
    """Cache projection with timestamp."""
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
    director_local_status = await get_director_local_status(workspace)

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

    try:
        active_director_status = await asyncio.wait_for(
            get_active_director_orchestration_status(workspace),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        logger.info(
            "build_runtime_projection: get_active_director_orchestration_status timed out "
            "(workspace=%r), falling back to workflow archive/local status",
            workspace,
        )
        active_director_status = None

    if active_director_status:
        if workflow_director_status:
            active_director_status["archive_status"] = workflow_director_status
        workflow_director_status = active_director_status

    # Step 4: Get active task-runtime rows for task selection.
    # Workflow archive rows stay under workflow_archive/raw_workflow_status as
    # historical evidence; they are not live task facts.
    runtime_projection_rows: list[dict[str, Any]] = []
    if workflow_director_status and str(workflow_director_status.get("source") or "").strip() == "orchestration_run":
        tasks_payload = workflow_director_status.get("tasks")
        if isinstance(tasks_payload, dict) and tasks_payload.get("projection_source") == "runtime.task_runtime":
            active_rows = tasks_payload.get("task_rows")
            runtime_projection_rows = (
                [dict(item) for item in active_rows if isinstance(item, dict)] if isinstance(active_rows, list) else []
            )

    # Step 5: Merge Director status using single implementation.
    # Run Ledger terminal overlays are run-scoped. Without an explicit run id,
    # do not read a "latest runs" aggregate because it can project an unrelated
    # terminal verdict onto the current workspace snapshot.
    run_ledger_run_id = _extract_run_ledger_run_id(workflow_director_status, director_local_status)
    run_ledger_projection = _read_run_ledger_projection_for_run(workspace, run_ledger_run_id)
    merged_director_status = merge_director_status(
        director_local_status,
        workflow_director_status,
        runtime_projection_rows,
        run_ledger_projection,
    )

    # Step 6: Select task rows (二选一规则).  TaskRuntime/workflow payloads
    # are external read-model data; malformed rows must not take down the
    # whole runtime projection or silently become live tasks.
    try:
        task_rows, task_source = select_task_rows(
            runtime_projection_rows if isinstance(runtime_projection_rows, list) else None,
            director_local_status,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "build_runtime_projection: select_task_rows failed for workspace=%r: %s",
            workspace,
            exc,
        )
        task_rows, task_source = [], TaskSource.NONE
    task_rows = _apply_run_ledger_task_rows_overlay(task_rows, run_ledger_projection)

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
    from polaris.cells.runtime.artifact_store.public.service import (
        build_memory_payload,
        build_success_stats_payload,
    )

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
    """从 projection 生成 /state/snapshot 标准载荷。"""
    from datetime import timezone

    derived = _derive_projection_fields(projection)
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
    runtime_roots = _candidate_runtime_roots(projection, resolved_cache_root)
    pm_contract_payload = _read_first_json_candidate(
        _resolve_runtime_artifact_candidates(
            workspace,
            runtime_roots,
            WORKFLOW_PM_TASKS_FILE,
        )
    )

    tasks: list[dict[str, Any]] = []
    runtime_task_rows = load_runtime_task_rows(workspace)
    tasks_from_runtime_rows = bool(runtime_task_rows)
    if runtime_task_rows:
        tasks = runtime_task_rows
    else:
        tasks = projection.task_rows or select_task_rows_from_projection(projection)
        if not tasks:
            base_tasks = pm_contract_payload.get("tasks")
            if isinstance(base_tasks, list):
                tasks = [dict(item) for item in base_tasks if isinstance(item, dict)]
        if not tasks:
            tasks = _read_factory_latest_plan_tasks(workspace)
    tasks = _enrich_tasks_with_factory_blueprints(tasks, workspace)
    run_ledger_task_projection = (
        projection.director_merged.get("run_ledger_projection")
        if isinstance(projection.director_merged, dict)
        else None
    )
    if not tasks_from_runtime_rows:
        observable_task_rows = load_runtime_task_rows(workspace)
        if observable_task_rows:
            tasks = observable_task_rows
    tasks = _apply_run_ledger_task_rows_overlay(tasks, run_ledger_task_projection)

    pm_state = _read_first_json_candidate(
        _resolve_runtime_artifact_candidates(
            workspace,
            runtime_roots,
            "runtime/state/pm.state.json",
        )
    )
    director_result = _read_first_json_candidate(
        _resolve_runtime_artifact_candidates(
            workspace,
            runtime_roots,
            "runtime/results/director.result.json",
        )
    )
    if not str(pm_state.get("last_director_status") or "").strip() and derived.get("director_status"):
        pm_state["last_director_status"] = derived["director_status"]
    director_result_status = str(director_result.get("status") or "").strip()
    if director_result_status and str(pm_state.get("last_director_status") or "").strip().lower() in {
        "",
        "idle",
        "pending",
    }:
        pm_state["last_director_status"] = director_result_status
    workflow_completed_tasks = _safe_int(derived.get("workflow_completed_tasks"))
    director_result_successes = _safe_int(
        director_result.get("successes") or director_result.get("completed") or director_result.get("completed_tasks")
    )
    existing_completed_tasks = _safe_int(pm_state.get("completed_task_count"))
    projected_completed_tasks = max(workflow_completed_tasks, director_result_successes)
    if projected_completed_tasks > existing_completed_tasks:
        pm_state["completed_task_count"] = projected_completed_tasks
    elif "completed_task_count" not in pm_state and derived.get("workflow_tasks") is not None:
        pm_state["completed_task_count"] = derived.get("workflow_tasks")

    # Keep git state in the snapshot payload consumed by the current UI.
    try:
        git_status = get_git_status(workspace)
    except (RuntimeError, ValueError) as exc:
        logger.warning("build_snapshot_payload_from_projection: git_status failed: %s", exc)
        git_status = {}

    docs_present, workspace_status = _workspace_readiness_projection(workspace)

    # Load plan_text and agents_content from files (independent of PM running)
    plan_text = ""
    plan_mtime = None
    agents_content = ""
    agents_mtime = None
    if workspace and runtime_roots:
        # Load plan.md
        plan_text, plan_mtime = _read_first_text_candidate(
            _resolve_runtime_artifact_candidates(
                workspace,
                runtime_roots,
                "runtime/contracts/plan.md",
            )
        )

        # Load agents.generated.md
        agents_content, agents_mtime = _read_first_text_candidate(
            _resolve_runtime_artifact_candidates(
                workspace,
                runtime_roots,
                "runtime/contracts/agents.generated.md",
            )
        )

    return {
        "pm": projection.pm_local,
        "director": projection.director_merged or projection.director_local,
        "workflow": projection.workflow_archive,
        "engine": projection.engine_fallback,
        "run_id": str(derived.get("run_id") or pm_contract_payload.get("run_id") or "").strip(),
        "tasks": tasks,
        "pm_state": pm_state or None,
        "git": git_status,
        "snapshot_derived": derived,
        "resident": projection.resident,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "docs_present": docs_present,
        "workspace_status": workspace_status,
        # Add plan_text and agents_content
        "plan_text": plan_text,
        "plan_mtime": plan_mtime,
        "agents_content": agents_content,
        "agents_mtime": agents_mtime,
        "plan_text_normalized": False,  # Plan text loaded from file needs normalization
    }


def select_task_rows_from_projection(projection: RuntimeProjection) -> list[dict[str, Any]]:
    """Select live task rows using projection priority rules.

    Workflow archive rows are historical evidence and must not become live
    runtime tasks.  The caller may still expose archive payloads separately.
    """
    if projection.task_rows:
        return [row for row in projection.task_rows if isinstance(row, dict)]

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

    selected, _ = select_task_rows(None, local_payload)
    return selected


def load_runtime_task_rows(workspace: str) -> list[dict[str, Any]]:
    """Load bounded task-runtime-owned rows for runtime transport projection.

    TaskRuntimeService owns both the transitional file-backed rows and the
    execution fact read model; runtime projection only consumes that owner
    projection and never queries/parses the fact stream directly. Raw durable
    evidence remains owned by TaskRuntime; this transport projection retains
    task identity, state, dependency, target, reference, hash, count, and error
    fields while excluding large execution-internal payloads.
    """
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []
    try:
        rows = TaskRuntimeService(workspace_token).list_observable_task_rows()
        return [_project_observable_task_row(row) for row in rows if isinstance(row, dict)]
    except (RuntimeError, ValueError) as exc:
        logger.debug("failed to load runtime task rows: %s", exc)
        return []
