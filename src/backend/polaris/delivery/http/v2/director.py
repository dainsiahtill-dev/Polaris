"""Director API v2 routes.

Phase 6 Update: 新增统一编排兼容端点，内部可转发到 UnifiedOrchestrationService
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status

# Phase 6: 统一编排集成
from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
from polaris.cells.orchestration.workflow_runtime.public.service import (
    OrchestrationError,
    get_orchestration_service,
)
from polaris.cells.roles.kernel.public.service import (
    get_global_emitter,
    get_global_token_budget,
)
from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path

# Backward-compat namespace re-exports. These names were importable as module
# attributes (``director.<name>``) before the model classes moved to
# ``director_models``; the model bodies that referenced them now live there.
# They are re-imported here purely to preserve the module surface for any
# caller or test that resolved them via this module.
from polaris.cells.runtime.projection.public.role_contracts import (  # noqa: F401
    RoleTaskContractV1,
)
from polaris.cells.runtime.projection.public.service import (
    RuntimeProjectionService,
    build_cache_root,
    build_llm_status,
    build_workflow_status_payload,
    build_workflow_task_rows,
    get_workflow_runtime_status,
    merge_director_status,
    select_task_rows_from_projection,
)
from polaris.cells.runtime.task_market.public.contracts import QueryTaskMarketStatusV1
from polaris.cells.runtime.task_market.public.service import get_task_market_service
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.http.dependencies import (
    get_director_service as get_director_service_dep,
    require_auth,
)
from polaris.delivery.http.routers._shared import StructuredHTTPException, ensure_required_roles_ready
from polaris.delivery.http.schemas.common import RoleCapabilitiesResponse
from polaris.delivery.http.v2.director_models import (
    DirectorDiagnosticsLLMSection,
    DirectorDiagnosticsResponse,
    DirectorDiagnosticsStatusSection,
    DirectorDiagnosticsTaskSection,
    DirectorDiagnosticsWorkerSection,
    DirectorIntegrationQaRequest,
    DirectorIntegrationQaResponse,
    DirectorOrchestrationResponse,
    DirectorRunOrchestrationRequest,
    DirectorStatusResponse,  # noqa: F401  # re-exported for backward-compat callers/tests
    TaskCreateRequest,
    TaskResponse,
)
from polaris.delivery.http.v2.llm_event_filters import filter_llm_events_by_workspace
from polaris.delivery.http.workspace import (
    active_workspace_value,
    requested_or_active_workspace,
    settings_with_workspace_override,
    workspace_values_match,
)
from polaris.domain.entities import TaskPriority
from polaris.kernelone._runtime_config import resolve_env_str
from polaris.kernelone.constants import (  # noqa: F401
    DEFAULT_DIRECTOR_MAX_PARALLELISM,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
)
from pydantic import BaseModel, Field  # noqa: F401

if TYPE_CHECKING:
    from polaris.cells.director.execution.public.service import DirectorService

logger = logging.getLogger(__name__)


# Deprecation removal target: 2026-06-30.
# Backward-compat re-export for tests.
# Tests should import merge_director_status directly from
# polaris.cells.runtime.projection.public.service.
# This alias will be removed in v2.0.
def _merge_director_status(*args, **kwargs):
    warnings.warn(
        "_merge_director_status re-export is deprecated. "
        "Import merge_director_status from "
        "polaris.cells.runtime.projection.public.service instead. "
        "Will be removed in v2.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    return merge_director_status(*args, **kwargs)


router = APIRouter(prefix="/director", tags=["Director v2"])


def _append_debug(event: str, payload: dict[str, Any]) -> None:
    log_target = str(os.environ.get("KERNELONE_BACKEND_DEBUG_LOG", "") or "").strip()
    if not log_target:
        return

    try:
        log_path = Path(log_target)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "event": event,
            "payload": payload,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        # Event logging failure should not break main flow, but visibility is compromised.
        logger.debug("Director debug event append failed: %s", exc, exc_info=True)


def _state_token(payload: dict[str, Any]) -> str:
    state = str(payload.get("state") or "").strip().upper()
    if state:
        return state
    nested_status = payload.get("status")
    if isinstance(nested_status, dict):
        nested_state = str(nested_status.get("state") or "").strip().upper()
        if nested_state:
            return nested_state
    if bool(payload.get("running")):
        return "RUNNING"
    return "IDLE"


def _flatten_director_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    local_payload = payload if isinstance(payload, dict) else {}
    state_token = _state_token(local_payload)
    running = bool(local_payload.get("running")) or state_token == "RUNNING"
    flattened = dict(local_payload)
    flattened["running"] = running
    flattened["state"] = state_token
    flattened.setdefault("status", local_payload.get("status") or {"state": state_token})
    flattened.setdefault("source", str(local_payload.get("source") or "none"))
    return flattened


def _director_tasks_queued(result: Any, requested_task_ids: list[str]) -> int:
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict):
        queued = metadata.get("tasks_queued")
        if isinstance(queued, int) and not isinstance(queued, bool) and queued >= 0:
            return queued
        if isinstance(queued, str) and queued.strip().isdigit():
            return int(queued.strip())
        task_ids = metadata.get("task_ids")
        if isinstance(task_ids, list):
            return len([item for item in task_ids if str(item).strip()])
    return len(requested_task_ids)


def _director_run_task_ids_from_diagnostics(
    diagnostics: Any,
    explicit_task_ids: list[str],
) -> tuple[list[str], str]:
    """Resolve Director run task IDs from explicit request or diagnostics."""

    normalized_explicit = [str(item).strip() for item in explicit_task_ids if str(item).strip()]
    if normalized_explicit:
        return normalized_explicit, "explicit_request"

    task_section = getattr(diagnostics, "tasks", None)
    candidate_sources = (
        ("diagnostics_blueprint_ready", getattr(task_section, "blueprint_ready_task_ids", None)),
        ("diagnostics_ready", getattr(task_section, "ready_task_ids", None)),
    )
    seen: set[str] = set()
    selected: list[str] = []
    selected_sources: list[str] = []
    for source, values in candidate_sources:
        if not isinstance(values, list):
            continue
        added_from_source = False
        for item in values:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            selected.append(token)
            added_from_source = True
        if added_from_source:
            selected_sources.append(source)
    if selected:
        if len(selected_sources) == 1:
            return selected, selected_sources[0]
        return selected, "diagnostics_mixed_ready"
    return [], "none"


def _projection_task_rows(projection: Any) -> list[dict[str, Any]]:
    projection_source = _projection_source_for_task_rows(projection)
    rows = getattr(projection, "task_rows", None)
    if isinstance(rows, list) and rows:
        return [
            _with_task_projection_source(item, fallback_source=projection_source)
            for item in rows
            if isinstance(item, dict)
        ]
    selected = select_task_rows_from_projection(projection)
    if selected:
        return [
            _with_task_projection_source(item, fallback_source=projection_source)
            for item in selected
            if isinstance(item, dict)
        ]
    snapshot = getattr(projection, "snapshot", None)
    snapshot_tasks = snapshot.get("tasks") if isinstance(snapshot, dict) else None
    if not isinstance(snapshot_tasks, list):
        return []

    fallback_rows: list[dict[str, Any]] = []
    for item in snapshot_tasks:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or item.get("task_id") or "").strip()
        if not task_id:
            continue
        raw_metadata = item.get("metadata")
        metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        metadata.setdefault("pm_task_id", task_id)
        status_token = str(item.get("status") or "PENDING").strip().upper()
        if status_token in {"TODO", "TO_DO"}:
            status_token = "PENDING"
        fallback_rows.append(
            _with_task_projection_source(
                {
                    "id": task_id,
                    "subject": str(item.get("subject") or item.get("title") or task_id).strip(),
                    "description": str(item.get("description") or item.get("goal") or "").strip(),
                    "status": status_token,
                    "priority": str(item.get("priority") or "MEDIUM").strip() or "MEDIUM",
                    "claimed_by": item.get("claimed_by"),
                    "result": item.get("result") if isinstance(item.get("result"), dict) else None,
                    "metadata": metadata,
                },
                fallback_source=projection_source,
            )
        )
    return fallback_rows


def _task_market_row_to_director_task_row(item: dict[str, Any]) -> dict[str, Any] | None:
    task_id = str(item.get("task_id") or item.get("id") or "").strip()
    if not task_id:
        return None
    payload = _as_dict(item.get("payload"))
    item_metadata = _as_dict(item.get("metadata"))
    metadata = dict(item_metadata)
    metadata.setdefault("pm_task_id", task_id)
    metadata.setdefault("source_task_id", task_id)
    metadata.setdefault("task_market_stage", str(item.get("stage") or "").strip())
    metadata.setdefault("task_market_status", str(item.get("status") or "").strip())
    metadata.setdefault("task_market_source", "runtime.task_market")
    metadata.setdefault("projection_source", "runtime.task_market")
    for key in (
        "route",
        "task_market_route",
        "blueprint_required",
        "blueprint_id",
        "blueprint_path",
        "runtime_blueprint_path",
        "scope_paths",
        "target_files",
        "acceptance_criteria",
    ):
        if key in payload and key not in metadata:
            metadata[key] = payload[key]

    return {
        "id": task_id,
        "task_id": task_id,
        "pm_task_id": task_id,
        "subject": str(payload.get("title") or item.get("title") or task_id).strip(),
        "title": str(payload.get("title") or task_id).strip(),
        "description": str(payload.get("goal") or payload.get("description") or "").strip(),
        "goal": str(payload.get("goal") or "").strip(),
        "status": str(item.get("status") or item.get("stage") or "pending_exec").strip(),
        "priority": str(item.get("priority") or "MEDIUM").strip() or "MEDIUM",
        "claimed_by": item.get("claimed_by"),
        "worker": item.get("claimed_by"),
        "target_files": payload.get("target_files") if isinstance(payload.get("target_files"), list) else [],
        "scope_paths": payload.get("scope_paths") if isinstance(payload.get("scope_paths"), list) else [],
        "acceptance_criteria": (
            payload.get("acceptance_criteria") if isinstance(payload.get("acceptance_criteria"), list) else []
        ),
        "depends_on": item.get("depends_on") if isinstance(item.get("depends_on"), list) else [],
        "blueprint_id": payload.get("blueprint_id") or metadata.get("blueprint_id"),
        "blueprint_path": payload.get("blueprint_path") or metadata.get("blueprint_path"),
        "runtime_blueprint_path": payload.get("runtime_blueprint_path") or metadata.get("runtime_blueprint_path"),
        "result": None,
        "metadata": metadata,
    }


def _task_market_execution_rows_for_workspace(workspace: str) -> list[dict[str, Any]]:
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []
    try:
        status = get_task_market_service().query_status(
            QueryTaskMarketStatusV1(
                workspace=workspace_token,
                stage="pending_exec",
                include_payload=True,
                limit=5000,
            )
        )
    except (RuntimeError, OSError, TypeError, ValueError):
        logger.debug("Director task-market rows unavailable for workspace=%s", workspace_token, exc_info=True)
        return []
    rows: list[dict[str, Any]] = []
    for item in getattr(status, "items", ()) or ():
        if not isinstance(item, dict):
            continue
        row = _task_market_row_to_director_task_row(item)
        if row is not None:
            rows.append(row)
    return rows


def _merge_task_rows_by_identity(
    primary_rows: list[dict[str, Any]],
    overlay_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not primary_rows:
        return list(overlay_rows)
    if not overlay_rows:
        return list(primary_rows)

    merged_rows: list[dict[str, Any]] = [dict(row) for row in primary_rows]
    index_by_token: dict[str, int] = {}
    for index, row in enumerate(merged_rows):
        details = _task_details(row)
        for token in _task_identity_tokens(_task_id_from_row(row), details):
            index_by_token.setdefault(token, index)

    for overlay in overlay_rows:
        overlay_id = _task_id_from_row(overlay)
        overlay_details = _task_details(overlay)
        target_index = None
        for token in _task_identity_tokens(overlay_id, overlay_details):
            if token in index_by_token:
                target_index = index_by_token[token]
                break
        if target_index is None:
            index_by_token[overlay_id] = len(merged_rows)
            merged_rows.append(dict(overlay))
            continue

        current = merged_rows[target_index]
        merged = dict(current)
        merged.update(overlay)
        current_metadata = _as_dict(current.get("metadata"))
        overlay_metadata = _as_dict(overlay.get("metadata"))
        metadata = dict(current_metadata)
        metadata.update(overlay_metadata)
        merged["metadata"] = metadata
        merged_rows[target_index] = merged
    return merged_rows


def _contract_backed_task_rows(
    task_rows: list[dict[str, Any]],
    *,
    workspace: str,
    cache_root: str,
) -> list[dict[str, Any]]:
    """Use the PM contract as Director diagnostics' full task universe."""

    contract_rows = build_workflow_task_rows({}, workspace=workspace, cache_root=cache_root)
    contract_rows = [dict(row) for row in contract_rows if isinstance(row, dict)]
    if not contract_rows:
        return task_rows

    runtime_by_token: dict[str, dict[str, Any]] = {}
    for row in task_rows:
        task_id = _task_id_from_row(row)
        details = _task_details(row)
        for token in _task_identity_tokens(task_id, details):
            runtime_by_token.setdefault(token, row)

    merged_rows: list[dict[str, Any]] = []
    matched_runtime_ids: set[int] = set()
    for contract in contract_rows:
        contract_id = str(contract.get("id") or contract.get("task_id") or "").strip()
        metadata_raw = contract.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        contract_tokens = {
            token
            for token in (
                contract_id,
                str(contract.get("pm_task_id") or "").strip(),
                str(metadata.get("pm_task_id") or "").strip(),
                str(metadata.get("source_task_id") or "").strip(),
                str(metadata.get("external_task_id") or "").strip(),
            )
            if token
        }
        runtime_row = next(
            (runtime_by_token.get(token) for token in contract_tokens if token in runtime_by_token), None
        )
        if runtime_row is None:
            normalized = dict(contract)
            normalized.setdefault("status", "PENDING")
            normalized_metadata_raw = normalized.get("metadata")
            normalized_metadata: dict[str, Any] = (
                normalized_metadata_raw if isinstance(normalized_metadata_raw, dict) else {}
            )
            normalized_metadata.setdefault("pm_task_id", contract_id)
            normalized["metadata"] = normalized_metadata
            merged_rows.append(_with_task_projection_source(normalized, fallback_source="pm_task_contract"))
            continue

        matched_runtime_ids.add(id(runtime_row))
        merged = dict(contract)
        merged.update(runtime_row)
        runtime_metadata_raw = runtime_row.get("metadata")
        runtime_metadata: dict[str, Any] = runtime_metadata_raw if isinstance(runtime_metadata_raw, dict) else {}
        contract_metadata_raw = contract.get("metadata")
        contract_metadata: dict[str, Any] = contract_metadata_raw if isinstance(contract_metadata_raw, dict) else {}
        merged_metadata = dict(contract_metadata)
        merged_metadata.update(runtime_metadata)
        if contract_id:
            merged_metadata.setdefault("pm_task_id", contract_id)
        merged["metadata"] = merged_metadata
        for key in ("dependencies", "depends_on", "blocked_by", "blockedBy"):
            if not merged.get(key) and contract.get(key):
                merged[key] = contract.get(key)
        merged_rows.append(_with_task_projection_source(merged, fallback_source="pm_task_contract"))

    for row in task_rows:
        if id(row) not in matched_runtime_ids:
            merged_rows.append(_with_task_projection_source(row, fallback_source="runtime_projection"))
    return merged_rows


