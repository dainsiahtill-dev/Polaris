"""PM Dispatch Pipeline Module.

This module handles task dispatch to Director, result merging, and integration QA.
Designed to be testable with proper dependency injection.

Design invariant: this file MUST NOT contain any import of
``polaris.delivery.*`` at module level.  The local Shangshuling port is
implemented inside the Cell so the module can always be imported in
isolation.  Validated by ``tests/test_pm_dispatch_no_delivery_import.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchCallbacks:
    """Cell-layer abstraction for host-layer callbacks used during dispatch.

    This dataclass severs the Cell→Host reverse-dependency that previously
    required ``run_engine_dispatch`` to receive a ``PolarisEngine`` instance.
    The delivery layer provides an implementation that delegates to the engine;
    tests provide a mock/capture implementation.

    Attributes:
        update_role_status: Called when Director status changes.
            Signature: (role, *, status, running, detail, task_id, task_title, meta)
    """

    update_role_status: Callable[..., None] = field(default_factory=lambda: _nop_update_role_status)


def _nop_update_role_status(role: str, *, status: str, running: bool, detail: str) -> None:
    """No-op fallback when no callback is provided."""
    pass


def _get_chief_engineer_service() -> Callable:
    """Lazy import for chief_engineer.blueprint to avoid module-level cross-Cell coupling."""
    from polaris.cells.chief_engineer.blueprint.public.service import (
        run_pre_dispatch_chief_engineer,
    )

    return run_pre_dispatch_chief_engineer


def _get_workflow_runtime() -> tuple[type, type, Callable]:
    """Lazy import for workflow_runtime to avoid module-level cross-Cell coupling."""
    from polaris.cells.orchestration.workflow_runtime.public.service import (
        PMWorkflowInput,
        WorkflowSubmissionResult,
        submit_pm_workflow_sync,
    )

    return PMWorkflowInput, WorkflowSubmissionResult, submit_pm_workflow_sync


def _get_task_market_services() -> tuple[type, Callable]:
    """Lazy import for runtime.task_market to avoid module-level cross-Cell coupling."""
    from polaris.cells.runtime.task_market.public.contracts import (
        PublishTaskWorkItemCommandV1,
    )
    from polaris.cells.runtime.task_market.public.service import (
        get_task_market_service,
    )

    return PublishTaskWorkItemCommandV1, get_task_market_service


def _get_task_market_revision_services() -> tuple[type, type, type]:
    """Lazy import for task_market revision/change-order contracts."""
    from polaris.cells.runtime.task_market.public.contracts import (
        QueryPlanRevisionsV1,
        RegisterPlanRevisionCommandV1,
        SubmitChangeOrderCommandV1,
    )

    return RegisterPlanRevisionCommandV1, SubmitChangeOrderCommandV1, QueryPlanRevisionsV1


def _get_task_market_consumers() -> tuple[type, type, type]:
    """Lazy import for CE/Director/QA task-market consumers."""
    from polaris.cells.chief_engineer.blueprint.public.service import CEConsumer
    from polaris.cells.director.task_consumer import DirectorExecutionConsumer
    from polaris.cells.qa.audit_verdict.public.service import QAConsumer

    return CEConsumer, DirectorExecutionConsumer, QAConsumer


def _get_shared_quality() -> tuple[Callable, Callable]:
    """Lazy import for shared_quality to avoid circular imports."""
    from polaris.cells.orchestration.pm_planning.public.service import (
        detect_integration_verify_command,
        run_integration_verify_runner,
    )

    return detect_integration_verify_command, run_integration_verify_runner


def _get_io_utils() -> tuple[Callable, Callable]:
    """Lazy import for events to avoid circular imports."""
    from polaris.kernelone.events import emit_dialogue, emit_event

    return emit_event, emit_dialogue


def _get_tasks_utils() -> tuple[Callable, Callable]:
    """Return task utility functions from the Cell's own port module.

    Delivery layer is intentionally never imported here; all pure logic
    lives in ``pm_task_utils``.
    """
    from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
        get_director_task_status_summary,
        to_bool,
    )

    return get_director_task_status_summary, to_bool


def _get_shangshuling_port() -> Any:
    """Return the cell-local Shangshuling port."""
    from polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry import (
        get_shangshuling_port,
    )

    return get_shangshuling_port()


def _get_traceability_safety() -> tuple[Any, Any, Any]:
    """Lazy import for traceability safety helpers."""
    from polaris.kernelone.traceability.internal.safety import (
        safe_find_node,
        safe_link,
        safe_register_node,
    )

    return safe_find_node, safe_link, safe_register_node


def _resolve_task_market_mode() -> str:
    """Resolve task-market mode to a stable internal value."""
    rollout_mode = _resolve_task_market_rollout_mode()
    if rollout_mode in {"mainline", "mainline-design", "mainline-full", "mainline-durable"}:
        return "mainline"
    if rollout_mode == "shadow":
        return "shadow"
    return "off"


def _resolve_task_market_rollout_mode() -> str:
    """Resolve task-market rollout phase from environment."""
    raw_mode = str(os.environ.get("KERNELONE_TASK_MARKET_MODE", "off") or "off").strip().lower()
    if raw_mode in {"off", "shadow", "mainline", "mainline-design", "mainline-full", "mainline-durable"}:
        return raw_mode
    if raw_mode == "mainline-exec":
        # Preserve forward compatibility with docs that mention this phase.
        return "mainline-full"
    return "off"


def _hash_payload(payload: Any) -> str:
    try:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(payload)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _build_revision_context(
    *,
    workspace_full: str,
    run_id: str,
    tasks: list[dict[str, Any]],
    normalized: dict[str, Any] | None = None,
    docs_stage: dict[str, Any] | None = None,
) -> dict[str, str]:
    normalized_payload = normalized if isinstance(normalized, dict) else {}
    docs_payload = docs_stage if isinstance(docs_stage, dict) else {}
    default_plan_id = (
        str(normalized_payload.get("project_id") or normalized_payload.get("initiative_id") or "").strip()
        or str(docs_payload.get("active_doc_path") or "").strip()
        or f"workspace::{workspace_full}"
    )
    plan_id = str(normalized_payload.get("plan_id") or default_plan_id).strip()
    task_projection = [
        {
            "id": str(task.get("id") or "").strip(),
            "title": str(task.get("title") or "").strip(),
            "goal": str(task.get("goal") or "").strip(),
            "depends_on": task.get("depends_on") if isinstance(task.get("depends_on"), list) else [],
            "scope_paths": task.get("scope_paths") if isinstance(task.get("scope_paths"), list) else [],
            "target_files": task.get("target_files") if isinstance(task.get("target_files"), list) else [],
        }
        for task in tasks
        if isinstance(task, dict)
    ]
    requirement_basis = {
        "overall_goal": str(normalized_payload.get("overall_goal") or "").strip(),
        "focus": str(normalized_payload.get("focus") or "").strip(),
        "notes": str(normalized_payload.get("notes") or "").strip(),
        "tasks": task_projection,
        "docs_active_path": str(docs_payload.get("active_doc_path") or "").strip(),
    }
    requirement_digest = _hash_payload(requirement_basis)
    constraint_basis = {
        "docs_enabled": bool(docs_payload.get("enabled")),
        "dispatch_task_count": len(task_projection),
        "run_id": str(run_id or "").strip(),
    }
    constraint_digest = _hash_payload(constraint_basis)
    plan_revision_id = str(normalized_payload.get("plan_revision_id") or "").strip() or f"rev-{requirement_digest[:12]}"
    return {
        "plan_id": plan_id,
        "plan_revision_id": plan_revision_id,
        "requirement_digest": requirement_digest,
        "constraint_digest": constraint_digest,
    }


def _extract_task_dependencies(task: dict[str, Any]) -> tuple[str, ...]:
    raw_depends_on = task.get("depends_on")
    if isinstance(raw_depends_on, list):
        source = raw_depends_on
    else:
        raw_dependencies = task.get("dependencies")
        source = raw_dependencies if isinstance(raw_dependencies, list) else []
    normalized = [str(item).strip() for item in source if str(item).strip()]
    deduped: list[str] = []
    for item in normalized:
        if item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def _sync_revision_and_change_order(
    *,
    service: Any,
    workspace_full: str,
    trace_id: str,
    tasks: list[dict[str, Any]],
    revision_context: dict[str, str],
    source_role: str = "PM",
    docs_stage: dict[str, Any] | None = None,
) -> None:
    """Best-effort revision registration and change-order submission."""
    if not (
        hasattr(service, "register_plan_revision")
        and hasattr(service, "query_plan_revisions")
        and hasattr(service, "submit_change_order")
    ):
        return
    try:
        register_cmd, change_order_cmd, query_cmd = _get_task_market_revision_services()
    except (ImportError, RuntimeError, ValueError):
        return

    plan_id = str(revision_context.get("plan_id") or "").strip()
    plan_revision_id = str(revision_context.get("plan_revision_id") or "").strip()
    if not plan_id or not plan_revision_id:
        return

    try:
        history = service.query_plan_revisions(query_cmd(workspace=workspace_full, plan_id=plan_id, limit=1))
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        history = ()

    latest = history[0] if history else {}
    latest_revision_id = str((latest or {}).get("plan_revision_id") or "").strip()
    if latest_revision_id == plan_revision_id:
        return

    service.register_plan_revision(
        register_cmd(
            workspace=workspace_full,
            plan_id=plan_id,
            plan_revision_id=plan_revision_id,
            parent_revision_id=latest_revision_id,
            source_role=source_role,
            requirement_digest=revision_context.get("requirement_digest", ""),
            constraint_digest=revision_context.get("constraint_digest", ""),
            metadata={"registered_via": "pm_dispatch"},
        )
    )

    if not latest_revision_id:
        return

    docs_payload = docs_stage if isinstance(docs_stage, dict) else {}
    change_type = "doc_patch" if bool(docs_payload.get("enabled")) else "manual_task_edit"
    affected_task_ids = tuple(
        str(task.get("id") or "").strip()
        for task in tasks
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    )
    service.submit_change_order(
        change_order_cmd(
            workspace=workspace_full,
            plan_id=plan_id,
            from_revision_id=latest_revision_id,
            to_revision_id=plan_revision_id,
            source_role=source_role,
            change_type=change_type,
            trace_id=trace_id,
            summary="PM dispatch detected revision drift",
            affected_task_ids=affected_task_ids,
            metadata={"submitted_via": "pm_dispatch"},
        )
    )


def _read_positive_int_env(name: str, *, default: int, minimum: int = 1, maximum: int = 3600) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _read_bool_env(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _run_inline_task_market_consumers(
    *,
    workspace_full: str,
    run_id: str,
    iteration: int,
    published_task_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Run one bounded PM->CE->Director->QA cycle for mainline-full mode."""
    rollout_mode = _resolve_task_market_rollout_mode()
    if rollout_mode != "mainline-full":
        return {
            "enabled": False,
            "rollout_mode": rollout_mode,
            "reason": "not_mainline_full",
            "ok": True,
        }

    try:
        ce_consumer_type, director_consumer_type, qa_consumer_type = _get_task_market_consumers()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": rollout_mode,
            "ok": False,
            "reason": "consumer_import_failed",
            "error": str(exc),
        }

    worker_suffix = f"{iteration}-{_hash_payload(run_id)[:8]}"
    max_cycles = _read_positive_int_env(
        "KERNELONE_TASK_MARKET_MAINLINE_FULL_MAX_CYCLES",
        default=2,
        minimum=1,
        maximum=20,
    )
    design_timeout = _read_positive_int_env(
        "KERNELONE_TASK_MARKET_DESIGN_VISIBILITY_TIMEOUT_SECONDS",
        default=900,
        minimum=30,
        maximum=7200,
    )
    exec_timeout = _read_positive_int_env(
        "KERNELONE_TASK_MARKET_EXEC_VISIBILITY_TIMEOUT_SECONDS",
        default=1800,
        minimum=30,
        maximum=7200,
    )
    qa_timeout = _read_positive_int_env(
        "KERNELONE_TASK_MARKET_QA_VISIBILITY_TIMEOUT_SECONDS",
        default=900,
        minimum=30,
        maximum=7200,
    )
    enable_safe_parallel = _read_bool_env(
        "KERNELONE_TASK_MARKET_ENABLE_SAFE_PARALLEL_DIRECTOR",
        default=False,
    )

    try:
        ce_consumer = ce_consumer_type(
            workspace=workspace_full,
            worker_id=f"pm_inline_ce_{worker_suffix}",
            visibility_timeout_seconds=design_timeout,
            poll_interval=0.05,
        )
        director_consumer = director_consumer_type(
            workspace=workspace_full,
            worker_id=f"pm_inline_director_{worker_suffix}",
            visibility_timeout_seconds=exec_timeout,
            poll_interval=0.05,
            enable_safe_parallel=enable_safe_parallel,
        )
        qa_consumer = qa_consumer_type(
            workspace=workspace_full,
            worker_id=f"pm_inline_qa_{worker_suffix}",
            visibility_timeout_seconds=qa_timeout,
            poll_interval=0.05,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": rollout_mode,
            "ok": False,
            "reason": "consumer_init_failed",
            "error": str(exc),
        }

    ce_results: list[dict[str, Any]] = []
    director_results: list[dict[str, Any]] = []
    qa_results: list[dict[str, Any]] = []
    terminal_status_by_task: dict[str, str] = {}
    cycles_ran = 0
    loop_error = ""

    for cycle_index in range(max_cycles):
        cycles_ran = cycle_index + 1
        try:
            ce_cycle = ce_consumer.poll_once()
            director_cycle = director_consumer.poll_once()
            qa_cycle = qa_consumer.poll_once()
        except Exception as exc:
            loop_error = str(exc)
            logger.exception("mainline-full inline consumer loop failed: %s", exc)
            break

        ce_results.extend(ce_cycle)
        director_results.extend(director_cycle)
        qa_results.extend(qa_cycle)

        for qa_item in qa_cycle:
            if not isinstance(qa_item, dict):
                continue
            task_id = str(qa_item.get("task_id") or "").strip()
            task_status = str(qa_item.get("status") or "").strip().lower()
            if task_id and task_status in {"resolved", "rejected", "dead_letter"}:
                terminal_status_by_task[task_id] = task_status

        if not ce_cycle and not director_cycle and not qa_cycle:
            break

    published_ids = tuple(dict.fromkeys(task_id for task_id in published_task_ids if task_id))
    unresolved_ids = [task_id for task_id in published_ids if task_id not in terminal_status_by_task]
    rejected_ids = [
        task_id
        for task_id, task_status in terminal_status_by_task.items()
        if task_status in {"rejected", "dead_letter"}
    ]
    reconciliation_result: dict[str, Any] = {"ok": False, "reason": "not_available"}
    try:
        _, get_task_market_service = _get_task_market_services()
        task_market_service = get_task_market_service()
        if hasattr(task_market_service, "reconcile_parent_statuses"):
            raw_result = task_market_service.reconcile_parent_statuses(workspace_full)
            if isinstance(raw_result, dict):
                reconciliation_result = {"ok": True, **raw_result}
            else:
                reconciliation_result = {"ok": True, "result": raw_result}
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        reconciliation_result = {"ok": False, "reason": "reconcile_error", "error": str(exc)}

    has_worker_failure = any(
        not bool(result.get("ok", False))
        for result in (ce_results + director_results + qa_results)
        if isinstance(result, dict)
    )
    ok = not has_worker_failure and not unresolved_ids and not rejected_ids and not loop_error
    reason = "mainline_full_complete" if ok else "mainline_full_incomplete"
    if loop_error:
        reason = "mainline_full_loop_error"

    return {
        "enabled": True,
        "rollout_mode": rollout_mode,
        "ok": ok,
        "reason": reason,
        "max_cycles": max_cycles,
        "cycles_ran": cycles_ran,
        "published_task_ids": published_ids,
        "terminal_status_by_task": dict(terminal_status_by_task),
        "unresolved_task_ids": tuple(unresolved_ids),
        "rejected_task_ids": tuple(rejected_ids),
        "ce_results": tuple(ce_results),
        "director_results": tuple(director_results),
        "qa_results": tuple(qa_results),
        "loop_error": loop_error,
        "reconciliation": reconciliation_result,
    }


