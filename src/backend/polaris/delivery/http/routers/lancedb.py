from typing import Any

from fastapi import APIRouter, Depends
from polaris.application.health import get_lancedb_status
from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.schemas import LanceDBStatusResponse

router = APIRouter()


@router.get("/lancedb/status", dependencies=[Depends(require_auth)], response_model=LanceDBStatusResponse)  # DEPRECATED
def lancedb_status_endpoint() -> dict[str, Any]:
    return get_lancedb_status()


@router.get("/v2/lancedb/status", dependencies=[Depends(require_auth)], response_model=LanceDBStatusResponse)
def v2_lancedb_status_endpoint() -> dict[str, Any]:
    """Get LanceDB availability status."""
    return get_lancedb_status()
