import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.archive.factory_archive.public.service import (
    get_factory_manifest,
    list_factory_runs,
)
from polaris.cells.archive.run_archive.public.service import (
    get_run_events,
    get_run_manifest,
    list_history_runs as list_archived_runs,
)
from polaris.cells.archive.task_snapshot_archive.public.service import (
    get_task_snapshot_manifest,
    list_task_snapshots,
)
from polaris.cells.runtime.projection.public.service import format_mtime
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.delivery.http.schemas.common import (
    FactorySnapshotsResponse,
    HistoryEventsResponse,
    HistoryManifestResponse,
    HistoryRunListResponse,
    TaskSnapshotsResponse,
)
from polaris.kernelone.runtime.defaults import DEFAULT_WORKSPACE
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

from ._shared import StructuredHTTPException, get_state, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()


def _read_json(path: str) -> Any:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to read JSON from {path}: {e}")
        return None


def _read_json_dict(path: str) -> dict[str, Any]:
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {}


def _first_existing_file(paths: list[str]) -> str:
    for path in paths:
        if path and os.path.isfile(path):
            return path
    return ""


def _read_task_rounds(workspace: str, cache_root: str) -> list[dict[str, Any]]:
    task_history_path = resolve_artifact_path(
        workspace,
        cache_root,
        "runtime/state/task_history.state.json",
    )
    payload = _read_json_dict(task_history_path)
    rounds = payload.get("rounds")
    if not isinstance(rounds, list):
        return []
    output: list[dict[str, Any]] = []
    for item in rounds:
        if isinstance(item, dict):
            output.append(item)
    return output


def _resolve_runs_dir(workspace: str, cache_root: str) -> str:
    return resolve_artifact_path(workspace, cache_root, "runtime/runs")


def _read_director_result(path: str) -> dict[str, Any]:
    payload = _read_json_dict(path)
    status = str(payload.get("status") or "unknown")
    return {
        "status": status,
        "start_time": str(payload.get("start_time") or ""),
        "end_time": str(payload.get("end_time") or ""),
        "successes": int(payload.get("successes") or 0),
        "total": int(payload.get("total") or 0),
        "reason": str(payload.get("reason") or ""),
        "error_code": str(payload.get("error_code") or ""),
    }


def _load_director_runs(runs_dir: str) -> dict[str, dict[str, Any]]:
    director_runs: dict[str, dict[str, Any]] = {}
    if not os.path.isdir(runs_dir):
        return director_runs
    try:
        for entry in os.scandir(runs_dir):
            if not entry.is_dir():
                continue
            result_path = _first_existing_file(
                [
                    os.path.join(entry.path, "results", "director.result.json"),
                ]
            )
            if not os.path.isfile(result_path):
                continue
            director_runs[entry.name] = _read_director_result(result_path)
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to read director runs: {e}")
        return director_runs
    return director_runs


def _normalize_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text)
    return output


def _normalize_non_director_queue(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "id": str(item.get("id") or "").strip(),
                "assigned_to": str(item.get("assigned_to") or "").strip(),
                "title": str(item.get("title") or "").strip(),
            }
        )
    return output


def _normalize_execution_results(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "task_id": str(item.get("task_id") or "").strip(),
                "assigned_to": str(item.get("assigned_to") or "").strip(),
                "status": str(item.get("status") or "unknown").strip().lower(),
                "ok": bool(item.get("ok", False)),
                "blocking": bool(item.get("blocking", False)),
                "summary": str(item.get("summary") or "").strip(),
                "error_code": str(item.get("error_code") or "").strip(),
                "duration_ms": int(item.get("duration_ms") or 0),
                "artifact_path": str(item.get("artifact_path") or "").strip(),
                "output": item.get("output") if isinstance(item.get("output"), dict) else {},
            }
        )
    return output


