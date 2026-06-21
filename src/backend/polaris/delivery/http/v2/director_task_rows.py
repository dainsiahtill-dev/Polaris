"""Task-row assembly + task-market projection helpers for the Director v2 router.

Extracted from ``polaris.delivery.http.v2.director`` during the lossless module
split. These helpers stitch together task rows from the runtime projection, the
PM workflow contract, the task market and the canonical task runtime.

Monkeypatch contract (CRITICAL): tests patch collaborators on the ``director``
module namespace (``director.select_task_rows_from_projection``,
``director.get_task_market_service``, ``director.build_workflow_task_rows``,
``director._runtime_task_rows_for_workspace``, ``director.logger`` ...) and then
drive full routes. Any such collaborator referenced from a helper that lives
here MUST be dereferenced through the ``director`` module object at call time
(``from . import director as _d; _d.X(...)``) so the patch is honored. A lazy
import of ``director`` is used to avoid the import cycle (``director`` imports the
names defined here; these helpers reference the ``director`` module object, but
only at call time after both modules finish importing).
"""

from __future__ import annotations

from typing import Any

from polaris.delivery.http.v2.director_helpers import (
    _as_dict,
    _is_workflow_shell_task,
    _task_details,
    _task_id_from_row,
    _task_identity_tokens,
    _with_task_projection_source,
)


def _projection_task_rows(projection: Any) -> list[dict[str, Any]]:
    from polaris.delivery.http.v2 import director as _d

    projection_source = _d._projection_source_for_task_rows(projection)
    rows = getattr(projection, "task_rows", None)
    if isinstance(rows, list) and rows:
        return [
            _with_task_projection_source(item, fallback_source=projection_source)
            for item in rows
            if isinstance(item, dict)
        ]
    selected = _d.select_task_rows_from_projection(projection)
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
    from polaris.delivery.http.v2 import director as _d

    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []
    try:
        status = _d.get_task_market_service().query_status(
            _d.QueryTaskMarketStatusV1(
                workspace=workspace_token,
                stage="pending_exec",
                include_payload=True,
                limit=5000,
            )
        )
    except (RuntimeError, OSError, TypeError, ValueError):
        _d.logger.debug("Director task-market rows unavailable for workspace=%s", workspace_token, exc_info=True)
        return []
    rows: list[dict[str, Any]] = []
    for item in getattr(status, "items", ()) or ():
        if not isinstance(item, dict):
            continue
        row = _task_market_row_to_director_task_row(item)
        if row is not None:
            rows.append(row)
    return rows


def _contract_backed_task_rows(
    task_rows: list[dict[str, Any]],
    *,
    workspace: str,
    cache_root: str,
) -> list[dict[str, Any]]:
    """Show PM contract rows as waiting for Chief Engineer handoff."""

    from polaris.delivery.http.v2 import director as _d

    contract_rows = _d.build_workflow_task_rows({}, workspace=workspace, cache_root=cache_root)
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
            merged_rows.append(
                _with_task_projection_source(normalized, fallback_source="pm_contract_waiting_chief_engineer")
            )
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
        merged_rows.append(_with_task_projection_source(merged, fallback_source="chief_engineer_handoff_projection"))

    for row in task_rows:
        if id(row) not in matched_runtime_ids:
            merged_rows.append(_with_task_projection_source(row, fallback_source="runtime_projection"))
    return merged_rows


def _runtime_task_rows_for_workspace(workspace: str) -> list[dict[str, Any]]:
    from polaris.delivery.http.v2 import director as _d

    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []
    try:
        runtime_rows = _d.TaskRuntimeService(workspace_token).list_task_rows()
    except (RuntimeError, ValueError, OSError):
        _d.logger.debug("Director diagnostics runtime task overlay failed for workspace=%s", workspace, exc_info=True)
        return []
    return [dict(row) for row in runtime_rows if isinstance(row, dict)]


def _runtime_backed_task_rows(
    task_rows: list[dict[str, Any]],
    *,
    workspace: str,
) -> list[dict[str, Any]]:
    """Overlay canonical task runtime rows so diagnostics honor lease expiry."""

    from polaris.delivery.http.v2 import director as _d

    runtime_rows = _d._runtime_task_rows_for_workspace(workspace)
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


__all__ = [
    "_contract_backed_task_rows",
    "_projection_task_rows",
    "_runtime_backed_task_rows",
    "_runtime_task_rows_for_workspace",
    "_task_market_execution_rows_for_workspace",
    "_task_market_row_to_director_task_row",
]
