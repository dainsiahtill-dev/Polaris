"""API dependencies for FastAPI.

Provides dependency injection for services and common parameters.
Uses FastAPI's native Depends pattern integrated with DI container.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from polaris.bootstrap.config import Settings, get_settings
from fastapi import HTTPException, Request
from polaris.cells.director.execution.public import rebind_director_service
from polaris.cells.director.execution.public.service import DirectorService
from polaris.cells.orchestration.pm_planning.public.service import PMService
from polaris.domain.services.background_task import BackgroundTaskService
from polaris.infrastructure.di.container import get_container
from polaris.kernelone.auth_context import SimpleAuthContext

logger = logging.getLogger(__name__)


def _append_debug(event: str, payload: dict[str, object]) -> None:
    """Best-effort backend debug event sink for stall diagnosis."""
    try:
        log_path = Path(os.environ.get("POLARIS_BACKEND_DEBUG_LOG", "C:/Temp/hp_backend_debug.jsonl"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "event": event,
            "payload": payload,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (RuntimeError, ValueError):
        logger.warning("Failed to write debug event to log file")
        return


async def get_current_settings() -> Settings:
    """Get application settings."""
    return get_settings()


async def get_workspace(request: Request) -> Path:
    """Resolve active workspace path for request-scoped dependencies."""
    app_state = getattr(request.app.state, "app_state", None)
    state_settings = getattr(app_state, "settings", None)
    app_settings = getattr(request.app.state, "settings", None)
    raw_workspace = str(getattr(state_settings, "workspace", "") or getattr(app_settings, "workspace", "")).strip()
    if not raw_workspace:
        raise HTTPException(status_code=500, detail="workspace not configured")
    return Path(raw_workspace).resolve()


async def get_pm_service() -> PMService:
    """Get PM service instance from DI container."""
    container = await get_container()
    return await container.resolve_async(PMService)


async def get_director_service(request: Request) -> DirectorService:
    """Get Director service instance from DI container."""
    start = time.perf_counter()
    requested_workspace = ""
    rebind_needed = False
    container = await get_container()
    service = await container.resolve_async(DirectorService)

    app_state = getattr(request.app.state, "app_state", None)
    state_settings = getattr(app_state, "settings", None)
    requested_workspace = str(getattr(state_settings, "workspace", "") or "").strip()
    if requested_workspace:
        current_workspace = str(getattr(service.config, "workspace", "") or "").strip()
        try:
            normalized_requested = str(Path(requested_workspace).resolve())
            normalized_current = str(Path(current_workspace).resolve()) if current_workspace else ""
        except (RuntimeError, ValueError):
            normalized_requested = requested_workspace
            normalized_current = current_workspace
        if normalized_requested and normalized_requested != normalized_current:
            rebind_needed = True
            service = await rebind_director_service(normalized_requested)

    _append_debug(
        "dependency.get_director_service",
        {
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "requested_workspace": requested_workspace,
            "service_workspace": str(getattr(getattr(service, "config", None), "workspace", "")),
            "rebind": rebind_needed,
        },
    )
    return service


async def get_background_task_service() -> BackgroundTaskService:
    """Get background task service instance from DI container."""
    container = await get_container()
    return await container.resolve_async(BackgroundTaskService)


def require_auth(request: Request) -> None:
    """Require bearer auth when backend token is configured.

    Security: Token MUST be provided via Authorization header only.
    Query parameter token is NOT supported to prevent leakage in:
    - Server access logs
    - Browser history
    - Referer headers

    On success, binds a SimpleAuthContext to request.state for use by
    require_permission() and other dependencies within the same request.
    """
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        # Auth disabled — bind an anonymous context so require_permission
        # can distinguish "no auth configured" from "no token provided".
        request.state.auth_context = SimpleAuthContext(
            principal="anonymous",
            auth_token="",
            scopes=frozenset(),
        )
        return
    auth_header = request.headers.get("authorization", "")
    if not auth.check(auth_header):
        raise HTTPException(status_code=401, detail="unauthorized")
    # Token valid — bind an authenticated context.
    request.state.auth_context = SimpleAuthContext(
        principal="authenticated",
        auth_token=auth_header,
        scopes=frozenset({"*"}),  # Full access for valid token holders
    )


def require_permission(permission: str):
    """Factory for permission-checking dependency.

    Reads permission from the server-bound SimpleAuthContext (set by require_auth).
    The client can no longer forge permissions via request headers.

    Usage:
        @router.get("/admin", dependencies=[Depends(require_permission("admin"))])
    """

    def check_permission(request: Request) -> None:
        require_auth(request)
        ctx: SimpleAuthContext | None = getattr(request.state, "auth_context", None)
        if ctx is None:
            raise HTTPException(status_code=403, detail="permission denied")
        if not ctx.has_scope(permission):
            raise HTTPException(status_code=403, detail="permission denied")

    return check_permission