def _runtime_task_rows_for_workspace(workspace: str) -> list[dict[str, Any]]:
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []
    try:
        runtime_rows = TaskRuntimeService(workspace_token).list_task_rows()
    except (RuntimeError, ValueError, OSError):
        logger.debug("Director diagnostics runtime task overlay failed for workspace=%s", workspace, exc_info=True)
        return []
    return [dict(row) for row in runtime_rows if isinstance(row, dict)]


def _is_workflow_shell_task(row: dict[str, Any]) -> bool:
    metadata = _as_dict(row.get("metadata"))
    task_id = str(row.get("id") or row.get("task_id") or "").strip()
    workflow_task_id = str(metadata.get("workflow_task_id") or "").strip()
    has_pm_identity = any(
        str(value or "").strip()
        for value in (
            row.get("pm_task_id"),
            metadata.get("pm_task_id"),
            metadata.get("source_task_id"),
            metadata.get("external_task_id"),
        )
    )
    if has_pm_identity:
        return False
    shell_ids = {task_id, workflow_task_id}
    return any(token.startswith("task-") and token.endswith("-director") for token in shell_ids if token)


def _runtime_backed_task_rows(
    task_rows: list[dict[str, Any]],
    *,
    workspace: str,
) -> list[dict[str, Any]]:
    """Overlay canonical task runtime rows so diagnostics honor lease expiry."""

    runtime_rows = _runtime_task_rows_for_workspace(workspace)
    if not runtime_rows:
        return task_rows

    runtime_by_token: dict[str, dict[str, Any]] = {}
    for runtime_task_row in runtime_rows:
        runtime_task_id = _task_id_from_row(runtime_task_row)
        runtime_details = _task_details(runtime_task_row)
        for token in _task_identity_tokens(runtime_task_id, runtime_details):
            runtime_by_token.setdefault(token, runtime_task_row)

    merged_rows: list[dict[str, Any]] = []
    matched_runtime_ids: set[int] = set()
    for row in task_rows:
        task_id = _task_id_from_row(row)
        details = _task_details(row)
        runtime_row: dict[str, Any] | None = None
        for token in _task_identity_tokens(task_id, details):
            candidate = runtime_by_token.get(token)
            if candidate is not None:
                runtime_row = candidate
                break
        if runtime_row is None:
            merged_rows.append(row)
            continue

        matched_runtime_ids.add(id(runtime_row))
        merged = dict(row)
        merged.update(runtime_row)
        row_metadata_raw = row.get("metadata")
        row_metadata: dict[str, Any] = row_metadata_raw if isinstance(row_metadata_raw, dict) else {}
        runtime_metadata_raw = runtime_row.get("metadata")
        runtime_metadata: dict[str, Any] = runtime_metadata_raw if isinstance(runtime_metadata_raw, dict) else {}
        metadata = dict(row_metadata)
        metadata.update(runtime_metadata)
        merged["metadata"] = metadata
        for key in ("dependencies", "depends_on", "blocked_by", "blockedBy"):
            if not merged.get(key) and row.get(key):
                merged[key] = row.get(key)
        merged_rows.append(_with_task_projection_source(merged, fallback_source="runtime.task_runtime"))

    for runtime_row in runtime_rows:
        if id(runtime_row) not in matched_runtime_ids:
            merged_rows.append(_with_task_projection_source(runtime_row, fallback_source="runtime.task_runtime"))

    if runtime_rows:
        merged_rows = [row for row in merged_rows if not _is_workflow_shell_task(row)]

    return merged_rows


def _task_row_matches_id(row: dict[str, Any], task_id: str) -> bool:
    requested = str(task_id or "").strip()
    if not requested:
        return False

    metadata = _as_dict(row.get("metadata"))
    candidates = {
        row.get("id"),
        row.get("task_id"),
        row.get("pm_task_id"),
        metadata.get("pm_task_id"),
        metadata.get("external_task_id"),
        metadata.get("source_task_id"),
    }
    return requested in {str(candidate).strip() for candidate in candidates if candidate is not None}


