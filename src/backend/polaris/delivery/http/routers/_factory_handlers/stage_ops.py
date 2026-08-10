# ruff: noqa: E402, F403, F405
"""Factory HTTP router helpers — payload builders, cores, bench session utils.

Extracted from factory.py so route registration stays thin. External callers that
historically imported private helpers from factory.py continue to re-export them
from polaris.delivery.http.routers.factory.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.control_plane.run_ledger.public.contracts import ReadRunLedgerProjectionQueryV1
from polaris.cells.control_plane.run_ledger.public.service import read_run_ledger_projection
from polaris.cells.factory.pipeline.public import (
    FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
    FactoryRun,
    FactoryRunService,
    FactoryTerminalTaskRuntimeProjectionV1,
)
from polaris.cells.factory.pipeline.public.types import (
    FactoryStartRequest,
)
from polaris.cells.runtime.task_runtime.public.evidence import task_row_execution_event_failure
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    ensure_required_roles_ready,
)
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.llm.budget_policy import resolve_director_dispatch_timeout_seconds
from polaris.kernelone.quality import (
    ScopeAuthorityOwnerHandoffIndex,
    ScopeAuthorityOwnerHandoffRouting,
    owner_handoff_index_summary,
    ownership_handoff_requests_from_scope_payload,
    resolve_owner_handoff_routing,
    task_identifier_token_aliases,
    task_record_routing_key,
)
from polaris.kernelone.storage import resolve_logical_path, resolve_runtime_path, resolve_storage_roots

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from .mapping import *


def _check_docs_ready(workspace: str) -> bool:
    """Check whether required docs are already present."""
    workspace_path = Path(workspace)
    docs_to_check = [
        workspace_path / "SPEC.md",
        workspace_path / "requirements.md",
        workspace_path / "docs" / "SPEC.md",
        workspace_path / "docs" / "requirements.md",
    ]
    return any(doc.exists() for doc in docs_to_check)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pm_plan_task_count(workspace: str) -> int:
    payload = _load_json_object(Path(resolve_runtime_path(workspace, "runtime/tasks/plan.json")))
    tasks = payload.get("tasks")
    return len(tasks) if isinstance(tasks, list) else 0


def _pm_plan_task_ids(workspace: str) -> tuple[str, ...]:
    """Return canonical PM task ids that Director-local rework may reopen."""

    payload = _load_json_object(Path(resolve_runtime_path(workspace, "runtime/tasks/plan.json")))
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return ()
    task_ids: list[str] = []
    seen: set[str] = set()
    for item in tasks:
        task_id = _resolve_task_identifier(item)
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        task_ids.append(task_id)
    return tuple(task_ids)


def _director_resume_task_files(task_dir: Path) -> list[Path]:
    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(task_dir)
    task_files = inspection.get("task_files")
    if not isinstance(task_files, list):
        return []
    return [task_dir / str(name) for name in task_files if str(name or "").strip()]


def _director_resume_task_payloads(task_dir: Path) -> list[dict[str, Any]]:
    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(task_dir)
    task_rows = inspection.get("task_rows")
    return [dict(row) for row in task_rows if isinstance(row, dict)] if isinstance(task_rows, list) else []


def _director_resume_task_rows_mtime(task_dir: Path) -> float:
    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(task_dir)
    try:
        return float(inspection.get("latest_mtime") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _taskboard_record_count(workspace: str) -> int:
    task_dir = Path(resolve_runtime_path(workspace, "runtime/tasks"))
    return len(_director_resume_task_files(task_dir))


def _director_resume_workspace_slug(workspace_key: str) -> str:
    match = re.match(r"^(?P<slug>.+)-[0-9a-f]{12}$", workspace_key)
    return str(match.group("slug")) if match else workspace_key


def _director_resume_source_task_dirs(workspace: str) -> list[Path]:
    roots = resolve_storage_roots(workspace)
    current_task_dir = Path(resolve_runtime_path(workspace, "runtime/tasks")).resolve()
    slug = _director_resume_workspace_slug(str(roots.workspace_key))
    runtime_project_bases = [
        Path(roots.runtime_projects_root),
        Path(os.path.expanduser("~/.cache/polaris")) / ".polaris" / "projects",
        Path(os.path.expanduser("~/.cache/kernelone")) / ".polaris" / "projects",
    ]
    candidates: list[Path] = []
    with contextlib.suppress(OSError):
        for runtime_projects_root in dict.fromkeys(runtime_project_bases):
            if not runtime_projects_root.exists():
                continue
            for project_root in runtime_projects_root.glob(f"{slug}-*"):
                task_dir = project_root / "runtime" / "tasks"
                if task_dir.resolve() == current_task_dir:
                    continue
                if (task_dir / "plan.json").is_file() and _director_resume_task_files(task_dir):
                    candidates.append(task_dir)
    return sorted(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)


def _director_resume_taskboard_score(task_dir: Path) -> tuple[int, int, float]:
    task_payloads = _director_resume_task_payloads(task_dir)
    plan = _load_json_object(task_dir / "plan.json")
    tasks = plan.get("tasks")
    planned_count = len(tasks) if isinstance(tasks, list) else 0
    blueprint_dir = task_dir.parent / "blueprints"
    blueprint_count = 0
    with contextlib.suppress(OSError):
        blueprint_count = len([path for path in blueprint_dir.glob("ce_*.json") if path.is_file()])
    mtime = max(
        (path.stat().st_mtime for path in [task_dir / "plan.json"] if path.exists()),
        default=0.0,
    )
    mtime = max(mtime, _director_resume_task_rows_mtime(task_dir))
    return (blueprint_count, min(planned_count, len(task_payloads)), mtime)


def _raise_director_resume_task_runtime_failure(result: dict[str, Any]) -> None:
    raise StructuredHTTPException(
        status_code=500,
        code="DIRECTOR_RESUME_TASK_RUNTIME_WRITE_FAILED",
        message="Director resume task rows must be prepared through task_runtime execution evidence",
        details={"task_runtime_result": result},
    )


def _rehydrate_director_resume_taskboard(workspace: str) -> str:
    target_dir = Path(resolve_runtime_path(workspace, "runtime/tasks"))
    if _pm_plan_task_count(workspace) > 0 and _taskboard_record_count(workspace) > 0:
        _reset_current_director_resume_taskboard(workspace, target_dir=target_dir)
        return ""
    candidates = sorted(
        _director_resume_source_task_dirs(workspace),
        key=_director_resume_taskboard_score,
        reverse=True,
    )
    for source_dir in candidates:
        plan_payload = _load_json_object(source_dir / "plan.json")
        task_payloads = _director_resume_task_payloads(source_dir)
        if not isinstance(plan_payload.get("tasks"), list) or not task_payloads:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_dir / "plan.json", target_dir / "plan.json")
        prepare_result = TaskRuntimeService(workspace).import_task_rows_for_reexecution(
            task_payloads,
            source="factory.director_resume.rehydration",
            source_task_dir=str(source_dir),
        )
        if not bool(prepare_result.get("success")):
            _raise_director_resume_task_runtime_failure(prepare_result)
        copied: list[str] = ["plan.json", *[str(path) for path in prepare_result.get("imported_files", [])]]
        evidence = {
            "schema_version": "factory.director_resume_taskboard_rehydration.v1",
            "source": "factory_http",
            "source_task_dir": str(source_dir),
            "target_task_dir": str(target_dir),
            "copied_files": copied,
            "task_runtime_prepare_result": prepare_result,
            "reset_statuses": "all_task_records",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_text_atomic(target_dir / "director_resume_rehydration.json", evidence, trailing_newline=False)
        return str(source_dir)
    return ""


def _reset_current_director_resume_taskboard(
    workspace: str,
    *,
    target_dir: Path | None = None,
) -> dict[str, Any]:
    """Reset existing Director task rows to a clean pre-Director claimable state."""
    task_dir = target_dir or Path(resolve_runtime_path(workspace, "runtime/tasks"))
    if not _director_resume_task_files(task_dir):
        return {}

    prepare_result = TaskRuntimeService(workspace).reset_task_rows_for_reexecution(
        source="factory.director_resume.reset"
    )
    if not bool(prepare_result.get("success")):
        _raise_director_resume_task_runtime_failure(prepare_result)

    evidence = {
        "schema_version": "factory.director_resume_taskboard_reset.v1",
        "source": "factory_http",
        "workspace": workspace,
        "target_task_dir": str(task_dir),
        "reset_files": prepare_result.get("reset_files", []),
        "skipped_files": prepare_result.get("skipped_files", []),
        "deleted_session_files": prepare_result.get("deleted_session_files", []),
        "task_runtime_prepare_result": prepare_result,
        "reset_statuses": "all_task_records",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    _write_json_text_atomic(task_dir / "director_resume_reset.json", evidence)
    return evidence


def _chief_engineer_blueprint_count(workspace: str) -> int:
    workspace_path = Path(workspace)
    candidates = [
        workspace_path / ".polaris" / "blueprints" / "latest.review.json",
        Path(resolve_logical_path(workspace, "workspace/.polaris/blueprints/latest.review.json")),
    ]
    state_dir = Path(resolve_runtime_path(workspace, "runtime/state/blueprints"))
    with contextlib.suppress(OSError):
        candidates.extend(path for path in state_dir.glob("*.review.json") if path.is_file())
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        payload = _load_json_object(resolved)
        raw_count = payload.get("generated_blueprints")
        try:
            count = int(str(raw_count or 0))
        except (TypeError, ValueError):
            count = 0
        blueprints = payload.get("blueprints")
        if count > 0 or (isinstance(blueprints, list) and bool(blueprints)):
            return max(count, len(blueprints) if isinstance(blueprints, list) else 0)
    return 0


def _pre_director_snapshot_ready(workspace: str) -> bool:
    manifest_path = Path(workspace) / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"
    if not manifest_path.is_file():
        return False
    payload = _load_json_object(manifest_path)
    return str(payload.get("snapshot_kind") or "") == "pre_director_workspace"


def _ensure_director_resume_evidence_ready(workspace: str) -> None:
    if _chief_engineer_blueprint_count(workspace) > 0:
        _rehydrate_director_resume_taskboard(workspace)
    missing: list[str] = []
    if _pm_plan_task_count(workspace) <= 0:
        missing.append("runtime/tasks/plan.json")
    if _taskboard_record_count(workspace) <= 0:
        missing.append("runtime/tasks/task_*.json")
    if _chief_engineer_blueprint_count(workspace) <= 0:
        missing.append(".polaris/blueprints/latest.review.json")
    if not _pre_director_snapshot_ready(workspace):
        missing.append(".polaris/factory_snapshots/pre_director/manifest.json")
    if missing:
        raise StructuredHTTPException(
            status_code=409,
            code="DIRECTOR_RESUME_EVIDENCE_MISSING",
            message="Director-only Factory run requires trusted PM, Chief Engineer, TaskBoard, and pre-Director snapshot evidence",
            details={
                "workspace": workspace,
                "missing_evidence": missing,
                "required_evidence": [
                    "runtime/tasks/plan.json",
                    "runtime/tasks/task_*.json",
                    ".polaris/blueprints/latest.review.json",
                    ".polaris/factory_snapshots/pre_director/manifest.json",
                ],
            },
        )


def _normalize_start_from(start_from: str, workspace: str) -> str:
    normalized = str(start_from or "auto").strip().lower()
    if normalized in {"resume_director", "director-only", "director_only"}:
        normalized = "director_resume"
    if normalized not in {"auto", "architect", "pm", "director_resume"}:
        normalized = "auto"
    if normalized != "auto":
        return normalized
    return "architect" if not _check_docs_ready(workspace) else "pm"


def _build_stage_list(start_from: str, run_director: bool) -> list[str]:
    del run_director
    normalized = str(start_from or "auto").strip().lower()
    if normalized == "architect":
        return [
            "docs_generation",
            "pm_planning",
            "chief_engineer_review",
            "director_dispatch",
            "quality_gate",
        ]
    if normalized == "pm":
        return [
            "pm_planning",
            "chief_engineer_review",
            "director_dispatch",
            "quality_gate",
        ]
    if normalized == "director_resume":
        return [
            "director_dispatch",
            "quality_gate",
        ]
    # auto is normalized before this point; fail closed to the canonical chain.
    return [
        "pm_planning",
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ]


def _required_ready_roles_for_stages(stages: list[str], *, qa_enabled: bool) -> list[str]:
    roles: list[str] = []
    for stage in stages:
        role = STAGE_TO_ROLE.get(str(stage or "").strip())
        if not role:
            continue
        # Factory CE review uses the local chief_engineer.blueprint service; it
        # must not be blocked by role-chat LLM readiness.
        if role == "chief_engineer":
            continue
        if role == "qa" and not qa_enabled:
            continue
        if role not in roles:
            roles.append(role)
    return roles


def _settings_qa_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "qa_enabled", True))


def _ensure_factory_runtime_ready(state: AppState, stages: list[str]) -> None:
    roles = _required_ready_roles_for_stages(stages, qa_enabled=_settings_qa_enabled(state.settings))
    if not roles:
        return
    live_check = os.environ.get("KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }
    ensure_required_roles_ready(
        state,
        default_roles=roles,
        force_roles=roles,
        live_check=live_check,
    )


def _build_stage_context(
    stage: str,
    payload: FactoryStartRequest,
    state: AppState,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    metadata = dict(payload.metadata or {})
    metadata["factory_start_from"] = str(payload.start_from or "").strip().lower()
    context: dict[str, Any] = {
        "settings": getattr(state, "settings", None),
        "factory_run_id": str(run_id or "").strip(),
        "factory_start_from": metadata["factory_start_from"],
        "metadata": metadata,
    }
    _append_factory_deadline_context(context, metadata)
    if stage in {"docs_generation", "pm_planning"}:
        context["directive"] = payload.directive
    if stage == "chief_engineer_review":
        context["directive"] = payload.directive
    if stage == "director_dispatch":
        requested_execution_mode = str(payload.director_workflow_execution_mode or "").strip().lower()
        context["execution_mode"] = (
            requested_execution_mode
            if requested_execution_mode in {"serial", "parallel"}
            else getattr(state.settings, "director_execution_mode", "parallel")
        )
        context["max_workers"] = getattr(
            state.settings, "director_max_parallel_tasks", DEFAULT_DIRECTOR_MAX_PARALLELISM
        )
        context["director_dispatch_driver"] = "task-market"
        context["dispatch_mode"] = "mainline-full"
        if int(payload.director_iterations) > 0:
            context["director_max_rounds"] = int(payload.director_iterations)
        director_dispatch_timeout = resolve_director_dispatch_timeout_seconds()
        context["timeout"] = director_dispatch_timeout
        context["director_dispatch_timeout_seconds"] = director_dispatch_timeout
        context["llm_call_timeout_seconds"] = director_dispatch_timeout
        context["director_llm_timeout_seconds"] = director_dispatch_timeout
    if stage == "quality_gate":
        context["qa_target"] = payload.directive or "Quality gate"
    return context


def _json_payload(data: Any) -> str:
    payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    return json.dumps(payload, ensure_ascii=False)


def _write_json_text_atomic(path: Path, payload: Any, *, trailing_newline: bool = True) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if trailing_newline:
        text += "\n"
    write_text_atomic(str(path), text)


def _resolve_runtime_path(workspace: str, relative_path: str) -> Path:
    rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if rel == "docs" or rel.startswith("docs/"):
        rel = f"workspace/{rel}"
    elif rel.startswith(("tasks/", "dispatch/")):
        rel = f"runtime/{rel}"
    resolved = resolve_logical_path(str(workspace), rel)
    return Path(resolved).resolve()


def _read_json_artifact(workspace: str, relative_path: str) -> dict[str, Any]:
    target = _resolve_runtime_path(workspace, relative_path)
    if not target.exists() or not target.is_file():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (RuntimeError, ValueError):
        logger.debug("Failed to read JSON artifact: workspace=%s path=%s", workspace, relative_path)
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


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


def _resolve_task_identifier(*sources: Any) -> str:
    """Return the first display-stable task identifier from known payload shapes.

    The alias helper is used only to validate that a candidate participates in
    the same identifier space as ScopeAuthority owner routing. The returned
    value intentionally remains the original display token.
    """

    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _TASK_IDENTIFIER_KEYS:
            value = str(source.get(key) or "").strip()
            if value and task_identifier_token_aliases(value):
                return value
    return ""


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
                    "reason": rework_reason,
                    "project_completion_action_id": completion_action_id,
                    "transition_owner": "runtime.task_runtime",
                }
            )
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


def _resolve_loop_max_cycles() -> int:
    raw = os.getenv("KERNELONE_FACTORY_LOOP_MAX_CYCLES", str(_DEFAULT_LOOP_MAX_CYCLES))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_LOOP_MAX_CYCLES
    return max(1, min(value, 200))


def _resolve_loop_stall_threshold() -> int:
    raw = os.getenv("KERNELONE_FACTORY_LOOP_STALL_THRESHOLD", str(_DEFAULT_LOOP_STALL_THRESHOLD))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_LOOP_STALL_THRESHOLD
    return max(1, min(value, 20))


def _resolve_quality_rework_max_cycles() -> int:
    raw = os.getenv("KERNELONE_FACTORY_QUALITY_REWORK_MAX_CYCLES", str(_DEFAULT_QUALITY_REWORK_MAX_CYCLES))
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = _DEFAULT_QUALITY_REWORK_MAX_CYCLES
    return max(1, min(value, 20))


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
        requested = bool(metadata.get("qa_rework_requested"))
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
            "reason": str(metadata.get("qa_rework_reason") or "").strip(),
            "retry_count": metadata.get("qa_rework_retry_count"),
            "max_retries": metadata.get("qa_rework_max_retries"),
            "exhausted": exhausted,
        }
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


def _build_summary_json(
    *,
    run: FactoryRun,
    payload: FactoryStartRequest,
    status: str,
    workspace: str,
) -> dict[str, Any]:
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    history = metadata.get("loop_history")
    loop_history = history if isinstance(history, list) else []
    docs_state = metadata.get("loop_last_docs_state")
    if not isinstance(docs_state, dict):
        docs_state = {}
    failure = metadata.get("failure")
    if not isinstance(failure, dict):
        failure = {}
    return {
        "run_id": run.id,
        "status": status,
        "workspace": workspace,
        "start_from": payload.start_from,
        "run_director": bool(payload.run_director),
        "loop_enabled": bool(payload.loop),
        "stages_configured": list(run.config.stages or []),
        "stages_completed": list(run.stages_completed or []),
        "stages_failed": list(run.stages_failed or []),
        "loop_cycles_executed": int(metadata.get("loop_cycles_executed") or 0),
        "loop_stop_reason": str(metadata.get("loop_stop_reason") or "").strip() or None,
        "docs_pipeline": docs_state,
        "loop_history": loop_history,
        "failure": failure or None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_summary_markdown(summary_json: dict[str, Any]) -> str:
    status = str(summary_json.get("status") or "FAIL").strip().upper()
    run_id = str(summary_json.get("run_id") or "").strip()
    loop_enabled = bool(summary_json.get("loop_enabled"))
    loop_cycles = int(summary_json.get("loop_cycles_executed") or 0)
    stop_reason = str(summary_json.get("loop_stop_reason") or "").strip() or "n/a"
    completed = summary_json.get("stages_completed")
    failed = summary_json.get("stages_failed")
    completed_text = ", ".join(completed) if isinstance(completed, list) and completed else "none"
    failed_text = ", ".join(failed) if isinstance(failed, list) and failed else "none"

    lines = [
        "# Factory Run Summary",
        "",
        f"- Run ID: `{run_id}`",
        f"- Status: `{status}`",
        f"- Workspace: `{summary_json.get('workspace')}`",
        f"- Start From: `{summary_json.get('start_from')}`",
        f"- Loop Enabled: `{loop_enabled}`",
        f"- Loop Cycles Executed: `{loop_cycles}`",
        f"- Loop Stop Reason: `{stop_reason}`",
        f"- Stages Completed: `{completed_text}`",
        f"- Stages Failed: `{failed_text}`",
    ]

    failure = summary_json.get("failure")
    if isinstance(failure, dict) and failure:
        lines.extend(
            [
                "",
                "## Failure",
                "",
                f"- Stage: `{failure.get('stage')}`",
                f"- Code: `{failure.get('code')}`",
                f"- Detail: {failure.get('detail')}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _model_dump_json_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        payload = value.dict()
    else:
        payload = value
    if isinstance(payload, dict):
        return payload
    return {}


def _artifact_response_path(artifact_path: Path, workspace: str) -> str:
    try:
        return str(artifact_path.relative_to(Path(workspace)))
    except ValueError:
        return str(artifact_path)


def _list_run_artifacts(
    *,
    service: FactoryRunService,
    workspace: str,
    run_id: str,
) -> list[dict[str, Any]]:
    run_dir = service.store.get_run_dir(run_id)
    artifacts_dir = run_dir / "artifacts"
    artifacts: list[dict[str, Any]] = []

    if not artifacts_dir.exists():
        return artifacts

    for artifact_path in sorted(artifacts_dir.iterdir(), key=lambda item: item.name):
        if not artifact_path.is_file():
            continue
        artifacts.append(_artifact_item_from_path(artifact_path, _artifact_response_path(artifact_path, workspace)))

    return artifacts


def _extract_task_id_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return _resolve_task_identifier(
        payload,
        payload.get("raw") if isinstance(payload.get("raw"), dict) else None,
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
    )


def _task_id_from_artifact_name(name: str) -> str:
    stem = Path(str(name or "").replace("\\", "/")).stem.strip()
    if not stem:
        return ""
    lowered = stem.lower()
    for prefix in ("ce_", "ce-", "blueprint_", "blueprint-", "chief_engineer_", "chief-engineer-"):
        if lowered.startswith(prefix):
            return stem[len(prefix) :].strip()
    return ""


def _task_id_from_artifact_file(artifact_path: Path) -> str:
    if artifact_path.suffix.lower() == ".json":
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        task_id = _extract_task_id_from_payload(payload)
        if task_id:
            return task_id
    return _task_id_from_artifact_name(artifact_path.name)


def _artifact_item_from_path(artifact_path: Path, response_path: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": artifact_path.name,
        "path": response_path,
        "size": artifact_path.stat().st_size,
    }
    task_id = _task_id_from_artifact_file(artifact_path)
    if task_id:
        item["task_id"] = task_id
    return item


def _artifact_item_from_stage_ref(workspace: str, relative_path: str) -> dict[str, Any] | None:
    rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    try:
        artifact_path = _resolve_runtime_path(workspace, rel)
    except (OSError, RuntimeError, ValueError):
        return None
    if not artifact_path.exists() or not artifact_path.is_file():
        return None
    return _artifact_item_from_path(artifact_path, rel)


def _list_stage_artifacts(
    *,
    workspace: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if str(event.get("type") or "").strip() != "stage_completed":
            continue
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        raw_artifacts = result.get("artifacts")
        if not isinstance(raw_artifacts, list):
            continue
        for raw_path in raw_artifacts:
            rel = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
            if not rel or rel in seen:
                continue
            item = _artifact_item_from_stage_ref(workspace, rel)
            if item is None:
                continue
            seen.add(rel)
            artifacts.append(item)
    return artifacts


def _merge_artifact_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            path = str(item.get("path") or "").strip()
            name = str(item.get("name") or "").strip()
            key = path or name
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _build_artifacts_response(
    *,
    run: FactoryRun,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    summary_json = run.metadata.get("summary_json")
    return {
        "run_id": run.id,
        "artifacts": artifacts,
        "summary_md": str(run.metadata.get("summary_md") or "").strip() or None,
        "summary_json": summary_json if isinstance(summary_json, dict) else None,
    }


def _safe_events_tail_limit(limit: int) -> int:
    return max(0, min(int(limit), 1000))


def _count_events_by_type(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type") or "unknown").strip() or "unknown"
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _extract_taskboard_snapshots(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extract initial and final taskboard snapshots from stage events."""
    initial: dict[str, Any] = {}
    final: dict[str, Any] = {}
    for event in events:
        taskboard = event.get("taskboard")
        if not isinstance(taskboard, dict):
            continue
        if not initial:
            initial = {
                "total": taskboard.get("total"),
                "claimed": taskboard.get("claimed"),
                "completed": taskboard.get("completed"),
                "failed": taskboard.get("failed"),
                "blocked": taskboard.get("blocked"),
            }
        final = {
            "total": taskboard.get("total"),
            "claimed": taskboard.get("claimed"),
            "completed": taskboard.get("completed"),
            "failed": taskboard.get("failed"),
            "blocked": taskboard.get("blocked"),
        }
    return {"initial": initial, "final": final}