def _load_role_queue_entries(role_queue_dir: str, workspace: str) -> list[dict[str, Any]]:
    if not os.path.isdir(role_queue_dir):
        return []
    entries: list[dict[str, Any]] = []
    try:
        files = sorted(
            [entry for entry in os.scandir(role_queue_dir) if entry.is_file() and entry.name.endswith(".json")],
            key=lambda item: item.name.lower(),
        )
    except (RuntimeError, ValueError):
        # Directory scan failed - return empty list for graceful degradation
        logger.debug("Failed to scan role_queue directory: %s", role_queue_dir)
        return []
    for file_entry in files:
        payload = _read_json_dict(file_entry.path)
        task_dict = payload.get("task")
        task = task_dict if isinstance(task_dict, dict) else {}
        result_dict = payload.get("result")
        result = result_dict if isinstance(result_dict, dict) else {}
        rel_path = file_entry.path
        try:
            rel_path = os.path.relpath(file_entry.path, workspace).replace("\\", "/")
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to compute relative path: {e}")
        entries.append(
            {
                "task_id": str(task.get("id") or "").strip(),
                "assigned_to": str(task.get("assigned_to") or "").strip(),
                "status": str(result.get("status") or "unknown").strip().lower(),
                "ok": bool(result.get("ok", False)),
                "blocking": bool(result.get("blocking", False)),
                "error_code": str(result.get("error_code") or "").strip(),
                "summary": str(result.get("summary") or "").strip(),
                "artifact_path": rel_path,
            }
        )
    return entries


def _normalize_status(status: str) -> str:
    lowered = str(status or "").strip().lower()
    if "success" in lowered or lowered == "pass":
        return "passed"
    if "blocked" in lowered:
        return "blocked"
    if "fail" in lowered:
        return "failed"
    if lowered:
        return lowered
    return "unknown"


def _build_factory_flow(
    workspace: str,
    round_id: str,
    runs_dir: str,
    fallback_routing: dict[str, Any],
    fallback_routing_path: str,
    fallback_execution: dict[str, Any],
    fallback_execution_path: str,
    director_result: dict[str, Any],
) -> dict[str, Any]:
    run_dir = os.path.join(runs_dir, round_id) if round_id and runs_dir else ""
    routing_path = (
        _first_existing_file(
            [
                os.path.join(run_dir, "state", "assignee_routing.state.json"),
                os.path.join(run_dir, "ASSIGNEE_ROUTING.json"),
            ]
        )
        if run_dir
        else ""
    )
    execution_path = (
        _first_existing_file(
            [
                os.path.join(run_dir, "state", "assignee_execution.state.json"),
                os.path.join(run_dir, "ASSIGNEE_EXECUTION.json"),
            ]
        )
        if run_dir
        else ""
    )
    if run_dir and not routing_path:
        routing_path = os.path.join(run_dir, "state", "assignee_routing.state.json")
    if run_dir and not execution_path:
        execution_path = os.path.join(run_dir, "state", "assignee_execution.state.json")
    routing = _read_json_dict(routing_path)
    execution = _read_json_dict(execution_path)

    if not routing and fallback_routing and str(fallback_routing.get("run_id") or "") == round_id:
        routing = dict(fallback_routing)
        routing_path = fallback_routing_path
    if not execution and fallback_execution and str(fallback_execution.get("run_id") or "") == round_id:
        execution = dict(fallback_execution)
        execution_path = fallback_execution_path

    director_task_ids = _normalize_str_list(routing.get("director_task_ids"))
    docs_only_task_ids = _normalize_str_list(routing.get("docs_only_task_ids"))
    non_director_queue = _normalize_non_director_queue(routing.get("non_director_queue"))

    execution_results = _normalize_execution_results(execution.get("results"))
    hard_block = bool(execution.get("hard_block", False))
    blocked_reasons = _normalize_str_list(execution.get("blocked_reasons"))

    generated_director_tasks_raw = execution.get("generated_director_tasks")
    generated_director_tasks: list[dict[str, Any]] = []
    if isinstance(generated_director_tasks_raw, list):
        for item in generated_director_tasks_raw:
            if isinstance(item, dict):
                generated_director_tasks.append(item)
    generated_ids = _normalize_str_list(routing.get("generated_director_task_ids"))
    for task in generated_director_tasks:
        task_id = str(task.get("id") or "").strip()
        if task_id and task_id not in generated_ids:
            generated_ids.append(task_id)

    role_queue_dir = os.path.join(run_dir, "role_queue") if run_dir else ""
    role_queue_entries = _load_role_queue_entries(role_queue_dir, workspace)
    if not role_queue_entries and execution_results:
        role_queue_entries = execution_results

    director_status = _normalize_status(str(director_result.get("status") or ""))
    pipeline_reason = ""
    pipeline_status = "unknown"
    if hard_block:
        pipeline_status = "blocked"
        pipeline_reason = "; ".join(blocked_reasons) if blocked_reasons else "non-director queue hard blocked"
    elif director_status in ("blocked", "failed", "passed"):
        pipeline_status = director_status
        pipeline_reason = str(director_result.get("reason") or director_result.get("error_code") or "").strip()
    elif non_director_queue or execution_results or director_task_ids:
        pipeline_status = "in_progress"
    elif docs_only_task_ids:
        pipeline_status = "pm_only"

    auditor_fail_detected = any(
        row.get("error_code") == "AUDITOR_FAILS_WITH_DEFECT" for row in execution_results if isinstance(row, dict)
    )

    return {
        "pipeline_status": {
            "status": pipeline_status,
            "reason": pipeline_reason,
            "hard_block": hard_block,
        },
        "routing": {
            "director_task_ids": director_task_ids,
            "docs_only_task_ids": docs_only_task_ids,
            "non_director_queue": non_director_queue,
        },
        "non_director_execution": {
            "results": execution_results,
            "hard_block": hard_block,
            "blocked_reasons": blocked_reasons,
        },
        "defect_loop": {
            "auditor_fail_detected": auditor_fail_detected,
            "generated_count": len(generated_ids),
            "generated_director_task_ids": generated_ids,
            "generated_director_tasks": generated_director_tasks,
        },
        "role_queue": {
            "artifact_count": len(role_queue_entries),
            "entries": role_queue_entries,
        },
        "artifacts": {
            "routing_path": routing_path,
            "execution_path": execution_path,
            "role_queue_dir": role_queue_dir,
        },
    }


