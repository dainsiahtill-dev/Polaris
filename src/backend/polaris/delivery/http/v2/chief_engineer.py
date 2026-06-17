"""Chief Engineer v2 delivery routes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.chief_engineer.blueprint.public import (
    ADRStatus,
    BlueprintPersistence,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    IncidentSeverity,
    ListADRsQueryV1,
    ListPostMortemsQueryV1,
    ListRisksQueryV1,
    ListTechDebtQueryV1,
    ListTechRadarQueryV1,
    PostMortemStatus,
    RegisterADRCommandV1,
    RegisterPostMortemCommandV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RegisterTechRadarCommandV1,
    RiskSeverity,
    RiskStatus,
    TaskBlueprintResultV1,
    TechDebtSeverity,
    TechDebtStatus,
    TechRadarRing,
    UpdateADRStatusCommandV1,
    UpdatePostMortemStatusCommandV1,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
    UpdateTechRadarRingCommandV1,
    assess_release_readiness,
    check_stack_policy,
    evaluate_handoff_decision_for_blueprint,
    generate_task_blueprint,
    get_blueprint_status,
    list_adrs,
    list_post_mortems,
    list_risks,
    list_tech_debt,
    list_tech_radar,
    register_adr,
    register_post_mortem,
    register_risk,
    register_tech_debt,
    register_tech_radar,
    summarize_adrs,
    summarize_post_mortems,
    summarize_risks,
    summarize_tech_debt,
    summarize_tech_radar,
    update_adr_status,
    update_post_mortem_status,
    update_risk_status,
    update_tech_debt_status,
    update_tech_radar_ring,
)
from polaris.cells.roles.kernel.public.service import (
    get_global_emitter,
    get_global_token_budget,
)
from polaris.cells.runtime.projection.public.role_contracts import (
    ChiefEngineerBlueprintDetailV1,
    ChiefEngineerBlueprintListV1,
    ChiefEngineerBlueprintSummaryV1,
)
from polaris.cells.runtime.projection.public.service import build_llm_status
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    ensure_required_roles_ready,
    get_state,
    require_auth,
)
from polaris.delivery.http.v2.llm_event_filters import filter_llm_events_by_workspace
from polaris.delivery.http.workspace import active_workspace_value, settings_with_workspace_override
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage import resolve_logical_path
from pydantic import BaseModel, Field

router = APIRouter(tags=["chief-engineer"])

_BLUEPRINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ChiefEngineerBlueprintSummary(ChiefEngineerBlueprintSummaryV1):
    """Chief Engineer blueprint summary bound to the shared contract."""


class ChiefEngineerBlueprintListResponse(ChiefEngineerBlueprintListV1):
    """Chief Engineer blueprint list response bound to the shared contract."""


class ChiefEngineerBlueprintDetailResponse(ChiefEngineerBlueprintDetailV1):
    """Chief Engineer blueprint detail response bound to the shared contract."""


class ChiefEngineerGenerateBlueprintRequest(BaseModel):
    """Desktop request for generating a task-level Chief Engineer blueprint."""

    task_id: str
    objective: str
    run_id: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerBulkBlueprintTaskRequest(BaseModel):
    """One task entry in a Chief Engineer bulk blueprint generation request."""

    task_id: str
    objective: str
    run_id: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerBulkGenerateBlueprintRequest(BaseModel):
    """Bulk request for covering multiple PM/Director tasks with CE blueprints."""

    tasks: list[ChiefEngineerBulkBlueprintTaskRequest] = Field(default_factory=list)
    stop_on_error: bool = False


class ChiefEngineerTaskBlueprintResultResponse(BaseModel):
    """Chief Engineer command/query result with persisted blueprint context."""

    ok: bool
    task_id: str
    workspace: str
    status: str
    blueprint_id: str | None = None
    blueprint_path: str | None = None
    source: str = "runtime/blueprints"
    summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    blueprint: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerBulkBlueprintError(BaseModel):
    """Per-task failure captured during bulk blueprint generation."""

    task_id: str
    code: str
    message: str


class ChiefEngineerBulkGenerateBlueprintResponse(BaseModel):
    """Bulk blueprint generation evidence for the Chief Engineer desktop."""

    ok: bool
    workspace: str
    total: int
    generated: int
    failed: int
    results: list[ChiefEngineerTaskBlueprintResultResponse] = Field(default_factory=list)
    errors: list[ChiefEngineerBulkBlueprintError] = Field(default_factory=list)


class ChiefEngineerBlueprintDeleteResponse(BaseModel):
    """Deletion evidence for a persisted Chief Engineer blueprint."""

    ok: bool
    blueprint_id: str
    deleted: bool
    source: str = "runtime/blueprints"


class ChiefEngineerDiagnosticsWorkspaceStatus(BaseModel):
    """Workspace readiness section for Chief Engineer desktop diagnostics."""

    ok: bool
    status: str
    workspace: str
    exists: bool
    error: str | None = None


class ChiefEngineerDiagnosticsLLMStatus(BaseModel):
    """Chief Engineer role-specific LLM readiness section."""

    ok: bool
    state: str
    role: str = "chief_engineer"
    blocked_roles: list[str] = Field(default_factory=list)
    unsupported_roles: list[str] = Field(default_factory=list)
    required_ready_roles: list[str] = Field(default_factory=list)
    provider_id: str | None = None
    model: str | None = None
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerDiagnosticsBlueprintStatus(BaseModel):
    """Blueprint store readiness section for Chief Engineer desktop diagnostics."""

    ok: bool
    status: str
    source: str = "runtime/blueprints"
    plan_status: str = "unknown"
    plan_path: str | None = None
    plan_error: str | None = None
    total: int = 0
    loadable: int = 0
    invalid_payloads: int = 0
    planned_tasks: int = 0
    covered_tasks: int = 0
    missing_task_ids: list[str] = Field(default_factory=list)
    director_handoff_ready: bool = False
    latest_updated_at: str | None = None
    error: str | None = None


class ChiefEngineerPMTaskPlanProbe(BaseModel):
    """Read-only PM task plan evidence for Chief Engineer handoff diagnostics."""

    status: str
    path: str | None = None
    task_ids: list[str] | None = None
    error: str | None = None


class ChiefEngineerDiagnosticsResponse(BaseModel):
    """Side-effect-free Chief Engineer desktop readiness snapshot."""

    ok: bool
    can_handoff: bool
    role: str = "chief_engineer"
    generated_at: str
    workspace: ChiefEngineerDiagnosticsWorkspaceStatus
    llm: ChiefEngineerDiagnosticsLLMStatus
    blueprints: ChiefEngineerDiagnosticsBlueprintStatus
    can_generate: bool
    issues: list[str] = Field(default_factory=list)
    generate_blockers: list[str] = Field(default_factory=list)
    handoff_blockers: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_blueprint_id(blueprint_id: str) -> str:
    token = str(blueprint_id or "").strip()
    if not _BLUEPRINT_ID_RE.fullmatch(token):
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_BLUEPRINT_ID",
            message="invalid blueprint id",
        )
    return token


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        if isinstance(item, str):
            token = item.strip()
        elif isinstance(item, dict):
            token = str(
                item.get("path")
                or item.get("file")
                or item.get("description")
                or item.get("text")
                or item.get("title")
                or item.get("name")
                or item.get("id")
                or item.get("value")
                or ""
            ).strip()
        else:
            token = str(item or "").strip()
        if token:
            rows.append(token)
    return rows


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pm_task_plan_candidate_paths(workspace: str, *, ramdisk_root: str = "") -> tuple[list[Path], list[str]]:
    candidate_paths: list[Path] = []
    resolution_errors: list[str] = []
    for logical_path in ("runtime/tasks/plan.json", "runtime/contracts/pm_tasks.contract.json"):
        try:
            candidate_paths.append(
                Path(
                    resolve_logical_path(
                        workspace,
                        logical_path,
                        ramdisk_root=ramdisk_root or None,
                    )
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            resolution_errors.append(f"{logical_path}: {type(exc).__name__}: {exc}")
    return candidate_paths, resolution_errors


def _pm_task_plan_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tasks = payload.get("tasks")
    if isinstance(raw_tasks, dict):
        return [item for item in raw_tasks.values() if isinstance(item, dict)]
    if isinstance(raw_tasks, list):
        return [item for item in raw_tasks if isinstance(item, dict)]
    return []


def _task_id_from_plan_task(task: dict[str, Any], index: int) -> str:
    for key in ("id", "task_id", "uid", "pm_task_id"):
        value = task.get(key)
        token = str(value or "").strip()
        if token:
            return token
    return f"task-{index}"


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


def _blueprint_task_id(payload: dict[str, Any]) -> str:
    sources = [
        payload,
        payload.get("raw") if isinstance(payload.get("raw"), dict) else None,
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
        payload.get("context") if isinstance(payload.get("context"), dict) else None,
    ]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("task_id", "pm_task_id", "taskId", "id"):
            token = str(source.get(key) or "").strip()
            if token:
                return token
    return ""


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _blueprint_contract_list(payload: dict[str, Any], *keys: str) -> list[str]:
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


def _blueprint_id_from_payload(payload: dict[str, Any], fallback: str) -> str:
    return (
        str(payload.get("blueprint_id") or payload.get("id") or payload.get("task_id") or fallback).strip() or fallback
    )


def _blueprint_summary(payload: dict[str, Any], fallback_id: str) -> ChiefEngineerBlueprintSummary:
    blueprint_id = _blueprint_id_from_payload(payload, fallback_id)
    return ChiefEngineerBlueprintSummary(
        blueprint_id=blueprint_id,
        title=str(payload.get("title") or payload.get("task_title") or payload.get("subject") or blueprint_id).strip(),
        summary=str(payload.get("summary") or payload.get("goal") or payload.get("description") or "").strip(),
        status=str(payload.get("status")).strip() if payload.get("status") is not None else None,
        target_files=_string_list(
            payload.get("target_files")
            or payload.get("scope_paths")
            or payload.get("files")
            or payload.get("affected_files")
        ),
        updated_at=str(payload.get("updated_at") or payload.get("created_at") or "").strip() or None,
        raw=payload,
    )


def _settings_for_request(request: Request, workspace: str = "") -> Any:
    state = get_state(request)
    return settings_with_workspace_override(state.settings, workspace)


def _state_for_settings(request: Request, settings: Any) -> Any:
    state = get_state(request)
    if settings is state.settings:
        return state
    return SimpleNamespace(settings=settings)


def _persistence_for_request(
    request: Request,
    *,
    ensure_directory: bool = True,
    workspace: str = "",
) -> BlueprintPersistence:
    settings = _settings_for_request(request, workspace)
    target_workspace = _workspace_value(settings)
    if not target_workspace:
        raise StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_NOT_CONFIGURED",
            message="workspace is not configured",
        )
    return BlueprintPersistence(target_workspace, ensure_directory=ensure_directory)


def _blueprint_payload_for_result(result: TaskBlueprintResultV1) -> dict[str, Any]:
    if not result.blueprint_id:
        return {}
    payload = BlueprintPersistence(result.workspace, ensure_directory=False).load(result.blueprint_id)
    return payload if isinstance(payload, dict) else {}


def _blueprint_result_response(result: TaskBlueprintResultV1) -> ChiefEngineerTaskBlueprintResultResponse:
    return ChiefEngineerTaskBlueprintResultResponse(
        ok=result.ok,
        task_id=result.task_id,
        workspace=result.workspace,
        status=result.status,
        blueprint_id=result.blueprint_id,
        blueprint_path=result.blueprint_path,
        summary=result.summary,
        recommendations=list(result.recommendations),
        risks=list(result.risks),
        blueprint=_blueprint_payload_for_result(result),
    )


def _generate_blueprint_for_task(
    item: ChiefEngineerGenerateBlueprintRequest | ChiefEngineerBulkBlueprintTaskRequest,
    *,
    workspace: str,
) -> TaskBlueprintResultV1:
    command = GenerateTaskBlueprintCommandV1(
        task_id=item.task_id,
        workspace=workspace,
        objective=item.objective,
        run_id=item.run_id,
        constraints=item.constraints,
        context=item.context,
    )
    return generate_task_blueprint(command)


def _blueprint_reference_update(result: ChiefEngineerTaskBlueprintResultResponse) -> dict[str, str]:
    blueprint_id = str(result.blueprint_id or "").strip()
    if not result.ok or not blueprint_id:
        return {}
    blueprint_path = str(result.blueprint_path or "").strip() or f"runtime/blueprints/{blueprint_id}.json"
    return {
        "blueprint_id": blueprint_id,
        "blueprint_path": blueprint_path,
        "runtime_blueprint_path": blueprint_path,
    }


def _run_contract_copy_path(plan_path: Path, payload: dict[str, Any]) -> Path | None:
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return None
    try:
        resolved = plan_path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    runtime_root = resolved.parent.parent
    if runtime_root.name.lower() != "runtime":
        return None
    return runtime_root / "runs" / run_id / "contracts" / "pm_tasks.contract.json"


def _apply_blueprint_references_to_plan_payload(
    payload: dict[str, Any],
    updates_by_task_id: dict[str, dict[str, str]],
) -> int:
    updated = 0
    for index, task in enumerate(_pm_task_plan_rows(payload), start=1):
        task_id = _task_id_from_plan_task(task, index)
        update = updates_by_task_id.get(task_id)
        if not update:
            continue
        task.update(update)
        metadata_raw = task.get("metadata")
        metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        metadata.update(update)
        metadata.setdefault("pm_task_id", task_id)
        task["metadata"] = metadata
        updated += 1
    return updated


def _sync_blueprint_references_to_pm_task_plans(
    *,
    workspace: str,
    ramdisk_root: str,
    results: list[ChiefEngineerTaskBlueprintResultResponse],
) -> int:
    updates_by_task_id = {
        result.task_id: update
        for result in results
        for update in [_blueprint_reference_update(result)]
        if update and result.task_id
    }
    if not updates_by_task_id:
        return 0

    candidate_paths, resolution_errors = _pm_task_plan_candidate_paths(workspace, ramdisk_root=ramdisk_root)
    if not candidate_paths:
        raise RuntimeError("; ".join(resolution_errors) or "pm_task_plan_unresolved")

    updated_total = 0
    loaded_payloads = 0
    written_paths: set[Path] = set()
    for plan_path in candidate_paths:
        payload = _read_json_file(plan_path)
        if not isinstance(payload, dict):
            continue
        loaded_payloads += 1
        updated_count = _apply_blueprint_references_to_plan_payload(payload, updates_by_task_id)
        if updated_count <= 0:
            continue
        write_json_atomic(str(plan_path), payload)
        written_paths.add(plan_path.resolve())
        updated_total += updated_count

        run_copy_path = _run_contract_copy_path(plan_path, payload)
        if run_copy_path is not None and run_copy_path.is_file():
            write_json_atomic(str(run_copy_path), payload)
            written_paths.add(run_copy_path.resolve())

    if loaded_payloads <= 0:
        return 0
    if updated_total <= 0:
        raise RuntimeError("pm_task_plan_blueprint_references_not_updated")
    return len(written_paths)


def _sync_blueprint_references_or_raise(
    *,
    settings: Any,
    workspace: str,
    results: list[ChiefEngineerTaskBlueprintResultResponse],
) -> None:
    if not any(result.ok and result.blueprint_id for result in results):
        return
    try:
        _sync_blueprint_references_to_pm_task_plans(
            workspace=workspace,
            ramdisk_root=str(getattr(settings, "ramdisk_root", "") or "").strip(),
            results=results,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=500,
            code="BLUEPRINT_TASK_PLAN_SYNC_FAILED",
            message="generated Chief Engineer blueprints could not be linked to the PM task plan",
            details={"error": f"{type(exc).__name__}: {exc}"},
        ) from exc


def _workspace_value(settings: Any) -> str:
    return active_workspace_value(settings)


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
    try:
        payload = build_llm_status(settings)
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
    workspace = _workspace_value(settings)
    if not workspace:
        return ChiefEngineerDiagnosticsBlueprintStatus(
            ok=False,
            status="missing_workspace",
            error="workspace_not_configured",
        )

    try:
        persistence = BlueprintPersistence(workspace, ensure_directory=False)
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

    emitter = get_global_emitter()
    events = emitter.get_events(
        run_id=run_id,
        task_id=task_id,
        role="chief_engineer",
        limit=limit,
    )
    resolved_workspace = _workspace_value(_settings_for_request(request, workspace))
    if workspace.strip():
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

    budget = get_global_token_budget()
    return budget.get_stats()


@router.get(
    "/chief-engineer/blueprints",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerBlueprintListResponse,
)
def list_chief_engineer_blueprints(
    request: Request,
    workspace: str = "",
) -> ChiefEngineerBlueprintListResponse:
    """List persisted Chief Engineer blueprints for the active workspace."""

    persistence = _persistence_for_request(request, ensure_directory=False, workspace=workspace)
    rows: list[ChiefEngineerBlueprintSummaryV1] = []
    for blueprint_id in persistence.list_all():
        payload = persistence.load(blueprint_id)
        if isinstance(payload, dict):
            rows.append(_blueprint_summary(payload, blueprint_id))

    rows.sort(key=lambda item: item.updated_at or item.blueprint_id, reverse=True)
    return ChiefEngineerBlueprintListResponse(blueprints=rows, total=len(rows))


@router.post(
    "/chief-engineer/blueprints",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerTaskBlueprintResultResponse,
)
def generate_chief_engineer_blueprint(
    request: Request,
    payload: ChiefEngineerGenerateBlueprintRequest,
    workspace: str = "",
) -> ChiefEngineerTaskBlueprintResultResponse:
    """Generate and persist a Chief Engineer blueprint through the cell command contract."""

    settings = _settings_for_request(request, workspace)
    ensure_required_roles_ready(
        _state_for_settings(request, settings),
        default_roles=["chief_engineer"],
        force_first="chief_engineer",
    )
    target_workspace = _workspace_value(settings)
    try:
        result = _generate_blueprint_for_task(payload, workspace=target_workspace)
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_BLUEPRINT_COMMAND",
            message=str(exc),
        ) from exc
    response = _blueprint_result_response(result)
    _sync_blueprint_references_or_raise(
        settings=settings,
        workspace=target_workspace,
        results=[response],
    )
    return response


@router.post(
    "/chief-engineer/blueprints/bulk",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerBulkGenerateBlueprintResponse,
)
def bulk_generate_chief_engineer_blueprints(
    request: Request,
    payload: ChiefEngineerBulkGenerateBlueprintRequest,
    workspace: str = "",
) -> ChiefEngineerBulkGenerateBlueprintResponse:
    """Generate Chief Engineer blueprints for multiple tasks through the cell contract."""

    if not payload.tasks:
        raise StructuredHTTPException(
            status_code=400,
            code="EMPTY_BLUEPRINT_BATCH",
            message="at least one task is required",
        )

    settings = _settings_for_request(request, workspace)
    ensure_required_roles_ready(
        _state_for_settings(request, settings),
        default_roles=["chief_engineer"],
        force_first="chief_engineer",
    )
    target_workspace = _workspace_value(settings)
    results: list[ChiefEngineerTaskBlueprintResultResponse] = []
    errors: list[ChiefEngineerBulkBlueprintError] = []

    for item in payload.tasks:
        try:
            result = _generate_blueprint_for_task(item, workspace=target_workspace)
            result_response = _blueprint_result_response(result)
            results.append(result_response)
            if not result_response.ok:
                errors.append(
                    ChiefEngineerBulkBlueprintError(
                        task_id=result_response.task_id,
                        code="BLUEPRINT_GENERATION_FAILED",
                        message=result_response.summary or result_response.status,
                    )
                )
                if payload.stop_on_error:
                    break
        except ValueError as exc:
            errors.append(
                ChiefEngineerBulkBlueprintError(
                    task_id=str(item.task_id or "").strip(),
                    code="INVALID_BLUEPRINT_COMMAND",
                    message=str(exc),
                )
            )
            if payload.stop_on_error:
                break

    generated = sum(1 for item in results if item.ok and item.blueprint_id)
    failed = len(errors)
    _sync_blueprint_references_or_raise(
        settings=settings,
        workspace=target_workspace,
        results=results,
    )
    return ChiefEngineerBulkGenerateBlueprintResponse(
        ok=failed == 0 and generated == len(payload.tasks),
        workspace=target_workspace,
        total=len(payload.tasks),
        generated=generated,
        failed=failed,
        results=results,
        errors=errors,
    )


@router.get(
    "/chief-engineer/blueprints/status",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerTaskBlueprintResultResponse,
)
def get_chief_engineer_blueprint_status(
    request: Request,
    task_id: str,
    run_id: str | None = None,
    workspace: str = "",
) -> ChiefEngineerTaskBlueprintResultResponse:
    """Return Chief Engineer blueprint status for a task through the cell query contract."""

    target_workspace = _workspace_value(_settings_for_request(request, workspace))
    try:
        query = GetBlueprintStatusQueryV1(
            task_id=task_id,
            workspace=target_workspace,
            run_id=run_id,
        )
        result = get_blueprint_status(query)
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_BLUEPRINT_STATUS_QUERY",
            message=str(exc),
        ) from exc
    return _blueprint_result_response(result)


@router.get(
    "/chief-engineer/blueprints/{blueprint_id}",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerBlueprintDetailResponse,
)
def get_chief_engineer_blueprint(
    request: Request,
    blueprint_id: str,
    workspace: str = "",
) -> ChiefEngineerBlueprintDetailResponse:
    """Load one persisted Chief Engineer blueprint by id."""

    safe_blueprint_id = _validate_blueprint_id(blueprint_id)
    payload = _persistence_for_request(request, ensure_directory=False, workspace=workspace).load(safe_blueprint_id)
    if not isinstance(payload, dict):
        raise StructuredHTTPException(
            status_code=404,
            code="BLUEPRINT_NOT_FOUND",
            message="blueprint not found",
        )
    return ChiefEngineerBlueprintDetailResponse(
        blueprint_id=_blueprint_id_from_payload(payload, safe_blueprint_id),
        blueprint=payload,
    )


@router.delete(
    "/chief-engineer/blueprints/{blueprint_id}",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerBlueprintDeleteResponse,
)
def delete_chief_engineer_blueprint(
    request: Request,
    blueprint_id: str,
    workspace: str = "",
) -> ChiefEngineerBlueprintDeleteResponse:
    """Delete one persisted Chief Engineer blueprint by id."""

    safe_blueprint_id = _validate_blueprint_id(blueprint_id)
    deleted = _persistence_for_request(request, ensure_directory=False, workspace=workspace).delete(safe_blueprint_id)
    if not deleted:
        raise StructuredHTTPException(
            status_code=404,
            code="BLUEPRINT_NOT_FOUND",
            message="blueprint not found",
        )
    return ChiefEngineerBlueprintDeleteResponse(
        ok=True,
        blueprint_id=safe_blueprint_id,
        deleted=True,
    )


# ═══════════════════════════════════════════════════════════════════════
# Tier-1 governance surface: Risk Register + Tech-Debt Ledger
# ═══════════════════════════════════════════════════════════════════════


def _governance_workspace(request: Request, workspace: str) -> str:
    """Resolve and validate the workspace for a governance request."""

    settings = _settings_for_request(request, workspace)
    target_workspace = _workspace_value(settings)
    if not target_workspace:
        raise StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_NOT_CONFIGURED",
            message="workspace is not configured",
        )
    return target_workspace


class ChiefEngineerRegisterRiskRequest(BaseModel):
    """Desktop request to register a Risk Register entry."""

    task_id: str
    title: str
    severity: str
    owner: str
    mitigation: str = ""
    links: list[str] = Field(default_factory=list)
    supersedes: str | None = None


class ChiefEngineerUpdateRiskStatusRequest(BaseModel):
    """Desktop request to transition a risk to a new status."""

    status: str
    note: str = ""
    actor: str = "chief_engineer"


class ChiefEngineerRegisterTechDebtRequest(BaseModel):
    """Desktop request to register a Tech-Debt Ledger entry."""

    title: str
    description: str = ""
    severity: str
    surface: str
    owner: str
    evidence: list[str] = Field(default_factory=list)


class ChiefEngineerUpdateTechDebtStatusRequest(BaseModel):
    """Desktop request to transition a tech-debt entry to a new status."""

    status: str
    note: str = ""
    actor: str = "chief_engineer"


@router.post("/chief-engineer/risks", dependencies=[Depends(require_auth)])
def register_chief_engineer_risk(
    request: Request,
    payload: ChiefEngineerRegisterRiskRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Register a new Risk Register entry for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_risk(
            RegisterRiskCommandV1(
                task_id=payload.task_id,
                title=payload.title,
                severity=RiskSeverity(payload.severity.strip().lower()),
                owner=payload.owner,
                mitigation=payload.mitigation,
                workspace=target_workspace,
                links=tuple(payload.links),
                supersedes=payload.supersedes,
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_RISK_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "risk": record.to_dict()}


@router.get("/chief-engineer/risks", dependencies=[Depends(require_auth)])
def list_chief_engineer_risks(
    request: Request,
    workspace: str = "",
    task_id: str = "",
    severity: str = "",
    status: str = "",
) -> dict[str, Any]:
    """List Risk Register entries for the workspace with optional filters."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListRisksQueryV1(
            workspace=target_workspace,
            task_id=task_id.strip() or None,
            severity=RiskSeverity(severity.strip().lower()) if severity.strip() else None,
            status=RiskStatus(status.strip().lower()) if status.strip() else None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_RISK_QUERY",
            message=str(exc),
        ) from exc
    records = list_risks(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "risks": [record.to_dict() for record in records],
        "summary": summarize_risks(target_workspace, task_id=task_id.strip() or None),
    }


