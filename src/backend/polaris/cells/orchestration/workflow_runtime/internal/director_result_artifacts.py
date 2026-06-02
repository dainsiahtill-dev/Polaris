"""Director result artifact reconciliation for orchestration runs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.fs.text_ops import write_json_atomic

_TERMINAL_SUCCESS = {"completed", "done", "success", "passed", "succeeded"}
_TERMINAL_FAILURE = {"failed", "failure", "error"}
_TERMINAL_BLOCKED = {"blocked", "cancelled", "canceled", "timeout", "timed_out"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _safe_run_id(value: str) -> str:
    token = str(value or "").strip()
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", token).strip("._")
    return token or "unknown-run"


def _normalize_task_rows(payload: Any) -> list[dict[str, Any]]:
    raw_tasks = payload.get("tasks") if isinstance(payload, dict) else payload
    rows = [dict(item) for item in _as_list(raw_tasks) if isinstance(item, dict)]
    director_rows = [
        row
        for row in rows
        if str(row.get("assigned_to") or row.get("owner") or "").strip().lower() in {"director", "工部侍郎"}
    ]
    return director_rows or rows


def _read_pm_contract_rows(workspace: str) -> list[dict[str, Any]]:
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []
    try:
        contract_path = Path(resolve_artifact_path(workspace_token, "", "runtime/contracts/pm_tasks.contract.json"))
    except (OSError, RuntimeError, ValueError):
        return []
    if not contract_path.is_file():
        return []
    try:
        with contract_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []
    return _normalize_task_rows(payload)


def _identity_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    metadata = _as_dict(row.get("metadata"))
    runtime_execution = _as_dict(metadata.get("runtime_execution"))
    for source in (row, metadata, runtime_execution):
        for key in (
            "id",
            "task_id",
            "pm_task_id",
            "source_task_id",
            "external_task_id",
            "workflow_run_id",
        ):
            token = str(source.get(key) or "").strip()
            if token:
                tokens.add(token)
    return tokens


def _runtime_identity_tokens(row: dict[str, Any]) -> set[str]:
    """Return stable identity tokens for matching runtime rows to PM contracts."""

    metadata = _as_dict(row.get("metadata"))
    runtime_execution = _as_dict(metadata.get("runtime_execution"))
    canonical_tokens = {
        str(value or "").strip()
        for value in (
            metadata.get("source_task_id"),
            metadata.get("pm_task_id"),
        )
        if str(value or "").strip()
    }
    volatile_tokens = {
        str(value or "").strip()
        for value in (
            metadata.get("external_task_id"),
            runtime_execution.get("external_task_id"),
        )
        if str(value or "").strip()
    }
    tokens = _identity_tokens(row)
    if canonical_tokens and volatile_tokens.difference(canonical_tokens):
        tokens.difference_update(volatile_tokens)
        tokens.update(canonical_tokens)
    return tokens


def _dependency_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    metadata = _as_dict(row.get("metadata"))
    for source in (row, metadata):
        for key in ("depends_on", "dependencies", "blocked_by", "blockedBy"):
            for item in _as_list(source.get(key)):
                token = str(item or "").strip()
                if token:
                    tokens.add(token)
    return tokens


def _row_status(row: dict[str, Any] | None) -> str:
    if row is None:
        return "pending"
    status = str(row.get("status") or "").strip().lower()
    metadata = _as_dict(row.get("metadata"))
    runtime_execution = _as_dict(metadata.get("runtime_execution"))
    effective = str(runtime_execution.get("effective_status") or "").strip().lower()
    return effective or status or "pending"


def _is_blocking_status(status: str) -> bool:
    return status in _TERMINAL_FAILURE or status in _TERMINAL_BLOCKED


def _blocking_identity_tokens(
    contract_rows: list[dict[str, Any]],
    statuses: list[str],
) -> set[str]:
    tokens: set[str] = set()
    for index, status in enumerate(statuses):
        if index < len(contract_rows) and _is_blocking_status(status):
            tokens.update(_identity_tokens(contract_rows[index]))
    return tokens


def _propagate_dependency_blocks(
    contract_rows: list[dict[str, Any]],
    statuses: list[str],
) -> tuple[list[str], list[list[str]]]:
    """Mark pending contract tasks blocked when a dependency has failed or is blocked."""

    resolved_statuses = list(statuses)
    blocked_by: list[list[str]] = [[] for _ in contract_rows]
    changed = True
    while changed:
        changed = False
        blocking_tokens = _blocking_identity_tokens(contract_rows, resolved_statuses)
        for index, contract in enumerate(contract_rows):
            status = resolved_statuses[index]
            if status in _TERMINAL_SUCCESS or _is_blocking_status(status):
                continue
            dependencies = _dependency_tokens(contract)
            blocked_dependencies = sorted(dependencies.intersection(blocking_tokens))
            if not blocked_dependencies:
                continue
            resolved_statuses[index] = "blocked"
            blocked_by[index] = blocked_dependencies
            changed = True

    blocking_tokens = _blocking_identity_tokens(contract_rows, resolved_statuses)
    for index, contract in enumerate(contract_rows):
        if resolved_statuses[index] == "blocked" and not blocked_by[index]:
            blocked_by[index] = sorted(_dependency_tokens(contract).intersection(blocking_tokens))
    return resolved_statuses, blocked_by


def _task_result_payload(
    contract: dict[str, Any],
    row: dict[str, Any] | None,
    *,
    status_override: str | None = None,
    blocked_by: list[str] | None = None,
) -> dict[str, Any]:
    metadata = _as_dict(row.get("metadata") if row else {})
    adapter_result = _as_dict(metadata.get("adapter_result"))
    runtime_execution = _as_dict(metadata.get("runtime_execution"))
    changed_files = [
        *[str(item) for item in _as_list(adapter_result.get("new_files")) if str(item).strip()],
        *[str(item) for item in _as_list(adapter_result.get("modified_files")) if str(item).strip()],
    ]
    status = str(status_override or _row_status(row)).strip().lower() or "pending"
    blocked_dependencies = [str(item).strip() for item in (blocked_by or []) if str(item).strip()]
    summary = str(
        runtime_execution.get("last_result_summary")
        or metadata.get("last_execution_summary")
        or (row or {}).get("result_summary")
        or ""
    ).strip()
    if not summary and status == "blocked" and blocked_dependencies:
        summary = f"Blocked by failed dependency: {', '.join(blocked_dependencies)}"
    result: dict[str, Any] = {
        "task_id": str(
            contract.get("id") or contract.get("task_id") or contract.get("pm_task_id") or (row or {}).get("id") or ""
        ).strip(),
        "status": status,
        "title": str(contract.get("title") or contract.get("subject") or (row or {}).get("subject") or "").strip(),
        "summary": summary,
        "changed_files": changed_files,
        "tools_executed": _safe_int(adapter_result.get("tools_executed")),
    }
    if blocked_dependencies:
        result["blocked_by"] = blocked_dependencies
    if adapter_result:
        result["adapter_result"] = adapter_result
    error_text = str(
        runtime_execution.get("last_error")
        or metadata.get("last_execution_error")
        or (row or {}).get("error_message")
        or ""
    ).strip()
    if not error_text and status == "blocked" and blocked_dependencies:
        error_text = "blocked_by_failed_dependency"
    if error_text:
        result["error"] = error_text
    return result


def _row_matches_run_id(row: dict[str, Any], run_id: str) -> bool:
    expected = str(run_id or "").strip()
    if not expected:
        return False
    metadata = _as_dict(row.get("metadata"))
    runtime_execution = _as_dict(metadata.get("runtime_execution"))
    for source in (row, metadata, runtime_execution):
        token = str(source.get("workflow_run_id") or source.get("run_id") or "").strip()
        if token == expected:
            return True
    return False


def _index_runtime_rows(rows: list[dict[str, Any]], *, run_id: str = "") -> dict[str, dict[str, Any]]:
    by_token: dict[str, dict[str, Any]] = {}
    ordered_rows = sorted(rows, key=lambda row: 0 if _row_matches_run_id(row, run_id) else 1)
    for row in ordered_rows:
        for token in _runtime_identity_tokens(row):
            by_token.setdefault(token, row)
    return by_token


def build_director_result_from_runtime(
    *,
    workspace: str,
    run_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Build a global Director result from PM contract rows and TaskRuntime rows.

    Returns ``(payload, terminal)``. ``payload`` is ``None`` when there is not
    enough task evidence to build a meaningful result.
    """

    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return None, False

    runtime_rows = TaskRuntimeService(workspace_token).list_task_rows()
    runtime_rows = [dict(row) for row in runtime_rows if isinstance(row, dict)]
    contract_rows = _read_pm_contract_rows(workspace_token)
    rows_by_token = _index_runtime_rows(runtime_rows, run_id=run_id)
    if not contract_rows:
        contract_rows = [
            row for row in runtime_rows if str(_as_dict(row.get("metadata")).get("pm_task_id") or "").strip()
        ] or runtime_rows
    if not contract_rows:
        return None, False

    matched_rows: list[dict[str, Any] | None] = []
    for contract in contract_rows:
        matched_row = None
        for token in _identity_tokens(contract):
            matched_row = rows_by_token.get(token)
            if matched_row is not None:
                break
        matched_rows.append(matched_row)

    initial_statuses = [_row_status(row) for row in matched_rows]
    statuses, blocked_by = _propagate_dependency_blocks(contract_rows, initial_statuses)

    task_results: list[dict[str, Any]] = []
    successes = failures = blocked = pending = 0
    for contract, matched_row, status, blocked_dependencies in zip(
        contract_rows,
        matched_rows,
        statuses,
        blocked_by,
        strict=True,
    ):
        if status in _TERMINAL_SUCCESS:
            successes += 1
        elif status in _TERMINAL_FAILURE:
            failures += 1
        elif status in _TERMINAL_BLOCKED:
            blocked += 1
        else:
            pending += 1
        task_results.append(
            _task_result_payload(
                contract,
                matched_row,
                status_override=status,
                blocked_by=blocked_dependencies,
            )
        )

    total = len(task_results)
    terminal = total > 0 and pending == 0
    if not terminal:
        return None, False

    status = "success" if failures == 0 and blocked == 0 and successes >= total else "failed"
    error = "" if status == "success" else "director_failed"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "run_id": str(run_id or "").strip(),
        "mode": "orchestration_task_runtime",
        "successes": successes,
        "failures": failures,
        "blocked": blocked,
        "pending": pending,
        "total": total,
        "summary": (
            f"Director completed {successes}/{total} tasks"
            if status == "success"
            else f"Director completed with failures={failures}, blocked={blocked}, successes={successes}/{total}"
        ),
        "error": error,
        "source": "v2_director_run_reconciliation",
        "task_results": task_results,
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    return payload, True