def _load_merged_rounds(state: AppState, include_factory_flow: bool = False) -> list[dict[str, Any]]:
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)
    task_rounds = _read_task_rounds(workspace, cache_root)
    runs_dir = _resolve_runs_dir(workspace, cache_root)
    director_runs = _load_director_runs(runs_dir)

    fallback_routing_path = resolve_artifact_path(
        workspace,
        cache_root,
        "runtime/state/assignee_routing.state.json",
    )
    fallback_execution_path = resolve_artifact_path(
        workspace,
        cache_root,
        "runtime/state/assignee_execution.state.json",
    )
    fallback_routing = _read_json_dict(fallback_routing_path)
    fallback_execution = _read_json_dict(fallback_execution_path)

    merged_rounds: list[dict[str, Any]] = []
    for round_data in task_rounds:
        round_id = str(round_data.get("round_id") or "")
        director_result = director_runs.get(round_id) or {}
        merged_round = {**round_data, "director_results": director_result}
        if include_factory_flow:
            merged_round["factory_flow"] = _build_factory_flow(
                workspace,
                round_id,
                runs_dir,
                fallback_routing,
                fallback_routing_path,
                fallback_execution,
                fallback_execution_path,
                director_result,
            )
        merged_rounds.append(merged_round)

    merged_rounds.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return merged_rounds


