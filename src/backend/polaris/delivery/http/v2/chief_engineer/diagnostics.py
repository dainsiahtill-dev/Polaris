"""Workspace / LLM / blueprint diagnostics route handlers for the Chief Engineer v2 router.

Lossless extraction of the diagnostics domain from the former single-file
``chief_engineer`` module. The diagnostics route is the largest reader of
test-patchable external symbols (``BlueprintPersistence``, ``build_llm_status``,
``resolve_logical_path``, ``get_global_emitter``, ``get_global_token_budget``),
so every such reference is resolved through the live package namespace
(``_ce.<Name>``) at call time to preserve ``monkeypatch.setattr`` semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, Request
from polaris.cells.chief_engineer.blueprint.public import summarize_risks
from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.v2.chief_engineer._router import (
    _blueprint_task_id,
    _settings_for_request,
    _string_list,
    _utc_now,
    _workspace_value,
    router,
)
from polaris.delivery.http.v2.chief_engineer._schemas import (
    ChiefEngineerDiagnosticsBlueprintStatus,
    ChiefEngineerDiagnosticsLLMStatus,
    ChiefEngineerDiagnosticsResponse,
    ChiefEngineerDiagnosticsWorkspaceStatus,
    ChiefEngineerPMTaskPlanProbe,
)
from polaris.delivery.http.v2.llm_event_filters import filter_llm_events_by_workspace


def _pm_task_plan_candidate_paths(workspace: str, *, ramdisk_root: str = "") -> tuple[list[Path], list[str]]:
    import polaris.delivery.http.v2.chief_engineer as _ce

    candidate_paths: list[Path] = []
    resolution_errors: list[str] = []
    for logical_path in ("runtime/tasks/plan.json", "runtime/contracts/pm_tasks.contract.json"):
        try:
            candidate_paths.append(
                Path(
                    _ce.resolve_logical_path(
                        workspace,
                        logical_path,
                        ramdisk_root=ramdisk_root or None,
                    )
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            resolution_errors.append(f"{logical_path}: {type(exc).__name__}: {exc}")
    return candidate_paths, resolution_errors


def _load_pm_task_plan_probe(workspace: str, *, ramdisk_root: str = "") -> ChiefEngineerPMTaskPlanProbe:
    candidate_paths, resolution_errors = _pm_task_plan_candidate_paths(workspace, ramdisk_root=ramdisk_root)
    if not candidate_paths:
        return ChiefEngineerPMTaskPlanProbe(
            status="unresolved",
            error="; ".join(resolution_errors) or "pm_task_plan_unresolved",
        )

    plan_path = next((path for path in candidate_paths if path.is_file()), candidate_paths[0])
    if not plan_path.is_file():
        return ChiefEngineerPMTaskPlanProbe(
            status="missing",
            path=str(plan_path),
            error="pm_task_plan_missing",
        )

    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return ChiefEngineerPMTaskPlanProbe(
            status="unreadable",
            path=str(plan_path),
            error=f"{type(exc).__name__}: {exc}",
        )
    except json.JSONDecodeError as exc:
        return ChiefEngineerPMTaskPlanProbe(
            status="invalid",
            path=str(plan_path),
            error=f"JSONDecodeError: {exc.msg}",
        )

    if not isinstance(payload, dict):
        return ChiefEngineerPMTaskPlanProbe(
            status="invalid",
            path=str(plan_path),
            error="pm_task_plan_payload_not_object",
        )

    from polaris.delivery.http.v2.chief_engineer._router import _pm_task_plan_rows, _task_id_from_plan_task

    task_rows = _pm_task_plan_rows(payload)
    if not task_rows and not isinstance(payload.get("tasks"), (dict, list)):
        return ChiefEngineerPMTaskPlanProbe(
            status="invalid",
            path=str(plan_path),
            error="pm_task_plan_tasks_not_list",
        )

    task_ids: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(task_rows, start=1):
        task_id = _task_id_from_plan_task(item, index)
        if task_id and task_id not in seen:
            seen.add(task_id)
            task_ids.append(task_id)
    return ChiefEngineerPMTaskPlanProbe(
        status="ready" if task_ids else "empty",
        path=str(plan_path),
        task_ids=task_ids,
    )


def _blueprint_contract_list(payload: dict[str, Any], *keys: str) -> list[str]:
    from polaris.delivery.http.v2.chief_engineer._router import _dict_value

    base_schema = _dict_value(payload, "base_schema")
    context = _dict_value(payload, "context") or _dict_value(base_schema, "context")
    pm_task = _dict_value(payload, "pm_task") or _dict_value(base_schema, "pm_task") or _dict_value(context, "task")
    qa_contract = _dict_value(pm_task, "qa_contract")
    for key in keys:
        for source in (payload, base_schema, context, pm_task, qa_contract):
            rows = _string_list(source.get(key))
            if rows:
                return rows
    return []


def _blueprint_handoff_missing_fields(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    target_files = _blueprint_contract_list(payload, "target_files", "scope_paths", "files", "affected_files")
    acceptance = _blueprint_contract_list(payload, "acceptance_criteria", "acceptance")
    execution_checklist = _blueprint_contract_list(payload, "execution_checklist", "steps")
    if not target_files:
        missing.append("target_files")
    if not acceptance:
        missing.append("acceptance_criteria")
    if not execution_checklist:
        missing.append("execution_checklist")
    return missing


def _blueprint_payload_is_handoff_ready(payload: dict[str, Any]) -> bool:
    if _blueprint_payload_is_traceability_only(payload):
        return False
    completeness = payload.get("contract_completeness")
    if isinstance(completeness, dict):
        missing_fields = _string_list(completeness.get("missing_fields"))
        if completeness.get("handoff_ready") is False or missing_fields:
            return False
    if not _blueprint_task_id(payload):
        return False
    return not _blueprint_handoff_missing_fields(payload)


def _blueprint_payload_is_traceability_only(payload: dict[str, Any]) -> bool:
    if payload.get("traceability_only") is True:
        return True
    source = str(payload.get("source") or "").strip().lower()
    return source.startswith("pm_dispatch.traceability")


def _build_workspace_diagnostics(settings: Any) -> ChiefEngineerDiagnosticsWorkspaceStatus:
    workspace = _workspace_value(settings)
    if not workspace:
        return ChiefEngineerDiagnosticsWorkspaceStatus(
            ok=False,
            status="missing",
            workspace="",
            exists=False,
            error="workspace_not_configured",
        )

    exists = Path(workspace).exists()
    return ChiefEngineerDiagnosticsWorkspaceStatus(
        ok=exists,
        status="ok" if exists else "missing",
        workspace=workspace,
        exists=exists,
        error=None if exists else "workspace_path_missing",
    )


def _role_payload(payload: dict[str, Any], role: str) -> dict[str, Any]:
    roles_value = payload.get("roles")
    roles = roles_value if isinstance(roles_value, dict) else {}
    target = role.strip().lower()
    for key, value in roles.items():
        if str(key or "").strip().lower() == target and isinstance(value, dict):
            return value
    return {}


def _build_llm_diagnostics(settings: Any) -> ChiefEngineerDiagnosticsLLMStatus:
    import polaris.delivery.http.v2.chief_engineer as _ce

    try:
        payload = _ce.build_llm_status(settings)
    except (RuntimeError, OSError, ValueError) as exc:
        return ChiefEngineerDiagnosticsLLMStatus(
            ok=False,
            state="error",
            blocked_roles=["chief_engineer"],
            error=str(exc),
        )

    if not isinstance(payload, dict):
        return ChiefEngineerDiagnosticsLLMStatus(
            ok=False,
            state="error",
            blocked_roles=["chief_engineer"],
            error="invalid_llm_status_payload",
        )

    role_info = _role_payload(payload, "chief_engineer")
    ready = bool(role_info.get("ready"))
    runtime_supported = bool(role_info.get("runtime_supported"))
    blocked_roles = _string_list(payload.get("blocked_roles"))
    unsupported_roles = _string_list(payload.get("unsupported_roles"))
    required_ready_roles = _string_list(payload.get("required_ready_roles"))
    if "chief_engineer" not in required_ready_roles:
        required_ready_roles.append("chief_engineer")
    if not ready and "chief_engineer" not in blocked_roles:
        blocked_roles.append("chief_engineer")
    if not runtime_supported and "chief_engineer" not in unsupported_roles:
        unsupported_roles.append("chief_engineer")

    ok = ready and runtime_supported
    return ChiefEngineerDiagnosticsLLMStatus(
        ok=ok,
        state="ready" if ok else "blocked",
        blocked_roles=blocked_roles,
        unsupported_roles=unsupported_roles,
        required_ready_roles=required_ready_roles,
        provider_id=str(role_info.get("provider_id") or "").strip() or None,
        model=str(role_info.get("model") or "").strip() or None,
        details=payload,
    )


def _build_blueprint_diagnostics(settings: Any) -> ChiefEngineerDiagnosticsBlueprintStatus:
    import polaris.delivery.http.v2.chief_engineer as _ce

    workspace = _workspace_value(settings)
    if not workspace:
        return ChiefEngineerDiagnosticsBlueprintStatus(
            ok=False,
            status="missing_workspace",
            error="workspace_not_configured",
        )

    try:
        persistence = _ce.BlueprintPersistence(workspace, ensure_directory=False)
        blueprint_ids = persistence.list_all()
        plan_probe = _load_pm_task_plan_probe(
            workspace,
            ramdisk_root=str(getattr(settings, "ramdisk_root", "") or "").strip(),
        )
        loadable = 0
        invalid_payloads = 0
        updated_tokens: list[str] = []
        covered_task_ids: set[str] = set()
        for blueprint_id in blueprint_ids:
            payload = persistence.load(blueprint_id)
            if isinstance(payload, dict):
                loadable += 1
                updated_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
                if updated_at:
                    updated_tokens.append(updated_at)
                task_id = _blueprint_task_id(payload)
                if task_id and _blueprint_payload_is_handoff_ready(payload):
                    covered_task_ids.add(task_id)
                elif task_id and not _blueprint_payload_is_traceability_only(payload):
                    invalid_payloads += 1
            else:
                invalid_payloads += 1
    except (OSError, RuntimeError, ValueError) as exc:
        return ChiefEngineerDiagnosticsBlueprintStatus(
            ok=False,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )

    if invalid_payloads:
        status = "degraded"
    elif loadable:
        status = "ready"
    else:
        status = "empty"

    planned_task_ids = plan_probe.task_ids
    if plan_probe.status != "ready":
        missing_task_ids: list[str] = []
        covered_tasks = 0
        director_handoff_ready = False
    else:
        ready_task_ids = planned_task_ids or []
        missing_task_ids = [task_id for task_id in ready_task_ids if task_id not in covered_task_ids]
        covered_tasks = len(ready_task_ids) - len(missing_task_ids)
        director_handoff_ready = bool(ready_task_ids) and not missing_task_ids

    return ChiefEngineerDiagnosticsBlueprintStatus(
        ok=plan_probe.status == "ready" and not missing_task_ids,
        status=status,
        plan_status=plan_probe.status,
        plan_path=plan_probe.path,
        plan_error=plan_probe.error,
        total=len(blueprint_ids),
        loadable=loadable,
        invalid_payloads=invalid_payloads,
        planned_tasks=len(planned_task_ids or []),
        covered_tasks=covered_tasks,
        missing_task_ids=missing_task_ids,
        director_handoff_ready=director_handoff_ready,
        latest_updated_at=max(updated_tokens) if updated_tokens else None,
    )


def _diagnostic_issues(
    workspace: ChiefEngineerDiagnosticsWorkspaceStatus,
    llm: ChiefEngineerDiagnosticsLLMStatus,
    blueprints: ChiefEngineerDiagnosticsBlueprintStatus,
) -> list[str]:
    issues: list[str] = []
    if not workspace.ok:
        issues.append("workspace_unavailable")
    if not llm.ok:
        issues.append("llm_not_ready")
    if blueprints.plan_status in {"missing", "unresolved", "unreadable", "invalid"}:
        issues.append("blueprint_task_plan_unavailable")
    elif blueprints.plan_status == "empty":
        issues.append("blueprint_task_plan_empty")
    if blueprints.status == "error":
        issues.append("blueprint_store_unreadable")
    if blueprints.invalid_payloads and not blueprints.director_handoff_ready:
        issues.append("blueprint_payload_invalid")
    if blueprints.missing_task_ids:
        issues.append("blueprint_coverage_incomplete")
    if not blueprints.director_handoff_ready and not issues:
        issues.append("blueprint_handoff_not_ready")
    return issues


def _generate_blockers(
    workspace: ChiefEngineerDiagnosticsWorkspaceStatus,
    llm: ChiefEngineerDiagnosticsLLMStatus,
) -> list[str]:
    """Return hard blockers for Chief Engineer blueprint generation."""
    blockers: list[str] = []
    if not workspace.ok:
        blockers.append("workspace_unavailable")
    if not llm.ok:
        blockers.append("llm_not_ready")
    return blockers


def _handoff_blockers(
    workspace: ChiefEngineerDiagnosticsWorkspaceStatus,
    blueprints: ChiefEngineerDiagnosticsBlueprintStatus,
) -> list[str]:
    """Return hard blockers that should disable Chief Engineer -> Director handoff."""
    blockers: list[str] = []
    if not workspace.ok:
        blockers.append("workspace_unavailable")
    if blueprints.plan_status in {"missing", "unresolved", "unreadable", "invalid"}:
        blockers.append("blueprint_task_plan_unavailable")
    elif blueprints.plan_status == "empty":
        blockers.append("blueprint_task_plan_empty")
    if blueprints.status == "error":
        blockers.append("blueprint_store_unreadable")
    if blueprints.invalid_payloads and not blueprints.director_handoff_ready:
        blockers.append("blueprint_payload_invalid")
    if blueprints.missing_task_ids:
        blockers.append("blueprint_coverage_incomplete")
    # Tier-2 enforcement: an open critical/blocker risk in the workspace Risk
    # Register blocks handoff at the desktop dispatch boundary. Read-only and
    # defensive — a register read failure must never crash diagnostics.
    if workspace.ok and workspace.workspace:
        try:
            risk_summary = summarize_risks(workspace.workspace)
            if int(risk_summary.get("open_critical_or_blocker", 0) or 0) > 0:
                blockers.append("open_blocker_risks")
        except (OSError, RuntimeError, ValueError):
            pass
    if not blueprints.director_handoff_ready and not blockers:
        blockers.append("blueprint_handoff_not_ready")
    return blockers


def _llm_event_stats(events: list[Any]) -> dict[str, int]:
    return {
        "total": len(events),
        "call_start": sum(1 for event in events if event.event_type == "llm_call_start"),
        "call_end": sum(1 for event in events if event.event_type == "llm_call_end"),
        "call_error": sum(1 for event in events if event.event_type == "llm_error"),
        "call_retry": sum(1 for event in events if event.event_type == "llm_retry"),
        "validation_pass": sum(1 for event in events if event.event_type == "validation_pass"),
        "validation_fail": sum(1 for event in events if event.event_type == "validation_fail"),
    }


@router.get(
    "/chief-engineer/diagnostics",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerDiagnosticsResponse,
)
def get_chief_engineer_diagnostics(request: Request, workspace: str = "") -> ChiefEngineerDiagnosticsResponse:
    """Return side-effect-free Chief Engineer desktop readiness diagnostics."""

    settings = _settings_for_request(request, workspace)
    workspace_status = _build_workspace_diagnostics(settings)
    llm = _build_llm_diagnostics(settings)
    blueprints = _build_blueprint_diagnostics(settings)
    generate_blockers = _generate_blockers(workspace_status, llm)
    issues = _diagnostic_issues(workspace_status, llm, blueprints)
    handoff_blockers = _handoff_blockers(workspace_status, blueprints)
    return ChiefEngineerDiagnosticsResponse(
        ok=not issues,
        can_handoff=not handoff_blockers,
        can_generate=not generate_blockers,
        generated_at=_utc_now(),
        workspace=workspace_status,
        llm=llm,
        blueprints=blueprints,
        issues=issues,
        generate_blockers=generate_blockers,
        handoff_blockers=handoff_blockers,
    )


@router.get("/chief-engineer/llm-events", dependencies=[Depends(require_auth)])
async def get_chief_engineer_llm_events(
    request: Request,
    run_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
    workspace: str = "",
) -> dict[str, Any]:
    """Return Chief Engineer LLM event history from the shared roles kernel."""

    import polaris.delivery.http.v2.chief_engineer as _ce

    emitter = _ce.get_global_emitter()
    events = emitter.get_events(
        run_id=run_id,
        task_id=task_id,
        role="chief_engineer",
        limit=limit,
    )
    resolved_workspace = _workspace_value(_settings_for_request(request, workspace))
    events = filter_llm_events_by_workspace(events, resolved_workspace)
    return {
        "run_id": run_id,
        "task_id": task_id,
        "role": "chief_engineer",
        "workspace": resolved_workspace,
        "events": [event.to_dict() for event in events],
        "count": len(events),
        "stats": _llm_event_stats(events),
    }


@router.get("/chief-engineer/cache-stats", dependencies=[Depends(require_auth)])
async def get_chief_engineer_cache_stats() -> dict[str, Any]:
    """Return shared LLM cache statistics for Chief Engineer desktop evidence."""

    from polaris.cells.roles.kernel.public.service import get_global_llm_cache

    cache = get_global_llm_cache()
    return cache.get_stats()


@router.post("/chief-engineer/cache-clear", dependencies=[Depends(require_auth)])
async def clear_chief_engineer_cache() -> dict[str, Any]:
    """Clear the shared LLM cache through the Chief Engineer role surface."""

    from polaris.cells.roles.kernel.public.service import get_global_llm_cache

    cache = get_global_llm_cache()
    cache.clear()
    return {"ok": True, "message": "Cache cleared"}


@router.get("/chief-engineer/token-budget-stats", dependencies=[Depends(require_auth)])
async def get_chief_engineer_token_budget_stats() -> dict[str, Any]:
    """Return shared token-budget statistics for Chief Engineer desktop evidence."""

    import polaris.delivery.http.v2.chief_engineer as _ce

    budget = _ce.get_global_token_budget()
    return budget.get_stats()


__all__ = [
    "_blueprint_contract_list",
    "_blueprint_handoff_missing_fields",
    "_blueprint_payload_is_handoff_ready",
    "_blueprint_payload_is_traceability_only",
    "_build_blueprint_diagnostics",
    "_build_llm_diagnostics",
    "_build_workspace_diagnostics",
    "_diagnostic_issues",
    "_generate_blockers",
    "_handoff_blockers",
    "_llm_event_stats",
    "_load_pm_task_plan_probe",
    "_pm_task_plan_candidate_paths",
    "_role_payload",
    "clear_chief_engineer_cache",
    "get_chief_engineer_cache_stats",
    "get_chief_engineer_diagnostics",
    "get_chief_engineer_llm_events",
    "get_chief_engineer_token_budget_stats",
]
