"""Post-dispatch integration QA: evidence grading, receipts and requeue.

All bodies moved verbatim from ``dispatch_pipeline.py``. Cross-Cell access
(events, shared-quality verifier, cognitive runtime, task-market requeue,
task utils, shangshuling port) is routed through the in-function lazy
loaders in ``_lazy_imports``, so this module imports no Cell at load time."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

from polaris.cells.orchestration.pm_dispatch.internal.dispatch._lazy_imports import (
    _get_chief_engineer_blueprint_services,
    _get_cognitive_runtime_services,
    _get_io_utils,
    _get_shangshuling_port,
    _get_shared_quality,
    _get_task_market_requeue_services,
    _get_tasks_utils,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _classify_integration_qa_evidence(
    *,
    ran: bool,
    passed: bool | None,
    reason: str,
    summary: str,
    errors: list[str],
) -> str:
    """Classify integration QA evidence strength for audit consumers."""
    if not ran:
        return "not_run"
    normalized_reason = str(reason or "").strip().lower()
    normalized_summary = str(summary or "").strip().lower()
    normalized_errors = " ".join(str(item or "").strip().lower() for item in errors)
    if normalized_reason in {"docs_only", "docs_stage_docs_only"}:
        return "not_run_docs_only"
    if normalized_reason in {
        "integration_qa_disabled",
        "no_tasks",
        "no_director_tasks",
        "pending_director_tasks",
        "incomplete_tasks",
        "director_failures_present",
    }:
        return "not_run"
    if "node static verification passed" in normalized_summary:
        return "structural_fallback_passed" if passed is True else "structural_fallback_failed"
    if (
        "node dependencies are declared but not installed" in normalized_summary
        or "node dependencies are declared but not installed" in normalized_errors
    ):
        return "blocked_missing_dependencies"
    if "integration verification passed:" in normalized_summary:
        return "real_command_passed" if passed is True else "real_command_failed"
    if "integration verification failed:" in normalized_summary or normalized_errors:
        return "real_command_failed" if passed is False else "unknown"
    if normalized_reason == "integration_qa_error":
        return "qa_error"
    return "unknown"


def _context_snapshot_evidence(snapshot: Any) -> dict[str, Any]:
    """Extract compact, stable Context OS evidence from a resolved snapshot."""
    if snapshot is None:
        return {}
    context_os_summary = getattr(snapshot, "context_os_summary", {})
    source_refs = getattr(snapshot, "source_refs", ())
    return {
        "workspace": str(getattr(snapshot, "workspace", "") or "").strip(),
        "role": str(getattr(snapshot, "role", "") or "").strip(),
        "run_id": str(getattr(snapshot, "run_id", "") or "").strip(),
        "session_id": str(getattr(snapshot, "session_id", "") or "").strip(),
        "mode": str(getattr(snapshot, "mode", "") or "").strip(),
        "token_usage_estimate": int(getattr(snapshot, "token_usage_estimate", 0) or 0),
        "source_refs": [str(item).strip() for item in source_refs if str(item).strip()],
        "context_os_summary": dict(context_os_summary) if isinstance(context_os_summary, dict) else {},
    }


def _record_pm_dispatch_qa_cognitive_receipt(
    *,
    workspace_full: str,
    run_id: str,
    iteration: int,
    result: dict[str, Any],
    required: bool = True,
    context_os_expected: bool = True,
) -> dict[str, Any]:
    """Record Cognitive Runtime evidence for PM dispatch integration QA."""
    receipt_evidence: dict[str, Any] = {
        "ok": False,
        "required": bool(required),
        "receipt_type": "qa_verification",
        "source": "pm_dispatch",
        "context_os_expected": bool(context_os_expected),
    }
    workspace = str(workspace_full or "").strip()
    if not workspace:
        receipt_evidence["error_message"] = "missing_workspace"
        return receipt_evidence

    trace_refs = [
        str(item).strip()
        for item in (result.get("result_path"), result.get("runtime_result_path"))
        if str(item or "").strip()
    ]
    raw_errors = result.get("errors")
    errors = [str(item).strip() for item in raw_errors if str(item).strip()] if isinstance(raw_errors, list) else []
    raw_director_task_status = result.get("director_task_status")
    director_task_status = dict(raw_director_task_status) if isinstance(raw_director_task_status, dict) else {}
    status = "completed" if result.get("passed") is True else "skipped" if result.get("ran") is False else "failed"
    should_resolve_context = bool(context_os_expected and result.get("ran") is True)
    session_id = f"qa-{run_id or 'adhoc'}-{int(iteration or 0)}"
    context_os_evidence: dict[str, Any] = {
        "ok": False,
        "required": should_resolve_context,
        "skipped": not should_resolve_context,
        "reason": "qa_not_run" if not should_resolve_context else "",
    }
    try:
        receipt_command_type, resolve_context_command_type, get_cognitive_runtime_public_service = (
            _get_cognitive_runtime_services()
        )
        service = get_cognitive_runtime_public_service()
        try:
            if should_resolve_context:
                context_result = service.resolve_context(
                    resolve_context_command_type(
                        workspace=workspace,
                        role="qa",
                        query=str(result.get("summary") or result.get("reason") or "post-dispatch integration QA"),
                        step=int(iteration or 0),
                        run_id=str(run_id or "").strip() or "pm-dispatch",
                        mode="pm_dispatch_integration_qa",
                        session_id=session_id,
                        sources_enabled=("runtime", "events", "contracts"),
                        policy={
                            "source": "pm_dispatch.integration_qa",
                            "context_os_required": True,
                            "evidence_grade": str(result.get("evidence_grade") or "").strip(),
                        },
                    )
                )
                if not bool(getattr(context_result, "ok", False)):
                    context_os_evidence["error_message"] = (
                        str(getattr(context_result, "error_message", "") or "").strip()
                        or str(getattr(context_result, "error_code", "") or "").strip()
                        or "context_os_resolve_failed"
                    )
                    receipt_evidence["context_os"] = context_os_evidence
                    receipt_evidence["error_code"] = "qa_context_os_resolve_failed"
                    receipt_evidence["error_message"] = context_os_evidence["error_message"]
                    return receipt_evidence
                context_os_evidence = {
                    "ok": True,
                    "required": True,
                    "skipped": False,
                    "snapshot": _context_snapshot_evidence(getattr(context_result, "snapshot", None)),
                }
            receipt_evidence["context_os"] = context_os_evidence
            receipt_result = service.record_runtime_receipt(
                receipt_command_type(
                    workspace=workspace,
                    receipt_type="qa_verification",
                    session_id=session_id,
                    run_id=str(run_id or "").strip() or None,
                    trace_refs=tuple(trace_refs),
                    payload={
                        "source": "pm_dispatch.integration_qa",
                        "role": "qa",
                        "status": status,
                        "reason": str(result.get("reason") or "").strip(),
                        "summary": str(result.get("summary") or "").strip(),
                        "ran": bool(result.get("ran") is True),
                        "passed": result.get("passed"),
                        "evidence_grade": str(result.get("evidence_grade") or "").strip(),
                        "qa_path": str(result.get("qa_path") or "dispatch_pipeline"),
                        "pm_iteration": int(iteration or 0),
                        "director_task_status": director_task_status,
                        "result_path": str(result.get("result_path") or "").strip(),
                        "runtime_result_path": str(result.get("runtime_result_path") or "").strip(),
                        "errors": errors,
                        "context_os_expected": bool(context_os_expected),
                        "context_os": context_os_evidence,
                    },
                    turn_envelope={
                        "role": "qa",
                        "session_id": session_id,
                        "run_id": str(run_id or "").strip(),
                        "task_id": "qa::post_dispatch_integration",
                    },
                )
            )
        finally:
            service.close()
    except (RuntimeError, ValueError, ImportError) as exc:
        receipt_evidence["error_message"] = str(exc)
        return receipt_evidence

    if not bool(getattr(receipt_result, "ok", False)):
        error_message = str(getattr(receipt_result, "error_message", "") or "").strip()
        error_code = str(getattr(receipt_result, "error_code", "") or "").strip()
        receipt_evidence["error_message"] = error_message or error_code
        return receipt_evidence

    receipt = getattr(receipt_result, "receipt", None)
    receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
    if required and not receipt_id:
        receipt_evidence["error_message"] = "qa_cognitive_runtime_receipt_missing_id"
        return receipt_evidence
    receipt_evidence["ok"] = True
    if receipt_id:
        receipt_evidence["receipt_id"] = receipt_id
    return receipt_evidence


def _attach_pm_dispatch_qa_cognitive_receipt(
    *,
    workspace_full: str,
    run_id: str,
    iteration: int,
    result: dict[str, Any],
) -> None:
    """Attach Cognitive Runtime receipt evidence and fail closed when required."""
    receipt = _record_pm_dispatch_qa_cognitive_receipt(
        workspace_full=workspace_full,
        run_id=run_id,
        iteration=iteration,
        result=result,
    )
    result["cognitive_runtime_required"] = True
    result["context_os_expected"] = True
    result["cognitive_runtime_receipt"] = receipt
    if bool(receipt.get("ok")):
        return

    error_code = str(receipt.get("error_code") or "").strip()
    error_message = str(receipt.get("error_message") or "qa_cognitive_runtime_receipt_failed").strip()
    raw_errors = result.get("errors")
    errors = list(raw_errors) if isinstance(raw_errors, list) else []
    result["errors"] = [*errors, error_message]
    result["passed"] = False
    result["reason"] = (
        error_code if error_code == "qa_context_os_resolve_failed" else "qa_cognitive_runtime_receipt_failed"
    )
    result["summary"] = f"QA Cognitive Runtime receipt failed: {error_message}"
    result["evidence_grade"] = "qa_error"


def run_integration_qa(
    *,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    iteration: int,
    tasks: list[dict[str, Any]],
    run_events: str,
    dialogue_full: str,
    docs_stage: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run integration QA after dispatch.

    Args:
        workspace_full: Workspace path
        cache_root_full: Cache root path
        run_dir: Run directory
        run_id: Run identifier
        iteration: Iteration number
        tasks: Dispatched tasks
        run_events: Events file path
        dialogue_full: Dialogue file path
        docs_stage: Docs stage configuration

    Returns:
        Integration QA result dict
    """
    get_director_task_status_summary, to_bool = _get_tasks_utils()

    enabled = to_bool(
        os.environ.get("KERNELONE_INTEGRATION_QA_ENABLED", "1"),
        True,
    )

    status_summary = get_director_task_status_summary(tasks)

    result: dict[str, Any] = {
        "schema_version": 1,
        "enabled": enabled,
        "ran": False,
        "passed": None,
        "reason": "",
        "summary": "",
        "errors": [],
        "evidence_grade": "not_run",
        "run_id": run_id,
        "pm_iteration": int(iteration or 0),
        "director_task_status": status_summary,
        "cognitive_runtime_required": True,
        "context_os_expected": True,
        # Evidence-chain field: documents which QA path was used.
        # Surviving path: dispatch_pipeline (Cell-local, lightweight).
        # Deprecated path: QAWorkflow (temporal-activity-based, heavyweight).
        "qa_path": "dispatch_pipeline",
    }

    if not enabled:
        result["reason"] = "integration_qa_disabled"
        result["summary"] = "Integration QA is disabled"
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=False,
            passed=None,
            reason=str(result["reason"]),
            summary=str(result["summary"]),
            errors=[],
        )
        _attach_pm_dispatch_qa_cognitive_receipt(
            workspace_full=workspace_full,
            run_id=run_id,
            iteration=iteration,
            result=result,
        )
        return result

    if not tasks:
        result["reason"] = "no_tasks"
        result["summary"] = "No tasks to verify"
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=False,
            passed=None,
            reason=str(result["reason"]),
            summary=str(result["summary"]),
            errors=[],
        )
        _attach_pm_dispatch_qa_cognitive_receipt(
            workspace_full=workspace_full,
            run_id=run_id,
            iteration=iteration,
            result=result,
        )
        return result

    all_done = all(str(task.get("status", "")).lower() in ("done", "completed", "success") for task in tasks)
    if not all_done:
        result["reason"] = "incomplete_tasks"
        result["summary"] = "Not all tasks completed, skipping integration QA"
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=False,
            passed=None,
            reason=str(result["reason"]),
            summary=str(result["summary"]),
            errors=[],
        )
        _attach_pm_dispatch_qa_cognitive_receipt(
            workspace_full=workspace_full,
            run_id=run_id,
            iteration=iteration,
            result=result,
        )
        return result

    if _tasks_touch_docs_only(tasks):
        result["reason"] = "docs_only"
        result["summary"] = "All tasks are docs-only, skipping integration QA"
        result["ran"] = True
        result["passed"] = True
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=True,
            passed=True,
            reason=str(result["reason"]),
            summary=str(result["summary"]),
            errors=[],
        )
        _attach_pm_dispatch_qa_cognitive_receipt(
            workspace_full=workspace_full,
            run_id=run_id,
            iteration=iteration,
            result=result,
        )
        return result

    result["ran"] = True

    try:
        _, run_integration_verify_runner = _get_shared_quality()
        passed, summary, errors = run_integration_verify_runner(workspace_full)
        result["passed"] = passed
        result["summary"] = summary
        result["errors"] = errors
        result["reason"] = "integration_qa_passed" if passed else "integration_qa_failed"
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=True,
            passed=bool(passed),
            reason=str(result["reason"]),
            summary=str(summary or ""),
            errors=[str(item).strip() for item in (errors or []) if str(item).strip()],
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        result["passed"] = False
        result["reason"] = "integration_qa_error"
        result["summary"] = f"Integration QA error: {exc}"
        result["errors"] = [str(exc)]
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=True,
            passed=False,
            reason=str(result["reason"]),
            summary=str(result["summary"]),
            errors=[str(exc)],
        )

    _attach_pm_dispatch_qa_cognitive_receipt(
        workspace_full=workspace_full,
        run_id=run_id,
        iteration=iteration,
        result=result,
    )
    result["director_critique_feedback"] = _requeue_director_tasks_after_integration_qa_failure(
        workspace_full=workspace_full,
        result=result,
        tasks=tasks,
    )
    return result