def _build_factory_summary(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "total_rounds": len(rounds),
        "passed_rounds": 0,
        "blocked_rounds": 0,
        "failed_rounds": 0,
        "in_progress_rounds": 0,
        "unknown_rounds": 0,
        "non_director_results": 0,
        "defect_followups_generated": 0,
        "hard_block_rounds": 0,
        "policy_gate_blocks": 0,
        "finops_blocks": 0,
        "auditor_failures": 0,
    }
    for round_data in rounds:
        flow = round_data.get("factory_flow")
        if not isinstance(flow, dict):
            summary["unknown_rounds"] += 1
            continue
        pipeline_dict = flow.get("pipeline_status")
        pipeline = pipeline_dict if isinstance(pipeline_dict, dict) else {}
        status = str(pipeline.get("status") or "unknown")
        if status == "passed":
            summary["passed_rounds"] += 1
        elif status == "blocked":
            summary["blocked_rounds"] += 1
        elif status == "failed":
            summary["failed_rounds"] += 1
        elif status in ("in_progress", "pm_only"):
            summary["in_progress_rounds"] += 1
        else:
            summary["unknown_rounds"] += 1
        if bool(pipeline.get("hard_block", False)):
            summary["hard_block_rounds"] += 1

        non_director = (
            flow.get("non_director_execution") if isinstance(flow.get("non_director_execution"), dict) else {}
        )
        results = non_director.get("results") if isinstance(non_director, dict) else None
        if isinstance(results, list):
            summary["non_director_results"] += len(results)
            for row in results:
                if not isinstance(row, dict):
                    continue
                error_code = str(row.get("error_code") or "")
                if error_code == "POLICY_GATE_BLOCKED":
                    summary["policy_gate_blocks"] += 1
                elif error_code == "FINOPS_BUDGET_BLOCKED":
                    summary["finops_blocks"] += 1
                elif error_code == "AUDITOR_FAILS_WITH_DEFECT":
                    summary["auditor_failures"] += 1

        defect_loop_dict = flow.get("defect_loop")
        defect_loop = defect_loop_dict if isinstance(defect_loop_dict, dict) else {}
        summary["defect_followups_generated"] += int(defect_loop.get("generated_count") or 0)
    return summary


@router.get("/history/runs", dependencies=[Depends(require_auth)])
def history_runs(
    request: Request,
    limit: int = 50,
    source: str = "all",  # "runtime", "archived", "all"
) -> dict[str, Any]:
    """List historical runs from both runtime and archived storage.

    Args:
        limit: Maximum number of entries to return
        source: Data source - "runtime" (current runs), "archived" (history), "all" (both)
    """
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)

    runs: list[dict[str, Any]] = []
    archived_runs: list[dict[str, Any]] = []

    # Get runtime runs
    if source in ("runtime", "all"):
        runs_dir = _resolve_runs_dir(workspace, cache_root)
        if os.path.isdir(runs_dir):
            try:
                for entry in os.scandir(runs_dir):
                    if not entry.is_dir():
                        continue
                    run_id = entry.name
                    mtime = format_mtime(entry.path)
                    result_path = _first_existing_file(
                        [
                            os.path.join(entry.path, "results", "director.result.json"),
                        ]
                    )
                    status = "unknown"
                    start_time = ""
                    end_time = ""
                    if os.path.isfile(result_path):
                        result = _read_director_result(result_path)
                        status = result.get("status", "unknown")
                        start_time = result.get("start_time", "")
                        end_time = result.get("end_time", "")
                    runs.append(
                        {
                            "id": run_id,
                            "source": "runtime",
                            "mtime": mtime,
                            "status": status,
                            "start_time": start_time,
                            "end_time": end_time,
                        }
                    )
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to read director runs: {e}")

    # Get archived runs
    if source in ("archived", "all"):
        try:
            archived = list_archived_runs(workspace=workspace, limit=limit, offset=0)
            archived_runs = [
                {
                    "id": r.get("run_id", ""),
                    "source": "archived",
                    "mtime": r.get("archive_timestamp", 0),
                    "status": r.get("status", ""),
                    "archive_timestamp": r.get("archive_timestamp", 0),
                    "archive_reason": r.get("reason", ""),
                }
                for r in archived
            ]
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to read archived runs: {e}")

    # Combine and sort by mtime descending
    all_runs = runs + archived_runs
    all_runs.sort(key=lambda x: x.get("mtime", ""), reverse=True)

    return {"runs": all_runs[:limit]}


@router.get("/history/tasks", dependencies=[Depends(require_auth)])
def history_tasks(request: Request, limit: int = 50) -> dict[str, Any]:
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)
    rounds = _read_task_rounds(workspace, cache_root)
    rounds.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return {"rounds": rounds[:limit]}


@router.get("/history/rounds", dependencies=[Depends(require_auth)])
def history_rounds(request: Request, limit: int = 50) -> dict[str, Any]:
    merged_rounds = _load_merged_rounds(get_state(request), include_factory_flow=False)
    return {"rounds": merged_rounds[:limit]}


