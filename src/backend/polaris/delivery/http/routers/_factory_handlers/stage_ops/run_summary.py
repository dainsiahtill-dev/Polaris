# ruff: noqa: E402, F403, F405
"""Factory stage-ops helpers — run summary, markdown, audit bundle, convergence.

Extracted verbatim from the former single-file ``stage_ops`` module during the
lossless decomposition of that god-module.

``factory.py``'s ``_rebind_helper_module`` rebinds these callables into the host
router namespace; the package ``__init__`` rewrites ``__module__`` so the rebind
treats them as package-owned. Cross-module free names are injected by
``_wire_cross_module_namespace``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public.contracts import ReadRunLedgerProjectionQueryV1
from polaris.cells.control_plane.run_ledger.public.service import read_run_ledger_projection
from polaris.cells.factory.pipeline.public import (
    FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
    FactoryRun,
    FactoryRunService,
    FactoryTerminalTaskRuntimeProjectionV1,
)
from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from ..mapping import *
from ._common import _safe_events_tail_limit


def _model_dump_json_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        payload = value.dict()
    else:
        payload = value
    if isinstance(payload, dict):
        return payload
    return {}


def _count_events_by_type(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type") or "unknown").strip() or "unknown"
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _extract_taskboard_snapshots(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extract initial and final taskboard snapshots from stage events."""
    initial: dict[str, Any] = {}
    final: dict[str, Any] = {}
    for event in events:
        taskboard = event.get("taskboard")
        if not isinstance(taskboard, dict):
            continue
        if not initial:
            initial = {
                "total": taskboard.get("total"),
                "claimed": taskboard.get("claimed"),
                "completed": taskboard.get("completed"),
                "failed": taskboard.get("failed"),
                "blocked": taskboard.get("blocked"),
            }
        final = {
            "total": taskboard.get("total"),
            "claimed": taskboard.get("claimed"),
            "completed": taskboard.get("completed"),
            "failed": taskboard.get("failed"),
            "blocked": taskboard.get("blocked"),
        }
    return {"initial": initial, "final": final}


