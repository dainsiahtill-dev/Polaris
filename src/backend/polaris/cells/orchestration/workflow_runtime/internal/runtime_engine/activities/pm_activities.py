"""PM-related Workflow activities."""

from __future__ import annotations

from typing import Any, cast

from polaris.cells.orchestration.pm_planning.public.service import evaluate_pm_task_quality
from polaris.cells.orchestration.workflow_runtime.internal.models import PMWorkflowInput, TaskContract
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_activity_api
from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path

from .base import ActivityExecutionResult, register_activity

activity = get_activity_api()


def _validate_tasks(tasks: list[TaskContract]) -> list[str]:
    issues: list[str] = []
    if not tasks:
        issues.append("No PM tasks were provided to the Workflow workflow")
        return issues
    for task in tasks:
        if not task.task_id:
            issues.append("Task is missing id")
        if not task.title:
            issues.append(f"Task {task.task_id or '<unknown>'} is missing title")
        acceptance = task.payload.get("acceptance_criteria")
        if not isinstance(acceptance, list) or not [str(item).strip() for item in acceptance if str(item).strip()]:
            issues.append(f"Task {task.task_id or '<unknown>'} is missing acceptance_criteria")
    return issues


def _merge_chief_engineer_task_updates(
    tasks: list[TaskContract],
    task_update_map: dict[str, Any],
    cognitive_runtime_receipt: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    receipt_payload = dict(cognitive_runtime_receipt or {})
    for task in tasks:
        payload = task.to_dict()
        update_raw = task_update_map.get(task.task_id)
        update: dict[str, Any] = update_raw if isinstance(update_raw, dict) else {}
        if not update:
            if receipt_payload:
                chief_engineer_payload = payload.get("chief_engineer")
                chief_engineer = dict(chief_engineer_payload) if isinstance(chief_engineer_payload, dict) else {}
                chief_engineer["cognitive_runtime_receipt"] = dict(receipt_payload)
                payload["chief_engineer"] = chief_engineer
            enriched.append(payload)
            continue

        construction_plan = update.get("construction_plan")
        if isinstance(construction_plan, dict) and construction_plan:
            payload["construction_plan"] = dict(construction_plan)

        scope_for_apply = [str(item).strip() for item in update.get("scope_for_apply") or [] if str(item).strip()]
        missing_targets = [str(item).strip() for item in update.get("missing_targets") or [] if str(item).strip()]
        blueprint_scope_raw = update.get("blueprint_scope")
        blueprint_scope: dict[str, Any] = dict(blueprint_scope_raw) if isinstance(blueprint_scope_raw, dict) else {}
        payload["chief_engineer"] = {
            "scope_for_apply": scope_for_apply,
            "missing_targets": missing_targets,
            "blueprint_scope": blueprint_scope,
        }
        if receipt_payload:
            payload["chief_engineer"]["cognitive_runtime_receipt"] = dict(receipt_payload)

        existing_scope = payload.get("scope_paths")
        scope_paths = (
            [str(item).strip() for item in existing_scope if str(item).strip()]
            if isinstance(existing_scope, list)
            else []
        )
        if scope_for_apply:
            payload["scope_paths"] = list(dict.fromkeys([*scope_paths, *scope_for_apply]))

        existing_targets = payload.get("target_files")
        target_files = (
            [str(item).strip() for item in existing_targets if str(item).strip()]
            if isinstance(existing_targets, list)
            else []
        )
        if missing_targets:
            payload["target_files"] = list(dict.fromkeys([*target_files, *missing_targets]))

        constraints_raw = payload.get("constraints")
        constraints = (
            [str(item).strip() for item in constraints_raw if str(item).strip()]
            if isinstance(constraints_raw, list)
            else []
        )
        ce_constraints = [str(item).strip() for item in update.get("constraints") or [] if str(item).strip()]
        if ce_constraints:
            payload["constraints"] = list(dict.fromkeys([*constraints, *ce_constraints]))
        enriched.append(payload)
    return enriched


def _flag_enabled(*sources: dict[str, Any], key: str) -> bool:
    for source in sources:
        value = source.get(key)
        if isinstance(value, bool):
            return value
        token = str(value or "").strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return False


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


def _record_chief_engineer_cognitive_receipt(
    *,
    workspace: str,
    run_id: str,
    metadata: dict[str, Any],
    tasks: list[TaskContract],
    status: str,
    summary: str,
    analysis: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Record Cognitive Runtime evidence for the deterministic ChiefEngineer blueprint activity."""

    required = _flag_enabled(
        metadata,
        key="cognitive_runtime_required",
    )
    context_os_expected = _flag_enabled(
        metadata,
        key="context_os_expected",
    )
    evidence: dict[str, Any] = {
        "ok": False,
        "required": required,
        "receipt_type": "chief_engineer_blueprint",
        "status": status,
        "context_os_expected": context_os_expected,
    }
    context_os_evidence: dict[str, Any] = {
        "ok": False,
        "required": context_os_expected,
        "skipped": not context_os_expected,
        "reason": "" if context_os_expected else "context_os_not_expected",
    }
    try:
        from polaris.cells.factory.cognitive_runtime.public import (
            RecordRuntimeReceiptCommandV1,
            ResolveContextCommandV1,
            get_cognitive_runtime_public_service,
        )

        session_id = (
            str(
                metadata.get("chief_engineer_session_id")
                or metadata.get("role_session_id")
                or metadata.get("session_id")
                or f"chief-engineer-{run_id or 'adhoc'}"
            ).strip()
            or None
        )
        service = get_cognitive_runtime_public_service()
        try:
            if context_os_expected:
                context_result = service.resolve_context(
                    ResolveContextCommandV1(
                        workspace=workspace,
                        role="chief_engineer",
                        query=summary or f"chief engineer blueprint for {len(tasks)} task(s)",
                        step=int(metadata.get("pm_iteration") or 0),
                        run_id=run_id or "chief-engineer-blueprint",
                        mode="workflow_runtime_chief_engineer_blueprint",
                        session_id=session_id,
                        sources_enabled=("runtime", "events", "contracts"),
                        policy={
                            "source": "workflow_runtime.pm_activities.run_chief_engineer_blueprint",
                            "context_os_required": True,
                            "task_ids": [task.task_id for task in tasks],
                        },
                    )
                )
                if not bool(getattr(context_result, "ok", False)):
                    context_os_evidence["error_message"] = (
                        str(getattr(context_result, "error_message", "") or "").strip()
                        or str(getattr(context_result, "error_code", "") or "").strip()
                        or "context_os_resolve_failed"
                    )
                    evidence["context_os"] = context_os_evidence
                    if required:
                        raise RuntimeError(
                            f"chief_engineer_context_os_resolve_failed:{context_os_evidence['error_message']}"
                        )
                else:
                    context_os_evidence = {
                        "ok": True,
                        "required": True,
                        "skipped": False,
                        "snapshot": _context_snapshot_evidence(getattr(context_result, "snapshot", None)),
                    }
            evidence["context_os"] = context_os_evidence
            result = service.record_runtime_receipt(
                RecordRuntimeReceiptCommandV1(
                    workspace=workspace,
                    receipt_type="chief_engineer_blueprint",
                    session_id=session_id,
                    run_id=run_id or None,
                    payload={
                        "source": "workflow_runtime.pm_activities.run_chief_engineer_blueprint",
                        "role": "chief_engineer",
                        "status": status,
                        "summary": summary,
                        "task_ids": [task.task_id for task in tasks],
                        "task_count": len(tasks),
                        "task_update_count": int((analysis or {}).get("task_update_count") or 0),
                        "blueprint_path": str((analysis or {}).get("blueprint_path") or ""),
                        "runtime_blueprint_path": str((analysis or {}).get("runtime_blueprint_path") or ""),
                        "errors": list(errors or []),
                        "context_os_expected": context_os_expected,
                        "context_os": context_os_evidence,
                    },
                    turn_envelope={
                        "role": "chief_engineer",
                        "session_id": session_id,
                        "run_id": run_id,
                        "task_id": "chief_engineer::blueprint",
                        "task_ids": [task.task_id for task in tasks],
                    },
                )
            )
        finally:
            service.close()
    except (RuntimeError, ValueError, ImportError) as exc:
        evidence["error_message"] = str(exc)
        if required:
            raise RuntimeError(f"chief_engineer_cognitive_runtime_receipt_failed:{exc}") from exc
        return evidence

    if not bool(getattr(result, "ok", False)):
        error_message = str(getattr(result, "error_message", "") or "").strip()
        error_code = str(getattr(result, "error_code", "") or "").strip()
        evidence["error_message"] = error_message or error_code
        if required:
            raise RuntimeError(evidence["error_message"] or "chief_engineer_cognitive_runtime_receipt_failed")
        return evidence

    receipt = getattr(result, "receipt", None)
    receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
    if required and not receipt_id:
        raise RuntimeError("chief_engineer_cognitive_runtime_receipt_missing_id")
    evidence["ok"] = True
    if receipt_id:
        evidence["receipt_id"] = receipt_id
    return evidence


@register_activity("generate_pm_tasks")
@activity.defn(name="generate_pm_tasks")
async def generate_pm_tasks(workflow_input: PMWorkflowInput) -> dict[str, Any]:
    """Bridge a precomputed PM payload into the Workflow workflow."""
    tasks = [task.to_dict() for task in workflow_input.payload_tasks()]
    if not tasks:
        return ActivityExecutionResult(
            success=False,
            summary=("Workflow PM workflow requires a registered PM generator or a precomputed payload"),
            errors=["no_precomputed_pm_payload"],
        ).to_dict()
    return ActivityExecutionResult(
        success=True,
        summary="Using precomputed PM payload from legacy orchestrator",
        payload={"tasks": tasks, "task_count": len(tasks)},
    ).to_dict()


@register_activity("run_chief_engineer_blueprint")
@activity.defn(name="run_chief_engineer_blueprint")
async def run_chief_engineer_blueprint(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Generate ChiefEngineer blueprints and enrich PM tasks before Director dispatch."""
    workspace = str(payload.get("workspace") or "").strip()
    run_id = str(payload.get("run_id") or "").strip()
    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    raw_tasks_value = payload.get("tasks")
    raw_tasks: list[Any] = raw_tasks_value if isinstance(raw_tasks_value, list) else []
    tasks = [TaskContract.from_mapping(item) for item in raw_tasks]
    tasks = [task for task in tasks if task.task_id]
    if not workspace:
        return ActivityExecutionResult(
            success=False,
            summary="ChiefEngineer blueprint generation requires workspace",
            errors=["missing_workspace"],
            error_code="chief_engineer_missing_workspace",
        ).to_dict()
    if not tasks:
        return ActivityExecutionResult(
            success=True,
            summary="No PM tasks to enrich with ChiefEngineer blueprints",
            payload={"tasks": [], "task_count": 0, "task_update_count": 0},
        ).to_dict()

    run_dir = str(metadata.get("run_dir") or "").strip()
    cache_root_full = str(metadata.get("cache_root_full") or "").strip()
    pm_iteration_raw = metadata.get("pm_iteration")
    try:
        pm_iteration = int(pm_iteration_raw or 0)
    except (RuntimeError, ValueError):
        pm_iteration = 0
    run_dir_clean = run_dir.rstrip("/\\")
    run_blueprint_path = (
        f"{run_dir_clean}/contracts/chief_engineer.blueprint.json"
        if run_dir_clean
        else resolve_artifact_path(workspace, cache_root_full, "runtime/contracts/chief_engineer.blueprint.json")
    )
    runtime_blueprint_path = resolve_artifact_path(
        workspace,
        cache_root_full,
        "runtime/contracts/chief_engineer.blueprint.json",
    )

    from polaris.delivery.cli.pm.chief_engineer import run_chief_engineer_analysis

    try:
        analysis = run_chief_engineer_analysis(
            tasks=[task.to_dict() for task in tasks],
            workspace_full=workspace,
            run_id=run_id,
            pm_iteration=pm_iteration,
            run_blueprint_path=run_blueprint_path,
            runtime_blueprint_path=runtime_blueprint_path,
        )
    except (RuntimeError, ValueError, ImportError) as exc:
        summary = f"ChiefEngineer blueprint generation failed: {exc}"
        errors = [str(exc)]
        try:
            cognitive_runtime_receipt = _record_chief_engineer_cognitive_receipt(
                workspace=workspace,
                run_id=run_id,
                metadata=metadata,
                tasks=tasks,
                status="failed",
                summary=summary,
                errors=errors,
            )
        except RuntimeError as receipt_exc:
            return ActivityExecutionResult(
                success=False,
                summary=str(receipt_exc),
                errors=[str(receipt_exc)],
                error_code="chief_engineer_cognitive_runtime_receipt_failed",
            ).to_dict()
        return ActivityExecutionResult(
            success=False,
            summary=summary,
            payload={"cognitive_runtime_receipt": cognitive_runtime_receipt},
            errors=errors,
            error_code="chief_engineer_blueprint_failed",
        ).to_dict()

    if bool(analysis.get("hard_failure")):
        summary = str(analysis.get("summary") or "ChiefEngineer blueprint generation failed")
        errors = [str(analysis.get("reason") or "chief_engineer_hard_failure")]
        try:
            cognitive_runtime_receipt = _record_chief_engineer_cognitive_receipt(
                workspace=workspace,
                run_id=run_id,
                metadata=metadata,
                tasks=tasks,
                status="failed",
                summary=summary,
                analysis=analysis,
                errors=errors,
            )
        except RuntimeError as receipt_exc:
            return ActivityExecutionResult(
                success=False,
                summary=str(receipt_exc),
                payload={"analysis": analysis},
                errors=[str(receipt_exc)],
                error_code="chief_engineer_cognitive_runtime_receipt_failed",
            ).to_dict()
        return ActivityExecutionResult(
            success=False,
            summary=summary,
            payload={"analysis": analysis, "cognitive_runtime_receipt": cognitive_runtime_receipt},
            errors=errors,
            error_code="chief_engineer_hard_failure",
        ).to_dict()

    task_update_map_raw = analysis.get("task_update_map")
    task_update_map: dict[str, Any] = task_update_map_raw if isinstance(task_update_map_raw, dict) else {}
    summary = str(analysis.get("summary") or "ChiefEngineer blueprint generated")
    try:
        cognitive_runtime_receipt = _record_chief_engineer_cognitive_receipt(
            workspace=workspace,
            run_id=run_id,
            metadata=metadata,
            tasks=tasks,
            status="completed",
            summary=summary,
            analysis=analysis,
        )
    except RuntimeError as exc:
        return ActivityExecutionResult(
            success=False,
            summary=str(exc),
            payload={"analysis": analysis},
            errors=[str(exc)],
            error_code="chief_engineer_cognitive_runtime_receipt_failed",
        ).to_dict()
    enriched_tasks = _merge_chief_engineer_task_updates(tasks, task_update_map, cognitive_runtime_receipt)
    return ActivityExecutionResult(
        success=True,
        summary=summary,
        payload={
            "tasks": enriched_tasks,
            "task_count": len(enriched_tasks),
            "task_update_count": int(analysis.get("task_update_count") or 0),
            "blueprint_path": str(analysis.get("blueprint_path") or run_blueprint_path),
            "runtime_blueprint_path": str(analysis.get("runtime_blueprint_path") or runtime_blueprint_path),
            "analysis": analysis,
            "cognitive_runtime_receipt": cognitive_runtime_receipt,
        },
    ).to_dict()


@register_activity("validate_task_contract")
@activity.defn(name="validate_task_contract")
async def validate_task_contract(
    payload: dict[str, Any] | list[TaskContract] | list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the existing PM quality gate before Director execution."""
    docs_stage: dict[str, Any] = {}
    if isinstance(payload, dict):
        tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
        docs_stage = cast(
            "dict[str, Any]",
            payload.get("docs_stage") if isinstance(payload.get("docs_stage"), dict) else {},
        )
    else:
        tasks = payload
    normalized_tasks: list[TaskContract] = []
    for item in tasks or []:
        normalized_tasks.append(item if isinstance(item, TaskContract) else TaskContract.from_mapping(item))
    issues = _validate_tasks(normalized_tasks)
    if not issues:
        try:
            report = evaluate_pm_task_quality(
                {"tasks": [task.to_dict() for task in normalized_tasks]},
                docs_stage=docs_stage,
            )
            if not bool(report.get("ok")):
                issues.extend([str(item).strip() for item in report.get("critical_issues") or [] if str(item).strip()])
        except (RuntimeError, ValueError) as exc:
            issues.append(f"pm_quality_gate_runtime_error: {exc}")
    return ActivityExecutionResult(
        success=not issues,
        summary="PM task contract validated" if not issues else "PM task contract rejected",
        payload={"task_count": len(normalized_tasks), "docs_stage": docs_stage},
        errors=issues,
    ).to_dict()