@router.get("/history/factory/overview", dependencies=[Depends(require_auth)])
def history_factory_overview(request: Request, limit: int = 50) -> dict[str, Any]:
    merged_rounds = _load_merged_rounds(get_state(request), include_factory_flow=True)
    limited_rounds = merged_rounds[:limit]
    return {"summary": _build_factory_summary(limited_rounds), "rounds": limited_rounds}


@router.get("/history/round/{round_id}", dependencies=[Depends(require_auth)])
def history_round_detail(request: Request, round_id: str) -> dict[str, Any]:
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE
    rounds = _load_merged_rounds(state, include_factory_flow=True)
    round_detail: dict[str, Any] | None = None
    for item in rounds:
        if item.get("round_id") == round_id:
            round_detail = item
            break

    if not round_detail:
        raise StructuredHTTPException(
            status_code=404,
            code="ROUND_NOT_FOUND",
            message=f"Round {round_id} not found",
        )

    artifacts = round_detail.get("artifacts", {})
    artifact_contents: dict[str, str] = {}
    if isinstance(artifacts, dict):
        for artifact_type, path in artifacts.items():
            if not isinstance(path, str):
                continue
            full_path = os.path.join(workspace, path)
            if not os.path.isfile(full_path):
                continue
            try:
                with open(full_path, encoding="utf-8") as handle:
                    if artifact_type == "events_path":
                        lines = handle.readlines()
                        artifact_contents[artifact_type] = "".join(lines[-50:])
                    else:
                        artifact_contents[artifact_type] = handle.read()
            except (RuntimeError, ValueError):
                artifact_contents[artifact_type] = f"Error reading {path}"

    return {"round": round_detail, "artifact_contents": artifact_contents}


# ============================================================================
# New v2 History API - Supports Archived Data
# ============================================================================


@router.get(
    "/v2/history/runs",
    dependencies=[Depends(require_auth)],
    response_model=HistoryRunListResponse,
)
def v2_history_runs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    source: str = "all",  # "runtime", "archived", "all"
) -> dict[str, Any]:
    """List historical runs from runtime and archived storage.

    Returns:
        Paginated list of runs with total count.
    """
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE

    runs: list[dict[str, Any]] = []
    archived_runs: list[dict[str, Any]] = []

    # Get runtime runs
    if source in ("runtime", "all"):
        cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)
        runs_dir = resolve_artifact_path(workspace, cache_root, "runtime/runs")

        if os.path.isdir(runs_dir):
            try:
                for entry in os.scandir(runs_dir):
                    if not entry.is_dir():
                        continue
                    run_id = entry.name
                    mtime = format_mtime(entry.path)
                    result_path = _first_existing_file([os.path.join(entry.path, "results", "director.result.json")])
                    status = "unknown"
                    if os.path.isfile(result_path):
                        result = _read_director_result(result_path)
                        status = result.get("status", "unknown")
                    runs.append(
                        {
                            "id": run_id,
                            "source": "runtime",
                            "mtime": mtime,
                            "status": status,
                        }
                    )
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to read runtime runs: {e}")

    # Get archived runs
    if source in ("archived", "all"):
        try:
            archived = list_archived_runs(workspace=workspace, limit=limit + offset, offset=0)
            archived_runs = [
                {
                    "id": r.get("run_id", ""),
                    "source": "archived",
                    "mtime": r.get("archive_timestamp", 0),
                    "status": r.get("status", ""),
                    "archive_timestamp": r.get("archive_timestamp", 0),
                    "archive_reason": r.get("reason", ""),
                }
                for r in archived
            ]
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to read archived runs: {e}")

    # Combine and sort by mtime descending
    all_runs = runs + archived_runs
    all_runs.sort(key=lambda x: x.get("mtime", 0), reverse=True)

    return {"runs": all_runs[offset : offset + limit], "total": len(all_runs)}


@router.get(
    "/v2/history/runs/{run_id}/manifest",
    dependencies=[Depends(require_auth)],
    response_model=HistoryManifestResponse,
)
def v2_history_run_manifest(request: Request, run_id: str) -> dict[str, Any]:
    """Get manifest for an archived run.

    Returns:
        Run manifest metadata.
    """
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE

    try:
        manifest = get_run_manifest(workspace, run_id)

        if manifest is None:
            raise StructuredHTTPException(
                status_code=404,
                code="MANIFEST_NOT_FOUND",
                message=f"Manifest not found for run {run_id}",
            )

        return {"manifest": manifest}
    except StructuredHTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to get manifest for run {run_id}: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="INTERNAL_ERROR",
            message="internal error",
        ) from e


