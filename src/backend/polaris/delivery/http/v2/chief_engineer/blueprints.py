"""Blueprint lifecycle route handlers for the Chief Engineer v2 router.

Lossless extraction of the blueprint lifecycle domain (generate / list /
status / get / delete / bulk + PM-task-plan reference sync) from the former
single-file ``chief_engineer`` module.

Test-patchable external symbols (``BlueprintPersistence``,
``generate_task_blueprint``, ``get_blueprint_status``,
``ensure_required_roles_ready``) are resolved through the live package
namespace (``_ce.<Name>``) at call time so that
``monkeypatch.setattr("...chief_engineer.<Name>", ...)`` keeps being observed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, Request
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    TaskBlueprintResultV1,
)
from polaris.cells.runtime.projection.public.role_contracts import (
    ChiefEngineerBlueprintSummaryV1,
)
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    require_auth,
)
from polaris.delivery.http.v2.chief_engineer._router import (
    _pm_task_plan_rows,
    _read_json_file,
    _settings_for_request,
    _state_for_settings,
    _string_list,
    _task_id_from_plan_task,
    _validate_blueprint_id,
    _workspace_value,
    router,
)
from polaris.delivery.http.v2.chief_engineer._schemas import (
    ChiefEngineerBlueprintDeleteResponse,
    ChiefEngineerBlueprintDetailResponse,
    ChiefEngineerBlueprintListResponse,
    ChiefEngineerBlueprintSummary,
    ChiefEngineerBulkBlueprintError,
    ChiefEngineerBulkBlueprintTaskRequest,
    ChiefEngineerBulkGenerateBlueprintRequest,
    ChiefEngineerBulkGenerateBlueprintResponse,
    ChiefEngineerGenerateBlueprintRequest,
    ChiefEngineerTaskBlueprintResultResponse,
)
from polaris.kernelone.fs.text_ops import write_json_atomic


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


def _persistence_for_request(
    request: Request,
    *,
    ensure_directory: bool = True,
    workspace: str = "",
) -> BlueprintPersistence:
    import polaris.delivery.http.v2.chief_engineer as _ce

    settings = _settings_for_request(request, workspace)
    target_workspace = _workspace_value(settings)
    if not target_workspace:
        raise StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_NOT_CONFIGURED",
            message="workspace is not configured",
        )
    return _ce.BlueprintPersistence(target_workspace, ensure_directory=ensure_directory)


def _blueprint_payload_for_result(result: TaskBlueprintResultV1) -> dict[str, Any]:
    import polaris.delivery.http.v2.chief_engineer as _ce

    if not result.blueprint_id:
        return {}
    payload = _ce.BlueprintPersistence(result.workspace, ensure_directory=False).load(result.blueprint_id)
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
    import polaris.delivery.http.v2.chief_engineer as _ce

    command = GenerateTaskBlueprintCommandV1(
        task_id=item.task_id,
        workspace=workspace,
        objective=item.objective,
        run_id=item.run_id,
        constraints=item.constraints,
        context=item.context,
    )
    return _ce.generate_task_blueprint(command)


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
    # Local import to keep the module import graph acyclic; the candidate-path
    # helper lives in the diagnostics domain module (it owns the
    # test-patchable ``resolve_logical_path`` resolution).
    from polaris.delivery.http.v2.chief_engineer.diagnostics import _pm_task_plan_candidate_paths

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

    import polaris.delivery.http.v2.chief_engineer as _ce

    settings = _settings_for_request(request, workspace)
    _ce.ensure_required_roles_ready(
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

    import polaris.delivery.http.v2.chief_engineer as _ce

    if not payload.tasks:
        raise StructuredHTTPException(
            status_code=400,
            code="EMPTY_BLUEPRINT_BATCH",
            message="at least one task is required",
        )

    settings = _settings_for_request(request, workspace)
    _ce.ensure_required_roles_ready(
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

    import polaris.delivery.http.v2.chief_engineer as _ce

    target_workspace = _workspace_value(_settings_for_request(request, workspace))
    try:
        query = GetBlueprintStatusQueryV1(
            task_id=task_id,
            workspace=target_workspace,
            run_id=run_id,
        )
        result = _ce.get_blueprint_status(query)
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


__all__ = [
    "_apply_blueprint_references_to_plan_payload",
    "_blueprint_id_from_payload",
    "_blueprint_payload_for_result",
    "_blueprint_reference_update",
    "_blueprint_result_response",
    "_blueprint_summary",
    "_generate_blueprint_for_task",
    "_persistence_for_request",
    "_run_contract_copy_path",
    "_sync_blueprint_references_or_raise",
    "_sync_blueprint_references_to_pm_task_plans",
    "bulk_generate_chief_engineer_blueprints",
    "delete_chief_engineer_blueprint",
    "generate_chief_engineer_blueprint",
    "get_chief_engineer_blueprint",
    "get_chief_engineer_blueprint_status",
    "list_chief_engineer_blueprints",
]