def _cancel_success_payload(task_id: str, result: Any) -> dict[str, Any] | None:
    if isinstance(result, bool):
        return {"ok": True, "task_id": task_id} if result else None

    if not isinstance(result, dict):
        return None

    status_token = _normalize_task_status_token(result.get("status"))
    accepted = result.get("ok") is True or result.get("cancelled") is True or status_token == "CANCELLED"
    if not accepted:
        return None

    payload = dict(result)
    payload["ok"] = True
    payload.setdefault("task_id", task_id)
    return payload


def _cancel_failure_detail(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("error", "detail", "message"):
            text = _text_or_none(result.get(key))
            if text:
                return text
    return "Task cannot be cancelled"


def _workspace_from_request(request: Request, workspace: str | None = None) -> str:
    state = getattr(request.app.state, "app_state", None) or request.app.state
    return requested_or_active_workspace(state.settings, workspace or "")


def _ensure_snapshot_workspace(request: Request, snapshot: Any, run_id: str, workspace: str) -> None:
    """Hide orchestration runs that do not belong to the requested desktop workspace."""

    if not str(workspace or "").strip():
        return
    resolved_workspace = _workspace_from_request(request, workspace)
    if not workspace_values_match(getattr(snapshot, "workspace", ""), resolved_workspace):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run not found: {run_id}",
        )


async def _projected_task_response(request: Request, task_id: str, workspace: str | None = None) -> TaskResponse | None:
    """Return a workflow/projection task detail when local Director queue misses."""
    state = getattr(request.app.state, "app_state", None) or request.app.state
    resolved_workspace = _workspace_from_request(request, workspace)

    projection_rows: list[dict[str, Any]] = []
    try:
        projection = await RuntimeProjectionService.build_async(resolved_workspace, state=state)
    except (RuntimeError, ValueError, TypeError, AttributeError):
        logger.debug("Failed to build Director task projection for task_id=%s", task_id, exc_info=True)
    else:
        projection_rows = _projection_task_rows(projection)

    for row in projection_rows:
        if _task_row_matches_id(row, task_id):
            return _task_response_from_row(row)
    for row in _task_market_execution_rows_for_workspace(resolved_workspace):
        if _task_row_matches_id(row, task_id):
            return _task_response_from_row(row)
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _text_or_none(value)
        if text:
            return text
    return None


def _projection_source_for_task_rows(projection: Any) -> str:
    """Return the best available provenance label for task projection rows."""

    workflow_archive = getattr(projection, "workflow_archive", None)
    if isinstance(workflow_archive, dict) and workflow_archive:
        return "workflow_archive"

    director_merged = getattr(projection, "director_merged", None)
    if isinstance(director_merged, dict) and director_merged:
        return "director_merged"

    director_local = getattr(projection, "director_local", None)
    if isinstance(director_local, dict) and director_local:
        return "director_local"

    return "runtime_projection"


def _with_task_projection_source(row: dict[str, Any], *, fallback_source: str) -> dict[str, Any]:
    normalized = dict(row)
    metadata = dict(_as_dict(normalized.get("metadata")))
    projection_source = _first_text(
        metadata.get("projection_source"),
        normalized.get("projection_source"),
        metadata.get("materialized_by"),
        normalized.get("materialized_by"),
        metadata.get("task_market_source"),
        normalized.get("task_market_source"),
        metadata.get("director_task_source"),
        normalized.get("director_task_source"),
        metadata.get("source"),
        normalized.get("source"),
        fallback_source,
    )
    if projection_source and not _text_or_none(metadata.get("projection_source")):
        metadata["projection_source"] = projection_source
    normalized["metadata"] = metadata
    return normalized


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, (list, tuple, set)):
        scalar_text = _text_or_none(value)
        return [scalar_text] if scalar_text else []

    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            item_text = _first_text(
                item.get("description"),
                item.get("text"),
                item.get("title"),
                item.get("name"),
                item.get("path"),
                item.get("file"),
                item.get("id"),
                item.get("value"),
            )
        else:
            item_text = _text_or_none(item)
        if item_text and item_text not in seen:
            seen.add(item_text)
            items.append(item_text)
    return items


def _first_string_list(*values: Any) -> list[str]:
    for value in values:
        items = _string_list(value)
        if items:
            return items
    return []


def _normalize_task_status_token(value: Any) -> str:
    token = str(value or "").strip().upper().replace("-", "_")
    aliases = {
        "": "PENDING",
        "TODO": "PENDING",
        "TO_DO": "PENDING",
        "QUEUED": "PENDING",
        "READY": "PENDING",
        "PENDING": "PENDING",
        "PENDING_EXEC": "PENDING",
        "PENDING_DESIGN": "PENDING",
        "PENDING_QA": "PENDING",
        "CLAIMED": "CLAIMED",
        "IN_PROGRESS": "RUNNING",
        "IN_EXECUTION": "RUNNING",
        "IN_DESIGN": "RUNNING",
        "IN_QA": "RUNNING",
        "RUNNING": "RUNNING",
        "EXECUTING": "RUNNING",
        "ACTIVE": "RUNNING",
        "BLOCKED": "BLOCKED",
        "FAILED": "FAILED",
        "ERROR": "FAILED",
        "TIMEOUT": "FAILED",
        "TIMED_OUT": "FAILED",
        "COMPLETED": "COMPLETED",
        "DONE": "COMPLETED",
        "SUCCESS": "COMPLETED",
        "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED",
    }
    return aliases.get(token, token or "PENDING")


def _task_row_from_object(task: Any) -> dict[str, Any]:
    if isinstance(task, dict):
        return _with_task_projection_source(task, fallback_source="director_local")

    result = getattr(task, "result", None)
    result_payload = result.to_dict() if result and hasattr(result, "to_dict") else result
    metadata = getattr(task, "metadata", None)
    status_value = getattr(getattr(task, "status", None), "name", None) or getattr(task, "status", None)
    priority_value = getattr(getattr(task, "priority", None), "name", None) or getattr(task, "priority", None)
    return _with_task_projection_source(
        {
            "id": str(getattr(task, "id", "")),
            "subject": getattr(task, "subject", ""),
            "description": getattr(task, "description", ""),
            "status": status_value,
            "priority": priority_value,
            "claimed_by": getattr(task, "claimed_by", None),
            "result": result_payload if isinstance(result_payload, dict) else None,
            "metadata": metadata if isinstance(metadata, dict) else {},
        },
        fallback_source="director_local",
    )


def _task_rows_from_local_tasks(tasks: list[Any]) -> list[dict[str, Any]]:
    return [_task_row_from_object(task) for task in tasks]


def _task_id_from_row(row: dict[str, Any]) -> str:
    metadata = _as_dict(row.get("metadata"))
    return str(row.get("id") or row.get("task_id") or row.get("pm_task_id") or metadata.get("pm_task_id") or "").strip()


