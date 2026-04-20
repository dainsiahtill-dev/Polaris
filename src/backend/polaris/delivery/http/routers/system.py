import logging
import os
from pathlib import Path
from typing import Any

from polaris.bootstrap.config import SettingsUpdate
from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.application.health import get_lancedb_status
from polaris.cells.director.execution.public import rebind_director_service
from polaris.cells.events.fact_stream.public.service import set_debug_tracing_enabled
from polaris.cells.runtime.artifact_store.public.service import build_snapshot
from polaris.cells.runtime.projection.public.service import resolve_workspace_runtime_context
from polaris.cells.storage.layout.public.service import (
    save_persisted_settings,
    sync_process_settings_environment,
)
from polaris.cells.workspace.integrity.public.service import (
    clear_workspace_status,
    validate_workspace,
    write_workspace_status,
)
from polaris.domain.exceptions import ValidationError as DomainValidationError
from polaris.kernelone.events.typed import (
    SettingsChanged as TypedSettingsChanged,
    get_default_adapter as get_typed_adapter,
)
from polaris.kernelone.process import (
    clear_director_stop_flag,
    clear_stop_flag,
    terminate_external_loop_pm_processes,
)
from polaris.kernelone.runtime.defaults import DEFAULT_PM_LOG, DEFAULT_WORKSPACE
from polaris.kernelone.storage import normalize_ramdisk_root
from polaris.kernelone.storage.io_paths import normalize_artifact_rel_path, workspace_has_docs
from polaris.kernelone.utils.time_utils import utc_now_str

from ._shared import get_state, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", dependencies=[Depends(require_auth)])
async def health() -> dict[str, Any]:
    """Get backend health status including PM/Director runtime state."""
    lancedb_status = get_lancedb_status()

    # Get PM status
    pm_status = {"status": "unknown", "running": False}
    try:
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container

        async def get_pm_status():
            container = get_container()
            pm_service = await container.resolve_async(PMService)
            return pm_service.get_status()

        # Use await directly - endpoint is async so no nested event loop
        try:
            pm_status = await get_pm_status()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"PM status unavailable: {e}")
            pm_status = {"status": "unavailable", "running": False}
    except (RuntimeError, ValueError) as e:
        logger.debug(f"PM service not available: {e}")
        pm_status = {"status": "unavailable", "running": False}

    # Get Director status
    director_status = {"status": "unknown", "state": "idle"}
    try:
        from polaris.cells.director.execution.public.service import DirectorService
        from polaris.infrastructure.di.container import get_container

        async def get_director_status():
            container = get_container()
            director_service = await container.resolve_async(DirectorService)
            return await director_service.get_status()

        try:
            director_status = await get_director_status()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Director status unavailable: {e}")
            director_status = {"status": "unavailable", "state": "idle"}
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Director service not available: {e}")
        director_status = {"status": "unavailable", "state": "idle"}

    return {
        "ok": bool(lancedb_status.get("ok")),
        "version": "0.1",
        "timestamp": utc_now_str(),
        "lancedb_ok": bool(lancedb_status.get("ok")),
        "lancedb_error": lancedb_status.get("error"),
        "python": lancedb_status.get("python"),
        "pm": pm_status,
        "director": director_status,
    }


@router.get("/settings", dependencies=[Depends(require_auth)])
def get_settings(request: Request) -> dict[str, Any]:
    state = get_state(request)
    payload = state.settings.to_payload()
    raw_json_log_path = str(payload.get("json_log_path") or "").strip()
    payload["json_log_path"] = normalize_artifact_rel_path(raw_json_log_path) if raw_json_log_path else DEFAULT_PM_LOG
    return payload


