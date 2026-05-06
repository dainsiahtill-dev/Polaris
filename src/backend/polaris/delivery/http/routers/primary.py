"""Primary router for officially supported non-v2 routes.

This router contains health checks and other officially supported endpoints
that are not part of the /v2 API namespace.
"""

from typing import Any

from fastapi import APIRouter, status
from polaris.bootstrap.config import get_settings
from polaris.delivery.http.routers._shared import StructuredHTTPException
from polaris.delivery.http.schemas.common import PrimaryHealthResponse, PrimaryLiveResponse, PrimaryReadyResponse

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


async def build_readiness_payload() -> dict[str, Any]:
    """Build canonical readiness checks shared by primary and v2 routes."""
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

    return {"ready": ready, "checks": checks}


@primary_router.get("/health", status_code=status.HTTP_200_OK, response_model=PrimaryHealthResponse)  # DEPRECATED
async def health_check() -> dict[str, Any]:
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "ok", "service": "polaris-backend", "version": "2.0.0"}


@primary_router.get("/ready", response_model=PrimaryReadyResponse)  # DEPRECATED
async def readiness_check() -> dict[str, Any]:
    """Readiness probe for orchestration systems (Kubernetes, etc.)."""
    payload = await build_readiness_payload()

    if not payload["ready"]:
        raise StructuredHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="NATS_REQUIRED_BUT_NOT_CONNECTED"
            if payload["checks"].get("nats") == "required_but_not_connected"
            else "SERVICE_UNAVAILABLE",
            message="NATS required but not connected"
            if payload["checks"].get("nats") == "required_but_not_connected"
            else "service_unavailable",
            details={
                "ready": False,
                "checks": payload["checks"],
            },
        )

    return payload


@primary_router.get("/live", response_model=PrimaryLiveResponse)  # DEPRECATED
async def liveness_check() -> dict[str, Any]:
    """Liveness probe for container orchestration."""
    return {"alive": True, "timestamp": "ok"}
