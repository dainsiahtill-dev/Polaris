"""Workflow dispatch pipeline runner (extracted from ``orchestration_engine``).

This module owns the heavy body of ``_run_dispatch_pipeline_with_workflow``:
submitting the PM workflow, waiting for terminal state, summarizing Director
task execution, grading exit codes, running post-dispatch integration QA, and
persisting the chain summary / workflow state.

The body is a lossless extraction of the original ``orchestration_engine``
definition. ``orchestration_engine`` keeps the canonical, keyword-only shim
``_run_dispatch_pipeline_with_workflow`` that delegates here.

CRITICAL — monkeypatch-through-namespace invariant:
The integration-QA guard tests ``monkeypatch.setattr`` the following names on
the ``orchestration_engine`` module object:
``submit_pm_workflow_sync``, ``wait_for_workflow_completion_sync``,
``get_workflow_runtime_status``, ``summarize_workflow_tasks``,
``resolve_director_dispatch_tasks``, ``run_post_dispatch_integration_qa``,
``persist_pm_payload``, ``emit_event``.

Therefore every call to one of those names MUST resolve it through the
``orchestration_engine`` module object at call time (``_oe.NAME(...)``) rather
than via a frozen ``from ... import name`` — otherwise ``setattr`` patches are
bypassed. A function-local import of ``orchestration_engine`` avoids the import
cycle (this module is imported by ``orchestration_engine`` at module load).
All other collaborators (not monkeypatched in tests) are imported directly.
"""

from __future__ import annotations

import argparse
import contextlib
import os
from datetime import datetime
from typing import Any, cast

from polaris.cells.runtime.projection.public import (
    canonicalize_workflow_task_state,
    write_workflow_state,
)
from polaris.delivery.cli.pm.blocked_policy import should_apply_degrade_settings
from polaris.delivery.cli.pm.engine.core import PolarisEngine
from polaris.delivery.cli.pm.orchestration.exit_grading import (
    build_chain_summary,
    grade_director_exit_code,
    grade_qa_exit_code,
)
from polaris.delivery.cli.pm.orchestration.workflow_timeout import (
    _pm_workflow_wait_timeout_seconds,
)
from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.shared.text_utils import normalize_timeout_seconds
from polaris.kernelone.storage.io_paths import resolve_artifact_path

__all__ = ["run_dispatch_pipeline_with_workflow"]


