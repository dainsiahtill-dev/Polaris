"""Helpers for persisting and querying orchestration runtime state."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any

from polaris.cells.runtime.projection.internal.io_helpers import read_json, resolve_artifact_path
from polaris.infrastructure.realtime.process_local.signal_hub import REALTIME_SIGNAL_HUB
from polaris.kernelone.fs.text_ops import write_json_atomic

logger = logging.getLogger(__name__)

# Note: State file uses legacy name for backward compatibility
WORKFLOW_STATE_FILE = "runtime/state/workflow.workflow.state.json"
WORKFLOW_PM_TASKS_FILE = "runtime/contracts/pm_tasks.contract.json"
_TERMINAL_STATUSES = {"completed", "failed", "terminated", "timed_out", "canceled"}
_FAILURE_STATUSES = {
    "blocked",
    "director_failed",
    "director_failures_present",
    "error",
    "failed",
    "fail",
    "failure",
    "integration_qa_error",
    "integration_qa_failed",
    "integration_qa_runtime_error",
    "qa_failed",
    "task_failed",
}
_NON_FAILURE_STATUSES = {
    "completed",
    "done",
    "integration_qa_passed",
    "ok",
    "passed",
    "success",
    "succeeded",
}
_WORKFLOW_STATE_MAP = {
    "planned": "pending",
    "pending": "pending",
    "queued": "pending",
    "ready": "ready",
    "claimed": "claimed",
    "running": "in_progress",
    "executing": "in_progress",
    "in_progress": "in_progress",
    "completed": "completed",
    "success": "completed",
    "failed": "failed",
    "error": "failed",
    "blocked": "blocked",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "retrying": "in_progress",  # 重试中视为进行中
    "paused": "paused",
    "timeout": "timed_out",
    "timed_out": "timed_out",
    "terminated": "terminated",
}
_RUNTIME_ENV_KEYS = (
    "KERNELONE_RUNTIME_DB",
    "KERNELONE_RUNTIME_CACHE_ROOT",
    "KERNELONE_CONTEXT_ROOT",
)
_RUNTIME_ENV_LOCK = threading.Lock()


def describe_workflow_sync(workflow_id: str, config: Any) -> dict[str, Any]:
    """Module-level wrapper kept patchable for tests and adapters."""
    from polaris.cells.orchestration.workflow_runtime.public.service import (
        describe_workflow_sync as _describe_workflow_sync,
    )

    return _describe_workflow_sync(workflow_id, config)


def query_workflow_sync(
    workflow_id: str,
    query_name: str,
    config: Any = None,
) -> dict[str, Any]:
    """Module-level wrapper kept patchable for tests and adapters."""
    from polaris.cells.orchestration.workflow_runtime.public.service import (
        query_workflow_sync as _query_workflow_sync,
    )

    return _query_workflow_sync(workflow_id, query_name, config=config)


def workflow_state_path(workspace: str, cache_root: str) -> str:
    """Resolve the canonical runtime path for workflow state."""
    return resolve_artifact_path(
        workspace,
        cache_root,
        WORKFLOW_STATE_FILE,
    )


def write_workflow_state(
    workspace: str,
    cache_root: str,
    payload: dict[str, Any],
) -> str:
    """Persist the latest workflow submission metadata and trigger realtime update."""
    path = workflow_state_path(workspace, cache_root)
    if path:
        write_json_atomic(path, payload)
        _trigger_realtime_update(cache_root)
    return path


async def _trigger_realtime_update_async(cache_root: str) -> None:
    """Trigger a realtime signal to notify listeners of state changes (async version)."""
    if not cache_root or not os.path.isdir(cache_root):
        return
    try:
        state_file = os.path.join(cache_root, "state", "workflow.workflow.state.json")
        if os.path.exists(state_file):
            os.utime(state_file, None)
    except OSError as e:
        logger.debug(f"Failed to update workflow state file: {e}")
    await REALTIME_SIGNAL_HUB.notify(source="workflow_state_update", root=cache_root)


def _trigger_realtime_update(cache_root: str) -> None:
    """Trigger a realtime signal to notify listeners of state changes."""
    if not cache_root or not os.path.isdir(cache_root):
        return
    try:
        state_file = os.path.join(cache_root, "state", "workflow.workflow.state.json")
        if os.path.exists(state_file):
            os.utime(state_file, None)
    except OSError as e:
        logger.debug(f"Failed to trigger realtime update: {e}")
    try:
        loop = asyncio.get_running_loop()
        _ = loop.create_task(REALTIME_SIGNAL_HUB.notify(source="workflow_state_update", root=cache_root))  # noqa: RUF006
    except RuntimeError:
        pass


def load_workflow_state(
    workspace: str,
    cache_root: str,
) -> dict[str, Any]:
    """Load the latest workflow submission metadata."""
    path = workflow_state_path(workspace, cache_root)
    if not path:
        return {}
    payload = read_json(path)
    if isinstance(payload, dict):
        payload.setdefault("state_path", path)
        return payload
    return {}


def _is_terminal_status(status: str) -> bool:
    return str(status or "").strip().lower() in _TERMINAL_STATUSES


def _iter_nested_payloads(value: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Return nested mappings with a conservative depth guard."""
    if depth > 8:
        return []
    if isinstance(value, dict):
        nested_payloads = [value]
        for item in value.values():
            nested_payloads.extend(_iter_nested_payloads(item, depth=depth + 1))
        return nested_payloads
    if isinstance(value, list):
        list_payloads: list[dict[str, Any]] = []
        for item in value:
            list_payloads.extend(_iter_nested_payloads(item, depth=depth + 1))
        return list_payloads
    return []


