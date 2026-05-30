from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    active_workspace_value,
    get_state,
    require_auth,
)

router = APIRouter()

VALID_ACTION_STATUSES = {
    "approve": "approved",
    "approved": "approved",
    "reject": "rejected",
    "rejected": "rejected",
    "ignore": "ignored",
    "ignored": "ignored",
}


def _interventions_path(request: Request) -> Path:
    state = get_state(request)
    workspace = active_workspace_value(state.settings)
    if not workspace:
        raise StructuredHTTPException(status_code=400, detail="workspace is not configured")
    return Path(workspace).resolve() / "INTERVENTIONS.json"


def _load_interventions(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "interventions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StructuredHTTPException(status_code=500, detail=f"invalid INTERVENTIONS.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise StructuredHTTPException(status_code=500, detail="invalid INTERVENTIONS.json: root must be an object")
    interventions = payload.get("interventions")
    if not isinstance(interventions, list):
        payload["interventions"] = []
    return payload


def _store_interventions(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@router.get("/interventions/list", dependencies=[Depends(require_auth)])
def list_interventions(request: Request) -> dict[str, Any]:
    """Return workspace intervention items for the desktop Intervention Center."""

    return _load_interventions(_interventions_path(request))


@router.post("/interventions/action", dependencies=[Depends(require_auth)])
async def apply_intervention_action(request: Request) -> dict[str, Any]:
    """Apply a simple action to a workspace intervention item."""

    body = await request.json()
    if not isinstance(body, dict):
        raise StructuredHTTPException(status_code=400, detail="request body must be an object")
    intervention_id = str(body.get("id") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    if not intervention_id:
        raise StructuredHTTPException(status_code=400, detail="intervention id is required")
    status = VALID_ACTION_STATUSES.get(action)
    if not status:
        raise StructuredHTTPException(status_code=400, detail=f"unsupported intervention action: {action or '(empty)'}")

    path = _interventions_path(request)
    payload = _load_interventions(path)
    interventions = payload.get("interventions")
    if not isinstance(interventions, list):
        interventions = []
        payload["interventions"] = interventions

    for item in interventions:
        if isinstance(item, dict) and str(item.get("id") or "") == intervention_id:
            item["status"] = status
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            _store_interventions(path, payload)
            return {"ok": True, "id": intervention_id, "status": status}

    raise StructuredHTTPException(status_code=404, detail=f"intervention not found: {intervention_id}")