def _tasks_touch_docs_only(tasks: Any) -> bool:
    """Check if all tasks only touch docs.

    Args:
        tasks: List of tasks

    Returns:
        True if all tasks are docs-only
    """
    if not isinstance(tasks, list):
        return False

    director_task_count = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue

        owner = str(task.get("assigned_to") or "").strip().lower()
        if owner and owner != "director":
            continue

        director_task_count += 1
        touched: list[str] = []
        for key in ("target_files", "context_files", "scope_paths", "scope"):
            value = task.get(key)
            if isinstance(value, str):
                entries = [segment.strip() for segment in value.split(",") if segment.strip()]
            elif isinstance(value, list):
                entries = [str(item).strip() for item in value if str(item).strip()]
            else:
                entries = []
            for item in entries:
                token = str(item).strip().replace("\\", "/").lower()
                token = token.lstrip("./")
                if token:
                    touched.append(token)

        if touched:
            for token in touched:
                if token.startswith("workspace/docs/") or token.startswith("docs/"):
                    continue
                if token.endswith(".md") and "/docs/" in token:
                    continue
                return False
            continue

        task_type = str(task.get("type") or "").lower()
        if "docs" not in task_type and "document" not in task_type:
            return False
    return director_task_count > 0


def _build_post_dispatch_integration_qa_result(
    *,
    enabled: bool,
    run_id: str,
    iteration: int,
    status_summary: dict[str, Any],
    docs_stage_payload: dict[str, Any],
) -> dict[str, Any]:
    """Create the baseline integration QA result payload."""
    return {
        "schema_version": 1,
        "enabled": enabled,
        "ran": False,
        "passed": None,
        "reason": "",
        "summary": "",
        "errors": [],
        "evidence_grade": "not_run",
        "run_id": run_id,
        "pm_iteration": int(iteration or 0),
        "director_task_status": status_summary,
        "result_path": "",
        "runtime_result_path": "",
        "cognitive_runtime_required": True,
        "context_os_expected": True,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "docs_stage": {
            "enabled": bool(docs_stage_payload.get("enabled")),
            "active_doc_path": str(docs_stage_payload.get("active_doc_path") or "").strip(),
        },
        # Evidence-chain field: documents which QA path was used.
        "qa_path": "dispatch_pipeline",
    }