def _extract_per_binding_task_status(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract per-task claim/terminal status from director events."""
    tasks: dict[str, dict[str, Any]] = {}
    for event in events:
        task_id = _resolve_task_identifier(event)
        if not task_id:
            payload = event.get("result") if isinstance(event.get("result"), dict) else None
            if isinstance(payload, dict):
                task_id = _resolve_task_identifier(payload)
        if not task_id:
            continue
        event_type = str(event.get("type") or "").strip()
        entry = tasks.setdefault(task_id, {"task_id": task_id, "status": "unknown", "events": []})
        entry["events"].append(event_type)
        if event_type in ("task_completed", "task_success"):
            entry["status"] = "completed"
        elif event_type in ("task_failed", "task_error"):
            entry["status"] = "failed"
        elif event_type in ("task_blocked",):
            entry["status"] = "blocked"
        elif event_type in ("task_claimed", "task_started") and entry["status"] == "unknown":
            entry["status"] = "claimed"
    return list(tasks.values())


def _extract_missing_delivery_targets(
    *,
    run: FactoryRun,
    status_payload: dict[str, Any],
) -> list[str]:
    """Return declared stages that were never reached or completed."""
    configured_stages = list(run.config.stages) if hasattr(run.config, "stages") else []
    completed = set(run.stages_completed or [])
    failed = set(run.stages_failed or [])
    reached = completed | failed
    return [s for s in configured_stages if s not in reached]


def _build_director_convergence(
    *,
    run: FactoryRun,
    events: list[dict[str, Any]],
    status_payload: dict[str, Any],
    summary_json: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build director convergence diagnostics when QA did not run.

    Returns None when QA ran successfully (convergence not relevant).
    """
    qa_gate = next(
        (
            g
            for g in (status_payload.get("gates") or [])
            if isinstance(g, dict) and g.get("gate_name") == "quality_gate"
        ),
        None,
    )
    qa_ran = bool(qa_gate and qa_gate.get("passed") is not None)
    status = str(status_payload.get("status") or "").lower()
    if qa_ran and status == "completed":
        return None

    blocking_phase = str(status_payload.get("current_stage") or status_payload.get("phase") or "").strip()
    taskboard = _extract_taskboard_snapshots(events)
    per_binding = _extract_per_binding_task_status(events)
    missing_targets = _extract_missing_delivery_targets(run=run, status_payload=status_payload)

    director_summary = (summary_json or {}).get("director") if isinstance(summary_json, dict) else None

    return {
        "qa_ran": qa_ran,
        "blocking_phase": blocking_phase,
        "taskboard_initial": taskboard["initial"],
        "taskboard_final": taskboard["final"],
        "missing_delivery_targets": missing_targets,
        "per_binding_task_status": per_binding,
        "director_summary": director_summary if isinstance(director_summary, dict) else None,
    }


def _build_factory_audit_bundle(
    *,
    run: FactoryRun,
    events: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    events_tail_limit: int = 100,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    status_payload = _model_dump_json_dict(_map_service_run_to_contract(run))
    summary_json = run.metadata.get("summary_json")
    tail_limit = _safe_events_tail_limit(events_tail_limit)
    events_tail = events[-tail_limit:] if tail_limit > 0 else []
    gates = status_payload.get("gates")
    failure = status_payload.get("failure")

    convergence = _build_director_convergence(
        run=run,
        events=events,
        status_payload=status_payload,
        summary_json=summary_json if isinstance(summary_json, dict) else None,
    )

    result: dict[str, Any] = {
        "run_id": status_payload.get("run_id") or run.id,
        "status": status_payload.get("status"),
        "phase": status_payload.get("phase"),
        "progress": status_payload.get("progress"),
        "current_stage": status_payload.get("current_stage"),
        "last_successful_stage": status_payload.get("last_successful_stage"),
        "gates": gates if isinstance(gates, list) else [],
        "failure": failure if isinstance(failure, dict) else None,
        "events_tail": events_tail,
        "artifacts": artifacts,
        "summary_md": str(run.metadata.get("summary_md") or "").strip() or None,
        "summary_json": summary_json if isinstance(summary_json, dict) else None,
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "evidence_counts": {
            "events_total": len(events),
            "events_tail": len(events_tail),
            "artifacts": len(artifacts),
            "gates": len(gates) if isinstance(gates, list) else 0,
            "failures": 1 if isinstance(failure, dict) else 0,
            "summary_md": 1 if str(run.metadata.get("summary_md") or "").strip() else 0,
            "summary_json": 1 if isinstance(summary_json, dict) else 0,
            "event_types": _count_events_by_type(events),
        },
    }
    if convergence is not None:
        result["director_convergence"] = convergence
    return result


def _factory_run_identity(*, run: FactoryRun, workspace: str) -> dict[str, Any]:
    start_request = run.metadata.get("factory_start_request")
    start_request_map = start_request if isinstance(start_request, dict) else {}
    start_metadata = start_request_map.get("metadata")
    start_metadata_map = start_metadata if isinstance(start_metadata, dict) else {}
    return {
        "schema_version": "factory.run_identity.v1",
        "run_id": run.id,
        "factory_run_id": run.id,
        "workspace": str(workspace),
        "requested_project_id": str(
            start_metadata_map.get("requested_project_id")
            or start_metadata_map.get("factory_bench_requested_project_id")
            or start_metadata_map.get("factory_bench_project_id")
            or ""
        ),
        "canonical_project_id": str(
            start_metadata_map.get("canonical_project_id")
            or start_metadata_map.get("factory_bench_canonical_project_id")
            or start_metadata_map.get("factory_bench_project_id")
            or ""
        ),
        "instance_id": str(
            start_metadata_map.get("instance_id") or start_metadata_map.get("launcher_instance_id") or ""
        ),
        "backend_port": start_metadata_map.get("backend_port"),
        "frontend_port": start_metadata_map.get("frontend_port"),
    }


def _attach_control_plane_projection(
    *,
    bundle: dict[str, Any],
    run: FactoryRun,
    workspace: str,
) -> None:
    identity = _factory_run_identity(run=run, workspace=workspace)
    bundle["factory_run_id"] = run.id
    bundle["workspace"] = str(workspace)
    bundle["run_identity"] = identity
    projection_errors: list[dict[str, str]] = []
    try:
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(workspace=str(workspace), run_id=run.id)
        ).projection
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        projection_errors.append(
            {
                "code": "RUN_LEDGER_PROJECTION_UNAVAILABLE",
                "message": str(exc)[:300],
                "exception_type": type(exc).__name__,
            }
        )
    else:
        bundle["control_plane_projection"] = projection
        bundle["run_ledger_projection"] = projection

    terminal_snapshot_payload = run.metadata.get(FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY)
    terminal_snapshot: FactoryTerminalTaskRuntimeProjectionV1 | None = None
    if isinstance(terminal_snapshot_payload, Mapping):
        try:
            terminal_snapshot = FactoryTerminalTaskRuntimeProjectionV1.from_dict(terminal_snapshot_payload)
            if terminal_snapshot.factory_run_id != run.id:
                raise ValueError("terminal TaskRuntime snapshot factory_run_id mismatch")
            if Path(terminal_snapshot.workspace).expanduser().resolve() != Path(workspace).expanduser().resolve():
                raise ValueError("terminal TaskRuntime snapshot workspace mismatch")
        except (OSError, TypeError, ValueError) as exc:
            projection_errors.append(
                {
                    "code": "TASK_RUNTIME_TERMINAL_PROJECTION_INVALID",
                    "message": str(exc)[:300],
                    "exception_type": type(exc).__name__,
                }
            )
        else:
            bundle["task_runtime_projection"] = dict(terminal_snapshot.projection)

    if terminal_snapshot is None:
        try:
            task_runtime_projection = TaskRuntimeService(str(workspace)).query_observable_task_rows_projection()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            projection_errors.append(
                {
                    "code": "TASK_RUNTIME_PROJECTION_UNAVAILABLE",
                    "message": str(exc)[:300],
                    "exception_type": type(exc).__name__,
                }
            )
        else:
            bundle["task_runtime_projection"] = task_runtime_projection.to_authority_dict(factory_run_id=run.id)

    if projection_errors:
        bundle["control_plane_projection_error"] = {
            "schema_version": "factory.control_plane_projection_error.v1",
            "code": "CONTROL_PLANE_PROJECTION_INCOMPLETE",
            "errors": projection_errors,
        }


async def _guard_automatic_router_mutation(
    *,
    service: FactoryRunService,
    run_id: str,
    current_run: FactoryRun,
    operation: str,
) -> FactoryRun:
    return await service.assert_automatic_router_mutation_allowed(
        run_id,
        operation=operation,
        current_run=current_run,
    )


async def _persist_run_summary(
    *,
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    workspace: str,
    status: str,
) -> None:
    if await service.get_run(run_id) is None:
        return

    def apply_summary(run: FactoryRun) -> None:
        summary_json = _build_summary_json(run=run, payload=payload, status=status, workspace=workspace)
        run.metadata["summary_json"] = summary_json
        run.metadata["summary_md"] = _build_summary_markdown(summary_json)

    await service.apply_automatic_router_mutation(
        run_id,
        operation="summary_projection",
        mutation=apply_summary,
    )


def _classify_factory_failure_code(*, stage: str, detail: str) -> str:
    normalized_detail = str(detail or "").lower()
    if "qa_llm_judgement_unavailable" in normalized_detail:
        return "QA_LLM_JUDGEMENT_UNAVAILABLE"
    # Tight provider/auth block signals only. Bare "403" / "forbidden" / "quota"
    # substrings mislabel platform failures (path:line 403, "forbidden path",
    # disk quota). Require provider-shaped HTTP or billing/usage phrasing.
    provider_block_signals = (
        "you've reached your usage limit",
        "usage limit for this billing cycle",
        "usage limit",
        "billing cycle",
        "insufficient_quota",
        "permission_error",
        "message='forbidden'",
        'message="forbidden"',
        "clientresponseerror: 403",
        "clientresponseerror: 429",
        'status_code": 403',
        'status_code": 429',
        "status=403",
        "status=429",
        "http 403",
        "http 429",
        " 403, message=",
        " 429, message=",
        "rate limit exceeded",
        "rate_limit_exceeded",
        "error code: 429",
        "error_code=429",
    )
    if any(signal in normalized_detail for signal in provider_block_signals):
        return "PROVIDER_QUOTA_OR_AUTH_BLOCKED"
    if str(stage or "").strip():
        return "FACTORY_STAGE_FAILED"
    return "FACTORY_RUN_EXCEPTION"


def _factory_failure_suggestion(code: str) -> str:
    if code == "QA_LLM_JUDGEMENT_UNAVAILABLE":
        return "Fix QA LLM connectivity or explicitly disable qa_require_llm_judgement for non-audited dry runs."
    if code == "PROVIDER_QUOTA_OR_AUTH_BLOCKED":
        return (
            "Provider rejected the call (quota exhausted, auth forbidden, or rate limited). "
            "Switch provider/model or refill quota, then restart the Factory run."
        )
    return ""


__all__ = [
    "_apply_quality_gate_task_boundary_rework_requests",
    "_artifact_item_from_path",
    "_artifact_item_from_stage_ref",
    "_artifact_response_path",
    "_attach_control_plane_projection",
    "_build_artifacts_response",
    "_build_director_convergence",
    "_build_factory_audit_bundle",
    "_build_stage_context",
    "_build_stage_list",
    "_build_summary_json",
    "_build_summary_markdown",
    "_check_docs_ready",
    "_chief_engineer_blueprint_count",
    "_classify_factory_failure_code",
    "_count_events_by_type",
    "_decide_delivery_loop_action",
    "_director_resume_source_task_dirs",
    "_director_resume_task_files",
    "_director_resume_task_payloads",
    "_director_resume_task_rows_mtime",
    "_director_resume_taskboard_score",
    "_director_resume_workspace_slug",
    "_ensure_director_resume_evidence_ready",
    "_ensure_factory_runtime_ready",
    "_extract_missing_delivery_targets",
    "_extract_per_binding_task_status",
    "_extract_task_id_from_payload",
    "_extract_taskboard_snapshots",
    "_factory_failure_suggestion",
    "_factory_run_identity",
    "_guard_automatic_router_mutation",
    "_json_payload",
    "_list_run_artifacts",
    "_list_stage_artifacts",
    "_load_json_object",
    "_merge_artifact_items",
    "_model_dump_json_dict",
    "_normalize_start_from",
    "_ownership_handoff_requests_from_repair_payload",
    "_persist_run_summary",
    "_pm_plan_task_count",
    "_pm_plan_task_ids",
    "_pre_director_snapshot_ready",
    "_quality_gate_handoff_summary_from_payload",
    "_quality_gate_owner_handoff_index",
    "_quality_gate_owner_handoff_routing",
    "_raise_director_resume_task_runtime_failure",
    "_read_docs_pipeline_state",
    "_read_json_artifact",
    "_read_pm_plan_signature",
    "_read_quality_gate_rework_summary",
    "_read_task_boundary_workspace_validation",
    "_record_factory_task_runtime_transition_failure",
    "_rehydrate_director_resume_taskboard",
    "_required_ready_roles_for_stages",
    "_reset_current_director_resume_taskboard",
    "_resolve_loop_max_cycles",
    "_resolve_loop_stall_threshold",
    "_resolve_quality_rework_max_cycles",
    "_resolve_runtime_path",
    "_resolve_task_identifier",
    "_safe_events_tail_limit",
    "_safe_rework_int",
    "_settings_qa_enabled",
    "_task_boundary_rework_evidence",
    "_task_id_from_artifact_file",
    "_task_id_from_artifact_name",
    "_task_record_needs_task_boundary_rework",
    "_taskboard_record_count",
    "_workspace_validation_requests_task_boundary_rework",
    "_write_json_text_atomic",
]
