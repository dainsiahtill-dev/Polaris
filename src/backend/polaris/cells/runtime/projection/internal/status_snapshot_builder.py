from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

# NOTE: artifact_store imports are lazy (inside functions) to avoid a load-time circular
# dependency: artifact_store.public.service → artifacts.py → projection.public.service
# → status_snapshot_builder (this module, still loading). Do not hoist to module level.
from polaris.cells.docs.court_workflow.public.service import map_engine_to_court_state
from polaris.cells.runtime.projection.internal.constants import DEFAULT_WORKSPACE
from polaris.cells.runtime.projection.internal.io_helpers import (
    build_cache_root,
    get_lancedb_status,
    read_json,
    resolve_artifact_path,
)
from polaris.cells.runtime.projection.internal.llm_status import build_llm_status
from polaris.cells.runtime.projection.internal.runtime_projection_service import (
    build_resident_state,
    get_director_local_status,
    get_workflow_director_status,
    merge_director_status,
)
from polaris.cells.runtime.projection.internal.workflow_status import get_workflow_runtime_status
from polaris.kernelone.memory.integration import (
    get_memory_store,
    get_reflection_store,
    init_anthropomorphic_modules,
)

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger(__name__)


def build_pm_status(state: AppState) -> dict[str, Any]:
    """Build PM status synchronously by running the async version in a thread.

    Note: This is a sync wrapper. For better performance in async context,
    use build_pm_status_async directly.

    This function safely handles both sync and async contexts without
    creating nested event loops which can cause deadlocks.
    """
    try:
        loop = asyncio.get_running_loop()
        # We're in an async context - use asyncio.run_coroutine_threadsafe
        # to schedule the coroutine on the existing loop from a thread
        import concurrent.futures

        def run_in_loop() -> Any:
            # This runs in a thread, so we can use the existing loop
            return asyncio.run_coroutine_threadsafe(build_pm_status_async(state), loop).result(timeout=10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_loop)
            return future.result(timeout=12)  # Slightly longer to account for scheduling overhead
    except RuntimeError:
        # No running loop, safe to use asyncio.run
        return asyncio.run(build_pm_status_async(state))


async def build_pm_status_async(state: AppState) -> dict[str, Any]:
    """Build PM status from PMService (the authoritative source).

    PMService is the only source for PM process status - no fallback to AppState.
    """
    running = False
    pid = None
    mode = ""
    started_at = None
    workflow_status = None

    # Get status from PMService (authoritative source)
    pm_status = None
    try:
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container

        container = await get_container()
        pm_service = await container.resolve_async(PMService)
        pm_status = pm_service.get_status()
    except (RuntimeError, ValueError) as exc:
        # PMService unavailable means PM is simply not running - log at debug.
        logger.debug("build_pm_status_async: PMService unavailable: %s", exc)

    if pm_status and isinstance(pm_status, dict):
        running = bool(pm_status.get("running"))
        pid = pm_status.get("pid")
        mode = str(pm_status.get("mode", ""))
        started_at = pm_status.get("started_at")

    # Check workflow status for additional context (not as fallback)
    if not running:
        workspace = str(state.settings.workspace or DEFAULT_WORKSPACE)
        ramdisk_root = getattr(state.settings, "ramdisk_root", "") or ""
        if workspace and ramdisk_root:
            cache_root = build_cache_root(ramdisk_root, workspace)
            workflow_status = get_workflow_runtime_status(workspace, cache_root)

    return {
        "running": running,
        "pid": pid,
        "mode": mode or ("workflow" if workflow_status else ""),
        "started_at": started_at,
        "workflow": workflow_status,
    }


async def build_director_status(
    state: AppState,
    workspace: str,
    cache_root: str,
) -> dict[str, Any]:
    """Build Director status from the current local/workflow sources only."""
    del state
    local_status = await get_director_local_status()
    workflow_status = await get_workflow_director_status(workspace, cache_root)
    return merge_director_status(local_status, workflow_status)


def _parse_engine_updated_at(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except (RuntimeError, ValueError):
        return None


def _build_engine_status(
    workspace: str,
    cache_root: str,
    pm_status: dict[str, Any] | None = None,
    director_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
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
        payload = dict(payload)
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


def _build_anthro_state(state: AppState) -> dict[str, Any] | None:
    try:
        base_dir = state.settings.ramdisk_root or state.settings.workspace or DEFAULT_WORKSPACE
        init_anthropomorphic_modules(str(base_dir))
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
        logger.debug("_build_anthro_state: optional module unavailable: %s", exc)
        return None


def build_snapshot(
    state: AppState,
    *,
    workspace: str,
    cache_root: str,
) -> dict[str, Any]:
    """Lazy proxy to artifact_store build_snapshot.

    Exposed as a module-level symbol so tests can patch it without importing
    artifact_store at module import time (which would reintroduce a cycle).
    """
    from polaris.cells.runtime.artifact_store.public.service import (
        build_snapshot as _build_snapshot,
    )

    return _build_snapshot(
        state,
        workspace=workspace,
        cache_root=cache_root,
    )


def build_memory_payload(workspace: str, cache_root: str) -> dict[str, Any]:
    """Lazy proxy to artifact_store build_memory_payload."""
    from polaris.cells.runtime.artifact_store.public.service import (
        build_memory_payload as _build_memory_payload,
    )

    result = _build_memory_payload(workspace, cache_root)
    return result if result is not None else {}


def build_success_stats_payload(
    workspace: str,
    cache_root: str,
) -> dict[str, Any]:
    """Lazy proxy to artifact_store build_success_stats_payload."""
    from polaris.cells.runtime.artifact_store.public.service import (
        build_success_stats_payload as _build_success_stats_payload,
    )

    return _build_success_stats_payload(workspace, cache_root)


def build_status_payload_sync(
    state: AppState,
    workspace: str,
    cache_root: str,
    pm_status: dict[str, Any],
    director_status: dict[str, Any],
) -> dict[str, Any]:
    """Build websocket status payload in a worker thread-safe context."""
    engine_status = _build_engine_status(
        workspace,
        cache_root,
        pm_status=pm_status,
        director_status=director_status,
    )
    llm_status = None
    try:
        llm_status = build_llm_status(state.settings)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "build_status_payload_sync: build_llm_status failed: %s",
            exc,
            exc_info=True,
        )
    court_state = map_engine_to_court_state(
        engine_status=engine_status,
        pm_status=pm_status,
        director_status=director_status,
    )
    return {
        "type": "status",
        "pm_status": pm_status,
        "director_status": director_status,
        "engine_status": engine_status,
        "llm_status": llm_status,
        "snapshot": build_snapshot(
            state,
            workspace=workspace,
            cache_root=cache_root,
        ),
        "resident": build_resident_state(workspace),
        "lancedb": get_lancedb_status(),
        "memory": build_memory_payload(workspace, cache_root),
        "success_stats": build_success_stats_payload(workspace, cache_root),
        "anthro_state": _build_anthro_state(state),
        "court_state": court_state,
    }
