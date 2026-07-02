from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.events.fact_stream.public.service import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
from polaris.cells.runtime.projection.public.service import build_director_runtime_status
from polaris.cells.runtime.state_owner.public.service import (
    clear_runtime_scope,
    reset_runtime_records,
)
from polaris.cells.runtime.task_runtime.public.service import reset_runtime_task_records
from polaris.cells.storage.layout.public import polaris_home
from polaris.delivery.http.schemas.common import (
    RuntimeClearResponse,
    RuntimeMigrationStatusResponse,
    RuntimeResetTasksResponse,
    RuntimeStorageLayoutResponse,
)
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
from polaris.kernelone.storage.layout import resolve_runtime_path
from polaris.kernelone.traceability.public.service import create_traceability_service
from pydantic import BaseModel

from ._shared import active_workspace_value, get_state, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()


def _merge_reset_results(*results: dict[str, object]) -> dict[str, object]:
    cleared_paths: list[str] = []
    failed_paths: list[str] = []
    for result in results:
        raw_cleared = result.get("cleared_paths", [])
        raw_failed = result.get("failed_paths", [])
        if isinstance(raw_cleared, list):
            cleared_paths.extend(str(path) for path in raw_cleared)
        if isinstance(raw_failed, list):
            failed_paths.extend(str(path) for path in raw_failed)

    unique_cleared = sorted(set(cleared_paths))
    unique_failed = sorted({path for path in failed_paths if path not in set(unique_cleared)})
    return {
        "cleared_paths": unique_cleared,
        "failed_paths": unique_failed,
        "cleared_count": len(unique_cleared),
        "failed_count": len(unique_failed),
    }


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


class RuntimeFactStreamProbePayload(BaseModel):
    marker: str = "e2e-fact-stream-probe"


class RuntimeTraceabilityProbePayload(BaseModel):
    marker: str = "e2e-traceability-probe"


def _safe_probe_slug(value: str, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-_")
    if not token:
        return fallback
    return token[:96].strip("-_") or fallback


def _runtime_storage_layout_core(request: Request) -> dict[str, Any]:
    state = get_state(request)
    workspace = active_workspace_value(state.settings)
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


@router.get(
    "/runtime/storage-layout",
    dependencies=[Depends(require_auth)],
    response_model=RuntimeStorageLayoutResponse,
)
async def runtime_storage_layout(request: Request) -> dict[str, Any]:  # DEPRECATED
    return _runtime_storage_layout_core(request)


@router.get(
    "/v2/runtime/storage/layout",
    dependencies=[Depends(require_auth)],
    response_model=RuntimeStorageLayoutResponse,
)
async def v2_runtime_storage_layout(request: Request) -> dict[str, Any]:
    """Get workspace storage layout, roots, and environment."""
    return _runtime_storage_layout_core(request)


def _runtime_clear_core(request: Request, payload: RuntimeClearPayload) -> dict[str, Any]:
    state = get_state(request)
    workspace = active_workspace_value(state.settings)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)

    result = clear_runtime_scope(workspace, cache_root, payload.scope)
    return {
        "ok": True,
        "scope": payload.scope,
        **result,
    }


@router.post(
    "/v2/runtime/clear",
    dependencies=[Depends(require_auth)],
    response_model=RuntimeClearResponse,
)
async def v2_runtime_clear(request: Request, payload: RuntimeClearPayload) -> dict[str, Any]:
    """Clear runtime scope (pm, director, dialogue, or all)."""
    return _runtime_clear_core(request, payload)


