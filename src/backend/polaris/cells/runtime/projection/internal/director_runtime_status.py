from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.cells.runtime.projection.public.contracts import DirectorStatusObservationV1Error

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger(__name__)


def _read_director_service_status_sync(workspace: str) -> dict[str, Any] | None:
    """Read Director status through the bootstrap-bound observation port.

    Absence of an owner observation is a control-plane failure, not an idle
    Director state.  Callers therefore receive a typed error rather than an
    indistinguishable ``None`` on that path.
    """
    try:
        from polaris.cells.runtime.projection.internal.director_status_owner import (
            observe_director_status_owner_sync,
        )

        observation = observe_director_status_owner_sync(workspace)
        if not observation.available or observation.status is None:
            raise DirectorStatusObservationV1Error(
                "director_status_owner_unavailable",
                "Director status owner explicitly returned unavailable",
            )
        return dict(observation.status)
    except DirectorStatusObservationV1Error:
        raise
    except (ImportError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
        raise DirectorStatusObservationV1Error(
            "director_status_owner_query_failed",
            f"Director status observation failed during sync projection: {exc}",
        ) from exc


def build_director_runtime_status(state: AppState, workspace: str, cache_root: str) -> dict[str, Any]:
    """Build Director runtime status from the bound owner observation only."""
    del state, cache_root
    projection_error = ""
    try:
        v2_status = _read_director_service_status_sync(workspace)
        if not isinstance(v2_status, dict):
            raise DirectorStatusObservationV1Error(
                "invalid_director_status_owner_payload",
                "Director status owner returned no exact dict payload",
            )
    except DirectorStatusObservationV1Error as exc:
        projection_error = f"{exc.error_code}: {exc}"
        logger.warning("Director status observation unavailable during sync projection: %s", projection_error)
        v2_status = None
    except (ImportError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
        projection_error = f"director_status_owner_query_failed: {exc}"
        logger.warning("Director status observation unavailable during sync projection: %s", projection_error)
        v2_status = None
    running = isinstance(v2_status, dict) and str(v2_status.get("state", "")).strip().upper() == "RUNNING"
    result = {
        "running": running,
        "pid": None,
        "mode": "v2_service" if isinstance(v2_status, dict) else "",
        "started_at": (v2_status.get("started_at") if isinstance(v2_status, dict) else None),
        "log_path": "",
        "source": "v2_service" if isinstance(v2_status, dict) else "none",
        "status": v2_status if isinstance(v2_status, dict) else None,
    }
    if projection_error:
        result["projection_error"] = projection_error
    return result