def _apply_post_dispatch_skip_reason(
    *,
    result: dict[str, Any],
    status_summary: dict[str, Any],
    tasks: Any,
    docs_stage_payload: dict[str, Any],
) -> bool:
    """Set a deterministic skip reason. Returns True when execution should stop."""
    if not bool(result.get("enabled")):
        result["reason"] = "integration_qa_disabled"
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=False,
            passed=None,
            reason=str(result["reason"]),
            summary=str(result.get("summary") or ""),
            errors=[],
        )
        return True
    if int(status_summary.get("total") or 0) <= 0:
        result["reason"] = "no_director_tasks"
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=False,
            passed=None,
            reason=str(result["reason"]),
            summary=str(result.get("summary") or ""),
            errors=[],
        )
        return True
    if bool(docs_stage_payload.get("enabled")) and _tasks_touch_docs_only(tasks):
        result["reason"] = "docs_stage_docs_only"
        result["summary"] = "Integration QA skipped for docs-only stage tasks."
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=False,
            passed=None,
            reason=str(result["reason"]),
            summary=str(result["summary"]),
            errors=[],
        )
        return True
    if (
        int(status_summary.get("todo") or 0)
        + int(status_summary.get("in_progress") or 0)
        + int(status_summary.get("review") or 0)
        + int(status_summary.get("needs_continue") or 0)
    ) > 0:
        result["reason"] = "pending_director_tasks"
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=False,
            passed=None,
            reason=str(result["reason"]),
            summary=str(result.get("summary") or ""),
            errors=[],
        )
        return True
    if int(status_summary.get("failed") or 0) > 0 or int(status_summary.get("blocked") or 0) > 0:
        done_count = int(status_summary.get("done") or 0)
        if done_count > 0:
            # Partial-evidence mode: at least one task completed, so run QA on
            # the workspace as delivered instead of skipping evidence-free.
            # Fail-closed is unchanged — the graded exit code still reflects
            # the Director failures — but a real verdict artifact now exists,
            # and falsely-failed tasks keep the reconciliation channel alive.
            result["scope"] = "partial_completed_tasks"
            result["scope_detail"] = {
                "done": done_count,
                "failed": int(status_summary.get("failed") or 0),
                "blocked": int(status_summary.get("blocked") or 0),
            }
            return False
        result["reason"] = "director_failures_present"
        result["passed"] = False
        result["summary"] = "Integration QA cannot run because Director produced failed or blocked tasks."
        result["evidence_grade"] = _classify_integration_qa_evidence(
            ran=False,
            passed=False,
            reason=str(result["reason"]),
            summary=str(result["summary"]),
            errors=[],
        )
        return True
    return False


