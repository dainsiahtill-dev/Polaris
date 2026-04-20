from typing import Any

from fastapi import APIRouter, Depends
from polaris.application.health import get_lancedb_status
from polaris.delivery.http.routers._shared import require_auth

router = APIRouter()


@router.get("/lancedb/status", dependencies=[Depends(require_auth)])
def lancedb_status_endpoint() -> dict[str, Any]:
    return get_lancedb_status()