def run_dispatch_pipeline_with_workflow(
    *,
    args: argparse.Namespace,
    engine: PolarisEngine,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    iteration: int,
    normalized: dict[str, Any],
    run_events: str,
    dialogue_full: str,
    runtime_pm_tasks_full: str,
    pm_out_full: str,
    run_pm_tasks: str,
    run_director_result: str,
    docs_stage: dict[str, Any] | None,
    pm_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run dispatch pipeline with workflow orchestration.

    This function uses the new dispatch_pipeline module to execute
    the full dispatch pipeline including Chief Engineer preflight,
    engine dispatch, and integration QA.
    """
    # Resolve monkeypatchable globals through the orchestration_engine module
    # object at call time so test setattr patches still win (see module docstring).
    import polaris.delivery.cli.pm.orchestration_engine as _oe

    logger = _oe.logger

    # Apply degrade settings from pm_state if present
    should_degrade, degrade_settings = should_apply_degrade_settings(pm_state or {})
    if should_degrade:
        # Create a copy of args with degraded settings
        args = argparse.Namespace(**vars(args))
        if degrade_settings.get("serial_mode"):
            args.director_workflow_execution_mode = "serial"
        if degrade_settings.get("max_parallel") is not None:
            args.director_max_parallel_tasks = degrade_settings["max_parallel"]
        # Note: integration_qa and max_verification_retries are handled in metadata below

    outcome: dict[str, Any] = {
        "used": False,
        "exit_code": 0,
        "chief_engineer_result": None,
        "engine_dispatch": None,
        "integration_qa_result": None,
        "director_result": None,
        "error": "",
    }

    def _summarize_workflow_execution(
        workflow_status: dict[str, Any] | None,
        base_tasks: list[dict[str, Any]],
        default_total: int,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        if isinstance(workflow_status, dict):
            summary = _oe.summarize_workflow_tasks(
                workflow_status,
                base_tasks=base_tasks,
                workspace=workspace_full,
                cache_root=cache_root_full,
            )
        _raw_tasks = summary.get("tasks") if isinstance(summary, dict) else None
        tasks: list[dict[str, Any]] = _raw_tasks if isinstance(_raw_tasks, list) else []
        if not tasks and base_tasks:
            tasks = list(base_tasks)

        counts = {
            "completed": 0,
            "failed": 0,
            "blocked": 0,
            "active": 0,
            "pending": 0,
        }
        for item in tasks:
            if not isinstance(item, dict):
                continue
            state = canonicalize_workflow_task_state(item.get("status") or item.get("state"))
            if state == "completed":
                counts["completed"] += 1
            elif state == "failed":
                counts["failed"] += 1
            elif state == "blocked":
                counts["blocked"] += 1
            elif state in {"ready", "claimed", "in_progress"}:
                counts["active"] += 1
            else:
                counts["pending"] += 1

        total = int(summary.get("total") or 0)
        if total <= 0:
            total = max(int(default_total or 0), len(tasks))
        if not tasks and total > 0:
            counts["pending"] = total

        workflow_status_token = str((workflow_status or {}).get("workflow_status") or "").strip().lower()
        workflow_failed = workflow_status_token in {"failed", "terminated", "timed_out", "canceled", "cancelled"}
        if workflow_failed:
            unresolved_count = max(0, total - counts["completed"])
            if unresolved_count > 0 and counts["failed"] == 0 and counts["blocked"] == 0:
                counts["failed"] = unresolved_count
                counts["pending"] = 0
                counts["active"] = 0
                for item in tasks:
                    if not isinstance(item, dict):
                        continue
                    state = canonicalize_workflow_task_state(item.get("status") or item.get("state"))
                    if state == "completed":
                        continue
                    item["status"] = "failed"
                    item["state"] = "failed"
                    item["error"] = "Workflow execution failed before Director task completion"

        state = str(summary.get("state") or "").strip().lower()
        if workflow_failed and counts["completed"] < total:
            state = "failed"
        if not state:
            if counts["failed"] > 0 or counts["blocked"] > 0:
                state = "failed"
            elif total > 0 and counts["completed"] >= total:
                state = "completed"
            elif counts["active"] > 0:
                state = "running"
            elif counts["pending"] > 0:
                state = "queued"
            else:
                state = "idle"

        return {
            "tasks": tasks,
            "total": total,
            "state": state,
            **counts,
        }

    def _extract_nested_workflow_result(payload: Any) -> dict[str, Any]:
        current = payload
        for _ in range(8):
            if not isinstance(current, dict):
                return {}
            if any(
                key in current
                for key in (
                    "director_status",
                    "qa_status",
                    "completed_tasks",
                    "failed_tasks",
                    "blocked_tasks",
                )
            ):
                return current
            details = current.get("details")
            if isinstance(details, dict) and isinstance(details.get("final"), dict):
                current = details["final"]
                continue
            result = current.get("result")
            if isinstance(result, dict) and result is not current:
                current = result
                continue
            return {}
        return {}

    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (RuntimeError, TypeError, ValueError):
            return int(default)

    def _canonicalize_terminal_nested_statuses(
        value: Any,
        *,
        final_workflow_status: str,
        final_director_status: str,
        final_qa_status: str,
    ) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if final_workflow_status == "completed" and key == "director_status":
                    result[key] = final_director_status
                elif final_workflow_status == "completed" and key == "qa_status":
                    result[key] = final_qa_status
                else:
                    result[key] = _canonicalize_terminal_nested_statuses(
                        item,
                        final_workflow_status=final_workflow_status,
                        final_director_status=final_director_status,
                        final_qa_status=final_qa_status,
                    )
            return result
        if isinstance(value, list):
            return [
                _canonicalize_terminal_nested_statuses(
                    item,
                    final_workflow_status=final_workflow_status,
                    final_director_status=final_director_status,
                    final_qa_status=final_qa_status,
                )
                for item in value
            ]
        return value

    def _workflow_result_director_status(payload: dict[str, Any]) -> str:
        token = str(payload.get("director_status") or payload.get("status") or "").strip().lower()
        if token in {"completed", "success", "succeeded", "passed"}:
            return "success"
        if token in {"failed", "fail", "error", "director_failed"}:
            return "failed"
        if token in {"blocked", "dependency_blocked"}:
            return "blocked"
        if token in {"running", "in_progress"}:
            return "running"
        if token in {"queued", "pending", "submitted"}:
            return "queued"
        failed_count = _safe_int(payload.get("failed_tasks"), 0)
        blocked_count = _safe_int(payload.get("blocked_tasks"), 0)
        completed_count = _safe_int(payload.get("completed_tasks"), 0)
        if failed_count > 0:
            return "failed"
        if blocked_count > 0:
            return "blocked"
        if completed_count > 0:
            return "success"
        return ""

    def _apply_workflow_result_summary(
        workflow_summary: dict[str, Any],
        result_payload: dict[str, Any],
        director_status_token: str,
        default_total: int,
    ) -> None:
        total = max(
            _safe_int(workflow_summary.get("total"), 0),
            _safe_int(result_payload.get("completed_tasks"), 0)
            + _safe_int(result_payload.get("failed_tasks"), 0)
            + _safe_int(result_payload.get("blocked_tasks"), 0),
            int(default_total or 0),
        )
        if total > 0:
            workflow_summary["total"] = total

        completed_count = _safe_int(result_payload.get("completed_tasks"), -1)
        failed_count = _safe_int(result_payload.get("failed_tasks"), -1)
        blocked_count = _safe_int(result_payload.get("blocked_tasks"), -1)

        if completed_count >= 0:
            workflow_summary["completed"] = completed_count
        if failed_count >= 0:
            workflow_summary["failed"] = failed_count
        if blocked_count >= 0:
            workflow_summary["blocked"] = blocked_count

        if director_status_token == "success":
            workflow_summary["completed"] = max(_safe_int(workflow_summary.get("completed"), 0), total)
            workflow_summary["failed"] = 0
            workflow_summary["blocked"] = 0
            workflow_summary["active"] = 0
            workflow_summary["pending"] = 0
            workflow_summary["state"] = "completed"
            raw_tasks = workflow_summary.get("tasks")
            if isinstance(raw_tasks, list):
                for item in raw_tasks:
                    if isinstance(item, dict):
                        item["status"] = "completed"
                        item["state"] = "completed"
        elif director_status_token in {"failed", "blocked"}:
            unresolved = max(
                0,
                total
                - _safe_int(workflow_summary.get("completed"), 0)
                - _safe_int(workflow_summary.get("failed"), 0)
                - _safe_int(workflow_summary.get("blocked"), 0),
            )
            if director_status_token == "failed" and _safe_int(workflow_summary.get("failed"), 0) <= 0:
                workflow_summary["failed"] = max(1 if total > 0 else 0, unresolved)
            if director_status_token == "blocked" and _safe_int(workflow_summary.get("blocked"), 0) <= 0:
                workflow_summary["blocked"] = max(1 if total > 0 else 0, unresolved)
            workflow_summary["active"] = 0
            workflow_summary["pending"] = 0
            workflow_summary["state"] = director_status_token
            raw_tasks = workflow_summary.get("tasks")
            if isinstance(raw_tasks, list):
                for item in raw_tasks:
                    if not isinstance(item, dict):
                        continue
                    state = canonicalize_workflow_task_state(item.get("status") or item.get("state"))
                    if state == "completed":
                        continue
                    item["status"] = director_status_token
                    item["state"] = director_status_token
        elif director_status_token in {"running", "queued"}:
            workflow_summary["state"] = director_status_token

    # Resolve dispatch tasks using shangshuling
    _raw_dispatch = normalized.get("tasks") if isinstance(normalized, dict) else None
    tasks: list[dict[str, Any]] = _raw_dispatch if isinstance(_raw_dispatch, list) else []
    dispatch_tasks, shangshuling_dispatch_meta = _oe.resolve_director_dispatch_tasks(
        workspace_full=workspace_full,
        tasks=tasks,
    )

    if not dispatch_tasks:
        outcome["error"] = "No tasks ready for dispatch"
        return outcome

    dispatch_payload = normalized
    if dispatch_tasks:
        dispatch_payload = dict(normalized)
        dispatch_payload["tasks"] = dispatch_tasks

    # Setup runtime DB path
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    runtime_root_for_db = cache_root_full or os.path.join(workspace_full, get_workspace_metadata_dir_name(), "runtime")
    runtime_db_path = os.path.join(runtime_root_for_db, "state", "workflow.runtime.db")
    try:
        os.makedirs(os.path.dirname(runtime_db_path), exist_ok=True)
        os.environ["KERNELONE_RUNTIME_DB"] = runtime_db_path
    except (RuntimeError, ValueError):
        logger.debug("DEBUG: orchestration_engine.py:{1117} {exc} (swallowed)")

    # Submit workflow
    from polaris.cells.orchestration.workflow_runtime.public.service import PMWorkflowInput, WorkflowConfig

    config = WorkflowConfig.from_env(force_enable=True)
    workflow_run_id = f"{run_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    director_config = {
        "type": str(getattr(args, "director_type", os.environ.get("KERNELONE_DIRECTOR_TYPE", "auto")) or "auto")
        .strip()
        .lower(),
        "adapter": "roles.adapters.director",
        "timeout": int(
            normalize_timeout_seconds(
                getattr(args, "director_timeout", None),
                default=3600,
            )
            or 3600
        ),
        "model": str(getattr(args, "director_model", "") or "").strip(),
        "prompt_profile": str(getattr(args, "prompt_profile", "") or "").strip(),
        "execution_mode": (
            "serial"
            if str(getattr(args, "director_workflow_execution_mode", "parallel") or "parallel").strip().lower()
            == "serial"
            else "parallel"
        ),
        "max_parallel_tasks": max(
            1,
            int(getattr(args, "director_max_parallel_tasks", 3) or 3),
        ),
        "ready_timeout_seconds": max(
            1,
            int(getattr(args, "director_ready_timeout_seconds", 30) or 30),
        ),
        "claim_timeout_seconds": max(
            1,
            int(getattr(args, "director_claim_timeout_seconds", 30) or 30),
        ),
        "phase_timeout_seconds": max(
            1,
            int(getattr(args, "director_phase_timeout_seconds", 900) or 900),
        ),
        "complete_timeout_seconds": max(
            1,
            int(getattr(args, "director_complete_timeout_seconds", 30) or 30),
        ),
        "task_timeout_seconds": max(
            1,
            int(
                getattr(args, "director_task_timeout_seconds", MAX_WORKFLOW_TIMEOUT_SECONDS)
                or MAX_WORKFLOW_TIMEOUT_SECONDS
            ),
        ),
    }

    workflow_input = PMWorkflowInput(
        workspace=workspace_full,
        run_id=workflow_run_id,
        precomputed_payload=dispatch_payload,
        metadata={
            "pm_run_id": run_id,
            "run_dir": run_dir,
            "cache_root_full": cache_root_full,
            "pm_iteration": int(iteration or 0),
            "docs_stage": docs_stage if isinstance(docs_stage, dict) else {},
            "director_config": director_config,
            "max_verification_retries": (
                0 if should_degrade and degrade_settings.get("max_verification_retries") == 0 else 2
            ),
            "integration_qa": (not (should_degrade and degrade_settings.get("integration_qa") is False)),
            "degraded_mode": should_degrade,
        },
    )

    wait_timeout = normalize_timeout_seconds(
        getattr(args, "director_result_timeout", None),
        default=60,
    )
    wait_seconds = None
    if wait_timeout is not None:
        wait_seconds = float(wait_timeout)
        if wait_seconds <= 0:
            wait_seconds = None
    wait_seconds = _pm_workflow_wait_timeout_seconds(
        wait_seconds,
        director_config,
        task_count=len(dispatch_tasks),
    )

    submission = _oe.submit_pm_workflow_sync(
        workflow_input,
        config,
        wait_until_complete=True,
        timeout_seconds=wait_seconds,
        poll_interval_seconds=0.5,
    )
    if not submission.submitted:
        outcome["error"] = str(submission.error or submission.status or "").strip()
        return outcome

    submission_payload = {
        "submitted": bool(submission.submitted),
        "status": str(submission.status or "").strip(),
        "workflow_id": str(submission.workflow_id or "").strip(),
        "workflow_run_id": str(submission.workflow_run_id or "").strip(),
        "error": str(submission.error or "").strip(),
        "details": submission.details if isinstance(submission.details, dict) else {},
    }

    # Build Chief Engineer result (deferred in workflow mode)
    chief_engineer_result = {
        "mode": "workflow",
        "ran": False,
        "reason": "workflow_runtime",
        "summary": "ChiefEngineer stage is deferred to the Workflow workflow chain",
    }
    engine.update_role_status(
        "ChiefEngineer",
        status="idle",
        running=False,
        detail=str(chief_engineer_result.get("summary") or "").strip(),
    )
    engine.update_role_status(
        "Director",
        status="running",
        running=True,
        detail="Director workflow scheduled in Workflow",
        meta={
            "workflow_id": submission.workflow_id,
            "workflow_run_id": submission.workflow_run_id,
            "task_queue": config.task_queue,
        },
    )
    engine.update_role_status(
        "QA",
        status="idle",
        running=False,
        detail="QA workflow is deferred to Workflow after Director completes",
    )

    # Build engine dispatch result
    _raw_dispatch_tasks = dispatch_payload.get("tasks") if isinstance(dispatch_payload, dict) else None
    task_count: int = len(_raw_dispatch_tasks) if isinstance(_raw_dispatch_tasks, list) else 0
    engine_dispatch = {
        "summary": {
            "mode": "workflow",
            "runtime": "workflow",
            "submitted": 1,
            "total": task_count,
            "successes": 0,
            "failures": 0,
            "workflow_id": submission.workflow_id,
            "workflow_run_id": submission.workflow_run_id,
            "task_queue": config.task_queue,
            "namespace": config.namespace,
            "deferred_execution": True,
        },
        "records": [],
        "hard_failure": False,
        "status_updates": {},
        "workflow": submission_payload,
    }

    director_result = {
        "run_id": run_id,
        "status": "queued",
        "mode": "workflow",
        "workflow_id": submission.workflow_id,
        "workflow_run_id": submission.workflow_run_id,
        "successes": task_count,
        "total": task_count,
    }

    # Write workflow state early so runtime readers can pick it up.
    workflow_state_payload = {
        "schema_version": 1,
        "workflow_id": submission.workflow_id,
        "workflow_run_id": submission.workflow_run_id,
        "run_id": run_id,
        "workflow_chain_run_id": workflow_run_id,
        "pm_iteration": int(iteration or 0),
        "workspace": workspace_full,
        "workflow_status": "running",
        "stage": "pm_started",
        "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task_queue": config.task_queue,
        "namespace": config.namespace,
        "details": submission_payload,
    }
    workflow_state_path = write_workflow_state(
        workspace_full,
        cache_root_full,
        workflow_state_payload,
    )

    workflow_exit_code = 0
    final_wait_payload = submission.details.get("final") if isinstance(submission.details, dict) else None
    wait_payload = (
        final_wait_payload
        if isinstance(final_wait_payload, dict)
        else _oe.wait_for_workflow_completion_sync(
            submission.workflow_id,
            timeout_seconds=wait_seconds,
            config=config,
        )
    )
    wait_error = str(wait_payload.get("error") or "").strip()

    workflow_status = _oe.get_workflow_runtime_status(workspace_full, cache_root_full)
    _raw_tasks = dispatch_payload.get("tasks") if isinstance(dispatch_payload, dict) else None
    _dispatch_tasks_list: list[dict[str, Any]] = _raw_tasks if isinstance(_raw_tasks, list) else []
    workflow_summary = _summarize_workflow_execution(
        workflow_status,
        _dispatch_tasks_list,
        task_count,
    )
    workflow_domain_result = _extract_nested_workflow_result(wait_payload)
    workflow_domain_director_status = _workflow_result_director_status(workflow_domain_result)
    if workflow_domain_director_status:
        _apply_workflow_result_summary(
            workflow_summary,
            workflow_domain_result,
            workflow_domain_director_status,
            task_count,
        )

    director_status = "queued"
    wait_status = str(wait_payload.get("status") or "").strip().lower()
    workflow_status_token = str((workflow_status or {}).get("workflow_status") or "").strip().lower()
    if workflow_domain_director_status in {"failed", "blocked", "success", "running", "queued"}:
        director_status = workflow_domain_director_status
    elif (
        workflow_summary.get("total", 0) > 0
        and workflow_summary.get("completed", 0) >= workflow_summary.get("total", 0)
        and workflow_summary.get("failed", 0) == 0
        and workflow_summary.get("blocked", 0) == 0
    ):
        director_status = "success"
    elif (
        wait_status in {"failed", "terminated", "timed_out", "canceled", "cancelled"}
        or workflow_status_token
        in {
            "failed",
            "terminated",
            "timed_out",
            "canceled",
            "cancelled",
        }
        or workflow_summary.get("failed", 0) > 0
    ):
        director_status = "failed"
    elif workflow_summary.get("blocked", 0) > 0:
        director_status = "blocked"
    elif workflow_summary.get("active", 0) > 0:
        director_status = "running"
    elif workflow_summary.get("pending", 0) > 0:
        director_status = "queued"
    elif wait_error:
        director_status = "failed"

    if wait_error and wait_error != "workflow_wait_timeout" and director_status in {"queued", "running"}:
        director_status = "failed"

    if director_status in {"failed", "blocked"}:
        workflow_exit_code = grade_director_exit_code(
            director_status,
            int(workflow_summary.get("completed", 0) or 0),
        )

    summary_text = "Director workflow scheduled in Workflow"
    if director_status == "success":
        summary_text = "Director workflow completed"
    elif director_status == "failed":
        summary_text = "Director workflow failed"
    elif director_status == "blocked":
        summary_text = "Director workflow blocked"
    elif wait_error == "workflow_wait_timeout":
        summary_text = f"Director workflow still running after {int(wait_timeout or 0)}s"

    director_result.update(
        {
            "status": director_status,
            "successes": int(workflow_summary.get("completed", 0)),
            "failures": int(workflow_summary.get("failed", 0)),
            "blocked": int(workflow_summary.get("blocked", 0)),
            "total": int(workflow_summary.get("total", task_count)),
            "summary": summary_text,
            "error": wait_error
            or (
                str(workflow_domain_result.get("qa_status") or workflow_domain_result.get("reason") or "").strip()
                if director_status in {"failed", "blocked"}
                else ""
            ),
        }
    )

    if isinstance(engine_dispatch, dict) and "summary" in engine_dispatch:
        cast("dict[str, Any]", engine_dispatch["summary"]).update(
            {
                "total": int(workflow_summary.get("total", task_count)),
                "successes": int(workflow_summary.get("completed", 0)),
                "failures": int(workflow_summary.get("failed", 0)),
                "blocked": int(workflow_summary.get("blocked", 0)),
                "deferred_execution": director_status in {"queued", "running"},
                "workflow_status": str((workflow_status or {}).get("workflow_status") or "").strip(),
                "workflow_domain_director_status": workflow_domain_director_status,
            }
        )

    if director_status == "success":
        engine.update_role_status(
            "Director",
            status="completed",
            running=False,
            detail=summary_text,
        )
    elif director_status == "failed":
        engine.update_role_status(
            "Director",
            status="failed",
            running=False,
            detail=summary_text,
        )
    elif director_status == "blocked":
        engine.update_role_status(
            "Director",
            status="blocked",
            running=False,
            detail=summary_text,
        )
    else:
        engine.update_role_status(
            "Director",
            status="running",
            running=True,
            detail=summary_text,
        )

    qa_status_token = str((workflow_status or {}).get("qa_workflow_status") or "").strip().lower()
    if qa_status_token in {"completed", "failed", "canceled", "cancelled", "terminated"}:
        qa_status = "completed" if qa_status_token == "completed" else "failed"
        engine.update_role_status(
            "QA",
            status=qa_status,
            running=False,
            detail=f"QA workflow {qa_status_token}",
        )

    # Run integration QA only after workflow reaches terminal status.
    if director_status in {"queued", "running"}:
        docs_stage_payload = docs_stage if isinstance(docs_stage, dict) else {}
        integration_qa_result = {
            "schema_version": 1,
            "enabled": True,
            "ran": False,
            "passed": None,
            "reason": "workflow_execution_incomplete",
            "summary": "Director workflow is not in a terminal state; integration QA deferred.",
            "errors": [wait_error] if wait_error and wait_error != "workflow_wait_timeout" else [],
            "run_id": run_id,
            "pm_iteration": int(iteration or 0),
            "director_task_status": {
                "total": int(workflow_summary.get("total", task_count)),
                "completed": int(workflow_summary.get("completed", 0)),
                "failed": int(workflow_summary.get("failed", 0)),
                "blocked": int(workflow_summary.get("blocked", 0)),
                "todo": int(workflow_summary.get("pending", 0)),
                "in_progress": int(workflow_summary.get("active", 0)),
                "review": 0,
                "needs_continue": 0,
            },
            "result_path": "",
            "runtime_result_path": "",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "docs_stage": {
                "enabled": bool(docs_stage_payload.get("enabled")),
                "active_doc_path": str(docs_stage_payload.get("active_doc_path") or "").strip(),
            },
        }
        deferred_qa_result_path = os.path.join(run_dir, "qa", "integration_qa.result.json")
        runtime_qa_result_path = resolve_artifact_path(
            workspace_full,
            cache_root_full,
            "runtime/results/integration_qa.result.json",
        )
        integration_qa_result["result_path"] = deferred_qa_result_path
        integration_qa_result["runtime_result_path"] = runtime_qa_result_path
        try:
            write_json_atomic(deferred_qa_result_path, integration_qa_result)
            write_json_atomic(runtime_qa_result_path, integration_qa_result)
        except (OSError, TypeError, ValueError) as qa_write_exc:
            _errors = integration_qa_result.get("errors") or []
            integration_qa_result["errors"] = (list(_errors) if isinstance(_errors, list) else []) + [
                f"qa_result_persist_failed: {qa_write_exc}"
            ]
            with contextlib.suppress(OSError, TypeError, ValueError):
                write_json_atomic(deferred_qa_result_path, integration_qa_result)
    else:
        workflow_tasks = workflow_summary.get("tasks")
        qa_tasks = (
            workflow_tasks if isinstance(workflow_tasks, list) and workflow_tasks else dispatch_payload.get("tasks")
        )
        integration_qa_result = _oe.run_post_dispatch_integration_qa(
            args=args,
            workspace_full=workspace_full,
            cache_root_full=cache_root_full,
            run_dir=run_dir,
            run_id=run_id,
            iteration=iteration,
            tasks=qa_tasks if isinstance(qa_tasks, list) else [],
            run_events=run_events,
            dialogue_full=dialogue_full,
            docs_stage=docs_stage,
        )

    qa_failure_reasons = {
        "director_failures_present",
        "integration_qa_failed",
        "integration_qa_error",
        "integration_qa_runtime_error",
    }
    qa_reason = (
        str(integration_qa_result.get("reason") or "").strip() if isinstance(integration_qa_result, dict) else ""
    )
    qa_passed = isinstance(integration_qa_result, dict) and integration_qa_result.get("passed") is True
    if isinstance(integration_qa_result, dict) and (
        integration_qa_result.get("passed") is False or qa_reason in qa_failure_reasons
    ):
        workflow_exit_code = grade_qa_exit_code(director_status, workflow_exit_code)
        engine.update_role_status(
            "QA",
            status="failed",
            running=False,
            detail=qa_reason or "Integration QA failed",
        )

    qa_failed_after_director_success = bool(
        director_status == "success"
        and isinstance(integration_qa_result, dict)
        and (integration_qa_result.get("passed") is False or qa_reason in qa_failure_reasons)
    )

    workflow_summary_tasks = workflow_summary.get("tasks")
    task_rows_all_completed = bool(
        isinstance(workflow_summary_tasks, list)
        and workflow_summary_tasks
        and all(
            isinstance(item, dict)
            and canonicalize_workflow_task_state(item.get("status") or item.get("state")) == "completed"
            for item in workflow_summary_tasks
        )
    )
    summary_counts_all_completed = bool(
        int(workflow_summary.get("total", task_count) or 0) > 0
        and int(workflow_summary.get("completed", 0) or 0) >= int(workflow_summary.get("total", task_count) or 0)
        and int(workflow_summary.get("failed", 0) or 0) == 0
        and int(workflow_summary.get("blocked", 0) or 0) == 0
    )
    all_director_tasks_completed = summary_counts_all_completed or task_rows_all_completed
    workflow_domain_failed = workflow_domain_director_status in {"failed", "blocked"}
    if (
        director_status in {"failed", "blocked"}
        and all_director_tasks_completed
        and qa_passed
        and not workflow_domain_failed
    ):
        director_status = "success"
        workflow_exit_code = 0
        summary_text = "Director workflow completed"
        workflow_summary["state"] = "completed"
        workflow_summary["failed"] = 0
        workflow_summary["blocked"] = 0
        workflow_summary["active"] = 0
        workflow_summary["pending"] = 0
        director_result.update(
            {
                "status": director_status,
                "successes": int(workflow_summary.get("completed", 0)),
                "failures": 0,
                "blocked": 0,
                "total": int(workflow_summary.get("total", task_count)),
                "summary": summary_text,
                "error": "",
            }
        )
        if isinstance(engine_dispatch, dict) and "summary" in engine_dispatch:
            cast("dict[str, Any]", engine_dispatch["summary"]).update(
                {
                    "successes": int(workflow_summary.get("completed", 0)),
                    "failures": 0,
                    "blocked": 0,
                    "deferred_execution": False,
                    "reconciled_from_task_and_qa_evidence": True,
                }
            )
        engine.update_role_status(
            "Director",
            status="completed",
            running=False,
            detail=summary_text,
        )

    # Persist results
    write_json_atomic(run_director_result, director_result)
    runtime_director_result = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/results/director.result.json",
    )
    write_json_atomic(runtime_director_result, director_result)

    # Machine-readable chain outcome for external runners/auditors. Written
    # after QA + reconciliation so every field is terminal-state truth.
    chain_summary = build_chain_summary(
        workflow_exit_code=workflow_exit_code,
        director_result=director_result,
        integration_qa_result=integration_qa_result,
        qa_passed=qa_passed,
        qa_reason=qa_reason,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    write_json_atomic(
        resolve_artifact_path(workspace_full, cache_root_full, "runtime/results/chain_summary.json"),
        chain_summary,
    )
    with contextlib.suppress(OSError):
        write_json_atomic(os.path.join(run_dir, "results", "chain_summary.json"), chain_summary)

    final_qa_status = qa_reason or ("integration_qa_passed" if qa_passed else "integration_qa_not_run")
    final_workflow_status = "completed" if workflow_exit_code == 0 else "failed"
    final_details = cast(
        "dict[str, Any]",
        _canonicalize_terminal_nested_statuses(
            dict(submission_payload),
            final_workflow_status=final_workflow_status,
            final_director_status=director_status,
            final_qa_status=final_qa_status,
        ),
    )
    final_details.update(
        {
            "status": final_workflow_status,
            "director_result": director_result,
            "integration_qa_result": integration_qa_result,
        }
    )
    workflow_state_payload = {
        **workflow_state_payload,
        "workflow_status": final_workflow_status,
        "stage": final_workflow_status,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "director_status": director_status,
        "qa_status": final_qa_status,
        "details": final_details,
    }
    workflow_state_path = write_workflow_state(
        workspace_full,
        cache_root_full,
        workflow_state_payload,
    )

    if workflow_exit_code != 0:
        terminal_error = (
            (
                str(qa_reason or integration_qa_result.get("summary") or "").strip()
                if isinstance(integration_qa_result, dict)
                else ""
            )
            or str(director_result.get("error") or "").strip()
            or str(director_result.get("summary") or "").strip()
            or "WORKFLOW_EXECUTION_FAILED"
        )
        if qa_failed_after_director_success:
            engine.update_role_status(
                "PM",
                status="completed",
                running=False,
                detail="PM contract persisted; downstream QA failed",
            )
        else:
            engine.update_role_status(
                "PM",
                status="failed",
                running=False,
                detail="PM iteration failed during downstream workflow execution",
            )
        if hasattr(engine, "set_phase"):
            engine.set_phase("failed", running=False, error=terminal_error)

    # Update normalized with execution info
    normalized["engine_execution"] = {
        "summary": engine_dispatch.get("summary", {}),
        "records": engine_dispatch.get("records", []),
        "shangshuling_dispatch": shangshuling_dispatch_meta,
        "integration_qa": integration_qa_result,
        "workflow": submission_payload,
    }
    _oe.persist_pm_payload(
        normalized=normalized,
        pm_out_full=pm_out_full,
        run_pm_tasks=run_pm_tasks,
    )

    _oe.emit_event(
        run_events,
        kind="status",
        actor="Engine",
        name="orchestration_workflow_submitted",
        refs={
            "run_id": run_id,
            "phase": "dispatching",
            "files": [run_director_result, runtime_pm_tasks_full],
        },
        summary="Workflow orchestration submitted",
        ok=True,
        output={
            "workflow_id": submission.workflow_id,
            "workflow_run_id": submission.workflow_run_id,
            "task_queue": config.task_queue,
            "task_count": task_count,
            "state_path": workflow_state_path,
        },
    )

    outcome.update(
        {
            "used": True,
            "exit_code": workflow_exit_code,
            "chief_engineer_result": chief_engineer_result,
            "engine_dispatch": engine_dispatch,
            "integration_qa_result": integration_qa_result,
            "director_result": director_result,
        }
    )
    return outcome