def _extract_per_binding_task_status(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract per-task claim/terminal status from director events."""
    tasks: dict[str, dict[str, Any]] = {}
    for event in events:
        task_id = _resolve_task_identifier(event)
        if not task_id:
            payload = event.get("result") if isinstance(event.get("result"), dict) else None
            if isinstance(payload, dict):
                task_id = _resolve_task_identifier(payload)
        if not task_id:
            continue
        event_type = str(event.get("type") or "").strip()
        entry = tasks.setdefault(task_id, {"task_id": task_id, "status": "unknown", "events": []})
        entry["events"].append(event_type)
        if event_type in ("task_completed", "task_success"):
            entry["status"] = "completed"
        elif event_type in ("task_failed", "task_error"):
            entry["status"] = "failed"
        elif event_type in ("task_blocked",):
            entry["status"] = "blocked"
        elif event_type in ("task_claimed", "task_started") and entry["status"] == "unknown":
            entry["status"] = "claimed"
    return list(tasks.values())


def _extract_missing_delivery_targets(
    *,
    run: FactoryRun,
    status_payload: dict[str, Any],
) -> list[str]:
    """Return declared stages that were never reached or completed."""
    configured_stages = list(run.config.stages) if hasattr(run.config, "stages") else []
    completed = set(run.stages_completed or [])
    failed = set(run.stages_failed or [])
    reached = completed | failed
    return [s for s in configured_stages if s not in reached]


def _build_director_convergence(
    *,
    run: FactoryRun,
    events: list[dict[str, Any]],
    status_payload: dict[str, Any],
    summary_json: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build director convergence diagnostics when QA did not run.

    Returns None when QA ran successfully (convergence not relevant).
    """
    qa_gate = next(
        (
            g
            for g in (status_payload.get("gates") or [])
            if isinstance(g, dict) and g.get("gate_name") == "quality_gate"
        ),
        None,
    )
    qa_ran = bool(qa_gate and qa_gate.get("passed") is not None)
    status = str(status_payload.get("status") or "").lower()
    if qa_ran and status == "completed":
        return None

    blocking_phase = str(status_payload.get("current_stage") or status_payload.get("phase") or "").strip()
    taskboard = _extract_taskboard_snapshots(events)
    per_binding = _extract_per_binding_task_status(events)
    missing_targets = _extract_missing_delivery_targets(run=run, status_payload=status_payload)

    director_summary = (summary_json or {}).get("director") if isinstance(summary_json, dict) else None

    return {
        "qa_ran": qa_ran,
        "blocking_phase": blocking_phase,
        "taskboard_initial": taskboard["initial"],
        "taskboard_final": taskboard["final"],
        "missing_delivery_targets": missing_targets,
        "per_binding_task_status": per_binding,
        "director_summary": director_summary if isinstance(director_summary, dict) else None,
    }


def _build_factory_audit_bundle(
    *,
    run: FactoryRun,
    events: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    events_tail_limit: int = 100,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    status_payload = _model_dump_json_dict(_map_service_run_to_contract(run))
    summary_json = run.metadata.get("summary_json")
    tail_limit = _safe_events_tail_limit(events_tail_limit)
    events_tail = events[-tail_limit:] if tail_limit > 0 else []
    gates = status_payload.get("gates")
    failure = status_payload.get("failure")

    convergence = _build_director_convergence(
        run=run,
        events=events,
        status_payload=status_payload,
        summary_json=summary_json if isinstance(summary_json, dict) else None,
    )

    result: dict[str, Any] = {
        "run_id": status_payload.get("run_id") or run.id,
        "status": status_payload.get("status"),
        "phase": status_payload.get("phase"),
        "progress": status_payload.get("progress"),
        "current_stage": status_payload.get("current_stage"),
        "last_successful_stage": status_payload.get("last_successful_stage"),
        "gates": gates if isinstance(gates, list) else [],
        "failure": failure if isinstance(failure, dict) else None,
        "events_tail": events_tail,
        "artifacts": artifacts,
        "summary_md": str(run.metadata.get("summary_md") or "").strip() or None,
        "summary_json": summary_json if isinstance(summary_json, dict) else None,
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "evidence_counts": {
            "events_total": len(events),
            "events_tail": len(events_tail),
            "artifacts": len(artifacts),
            "gates": len(gates) if isinstance(gates, list) else 0,
            "failures": 1 if isinstance(failure, dict) else 0,
            "summary_md": 1 if str(run.metadata.get("summary_md") or "").strip() else 0,
            "summary_json": 1 if isinstance(summary_json, dict) else 0,
            "event_types": _count_events_by_type(events),
        },
    }
    if convergence is not None:
        result["director_convergence"] = convergence
    return result


def _factory_run_identity(*, run: FactoryRun, workspace: str) -> dict[str, Any]:
    start_request = run.metadata.get("factory_start_request")
    start_request_map = start_request if isinstance(start_request, dict) else {}
    start_metadata = start_request_map.get("metadata")
    start_metadata_map = start_metadata if isinstance(start_metadata, dict) else {}
    return {
        "schema_version": "factory.run_identity.v1",
        "run_id": run.id,
        "factory_run_id": run.id,
        "workspace": str(workspace),
        "requested_project_id": str(
            start_metadata_map.get("requested_project_id")
            or start_metadata_map.get("factory_bench_requested_project_id")
            or start_metadata_map.get("factory_bench_project_id")
            or ""
        ),
        "canonical_project_id": str(
            start_metadata_map.get("canonical_project_id")
            or start_metadata_map.get("factory_bench_canonical_project_id")
            or start_metadata_map.get("factory_bench_project_id")
            or ""
        ),
        "instance_id": str(
            start_metadata_map.get("instance_id") or start_metadata_map.get("launcher_instance_id") or ""
        ),
        "backend_port": start_metadata_map.get("backend_port"),
        "frontend_port": start_metadata_map.get("frontend_port"),
    }


def _attach_control_plane_projection(
    *,
    bundle: dict[str, Any],
    run: FactoryRun,
    workspace: str,
) -> dict[str, Any] | None:
    identity = _factory_run_identity(run=run, workspace=workspace)
    bundle["factory_run_id"] = run.id
    bundle["workspace"] = str(workspace)
    bundle["run_identity"] = identity
    projection_errors: list[dict[str, str]] = []
    run_ledger_projection: dict[str, Any] | None = None
    try:
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(workspace=str(workspace), run_id=run.id)
        ).projection
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        projection_errors.append(
            {
                "code": "RUN_LEDGER_PROJECTION_UNAVAILABLE",
                "message": str(exc)[:300],
                "exception_type": type(exc).__name__,
            }
        )
    else:
        run_ledger_projection = projection
        bundle["control_plane_projection"] = projection
        bundle["run_ledger_projection"] = projection

    terminal_snapshot_payload = run.metadata.get(FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY)
    terminal_snapshot: FactoryTerminalTaskRuntimeProjectionV1 | None = None
    if isinstance(terminal_snapshot_payload, Mapping):
        try:
            terminal_snapshot = FactoryTerminalTaskRuntimeProjectionV1.from_dict(terminal_snapshot_payload)
            if terminal_snapshot.factory_run_id != run.id:
                raise ValueError("terminal TaskRuntime snapshot factory_run_id mismatch")
            if Path(terminal_snapshot.workspace).expanduser().resolve() != Path(workspace).expanduser().resolve():
                raise ValueError("terminal TaskRuntime snapshot workspace mismatch")
        except (OSError, TypeError, ValueError) as exc:
            projection_errors.append(
                {
                    "code": "TASK_RUNTIME_TERMINAL_PROJECTION_INVALID",
                    "message": str(exc)[:300],
                    "exception_type": type(exc).__name__,
                }
            )
        else:
            bundle["task_runtime_projection"] = dict(terminal_snapshot.projection)

    if terminal_snapshot is None:
        try:
            task_runtime_projection = TaskRuntimeService(str(workspace)).query_observable_task_rows_projection()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            projection_errors.append(
                {
                    "code": "TASK_RUNTIME_PROJECTION_UNAVAILABLE",
                    "message": str(exc)[:300],
                    "exception_type": type(exc).__name__,
                }
            )
        else:
            bundle["task_runtime_projection"] = task_runtime_projection.to_authority_dict(factory_run_id=run.id)

    if projection_errors:
        bundle["control_plane_projection_error"] = {
            "schema_version": "factory.control_plane_projection_error.v1",
            "code": "CONTROL_PLANE_PROJECTION_INCOMPLETE",
            "errors": projection_errors,
        }
    return run_ledger_projection


async def _persist_run_summary(
    *,
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    workspace: str,
    status: str,
) -> None:
    if await service.get_run(run_id) is None:
        return

    def apply_summary(run: FactoryRun) -> None:
        summary_json = _build_summary_json(run=run, payload=payload, status=status, workspace=workspace)
        run.metadata["summary_json"] = summary_json
        run.metadata["summary_md"] = _build_summary_markdown(summary_json)

    await service.apply_automatic_router_mutation(
        run_id,
        operation="summary_projection",
        mutation=apply_summary,
    )


def _build_summary_json(
    *,
    run: FactoryRun,
    payload: FactoryStartRequest,
    status: str,
    workspace: str,
) -> dict[str, Any]:
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    history = metadata.get("loop_history")
    loop_history = history if isinstance(history, list) else []
    docs_state = metadata.get("loop_last_docs_state")
    if not isinstance(docs_state, dict):
        docs_state = {}
    failure = metadata.get("failure")
    if not isinstance(failure, dict):
        failure = {}
    return {
        "run_id": run.id,
        "status": status,
        "workspace": workspace,
        "start_from": payload.start_from,
        "run_director": bool(payload.run_director),
        "loop_enabled": bool(payload.loop),
        "stages_configured": list(run.config.stages or []),
        "stages_completed": list(run.stages_completed or []),
        "stages_failed": list(run.stages_failed or []),
        "loop_cycles_executed": int(metadata.get("loop_cycles_executed") or 0),
        "loop_stop_reason": str(metadata.get("loop_stop_reason") or "").strip() or None,
        "docs_pipeline": docs_state,
        "loop_history": loop_history,
        "failure": failure or None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_summary_markdown(summary_json: dict[str, Any]) -> str:
    status = str(summary_json.get("status") or "FAIL").strip().upper()
    run_id = str(summary_json.get("run_id") or "").strip()
    loop_enabled = bool(summary_json.get("loop_enabled"))
    loop_cycles = int(summary_json.get("loop_cycles_executed") or 0)
    stop_reason = str(summary_json.get("loop_stop_reason") or "").strip() or "n/a"
    completed = summary_json.get("stages_completed")
    failed = summary_json.get("stages_failed")
    completed_text = ", ".join(completed) if isinstance(completed, list) and completed else "none"
    failed_text = ", ".join(failed) if isinstance(failed, list) and failed else "none"

    lines = [
        "# Factory Run Summary",
        "",
        f"- Run ID: `{run_id}`",
        f"- Status: `{status}`",
        f"- Workspace: `{summary_json.get('workspace')}`",
        f"- Start From: `{summary_json.get('start_from')}`",
        f"- Loop Enabled: `{loop_enabled}`",
        f"- Loop Cycles Executed: `{loop_cycles}`",
        f"- Loop Stop Reason: `{stop_reason}`",
        f"- Stages Completed: `{completed_text}`",
        f"- Stages Failed: `{failed_text}`",
    ]

    failure = summary_json.get("failure")
    if isinstance(failure, dict) and failure:
        lines.extend(
            [
                "",
                "## Failure",
                "",
                f"- Stage: `{failure.get('stage')}`",
                f"- Code: `{failure.get('code')}`",
                f"- Detail: {failure.get('detail')}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "_attach_control_plane_projection",
    "_build_director_convergence",
    "_build_factory_audit_bundle",
    "_build_summary_json",
    "_build_summary_markdown",
    "_count_events_by_type",
    "_extract_missing_delivery_targets",
    "_extract_per_binding_task_status",
    "_extract_taskboard_snapshots",
    "_factory_run_identity",
    "_model_dump_json_dict",
    "_persist_run_summary",
]