@router.post(
    "/v2/runtime/fact-stream/probe",
    dependencies=[Depends(require_auth)],
)
async def v2_runtime_fact_stream_probe(
    request: Request,
    payload: RuntimeFactStreamProbePayload,
) -> dict[str, Any]:
    """E2E-only probe for the events.fact_stream public writer path."""
    if os.environ.get("KERNELONE_E2E") != "1":
        raise HTTPException(status_code=404, detail="not found")

    state = get_state(request)
    workspace = active_workspace_value(state.settings)
    stream = "e2e.fact_stream_probe"
    event_type = "probe.appended"
    marker = str(payload.marker or "").strip() or "e2e-fact-stream-probe"

    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=workspace,
            stream=stream,
            event_type=event_type,
            payload={"marker": marker, "source": "runtime_fact_stream_probe"},
            source="delivery.runtime.e2e_probe",
            task_id=marker,
            run_id=marker,
            correlation_id=marker,
        )
    )
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=workspace,
            stream=stream,
            event_type=event_type,
            task_id=marker,
            limit=10,
            offset=0,
        )
    )
    absolute_path = resolve_runtime_path(
        workspace,
        appended.storage_path,
        ramdisk_root=getattr(state.settings, "ramdisk_root", "") or None,
    )

    return {
        "ok": True,
        "workspace": workspace,
        "stream": stream,
        "event_type": event_type,
        "event_id": appended.event_id,
        "storage_path": appended.storage_path,
        "absolute_path": absolute_path,
        "artifact_exists": os.path.isfile(absolute_path),
        "appended_at": appended.appended_at,
        "queried_total": queried.total,
        "queried_events": list(queried.events),
        "next_offset": queried.next_offset,
    }


@router.post(
    "/v2/runtime/traceability/probe",
    dependencies=[Depends(require_auth)],
)
async def v2_runtime_traceability_probe(
    request: Request,
    payload: RuntimeTraceabilityProbePayload,
) -> dict[str, Any]:
    """E2E-only probe for non-empty KernelOne traceability matrices."""
    if os.environ.get("KERNELONE_E2E") != "1":
        raise HTTPException(status_code=404, detail="not found")

    state = get_state(request)
    workspace = active_workspace_value(state.settings)
    raw_marker = str(payload.marker or "").strip()
    marker = _safe_probe_slug(raw_marker, "e2e-traceability-probe")
    run_id = f"{marker}-run"
    service = create_traceability_service(workspace)

    doc = service.register_node(
        node_kind="doc",
        role="pm",
        external_id=f"{marker}-doc",
        content=f"{marker}: PM source document",
        metadata={"probe": "runtime_traceability", "raw_marker": raw_marker},
    )
    task = service.register_node(
        node_kind="task",
        role="pm",
        external_id=f"{marker}-task",
        content=f"{marker}: executable task",
        metadata={"probe": "runtime_traceability", "raw_marker": raw_marker},
    )
    verdict = service.register_node(
        node_kind="qa_verdict",
        role="qa",
        external_id=f"{marker}-qa",
        content=f"{marker}: QA verdict",
        metadata={"probe": "runtime_traceability", "raw_marker": raw_marker},
    )
    service.link(doc, task, "derives_from")
    service.link(task, verdict, "verifies")

    matrix = service.build_matrix(run_id, 1)
    storage_path = f"runtime/traceability/{run_id}.1.matrix.json"
    absolute_path = resolve_runtime_path(
        workspace,
        storage_path,
        ramdisk_root=getattr(state.settings, "ramdisk_root", "") or None,
    )
    service.persist(matrix, absolute_path)
    matrix_payload = matrix.to_dict()

    return {
        "ok": True,
        "workspace": workspace,
        "run_id": run_id,
        "storage_path": storage_path,
        "absolute_path": absolute_path,
        "artifact_exists": os.path.isfile(absolute_path),
        "node_count": len(matrix.nodes),
        "link_count": len(matrix.links),
        "node_kinds": sorted({str(node.get("kind") or "") for node in matrix_payload.get("nodes", [])}),
        "link_kinds": sorted({str(link.get("kind") or "") for link in matrix_payload.get("links", [])}),
        "matrix": matrix_payload,
    }


