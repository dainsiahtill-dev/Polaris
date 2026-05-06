from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger(__name__)


def _read_director_service_status_sync() -> dict[str, Any] | None:
    """Read DirectorService status in sync context."""

    try:
        from polaris.cells.director.execution.service import DirectorService
        from polaris.infrastructure.di.container import get_container
    except ImportError:
        # DirectorService subsystem not available in this deployment - not an error.
        return None

    async def _fetch() -> dict[str, Any] | None:
        try:
            container = await get_container()
            director_service = await container.resolve_async(DirectorService)
            payload = await director_service.get_status()
            return payload if isinstance(payload, dict) else None
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "DirectorService.get_status() failed during projection: %s",
                exc,
                exc_info=True,
            )
            return None

    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    try:
        if in_running_loop:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, _fetch())
                return future.result(timeout=5)
        return asyncio.run(_fetch())
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Sync bridge for DirectorService status failed: %s",
            exc,
            exc_info=True,
        )
        return None


def build_director_runtime_status(state: AppState, workspace: str, cache_root: str) -> dict[str, Any]:
    """Build Director runtime status from DirectorService only."""
    del state, workspace, cache_root
    v2_status = _read_director_service_status_sync()
    running = isinstance(v2_status, dict) and str(v2_status.get("state", "")).strip().upper() == "RUNNING"
    return {
        "running": running,
        "pid": None,
        "mode": "v2_service" if isinstance(v2_status, dict) else "",
        "started_at": (v2_status.get("started_at") if isinstance(v2_status, dict) else None),
        "log_path": "",
        "source": "v2_service" if isinstance(v2_status, dict) else "none",
        "status": v2_status if isinstance(v2_status, dict) else None,
    }