def _task_details(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _as_dict(row.get("metadata"))
    runtime_execution = _as_dict(metadata.get("runtime_execution"))
    result = _as_dict(row.get("result"))
    status_token = _normalize_task_status_token(
        row.get("status") or runtime_execution.get("effective_status") or runtime_execution.get("status")
    )
    worker = _first_text(
        row.get("worker"),
        row.get("claimed_by"),
        row.get("assignee"),
        metadata.get("worker"),
        metadata.get("worker_id"),
        metadata.get("assigned_worker"),
        metadata.get("claimed_by"),
        metadata.get("last_claimed_by"),
        runtime_execution.get("worker_id"),
        runtime_execution.get("claimed_by"),
    )
    error = _first_text(
        row.get("error"),
        row.get("error_message"),
        row.get("last_error"),
        metadata.get("error"),
        metadata.get("last_error"),
        metadata.get("last_execution_error"),
        runtime_execution.get("last_error"),
        result.get("error"),
        result.get("stderr"),
    )
    if status_token == "FAILED":
        error = error or _first_text(result.get("summary"), row.get("result_summary"))

    return {
        "status": status_token,
        "goal": _first_text(row.get("goal"), metadata.get("goal"), metadata.get("task_goal"), row.get("description"))
        or "",
        "acceptance": _first_string_list(
            row.get("acceptance"),
            row.get("acceptance_criteria"),
            metadata.get("acceptance"),
            metadata.get("acceptance_criteria"),
            _as_dict(metadata.get("qa_contract")).get("acceptance_criteria"),
        ),
        "target_files": _first_string_list(
            row.get("target_files"),
            metadata.get("target_files"),
            metadata.get("scope_paths"),
        ),
        "dependencies": _first_string_list(
            row.get("dependencies"),
            row.get("depends_on"),
            row.get("blocked_by"),
            row.get("blockedBy"),
            metadata.get("dependencies"),
            metadata.get("depends_on"),
            metadata.get("blocked_by"),
        ),
        "current_file": _first_text(
            row.get("current_file"),
            metadata.get("current_file"),
            metadata.get("current_file_path"),
            runtime_execution.get("current_file"),
        ),
        "error": error,
        "worker": worker,
        "pm_task_id": _first_text(
            row.get("pm_task_id"),
            metadata.get("pm_task_id"),
            metadata.get("external_task_id"),
            metadata.get("source_task_id"),
        ),
        "blueprint_id": _first_text(
            row.get("blueprint_id"),
            row.get("blueprintId"),
            metadata.get("blueprint_id"),
            metadata.get("blueprintId"),
        ),
        "blueprint_path": _first_text(
            row.get("blueprint_path"),
            row.get("runtime_blueprint_path"),
            row.get("blueprintPath"),
            metadata.get("blueprint_path"),
            metadata.get("runtime_blueprint_path"),
            metadata.get("blueprintPath"),
        ),
        "runtime_blueprint_path": _first_text(
            row.get("runtime_blueprint_path"),
            metadata.get("runtime_blueprint_path"),
            row.get("blueprint_path"),
            metadata.get("blueprint_path"),
        ),
    }


def _row_requires_blueprint_evidence(row: dict[str, Any], *, source: str) -> bool:
    metadata = _as_dict(row.get("metadata"))
    payload = _as_dict(row.get("payload"))
    for container in (row, metadata, payload):
        route = (
            str(
                container.get("task_market_route")
                or container.get("route")
                or container.get("routing")
                or container.get("dispatch_route")
                or ""
            )
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        if route in {"direct_to_director", "direct", "director", "director_direct", "pending_exec"}:
            return False
        if route in {
            "chief_blueprint_required",
            "chief",
            "chief_engineer",
            "blueprint",
            "blueprint_required",
            "pending_design",
        }:
            return True
        raw_required = container.get("blueprint_required")
        if isinstance(raw_required, bool):
            return raw_required
        if isinstance(raw_required, str):
            token = raw_required.strip().lower()
            if token in {"1", "true", "yes", "y", "on"}:
                return True
            if token in {"0", "false", "no", "n", "off"}:
                return False
    return source == "workflow"


def _task_response_from_row(row: dict[str, Any]) -> TaskResponse:
    details = _task_details(row)
    result = row.get("result")
    return TaskResponse(
        id=str(row.get("id") or row.get("task_id") or ""),
        subject=str(row.get("subject") or row.get("title") or row.get("id") or "").strip(),
        description=str(row.get("description") or "").strip(),
        status=details["status"],
        priority=str(row.get("priority") or "MEDIUM").strip() or "MEDIUM",
        claimed_by=details["worker"],
        result=result if isinstance(result, dict) else None,
        metadata=_as_dict(row.get("metadata")),
        goal=details["goal"],
        acceptance=details["acceptance"],
        target_files=details["target_files"],
        dependencies=details["dependencies"],
        current_file=details["current_file"],
        error=details["error"],
        worker=details["worker"],
        pm_task_id=details["pm_task_id"],
        blueprint_id=details["blueprint_id"],
        blueprint_path=details["blueprint_path"],
        runtime_blueprint_path=details["runtime_blueprint_path"],
    )


def _get_workflow_snapshot_sync(
    workspace: str,
    *,
    ramdisk_root: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    workspace_value = str(workspace or "").strip()
    if not workspace_value:
        return None, []
    runtime_root = str(ramdisk_root or resolve_env_str("ramdisk_root") or "").strip()
    try:
        if not runtime_root:
            from polaris.bootstrap.config import get_settings

            settings = get_settings()
            runtime_root = str(getattr(settings, "ramdisk_root", "") or "").strip()
        cache_root = build_cache_root(runtime_root, workspace_value)
        workflow_status = get_workflow_runtime_status(workspace_value, cache_root)
    except (RuntimeError, ValueError):
        # Workflow status unavailable - return empty to maintain graceful degradation
        logger.debug("Failed to get workflow status for workspace=%s", workspace_value)
        return None, []

    status_payload = build_workflow_status_payload(
        workflow_status,
        workspace=workspace_value,
        cache_root=cache_root,
    )
    if not isinstance(status_payload, dict):
        return None, []
    task_rows = build_workflow_task_rows(
        workflow_status,
        workspace=workspace_value,
        cache_root=cache_root,
    )
    return status_payload, task_rows


async def _get_workflow_snapshot(
    workspace: str,
    *,
    ramdisk_root: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    return await asyncio.to_thread(
        _get_workflow_snapshot_sync,
        workspace,
        ramdisk_root=ramdisk_root,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Request/Response models are declared in ``director_models`` and re-exported
# at the top of this module (see ``from .director_models import ...``).


def _director_snapshot_status(snapshot: Any) -> str:
    status_value = getattr(snapshot, "status", None)
    value = getattr(status_value, "value", status_value)
    return str(value or "unknown")


def _director_snapshot_task_count(snapshot: Any) -> int:
    tasks = getattr(snapshot, "tasks", None)
    if isinstance(tasks, (dict, list, tuple, set)):
        return len(tasks)
    return 0


def _director_orchestration_response(snapshot: Any) -> DirectorOrchestrationResponse:
    status_value = _director_snapshot_status(snapshot)
    return DirectorOrchestrationResponse(
        run_id=str(snapshot.run_id),
        status=status_value,
        workspace=str(snapshot.workspace),
        tasks_queued=_director_snapshot_task_count(snapshot),
        message=f"Status: {status_value}",
    )


def _path_is_within(target: Path, root: str | Path | None) -> bool:
    root_text = str(root or "").strip()
    if not root_text:
        return False
    try:
        resolved_root = Path(root_text).resolve()
        resolved_target = target.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved_target == resolved_root or resolved_root in resolved_target.parents


def _blueprint_reference_values(details: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(details.get("blueprint_id") or "").strip(),
        str(details.get("blueprint_path") or "").strip(),
        str(details.get("runtime_blueprint_path") or "").strip(),
    )


def _task_identity_tokens(task_id: str, details: dict[str, Any]) -> set[str]:
    tokens = {
        str(task_id or "").strip(),
        str(details.get("pm_task_id") or "").strip(),
    }
    return {token for token in tokens if token}


def _payload_task_identity_values(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("task_id", "pm_task_id", "id", "source_task_id", "external_task_id"):
        token = str(payload.get(key) or "").strip()
        if token:
            values.add(token)

    context = _as_dict(payload.get("context"))
    for key in ("task_id", "pm_task_id", "id", "source_task_id", "external_task_id"):
        token = str(context.get(key) or "").strip()
        if token:
            values.add(token)

    for nested_key in ("base_schema", "preflight_result"):
        nested_payload = payload.get(nested_key)
        if isinstance(nested_payload, dict):
            values.update(_payload_task_identity_values(nested_payload))

    task_update_map = payload.get("task_update_map")
    if isinstance(task_update_map, dict):
        for key, item in task_update_map.items():
            token = str(key or "").strip()
            if token:
                values.add(token)
            if isinstance(item, dict):
                values.update(_payload_task_identity_values(item))

    task_updates = payload.get("task_updates")
    if isinstance(task_updates, list):
        for item in task_updates:
            if isinstance(item, dict):
                values.update(_payload_task_identity_values(item))

    return values


def _blueprint_payload_matches_task(payload: dict[str, Any], identities: set[str]) -> bool:
    if not identities:
        return False
    if _blueprint_payload_is_traceability_only(payload):
        return False
    if bool(payload.get("hard_failure")):
        return False
    status_token = str(payload.get("status") or "").strip().lower()
    if status_token in {"failed", "failure", "error", "rejected", "blocked"}:
        return False
    if not _blueprint_payload_is_handoff_ready(payload):
        return False
    return bool(_payload_task_identity_values(payload) & identities)


def _blueprint_contract_list(payload: dict[str, Any], *keys: str) -> list[str]:
    base_schema = _as_dict(payload.get("base_schema"))
    context = _as_dict(payload.get("context")) or _as_dict(base_schema.get("context"))
    pm_task = _as_dict(payload.get("pm_task")) or _as_dict(base_schema.get("pm_task")) or _as_dict(context.get("task"))
    qa_contract = _as_dict(pm_task.get("qa_contract"))
    for key in keys:
        for source in (payload, base_schema, context, pm_task, qa_contract):
            rows = _string_list(source.get(key))
            if rows:
                return rows
    return []


def _blueprint_handoff_missing_fields(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not _blueprint_contract_list(payload, "target_files", "scope_paths", "files", "affected_files"):
        missing.append("target_files")
    if not _blueprint_contract_list(payload, "acceptance_criteria", "acceptance"):
        missing.append("acceptance_criteria")
    if not _blueprint_contract_list(payload, "execution_checklist", "steps"):
        missing.append("execution_checklist")
    return missing


def _blueprint_payload_is_handoff_ready(payload: dict[str, Any]) -> bool:
    if _blueprint_payload_is_traceability_only(payload):
        return False
    completeness = _as_dict(payload.get("contract_completeness"))
    missing_fields = _string_list(completeness.get("missing_fields"))
    if completeness.get("handoff_ready") is False or missing_fields:
        return False
    return not _blueprint_handoff_missing_fields(payload)


def _blueprint_payload_is_traceability_only(payload: dict[str, Any]) -> bool:
    if payload.get("traceability_only") is True:
        return True
    source = str(payload.get("source") or "").strip().lower()
    return source.startswith("pm_dispatch.traceability")


def _load_blueprint_payload_by_id(workspace: str, blueprint_id: str) -> dict[str, Any] | None:
    if not blueprint_id:
        return None
    try:
        payload = BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("Director blueprint persistence probe failed for blueprint_id=%s", blueprint_id, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _load_all_blueprint_payloads(workspace: str) -> list[dict[str, Any]]:
    if not str(workspace or "").strip():
        return []
    try:
        persistence = BlueprintPersistence(workspace, ensure_directory=False)
        blueprint_ids = persistence.list_all()
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("Director blueprint persistence scan failed for workspace=%s", workspace, exc_info=True)
        return []

    payloads: list[dict[str, Any]] = []
    for blueprint_id in blueprint_ids:
        try:
            payload = persistence.load(str(blueprint_id or "").strip())
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.debug("Director blueprint payload scan failed for blueprint_id=%s", blueprint_id, exc_info=True)
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _resolve_blueprint_path(workspace: str, cache_root: str, value: str) -> Path | None:
    token = str(value or "").strip()
    if not token:
        return None

    try:
        raw_path = Path(token)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            normalized = token.replace("\\", "/")
            if normalized == "runtime" or normalized.startswith("runtime/"):
                resolved = Path(resolve_artifact_path(workspace, cache_root, normalized)).resolve()
            else:
                resolved = (Path(workspace) / token).resolve()
    except (OSError, RuntimeError, ValueError):
        return None

    if _path_is_within(resolved, workspace) or _path_is_within(resolved, cache_root):
        return resolved
    return None


def _load_blueprint_payload_by_path(workspace: str, cache_root: str, path_value: str) -> dict[str, Any] | None:
    path = _resolve_blueprint_path(workspace, cache_root, path_value)
    if path is None or not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        logger.debug("Director blueprint path probe failed for path=%s", path, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _blueprint_artifact_state(
    *,
    workspace: str,
    cache_root: str,
    task_id: str,
    details: dict[str, Any],
    blueprint_payloads: list[dict[str, Any]] | None = None,
) -> Literal["valid", "missing", "invalid"]:
    blueprint_id, blueprint_path, runtime_blueprint_path = _blueprint_reference_values(details)
    identities = _task_identity_tokens(task_id, details)
    if not any((blueprint_id, blueprint_path, runtime_blueprint_path)):
        available_payloads = (
            blueprint_payloads if blueprint_payloads is not None else _load_all_blueprint_payloads(workspace)
        )
        if any(_blueprint_payload_matches_task(payload, identities) for payload in available_payloads):
            return "valid"
        return "missing"

    payloads: list[dict[str, Any]] = []
    id_payload = _load_blueprint_payload_by_id(workspace, blueprint_id)
    if id_payload is not None:
        payloads.append(id_payload)

    for path_value in (runtime_blueprint_path, blueprint_path):
        path_payload = _load_blueprint_payload_by_path(workspace, cache_root, path_value)
        if path_payload is not None:
            payloads.append(path_payload)

    if any(_blueprint_payload_matches_task(payload, identities) for payload in payloads):
        return "valid"
    return "invalid"


def _task_diagnostics_from_rows(
    rows: list[dict[str, Any]],
    source: str,
    *,
    workspace: str = "",
    cache_root: str = "",
) -> DirectorDiagnosticsTaskSection:
    pending = 0
    claimed = 0
    running = 0
    blocked = 0
    failed = 0
    completed = 0
    cancelled = 0
    ready_task_ids: list[str] = []
    blueprint_ready_task_ids: list[str] = []
    missing_blueprint_task_ids: list[str] = []
    invalid_blueprint_task_ids: list[str] = []
    blocked_task_ids: list[str] = []
    running_task_ids: list[str] = []
    blueprint_payloads = _load_all_blueprint_payloads(workspace) if source == "workflow" else []
    completed_identity_tokens: set[str] = set()
    blocking_identity_tokens: set[str] = set()
    row_details: list[tuple[str, dict[str, Any]]] = []

    for row in rows:
        task_id = _task_id_from_row(row)
        details = _task_details(row)
        row_details.append((task_id, details))
        status_token = str(details["status"])
        if status_token == "COMPLETED":
            completed_identity_tokens.update(_task_identity_tokens(task_id, details))
        elif status_token in {"FAILED", "BLOCKED", "CANCELLED"}:
            blocking_identity_tokens.update(_task_identity_tokens(task_id, details))

    changed = True
    while changed:
        changed = False
        for task_id, details in row_details:
            status_token = str(details["status"])
            if status_token != "PENDING":
                continue
            dependencies = details["dependencies"]
            if any(str(dependency or "").strip() in blocking_identity_tokens for dependency in dependencies):
                identities = _task_identity_tokens(task_id, details)
                new_tokens = identities.difference(blocking_identity_tokens)
                if new_tokens:
                    blocking_identity_tokens.update(new_tokens)
                    changed = True

    for row in rows:
        task_id = _task_id_from_row(row)
        details = _task_details(row)
        status_token = str(details["status"])
        dependencies = details["dependencies"]
        requires_blueprint_evidence = _row_requires_blueprint_evidence(row, source=source)
        blueprint_state = (
            _blueprint_artifact_state(
                workspace=workspace,
                cache_root=cache_root,
                task_id=task_id,
                details=details,
                blueprint_payloads=blueprint_payloads,
            )
            if requires_blueprint_evidence and task_id
            else "valid"
        )

        if status_token == "PENDING":
            unmet_dependencies = [
                str(dependency or "").strip()
                for dependency in dependencies
                if str(dependency or "").strip() and str(dependency or "").strip() not in completed_identity_tokens
            ]
            blocking_dependencies = [
                dependency for dependency in unmet_dependencies if dependency in blocking_identity_tokens
            ]
            if blocking_dependencies:
                blocked += 1
                if task_id:
                    blocked_task_ids.append(task_id)
                continue
            pending += 1
            if not unmet_dependencies and task_id:
                if blueprint_state == "missing":
                    missing_blueprint_task_ids.append(task_id)
                elif blueprint_state == "invalid":
                    invalid_blueprint_task_ids.append(task_id)
                else:
                    ready_task_ids.append(task_id)
                    if requires_blueprint_evidence:
                        blueprint_ready_task_ids.append(task_id)
        elif status_token == "CLAIMED":
            claimed += 1
            running += 1
            if task_id:
                running_task_ids.append(task_id)
        elif status_token == "RUNNING":
            running += 1
            if task_id:
                running_task_ids.append(task_id)
        elif status_token == "BLOCKED":
            blocked += 1
            if task_id:
                blocked_task_ids.append(task_id)
        elif status_token == "FAILED":
            failed += 1
        elif status_token == "COMPLETED":
            completed += 1
        elif status_token == "CANCELLED":
            cancelled += 1
        else:
            pending += 1

    total = len(rows)
    return DirectorDiagnosticsTaskSection(
        ok=total > 0 and bool(ready_task_ids or running_task_ids) and blocked == 0 and failed == 0,
        source=source,
        total=total,
        pending=pending,
        claimed=claimed,
        running=running,
        blocked=blocked,
        failed=failed,
        completed=completed,
        cancelled=cancelled,
        ready_to_execute=len(ready_task_ids),
        ready_task_ids=ready_task_ids,
        blueprint_ready_task_ids=blueprint_ready_task_ids,
        missing_blueprint_task_ids=missing_blueprint_task_ids,
        invalid_blueprint_task_ids=invalid_blueprint_task_ids,
        blocked_task_ids=blocked_task_ids,
        running_task_ids=running_task_ids,
    )


def _worker_payload_from_object(worker: Any) -> dict[str, Any]:
    if isinstance(worker, dict):
        return worker
    to_dict = getattr(worker, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    return {
        "id": getattr(worker, "id", ""),
        "name": getattr(worker, "name", ""),
        "status": getattr(worker, "status", ""),
        "current_task_id": getattr(worker, "current_task_id", None) or getattr(worker, "task_id", None),
        "healthy": getattr(worker, "healthy", None),
    }


def _worker_id_from_row(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("worker_id") or row.get("name") or "").strip()


def _worker_row_matches_id(row: dict[str, Any], worker_id: str) -> bool:
    requested = str(worker_id or "").strip()
    if not requested:
        return False
    return requested in {
        str(row.get("id") or "").strip(),
        str(row.get("worker_id") or "").strip(),
        str(row.get("name") or "").strip(),
    }


def _worker_rows_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    worker_payload: Any = payload.get("workers")
    if worker_payload is None:
        status_payload = payload.get("status")
        if isinstance(status_payload, dict):
            worker_payload = status_payload.get("workers")

    candidates: Any = None
    if isinstance(worker_payload, list):
        candidates = worker_payload
    elif isinstance(worker_payload, dict):
        for key in ("worker_rows", "rows", "items", "workers"):
            value = worker_payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if candidates is None:
            dict_rows: list[dict[str, Any]] = []
            for key, value in worker_payload.items():
                if not isinstance(value, dict):
                    continue
                row = dict(value)
                row.setdefault("id", str(key))
                dict_rows.append(row)
            candidates = dict_rows
    elif isinstance(payload.get("worker_rows"), list):
        candidates = payload.get("worker_rows")

    rows: list[dict[str, Any]] = []
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if not _worker_id_from_row(row):
            continue
        rows.append(row)
    return rows


def _worker_rows_from_projection(projection: Any) -> list[dict[str, Any]]:
    merged_payload = getattr(projection, "director_merged", None)
    rows = _worker_rows_from_payload(merged_payload if isinstance(merged_payload, dict) else None)
    if rows:
        return rows
    local_payload = getattr(projection, "director_local", None)
    return _worker_rows_from_payload(local_payload if isinstance(local_payload, dict) else None)


async def _projected_worker_rows(request: Request, workspace: str | None = None) -> list[dict[str, Any]]:
    state = getattr(request.app.state, "app_state", None) or request.app.state
    resolved_workspace = _workspace_from_request(request, workspace)
    try:
        projection = await RuntimeProjectionService.build_async(resolved_workspace, state=state)
    except (RuntimeError, ValueError):
        logger.debug("Director worker projection unavailable for workspace=%s", resolved_workspace, exc_info=True)
        return []
    return _worker_rows_from_projection(projection)


def _worker_diagnostics_from_workers(workers: list[Any]) -> DirectorDiagnosticsWorkerSection:
    idle = 0
    busy = 0
    unhealthy = 0
    active_task_ids: list[str] = []
    idle_statuses = {"idle", "ready", "available", "waiting"}
    busy_statuses = {"busy", "running", "active", "claimed", "working", "executing"}
    unhealthy_statuses = {"failed", "error", "unhealthy", "offline"}

    for worker in workers:
        payload = _worker_payload_from_object(worker)
        status_token = str(payload.get("status") or "").strip().lower()
        if status_token in idle_statuses:
            idle += 1
        elif status_token in busy_statuses:
            busy += 1

        if payload.get("healthy") is False or status_token in unhealthy_statuses:
            unhealthy += 1

        task_id = str(
            payload.get("current_task_id") or payload.get("currentTaskId") or payload.get("task_id") or ""
        ).strip()
        if task_id and task_id not in active_task_ids:
            active_task_ids.append(task_id)

    total = len(workers)
    return DirectorDiagnosticsWorkerSection(
        ok=total > 0 and unhealthy == 0,
        total=total,
        idle=idle,
        busy=busy,
        healthy=max(0, total - unhealthy),
        unhealthy=unhealthy,
        active_task_ids=active_task_ids,
    )


def _role_payload(payload: dict[str, Any], role: str) -> dict[str, Any]:
    roles_value = payload.get("roles")
    roles = roles_value if isinstance(roles_value, dict) else {}
    target = str(role or "").strip().lower()
    for key, value in roles.items():
        if str(key or "").strip().lower() == target and isinstance(value, dict):
            return value
    return {}


def _build_llm_diagnostics(settings: Any) -> DirectorDiagnosticsLLMSection:
    try:
        payload = build_llm_status(settings)
    except (RuntimeError, OSError, ValueError) as exc:
        return DirectorDiagnosticsLLMSection(
            ok=False,
            state="error",
            blocked_roles=["director"],
            error=str(exc),
        )

    if not isinstance(payload, dict):
        return DirectorDiagnosticsLLMSection(
            ok=False,
            state="error",
            blocked_roles=["director"],
            error="invalid_llm_status_payload",
        )

    role_info = _role_payload(payload, "director")
    ready = bool(role_info.get("ready"))
    runtime_supported = bool(role_info.get("runtime_supported"))
    blocked_roles = _string_list(payload.get("blocked_roles"))
    unsupported_roles = _string_list(payload.get("unsupported_roles"))
    required_ready_roles = _string_list(payload.get("required_ready_roles"))
    if "director" not in required_ready_roles:
        required_ready_roles.append("director")
    if not ready and "director" not in blocked_roles:
        blocked_roles.append("director")
    if not runtime_supported and "director" not in unsupported_roles:
        unsupported_roles.append("director")

    ok = ready and runtime_supported
    return DirectorDiagnosticsLLMSection(
        ok=ok,
        state="ready" if ok else "blocked",
        blocked_roles=blocked_roles,
        unsupported_roles=unsupported_roles,
        required_ready_roles=required_ready_roles,
        provider_id=str(role_info.get("provider_id") or "").strip() or None,
        model=str(role_info.get("model") or "").strip() or None,
        details=payload,
    )


def _director_diagnostic_issues(
    status_section: DirectorDiagnosticsStatusSection,
    task_section: DirectorDiagnosticsTaskSection,
    worker_section: DirectorDiagnosticsWorkerSection,
    llm_section: DirectorDiagnosticsLLMSection,
) -> list[str]:
    issues: list[str] = []
    if not llm_section.ok:
        issues.append("director_llm_not_ready")
    if not status_section.ok:
        issues.append("director_status_unavailable")
    if task_section.error:
        issues.append("director_tasks_unavailable")
    if task_section.total == 0:
        issues.append("director_no_tasks")
    else:
        if task_section.missing_blueprint_task_ids:
            issues.append("director_ready_tasks_missing_blueprints")
        if task_section.invalid_blueprint_task_ids:
            issues.append("director_ready_tasks_invalid_blueprints")
    if task_section.blocked > 0:
        issues.append("director_tasks_blocked")
    if task_section.failed > 0:
        issues.append("director_tasks_failed")
    if (
        task_section.total > 0
        and not task_section.missing_blueprint_task_ids
        and not task_section.invalid_blueprint_task_ids
        and task_section.blocked == 0
        and task_section.failed == 0
        and task_section.ready_to_execute == 0
        and task_section.running == 0
        and not status_section.running
    ):
        issues.append("director_no_ready_tasks")
    if worker_section.error:
        issues.append("director_workers_unavailable")
    if worker_section.total == 0:
        issues.append("director_no_workers")
    elif task_section.ready_to_execute > 0 and worker_section.idle == 0 and not status_section.running:
        issues.append("director_no_idle_workers")
    if worker_section.unhealthy > 0:
        issues.append("director_workers_unhealthy")
    return issues


def _director_execution_blockers(
    status_section: DirectorDiagnosticsStatusSection,
    task_section: DirectorDiagnosticsTaskSection,
    worker_section: DirectorDiagnosticsWorkerSection,
    llm_section: DirectorDiagnosticsLLMSection,
) -> list[str]:
    """Return hard blockers that should disable a new Director run."""

    blockers: list[str] = []
    if not llm_section.ok:
        blockers.append("director_llm_not_ready")

    if status_section.running:
        return blockers

    if not status_section.ok:
        blockers.append("director_status_unavailable")
    if task_section.error:
        blockers.append("director_tasks_unavailable")
    if task_section.total == 0:
        blockers.append("director_no_tasks")
    else:
        if task_section.missing_blueprint_task_ids:
            blockers.append("director_ready_tasks_missing_blueprints")
        if task_section.invalid_blueprint_task_ids:
            blockers.append("director_ready_tasks_invalid_blueprints")
    has_runnable_or_running_work = (
        task_section.ready_to_execute > 0 or task_section.running > 0 or status_section.running
    )
    if not has_runnable_or_running_work and task_section.blocked > 0:
        blockers.append("director_tasks_blocked")
    if not has_runnable_or_running_work and task_section.failed > 0:
        blockers.append("director_tasks_failed")
    if (
        task_section.total > 0
        and not task_section.missing_blueprint_task_ids
        and not task_section.invalid_blueprint_task_ids
        and task_section.blocked == 0
        and task_section.failed == 0
        and task_section.ready_to_execute == 0
    ):
        blockers.append("director_no_ready_tasks")

    if worker_section.error:
        blockers.append("director_workers_unavailable")
    elif worker_section.total > 0 and task_section.ready_to_execute > 0 and worker_section.idle == 0:
        blockers.append("director_no_idle_workers")
    return blockers


async def _build_director_diagnostics(
    request: Request,
    service: DirectorService,
    workspace_override: str | None = None,
) -> DirectorDiagnosticsResponse:
    """Build a side-effect-free Director readiness snapshot."""

    state = getattr(request.app.state, "app_state", None) or request.app.state
    settings = settings_with_workspace_override(state.settings, workspace_override or "")
    workspace = active_workspace_value(settings)
    ramdisk_root = str(getattr(settings, "ramdisk_root", "") or resolve_env_str("ramdisk_root") or "").strip()
    cache_root = build_cache_root(ramdisk_root, workspace)

    status_section = DirectorDiagnosticsStatusSection(
        ok=False,
        state="UNKNOWN",
        running=False,
        error="projection unavailable",
    )
    task_rows: list[dict[str, Any]] = []
    projected_workers: list[dict[str, Any]] = []
    task_source = "empty"

    try:
        projection = await RuntimeProjectionService.build_async(workspace, state=state)
        selected_status = (
            getattr(projection, "director_merged", None)
            if getattr(projection, "director_merged", None)
            else projection.director_local
        )
        projection_source = "director_merged" if getattr(projection, "director_merged", None) else "director_local"
        local_status = _flatten_director_status(selected_status or {"running": False, "status": {"state": "IDLE"}})
        status_section = DirectorDiagnosticsStatusSection(
            ok=True,
            state=str(local_status.get("state") or "IDLE"),
            running=bool(local_status.get("running")),
            source=str(local_status.get("source") or "none"),
            projection_source=projection_source,
        )
        task_rows = _projection_task_rows(projection)
        if task_rows:
            task_rows = _runtime_backed_task_rows(task_rows, workspace=workspace)
            task_rows = _contract_backed_task_rows(task_rows, workspace=workspace, cache_root=cache_root)
        task_market_rows = _task_market_execution_rows_for_workspace(workspace)
        if task_market_rows:
            task_rows = _merge_task_rows_by_identity(task_rows, task_market_rows)
        projected_workers = _worker_rows_from_projection(projection)
        task_source = "workflow" if task_rows else "empty"
    except (RuntimeError, ValueError) as exc:
        logger.debug("Director diagnostics projection unavailable for workspace=%s", workspace, exc_info=True)
        status_section.error = str(exc)

    if not task_rows:
        task_market_rows = _task_market_execution_rows_for_workspace(workspace)
        if task_market_rows:
            task_rows = task_market_rows
            task_source = "workflow"
            task_section = _task_diagnostics_from_rows(
                task_rows, task_source, workspace=workspace, cache_root=cache_root
            )
        else:
            try:
                task_rows = _task_rows_from_local_tasks(await service.list_tasks(status=None))
                task_source = "local" if task_rows else "empty"
            except (RuntimeError, ValueError, AttributeError) as exc:
                logger.debug("Director diagnostics local task queue unavailable", exc_info=True)
                task_section = DirectorDiagnosticsTaskSection(
                    ok=False,
                    source="error",
                    error=str(exc),
                )
            else:
                task_rows = _runtime_backed_task_rows(task_rows, workspace=workspace)
                task_section = _task_diagnostics_from_rows(task_rows, task_source, workspace=workspace, cache_root="")
    else:
        task_section = _task_diagnostics_from_rows(
            task_rows,
            task_source,
            workspace=workspace,
            cache_root=cache_root,
        )

    try:
        workers = await service.list_workers()
    except (RuntimeError, ValueError, AttributeError) as exc:
        logger.debug("Director diagnostics worker pool unavailable", exc_info=True)
        worker_section = (
            _worker_diagnostics_from_workers(projected_workers)
            if projected_workers
            else DirectorDiagnosticsWorkerSection(ok=False, error=str(exc))
        )
    else:
        worker_rows = list(workers)
        worker_section = _worker_diagnostics_from_workers(worker_rows if worker_rows else projected_workers)

    llm_section = _build_llm_diagnostics(settings)
    issues = _director_diagnostic_issues(status_section, task_section, worker_section, llm_section)
    execution_blockers = _director_execution_blockers(status_section, task_section, worker_section, llm_section)
    return DirectorDiagnosticsResponse(
        ok=not issues,
        can_execute=not execution_blockers,
        generated_at=_utc_now(),
        workspace=workspace,
        status=status_section,
        tasks=task_section,
        workers=worker_section,
        llm=llm_section,
        issues=issues,
        execution_blockers=execution_blockers,
    )


async def _build_director_diagnostics_for_request(
    request: Request,
    workspace: str,
) -> DirectorDiagnosticsResponse:
    """Resolve DirectorService and build diagnostics for guarded run starts."""

    service = await get_director_service_dep(request)
    return await _build_director_diagnostics(request, service, workspace_override=workspace)


def _ensure_director_can_execute(diagnostics: DirectorDiagnosticsResponse) -> None:
    """Raise a structured 409 when Director execution prerequisites are not met."""

    if diagnostics.can_execute and not diagnostics.execution_blockers:
        return

    raise StructuredHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        code="DIRECTOR_EXECUTION_BLOCKED",
        message="Director execution prerequisites are not satisfied",
        details={
            "execution_blockers": diagnostics.execution_blockers,
            "issues": diagnostics.issues,
            "diagnostics": diagnostics.model_dump(mode="json"),
        },
    )


def _ensure_director_lifecycle_can_start(request: Request, workspace: str = "") -> None:
    """Ensure the Director role LLM runtime is ready before service startup."""

    state = getattr(request.app.state, "app_state", None) or request.app.state
    gate_state: Any = state
    if str(workspace or "").strip():
        gate_state = SimpleNamespace(settings=settings_with_workspace_override(state.settings, workspace))
    ensure_required_roles_ready(gate_state, default_roles=["director"], force_first="director")


def _parse_task_priority(value: str) -> TaskPriority:
    token = str(value or "").strip()
    if not token:
        return TaskPriority.MEDIUM

    name_token = token.upper().replace("-", "_")
    by_name = TaskPriority.__members__.get(name_token)
    if by_name is not None:
        return by_name

    value_token = token.lower()
    for priority in TaskPriority:
        if priority.value == value_token:
            return priority

    allowed = sorted({item.name for item in TaskPriority} | {item.value for item in TaskPriority})
    raise StructuredHTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="INVALID_TASK_PRIORITY",
        message="invalid task priority",
        details={"priority": token, "allowed": allowed},
    )


@router.post("/start", dependencies=[Depends(require_auth)])
async def start_director(
    request: Request,
    workspace: str = "",
) -> dict[str, Any]:
    """Start the Director service."""
    _ensure_director_lifecycle_can_start(request, workspace)
    service = await get_director_service_dep(request)
    await service.start()
    return {"ok": True, "state": service.state.name, "workspace": _workspace_from_request(request, workspace)}


@router.post("/stop", dependencies=[Depends(require_auth)])
async def stop_director(
    request: Request,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Stop the Director service."""
    await service.stop()
    return {"ok": True, "state": service.state.name, "workspace": _workspace_from_request(request, workspace)}


@router.get("/status", dependencies=[Depends(require_auth)])
async def get_status(
    request: Request,
    source: Literal["local", "auto"] = "local",
    workspace: str = "",
) -> dict[str, Any]:
    """Get director status.

    By default this endpoint preserves the legacy local-only contract. Callers
    that need PM-workflow-aware status can request ``source=auto``.
    """
    # Get service from app state
    state = getattr(request.app.state, "app_state", None) or request.app.state

    # Use RuntimeProjectionService for consistent state
    resolved_workspace = requested_or_active_workspace(state.settings, workspace)
    projection = await RuntimeProjectionService.build_async(resolved_workspace, state=state)

    selected_status = (
        getattr(projection, "director_merged", None)
        if source == "auto" and getattr(projection, "director_merged", None)
        else projection.director_local
    )
    local_status = _flatten_director_status(selected_status or {"running": False, "status": {"state": "IDLE"}})
    return {
        "ok": True,
        **local_status,
        "projection_source": "director_merged"
        if source == "auto" and getattr(projection, "director_merged", None)
        else "director_local",
    }


@router.get("/capabilities", dependencies=[Depends(require_auth)], response_model=RoleCapabilitiesResponse)
def get_director_capabilities() -> dict[str, Any]:
    """Return the Director capability matrix for desktop and workflow hosts."""
    try:
        from polaris.domain.entities.capability import get_role_capabilities

        return {
            "ok": True,
            "role": "director",
            "capabilities": get_role_capabilities("director"),
        }
    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=500,
            code="CAPABILITY_LOAD_FAILED",
            message=str(exc),
            details={"capabilities": {}},
        ) from exc


@router.get("/diagnostics", dependencies=[Depends(require_auth)], response_model=DirectorDiagnosticsResponse)
async def get_director_diagnostics(
    request: Request,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> DirectorDiagnosticsResponse:
    """Return side-effect-free Director desktop readiness diagnostics."""
    return await _build_director_diagnostics(request, service, workspace_override=workspace)


@router.post("/tasks", response_model=TaskResponse, dependencies=[Depends(require_auth)])
async def create_task(
    http_request: Request,
    request: TaskCreateRequest,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> TaskResponse:
    """Create and submit a new task."""
    priority = _parse_task_priority(request.priority)
    blocked_by: list[int | str] = list(request.blocked_by)
    resolved_workspace = _workspace_from_request(http_request, workspace)
    metadata = dict(request.metadata)
    metadata.setdefault("workspace", resolved_workspace)
    metadata.setdefault("director_workspace", resolved_workspace)

    task = await service.submit_task(
        subject=request.subject,
        description=request.description,
        command=request.command,
        priority=priority,
        blocked_by=blocked_by,
        timeout_seconds=request.timeout_seconds,
        metadata=metadata,
    )

    row = _task_row_from_object(task)
    task_metadata = _as_dict(row.get("metadata"))
    task_metadata.setdefault("workspace", resolved_workspace)
    task_metadata.setdefault("director_workspace", resolved_workspace)
    row["metadata"] = task_metadata
    return _task_response_from_row(row)


@router.get("/tasks", dependencies=[Depends(require_auth)])
async def list_tasks(
    request: Request,
    status: str | None = None,
    source: Literal["auto", "local", "workflow"] = "auto",
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> list[TaskResponse]:
    """List all tasks.

    Task selection follows "二选一" rule:
    - workflow: use workflow tasks if available
    - local: use local service tasks
    - auto: prefer workflow, fallback to local live tasks
    """
    requested_status = _normalize_task_status_token(status) if status else None
    start = time.perf_counter()
    tasks: list[dict[str, Any]] = []
    used_projection = False
    used_local_fallback = False

    try:
        if source == "local":
            tasks = _task_rows_from_local_tasks(await service.list_tasks(status=None))
        else:
            # Use RuntimeProjectionService for workflow/auto selection only.
            # Keep local-only path fast for high-frequency observers and stress tracer.
            state = getattr(request.app.state, "app_state", None) or request.app.state
            resolved_workspace = requested_or_active_workspace(state.settings, workspace)
            projection = await RuntimeProjectionService.build_async(resolved_workspace, state=state)
            used_projection = True

            tasks = _projection_task_rows(projection)
            if tasks:
                ramdisk_root = str(
                    getattr(state.settings, "ramdisk_root", "") or resolve_env_str("ramdisk_root") or ""
                ).strip()
                cache_root = build_cache_root(ramdisk_root, resolved_workspace)
                tasks = _runtime_backed_task_rows(tasks, workspace=resolved_workspace)
                tasks = _contract_backed_task_rows(tasks, workspace=resolved_workspace, cache_root=cache_root)
            task_market_rows = _task_market_execution_rows_for_workspace(resolved_workspace)
            if task_market_rows:
                tasks = _merge_task_rows_by_identity(tasks, task_market_rows)
            if source == "auto" and not tasks:
                tasks = _task_rows_from_local_tasks(await service.list_tasks(status=None))
                used_local_fallback = True

        responses = [_task_response_from_row(t) for t in tasks]
        if requested_status is not None:
            responses = [item for item in responses if item.status == requested_status]

        return responses
    finally:
        _append_debug(
            "api.director.list_tasks",
            {
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "source": source,
                "status_filter": requested_status or "",
                "task_count": len(tasks),
                "used_projection": used_projection,
                "used_local_fallback": used_local_fallback,
            },
        )


@router.get("/tasks/{task_id}", dependencies=[Depends(require_auth)])
async def get_task(
    request: Request,
    task_id: str,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> TaskResponse:
    """Get task by ID."""
    task = await service.get_task(task_id)
    if task:
        return _task_response_from_row(_task_row_from_object(task))

    projected = await _projected_task_response(request, task_id, workspace)
    if projected is not None:
        return projected

    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_task(
    request: Request,
    task_id: str,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Cancel a task."""
    result = await service.cancel_task(task_id)
    payload = _cancel_success_payload(task_id, result)

    if payload is None:
        raise HTTPException(status_code=400, detail=_cancel_failure_detail(result))

    payload.setdefault("workspace", _workspace_from_request(request, workspace))
    return payload


@router.get("/workers", dependencies=[Depends(require_auth)])
async def list_workers(
    request: Request,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> list[dict[str, Any]]:
    """List all workers."""
    workers = await service.list_workers()
    local_rows = [_worker_payload_from_object(worker) for worker in workers]
    if local_rows:
        return local_rows
    return await _projected_worker_rows(request, workspace)


@router.get("/workers/{worker_id}", dependencies=[Depends(require_auth)])
async def get_worker(
    request: Request,
    worker_id: str,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Get worker by ID."""
    worker = await service.get_worker(worker_id)

    if worker:
        return _worker_payload_from_object(worker)

    for row in await _projected_worker_rows(request, workspace):
        if _worker_row_matches_id(row, worker_id):
            return row

    raise HTTPException(status_code=404, detail="Worker not found")


# ============================================================================
# LLM Events API - 实时 LLM 调用状态
# ============================================================================


@router.get("/tasks/{task_id}/llm-events", dependencies=[Depends(require_auth)])
async def get_task_llm_events(
    request: Request,
    task_id: str,
    run_id: str | None = None,
    limit: int = 100,
    workspace: str = "",
) -> dict[str, Any]:
    """获取任务的 LLM 调用事件历史"""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, limit=limit)
    resolved_workspace = _workspace_from_request(request, workspace)
    if workspace.strip():
        events = filter_llm_events_by_workspace(events, resolved_workspace)

    # 分类统计
    stats = {
        "total": len(events),
        "call_start": sum(1 for e in events if e.event_type == "llm_call_start"),
        "call_end": sum(1 for e in events if e.event_type == "llm_call_end"),
        "call_error": sum(1 for e in events if e.event_type == "llm_error"),
        "call_retry": sum(1 for e in events if e.event_type == "llm_retry"),
        "validation_pass": sum(1 for e in events if e.event_type == "validation_pass"),
        "validation_fail": sum(1 for e in events if e.event_type == "validation_fail"),
        "tool_execute": sum(1 for e in events if e.event_type == "tool_execute"),
    }

    return {
        "task_id": task_id,
        "run_id": run_id,
        "workspace": resolved_workspace,
        "events": [e.to_dict() for e in events],
        "stats": stats,
    }


@router.get("/llm-events", dependencies=[Depends(require_auth)])
async def get_llm_events(
    request: Request,
    run_id: str | None = None,
    task_id: str | None = None,
    role: str | None = None,
    limit: int = 100,
    workspace: str = "",
) -> dict[str, Any]:
    """获取全局 LLM 调用事件历史（按角色/任务过滤）"""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role=role, limit=limit)
    resolved_workspace = _workspace_from_request(request, workspace)
    if workspace.strip():
        events = filter_llm_events_by_workspace(events, resolved_workspace)

    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "workspace": resolved_workspace,
    }


@router.get("/cache-stats", dependencies=[Depends(require_auth)])
async def get_cache_stats() -> dict[str, Any]:
    """获取 LLM 缓存统计信息"""
    from polaris.cells.roles.kernel.public.service import get_global_llm_cache

    cache = get_global_llm_cache()
    return cache.get_stats()


@router.post("/cache-clear", dependencies=[Depends(require_auth)])
async def clear_cache() -> dict[str, Any]:
    """清空 LLM 缓存"""
    from polaris.cells.roles.kernel.public.service import get_global_llm_cache

    cache = get_global_llm_cache()
    cache.clear()
    return {"ok": True, "message": "Cache cleared"}


@router.post(
    "/integration-qa",
    response_model=DirectorIntegrationQaResponse,
    dependencies=[Depends(require_auth)],
)
async def director_run_integration_qa(
    request: Request,
    payload: DirectorIntegrationQaRequest,
) -> DirectorIntegrationQaResponse:
    """Run the canonical post-dispatch integration QA after Director reaches terminal state."""
    try:
        from polaris.cells.orchestration.pm_dispatch.public.service import (
            run_post_dispatch_integration_qa,
        )
        from polaris.cells.orchestration.workflow_runtime.public.service import (
            build_integration_qa_tasks_from_director_result,
            persist_director_result_from_runtime,
        )

        settings = request.app.state.app_state.settings
        workspace = requested_or_active_workspace(settings, payload.workspace)
        run_id = str(payload.run_id or "").strip() or f"director-qa-{int(time.time())}"
        director_result = persist_director_result_from_runtime(workspace=workspace, run_id=run_id)
        if director_result is None:
            raise StructuredHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                code="director_result_not_terminal",
                message="Director tasks are not terminal; integration QA cannot run yet.",
                details={"workspace": workspace, "run_id": run_id},
            )

        task_rows = build_integration_qa_tasks_from_director_result(director_result)
        run_dir = resolve_artifact_path(workspace, "", f"runtime/runs/{run_id}")
        os.makedirs(run_dir, exist_ok=True)
        run_events = resolve_artifact_path(workspace, "", f"runtime/runs/{run_id}/events/runtime.events.jsonl")
        dialogue_full = resolve_artifact_path(workspace, "", "runtime/events/dialogue.transcript.jsonl")
        qa_result = await asyncio.to_thread(
            run_post_dispatch_integration_qa,
            args=SimpleNamespace(integration_qa=True),
            workspace_full=workspace,
            cache_root_full="",
            run_dir=run_dir,
            run_id=run_id,
            iteration=int(payload.iteration or 0),
            tasks=task_rows,
            run_events=run_events,
            dialogue_full=dialogue_full,
        )
        return DirectorIntegrationQaResponse(
            ok=bool(qa_result.get("passed") is True),
            workspace=workspace,
            run_id=run_id,
            director_result=director_result,
            result=qa_result,
        )
    except StructuredHTTPException:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error("Failed to run Director integration QA: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from exc


@router.get("/token-budget-stats", dependencies=[Depends(require_auth)])
async def get_token_budget_stats() -> dict[str, Any]:
    """获取 Token 预算统计信息"""
    budget = get_global_token_budget()
    return budget.get_stats()


# ============================================================================
# Phase 6: 统一编排兼容端点
# ============================================================================


@router.post(
    "/run",
    response_model=DirectorOrchestrationResponse,
    dependencies=[Depends(require_auth)],
)
async def director_run_orchestration(
    request: Request,
    payload: DirectorRunOrchestrationRequest,
) -> DirectorOrchestrationResponse:
    """Execute Director run - unified entry point

    Phase 4: Uses OrchestrationCommandService as the single write path.
    All Director execution goes through this endpoint for consistency.

    Example:
        POST /v2/director/run
        {
            "workspace": ".",
            "max_workers": 3,
            "execution_mode": "parallel"
        }
    """
    try:
        # Phase 4: Use OrchestrationCommandService as single entry point
        from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

        settings = request.app.state.app_state.settings
        workspace = requested_or_active_workspace(settings, payload.workspace)
        diagnostics = await _build_director_diagnostics_for_request(request, workspace)
        _ensure_director_can_execute(diagnostics)

        service = OrchestrationCommandService(settings)
        explicit_task_ids = [str(payload.task_id).strip()] if str(payload.task_id or "").strip() else []
        if explicit_task_ids or not str(payload.task_filter or "").strip():
            task_ids, task_selection_source = _director_run_task_ids_from_diagnostics(diagnostics, explicit_task_ids)
        else:
            task_ids, task_selection_source = [], "filter_request"
        task_filter = payload.task_filter or (task_ids[0] if len(task_ids) == 1 else None)

        result = await service.execute_director_run(
            workspace=workspace,
            tasks=task_ids,
            options={
                "task_filter": task_filter,
                "task_id": payload.task_id,
                "max_workers": payload.max_workers,
                "execution_mode": payload.execution_mode,
                "metadata": {
                    "task_selection_source": task_selection_source,
                    "selected_task_ids": task_ids,
                },
            },
        )

        # Register adapters for execution
        orch_service = await get_orchestration_service()
        from polaris.cells.roles.adapters.public.service import register_all_adapters

        register_all_adapters(orch_service)

        return DirectorOrchestrationResponse(
            run_id=result.run_id,
            status=result.status,
            workspace=workspace,
            tasks_queued=_director_tasks_queued(result, task_ids),
            message=result.message or f"Director started in {payload.execution_mode} mode",
        )

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error("Failed to start Director orchestration: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.get("/runs/{run_id}", response_model=DirectorOrchestrationResponse, dependencies=[Depends(require_auth)])
async def director_get_orchestration(
    request: Request,
    run_id: str,
    workspace: str = "",
) -> DirectorOrchestrationResponse:
    """查询 Director 编排运行状态"""
    try:
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        _ensure_snapshot_workspace(request, snapshot, run_id, workspace)
        return _director_orchestration_response(snapshot)

    except HTTPException:
        raise
    except (RuntimeError, ValueError, OrchestrationError) as e:
        logger.error("Failed to query Director run: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.post(
    "/runs/{run_id}/cancel",
    response_model=DirectorOrchestrationResponse,
    dependencies=[Depends(require_auth)],
)
async def director_cancel_orchestration(
    request: Request,
    run_id: str,
    workspace: str = "",
) -> DirectorOrchestrationResponse:
    """Cancel a Director orchestration run and return the resulting snapshot."""
    try:
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        _ensure_snapshot_workspace(request, snapshot, run_id, workspace)
        status_token = _director_snapshot_status(snapshot).lower()
        terminal_statuses = {"completed", "failed", "cancelled", "canceled", "blocked", "timeout"}
        if status_token not in terminal_statuses:
            snapshot = await service.cancel_run(run_id)

        return _director_orchestration_response(snapshot)

    except HTTPException:
        raise
    except (RuntimeError, ValueError, OrchestrationError) as e:
        logger.error("Failed to cancel Director run: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e
