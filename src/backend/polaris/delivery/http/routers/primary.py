"""Primary router for officially supported non-v2 routes.

This router contains health checks and other officially supported endpoints
that are not part of the /v2 API namespace.
"""

from typing import Any

from polaris.bootstrap.config import get_settings
from fastapi import APIRouter, HTTPException, status

primary_router = APIRouter(tags=["primary"])

# Global NATS connection state (will be managed by lifecycle)
_nats_connected: bool = False


def set_nats_connected(connected: bool) -> None:
    """Update global NATS connection state."""
    global _nats_connected
    _nats_connected = connected


def is_nats_connected() -> bool:
    """Check if NATS is connected."""
    return _nats_connected


@primary_router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> dict[str, Any]:
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "ok", "service": "polaris-backend", "version": "2.0.0"}


@primary_router.get("/ready")
async def readiness_check() -> dict[str, Any]:
    """Readiness probe for orchestration systems (Kubernetes, etc.)."""
    settings = get_settings()

    checks: dict[str, str] = {
        "api": "ok",
        "storage": "ok",
    }

    ready = True
    nats_status = "ok"

    if settings.nats.enabled:
        nats_ok = is_nats_connected()
        if not nats_ok:
            # Lazily verify live connection state from the default client.
            try:
                from polaris.infrastructure.messaging import get_default_client

                client = await get_default_client()
                nats_ok = bool(client and client.is_connected)
                set_nats_connected(nats_ok)
            except (RuntimeError, ValueError):
                nats_ok = False

        if not nats_ok:
            nats_status = "not_connected"
            if settings.nats.required:
                ready = False
                nats_status = "required_but_not_connected"
        checks["nats"] = nats_status

    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "ready": False,
                "checks": checks,
                "reason": "NATS required but not connected"
                if nats_status == "required_but_not_connected"
                else "service_unavailable",
            },
        )

    return {"ready": True, "checks": checks}


@primary_router.get("/live")
async def liveness_check() -> dict[str, Any]:
    """Liveness probe for container orchestration."""
    return {"alive": True, "timestamp": "ok"}