def _runtime_migration_status_core(request: Request) -> dict[str, Any]:
    """Get migration status for storage layout v2.

    Returns:
        Migration status including version, cutover time, backup path, archived counts, and strict mode.
    """
    import json
    from pathlib import Path

    state = get_state(request)
    workspace = active_workspace_value(state.settings)

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


@router.get(
    "/runtime/migration-status",
    dependencies=[Depends(require_auth)],
    response_model=RuntimeMigrationStatusResponse,
)
async def runtime_migration_status(request: Request) -> dict[str, Any]:  # DEPRECATED
    return _runtime_migration_status_core(request)


@router.get(
    "/v2/runtime/migration/status",
    dependencies=[Depends(require_auth)],
    response_model=RuntimeMigrationStatusResponse,
)
async def v2_runtime_migration_status(request: Request) -> dict[str, Any]:
    """Get storage layout migration status and archive counts."""
    return _runtime_migration_status_core(request)


async def _runtime_reset_tasks_core(request: Request) -> dict[str, Any]:
    state = get_state(request)
    workspace = active_workspace_value(state.settings)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)
    request_body: dict[str, Any] = {}
    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict):
            request_body = raw_body
    except (RuntimeError, ValueError):
        request_body = {}
    preserve_planning_contracts = bool(
        request_body.get("preserve_planning_contracts") or request_body.get("task_runtime_only")
    )

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
        pm_service = container.resolve(PMService)
        pm_status = pm_service.get_status()
        if pm_status.get("running"):
            pm_running = True
            await pm_service.stop()
    except (RuntimeError, ValueError) as e:
        logger.debug("PM stop failed: %s", e)

    # Clean up external PM processes emitted by legacy loop wrappers.
    pm_external_terminated_pids = terminate_external_loop_pm_processes(workspace)

    # Use DirectorService to stop Director (authoritative source).
    try:
        from polaris.cells.director.execution.public.service import DirectorService
        from polaris.infrastructure.di.container import get_container

        container = await get_container()
        director_service = container.resolve(DirectorService)
        director_status = await director_service.get_status()
        if str(director_status.get("state", "")).strip().upper() == "RUNNING":
            director_running = True
            await director_service.stop()
    except (RuntimeError, ValueError) as e:
        logger.debug("Director stop failed: %s", e)

    # Cleanup external director processes
    runtime_status = build_director_runtime_status(state, workspace, cache_root)
    if runtime_status.get("running") and director_running:
        pid = runtime_status.get("pid")
        if isinstance(pid, int) and pid > 0:
            director_external_pid = pid
            director_external_terminated = terminate_pid(pid)

    clear_stop_flag(workspace, cache_root)
    clear_director_stop_flag(workspace, cache_root)

    if preserve_planning_contracts:
        state_reset_result = {
            "cleared_paths": [],
            "failed_paths": [],
            "cleared_count": 0,
            "failed_count": 0,
            "preserved_planning_contracts": True,
        }
    else:
        state_reset_result = reset_runtime_records(workspace, cache_root)
    task_runtime_reset_result = reset_runtime_task_records(workspace)
    result = _merge_reset_results(state_reset_result, task_runtime_reset_result)
    if not preserve_planning_contracts:
        state.last_pm_payload = None

    return {
        "ok": True,
        "preserve_planning_contracts": preserve_planning_contracts,
        "pm_running": pm_running,
        "pm_external_terminated_pids": pm_external_terminated_pids,
        "director_running": director_running,
        "director_external_pid": director_external_pid,
        "director_external_terminated": director_external_terminated,
        "state_reset": state_reset_result,
        "task_runtime_reset": task_runtime_reset_result,
        **result,
    }


@router.post(
    "/v2/runtime/reset/tasks",
    dependencies=[Depends(require_auth)],
    response_model=RuntimeResetTasksResponse,
)
async def v2_runtime_reset_tasks(request: Request) -> dict[str, Any]:
    """Stop PM/Director and reset runtime task records."""
    return await _runtime_reset_tasks_core(request)
