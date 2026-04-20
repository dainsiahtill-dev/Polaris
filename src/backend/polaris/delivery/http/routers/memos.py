from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.runtime.projection.public import list_memos
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.kernelone.runtime.defaults import DEFAULT_WORKSPACE

router = APIRouter()


@router.get("/memos/list", dependencies=[Depends(require_auth)])
def get_memos(request: Request, limit: int = 200) -> dict[str, Any]:
    state = get_state(request)
    workspace = state.settings.workspace or DEFAULT_WORKSPACE
    ramdisk_root = state.settings.ramdisk_root or ""
    return list_memos(str(workspace), str(ramdisk_root), limit)