@router.post("/chief-engineer/risks/{risk_id}/status", dependencies=[Depends(require_auth)])
def update_chief_engineer_risk_status(
    request: Request,
    risk_id: str,
    payload: ChiefEngineerUpdateRiskStatusRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Transition a Risk Register entry to a new status."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_risk_status(
            UpdateRiskStatusCommandV1(
                workspace=target_workspace,
                risk_id=risk_id,
                status=RiskStatus(payload.status.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_RISK_STATUS",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="RISK_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "risk": record.to_dict()}


@router.post("/chief-engineer/tech-debt", dependencies=[Depends(require_auth)])
def register_chief_engineer_tech_debt(
    request: Request,
    payload: ChiefEngineerRegisterTechDebtRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Register a new Tech-Debt Ledger entry for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_tech_debt(
            RegisterTechDebtCommandV1(
                title=payload.title,
                description=payload.description,
                severity=TechDebtSeverity(payload.severity.strip().lower()),
                surface=payload.surface,
                owner=payload.owner,
                workspace=target_workspace,
                evidence=tuple(payload.evidence),
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_DEBT_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "tech_debt": record.to_dict()}


@router.get("/chief-engineer/tech-debt", dependencies=[Depends(require_auth)])
def list_chief_engineer_tech_debt(
    request: Request,
    workspace: str = "",
    severity: str = "",
    surface: str = "",
    status: str = "",
) -> dict[str, Any]:
    """List Tech-Debt Ledger entries for the workspace with optional filters."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListTechDebtQueryV1(
            workspace=target_workspace,
            severity=TechDebtSeverity(severity.strip().lower()) if severity.strip() else None,
            surface=surface.strip() or None,
            status=TechDebtStatus(status.strip().lower()) if status.strip() else None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_DEBT_QUERY",
            message=str(exc),
        ) from exc
    records = list_tech_debt(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "tech_debt": [record.to_dict() for record in records],
        "summary": summarize_tech_debt(target_workspace, surface=surface.strip() or None),
    }


@router.post("/chief-engineer/tech-debt/{debt_id}/status", dependencies=[Depends(require_auth)])
def update_chief_engineer_tech_debt_status(
    request: Request,
    debt_id: str,
    payload: ChiefEngineerUpdateTechDebtStatusRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Transition a Tech-Debt Ledger entry to a new status."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_tech_debt_status(
            UpdateTechDebtStatusCommandV1(
                workspace=target_workspace,
                debt_id=debt_id,
                status=TechDebtStatus(payload.status.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_DEBT_STATUS",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="TECH_DEBT_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "tech_debt": record.to_dict()}


# ═══════════════════════════════════════════════════════════════════════
# Tier-2 governance surface: Architecture Decision Log
# ═══════════════════════════════════════════════════════════════════════


class ChiefEngineerRegisterADRRequest(BaseModel):
    """Desktop request to record an Architecture Decision Record."""

    title: str
    decision: str
    owner: str
    context: str = ""
    consequences: str = ""
    alternatives: list[str] = Field(default_factory=list)
    related_task_ids: list[str] = Field(default_factory=list)
    supersedes: str | None = None


class ChiefEngineerUpdateADRStatusRequest(BaseModel):
    """Desktop request to transition an ADR to a new status."""

    status: str
    note: str = ""
    actor: str = "chief_engineer"


@router.post("/chief-engineer/adrs", dependencies=[Depends(require_auth)])
def register_chief_engineer_adr(
    request: Request,
    payload: ChiefEngineerRegisterADRRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Record a new Architecture Decision Record for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_adr(
            RegisterADRCommandV1(
                title=payload.title,
                decision=payload.decision,
                owner=payload.owner,
                workspace=target_workspace,
                context=payload.context,
                consequences=payload.consequences,
                alternatives=tuple(payload.alternatives),
                related_task_ids=tuple(payload.related_task_ids),
                supersedes=payload.supersedes,
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_ADR_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "adr": record.to_dict()}


@router.get("/chief-engineer/adrs", dependencies=[Depends(require_auth)])
def list_chief_engineer_adrs(
    request: Request,
    workspace: str = "",
    status: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """List Architecture Decision Records with optional filters."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListADRsQueryV1(
            workspace=target_workspace,
            status=ADRStatus(status.strip().lower()) if status.strip() else None,
            task_id=task_id.strip() or None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_ADR_QUERY",
            message=str(exc),
        ) from exc
    records = list_adrs(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "adrs": [record.to_dict() for record in records],
        "summary": summarize_adrs(target_workspace),
    }


@router.post("/chief-engineer/adrs/{adr_id}/status", dependencies=[Depends(require_auth)])
def update_chief_engineer_adr_status(
    request: Request,
    adr_id: str,
    payload: ChiefEngineerUpdateADRStatusRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Transition an Architecture Decision Record to a new status."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_adr_status(
            UpdateADRStatusCommandV1(
                workspace=target_workspace,
                adr_id=adr_id,
                status=ADRStatus(payload.status.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_ADR_STATUS",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="ADR_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "adr": record.to_dict()}


# ═══════════════════════════════════════════════════════════════════════
# Tier-2 gate enforcement: Director-handoff decision
# ═══════════════════════════════════════════════════════════════════════


@router.get("/chief-engineer/handoff-decision", dependencies=[Depends(require_auth)])
def get_chief_engineer_handoff_decision(
    request: Request,
    blueprint_id: str,
    workspace: str = "",
) -> dict[str, Any]:
    """Return the Director-handoff gate decision for a persisted blueprint.

    The enforcement-consultation surface: PM / Director / desktop call this
    before dispatch to learn whether the blueprint is cleared for handoff.
    A missing blueprint is fail-closed: ``allowed=False``.
    """
    target_workspace = _governance_workspace(request, workspace)
    safe_blueprint_id = _validate_blueprint_id(blueprint_id)
    decision = evaluate_handoff_decision_for_blueprint(target_workspace, safe_blueprint_id)
    if decision is None:
        return {
            "ok": True,
            "workspace": target_workspace,
            "decision": {
                "allowed": False,
                "blueprint_id": safe_blueprint_id,
                "task_id": "",
                "blocker_count": 0,
                "warning_count": 0,
                "open_blocker_risk_count": 0,
                "blockers": [],
                "reason": "blueprint_not_found",
                "evaluated_at": "",
            },
        }
    return {"ok": True, "workspace": target_workspace, "decision": decision.to_dict()}


# ═══════════════════════════════════════════════════════════════════════
# Tier-2 stack/library policy: Tech Radar
# ═══════════════════════════════════════════════════════════════════════


class ChiefEngineerRegisterTechRadarRequest(BaseModel):
    """Desktop request to place a library on a Tech-Radar ring."""

    library: str
    ring: str
    owner: str
    rationale: str = ""
    supersedes: str | None = None


class ChiefEngineerUpdateTechRadarRingRequest(BaseModel):
    """Desktop request to move a Tech-Radar entry to a new ring."""

    ring: str
    note: str = ""
    actor: str = "chief_engineer"


class ChiefEngineerStackPolicyCheckRequest(BaseModel):
    """Desktop request to check libraries against the Tech Radar."""

    libraries: list[str] = Field(default_factory=list)


@router.post("/chief-engineer/tech-radar", dependencies=[Depends(require_auth)])
def register_chief_engineer_tech_radar(
    request: Request,
    payload: ChiefEngineerRegisterTechRadarRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Place a library on a Tech-Radar ring for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_tech_radar(
            RegisterTechRadarCommandV1(
                library=payload.library,
                ring=TechRadarRing(payload.ring.strip().lower()),
                owner=payload.owner,
                workspace=target_workspace,
                rationale=payload.rationale,
                supersedes=payload.supersedes,
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_RADAR_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "entry": record.to_dict()}


@router.get("/chief-engineer/tech-radar", dependencies=[Depends(require_auth)])
def list_chief_engineer_tech_radar(
    request: Request,
    workspace: str = "",
    ring: str = "",
) -> dict[str, Any]:
    """List Tech-Radar entries with an optional ring filter."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListTechRadarQueryV1(
            workspace=target_workspace,
            ring=TechRadarRing(ring.strip().lower()) if ring.strip() else None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_RADAR_QUERY",
            message=str(exc),
        ) from exc
    records = list_tech_radar(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "entries": [record.to_dict() for record in records],
        "summary": summarize_tech_radar(target_workspace),
    }


@router.post("/chief-engineer/tech-radar/{entry_id}/ring", dependencies=[Depends(require_auth)])
def update_chief_engineer_tech_radar_ring(
    request: Request,
    entry_id: str,
    payload: ChiefEngineerUpdateTechRadarRingRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Move a Tech-Radar entry to a new ring."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_tech_radar_ring(
            UpdateTechRadarRingCommandV1(
                workspace=target_workspace,
                entry_id=entry_id,
                ring=TechRadarRing(payload.ring.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_RADAR_RING",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="TECH_RADAR_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "entry": record.to_dict()}


@router.post("/chief-engineer/stack-policy/check", dependencies=[Depends(require_auth)])
def check_chief_engineer_stack_policy(
    request: Request,
    payload: ChiefEngineerStackPolicyCheckRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Check a list of libraries against the Tech Radar (hold/deprecated)."""

    target_workspace = _governance_workspace(request, workspace)
    violations = check_stack_policy(target_workspace, list(payload.libraries))
    return {
        "ok": True,
        "workspace": target_workspace,
        "allowed": not violations,
        "violations": [v.to_dict() for v in violations],
    }


# ═══════════════════════════════════════════════════════════════════════
# Tier-2 incident learning: Post-Mortem / Incident Review
# ═══════════════════════════════════════════════════════════════════════


class ChiefEngineerRegisterPostMortemRequest(BaseModel):
    """Desktop request to record a post-mortem / incident review."""

    title: str
    severity: str
    occurred_at: str
    owner: str
    summary: str = ""
    root_cause: str = ""
    impact: str = ""
    timeline: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    related_risk_ids: list[str] = Field(default_factory=list)


class ChiefEngineerUpdatePostMortemStatusRequest(BaseModel):
    """Desktop request to transition a post-mortem to a new status."""

    status: str
    note: str = ""
    actor: str = "chief_engineer"


@router.post("/chief-engineer/post-mortems", dependencies=[Depends(require_auth)])
def register_chief_engineer_post_mortem(
    request: Request,
    payload: ChiefEngineerRegisterPostMortemRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Record a new post-mortem / incident review for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_post_mortem(
            RegisterPostMortemCommandV1(
                title=payload.title,
                severity=IncidentSeverity(payload.severity.strip().lower()),
                occurred_at=payload.occurred_at,
                owner=payload.owner,
                workspace=target_workspace,
                summary=payload.summary,
                root_cause=payload.root_cause,
                impact=payload.impact,
                timeline=tuple(payload.timeline),
                action_items=tuple(payload.action_items),
                related_risk_ids=tuple(payload.related_risk_ids),
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_POST_MORTEM_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "post_mortem": record.to_dict()}


@router.get("/chief-engineer/post-mortems", dependencies=[Depends(require_auth)])
def list_chief_engineer_post_mortems(
    request: Request,
    workspace: str = "",
    severity: str = "",
    status: str = "",
) -> dict[str, Any]:
    """List post-mortems with optional filters."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListPostMortemsQueryV1(
            workspace=target_workspace,
            severity=IncidentSeverity(severity.strip().lower()) if severity.strip() else None,
            status=PostMortemStatus(status.strip().lower()) if status.strip() else None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_POST_MORTEM_QUERY",
            message=str(exc),
        ) from exc
    records = list_post_mortems(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "post_mortems": [record.to_dict() for record in records],
        "summary": summarize_post_mortems(target_workspace),
    }


@router.post("/chief-engineer/post-mortems/{incident_id}/status", dependencies=[Depends(require_auth)])
def update_chief_engineer_post_mortem_status(
    request: Request,
    incident_id: str,
    payload: ChiefEngineerUpdatePostMortemStatusRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Transition a post-mortem to a new status."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_post_mortem_status(
            UpdatePostMortemStatusCommandV1(
                workspace=target_workspace,
                incident_id=incident_id,
                status=PostMortemStatus(payload.status.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_POST_MORTEM_STATUS",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="POST_MORTEM_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "post_mortem": record.to_dict()}


# ═══════════════════════════════════════════════════════════════════════
# Tier-2 capstone: Release Readiness / Change-Advisory
# ═══════════════════════════════════════════════════════════════════════


def _split_csv(value: str) -> list[str]:
    return [token.strip() for token in str(value or "").split(",") if token.strip()]


@router.get("/chief-engineer/release-readiness", dependencies=[Depends(require_auth)])
def get_chief_engineer_release_readiness(
    request: Request,
    workspace: str = "",
    blueprint_ids: str = "",
    libraries: str = "",
) -> dict[str, Any]:
    """Executive GO / NO-GO that aggregates the whole governance surface.

    Optional comma-separated ``blueprint_ids`` (release candidates, each run
    through the Quality Gate) and ``libraries`` (checked against the Tech
    Radar stack policy). Read-only and fail-closed.
    """
    target_workspace = _governance_workspace(request, workspace)
    # Validate every blueprint id at the boundary (defense-in-depth, mirrors the
    # handoff-decision route) so an unvalidated id can never reach the filesystem.
    validated_ids = [_validate_blueprint_id(bid) for bid in _split_csv(blueprint_ids)] or None
    decision = assess_release_readiness(
        target_workspace,
        blueprint_ids=validated_ids,
        libraries=_split_csv(libraries) or None,
    )
    return {"ok": True, "workspace": target_workspace, "readiness": decision.to_dict()}