def _payload_has_failure_signal(value: Any) -> bool:
    for payload in _iter_nested_payloads(value):
        for key in ("director_status", "qa_status", "reason", "status", "error"):
            token = str(payload.get(key) or "").strip().lower()
            if not token or token in _NON_FAILURE_STATUSES:
                continue
            if token in _FAILURE_STATUSES:
                return True
        if payload.get("passed") is False:
            reason = str(payload.get("reason") or payload.get("status") or "").strip().lower()
            if not reason or reason not in _NON_FAILURE_STATUSES:
                return True
        for count_key in ("failures", "blocked"):
            try:
                if int(payload.get(count_key) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _load_workflow_result_artifacts(cache_root: str) -> list[dict[str, Any]]:
    cache_root_value = str(cache_root or "").strip()
    if not cache_root_value:
        return []
    payloads: list[dict[str, Any]] = []
    for relative_path in (
        os.path.join("results", "director.result.json"),
        os.path.join("results", "integration_qa.result.json"),
    ):
        payload = read_json(os.path.join(cache_root_value, relative_path))
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _result_artifacts_prove_terminal_success(payloads: list[dict[str, Any]]) -> bool:
    director_result: dict[str, Any] = {}
    integration_qa_result: dict[str, Any] = {}
    for payload in payloads:
        if "integration_qa" in str(payload.get("reason") or "").lower() or "passed" in payload:
            integration_qa_result = payload
            continue
        if "successes" in payload or "failures" in payload or "blocked" in payload:
            director_result = payload

    director_status = str(director_result.get("status") or "").strip().lower()
    try:
        director_total = int(director_result.get("total") or 0)
        director_failures = int(director_result.get("failures") or 0)
        director_blocked = int(director_result.get("blocked") or 0)
    except (TypeError, ValueError):
        return False

    qa_reason = str(integration_qa_result.get("reason") or "").strip().lower()
    return (
        director_status == "success"
        and director_total > 0
        and director_failures == 0
        and director_blocked == 0
        and integration_qa_result.get("passed") is True
        and qa_reason == "integration_qa_passed"
    )


def _derive_terminal_failure_status(
    *,
    cache_root: str,
    statuses: tuple[str, ...],
    payloads: tuple[Any, ...],
) -> str:
    for status in statuses:
        token = str(status or "").strip().lower()
        if token in {"terminated", "timed_out", "canceled", "cancelled"}:
            return "canceled" if token == "cancelled" else token
        if token in _FAILURE_STATUSES:
            return "failed"

    result_artifacts = _load_workflow_result_artifacts(cache_root)
    if _result_artifacts_prove_terminal_success(result_artifacts):
        return ""

    for payload in (*payloads, *result_artifacts):
        if _payload_has_failure_signal(payload):
            return "failed"
    return ""


def _derive_terminal_success_status(*, cache_root: str) -> str:
    if _result_artifacts_prove_terminal_success(_load_workflow_result_artifacts(cache_root)):
        return "completed"
    return ""


def _snapshot_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _snapshot_tasks(snapshot: Any) -> dict[str, Any]:
    payload = _snapshot_payload(snapshot)
    tasks = payload.get("tasks")
    return tasks if isinstance(tasks, dict) else {}


def _cached_snapshot(record: dict[str, Any], key: str) -> dict[str, Any]:
    payload = record.get(key) if isinstance(record, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _runtime_db_path(cache_root: str) -> str:
    token = str(cache_root or "").strip()
    if not token:
        return ""
    return os.path.join(token, "state", "workflow.runtime.db")


@contextmanager
def _workflow_runtime_environment(workspace: str, cache_root: str) -> Any:
    workspace_value = str(workspace or "").strip()
    cache_root_value = str(cache_root or "").strip()
    overrides: dict[str, str] = {}
    if workspace_value:
        overrides["KERNELONE_CONTEXT_ROOT"] = workspace_value
    runtime_db = _runtime_db_path(cache_root_value)
    if runtime_db:
        overrides["KERNELONE_RUNTIME_DB"] = runtime_db
    if not overrides:
        yield
        return

    with _RUNTIME_ENV_LOCK:
        previous: dict[str, str | None] = {key: os.environ.get(key) for key in _RUNTIME_ENV_KEYS}
        try:
            for key, value in overrides.items():
                os.environ[key] = value
            yield
        finally:
            for key, value in previous.items():  # type: ignore[assignment]
                env_value: str | None = value
                if env_value is not None and env_value:
                    os.environ[key] = env_value
                else:
                    os.environ.pop(key, None)


def _extract_run_id_from_snapshot(snapshot: dict[str, Any] | None) -> str:
    payload = snapshot if isinstance(snapshot, dict) else {}
    direct_run_id = str(
        payload.get("run_id") or payload.get("pm_run_id") or payload.get("workflow_chain_run_id") or ""
    ).strip()
    if direct_run_id:
        return direct_run_id

    history_raw = payload.get("history")
    history: list[Any] = history_raw if isinstance(history_raw, list) else []
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        details_raw = item.get("details")
        details: dict[str, Any] = details_raw if isinstance(details_raw, dict) else {}
        run_id = str(details.get("run_id") or item.get("run_id") or item.get("pm_run_id") or "").strip()
        if run_id:
            return run_id
    return ""


def _resolve_workflow_chain_run_id(
    record: dict[str, Any],
    runtime_snapshot: dict[str, Any] | None,
) -> str:
    candidates = (
        record.get("workflow_chain_run_id"),
        record.get("workflow_input_run_id"),
        _extract_run_id_from_snapshot(runtime_snapshot),
        record.get("run_id"),
    )
    for candidate in candidates:
        token = str(candidate or "").strip()
        if token:
            return token
    return ""


def canonicalize_workflow_task_state(value: Any) -> str:
    """Map workflow-internal states to the canonical UI contract."""
    token = str(value or "").strip().lower()
    if not token:
        return "pending"
    return _WORKFLOW_STATE_MAP.get(token, token)


def get_workflow_task_snapshot(workflow_status: dict[str, Any] | None) -> dict[str, Any]:
    """Return the richest task snapshot available for a workflow."""
    payload = workflow_status if isinstance(workflow_status, dict) else {}
    director_snapshot = _snapshot_payload(payload.get("director_runtime_snapshot"))
    director_tasks = _snapshot_tasks(director_snapshot)
    if director_tasks:
        return director_tasks
    return _snapshot_tasks(payload.get("runtime_snapshot"))


def get_workflow_stage(workflow_status: dict[str, Any] | None) -> str:
    """Return the best available current stage token for a workflow."""
    payload = workflow_status if isinstance(workflow_status, dict) else {}
    for source in (
        payload.get("qa_runtime_snapshot"),
        payload.get("director_runtime_snapshot"),
        payload.get("runtime_snapshot"),
        payload.get("record"),
        payload,
    ):
        snapshot = _snapshot_payload(source)
        stage = str(snapshot.get("stage") or "").strip().lower()
        if stage:
            return stage
    return ""


def load_workflow_base_tasks(workspace: str, cache_root: str) -> list[dict[str, Any]]:
    """Load the canonical PM task contract used to render workflow task titles/details."""
    path = resolve_artifact_path(workspace, cache_root, WORKFLOW_PM_TASKS_FILE)
    if not path:
        return []
    payload = read_json(path)
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, list):
        return []
    return [dict(item) for item in tasks if isinstance(item, dict)]


def _workflow_result_run_tokens(workflow_status: dict[str, Any] | None) -> list[str]:
    payload = workflow_status if isinstance(workflow_status, dict) else {}
    record = payload.get("record")
    record_payload = record if isinstance(record, dict) else {}
    candidates = (
        payload.get("workflow_chain_run_id"),
        record_payload.get("workflow_chain_run_id"),
        payload.get("workflow_input_run_id"),
        record_payload.get("workflow_input_run_id"),
        payload.get("run_id"),
        record_payload.get("run_id"),
    )
    tokens: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = str(candidate or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _workflow_task_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("task_id") or "").strip()


def _workflow_task_result_path(cache_root: str, run_token: str, task_id: str) -> str:
    cache_root_value = str(cache_root or "").strip()
    if not cache_root_value or not run_token or not task_id:
        return ""
    workflow_root = os.path.abspath(os.path.join(cache_root_value, "workflow"))
    path = os.path.abspath(os.path.join(workflow_root, run_token, task_id, "director.result.json"))
    workflow_root_norm = os.path.normcase(workflow_root)
    path_norm = os.path.normcase(path)
    if path_norm != workflow_root_norm and not path_norm.startswith(workflow_root_norm + os.sep):
        return ""
    return path


def _load_workflow_task_result(cache_root: str, run_tokens: list[str], task_id: str) -> tuple[dict[str, Any], str]:
    for run_token in run_tokens:
        result_path = _workflow_task_result_path(cache_root, run_token, task_id)
        payload = read_json(result_path) if result_path else None
        if isinstance(payload, dict):
            return payload, result_path
    return {}, ""


def _director_result_task_state(result_payload: dict[str, Any]) -> str:
    status_token = str(result_payload.get("status") or "").strip().lower()
    qa_verdict = str(result_payload.get("qa_verdict") or result_payload.get("qa_status") or "").strip().upper()
    exit_code_raw = result_payload.get("exit_code")
    exit_code: int | None = None
    try:
        exit_code = int(exit_code_raw) if exit_code_raw is not None else None
    except (TypeError, ValueError):
        exit_code = None

    if status_token in {"success", "completed", "passed", "succeeded"} or qa_verdict == "PASS":
        return "completed"
    if status_token in {"blocked", "dependency_blocked"}:
        return "blocked"
    if status_token in {"failed", "fail", "error", "errored"}:
        return "failed"
    if exit_code == 0 and status_token not in {"failed", "fail", "error", "blocked"}:
        return "completed"
    if exit_code is not None and exit_code != 0:
        return "failed"
    return ""


def _apply_workflow_task_result(item: dict[str, Any], result_payload: dict[str, Any], result_path: str) -> None:
    state = _director_result_task_state(result_payload)
    if not state:
        return

    current_state = canonicalize_workflow_task_state(item.get("status") or item.get("state"))
    if current_state == "completed" and state != "completed":
        return

    item["status"] = state
    item["state"] = state
    item["done"] = state == "completed"
    item["completed"] = state == "completed"

    summary = str(
        result_payload.get("result_summary")
        or result_payload.get("summary")
        or result_payload.get("qa_diagnostics")
        or ""
    ).strip()
    if summary:
        item["summary"] = summary
    if state in {"failed", "blocked"}:
        error = str(result_payload.get("error") or summary or "").strip()
        if error:
            item["error"] = error
    else:
        item.pop("error", None)

    changed_files = result_payload.get("changed_files")
    if isinstance(changed_files, list):
        item["changed_files"] = [str(value) for value in changed_files if str(value or "").strip()]

    metadata_raw = item.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    metadata["director_result_path"] = result_path
    for key in ("qa_verdict", "qa_diagnostics", "exit_code"):
        if key in result_payload:
            metadata[key] = result_payload[key]
    item["metadata"] = metadata


def _apply_workflow_task_results(
    tasks: list[dict[str, Any]],
    workflow_status: dict[str, Any] | None,
    *,
    cache_root: str,
) -> None:
    run_tokens = _workflow_result_run_tokens(workflow_status)
    if not tasks or not run_tokens:
        return
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task_id = _workflow_task_id(item)
        if not task_id:
            continue
        result_payload, result_path = _load_workflow_task_result(cache_root, run_tokens, task_id)
        if result_payload:
            _apply_workflow_task_result(item, result_payload, result_path)


def merge_workflow_tasks(
    workflow_status: dict[str, Any] | None,
    *,
    base_tasks: Any = None,
    workspace: str = "",
    cache_root: str = "",
) -> list[dict[str, Any]]:
    """Merge live workflow task states onto the persisted PM task contract."""

    def _contract_task_id(item: dict[str, Any]) -> str:
        return str(item.get("id") or "").strip()

    def _safe_non_negative_int(value: Any, default: int = 0) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return max(0, int(default))

    def _normalize_file_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        files: list[str] = []
        for item in value:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            files.append(token)
        return files

    task_items = base_tasks if isinstance(base_tasks, list) else []
    if not task_items and workspace and cache_root:
        task_items = load_workflow_base_tasks(workspace, cache_root)

    normalized_tasks: list[dict[str, Any]] = [dict(item) for item in task_items if isinstance(item, dict)]
    merged_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in normalized_tasks:
        task_id = _contract_task_id(item)
        if not task_id:
            continue
        normalized = dict(item)
        normalized["id"] = task_id
        merged_by_id[task_id] = normalized
        order.append(task_id)

    for task_id, raw in get_workflow_task_snapshot(workflow_status).items():
        if not isinstance(raw, dict):
            continue
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            continue
        existing = merged_by_id.get(normalized_task_id, {"id": normalized_task_id})
        raw_metadata = raw.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        canonical_state = canonicalize_workflow_task_state(raw.get("state"))
        merged: dict[str, Any] = dict(existing)
        merged["id"] = normalized_task_id
        merged["title"] = str(
            merged.get("title") or metadata.get("task_title") or metadata.get("title") or normalized_task_id
        ).strip()
        goal = str(merged.get("goal") or metadata.get("task_goal") or metadata.get("goal") or "").strip()
        if goal:
            merged["goal"] = goal
        summary = str(raw.get("summary") or metadata.get("summary") or "").strip()
        if summary:
            merged["summary"] = summary
        merged["status"] = canonical_state
        merged["state"] = canonical_state
        merged["done"] = canonical_state == "completed"
        merged["completed"] = canonical_state == "completed"
        if canonical_state in {"failed", "blocked"} and summary:
            merged["error"] = summary
        if metadata:
            merged_metadata_raw = merged.get("metadata")
            merged_metadata: dict[str, Any] = merged_metadata_raw if isinstance(merged_metadata_raw, dict) else {}
            merged_metadata.update(metadata)
            merged_metadata["workflow_state"] = str(raw.get("state") or "").strip().lower()
            merged["metadata"] = merged_metadata
            retry_count = merged_metadata.get("retry_count")
            if retry_count is None:
                retry_count = merged_metadata.get("retries")
            if retry_count is not None:
                normalized_retry_count = _safe_non_negative_int(retry_count, 0)
                merged["retry_count"] = normalized_retry_count
                merged["retries"] = normalized_retry_count
            changed_files = _normalize_file_list(merged_metadata.get("changed_files"))
            files_modified = merged_metadata.get("files_modified")
            normalized_files_modified = _safe_non_negative_int(
                files_modified,
                len(changed_files),
            )
            if changed_files:
                merged["changed_files"] = changed_files
            if normalized_files_modified > 0 or changed_files:
                merged["files_modified"] = max(
                    normalized_files_modified,
                    len(changed_files),
                )
            current_file = str(
                merged_metadata.get("current_file")
                or merged_metadata.get("current_file_path")
                or (changed_files[-1] if changed_files else "")
            ).strip()
            if current_file:
                merged["current_file"] = current_file
            phase_token = str(merged_metadata.get("phase") or "").strip().lower()
            if phase_token:
                merged["phase"] = phase_token
        if normalized_task_id not in merged_by_id:
            order.append(normalized_task_id)
        merged_by_id[normalized_task_id] = merged

    if not merged_by_id:
        return []
    merged_tasks = [merged_by_id[task_id] for task_id in order if task_id in merged_by_id]
    _apply_workflow_task_results(merged_tasks, workflow_status, cache_root=cache_root)
    return merged_tasks


def summarize_workflow_tasks(
    workflow_status: dict[str, Any] | None,
    *,
    base_tasks: Any = None,
    workspace: str = "",
    cache_root: str = "",
) -> dict[str, Any]:
    """Return aggregate task counts for the current workflow state."""
    tasks = merge_workflow_tasks(
        workflow_status,
        base_tasks=base_tasks,
        workspace=workspace,
        cache_root=cache_root,
    )
    states = [canonicalize_workflow_task_state(item.get("status") or item.get("state")) for item in tasks]
    completed = len([state for state in states if state == "completed"])
    failed = len([state for state in states if state in {"failed", "blocked"}])
    active = len([state for state in states if state in {"ready", "claimed", "in_progress"}])
    pending = len([state for state in states if state == "pending"])
    aggregate_state = "idle"
    if failed > 0:
        aggregate_state = "failed"
    elif tasks and completed == len(tasks):
        aggregate_state = "completed"
    elif active > 0:
        aggregate_state = "running"
    elif pending > 0:
        aggregate_state = "queued"
    return {
        "tasks": tasks,
        "total": len(tasks),
        "completed": completed,
        "failed": failed,
        "active": active,
        "pending": pending,
        "state": aggregate_state,
    }


def workflow_task_status(value: Any) -> str:
    """Map canonical workflow task state to Director API task status tokens."""
    state = canonicalize_workflow_task_state(value)
    if state == "completed":
        return "COMPLETED"
    if state == "failed":
        return "FAILED"
    if state == "blocked":
        return "BLOCKED"
    if state == "cancelled":
        return "CANCELLED"
    if state in {"in_progress", "claimed"}:
        return "RUNNING"
    if state == "ready":
        return "READY"
    return "PENDING"


def build_workflow_task_rows(
    workflow_status: dict[str, Any] | None,
    *,
    base_tasks: Any = None,
    workspace: str = "",
    cache_root: str = "",
) -> list[dict[str, Any]]:
    """Project merged workflow task state into the Director API/WebSocket task schema."""
    rows: list[dict[str, Any]] = []
    for item in merge_workflow_tasks(
        workflow_status,
        base_tasks=base_tasks,
        workspace=workspace,
        cache_root=cache_root,
    ):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or "").strip()
        if not task_id:
            continue
        title = str(item.get("title") or item.get("summary") or task_id).strip()
        description = str(item.get("goal") or item.get("summary") or item.get("description") or "").strip()
        item_metadata_raw = item.get("metadata")
        metadata: dict[str, Any] = item_metadata_raw if isinstance(item_metadata_raw, dict) else {}
        metadata.setdefault("pm_task_id", task_id)
        blueprint_id = str(item.get("blueprint_id") or item.get("blueprintId") or "").strip()
        blueprint_path = str(
            item.get("blueprint_path") or item.get("runtime_blueprint_path") or item.get("blueprintPath") or ""
        ).strip()
        runtime_blueprint_path = str(item.get("runtime_blueprint_path") or blueprint_path or "").strip()
        if blueprint_id:
            metadata.setdefault("blueprint_id", blueprint_id)
        if blueprint_path:
            metadata.setdefault("blueprint_path", blueprint_path)
        if runtime_blueprint_path:
            metadata.setdefault("runtime_blueprint_path", runtime_blueprint_path)
        metadata.setdefault(
            "workflow_state",
            canonicalize_workflow_task_state(item.get("status") or item.get("state")),
        )
        claimed_by = str(
            metadata.get("claimed_by") or metadata.get("worker_id") or metadata.get("assigned_worker") or ""
        ).strip()
        canonical_status = workflow_task_status(item.get("status") or item.get("state"))
        if not claimed_by and canonical_status in {"RUNNING"}:
            claimed_by = "workflow-worker"
        if claimed_by:
            metadata.setdefault("claimed_by", claimed_by)
            metadata.setdefault("worker_id", claimed_by)
            metadata.setdefault("assigned_worker", claimed_by)
        result = None
        error_text = str(item.get("error") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if error_text:
            result = {"error": error_text, "summary": summary or error_text}
        elif summary:
            result = {"summary": summary}
        rows.append(
            {
                "id": task_id,
                "subject": title,
                "description": description,
                "status": canonical_status,
                "priority": str(item.get("priority") or "MEDIUM").strip() or "MEDIUM",
                "claimed_by": claimed_by or None,
                "result": result,
                "metadata": metadata,
                "blueprint_id": blueprint_id or metadata.get("blueprint_id"),
                "blueprint_path": blueprint_path or metadata.get("blueprint_path"),
                "runtime_blueprint_path": runtime_blueprint_path or metadata.get("runtime_blueprint_path"),
            }
        )
    return rows


def build_workflow_status_payload(
    workflow_status: dict[str, Any] | None,
    *,
    workspace: str,
    base_tasks: Any = None,
    cache_root: str = "",
) -> dict[str, Any] | None:
    """Project merged workflow task state into the Director status schema."""
    stage = get_workflow_stage(workflow_status)
    summary = summarize_workflow_tasks(
        workflow_status,
        base_tasks=base_tasks,
        workspace=workspace,
        cache_root=cache_root,
    )
    if int(summary.get("total") or 0) <= 0:
        return None

    states = [
        canonicalize_workflow_task_state(item.get("status") or item.get("state"))
        for item in summary.get("tasks") or []
        if isinstance(item, dict)
    ]
    ready_queue_size = len([state for state in states if state in {"pending", "ready"}])
    busy = int(summary.get("active") or 0)
    completed = int(summary.get("completed") or 0)
    failed = int(summary.get("failed") or 0)
    aggregate_state = str(summary.get("state") or "").strip().lower()
    has_active_execution = busy > 0 or aggregate_state == "running"
    state = "IDLE"
    if has_active_execution:
        state = "RUNNING"
    elif aggregate_state == "completed":
        state = "COMPLETED"
    elif aggregate_state == "failed":
        state = "FAILED"
    elif ready_queue_size > 0 or aggregate_state == "queued":
        state = "PENDING"

    workflow_id = (
        str((workflow_status or {}).get("director_workflow_id") or "").strip()
        or str((workflow_status or {}).get("workflow_id") or "").strip()
    )
    return {
        "state": state,
        "stage": stage,
        "workspace": str(workspace or "").strip(),
        "metrics": {
            "tasks_completed": completed,
            "tasks_failed": failed,
            "workflow_id": workflow_id,
            "workflow_parent_workflow_id": str((workflow_status or {}).get("workflow_id") or "").strip(),
        },
        "tasks": {
            "total": int(summary.get("total") or 0),
            "by_status": {
                "COMPLETED": completed,
                "FAILED": failed,
                "IN_PROGRESS": busy,
            },
            "ready_queue_size": ready_queue_size,
            "task_rows": [
                {
                    "id": item.get("id"),
                    "subject": item.get("subject") or item.get("title") or "",
                    "description": item.get("description") or "",
                    "status": canonicalize_workflow_task_state(item.get("status") or item.get("state")),
                    "priority": item.get("priority", "MEDIUM"),
                    "claimed_by": item.get("claimed_by"),
                    "metadata": item.get("metadata", {}),
                }
                for item in (summary.get("tasks") or [])
                if isinstance(item, dict)
            ],
        },
        "workers": {
            "total": max(1, busy) if int(summary.get("total") or 0) > 0 else 0,
            "available": 0 if busy else (1 if int(summary.get("total") or 0) > 0 else 0),
            "busy": busy,
            "worker_rows": [],
        },
        "token_budget": {},
    }


def should_prefer_workflow_status(
    local_status: dict[str, Any] | None,
    workflow_status: dict[str, Any] | None,
    workflow_tasks: list[dict[str, Any]] | None = None,
) -> bool:
    """Return whether workflow-backed Director state should be treated as authoritative."""
    if not isinstance(workflow_status, dict):
        return False

    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _state_token(payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        direct = str(payload.get("state") or "").strip().upper()
        if direct:
            return direct
        nested = payload.get("status")
        if isinstance(nested, dict):
            return str(nested.get("state") or "").strip().upper()
        return ""

    def _task_totals(payload: dict[str, Any] | None) -> tuple[int, int]:
        if not isinstance(payload, dict):
            return (0, 0)
        tasks = payload.get("tasks")
        if not isinstance(tasks, dict):
            return (0, 0)
        total = _safe_int(tasks.get("total"))
        by_status = tasks.get("by_status")
        if not isinstance(by_status, dict):
            return (total, 0)
        active = (
            _safe_int(by_status.get("IN_PROGRESS"))
            + _safe_int(by_status.get("RUNNING"))
            + _safe_int(by_status.get("CLAIMED"))
        )
        return (total, active)

    def _workflow_is_running(payload: dict[str, Any]) -> bool:
        parent_status = str(payload.get("workflow_status") or "").strip().lower()
        child_status = str(payload.get("director_workflow_status") or "").strip().lower()
        return bool(payload.get("running")) or parent_status == "running" or child_status == "running"

    workflow_task_total = 0
    tasks_payload = workflow_status.get("tasks")
    if isinstance(tasks_payload, dict):
        workflow_task_total = _safe_int(tasks_payload.get("total"))
    if workflow_task_total <= 0 and isinstance(workflow_tasks, list):
        workflow_task_total = len([item for item in workflow_tasks if isinstance(item, dict)])
    if workflow_task_total <= 0:
        return False

    local_payload = local_status if isinstance(local_status, dict) else {}
    local_state = _state_token(local_payload)
    local_running = bool(local_payload.get("running")) or local_state == "RUNNING"
    local_total, local_active = _task_totals(local_payload)
    workflow_running = _workflow_is_running(workflow_status)

    if local_running and (local_total > 0 or local_active > 0):
        if not workflow_running:
            return False
        status_tokens = []
        if isinstance(workflow_tasks, list):
            for item in workflow_tasks:
                if not isinstance(item, dict):
                    continue
                status_tokens.append(str(item.get("status") or item.get("state") or "").strip().upper())
        workflow_has_live_rows = any(
            token in {"RUNNING", "IN_PROGRESS", "CLAIMED", "COMPLETED", "FAILED", "BLOCKED"} for token in status_tokens
        )
        if not workflow_has_live_rows:
            return False

    return True


def get_workflow_runtime_status(
    workspace: str,
    cache_root: str,
) -> dict[str, Any] | None:
    """Return best-effort status for the latest submitted workflow.

    This function must remain read-only: it computes a fresher in-memory record
    but does not persist it to disk.
    """
    # Lazy import to break circular dependency:
    # workflow_status → orchestration/workflow_runtime/public/service
    #   → unified_orchestration_service → workspace/integrity → runtime/projection
    #   → workflow_status (cycle)
    from polaris.cells.orchestration.workflow_runtime.public.service import (
        WorkflowConfig,
        director_workflow_id,
        qa_workflow_id,
    )

    record = load_workflow_state(workspace, cache_root)
    workflow_id = str(record.get("workflow_id") or "").strip()
    if not workflow_id:
        return None

    with _workflow_runtime_environment(workspace, cache_root):
        config = WorkflowConfig.from_env(force_enable=True)
        description = describe_workflow_sync(workflow_id, config)
        described_status = str(description.get("status") or "").strip().lower()
        runtime_status = described_status or str(record.get("workflow_status") or "").strip().lower()

        snapshot = query_workflow_sync(
            workflow_id,
            "get_runtime_snapshot",
            config=config,
        )
        snapshot_raw = snapshot.get("payload")
        runtime_snapshot: dict[str, Any] = (
            snapshot_raw if isinstance(snapshot_raw, dict) else _cached_snapshot(record, "runtime_snapshot")
        )
        stage = str(runtime_snapshot.get("stage") or record.get("stage") or "").strip().lower()

        workflow_chain_run_id = _resolve_workflow_chain_run_id(record, runtime_snapshot)
        child_workflow_id = director_workflow_id(workflow_chain_run_id) if workflow_chain_run_id else ""
        qa_child_workflow_id = qa_workflow_id(workflow_chain_run_id) if workflow_chain_run_id else ""
        child_description: dict[str, Any] = {}
        child_snapshot_payload: dict[str, Any] = {}
        child_status = ""
        qa_child_description: dict[str, Any] = {}
        qa_child_snapshot_payload: dict[str, Any] = {}
        qa_child_status = ""
        if child_workflow_id:
            child_description = describe_workflow_sync(child_workflow_id, config)
            child_status = str(child_description.get("status") or "").strip().lower()
            if bool(child_description.get("ok")):
                child_snapshot = query_workflow_sync(
                    child_workflow_id,
                    "get_runtime_snapshot",
                    config=config,
                )
                child_payload_raw = child_snapshot.get("payload")
                child_snapshot_payload = (
                    child_payload_raw
                    if isinstance(child_payload_raw, dict)
                    else _cached_snapshot(record, "director_runtime_snapshot")
                )
            else:
                child_snapshot_payload = _cached_snapshot(record, "director_runtime_snapshot")
        if qa_child_workflow_id:
            qa_child_description = describe_workflow_sync(qa_child_workflow_id, config)
            qa_child_status = str(qa_child_description.get("status") or "").strip().lower()
            if bool(qa_child_description.get("ok")):
                qa_child_snapshot = query_workflow_sync(
                    qa_child_workflow_id,
                    "get_runtime_snapshot",
                    config=config,
                )
                qa_payload_raw = qa_child_snapshot.get("payload")
                qa_child_snapshot_payload = (
                    qa_payload_raw
                    if isinstance(qa_payload_raw, dict)
                    else _cached_snapshot(record, "qa_runtime_snapshot")
                )
            else:
                qa_child_snapshot_payload = _cached_snapshot(record, "qa_runtime_snapshot")

    stage = (
        str(
            qa_child_snapshot_payload.get("stage")
            or child_snapshot_payload.get("stage")
            or runtime_snapshot.get("stage")
            or stage
            or record.get("stage")
            or ""
        )
        .strip()
        .lower()
    )

    derived_failure_status = _derive_terminal_failure_status(
        cache_root=cache_root,
        statuses=(child_status, qa_child_status),
        payloads=(
            record,
            description,
            runtime_snapshot,
            child_description,
            child_snapshot_payload,
            qa_child_description,
            qa_child_snapshot_payload,
        ),
    )
    if derived_failure_status:
        runtime_status = derived_failure_status
    else:
        derived_success_status = _derive_terminal_success_status(cache_root=cache_root)
        if derived_success_status:
            runtime_status = derived_success_status

    running = not _is_terminal_status(runtime_status or "running")
    updated_record = dict(record)
    updated_record.update(
        {
            "workflow_status": runtime_status,
            "stage": stage,
            "workflow_run_id": str(description.get("run_id") or record.get("workflow_run_id") or "").strip(),
            "workflow_chain_run_id": workflow_chain_run_id,
            "director_workflow_id": child_workflow_id,
            "director_workflow_status": child_status,
            "qa_workflow_id": qa_child_workflow_id,
            "qa_workflow_status": qa_child_status,
            "runtime_snapshot": runtime_snapshot,
            "director_runtime_snapshot": child_snapshot_payload,
            "qa_runtime_snapshot": qa_child_snapshot_payload,
        }
    )
    state_sync_required = updated_record != record
    updated_record["state_path"] = workflow_state_path(workspace, cache_root)
    return {
        "source": "workflow",
        "running": running,
        "workflow_id": workflow_id,
        "workflow_run_id": str(description.get("run_id") or record.get("workflow_run_id") or "").strip(),
        "workflow_chain_run_id": workflow_chain_run_id,
        "workflow_status": runtime_status,
        "stage": stage,
        "runtime_snapshot": runtime_snapshot,
        "director_workflow_id": child_workflow_id,
        "director_workflow_status": child_status,
        "director_runtime_snapshot": child_snapshot_payload,
        "qa_workflow_id": qa_child_workflow_id,
        "qa_workflow_status": qa_child_status,
        "qa_runtime_snapshot": qa_child_snapshot_payload,
        "record": updated_record,
        "state_sync_required": state_sync_required,
        "error": "" if bool(description.get("ok")) else str(description.get("error") or "").strip(),
        "state_path": str(updated_record.get("state_path") or "").strip(),
    }


# Aliases for backward compatibility with existing code
build_workflow_director_status_payload = build_workflow_status_payload
build_workflow_director_task_rows = build_workflow_task_rows