def _resolve_verify_runner(
    verify_runner: Callable[[str], tuple[bool, str, list[str]]] | None,
) -> Callable[[str], tuple[bool, str, list[str]]]:
    """Resolve the verify runner used by integration QA."""
    if verify_runner is not None:
        return verify_runner
    from polaris.cells.orchestration.pm_planning.public.service import (
        run_integration_verify_runner,
    )

    return run_integration_verify_runner


def _execute_post_dispatch_integration_qa(
    *,
    workspace_full: str,
    result: dict[str, Any],
    verify_runner: Callable[[str], tuple[bool, str, list[str]]] | None,
) -> None:
    """Execute integration QA and mutate the result payload in place."""
    resolved_verify_runner = _resolve_verify_runner(verify_runner)
    result["ran"] = True
    success, summary, errors = resolved_verify_runner(workspace_full)
    result["passed"] = bool(success)
    result["summary"] = str(summary or "").strip()
    result["errors"] = [str(item).strip() for item in (errors or []) if str(item).strip()][:20]
    result["reason"] = "integration_qa_passed" if success else "integration_qa_failed"
    result["evidence_grade"] = _classify_integration_qa_evidence(
        ran=True,
        passed=bool(success),
        reason=str(result["reason"]),
        summary=str(result["summary"]),
        errors=list(result["errors"]),
    )