def _start_durable_consumer_loops(
    *,
    workspace_full: str,
    run_id: str,
) -> dict[str, Any]:
    """Start durable consumer daemon threads for mainline-durable mode.

    PM publishes and returns immediately; background consumers handle the
    rest.  The outbox relay also runs as a daemon thread.
    """
    try:
        _, get_task_market_service = _get_task_market_services()
        service = get_task_market_service()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": "mainline-durable",
            "ok": False,
            "reason": "service_import_failed",
            "error": str(exc),
        }

    try:
        started = service.start_consumer_loops(workspace_full)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "enabled": True,
            "rollout_mode": "mainline-durable",
            "ok": False,
            "reason": "consumer_start_failed",
            "error": str(exc),
        }

    status = service.query_consumer_loop_status(workspace_full)
    return {
        "enabled": True,
        "rollout_mode": "mainline-durable",
        "ok": started,
        "reason": "durable_consumers_started" if started else "durable_consumers_already_running",
        "consumer_status": status,
    }


def _mainline_publish_dispatch_tasks_to_task_market(
    *,
    workspace_full: str,
    run_id: str,
    tasks: list[dict[str, Any]],
    run_dir: str = "",
    cache_root_full: str = "",
    iteration: int = 0,
    normalized: dict[str, Any] | None = None,
    docs_stage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Publish dispatch tasks to task market in mainline mode.

    In mainline mode, tasks are published to the design queue
    (PENDING_DESIGN) for CE consumption, not directly to execution.
    CE consumer will claim, generate blueprint, and advance to PENDING_EXEC.

    Args:
        workspace_full: Workspace path for task market operations.
        run_id: Run identifier for the dispatch session.
        tasks: List of dispatch tasks to publish.
        run_dir: Run directory for CE consumer context.
        cache_root_full: Cache root for CE consumer context.
        iteration: PM iteration number for CE consumer context.
    """
    mode = _resolve_task_market_mode()
    rollout_mode = _resolve_task_market_rollout_mode()
    if mode != "mainline":
        return []

    results: list[dict[str, Any]] = []

    try:
        publish_contract_type, get_task_market_service = _get_task_market_services()
        service = get_task_market_service()
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.debug("task_market mainline publish unavailable: %s", exc)
        return results

    revision_context = _build_revision_context(
        workspace_full=workspace_full,
        run_id=run_id,
        tasks=tasks,
        normalized=normalized,
        docs_stage=docs_stage,
    )
    _sync_revision_and_change_order(
        service=service,
        workspace_full=workspace_full,
        trace_id=run_id,
        tasks=tasks,
        revision_context=revision_context,
        source_role="PM",
        docs_stage=docs_stage,
    )

    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        trace_id = str(task.get("trace_id") or run_id).strip() or run_id
        payload = {
            "title": str(task.get("title") or task.get("goal") or task_id).strip(),
            "goal": str(task.get("goal") or task.get("title") or "").strip(),
            "scope_paths": task.get("scope_paths") if isinstance(task.get("scope_paths"), list) else [],
            "target_files": task.get("target_files") if isinstance(task.get("target_files"), list) else [],
            "acceptance_criteria": (
                task.get("acceptance_criteria") if isinstance(task.get("acceptance_criteria"), list) else []
            ),
            "task": dict(task),
            # CE consumer PreflightContext requires these runtime fields.
            "workspace": workspace_full,
            "run_dir": run_dir,
            "cache_root": cache_root_full,
            "run_id": run_id,
            "pm_iteration": iteration,
        }
        try:
            command = publish_contract_type(
                workspace=workspace_full,
                trace_id=trace_id,
                run_id=run_id,
                task_id=task_id,
                stage="pending_design",
                source_role="PM",
                payload=payload,
                metadata={
                    "dispatch_mode": mode,
                    "dispatch_rollout_mode": rollout_mode,
                    "published_via": "mainline",
                    "plan_id": revision_context["plan_id"],
                    "plan_revision_id": revision_context["plan_revision_id"],
                },
                plan_id=revision_context["plan_id"],
                plan_revision_id=revision_context["plan_revision_id"],
                root_task_id=str(task.get("root_task_id") or task_id).strip() or task_id,
                parent_task_id=str(task.get("parent_task_id") or task.get("parent_id") or "").strip(),
                is_leaf=bool(task.get("is_leaf", True)),
                depends_on=_extract_task_dependencies(task),
                requirement_digest=revision_context["requirement_digest"],
                constraint_digest=revision_context["constraint_digest"],
                change_policy=str(task.get("change_policy") or "strict").strip().lower() or "strict",
                compensation_group_id=str(task.get("compensation_group_id") or revision_context["plan_id"]).strip(),
            )
            result = service.publish_work_item(command)
            results.append(
                {
                    "task_id": task_id,
                    "ok": result.ok,
                    "status": result.status,
                    "reason": result.reason,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("task_market mainline publish failed: task_id=%s error=%s", task_id, exc)
            results.append(
                {
                    "task_id": task_id,
                    "ok": False,
                    "status": "",
                    "reason": str(exc),
                }
            )

    # For mainline-durable: start background consumer daemon threads after publishing.
    if rollout_mode == "mainline-durable":
        durable_result = _start_durable_consumer_loops(
            workspace_full=workspace_full,
            run_id=run_id,
        )
        logger.info(
            "mainline-durable consumer loop start: ok=%s reason=%s",
            durable_result.get("ok"),
            durable_result.get("reason"),
        )

    return results


def _shadow_publish_dispatch_tasks_to_task_market(
    *,
    workspace_full: str,
    run_id: str,
    tasks: list[dict[str, Any]],
    normalized: dict[str, Any] | None = None,
    docs_stage: dict[str, Any] | None = None,
) -> None:
    """Best-effort task market publication for dispatch tasks.

    This path is intentionally non-blocking for the current rollout:
    - mode=off: disabled
    - mode=shadow/mainline*: publish work items, but do not fail dispatch on errors
    """
    mode = _resolve_task_market_mode()
    if mode == "off":
        return
    if not isinstance(tasks, list) or not tasks:
        return

    try:
        publish_contract_type, get_task_market_service = _get_task_market_services()
        service = get_task_market_service()
    except (ImportError, RuntimeError, ValueError):
        return

    revision_context = _build_revision_context(
        workspace_full=workspace_full,
        run_id=run_id,
        tasks=tasks,
        normalized=normalized,
        docs_stage=docs_stage,
    )
    _sync_revision_and_change_order(
        service=service,
        workspace_full=workspace_full,
        trace_id=run_id,
        tasks=tasks,
        revision_context=revision_context,
        source_role="PM",
        docs_stage=docs_stage,
    )

    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        trace_id = str(task.get("trace_id") or run_id).strip() or run_id
        payload = {
            "title": str(task.get("title") or task.get("goal") or task_id).strip(),
            "goal": str(task.get("goal") or task.get("title") or "").strip(),
            "scope_paths": task.get("scope_paths") if isinstance(task.get("scope_paths"), list) else [],
            "target_files": task.get("target_files") if isinstance(task.get("target_files"), list) else [],
            "acceptance_criteria": (
                task.get("acceptance_criteria") if isinstance(task.get("acceptance_criteria"), list) else []
            ),
            "task": dict(task),
        }
        try:
            command = publish_contract_type(
                workspace=workspace_full,
                trace_id=trace_id,
                run_id=run_id,
                task_id=task_id,
                stage="pending_exec",
                source_role="PM",
                payload=payload,
                metadata={
                    "dispatch_mode": mode,
                    "plan_id": revision_context["plan_id"],
                    "plan_revision_id": revision_context["plan_revision_id"],
                },
                plan_id=revision_context["plan_id"],
                plan_revision_id=revision_context["plan_revision_id"],
                root_task_id=str(task.get("root_task_id") or task_id).strip() or task_id,
                parent_task_id=str(task.get("parent_task_id") or task.get("parent_id") or "").strip(),
                is_leaf=bool(task.get("is_leaf", True)),
                depends_on=_extract_task_dependencies(task),
                requirement_digest=revision_context["requirement_digest"],
                constraint_digest=revision_context["constraint_digest"],
                change_policy=str(task.get("change_policy") or "strict").strip().lower() or "strict",
                compensation_group_id=str(task.get("compensation_group_id") or revision_context["plan_id"]).strip(),
            )
            service.publish_work_item(command)
        except (RuntimeError, ValueError) as exc:
            logger.debug("task_market shadow publish skipped: task_id=%s error=%s", task_id, exc)


def resolve_director_dispatch_tasks(
    *,
    workspace_full: str,
    tasks: list[dict[str, Any]],
    shangshuling_port: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve tasks ready for Director dispatch.

    Uses the shangshuling port to sync and filter tasks.  The port is loaded
    lazily from the Cell-local registry implementation when *shangshuling_port*
    is not provided, which keeps tests free of any delivery dependency.

    Args:
        workspace_full: Workspace path
        tasks: List of PM tasks
        shangshuling_port: Optional pre-injected ShangshulingPort; when None,
            the Cell-local registry port is loaded lazily.

    Returns:
        Tuple of (dispatch_tasks, metadata)
    """
    meta: dict[str, Any] = {
        "enabled": False,
        "sync_count": 0,
        "ready_count": 0,
        "selected_count": len(tasks) if isinstance(tasks, list) else 0,
    }
    if not isinstance(tasks, list) or not tasks:
        return [], meta

    port = shangshuling_port if shangshuling_port is not None else _get_shangshuling_port()

    try:
        sync_count = int(port.sync_tasks_to_shangshuling(workspace_full, tasks) or 0)
        ready_tasks = port.get_shangshuling_ready_tasks(
            workspace_full,
            limit=max(6, len(tasks) * 2),
        )
        ready_ids = {
            str(item.get("id") or "").strip()
            for item in ready_tasks
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        selected = [task for task in tasks if isinstance(task, dict) and str(task.get("id") or "").strip() in ready_ids]
        meta["enabled"] = True
        meta["sync_count"] = sync_count
        meta["ready_count"] = len(ready_tasks)
        return selected, meta
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return tasks, meta


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


def run_dispatch_pipeline(
    *,
    callbacks: DispatchCallbacks | None = None,
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
    docs_stage: dict[str, Any] | None = None,
    # Deprecated: host-layer parameters kept for backward compatibility with existing tests.
    args: Any | None = None,
    engine: Any | None = None,
) -> dict[str, Any]:
    """Execute the full dispatch pipeline.

    This is the main entry point for running the dispatch pipeline including
    chief engineer preflight, engine dispatch, and integration QA.

    Args:
        callbacks: Cell-layer callback abstraction. When None, a no-op callback is used.
            Prefer passing an explicit ``DispatchCallbacks`` over ``args``/``engine``.
        workspace_full: Workspace path
        cache_root_full: Cache root path
        run_dir: Run directory
        run_id: Run identifier
        iteration: Iteration number
        normalized: Normalized PM payload
        run_events: Events file path
        dialogue_full: Dialogue file path
        runtime_pm_tasks_full: Runtime PM tasks path
        pm_out_full: PM output path
        run_pm_tasks: Run PM tasks path
        run_director_result: Director result path
        docs_stage: Docs stage configuration
        args: DEPRECATED. Host-layer argparse.Namespace — use ``callbacks`` instead.
        engine: DEPRECATED. Host-layer PolarisEngine — use ``callbacks`` instead.

    Returns:
        Pipeline outcome dict
    """
    if args is not None:
        warnings.warn(
            "run_dispatch_pipeline: args= is deprecated; pass callbacks=DispatchCallbacks(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    if engine is not None:
        warnings.warn(
            "run_dispatch_pipeline: engine= is deprecated; pass callbacks=DispatchCallbacks(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    # Build callbacks — prefer explicit callbacks, bridge from engine if needed.
    # This keeps backward compatibility with existing tests that pass engine=.
    if callbacks is None:
        if engine is not None and hasattr(engine, "update_role_status"):
            callbacks = DispatchCallbacks(
                update_role_status=lambda role, *, status, running, detail, **kw: engine.update_role_status(
                    role, status=status, running=running, detail=detail
                ),
            )
        else:
            callbacks = DispatchCallbacks()

    outcome: dict[str, Any] = {
        "used": False,
        "exit_code": 0,
        "chief_engineer_result": None,
        "engine_dispatch": None,
        "integration_qa_result": None,
        "director_result": None,
        "error": "",
    }

    tasks = normalized.get("tasks") if isinstance(normalized.get("tasks"), list) else []
    dispatch_tasks, _shangshuling_dispatch_meta = resolve_director_dispatch_tasks(
        workspace_full=workspace_full,
        tasks=tasks,  # type: ignore[arg-type]  # tasks is guaranteed list after isinstance check
    )

    if not dispatch_tasks:
        outcome["error"] = "No tasks ready for dispatch"
        return outcome

    outcome["chief_engineer_result"] = run_chief_engineer_preflight(
        args=args,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_dir=run_dir,
        run_id=run_id,
        pm_iteration=iteration,
        tasks=dispatch_tasks,
        run_events=run_events,
        dialogue_full=dialogue_full,
    )

    # Traceability: register blueprint nodes per task after CE preflight
    safe_find_node, safe_link, safe_register_node = _get_traceability_safety()
    trace_service = None
    if isinstance(normalized, dict):
        trace_service = normalized.get("trace_service")
    if trace_service is not None and isinstance(outcome.get("chief_engineer_result"), dict):
        chief_engineer_result = outcome["chief_engineer_result"]
        for task in dispatch_tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue
            blueprint_id = f"bp-{run_id}-{task_id}"
            task["blueprint_id"] = blueprint_id
            bp_node = safe_register_node(
                trace_service,
                node_kind="blueprint",
                role="chief_engineer",
                external_id=blueprint_id,
                content=json.dumps(chief_engineer_result, ensure_ascii=False)[:1024],
            )
            task_node = safe_find_node(trace_service, task_id, "task")
            if task_node is None:
                task_node = safe_register_node(
                    trace_service,
                    node_kind="task",
                    role="pm",
                    external_id=task_id,
                    content=json.dumps(task, ensure_ascii=False)[:1024],
                )
            if bp_node is not None and task_node is not None:
                safe_link(trace_service, bp_node, task_node, "implements")

    # Persist minimal blueprint so traceability gate 14 can verify approval status
    for task in dispatch_tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        blueprint_id = f"bp-{run_id}-{task_id}"
        task["blueprint_id"] = blueprint_id
        try:
            from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
                BlueprintPersistence,
            )

            bp_persistence = BlueprintPersistence(workspace_full)
            bp_persistence.save(
                blueprint_id,
                {
                    "blueprint_id": blueprint_id,
                    "status": "approved",
                    "task_id": task_id,
                    "run_id": run_id,
                    "created_at": datetime.now().isoformat(),
                },
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug("blueprint persistence skipped for task_id=%s", task_id)

    mode = _resolve_task_market_mode()
    rollout_mode = _resolve_task_market_rollout_mode()

    # Publish to task market based on mode.
    # - off: disabled (do nothing)
    # - shadow: best-effort publish to PENDING_EXEC for monitoring (non-blocking)
    # - mainline: publish to PENDING_DESIGN for CE consumption, then skip
    #   engine dispatch (PM's responsibility ends at task publication)
    if mode == "mainline":
        mainline_results = _mainline_publish_dispatch_tasks_to_task_market(
            workspace_full=workspace_full,
            run_id=run_id,
            tasks=dispatch_tasks,
            run_dir=run_dir,
            cache_root_full=cache_root_full,
            iteration=iteration,
            normalized=normalized,
            docs_stage=docs_stage,
        )
        outcome["task_market_results"] = mainline_results
        outcome["used"] = True
        publish_failed_task_ids = tuple(
            str(item.get("task_id") or "").strip()
            for item in mainline_results
            if isinstance(item, dict) and not bool(item.get("ok", False))
        )
        if rollout_mode == "mainline-full":
            published_task_ids = tuple(
                str(task.get("id") or "").strip()
                for task in dispatch_tasks
                if isinstance(task, dict) and str(task.get("id") or "").strip()
            )
            inline_result = _run_inline_task_market_consumers(
                workspace_full=workspace_full,
                run_id=run_id,
                iteration=iteration,
                published_task_ids=published_task_ids,
            )
            director_results = (
                list(inline_result.get("director_results", ()))
                if isinstance(inline_result.get("director_results"), tuple | list)
                else []
            )
            director_successes = sum(
                1 for item in director_results if isinstance(item, dict) and bool(item.get("ok", False))
            )
            outcome["task_market_inline"] = inline_result
            outcome["engine_dispatch"] = {
                "skipped": True,
                "reason": "task_market_mainline_full",
            }
            outcome["director_result"] = {
                "run_id": run_id,
                "mode": "task_market_mainline_full",
                "total": len(director_results),
                "successes": director_successes,
            }
            outcome["integration_qa_result"] = {
                "enabled": True,
                "ran": True,
                "passed": bool(inline_result.get("ok", False)) and not publish_failed_task_ids,
                "reason": str(inline_result.get("reason") or "task_market_mainline_full"),
                "summary": "Task-market inline CE/Director/QA flow executed",
                "qa_results": inline_result.get("qa_results", ()),
                "unresolved_task_ids": inline_result.get("unresolved_task_ids", ()),
                "rejected_task_ids": inline_result.get("rejected_task_ids", ()),
                "publish_failed_task_ids": publish_failed_task_ids,
            }
            success = bool(outcome["integration_qa_result"]["passed"])
            outcome["exit_code"] = 0 if success else 1
            outcome["error"] = "" if success else "task_market_mainline_full_failed"
            return outcome

        # In mainline mode, PM's job is done once tasks are queued for design.
        # CE consumer will claim PENDING_DESIGN and generate blueprints.
        outcome["exit_code"] = 0 if not publish_failed_task_ids else 1
        outcome["error"] = "" if outcome["exit_code"] == 0 else "task_market_publish_failed"
        return outcome
    elif mode == "shadow":
        _shadow_publish_dispatch_tasks_to_task_market(
            workspace_full=workspace_full,
            run_id=run_id,
            tasks=dispatch_tasks,
            normalized=normalized,
            docs_stage=docs_stage,
        )

    engine_dispatch_result = run_engine_dispatch(
        callbacks=callbacks,
        workspace_full=workspace_full,
        run_id=run_id,
        iteration=iteration,
        tasks=dispatch_tasks,
        run_events=run_events,
        dialogue_full=dialogue_full,
    )
    outcome["engine_dispatch"] = engine_dispatch_result
    outcome["director_result"] = engine_dispatch_result.get("director_result")
    outcome["exit_code"] = engine_dispatch_result.get("exit_code", 0)

    if outcome["exit_code"] == 0:
        integration_qa_result = run_integration_qa(
            workspace_full=workspace_full,
            cache_root_full=cache_root_full,
            run_dir=run_dir,
            run_id=run_id,
            iteration=iteration,
            tasks=dispatch_tasks,
            run_events=run_events,
            dialogue_full=dialogue_full,
            docs_stage=docs_stage,
        )
        outcome["integration_qa_result"] = integration_qa_result

    outcome["used"] = True
    return outcome


def run_chief_engineer_preflight(
    *,
    args: Any,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    pm_iteration: int,
    tasks: list[dict[str, Any]],
    run_events: str,
    dialogue_full: str,
) -> dict[str, Any] | None:
    """Run Chief Engineer preflight checks.

    Args:
        args: Command line arguments
        workspace_full: Workspace path
        cache_root_full: Cache root path
        run_dir: Run directory
        run_id: Run identifier
        pm_iteration: PM iteration number
        tasks: Tasks to preflight
        run_events: Runtime event log path
        dialogue_full: Dialogue log path

    Returns:
        Chief engineer result or None
    """
    _run_pre_dispatch = (
        run_pre_dispatch_chief_engineer
        if run_pre_dispatch_chief_engineer is not None
        else _get_chief_engineer_service()
    )
    return _run_pre_dispatch(
        args=args,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_dir=run_dir,
        run_id=run_id,
        pm_iteration=pm_iteration,
        tasks=tasks,
        run_events=run_events,
        dialogue_full=dialogue_full,
    )


def _resolve_workflow_submit_fn(explicit_submit_fn: Callable[..., Any] | None) -> Callable[..., Any]:
    """Resolve the workflow submit function without import-time side effects."""
    if explicit_submit_fn is not None:
        return explicit_submit_fn
    if submit_pm_workflow_sync is not None:
        return submit_pm_workflow_sync
    return _submit_pm_workflow_sync_resolve()


def _build_workflow_input(
    workflow_input_type: Any,
    *,
    workspace_full: str,
    run_id: str,
    iteration: int,
    tasks: list[dict[str, Any]],
) -> Any:
    """Build the workflow submission input object."""
    return workflow_input_type(
        workspace=workspace_full,
        run_id=run_id,
        precomputed_payload={"tasks": tasks},
        metadata={"iteration": int(iteration or 0)},
    )


def _build_director_workflow_result(
    *,
    run_id: str,
    task_count: int,
    workflow_result: Any,
) -> dict[str, Any]:
    """Normalize workflow submission outcome into Director result payload."""
    submitted = bool(getattr(workflow_result, "submitted", False))
    status = str(getattr(workflow_result, "status", "") or "").strip()
    error_text = str(getattr(workflow_result, "error", "") or "").strip()
    details = getattr(workflow_result, "details", {})
    normalized_details = details if isinstance(details, dict) else {}

    if not submitted:
        return {
            "run_id": run_id,
            "status": status or "failed",
            "mode": "workflow",
            "workflow_id": str(getattr(workflow_result, "workflow_id", "") or "").strip(),
            "workflow_run_id": str(getattr(workflow_result, "workflow_run_id", "") or "").strip(),
            "summary": str(error_text or status).strip(),
            "error": error_text,
            "details": normalized_details,
            "successes": 0,
            "total": task_count,
        }

    return {
        "run_id": run_id,
        "status": "queued",
        "mode": "workflow",
        "workflow_id": str(getattr(workflow_result, "workflow_id", "") or "").strip(),
        "workflow_run_id": str(getattr(workflow_result, "workflow_run_id", "") or "").strip(),
        "summary": "Director workflow scheduled in Workflow",
        "error": error_text,
        "details": normalized_details,
        "successes": task_count,
        "total": task_count,
    }


def _emit_engine_dispatch_status(
    *,
    run_events: str,
    run_id: str,
    iteration: int,
    task_count: int,
    name: str,
    summary: str,
    ok: bool,
    status: str,
    error: str = "",
) -> None:
    """Emit a structured engine dispatch status event."""
    from polaris.kernelone.events import emit_event

    emit_event(
        run_events,
        kind="status",
        actor="PM",
        name=name,
        refs={"run_id": run_id, "phase": "dispatch"},
        summary=summary,
        ok=ok,
        output={
            "task_count": task_count,
            "status": status,
            "iteration": int(iteration or 0),
        },
        error=str(error or "").strip(),
    )


def run_engine_dispatch(
    *,
    callbacks: DispatchCallbacks | None = None,
    workspace_full: str,
    run_id: str,
    iteration: int,
    tasks: list[dict[str, Any]],
    run_events: str,
    dialogue_full: str,
    _submit_fn: Any | None = None,
    # Deprecated: host-layer parameters — use callbacks= instead.
    args: Any | None = None,
    engine: Any | None = None,
) -> dict[str, Any]:
    """Run engine dispatch for tasks.

    Args:
        callbacks: Cell-layer callback abstraction for host-layer side-effects
            (e.g. role status updates).  The delivery layer provides an
            implementation that delegates to ``PolarisEngine.update_role_status``.
            When None, a no-op callback is used (tests / standalone use).
        workspace_full: Workspace path
        run_id: Run identifier
        iteration: Iteration number
        tasks: Tasks to dispatch
        run_events: Events file path
        dialogue_full: Dialogue file path
        _submit_fn: Optional submit function for testing; when None the module-level
            cached lazy loader is used.
        args: DEPRECATED. Host-layer argparse.Namespace — use callbacks= instead.
        engine: DEPRECATED. Host-layer PolarisEngine — use callbacks= instead.

    Returns:
        Dispatch result dict
    """
    if args is not None:
        warnings.warn(
            "run_engine_dispatch: args= is deprecated; pass callbacks=DispatchCallbacks(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    if engine is not None:
        warnings.warn(
            "run_engine_dispatch: engine= is deprecated; pass callbacks=DispatchCallbacks(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    # Build callbacks — prefer explicit callbacks, bridge from engine if needed.
    if callbacks is None:
        if engine is not None and hasattr(engine, "update_role_status"):
            callbacks = DispatchCallbacks(
                update_role_status=lambda role, *, status, running, detail, **kw: engine.update_role_status(
                    role, status=status, running=running, detail=detail
                ),
            )
        else:
            callbacks = DispatchCallbacks()

    resolved_submit_fn = _resolve_workflow_submit_fn(_submit_fn)
    pm_workflow_input_type, workflow_submission_result_type, _ = _get_workflow_runtime_ref()

    result: dict[str, Any] = {
        "exit_code": 0,
        "director_result": None,
        "error": "",
    }

    try:
        _emit_engine_dispatch_status(
            run_events=run_events,
            run_id=run_id,
            iteration=iteration,
            task_count=len(tasks),
            name="engine_dispatch_started",
            summary="Engine dispatch started",
            ok=True,
            status="starting",
        )

        callbacks.update_role_status(
            "Director",
            status="running",
            running=True,
            detail=f"Executing {len(tasks)} tasks",
        )

        workflow_input = _build_workflow_input(
            pm_workflow_input_type,
            workspace_full=workspace_full,
            run_id=run_id,
            iteration=iteration,
            tasks=tasks,
        )
        workflow_result = resolved_submit_fn(workflow_input)
        if not isinstance(workflow_result, workflow_submission_result_type):
            result["error"] = f"Unexpected workflow result: {type(workflow_result)}"
            result["exit_code"] = 1
        else:
            result["director_result"] = _build_director_workflow_result(
                run_id=run_id,
                task_count=len(tasks),
                workflow_result=workflow_result,
            )
            if not bool(getattr(workflow_result, "submitted", False)):
                result["error"] = str(
                    getattr(workflow_result, "error", "") or getattr(workflow_result, "status", "") or ""
                ).strip()
                result["exit_code"] = 1

        _emit_engine_dispatch_status(
            run_events=run_events,
            run_id=run_id,
            iteration=iteration,
            task_count=len(tasks),
            name="engine_dispatch_completed",
            summary="Engine dispatch completed",
            ok=result["exit_code"] == 0,
            status="completed",
            error="" if result["exit_code"] == 0 else str(result.get("error") or "").strip(),
        )

    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        result["error"] = str(exc)
        result["exit_code"] = 1
        _emit_engine_dispatch_status(
            run_events=run_events,
            run_id=run_id,
            iteration=iteration,
            task_count=len(tasks),
            name="engine_dispatch_error",
            summary="Engine dispatch failed",
            ok=False,
            status="error",
            error=str(exc),
        )

    return result


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
        "passed": [],
        "reason": "",
        "summary": "",
        "errors": [],
        "run_id": run_id,
        "pm_iteration": int(iteration or 0),
        "director_task_status": status_summary,
        # Evidence-chain field: documents which QA path was used.
        # Surviving path: dispatch_pipeline (Cell-local, lightweight).
        # Deprecated path: QAWorkflow (temporal-activity-based, heavyweight).
        "qa_path": "dispatch_pipeline",
    }

    if not enabled:
        result["reason"] = "integration_qa_disabled"
        result["summary"] = "Integration QA is disabled"
        return result

    if not tasks:
        result["reason"] = "no_tasks"
        result["summary"] = "No tasks to verify"
        return result

    all_done = all(str(task.get("status", "")).lower() in ("done", "completed", "success") for task in tasks)
    if not all_done:
        result["reason"] = "incomplete_tasks"
        result["summary"] = "Not all tasks completed, skipping integration QA"
        return result

    if _tasks_touch_docs_only(tasks):
        result["reason"] = "docs_only"
        result["summary"] = "All tasks are docs-only, skipping integration QA"
        result["ran"] = True
        result["passed"] = True
        return result

    result["ran"] = True

    try:
        _, run_integration_verify_runner = _get_shared_quality()
        passed, summary, errors = run_integration_verify_runner(workspace_full)
        result["passed"] = passed
        result["summary"] = summary
        result["errors"] = errors
        result["reason"] = "integration_qa_passed" if passed else "integration_qa_failed"
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        result["passed"] = False
        result["reason"] = "integration_qa_error"
        result["summary"] = f"Integration QA error: {exc}"
        result["errors"] = [str(exc)]

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
        "passed": [],
        "reason": "",
        "summary": "",
        "errors": [],
        "run_id": run_id,
        "pm_iteration": int(iteration or 0),
        "director_task_status": status_summary,
        "result_path": "",
        "runtime_result_path": "",
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
        return True
    if int(status_summary.get("total") or 0) <= 0:
        result["reason"] = "no_director_tasks"
        return True
    if bool(docs_stage_payload.get("enabled")) and _tasks_touch_docs_only(tasks):
        result["reason"] = "docs_stage_docs_only"
        result["summary"] = "Integration QA skipped for docs-only stage tasks."
        return True
    if (
        int(status_summary.get("todo") or 0)
        + int(status_summary.get("in_progress") or 0)
        + int(status_summary.get("review") or 0)
        + int(status_summary.get("needs_continue") or 0)
    ) > 0:
        result["reason"] = "pending_director_tasks"
        return True
    if int(status_summary.get("failed") or 0) > 0 or int(status_summary.get("blocked") or 0) > 0:
        result["reason"] = "director_failures_present"
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


def _persist_post_dispatch_integration_qa_result(
    *,
    run_dir: str,
    result: dict[str, Any],
) -> None:
    """Persist the integration QA result payload."""
    from polaris.kernelone.fs.text_ops import write_json_atomic

    result_path = os.path.join(run_dir, "qa", "integration_qa.result.json")
    result["result_path"] = result_path
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
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
            "errors_count": len(result.get("errors") or []),
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

    _persist_post_dispatch_integration_qa_result(run_dir=run_dir, result=result)
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


# Re-export lazy-loaded functions as module-level names so external tests can
# monkeypatch them (e.g. test_dispatch_pipeline_engine_dispatch.py).
# These are populated on first access via the lazy loaders above.
submit_pm_workflow_sync = None  # type: ignore[assignment]
"""Re-exported from _get_workflow_runtime() — replaced on first _submit_pm_workflow_sync_resolve()."""

run_pre_dispatch_chief_engineer = None  # type: ignore[assignment]
"""Re-exported from _get_chief_engineer_service() for test patchability."""


def _submit_pm_workflow_sync_resolve() -> Callable:
    """Lazily resolve submit_pm_workflow_sync and cache at module level."""
    global submit_pm_workflow_sync
    if submit_pm_workflow_sync is None:
        _, _, submit_pm_workflow_sync = _get_workflow_runtime_ref()
    return submit_pm_workflow_sync


# Provide a module-level hook that tests can monkeypatch.
# run_engine_dispatch calls _get_workflow_runtime() internally; for test-patchability
# we also expose this resolver so tests patching _submit_pm_workflow_sync_resolve
# take effect.
_get_workflow_runtime_ref = _get_workflow_runtime
"""Exposed for test monkeypatching of the lazy-workflow-runtime loader."""
_get_chief_engineer_service_ref = _get_chief_engineer_service
"""Exposed for test monkeypatching of the lazy-chief-engineer loader."""


__all__ = [
    "DispatchCallbacks",
    "_mainline_publish_dispatch_tasks_to_task_market",
    "_shadow_publish_dispatch_tasks_to_task_market",
    "record_dispatch_status_to_shangshuling",
    "resolve_director_dispatch_tasks",
    "run_chief_engineer_preflight",
    "run_dispatch_pipeline",
    "run_engine_dispatch",
    "run_integration_qa",
    "run_post_dispatch_integration_qa",
]