@router.post("/settings", dependencies=[Depends(require_auth)])
async def update_settings(request: Request, payload: SettingsUpdate) -> dict[str, Any]:
    state = get_state(request)
    previous_workspace = str(state.settings.workspace or DEFAULT_WORKSPACE).strip()
    target_self_upgrade_mode = (
        bool(payload.self_upgrade_mode)
        if payload.self_upgrade_mode is not None
        else bool(getattr(state.settings, "self_upgrade_mode", False))
    )
    if payload.workspace:
        try:
            payload.workspace = validate_workspace(
                payload.workspace,
                self_upgrade_mode=target_self_upgrade_mode,
            )
        except DomainValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message)
    workspace_root = str(payload.workspace or state.settings.workspace or DEFAULT_WORKSPACE).strip()
    requested_workspace = str(payload.workspace or "").strip()
    resolved_requested = ""
    if requested_workspace:
        try:
            resolved_requested = str(Path(requested_workspace).resolve())
        except (RuntimeError, ValueError):
            resolved_requested = requested_workspace
    workspace_changed = False
    if requested_workspace:
        try:
            workspace_changed = Path(requested_workspace).resolve() != Path(previous_workspace).resolve()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Path resolve failed, using string comparison: {e}")
            workspace_changed = requested_workspace != previous_workspace

    if workspace_changed:
        from polaris.cells.director.execution.public.service import DirectorService
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container

        try:
            container = await get_container()
            pm_service = await container.resolve_async(PMService)
            pm_status = pm_service.get_status()
            if bool(pm_status.get("running")):
                raise HTTPException(
                    status_code=409,
                    detail="cannot switch workspace while PM is running; stop PM first",
                )
            director_service = await container.resolve_async(DirectorService)
            director_status = await director_service.get_status()
            if str(director_status.get("state", "")).strip().upper() == "RUNNING":
                raise HTTPException(
                    status_code=409,
                    detail="cannot switch workspace while Director is running; stop Director first",
                )
        except HTTPException:
            raise
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Workspace change check failed, continuing: {e}")

    if payload.ramdisk_root is not None:
        normalized = normalize_ramdisk_root(payload.ramdisk_root)
        if normalized:
            try:
                ws_abs = os.path.abspath(workspace_root)
                if os.path.commonpath([ws_abs, normalized]) == ws_abs:
                    payload.ramdisk_root = ""
                else:
                    payload.ramdisk_root = normalized
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Ramdisk path check failed: {e}")
                payload.ramdisk_root = normalized
    if payload.json_log_path is not None:
        raw_json_log_path = str(payload.json_log_path or "").strip()
        payload.json_log_path = normalize_artifact_rel_path(raw_json_log_path) if raw_json_log_path else DEFAULT_PM_LOG
    try:
        state.settings.apply_update(payload)
    except ValueError as exc:
        logger.error("apply_settings failed: %s", exc)
        raise HTTPException(status_code=400, detail="internal error") from exc
    if resolved_requested:
        try:
            current_workspace = str(Path(str(state.settings.workspace)).resolve())
        except (RuntimeError, ValueError):
            current_workspace = str(state.settings.workspace or "").strip()
        if current_workspace != resolved_requested:
            state.settings.workspace = Path(resolved_requested)
    sync_process_settings_environment(state.settings)
    set_debug_tracing_enabled(bool(state.settings.debug_tracing))
    if payload.workspace:
        workspace_str = str(state.settings.workspace or "")
        if workspace_has_docs(workspace_str):
            clear_workspace_status(workspace_str)
        else:
            write_workspace_status(
                workspace_str,
                status="NEEDS_DOCS_INIT",
                reason="docs/ directory not found",
                actions=["INIT_DOCS_WIZARD"],
            )
    if workspace_changed:
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container

        try:
            container = await get_container()
            pm_service = await container.resolve_async(PMService)
            pm_service.refresh_storage_layout()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"PM service refresh failed: {e}")
        try:
            await rebind_director_service(str(state.settings.workspace))
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Director service rebind failed: {e}")
    save_persisted_settings(state.settings)

    # Broadcast settings change event for real-time synchronization
    if workspace_changed:
        try:
            from polaris.infrastructure.di.container import get_container
            from polaris.kernelone.events.message_bus import MessageBus, MessageType

            container = await get_container()
            message_bus = container.resolve(MessageBus)

            # Emit typed event
            adapter = get_typed_adapter()
            if adapter:
                try:
                    typed_event = TypedSettingsChanged.create(
                        workspace=str(state.settings.workspace or ""),
                        previous_workspace=previous_workspace,
                        changed_fields=list(payload.model_dump(exclude_unset=True).keys()),
                    )
                    await adapter.emit_to_both(typed_event)
                except (RuntimeError, ValueError) as typed_err:
                    logger.debug(f"Failed to emit typed settings event: {typed_err}")

            # Emit legacy message for backward compatibility
            if message_bus:
                await message_bus.broadcast(
                    MessageType.SETTINGS_CHANGED,
                    "system",
                    {
                        "workspace": str(state.settings.workspace or ""),
                        "previous_workspace": previous_workspace,
                        "changed_fields": list(payload.model_dump(exclude_unset=True).keys()),
                    },
                )
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to broadcast settings change: {e}")

    result = state.settings.to_payload()
    raw_json_log_path = str(result.get("json_log_path") or "").strip()
    result["json_log_path"] = normalize_artifact_rel_path(raw_json_log_path) if raw_json_log_path else DEFAULT_PM_LOG
    return result


@router.get("/state/snapshot", dependencies=[Depends(require_auth)])
def state_snapshot(request: Request) -> dict[str, Any]:
    state = get_state(request)
    configured_workspace = str(getattr(state.settings, "workspace", "") or "").strip()
    ramdisk_root = str(getattr(state.settings, "ramdisk_root", "") or "").strip()
    workspace_ctx = resolve_workspace_runtime_context(
        configured_workspace=configured_workspace,
        default_workspace=DEFAULT_WORKSPACE,
        ramdisk_root=ramdisk_root,
    )
    return build_snapshot(
        state,
        workspace=workspace_ctx.workspace,
        cache_root=workspace_ctx.runtime_root,
    )


@router.post("/app/shutdown", dependencies=[Depends(require_auth)])
async def app_shutdown(request: Request) -> dict[str, Any]:
    state = get_state(request)
    workspace = str(state.settings.workspace or DEFAULT_WORKSPACE)

    pm_running = False
    pm_external_terminated_pids: list[int] = []
    director_running = False

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
        logger.debug(f"PM stop check failed: {e}")

    # Clean up external PM processes emitted by legacy loop wrappers.
    pm_external_terminated_pids = terminate_external_loop_pm_processes(workspace)
    clear_stop_flag(workspace)

    # Use DirectorService to stop Director (authoritative source)
    director_running = False
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
        logger.debug(f"Director stop check failed: {e}")

    clear_director_stop_flag(workspace)

    state.last_pm_payload = None

    pm_terminated = bool(pm_running or pm_external_terminated_pids)
    director_terminated = bool(director_running)

    return {
        "ok": True,
        "pm_running": pm_running,
        "pm_external_terminated_pids": pm_external_terminated_pids,
        "director_running": director_running,
        # Backward-compatible fields used by existing clients/tests.
        "pm_terminated": pm_terminated,
        "director_terminated": director_terminated,
    }
