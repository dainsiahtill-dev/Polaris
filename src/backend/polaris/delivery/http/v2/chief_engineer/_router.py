"""Shared router, regex, and cross-domain helpers for the Chief Engineer v2 package.

This is the lossless successor of module-level glue from the former single-file
``chief_engineer`` module. It owns the single :class:`fastapi.APIRouter` that the
sibling domain modules decorate, plus the small validation / coercion helpers
that have no test-patchable external dependency and are shared across blueprint,
diagnostics, governance and release-readiness domains.

Note on monkeypatch losslessness
--------------------------------
Provider/test patches target attributes on the public package
(``polaris.delivery.http.v2.chief_engineer.<Name>``). Sibling modules therefore
resolve those names *through the live package* at call time (``_ce.<Name>``).
The helpers here intentionally have no such dependency, so plain module-globals
are safe.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Request
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    get_state,
)
from polaris.delivery.http.workspace import (
    active_workspace_value,
    settings_with_workspace_override,
)

router = APIRouter(tags=["chief-engineer"])

_BLUEPRINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


# ═══════════════════════════════════════════════════════════════════════
# Shared Pydantic models that are referenced by sibling modules as type
# hints in helper signatures. They live in ``_schemas`` for lossless
# surface but are re-imported here only when needed by a helper signature;
# keep this list minimal.
# ═══════════════════════════════════════════════════════════════════════


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_blueprint_id(blueprint_id: str) -> str:
    token = str(blueprint_id or "").strip()
    if not _BLUEPRINT_ID_RE.fullmatch(token):
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_BLUEPRINT_ID",
            message="invalid blueprint id",
        )
    return token


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        if isinstance(item, str):
            token = item.strip()
        elif isinstance(item, dict):
            token = str(
                item.get("path")
                or item.get("file")
                or item.get("description")
                or item.get("text")
                or item.get("title")
                or item.get("name")
                or item.get("id")
                or item.get("value")
                or ""
            ).strip()
        else:
            token = str(item or "").strip()
        if token:
            rows.append(token)
    return rows


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pm_task_plan_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tasks = payload.get("tasks")
    if isinstance(raw_tasks, dict):
        return [item for item in raw_tasks.values() if isinstance(item, dict)]
    if isinstance(raw_tasks, list):
        return [item for item in raw_tasks if isinstance(item, dict)]
    return []


def _task_id_from_plan_task(task: dict[str, Any], index: int) -> str:
    for key in ("id", "task_id", "uid", "pm_task_id"):
        value = task.get(key)
        token = str(value or "").strip()
        if token:
            return token
    return f"task-{index}"


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _blueprint_task_id(payload: dict[str, Any]) -> str:
    sources = [
        payload,
        payload.get("raw") if isinstance(payload.get("raw"), dict) else None,
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
        payload.get("context") if isinstance(payload.get("context"), dict) else None,
    ]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("task_id", "pm_task_id", "taskId", "id"):
            token = str(source.get(key) or "").strip()
            if token:
                return token
    return ""


def _split_csv(value: str) -> list[str]:
    return [token.strip() for token in str(value or "").split(",") if token.strip()]


def _workspace_value(settings: Any) -> str:
    return active_workspace_value(settings)


def _settings_for_request(request: Request, workspace: str = "") -> Any:
    state = get_state(request)
    return settings_with_workspace_override(state.settings, workspace)


def _state_for_settings(request: Request, settings: Any) -> Any:
    state = get_state(request)
    if settings is state.settings:
        return state
    return SimpleNamespace(settings=settings)


def _governance_workspace(request: Request, workspace: str) -> str:
    """Resolve and validate the workspace for a governance request."""

    settings = _settings_for_request(request, workspace)
    target_workspace = _workspace_value(settings)
    if not target_workspace:
        raise StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_NOT_CONFIGURED",
            message="workspace is not configured",
        )
    return target_workspace


__all__ = [
    "_BLUEPRINT_ID_RE",
    "_blueprint_task_id",
    "_dict_value",
    "_governance_workspace",
    "_pm_task_plan_rows",
    "_read_json_file",
    "_settings_for_request",
    "_split_csv",
    "_state_for_settings",
    "_string_list",
    "_task_id_from_plan_task",
    "_utc_now",
    "_validate_blueprint_id",
    "_workspace_value",
    "router",
]
