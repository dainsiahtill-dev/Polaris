"""Run-ledger overlay helpers for runtime projection service package."""

from __future__ import annotations

from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    project_tool_lifecycle_failure_status,
    read_run_ledger_projection,
)
from polaris.cells.qa.audit_verdict.public import (
    QA_DEFAULT_TASK_BOUNDARY_FAILURE_CLASS,
    normalize_qa_failure_class,
    project_qa_failure_execution_state,
)

from ._models import logger


def _extract_run_ledger_run_id(*payloads: dict[str, Any] | None) -> str:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("run_id", "workflow_id", "factory_run_id"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        for nested_key in ("status", "metrics", "raw_workflow_status", "metadata"):
            nested = payload.get(nested_key)
            if not isinstance(nested, dict):
                continue
            for key in ("run_id", "workflow_id", "factory_run_id"):
                value = str(nested.get(key) or "").strip()
                if value:
                    return value
    return ""


def _read_run_ledger_projection_for_run(workspace: str, run_id: str) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        return {
            "available": False,
            "projection_source": "run_ledger",
            "missing_required_run_id": True,
        }
    try:
        result = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=workspace,
                run_id=normalized_run_id,
                max_runs=1,
                include_migration_ledgers=False,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("runtime projection: failed to read run ledger projection", exc_info=True)
        return {}
    projection = dict(result.projection) if isinstance(result.projection, dict) else {}
    projection.setdefault("bound_run_id", normalized_run_id)
    return projection


def _apply_run_ledger_director_status_overlay(
    status: dict[str, Any],
    run_ledger_projection: dict[str, Any] | None,
) -> dict[str, Any]:
    projection = run_ledger_projection if isinstance(run_ledger_projection, dict) else {}
    if not projection or not bool(projection.get("available")):
        return status
    merged = dict(status)
    tool_lifecycle = projection.get("tool_lifecycle")
    tool_lifecycle_map = tool_lifecycle if isinstance(tool_lifecycle, dict) else {}
    task_boundary = projection.get("task_boundary")
    task_boundary_map = task_boundary if isinstance(task_boundary, dict) else {}
    latest_boundary = task_boundary_map.get("latest")
    latest_boundary_map = latest_boundary if isinstance(latest_boundary, dict) else {}

    projection_evidence: dict[str, Any] = {
        "ok": bool(projection.get("ok")),
        "status": str(projection.get("status") or ""),
        "detail": str(projection.get("detail") or ""),
        "task_boundary": task_boundary_map,
        "tool_lifecycle": {key: value for key, value in tool_lifecycle_map.items() if key != "events"},
    }
    merged["run_ledger_projection"] = projection_evidence
    lifecycle_failure = project_tool_lifecycle_failure_status(tool_lifecycle_map)
    if bool(lifecycle_failure.get("failed")):
        failure_class = str(lifecycle_failure.get("failure_class") or "TOOL_LIFECYCLE_FAILED").strip()
        failure_code = failure_class.lower()
        merged.update(
            {
                "source": "run_ledger_projection",
                "state": "FAILED_PLATFORM",
                "running": False,
                "execution_state": "FAILED_PLATFORM",
                "error_code": failure_code,
                "last_error": str(lifecycle_failure.get("reason") or failure_class),
                "blocked_reason": failure_code,
            }
        )
    elif latest_boundary_map and not bool(latest_boundary_map.get("ok", True)):
        boundary_status = _task_boundary_status_projection(latest_boundary_map)
        failure_class = str(boundary_status["failure_class"])
        execution_state = str(boundary_status["execution_state"])
        merged.update(
            {
                "source": "run_ledger_projection",
                "state": execution_state,
                "running": False,
                "execution_state": execution_state,
                "error_code": failure_class.lower(),
                "last_error": str(boundary_status["reason"]),
                "blocked_reason": failure_class.lower(),
            }
        )
    else:
        merged["execution_state"] = "COMPLETED_VERIFIED" if bool(projection.get("ok")) else "PENDING"
    return merged


def _task_boundary_execution_state(failure_class: str) -> str:
    return project_qa_failure_execution_state(
        failure_class,
        default=QA_DEFAULT_TASK_BOUNDARY_FAILURE_CLASS,
    )


def _task_boundary_status_projection(latest_boundary: dict[str, Any]) -> dict[str, Any]:
    """Project one Run Ledger task-boundary verdict into runtime status fields."""

    boundary_ok = bool(latest_boundary.get("ok", True))
    failure_class = normalize_qa_failure_class(
        str(latest_boundary.get("failure_class") or QA_DEFAULT_TASK_BOUNDARY_FAILURE_CLASS).strip()
    )
    execution_state = "COMPLETED_VERIFIED" if boundary_ok else _task_boundary_execution_state(failure_class)
    reason = str(latest_boundary.get("reason") or failure_class or execution_state).strip()
    return {
        "boundary_ok": boundary_ok,
        "failure_class": failure_class,
        "execution_state": execution_state,
        "reason": reason,
        "responsible_layer": str(latest_boundary.get("responsible_layer") or "").strip(),
    }


def _latest_task_boundary(run_ledger_projection: dict[str, Any] | None) -> dict[str, Any]:
    projection = run_ledger_projection if isinstance(run_ledger_projection, dict) else {}
    if not projection:
        return {}
    task_boundary = projection.get("task_boundary")
    task_boundary_map = task_boundary if isinstance(task_boundary, dict) else {}
    latest = task_boundary_map.get("latest")
    return dict(latest) if isinstance(latest, dict) else {}


def _row_task_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    metadata_map = metadata if isinstance(metadata, dict) else {}
    return str(
        row.get("task_id")
        or row.get("id")
        or row.get("taskId")
        or metadata_map.get("task_id")
        or metadata_map.get("pm_task_id")
        or metadata_map.get("workflow_task_id")
        or ""
    ).strip()


def _task_boundary_row_from_latest(latest_boundary: dict[str, Any]) -> dict[str, Any]:
    boundary_task_id = str(latest_boundary.get("task_id") or latest_boundary.get("taskId") or "").strip()
    if not boundary_task_id:
        return {}
    boundary_status = _task_boundary_status_projection(latest_boundary)
    boundary_ok = bool(boundary_status["boundary_ok"])
    failure_class = str(boundary_status["failure_class"])
    execution_state = str(boundary_status["execution_state"])
    reason = str(boundary_status["reason"])
    metadata = {
        "source": "run_ledger_projection",
        "status_source": "run_ledger_projection",
        "run_ledger_task_boundary": latest_boundary,
    }
    return {
        "id": boundary_task_id,
        "task_id": boundary_task_id,
        "status": execution_state,
        "state": execution_state,
        "execution_state": execution_state,
        "running": False,
        "failure_class": "" if boundary_ok else failure_class,
        "responsible_layer": str(boundary_status["responsible_layer"]),
        "blocked_reason": "" if boundary_ok else failure_class.lower(),
        "error_message": "" if boundary_ok else reason,
        "run_ledger_projection": {"task_boundary": latest_boundary},
        "metadata": metadata,
    }


def _apply_run_ledger_task_rows_overlay(
    rows: list[dict[str, Any]],
    run_ledger_projection: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Overlay terminal task-boundary verdicts onto visible task rows.

    TaskBoard rows can come from workflow archives, local live Director state, or
    runtime task files. The Run Ledger is the terminal execution fact source, so
    failed task-boundary verdicts must win in projections without mutating the
    stored TaskBoard rows.
    """
    latest_boundary = _latest_task_boundary(run_ledger_projection)
    if not latest_boundary:
        return rows
    if not rows:
        synthetic_row = _task_boundary_row_from_latest(latest_boundary)
        return [synthetic_row] if synthetic_row else rows

    boundary_task_id = str(latest_boundary.get("task_id") or latest_boundary.get("taskId") or "").strip()
    if not boundary_task_id and len(rows) != 1:
        return rows

    boundary_status = _task_boundary_status_projection(latest_boundary)
    boundary_ok = bool(boundary_status["boundary_ok"])
    failure_class = str(boundary_status["failure_class"])
    execution_state = str(boundary_status["execution_state"])
    reason = str(boundary_status["reason"])
    overlay_metadata = {
        "status_source": "run_ledger_projection",
        "run_ledger_task_boundary": latest_boundary,
    }

    overlaid: list[dict[str, Any]] = []
    for row in rows:
        row_map = dict(row)
        row_task_id = _row_task_id(row_map)
        should_overlay = bool(boundary_task_id and row_task_id == boundary_task_id) or (
            not boundary_task_id and len(rows) == 1
        )
        if not should_overlay:
            overlaid.append(row_map)
            continue

        metadata = row_map.get("metadata")
        metadata_map = dict(metadata) if isinstance(metadata, dict) else {}
        metadata_map.update(overlay_metadata)
        row_map.update(
            {
                "status": execution_state,
                "state": execution_state,
                "execution_state": execution_state,
                "running": False,
                "failure_class": "" if boundary_ok else failure_class,
                "responsible_layer": str(boundary_status["responsible_layer"]),
                "blocked_reason": "" if boundary_ok else failure_class.lower(),
                "error_message": "" if boundary_ok else reason,
                "run_ledger_projection": {"task_boundary": latest_boundary},
                "metadata": metadata_map,
            }
        )
        overlaid.append(row_map)
    return overlaid