def persist_director_result_from_runtime(
    *,
    workspace: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Persist canonical Director result artifacts when all Director tasks are terminal."""

    payload, terminal = build_director_result_from_runtime(workspace=workspace, run_id=run_id)
    if not terminal or payload is None:
        return None
    workspace_token = str(workspace or "").strip()
    safe_run_id = _safe_run_id(run_id)
    latest_result_path = resolve_artifact_path(workspace_token, "", "runtime/results/director.result.json")
    run_result_path = resolve_artifact_path(
        workspace_token,
        "",
        f"runtime/runs/{safe_run_id}/results/director.result.json",
    )
    payload["runtime_result_path"] = latest_result_path
    payload["run_result_path"] = run_result_path
    write_json_atomic(run_result_path, payload)
    write_json_atomic(latest_result_path, payload)
    return payload


def build_integration_qa_tasks_from_director_result(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Project a Director result artifact into the task schema expected by integration QA."""

    result = _as_dict(payload)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(result.get("task_results")), start=1):
        if not isinstance(item, dict):
            continue
        changed_files = [
            str(file_path).strip() for file_path in _as_list(item.get("changed_files")) if str(file_path).strip()
        ]
        task_id = str(item.get("task_id") or f"director-result-{index}").strip()
        rows.append(
            {
                "id": task_id or f"director-result-{index}",
                "assigned_to": "director",
                "status": str(item.get("status") or "").strip() or "completed",
                "type": "code",
                "title": str(item.get("title") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "target_files": changed_files,
                "scope_paths": changed_files,
                "metadata": {
                    "source": "director_result_artifact",
                    "tools_executed": _safe_int(item.get("tools_executed")),
                },
            }
        )
    return rows


__all__ = [
    "build_director_result_from_runtime",
    "build_integration_qa_tasks_from_director_result",
    "persist_director_result_from_runtime",
]
