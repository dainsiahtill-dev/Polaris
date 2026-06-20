"""Director readiness diagnostics + blueprint-evidence helpers (v2 router).

Extracted from ``polaris.delivery.http.v2.director`` during the lossless module
split. This module computes the side-effect-free Director desktop readiness
snapshot: blueprint payload loaders, blueprint-artifact state resolution, task /
worker / LLM diagnostics sections, and the run-gating helpers.

Monkeypatch contract (CRITICAL): tests patch collaborators on the ``director``
module namespace (``director.RuntimeProjectionService``,
``director.BlueprintPersistence``, ``director.build_llm_status``,
``director.resolve_artifact_path``, ``director.Path``, ``director.logger``,
``director.ensure_required_roles_ready``, ``director._contract_backed_task_rows``
``director._build_director_diagnostics_for_request`` ...) and then drive full
routes. Every such collaborator referenced from a helper that lives here is
dereferenced through the ``director`` module object at call time
(``from . import director as _d; _d.X(...)``) so the patch is honored. The lazy
``director`` import also breaks the import cycle (``director`` imports the names
defined here; these helpers reach back into ``director`` only at call time).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

from fastapi import Request, status
from polaris.delivery.http.routers._shared import StructuredHTTPException
from polaris.delivery.http.v2.director_models import (
    DirectorDiagnosticsLLMSection,
    DirectorDiagnosticsResponse,
    DirectorDiagnosticsStatusSection,
    DirectorDiagnosticsTaskSection,
    DirectorDiagnosticsWorkerSection,
)
from polaris.domain.entities import TaskPriority

if TYPE_CHECKING:
    from polaris.cells.director.execution.public.service import DirectorService


def _load_blueprint_payload_by_id(workspace: str, blueprint_id: str) -> dict[str, Any] | None:
    from polaris.delivery.http.v2 import director as _d

    if not blueprint_id:
        return None
    try:
        payload = _d.BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    except (OSError, RuntimeError, TypeError, ValueError):
        _d.logger.debug("Director blueprint persistence probe failed for blueprint_id=%s", blueprint_id, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _load_all_blueprint_payloads(workspace: str) -> list[dict[str, Any]]:
    from polaris.delivery.http.v2 import director as _d

    if not str(workspace or "").strip():
        return []
    try:
        persistence = _d.BlueprintPersistence(workspace, ensure_directory=False)
        blueprint_ids = persistence.list_all()
    except (OSError, RuntimeError, TypeError, ValueError):
        _d.logger.debug("Director blueprint persistence scan failed for workspace=%s", workspace, exc_info=True)
        return []

    payloads: list[dict[str, Any]] = []
    for blueprint_id in blueprint_ids:
        try:
            payload = persistence.load(str(blueprint_id or "").strip())
        except (OSError, RuntimeError, TypeError, ValueError):
            _d.logger.debug("Director blueprint payload scan failed for blueprint_id=%s", blueprint_id, exc_info=True)
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _resolve_blueprint_path(workspace: str, cache_root: str, value: str) -> Path | None:
    from polaris.delivery.http.v2 import director as _d

    token = str(value or "").strip()
    if not token:
        return None

    try:
        raw_path = _d.Path(token)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            normalized = token.replace("\\", "/")
            if normalized == "runtime" or normalized.startswith("runtime/"):
                resolved = _d.Path(_d.resolve_artifact_path(workspace, cache_root, normalized)).resolve()
            else:
                resolved = (_d.Path(workspace) / token).resolve()
    except (OSError, RuntimeError, ValueError):
        return None

    if _d._path_is_within(resolved, workspace) or _d._path_is_within(resolved, cache_root):
        return resolved
    return None


def _load_blueprint_payload_by_path(workspace: str, cache_root: str, path_value: str) -> dict[str, Any] | None:
    from polaris.delivery.http.v2 import director as _d

    path = _d._resolve_blueprint_path(workspace, cache_root, path_value)
    if path is None or not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        _d.logger.debug("Director blueprint path probe failed for path=%s", path, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _blueprint_artifact_state(
    *,
    workspace: str,
    cache_root: str,
    task_id: str,
    details: dict[str, Any],
    blueprint_payloads: list[dict[str, Any]] | None = None,
) -> Literal["valid", "missing", "invalid"]:
    from polaris.delivery.http.v2 import director as _d

    blueprint_id, blueprint_path, runtime_blueprint_path = _d._blueprint_reference_values(details)
    identities = _d._task_identity_tokens(task_id, details)
    if not any((blueprint_id, blueprint_path, runtime_blueprint_path)):
        available_payloads = (
            blueprint_payloads if blueprint_payloads is not None else _d._load_all_blueprint_payloads(workspace)
        )
        if any(_d._blueprint_payload_matches_task(payload, identities) for payload in available_payloads):
            return "valid"
        return "missing"

    payloads: list[dict[str, Any]] = []
    id_payload = _d._load_blueprint_payload_by_id(workspace, blueprint_id)
    if id_payload is not None:
        payloads.append(id_payload)

    for path_value in (runtime_blueprint_path, blueprint_path):
        path_payload = _d._load_blueprint_payload_by_path(workspace, cache_root, path_value)
        if path_payload is not None:
            payloads.append(path_payload)

    if any(_d._blueprint_payload_matches_task(payload, identities) for payload in payloads):
        return "valid"
    return "invalid"


def _task_diagnostics_from_rows(
    rows: list[dict[str, Any]],
    source: str,
    *,
    workspace: str = "",
    cache_root: str = "",
) -> DirectorDiagnosticsTaskSection:
    from polaris.delivery.http.v2 import director as _d

    pending = 0
    claimed = 0
    running = 0
    blocked = 0
    failed = 0
    completed = 0
    cancelled = 0
    ready_task_ids: list[str] = []
    blueprint_ready_task_ids: list[str] = []
    missing_blueprint_task_ids: list[str] = []
    invalid_blueprint_task_ids: list[str] = []
    blocked_task_ids: list[str] = []
    running_task_ids: list[str] = []
    blueprint_payloads = _d._load_all_blueprint_payloads(workspace) if source == "workflow" else []
    completed_identity_tokens: set[str] = set()
    blocking_identity_tokens: set[str] = set()
    row_details: list[tuple[str, dict[str, Any]]] = []

    for row in rows:
        task_id = _d._task_id_from_row(row)
        details = _d._task_details(row)
        row_details.append((task_id, details))
        status_token = str(details["status"])
        if status_token == "COMPLETED":
            completed_identity_tokens.update(_d._task_identity_tokens(task_id, details))
        elif status_token in {"FAILED", "BLOCKED", "CANCELLED"}:
            blocking_identity_tokens.update(_d._task_identity_tokens(task_id, details))

    changed = True
    while changed:
        changed = False
        for task_id, details in row_details:
            status_token = str(details["status"])
            if status_token != "PENDING":
                continue
            dependencies = details["dependencies"]
            if any(str(dependency or "").strip() in blocking_identity_tokens for dependency in dependencies):
                identities = _d._task_identity_tokens(task_id, details)
                new_tokens = identities.difference(blocking_identity_tokens)
                if new_tokens:
                    blocking_identity_tokens.update(new_tokens)
                    changed = True

    for row in rows:
        task_id = _d._task_id_from_row(row)
        details = _d._task_details(row)
        status_token = str(details["status"])
        dependencies = details["dependencies"]
        requires_blueprint_evidence = _d._row_requires_blueprint_evidence(row, source=source)
        blueprint_state = (
            _d._blueprint_artifact_state(
                workspace=workspace,
                cache_root=cache_root,
                task_id=task_id,
                details=details,
                blueprint_payloads=blueprint_payloads,
            )
            if requires_blueprint_evidence and task_id
            else "valid"
        )

        if status_token == "PENDING":
            unmet_dependencies = [
                str(dependency or "").strip()
                for dependency in dependencies
                if str(dependency or "").strip() and str(dependency or "").strip() not in completed_identity_tokens
            ]
            blocking_dependencies = [
                dependency for dependency in unmet_dependencies if dependency in blocking_identity_tokens
            ]
            if blocking_dependencies:
                blocked += 1
                if task_id:
                    blocked_task_ids.append(task_id)
                continue
            pending += 1
            if not unmet_dependencies and task_id:
                if blueprint_state == "missing":
                    missing_blueprint_task_ids.append(task_id)
                elif blueprint_state == "invalid":
                    invalid_blueprint_task_ids.append(task_id)
                else:
                    ready_task_ids.append(task_id)
                    if requires_blueprint_evidence:
                        blueprint_ready_task_ids.append(task_id)
        elif status_token == "CLAIMED":
            claimed += 1
            running += 1
            if task_id:
                running_task_ids.append(task_id)
        elif status_token == "RUNNING":
            running += 1
            if task_id:
                running_task_ids.append(task_id)
        elif status_token == "BLOCKED":
            blocked += 1
            if task_id:
                blocked_task_ids.append(task_id)
        elif status_token == "FAILED":
            failed += 1
        elif status_token == "COMPLETED":
            completed += 1
        elif status_token == "CANCELLED":
            cancelled += 1
        else:
            pending += 1

    total = len(rows)
    return DirectorDiagnosticsTaskSection(
        ok=total > 0 and bool(ready_task_ids or running_task_ids) and blocked == 0 and failed == 0,
        source=source,
        total=total,
        pending=pending,
        claimed=claimed,
        running=running,
        blocked=blocked,
        failed=failed,
        completed=completed,
        cancelled=cancelled,
        ready_to_execute=len(ready_task_ids),
        ready_task_ids=ready_task_ids,
        blueprint_ready_task_ids=blueprint_ready_task_ids,
        missing_blueprint_task_ids=missing_blueprint_task_ids,
        invalid_blueprint_task_ids=invalid_blueprint_task_ids,
        blocked_task_ids=blocked_task_ids,
        running_task_ids=running_task_ids,
    )


def _build_llm_diagnostics(settings: Any) -> DirectorDiagnosticsLLMSection:
    from polaris.delivery.http.v2 import director as _d

    try:
        payload = _d.build_llm_status(settings)
    except (RuntimeError, OSError, ValueError) as exc:
        return DirectorDiagnosticsLLMSection(
            ok=False,
            state="error",
            blocked_roles=["director"],
            error=str(exc),
        )

    if not isinstance(payload, dict):
        return DirectorDiagnosticsLLMSection(
            ok=False,
            state="error",
            blocked_roles=["director"],
            error="invalid_llm_status_payload",
        )

    role_info = _d._role_payload(payload, "director")
    ready = bool(role_info.get("ready"))
    runtime_supported = bool(role_info.get("runtime_supported"))
    blocked_roles = _d._string_list(payload.get("blocked_roles"))
    unsupported_roles = _d._string_list(payload.get("unsupported_roles"))
    required_ready_roles = _d._string_list(payload.get("required_ready_roles"))
    if "director" not in required_ready_roles:
        required_ready_roles.append("director")
    if not ready and "director" not in blocked_roles:
        blocked_roles.append("director")
    if not runtime_supported and "director" not in unsupported_roles:
        unsupported_roles.append("director")

    ok = ready and runtime_supported
    return DirectorDiagnosticsLLMSection(
        ok=ok,
        state="ready" if ok else "blocked",
        blocked_roles=blocked_roles,
        unsupported_roles=unsupported_roles,
        required_ready_roles=required_ready_roles,
        provider_id=str(role_info.get("provider_id") or "").strip() or None,
        model=str(role_info.get("model") or "").strip() or None,
        details=payload,
    )


async def _build_director_diagnostics(
    request: Request,
    service: DirectorService,
    workspace_override: str | None = None,
) -> DirectorDiagnosticsResponse:
    """Build a side-effect-free Director readiness snapshot."""

    from polaris.delivery.http.v2 import director as _d

    state = getattr(request.app.state, "app_state", None) or request.app.state
    settings = _d.settings_with_workspace_override(state.settings, workspace_override or "")
    workspace = _d.active_workspace_value(settings)
    ramdisk_root = str(getattr(settings, "ramdisk_root", "") or _d.resolve_env_str("ramdisk_root") or "").strip()
    cache_root = _d.build_cache_root(ramdisk_root, workspace)

    status_section = DirectorDiagnosticsStatusSection(
        ok=False,
        state="UNKNOWN",
        running=False,
        error="projection unavailable",
    )
    task_rows: list[dict[str, Any]] = []
    projected_workers: list[dict[str, Any]] = []
    task_source = "empty"

    try:
        projection = await _d.RuntimeProjectionService.build_async(workspace, state=state)
        selected_status = (
            getattr(projection, "director_merged", None)
            if getattr(projection, "director_merged", None)
            else projection.director_local
        )
        projection_source = "director_merged" if getattr(projection, "director_merged", None) else "director_local"
        local_status = _d._flatten_director_status(selected_status or {"running": False, "status": {"state": "IDLE"}})
        status_section = DirectorDiagnosticsStatusSection(
            ok=True,
            state=str(local_status.get("state") or "IDLE"),
            running=bool(local_status.get("running")),
            source=str(local_status.get("source") or "none"),
            projection_source=projection_source,
        )
        task_rows = _d._projection_task_rows(projection)
        if task_rows:
            task_rows = _d._runtime_backed_task_rows(task_rows, workspace=workspace)
            task_rows = _d._contract_backed_task_rows(task_rows, workspace=workspace, cache_root=cache_root)
        task_market_rows = _d._task_market_execution_rows_for_workspace(workspace)
        if task_market_rows:
            task_rows = _d._merge_task_rows_by_identity(task_rows, task_market_rows)
        projected_workers = _d._worker_rows_from_projection(projection)
        task_source = "workflow" if task_rows else "empty"
    except (RuntimeError, ValueError) as exc:
        _d.logger.debug("Director diagnostics projection unavailable for workspace=%s", workspace, exc_info=True)
        status_section.error = str(exc)

    if not task_rows:
        task_market_rows = _d._task_market_execution_rows_for_workspace(workspace)
        if task_market_rows:
            task_rows = task_market_rows
            task_source = "workflow"
            task_section = _d._task_diagnostics_from_rows(
                task_rows, task_source, workspace=workspace, cache_root=cache_root
            )
        else:
            try:
                task_rows = _d._task_rows_from_local_tasks(await service.list_tasks(status=None))
                task_source = "local" if task_rows else "empty"
            except (RuntimeError, ValueError, AttributeError) as exc:
                _d.logger.debug("Director diagnostics local task queue unavailable", exc_info=True)
                task_section = DirectorDiagnosticsTaskSection(
                    ok=False,
                    source="error",
                    error=str(exc),
                )
            else:
                task_rows = _d._runtime_backed_task_rows(task_rows, workspace=workspace)
                task_section = _d._task_diagnostics_from_rows(
                    task_rows, task_source, workspace=workspace, cache_root=""
                )
    else:
        task_section = _d._task_diagnostics_from_rows(
            task_rows,
            task_source,
            workspace=workspace,
            cache_root=cache_root,
        )

    try:
        workers = await service.list_workers()
    except (RuntimeError, ValueError, AttributeError) as exc:
        _d.logger.debug("Director diagnostics worker pool unavailable", exc_info=True)
        worker_section = (
            _d._worker_diagnostics_from_workers(projected_workers)
            if projected_workers
            else DirectorDiagnosticsWorkerSection(ok=False, error=str(exc))
        )
    else:
        worker_rows = list(workers)
        worker_section = _d._worker_diagnostics_from_workers(worker_rows if worker_rows else projected_workers)

    llm_section = _d._build_llm_diagnostics(settings)
    issues = _d._director_diagnostic_issues(status_section, task_section, worker_section, llm_section)
    execution_blockers = _d._director_execution_blockers(status_section, task_section, worker_section, llm_section)
    return DirectorDiagnosticsResponse(
        ok=not issues,
        can_execute=not execution_blockers,
        generated_at=_d._utc_now(),
        workspace=workspace,
        status=status_section,
        tasks=task_section,
        workers=worker_section,
        llm=llm_section,
        issues=issues,
        execution_blockers=execution_blockers,
    )


async def _build_director_diagnostics_for_request(
    request: Request,
    workspace: str,
) -> DirectorDiagnosticsResponse:
    """Resolve DirectorService and build diagnostics for guarded run starts."""

    from polaris.delivery.http.v2 import director as _d

    service = await _d.get_director_service_dep(request)
    return await _d._build_director_diagnostics(request, service, workspace_override=workspace)


def _ensure_director_can_execute(diagnostics: DirectorDiagnosticsResponse) -> None:
    """Raise a structured 409 when Director execution prerequisites are not met."""

    if diagnostics.can_execute and not diagnostics.execution_blockers:
        return

    raise StructuredHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        code="DIRECTOR_EXECUTION_BLOCKED",
        message="Director execution prerequisites are not satisfied",
        details={
            "execution_blockers": diagnostics.execution_blockers,
            "issues": diagnostics.issues,
            "diagnostics": diagnostics.model_dump(mode="json"),
        },
    )


def _ensure_director_lifecycle_can_start(request: Request, workspace: str = "") -> None:
    """Ensure the Director role LLM runtime is ready before service startup."""

    from polaris.delivery.http.v2 import director as _d

    state = getattr(request.app.state, "app_state", None) or request.app.state
    gate_state: Any = state
    if str(workspace or "").strip():
        gate_state = SimpleNamespace(settings=_d.settings_with_workspace_override(state.settings, workspace))
    _d.ensure_required_roles_ready(gate_state, default_roles=["director"], force_first="director")


def _parse_task_priority(value: str) -> TaskPriority:
    token = str(value or "").strip()
    if not token:
        return TaskPriority.MEDIUM

    name_token = token.upper().replace("-", "_")
    by_name = TaskPriority.__members__.get(name_token)
    if by_name is not None:
        return by_name

    value_token = token.lower()
    for priority in TaskPriority:
        if priority.value == value_token:
            return priority

    allowed = sorted({item.name for item in TaskPriority} | {item.value for item in TaskPriority})
    raise StructuredHTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="INVALID_TASK_PRIORITY",
        message="invalid task priority",
        details={"priority": token, "allowed": allowed},
    )


__all__ = [
    "_blueprint_artifact_state",
    "_build_director_diagnostics",
    "_build_director_diagnostics_for_request",
    "_build_llm_diagnostics",
    "_ensure_director_can_execute",
    "_ensure_director_lifecycle_can_start",
    "_load_all_blueprint_payloads",
    "_load_blueprint_payload_by_id",
    "_load_blueprint_payload_by_path",
    "_parse_task_priority",
    "_resolve_blueprint_path",
    "_task_diagnostics_from_rows",
]
