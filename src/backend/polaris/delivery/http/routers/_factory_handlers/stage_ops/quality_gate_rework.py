# ruff: noqa: E402, F403, F405
"""Factory stage-ops helpers — quality-gate task-boundary rework routing.

Extracted verbatim from the former single-file ``stage_ops`` module during the
lossless decomposition of that god-module. These helpers read the QA
workspace-validation artifact, classify failed task rows, and route
task-boundary / owner-handoff rework requests through TaskRuntime.

``factory.py``'s ``_rebind_helper_module`` rebinds these callables into the host
router namespace; the package ``__init__`` rewrites ``__module__`` so the rebind
treats them as package-owned. Cross-module free names are injected by
``_wire_cross_module_namespace``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from polaris.cells.factory.pipeline.public import FactoryRun
from polaris.cells.runtime.task_runtime.public.evidence import task_row_execution_event_failure
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.quality import (
    ScopeAuthorityOwnerHandoffIndex,
    ScopeAuthorityOwnerHandoffRouting,
    owner_handoff_index_summary,
    ownership_handoff_requests_from_scope_payload,
    resolve_owner_handoff_routing,
    task_record_routing_key,
)

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from ..mapping import *
from ._common import (
    _read_json_artifact,
    _resolve_quality_rework_max_cycles,
    _resolve_task_identifier,
)


def _workspace_validation_requests_task_boundary_rework(payload: dict[str, Any]) -> bool:
    repair_raw = payload.get("repair")
    repair: dict[str, Any] = repair_raw if isinstance(repair_raw, dict) else {}
    if bool(repair.get("task_boundary_triage_required")):
        return True
    if str(repair.get("success_reason") or "").strip() == _TASK_BOUNDARY_REWORK_REASON:
        return True
    if _ownership_handoff_requests_from_repair_payload(repair):
        return True
    warnings = payload.get("warnings")
    return isinstance(warnings, list) and _TASK_BOUNDARY_REWORK_REASON in {str(item).strip() for item in warnings}


def _read_task_boundary_workspace_validation(workspace: str) -> tuple[dict[str, Any], str]:
    for relative_path in (
        "workspace/qa/latest.workspace-validation.json",
        "runtime/qa/workspace-validation.json",
    ):
        payload = _read_json_artifact(workspace, relative_path)
        if payload and _workspace_validation_requests_task_boundary_rework(payload):
            return payload, relative_path
    return {}, ""


def _task_record_needs_task_boundary_rework(record: dict[str, Any]) -> bool:
    status = str(record.get("status") or "").strip().lower()
    if status not in {"failed", "error"}:
        return False

    metadata_raw = record.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    adapter_result_raw = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = adapter_result_raw if isinstance(adapter_result_raw, dict) else {}
    quality_repair_raw = adapter_result.get("quality_repair") or metadata.get("quality_repair")
    quality_repair: dict[str, Any] = quality_repair_raw if isinstance(quality_repair_raw, dict) else {}
    interface_evidence_raw = (
        adapter_result.get("interface_discrepancy_evidence")
        or quality_repair.get("interface_discrepancy_evidence")
        or metadata.get("interface_discrepancy_evidence")
    )
    interface_evidence: dict[str, Any] = interface_evidence_raw if isinstance(interface_evidence_raw, dict) else {}
    plan_probe_raw = quality_repair.get("plan_probe_preaudit") or adapter_result.get("plan_probe_preaudit")
    plan_probe: dict[str, Any] = plan_probe_raw if isinstance(plan_probe_raw, dict) else {}

    markers = {
        str(metadata.get("last_execution_error") or "").strip(),
        str(adapter_result.get("success_reason") or "").strip(),
        str(quality_repair.get("success_reason") or "").strip(),
        str(quality_repair.get("stage") or "").strip(),
        str(interface_evidence.get("reason") or "").strip(),
        str(interface_evidence.get("plan_probe_status") or "").strip(),
        str(plan_probe.get("status") or "").strip(),
    }
    return bool(
        {
            "director_materialization_quality_failed",
            "runtime_plan_probe_unplannable",
            _TASK_BOUNDARY_REWORK_REASON,
            _PLAN_PROBE_UNPLANNABLE_STATUS,
        }
        & markers
    )


def _ownership_handoff_requests_from_repair_payload(repair: dict[str, Any]) -> list[dict[str, Any]]:
    return list(ownership_handoff_requests_from_scope_payload(repair))


def _quality_gate_owner_handoff_index(
    repair: dict[str, Any],
    entries: list[Any],
) -> ScopeAuthorityOwnerHandoffIndex:
    return _quality_gate_owner_handoff_routing(repair, entries).index


def _quality_gate_owner_handoff_routing(
    repair: dict[str, Any],
    entries: list[Any],
) -> ScopeAuthorityOwnerHandoffRouting:
    records: list[dict[str, Any]] = []
    for entry in entries:
        record = entry.to_dict() if hasattr(entry, "to_dict") else entry
        if isinstance(record, dict):
            records.append(record)
    return resolve_owner_handoff_routing(repair, records)


def _safe_rework_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (RuntimeError, TypeError, ValueError):
        return int(default)


def _task_boundary_rework_evidence(payload: dict[str, Any], *, artifact: str) -> dict[str, Any]:
    repair_raw = payload.get("repair")
    repair: dict[str, Any] = repair_raw if isinstance(repair_raw, dict) else {}
    warnings_raw = payload.get("warnings")
    warnings = [str(item).strip() for item in warnings_raw] if isinstance(warnings_raw, list) else []
    evidence: dict[str, Any] = {
        "artifact": artifact,
        "reason": _TASK_BOUNDARY_REWORK_REASON,
        "warnings": [item for item in warnings if item],
    }
    for key in (
        "success_reason",
        "plan_probe_preaudit",
        "interface_discrepancy_evidence",
        "task_boundary_scope_filter",
        "residual_error_count",
        "residual_errors",
    ):
        value = repair.get(key)
        if value not in (None, "", [], {}):
            evidence[key] = value
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        evidence["errors"] = errors[:20]
    return evidence


def _record_factory_task_runtime_transition_failure(
    summary: dict[str, Any],
    *,
    task_id: int,
    action: str,
    reason: str,
    transition_result: dict[str, Any] | None = None,
) -> None:
    """Record a failed TaskRuntime transition before Factory advances rework state."""

    failures_raw = summary.get("task_runtime_transition_failures")
    failures: list[dict[str, Any]]
    if isinstance(failures_raw, list):
        failures = failures_raw
    else:
        failures = []
        summary["task_runtime_transition_failures"] = failures

    failures.append(
        {
            "success": False,
            "task_id": int(task_id),
            "action": str(action or "").strip(),
            "reason": str(reason or "task_runtime_transition_failed").strip() or "task_runtime_transition_failed",
            "transition_result": dict(transition_result or {}),
        }
    )


def _apply_quality_gate_task_boundary_rework_requests(workspace: str) -> dict[str, Any]:
    payload, artifact = _read_task_boundary_workspace_validation(workspace)
    summary: dict[str, Any] = {
        "requested": False,
        "evaluated_count": 0,
        "reopened_count": 0,
        "exhausted_count": 0,
        "skipped_count": 0,
        "task_runtime_transition_failures": [],
        **owner_handoff_index_summary(),
        "tasks": [],
        "reason": _TASK_BOUNDARY_REWORK_REASON,
        "artifact": artifact,
    }
    if not payload:
        return summary

    try:
        task_runtime = TaskRuntimeService(str(workspace))
        entries = task_runtime.list_observable_task_rows()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    max_retries = _resolve_quality_rework_max_cycles()
    now_iso = datetime.now(timezone.utc).isoformat()
    evidence = _task_boundary_rework_evidence(payload, artifact=artifact)
    repair_raw = payload.get("repair")
    repair: dict[str, Any] = repair_raw if isinstance(repair_raw, dict) else {}
    owner_handoff_index = _quality_gate_owner_handoff_index(repair, entries)
    for entry in entries:
        record = entry.to_dict() if hasattr(entry, "to_dict") else entry
        if not isinstance(record, dict):
            continue
        metadata_raw = record.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        completion_action_raw = metadata.get("factory_local_rework")
        completion_action = completion_action_raw if isinstance(completion_action_raw, Mapping) else {}
        completion_action_id = str(completion_action.get("action_id") or "").strip()
        if re.fullmatch(r"[0-9a-f]{64}", completion_action_id) is not None:
            summary["evaluated_count"] += 1
            summary["reopened_count"] += 1
            summary["requested"] = True
            summary["tasks"].append(
                {
                    "task_id": str(record.get("id") or record.get("task_id") or "").strip(),
                    "external_task_id": _resolve_task_identifier(metadata, record),
                    "retry_count": int(completion_action.get("rework_attempt") or 1),
                    "max_retries": _resolve_quality_rework_max_cycles(),
                    "exhausted": False,
                    "reason": "project_completion_owner_rework_authorized",
                    "project_completion_action_id": completion_action_id,
                    "transition_owner": "runtime.task_runtime",
                }
            )
            continue
        task_key = task_record_routing_key(record)
        owner_handoff_request = owner_handoff_index.matched_owner_handoff_by_task_key.get(task_key, {})
        if owner_handoff_index.all_handoff_requests:
            if not owner_handoff_request:
                continue
            rework_reason = _TASK_BOUNDARY_OWNER_REWORK_REASON
            task_evidence = {
                **evidence,
                "reason": rework_reason,
                "ownership_handoff_request": owner_handoff_request,
            }
        elif _task_record_needs_task_boundary_rework(record):
            rework_reason = _TASK_BOUNDARY_REWORK_REASON
            task_evidence = evidence
        else:
            continue

        task_id = _safe_rework_int(record.get("id") or record.get("task_id"), default=0)
        if task_id <= 0:
            summary["skipped_count"] += 1
            continue

        adapter_result_raw = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = adapter_result_raw if isinstance(adapter_result_raw, dict) else {}
        retry_count = _safe_rework_int(
            metadata.get("qa_rework_retry_count", adapter_result.get("qa_rework_retry_count")),
            default=0,
        )
        next_retry_count = retry_count + 1
        exhausted = next_retry_count >= max_retries

        merged_adapter_result: dict[str, Any] = dict(adapter_result)
        merged_adapter_result.update(
            {
                "task_boundary_rework_requested": not exhausted,
                "task_boundary_rework_reason": rework_reason,
                "qa_rework_retry_count": next_retry_count,
                "qa_rework_max_retries": max_retries,
                "qa_rework_reason": rework_reason,
                "qa_rework_exhausted": exhausted,
                "qa_rework_evidence": task_evidence,
            }
        )
        metadata_update = {
            "adapter_result": merged_adapter_result,
            "task_boundary_rework_requested": not exhausted,
            "task_boundary_rework_reason": rework_reason,
            "task_boundary_rework_evidence": task_evidence,
            "qa_rework_requested": not exhausted,
            "qa_rework_exhausted": exhausted,
            "qa_rework_retry_count": next_retry_count,
            "qa_rework_max_retries": max_retries,
            "qa_rework_reason": rework_reason,
            "qa_rework_evidence": task_evidence,
            "qa_last_reviewed_at": now_iso,
            "qa_last_verdict": "FAIL",
        }
        summary["evaluated_count"] += 1
        task_summary = {
            "task_id": str(task_id),
            "external_task_id": _resolve_task_identifier(metadata, record),
            "retry_count": next_retry_count,
            "max_retries": max_retries,
            "exhausted": exhausted,
            "reason": rework_reason,
        }
        if owner_handoff_request:
            task_summary["ownership_handoff_request"] = dict(owner_handoff_request)
            task_summary["ownership_handoff_target_file"] = str(owner_handoff_request.get("target_file") or "").strip()
        try:
            if exhausted:
                transition_result = task_runtime.update_task_row(task_id, metadata=metadata_update)
                if transition_result is None:
                    _record_factory_task_runtime_transition_failure(
                        summary,
                        task_id=task_id,
                        action="mark_rework_exhausted",
                        reason="task_runtime_update_missing_row",
                    )
                    summary["skipped_count"] += 1
                    continue
                execution_failure = task_row_execution_event_failure(transition_result)
                if execution_failure is not None:
                    _record_factory_task_runtime_transition_failure(
                        summary,
                        task_id=task_id,
                        action="mark_rework_exhausted",
                        reason="task_runtime_execution_event_append_failed",
                        transition_result=execution_failure,
                    )
                    summary["skipped_count"] += 1
                    continue
                summary["exhausted_count"] += 1
            else:
                transition_result = task_runtime.reopen_task_row(
                    task_id,
                    reason=rework_reason,
                    metadata=metadata_update,
                )
                if transition_result is None:
                    _record_factory_task_runtime_transition_failure(
                        summary,
                        task_id=task_id,
                        action="reopen_for_rework",
                        reason="task_runtime_reopen_missing_row",
                    )
                    summary["skipped_count"] += 1
                    continue
                execution_failure = task_row_execution_event_failure(transition_result)
                if execution_failure is not None:
                    _record_factory_task_runtime_transition_failure(
                        summary,
                        task_id=task_id,
                        action="reopen_for_rework",
                        reason="task_runtime_execution_event_append_failed",
                        transition_result=execution_failure,
                    )
                    summary["skipped_count"] += 1
                    continue
                summary["reopened_count"] += 1
                summary["requested"] = True
            summary["tasks"].append(task_summary)
        except (RuntimeError, ValueError) as exc:
            _record_factory_task_runtime_transition_failure(
                summary,
                task_id=task_id,
                action="mark_rework_exhausted" if exhausted else "reopen_for_rework",
                reason="task_runtime_transition_exception",
                transition_result={
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            summary["skipped_count"] += 1

    owner_handoff_summary = owner_handoff_index_summary(owner_handoff_index)
    summary.update(owner_handoff_summary)
    summary["skipped_count"] += int(owner_handoff_summary["unmatched_owner_handoff_count"]) + int(
        owner_handoff_summary["unknown_owner_handoff_count"]
    )

    return summary


def _read_pm_plan_signature(workspace: str) -> str:
    plan_payload = _read_json_artifact(workspace, "tasks/plan.json")
    tasks_payload = plan_payload.get("tasks")
    if not isinstance(tasks_payload, list) or not tasks_payload:
        return ""
    canonical = json.dumps(plan_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _quality_gate_handoff_summary_from_payload(
    payload: dict[str, Any],
    entries: list[Any],
) -> dict[str, Any]:
    repair_raw = payload.get("repair")
    repair: dict[str, Any] = repair_raw if isinstance(repair_raw, dict) else {}
    owner_handoff_routing = _quality_gate_owner_handoff_routing(repair, entries)
    return owner_handoff_routing.summary


def _read_quality_gate_rework_summary(workspace: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "requested": False,
        "requested_count": 0,
        "exhausted_count": 0,
        "ready_count": 0,
        **owner_handoff_index_summary(),
        "tasks": [],
    }
    try:
        task_runtime = TaskRuntimeService(str(workspace))
        entries = task_runtime.list_observable_task_rows()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    tasks: list[dict[str, Any]] = []
    requested_count = 0
    exhausted_count = 0
    ready_count = 0
    payload, _artifact = _read_task_boundary_workspace_validation(workspace)
    owner_handoff_routing: ScopeAuthorityOwnerHandoffRouting | None = None
    owner_handoff_index: ScopeAuthorityOwnerHandoffIndex | None = None
    if payload:
        repair_raw = payload.get("repair")
        repair: dict[str, Any] = repair_raw if isinstance(repair_raw, dict) else {}
        owner_handoff_routing = _quality_gate_owner_handoff_routing(repair, entries)
        owner_handoff_index = owner_handoff_routing.index
    for entry in entries:
        record = entry.to_dict() if hasattr(entry, "to_dict") else entry
        if not isinstance(record, dict):
            continue
        metadata_raw = record.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        completion_action_raw = metadata.get("factory_local_rework")
        completion_action = completion_action_raw if isinstance(completion_action_raw, Mapping) else {}
        completion_action_id = str(completion_action.get("action_id") or "").strip()
        completion_action_requested = re.fullmatch(r"[0-9a-f]{64}", completion_action_id) is not None
        requested = bool(metadata.get("qa_rework_requested")) or completion_action_requested
        exhausted = bool(metadata.get("qa_rework_exhausted"))
        if not requested and not exhausted:
            continue
        status = str(record.get("status") or "").strip().lower()
        if exhausted:
            exhausted_count += 1
        elif requested:
            requested_count += 1
        if status in {"pending", "ready"}:
            ready_count += 1
        task_entry: dict[str, Any] = {
            "task_id": str(record.get("id") or record.get("task_id") or "").strip(),
            "external_task_id": _resolve_task_identifier(metadata, record),
            "status": status,
            "reason": str(
                metadata.get("qa_rework_reason")
                or ("project_completion_owner_rework_authorized" if completion_action_requested else "")
            ).strip(),
            "retry_count": metadata.get("qa_rework_retry_count"),
            "max_retries": metadata.get("qa_rework_max_retries"),
            "exhausted": exhausted,
        }
        if completion_action_requested:
            task_entry["project_completion_action_id"] = completion_action_id
        if owner_handoff_index is not None:
            task_key = task_record_routing_key(record)
            matched_request = owner_handoff_index.matched_owner_handoff_by_task_key.get(task_key, {})
            if matched_request:
                task_entry["ownership_handoff_request"] = dict(matched_request)
                task_entry["ownership_handoff_target_file"] = str(matched_request.get("target_file") or "").strip()
        tasks.append(task_entry)

    summary.update(
        {
            "requested": requested_count > 0,
            "requested_count": requested_count,
            "exhausted_count": exhausted_count,
            "ready_count": ready_count,
            "tasks": tasks,
        }
    )
    if owner_handoff_routing is not None:
        summary.update(owner_handoff_routing.summary)
    return summary


def _read_docs_pipeline_state(workspace: str) -> dict[str, Any]:
    pipeline_payload = _read_json_artifact(workspace, "runtime/contracts/architect.docs_pipeline.json")
    progress_payload = _read_json_artifact(workspace, "runtime/state/pm.docs_progress.json")

    raw_stages = pipeline_payload.get("stages")
    stage_count = len(raw_stages) if isinstance(raw_stages, list) else 0
    enabled = stage_count > 0
    active_index_raw = progress_payload.get("active_stage_index", 0)
    try:
        active_index = int(active_index_raw)
    except (RuntimeError, ValueError):
        active_index = 0
    active_index = 0 if stage_count <= 0 else max(0, min(active_index, stage_count - 1))

    advance_reason = str(progress_payload.get("advance_reason") or "").strip()
    completed = enabled and advance_reason == "pipeline_complete"
    return {
        "enabled": enabled,
        "stage_count": stage_count,
        "active_stage_index": active_index,
        "active_stage_id": str(progress_payload.get("active_stage_id") or "").strip(),
        "advance_reason": advance_reason,
        "completed": completed,
    }


def _decide_delivery_loop_action(
    *,
    plan_signature: str,
    previous_plan_signature: str,
    unchanged_cycles: int,
    docs_state: dict[str, Any],
    max_stalled_cycles: int,
) -> dict[str, str]:
    signature_changed = bool(plan_signature) and (plan_signature != previous_plan_signature)
    docs_enabled = bool(docs_state.get("enabled"))
    docs_completed = bool(docs_state.get("completed"))

    if not plan_signature:
        return {
            "action": "fail",
            "reason": "pm_plan_signature_missing",
            "message": "PM loop cannot continue: tasks/plan.json missing or empty",
        }

    if docs_enabled and not docs_completed:
        if not signature_changed and unchanged_cycles >= max_stalled_cycles:
            return {
                "action": "fail",
                "reason": "docs_pipeline_stalled",
                "message": (
                    "Architect docs pipeline still incomplete but PM plan signature stopped changing "
                    f"(unchanged_cycles={unchanged_cycles}, stall_threshold={max_stalled_cycles})"
                ),
            }
        return {
            "action": "continue",
            "reason": "docs_pipeline_incomplete",
            "message": "Architect docs pipeline incomplete; continue PM→Chief Engineer→Director loop",
        }

    if signature_changed:
        return {
            "action": "continue",
            "reason": "plan_signature_changed",
            "message": "PM produced new task contract; continue PM→Chief Engineer→Director loop",
        }

    return {
        "action": "stop",
        "reason": "plan_signature_stable",
        "message": "PM task contract stabilized; stop delivery loop",
    }


# FactoryRun is imported here so that downstream modules inheriting this
# module's namespace via the package re-export continue to observe it.
__all__ = [
    "FactoryRun",
    "_apply_quality_gate_task_boundary_rework_requests",
    "_decide_delivery_loop_action",
    "_ownership_handoff_requests_from_repair_payload",
    "_quality_gate_handoff_summary_from_payload",
    "_quality_gate_owner_handoff_index",
    "_quality_gate_owner_handoff_routing",
    "_read_docs_pipeline_state",
    "_read_pm_plan_signature",
    "_read_quality_gate_rework_summary",
    "_read_task_boundary_workspace_validation",
    "_record_factory_task_runtime_transition_failure",
    "_safe_rework_int",
    "_task_boundary_rework_evidence",
    "_task_record_needs_task_boundary_rework",
    "_workspace_validation_requests_task_boundary_rework",
]