@router.get(
    "/v2/history/runs/{run_id}/events",
    dependencies=[Depends(require_auth)],
    response_model=HistoryEventsResponse,
)
def v2_history_run_events(request: Request, run_id: str) -> dict[str, Any]:
    """Get events for an archived run (auto-decompresses .zst).

    Returns:
        Run events list with count.
    """
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE

    try:
        events = get_run_events(workspace, run_id)

        return {"run_id": run_id, "events": events, "count": len(events)}
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to get events for run {run_id}: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="INTERNAL_ERROR",
            message="internal error",
        ) from e


@router.get(
    "/v2/history/tasks/snapshots",
    dependencies=[Depends(require_auth)],
    response_model=TaskSnapshotsResponse,
)
def v2_history_task_snapshots(
    request: Request,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List archived task snapshots.

    Returns:
        Paginated task snapshots with total count.
    """
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE

    try:
        snapshots = list_task_snapshots(workspace=workspace, limit=limit, offset=offset)
        return {
            "snapshots": snapshots,
            "total": len(snapshots),
        }
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to list task snapshots: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="INTERNAL_ERROR",
            message="internal error",
        ) from e


@router.get(
    "/v2/history/factory/snapshots",
    dependencies=[Depends(require_auth)],
    response_model=FactorySnapshotsResponse,
)
def v2_history_factory_snapshots(
    request: Request,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List archived factory runs.

    Returns:
        Paginated factory snapshots with total count.
    """
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE

    try:
        snapshots = list_factory_runs(workspace=workspace, limit=limit, offset=offset)
        return {
            "factory_runs": snapshots,
            "total": len(snapshots),
        }
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to list factory snapshots: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="INTERNAL_ERROR",
            message="internal error",
        ) from e


# ============================================================================
# v1 History Manifest Endpoints
# ============================================================================


@router.get("/history/runs/{run_id}/manifest", dependencies=[Depends(require_auth)])
def history_run_manifest(request: Request, run_id: str) -> dict[str, Any]:
    """Get manifest for an archived run."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE

    try:
        manifest = get_run_manifest(workspace, run_id)

        if manifest is None:
            raise StructuredHTTPException(
                status_code=404,
                code="MANIFEST_NOT_FOUND",
                message=f"Manifest not found for run {run_id}",
            )

        return {"manifest": manifest}
    except StructuredHTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to get manifest for run {run_id}: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="INTERNAL_ERROR",
            message="internal error",
        ) from e


@router.get("/history/tasks/{snapshot_id}/manifest", dependencies=[Depends(require_auth)])
def history_task_snapshot_manifest(request: Request, snapshot_id: str) -> dict[str, Any]:
    """Get manifest for an archived task snapshot."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE

    try:
        manifest = get_task_snapshot_manifest(workspace, snapshot_id)

        if manifest is None:
            raise StructuredHTTPException(
                status_code=404,
                code="MANIFEST_NOT_FOUND",
                message=f"Manifest not found for task snapshot {snapshot_id}",
            )

        return {"manifest": manifest}
    except StructuredHTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to get manifest for task snapshot {snapshot_id}: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="INTERNAL_ERROR",
            message="internal error",
        ) from e


@router.get("/history/factory/{run_id}/manifest", dependencies=[Depends(require_auth)])
def history_factory_manifest(request: Request, run_id: str) -> dict[str, Any]:
    """Get manifest for an archived factory run."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or DEFAULT_WORKSPACE

    try:
        manifest = get_factory_manifest(workspace, run_id)

        if manifest is None:
            raise StructuredHTTPException(
                status_code=404,
                code="MANIFEST_NOT_FOUND",
                message=f"Manifest not found for factory run {run_id}",
            )

        return {"manifest": manifest}
    except StructuredHTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to get manifest for factory run {run_id}: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="INTERNAL_ERROR",
            message="internal error",
        ) from e
