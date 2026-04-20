from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.runtime.artifact_store.public.service import resolve_safe_path
from polaris.cells.runtime.projection.public.service import format_mtime, read_file_tail
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.kernelone.runtime.defaults import DEFAULT_WORKSPACE
from polaris.kernelone.storage.io_paths import build_cache_root

router = APIRouter()


@router.get("/files/read", dependencies=[Depends(require_auth)])
def read_file(
    request: Request,
    path: str,
    tail_lines: int = 400,
    max_chars: int = 20000,
) -> dict[str, Any]:
    state = get_state(request)
    workspace = state.settings.workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root("", str(workspace))
    full_path = resolve_safe_path(str(workspace), str(cache_root), path)
    normalized = full_path.replace("\\", "/").lower()
    allow_fallback = not normalized.endswith("/dialogue.jsonl")
    content = read_file_tail(full_path, max_lines=tail_lines, max_chars=max_chars, allow_fallback=allow_fallback)
    return {
        "path": full_path,
        "rel_path": path,
        "mtime": format_mtime(full_path),
        "content": content,
    }
