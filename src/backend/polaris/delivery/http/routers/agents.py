import logging
import os
import shutil
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.runtime.artifact_store.public.service import resolve_safe_path
from polaris.cells.runtime.projection.public.service import format_mtime
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.delivery.http.schemas import AgentsApplyPayload, AgentsFeedbackPayload
from polaris.kernelone.runtime.defaults import (
    AGENTS_DRAFT_REL,
    AGENTS_FEEDBACK_REL,
    DEFAULT_WORKSPACE,
)
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

router = APIRouter()


@router.post("/agents/apply", dependencies=[Depends(require_auth)])
def apply_agents(request: Request, payload: AgentsApplyPayload) -> dict[str, Any]:
    state = get_state(request)
    workspace = state.settings.workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root("", str(workspace))
    draft_rel = payload.draft_path or AGENTS_DRAFT_REL
    draft_path = resolve_safe_path(str(workspace), str(cache_root), draft_rel)
    target_path = os.path.join(workspace, "AGENTS.md")
    if not os.path.isfile(draft_path):
        raise HTTPException(status_code=404, detail="draft not found")
    if os.path.isfile(target_path):
        raise HTTPException(status_code=409, detail="AGENTS.md already exists")
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copyfile(draft_path, target_path)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"failed to copy AGENTS.md: {exc}") from exc
    return {"ok": True, "target_path": target_path}


@router.post("/agents/feedback", dependencies=[Depends(require_auth)])
def save_agents_feedback(request: Request, payload: AgentsFeedbackPayload) -> dict[str, Any]:
    state = get_state(request)
    workspace = state.settings.workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root("", str(workspace))
    feedback_path = resolve_artifact_path(str(workspace), str(cache_root), AGENTS_FEEDBACK_REL)
    text = (payload.text or "").strip()
    if not text:
        # allow clearing feedback
        try:
            if os.path.isfile(feedback_path):
                os.remove(feedback_path)
        except (OSError, PermissionError) as exc:
            logging.getLogger("polaris.routers.agents").warning("Failed to clear feedback file: %s", exc)
        return {"ok": True, "cleared": True}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"## {timestamp}\n{text}\n"
    try:
        os.makedirs(os.path.dirname(feedback_path), exist_ok=True)
        with open(feedback_path, "w", encoding="utf-8") as handle:
            handle.write(content)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"failed to save feedback: {exc}") from exc
    return {"ok": True, "path": feedback_path, "mtime": format_mtime(feedback_path)}