def _integration_qa_failure_should_requeue(result: dict[str, Any]) -> bool:
    if result.get("passed") is not False:
        return False
    if result.get("ran") is not True:
        return False
    return str(result.get("reason") or "").strip() in {
        "integration_qa_failed",
        "integration_qa_runtime_error",
        "integration_qa_error",
    }


def _extract_task_id(task: Any) -> str:
    if not isinstance(task, dict):
        return ""
    for key in ("id", "task_id", "pm_task_id"):
        token = str(task.get(key) or "").strip()
        if token:
            return token
    return ""


def _is_director_assigned_task(task: dict[str, Any]) -> bool:
    assigned_to = str(task.get("assigned_to") or task.get("assignee") or "").strip().lower()
    return not assigned_to or assigned_to == "director"


def _string_list_from_task(task: dict[str, Any], key: str) -> list[str]:
    raw = task.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _string_list_from_result_field(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        token = raw.strip()
        return [token] if token else []
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    token = str(raw).strip()
    return [token] if token else []


def _build_integration_qa_last_failure(
    *,
    result: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    errors = _string_list_from_result_field(result.get("errors"))
    summary = str(result.get("summary") or "").strip()
    message_parts = [summary, *errors]
    error_message = " | ".join(part for part in message_parts if part).strip()
    if not error_message:
        error_message = str(result.get("reason") or "integration_qa_failed")
    return {
        "error_code": "INTEGRATION_QA_FAILED",
        "error_message": error_message[:1200],
        "source": "pm_dispatch.integration_qa",
        "reason": str(result.get("reason") or ""),
        "run_id": str(result.get("run_id") or ""),
        "pm_iteration": int(result.get("pm_iteration") or 0),
        "result_path": str(result.get("result_path") or ""),
        "runtime_result_path": str(result.get("runtime_result_path") or ""),
        "qa_path": str(result.get("qa_path") or "dispatch_pipeline"),
        "evidence_grade": str(result.get("evidence_grade") or "unknown"),
        "target_files": _string_list_from_task(task, "target_files"),
        "acceptance_criteria": _string_list_from_task(task, "acceptance_criteria"),
    }


def _build_integration_qa_verification_failure_report(
    *,
    result: dict[str, Any],
    last_failure: dict[str, Any],
    task_id: str,
    target_task_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "verification.failure.v1",
        "source": "pm_dispatch.integration_qa",
        "failure_classification": "Director Execution",
        "gate": "integration_qa",
        "reason": str(result.get("reason") or "integration_qa_failed"),
        "summary": str(result.get("summary") or ""),
        "errors": _string_list_from_result_field(result.get("errors")),
        "pm_task_id": task_id,
        "target_task_id": target_task_id,
        "run_id": str(result.get("run_id") or ""),
        "result_path": str(result.get("result_path") or ""),
        "runtime_result_path": str(result.get("runtime_result_path") or ""),
        "last_failure": dict(last_failure),
    }


def _build_integration_qa_rework_blueprint_context(
    *,
    task: dict[str, Any],
    task_id: str,
    target_task_id: str,
    last_failure: dict[str, Any],
    verification_failure_report: dict[str, Any],
) -> dict[str, Any]:
    title = str(task.get("title") or task.get("name") or task_id).strip()
    target_files = _string_list_from_task(task, "target_files")
    acceptance_criteria = _string_list_from_task(task, "acceptance_criteria")
    return {
        "task": dict(task),
        "task_title": f"Rework after integration QA failure: {title}" if title else "Integration QA rework",
        "title": title,
        "pm_task_id": task_id,
        "director_task_id": target_task_id,
        "target_files": target_files,
        "scope_paths": target_files,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": [
            "Inspect the recorded integration QA failure evidence before editing.",
            "Repair the existing implementation within the PM contract and CE handoff scope.",
            "Run at least one real build, test, lint, CLI, Web, or API gate after the repair.",
        ],
        "rework": {
            "kind": "integration_qa_failure",
            "requires_chief_engineer_handoff": True,
            "failure": dict(last_failure),
            "verification_failure_report": dict(verification_failure_report),
        },
    }


def _generate_integration_qa_rework_blueprint(
    *,
    workspace_full: str,
    result: dict[str, Any],
    task: dict[str, Any],
    task_id: str,
    target_task_id: str,
    last_failure: dict[str, Any],
    verification_failure_report: dict[str, Any],
) -> dict[str, Any]:
    try:
        command_type, generate_blueprint = _get_chief_engineer_blueprint_services()
    except (ImportError, RuntimeError, ValueError) as exc:
        return {"ok": False, "error": f"chief_engineer_blueprint_unavailable: {exc}"}

    objective = (
        f"Repair task {target_task_id} after integration QA failure. "
        f"Failure: {last_failure.get('error_message') or result.get('summary') or result.get('reason')}"
    )
    try:
        blueprint_result = generate_blueprint(
            command_type(
                task_id=target_task_id,
                workspace=workspace_full,
                objective=objective[:4000],
                run_id=str(result.get("run_id") or ""),
                constraints={
                    "source": "pm_dispatch.integration_qa.rework",
                    "chain": "PM->ChiefEngineer->Director",
                    "failure_classification": "Director Execution",
                    "must_preserve_pm_contract": True,
                    "target_project_code_must_not_be_hardcoded_in_polaris": True,
                },
                context=_build_integration_qa_rework_blueprint_context(
                    task=task,
                    task_id=task_id,
                    target_task_id=target_task_id,
                    last_failure=last_failure,
                    verification_failure_report=verification_failure_report,
                ),
            )
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        return {"ok": False, "error": f"chief_engineer_blueprint_failed: {type(exc).__name__}: {exc}"}

    blueprint_id = str(getattr(blueprint_result, "blueprint_id", "") or "").strip()
    blueprint_path = str(getattr(blueprint_result, "blueprint_path", "") or "").strip()
    if not bool(getattr(blueprint_result, "ok", False)) or not blueprint_id or not blueprint_path:
        summary = str(getattr(blueprint_result, "summary", "") or getattr(blueprint_result, "status", "") or "")
        return {"ok": False, "error": f"chief_engineer_blueprint_invalid: {summary}"}
    return {
        "ok": True,
        "blueprint_id": blueprint_id,
        "blueprint_path": blueprint_path,
        "summary": str(getattr(blueprint_result, "summary", "") or ""),
    }


def _resolve_integration_qa_requeue_target_ids(
    *,
    task_market_service: Any,
    workspace_full: str,
    pm_task_id: str,
) -> list[str]:
    if not hasattr(task_market_service, "query_status"):
        return [pm_task_id]
    try:
        from polaris.cells.runtime.task_market.public.contracts import QueryTaskMarketStatusV1

        status_result = task_market_service.query_status(
            QueryTaskMarketStatusV1(workspace=workspace_full, limit=10_000, include_payload=True)
        )
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("integration QA lineage lookup failed for %s: %s", pm_task_id, exc)
        return [pm_task_id]

    leaf_ids: list[str] = []
    for row in getattr(status_result, "items", ()):
        if not isinstance(row, dict):
            continue
        row_task_id = str(row.get("task_id") or "").strip()
        if not row_task_id:
            continue
        lineage = {
            row_task_id,
            str(row.get("root_task_id") or "").strip(),
            str(row.get("parent_task_id") or "").strip(),
        }
        if pm_task_id not in lineage:
            continue
        if bool(row.get("is_leaf", True)):
            leaf_ids.append(row_task_id)

    unique_leaf_ids = list(dict.fromkeys(leaf_ids))
    return unique_leaf_ids or [pm_task_id]


def _requeue_director_tasks_after_integration_qa_failure(
    *,
    workspace_full: str,
    result: dict[str, Any],
    tasks: Any,
) -> dict[str, Any]:
    feedback: dict[str, Any] = {
        "attempted_task_ids": [],
        "requeued_task_ids": [],
        "skipped_task_ids": [],
        "errors": [],
    }
    if not _integration_qa_failure_should_requeue(result):
        return feedback
    if not isinstance(tasks, list):
        feedback["errors"].append("tasks_payload_not_list")
        return feedback

    try:
        requeue_task_command_v1, get_task_market_service = _get_task_market_requeue_services()
        task_market_service = get_task_market_service()
    except (ImportError, RuntimeError, ValueError) as exc:
        feedback["errors"].append(f"task_market_unavailable: {exc}")
        return feedback

    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if not _is_director_assigned_task(task):
            continue
        task_id = _extract_task_id(task)
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        feedback["attempted_task_ids"].append(task_id)
        last_failure = _build_integration_qa_last_failure(result=result, task=task)
        target_task_ids = _resolve_integration_qa_requeue_target_ids(
            task_market_service=task_market_service,
            workspace_full=workspace_full,
            pm_task_id=task_id,
        )
        for target_task_id in target_task_ids:
            verification_failure_report = _build_integration_qa_verification_failure_report(
                result=result,
                last_failure=last_failure,
                task_id=task_id,
                target_task_id=target_task_id,
            )
            ce_rework_blueprint = _generate_integration_qa_rework_blueprint(
                workspace_full=workspace_full,
                result=result,
                task=task,
                task_id=task_id,
                target_task_id=target_task_id,
                last_failure=last_failure,
                verification_failure_report=verification_failure_report,
            )
            if not bool(ce_rework_blueprint.get("ok")):
                feedback["skipped_task_ids"].append(target_task_id)
                feedback["errors"].append(f"{target_task_id}: {ce_rework_blueprint.get('error') or 'ce_rework_failed'}")
                continue
            chief_engineer_handoff = {
                "source": "chief_engineer.generate_task_blueprint",
                "handoff_kind": "integration_qa_rework",
                "chain": "PM->ChiefEngineer->Director",
                "pm_task_id": task_id,
                "director_task_id": target_task_id,
                "blueprint_id": str(ce_rework_blueprint["blueprint_id"]),
                "blueprint_path": str(ce_rework_blueprint["blueprint_path"]),
            }
            enriched_last_failure = {
                **last_failure,
                "ce_rework_required": True,
                "ce_rework_blueprint_id": str(ce_rework_blueprint["blueprint_id"]),
                "ce_rework_blueprint_path": str(ce_rework_blueprint["blueprint_path"]),
                "chief_engineer_handoff": chief_engineer_handoff,
            }
            try:
                requeue_result = task_market_service.requeue_task(
                    requeue_task_command_v1(
                        workspace=workspace_full,
                        task_id=target_task_id,
                        target_stage="pending_design",
                        reason=str(result.get("summary") or result.get("reason") or "integration_qa_failed"),
                        metadata={
                            "source": "pm_dispatch.integration_qa.rework",
                            "pm_task_id": task_id,
                            "ce_rework_required": True,
                            "ce_rework_blueprint_id": str(ce_rework_blueprint["blueprint_id"]),
                            "ce_rework_blueprint_path": str(ce_rework_blueprint["blueprint_path"]),
                            "chief_engineer_handoff": chief_engineer_handoff,
                            "verification_failure_report": verification_failure_report,
                            "last_failure": enriched_last_failure,
                        },
                        reopen_policy={
                            "allowed_sources": ["pm_dispatch.integration_qa.rework"],
                            "max_reopen_count": 3,
                            "requires_failure_report": True,
                        },
                    )
                )
            except (RuntimeError, ValueError) as exc:
                feedback["errors"].append(f"{target_task_id}: {exc}")
                continue
            if bool(getattr(requeue_result, "ok", False)):
                feedback["requeued_task_ids"].append(target_task_id)
            else:
                feedback["skipped_task_ids"].append(target_task_id)
                reason = str(
                    getattr(requeue_result, "reason", "") or getattr(requeue_result, "error_message", "") or ""
                )
                if reason:
                    feedback["errors"].append(f"{target_task_id}: {reason}")
    return feedback


def _persist_post_dispatch_integration_qa_result(
    *,
    run_dir: str,
    workspace_full: str,
    cache_root_full: str,
    result: dict[str, Any],
) -> None:
    """Persist the integration QA result payload."""
    from polaris.kernelone.fs.text_ops import write_json_atomic
    from polaris.kernelone.storage.io_paths import resolve_artifact_path

    result_path = os.path.join(run_dir, "qa", "integration_qa.result.json")
    result["result_path"] = result_path
    runtime_result_path = ""
    if workspace_full:
        runtime_result_path = resolve_artifact_path(
            workspace_full,
            cache_root_full,
            "runtime/results/integration_qa.result.json",
        )
        result["runtime_result_path"] = runtime_result_path
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    write_json_atomic(result_path, result)
    if runtime_result_path:
        try:
            write_json_atomic(runtime_result_path, result)
        except (OSError, TypeError, ValueError) as exc:
            raw_errors = result.get("errors")
            errors = list(raw_errors) if isinstance(raw_errors, list) else []
            result["errors"] = [*errors, f"qa_runtime_result_persist_failed: {exc}"]
            write_json_atomic(result_path, result)


def _emit_post_dispatch_integration_qa_result(
    *,
    run_events: str,
    dialogue_full: str,
    run_id: str,
    iteration: int,
    result: dict[str, Any],
    emit_event: Callable[..., Any],
    emit_dialogue: Callable[..., Any],
) -> None:
    """Emit integration QA completion events after the result is persisted."""
    if result["ran"] is not True:
        return

    cognitive_runtime_receipt = (
        result.get("cognitive_runtime_receipt") if isinstance(result.get("cognitive_runtime_receipt"), dict) else {}
    )
    emit_event(
        run_events,
        kind="status",
        actor="QA",
        name="integration_qa_complete",
        refs={
            "run_id": run_id,
            "phase": "integration_qa",
            "files": [result.get("result_path", "")],
        },
        summary=("Project integration QA passed" if result.get("passed") is True else "Project integration QA failed"),
        ok=bool(result.get("passed") is True),
        output={
            "summary": result.get("summary"),
            "reason": result.get("reason"),
            "passed": bool(result.get("passed") is True),
            "evidence_grade": str(result.get("evidence_grade") or "unknown"),
            "errors_count": len(result.get("errors") or []),
            "result_path": result.get("result_path"),
            "runtime_result_path": result.get("runtime_result_path"),
            "cognitive_runtime_receipt": cognitive_runtime_receipt,
        },
        error="" if result.get("passed") is True else "INTEGRATION_QA_FAILED",
    )
    emit_dialogue(
        dialogue_full,
        speaker="QA",
        type="review",
        text=(
            f"Project integration QA: {'PASS' if result.get('passed') is True else 'FAIL'}; "
            + str(result.get("summary") or "")
        ).strip(),
        summary="Project integration QA",
        run_id=run_id,
        pm_iteration=iteration,
        refs={"phase": "integration_qa", "files": [result.get("result_path", "")]},
        meta={
            "passed": bool(result.get("passed") is True),
            "reason": str(result.get("reason") or ""),
            "evidence_grade": str(result.get("evidence_grade") or "unknown"),
            "cognitive_runtime_receipt": cognitive_runtime_receipt,
        },
    )


def run_post_dispatch_integration_qa(
    *,
    args: Any = None,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    iteration: int,
    tasks: Any,
    run_events: str,
    dialogue_full: str,
    verify_runner: Callable[[str], tuple[bool, str, list[str]]] | None = None,
    docs_stage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run project-level integration QA after task dispatch when all director tasks are done.

    Args:
        args: Optional CLI arguments carrying the integration_qa switch
        workspace_full: Workspace path
        cache_root_full: Cache root path
        run_dir: Run directory
        run_id: Run identifier
        iteration: Iteration number
        tasks: Tasks to verify
        run_events: Events file path
        dialogue_full: Dialogue file path
        verify_runner: Optional custom verify runner function
        docs_stage: Docs stage configuration

    Returns:
        Integration QA result dict
    """
    get_director_task_status_summary, to_bool = _get_tasks_utils()
    emit_event, emit_dialogue = _get_io_utils()

    enabled = to_bool(
        getattr(args, "integration_qa", None),
        default=to_bool(
            os.environ.get("KERNELONE_INTEGRATION_QA_ENABLED", "1"),
            default=True,
        ),
    )
    status_summary = get_director_task_status_summary(tasks)
    docs_stage_payload = docs_stage if isinstance(docs_stage, dict) else {}
    result = _build_post_dispatch_integration_qa_result(
        enabled=enabled,
        run_id=run_id,
        iteration=iteration,
        status_summary=status_summary,
        docs_stage_payload=docs_stage_payload,
    )

    should_skip = _apply_post_dispatch_skip_reason(
        result=result,
        status_summary=status_summary,
        tasks=tasks,
        docs_stage_payload=docs_stage_payload,
    )
    if not should_skip:
        try:
            _execute_post_dispatch_integration_qa(
                workspace_full=workspace_full,
                result=result,
                verify_runner=verify_runner,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            result["passed"] = False
            result["reason"] = "integration_qa_runtime_error"
            result["summary"] = f"Integration QA runtime error: {exc}"
            result["errors"] = [str(exc)]

    _persist_post_dispatch_integration_qa_result(
        run_dir=run_dir,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        result=result,
    )
    _attach_pm_dispatch_qa_cognitive_receipt(
        workspace_full=workspace_full,
        run_id=run_id,
        iteration=iteration,
        result=result,
    )
    result["director_critique_feedback"] = _requeue_director_tasks_after_integration_qa_failure(
        workspace_full=workspace_full,
        result=result,
        tasks=tasks,
    )
    _persist_post_dispatch_integration_qa_result(
        run_dir=run_dir,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        result=result,
    )
    _emit_post_dispatch_integration_qa_result(
        run_events=run_events,
        dialogue_full=dialogue_full,
        run_id=run_id,
        iteration=iteration,
        result=result,
        emit_event=emit_event,
        emit_dialogue=emit_dialogue,
    )

    return result


def record_dispatch_status_to_shangshuling(
    *,
    workspace_full: str,
    status_updates: dict[str, str],
    failure_info: dict[str, Any],
    shangshuling_port: Any | None = None,
) -> int:
    """Record task dispatch status to shangshuling.

    Args:
        workspace_full: Workspace path
        status_updates: Dict of task_id -> status
        failure_info: Failure information to record
        shangshuling_port: Optional pre-injected ShangshulingPort; when None,
            the Cell-local registry port is loaded lazily.

    Returns:
        Number of records written
    """
    from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
        normalize_task_status,
    )

    if not isinstance(status_updates, dict) or not status_updates:
        return 0

    port = shangshuling_port if shangshuling_port is not None else _get_shangshuling_port()

    recorded = 0
    failure_payload = failure_info if isinstance(failure_info, dict) else {}
    for task_id, raw_status in status_updates.items():
        status = normalize_task_status(raw_status)
        if status not in {"done", "failed", "blocked"}:
            continue
        success = status == "done"
        try:
            port.record_shangshuling_task_completion(
                workspace_full,
                task_id=task_id,
                success=success,
                metadata=failure_payload,
            )
            recorded += 1
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            logger.debug("Failed to record task completion for %s: %s", task_id, e)
    return recorded
