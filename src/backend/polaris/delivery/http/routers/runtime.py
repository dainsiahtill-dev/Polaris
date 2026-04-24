from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from polaris.cells.runtime.projection.public.service import build_director_runtime_status
from polaris.cells.runtime.state_owner.public.service import (
    clear_runtime_scope,
    reset_runtime_records,
)
from polaris.cells.storage.layout.internal.layout_business import polaris_home
from polaris.kernelone._runtime_config import resolve_env_str
from polaris.kernelone.process import (
    clear_director_stop_flag,
    clear_stop_flag,
    terminate_external_loop_pm_processes,
    terminate_pid,
)
from polaris.kernelone.storage import (
    STORAGE_POLICY_REGISTRY,
    resolve_global_path,
    resolve_storage_roots,
    resolve_workspace_persistent_path,
)
from polaris.kernelone.storage.io_paths import build_cache_root
from pydantic import BaseModel

from ._shared import get_state, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()

_STORAGE_CLASSIFICATION: dict[str, dict[str, Any]] = {
    "global_config": {
        "description": "Global configuration (config/llm/*)",
        "lifecycle": "permanent",
        "example_paths": ["config/settings.json", "config/llm/*"],
    },
    "workspace_persistent": {
        "description": "Project-local persistent data (.polaris)",
        "lifecycle": "permanent or active",
        "example_paths": [
            "workspace/docs/*",
            "workspace/brain/*",
            "workspace/policy/*",
            "workspace/meta/*",
        ],
    },
    "runtime_current": {
        "description": "Current runtime state (active/ephemeral)",
        "lifecycle": "active or ephemeral",
        "example_paths": [
            "runtime/contracts/*",
            "runtime/tasks/*",
            "runtime/state/*",
            "runtime/events/*",
        ],
    },
    "runtime_run": {
        "description": "Run-scoped snapshots (temporary)",
        "lifecycle": "active",
        "example_paths": ["runtime/runs/<run_id>/*"],
    },
    "workspace_history": {
        "description": "Historical archives (permanent, compressed)",
        "lifecycle": "history",
        "example_paths": [
            "workspace/history/runs/*",
            "workspace/history/tasks/*",
            "workspace/history/factory/*",
        ],
    },
}


class RuntimeClearPayload(BaseModel):
    scope: Literal["pm", "director", "dialogue", "all"] = "all"


@router.get("/runtime/storage-layout", dependencies=[Depends(require_auth)])
async def runtime_storage_layout(request: Request) -> dict[str, Any]:
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    roots = resolve_storage_roots(
        workspace,
        ramdisk_root=state.settings.ramdisk_root or None,
    )

    # Get storage classification and policies
    classification = _STORAGE_CLASSIFICATION
    policies = []
    policy_keys = set()
    for policy in STORAGE_POLICY_REGISTRY:
        if policy.logical_prefix and policy.logical_prefix not in policy_keys:
            policy_keys.add(policy.logical_prefix)
            policies.append(
                {
                    "prefix": policy.logical_prefix,
                    "category": policy.category.value,
                    "lifecycle": policy.lifecycle.value,
                    "retention_days": policy.retention_days,
                    "compress": policy.compress,
                    "archive_on_terminal": policy.archive_on_terminal,
                }
            )

    return {
        "workspace": roots.workspace_abs,
        "workspace_abs": roots.workspace_abs,
        "workspace_key": roots.workspace_key,
        "storage_layout_mode": roots.storage_layout_mode,
        "runtime_mode": roots.runtime_mode,
        "ramdisk_root": str(state.settings.ramdisk_root or ""),
        "home_root": roots.home_root,
        "global_root": roots.global_root,
        "projects_root": roots.projects_root,
        "project_root": roots.project_root,
        "config_root": roots.config_root,
        "workspace_persistent_root": roots.workspace_persistent_root,
        "project_persistent_root": roots.project_persistent_root,
        "runtime_base": roots.runtime_base,
        "runtime_root": roots.runtime_root,
        "runtime_project_root": roots.runtime_project_root,
        "history_root": getattr(roots, "history_root", ""),
        "classification": classification,
        "policies": policies,
        "migration_version": 2,
        "paths": {
            "settings": resolve_global_path("config/settings.json"),
            "llm_config": resolve_global_path("config/llm/llm_config.json"),
            "llm_test_index": resolve_global_path("config/llm/llm_test_index.json"),
            "global_settings": resolve_global_path("config/settings.json"),
            "global_llm_config": resolve_global_path("config/llm/llm_config.json"),
            "global_llm_test_index": resolve_global_path("config/llm/llm_test_index.json"),
            "brain": resolve_workspace_persistent_path(workspace, "workspace/brain"),
            "lancedb": resolve_workspace_persistent_path(workspace, "workspace/lancedb"),
            "verify": resolve_workspace_persistent_path(workspace, "workspace/verify"),
            "policy": resolve_workspace_persistent_path(workspace, "workspace/policy"),
            "meta": resolve_workspace_persistent_path(workspace, "workspace/meta"),
            "history_runs": resolve_workspace_persistent_path(workspace, "workspace/history/runs"),
        },
        "env": {
            "KERNELONE_HOME": polaris_home(),
            "KERNELONE_RUNTIME_ROOT": resolve_env_str("runtime_root"),
            "KERNELONE_RUNTIME_CACHE_ROOT": resolve_env_str("runtime_cache_root"),
            "KERNELONE_STATE_TO_RAMDISK": resolve_env_str("state_to_ramdisk"),
        },
    }


@router.post("/runtime/clear", dependencies=[Depends(require_auth)])
async def runtime_clear(request: Request, payload: RuntimeClearPayload) -> dict[str, Any]:
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)

    result = clear_runtime_scope(workspace, cache_root, payload.scope)
    return {
        "ok": True,
        "scope": payload.scope,
        **result,
    }


@router.get("/runtime/migration-status", dependencies=[Depends(require_auth)])
async def runtime_migration_status(request: Request) -> dict[str, Any]:
    """Get migration status for storage layout v2.

    Returns:
        Migration status including version, cutover time, backup path, archived counts, and strict mode.
    """
    import json
    from pathlib import Path

    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    # Default values
    version = 1
    cutover_at: str | None = None
    backup_path = ""
    archived_counts = {"runs": 0, "tasks": 0, "factory": 0}
    strict_mode = False

    try:
        roots = resolve_storage_roots(workspace)

        # Read version file
        version_file = Path(roots.workspace_persistent_root) / "meta" / "storage_layout.version.json"
        if version_file.exists():
            with open(version_file, encoding="utf-8") as f:
                version_data = json.load(f)
                version = version_data.get("version", 1)
                cutover_at = version_data.get("cutover_at")
                strict_mode = version_data.get("strict_mode", False)

        # Read backup path from protocol_backup directory if exists
        backup_dirs = list(Path(roots.workspace_persistent_root).glob("protocol_backup_*"))
        if backup_dirs:
            latest_backup = max(backup_dirs, key=lambda p: p.stat().st_mtime)
            backup_path = str(latest_backup)

        # Count archived items
        history_root = Path(roots.history_root)
        if history_root.exists():
            # Count runs
            runs_dir = history_root / "runs"
            if runs_dir.exists():
                archived_counts["runs"] = len([d for d in runs_dir.iterdir() if d.is_dir()])

            # Count tasks
            tasks_dir = history_root / "tasks"
            if tasks_dir.exists():
                archived_counts["tasks"] = len([d for d in tasks_dir.iterdir() if d.is_dir()])

            # Count factory runs
            factory_dir = history_root / "factory"
            if factory_dir.exists():
                archived_counts["factory"] = len([d for d in factory_dir.iterdir() if d.is_dir()])

    except (RuntimeError, ValueError) as e:
        logger.warning(f"Failed to get migration status: {e}")

    return {
        "version": version,
        "cutover_at": cutover_at,
        "backup_path": backup_path,
        "archived_counts": archived_counts,
        "strict_mode": strict_mode,
    }


@router.post("/runtime/reset-tasks", dependencies=[Depends(require_auth)])
async def runtime_reset_tasks(request: Request) -> dict[str, Any]:
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)

    pm_running = False
    pm_external_terminated_pids: list[int] = []
    director_running = False
    director_external_pid = None
    director_external_terminated = False

    # Use PMService to stop PM (authoritative source)
    try:
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container

        container = await get_container()
        pm_service = await container.resolve_async(PMService)
        pm_status = pm_service.get_status()
        if pm_status.get("running"):
            pm_running = True
            await pm_service.stop()
    except (RuntimeError, ValueError) as e:
        logger.debug(f"PM stop failed: {e}")

    # Clean up external PM processes emitted by legacy loop wrappers.
    pm_external_terminated_pids = terminate_external_loop_pm_processes(workspace)

    # Use DirectorService to stop Director (authoritative source).
    try:
        from polaris.cells.director.execution.public.service import DirectorService
        from polaris.infrastructure.di.container import get_container

        container = await get_container()
        director_service = await container.resolve_async(DirectorService)
        director_status = await director_service.get_status()
        if str(director_status.get("state", "")).strip().upper() == "RUNNING":
            director_running = True
            await director_service.stop()
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Director stop failed: {e}")

    # Cleanup external director processes
    runtime_status = build_director_runtime_status(state, workspace, cache_root)
    if runtime_status.get("running") and director_running:
        pid = runtime_status.get("pid")
        if isinstance(pid, int) and pid > 0:
            director_external_pid = pid
            director_external_terminated = terminate_pid(pid)

    clear_stop_flag(workspace, cache_root)
    clear_director_stop_flag(workspace, cache_root)

    result = reset_runtime_records(workspace, cache_root)
    state.last_pm_payload = None

    return {
        "ok": True,
        "pm_running": pm_running,
        "pm_external_terminated_pids": pm_external_terminated_pids,
        "director_running": director_running,
        "director_external_pid": director_external_pid,
        "director_external_terminated": director_external_terminated,
        **result,
    }
