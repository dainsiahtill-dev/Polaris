# ruff: noqa: E402, F403, F405
"""Factory stage-ops helpers — director-resume evidence hydration.

Extracted verbatim from the former single-file ``stage_ops`` module during the
lossless decomposition of that god-module. These helpers hydrate trusted prior
PM / Chief Engineer / pre-Director snapshot evidence so a Director-only Factory
run can resume with the canonical TaskRuntime identity.

``factory.py``'s ``_rebind_helper_module`` rebinds these callables into the host
router namespace; the package ``__init__`` rewrites ``__module__`` so the rebind
treats them as package-owned. Cross-module free names are injected by
``_wire_cross_module_namespace``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.http.routers._shared import StructuredHTTPException
from polaris.kernelone.storage import resolve_runtime_path, resolve_storage_roots

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from ..mapping import *
from ._common import (
    _load_json_object,
    _pm_plan_task_count,
    _pm_plan_task_ids,
    _write_json_text_atomic,
)


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
    """Reopen only unfinished PM tasks while preserving verified work."""
    task_dir = target_dir or Path(resolve_runtime_path(workspace, "runtime/tasks"))
    if not _director_resume_task_files(task_dir):
        return {}

    prepare_result = TaskRuntimeService(workspace).reset_task_rows_for_reexecution(
        source="factory.director_resume.reset",
        preserve_completed=True,
        eligible_external_task_ids=_pm_plan_task_ids(workspace),
    )
    if not bool(prepare_result.get("success")):
        _raise_director_resume_task_runtime_failure(prepare_result)

    evidence = {
        "schema_version": "factory.director_resume_taskboard_reset.v1",
        "source": "factory_http",
        "workspace": workspace,
        "target_task_dir": str(task_dir),
        "reset_files": prepare_result.get("reset_files", []),
        "preserved_files": prepare_result.get("preserved_files", []),
        "excluded_files": prepare_result.get("excluded_files", []),
        "skipped_files": prepare_result.get("skipped_files", []),
        "deleted_session_files": prepare_result.get("deleted_session_files", []),
        "task_runtime_prepare_result": prepare_result,
        "reset_statuses": "unfinished_pm_task_records_only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    _write_json_text_atomic(task_dir / "director_resume_reset.json", evidence)
    return evidence


def _chief_engineer_review_evidence(workspace: str) -> tuple[Path | None, dict[str, Any]]:
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
            return resolved, payload
    return None, {}


def _chief_engineer_blueprint_count(workspace: str) -> int:
    _source, payload = _chief_engineer_review_evidence(workspace)
    raw_count = payload.get("generated_blueprints")
    try:
        count = int(str(raw_count or 0))
    except (TypeError, ValueError):
        count = 0
    blueprints = payload.get("blueprints")
    return max(count, len(blueprints) if isinstance(blueprints, list) else 0)


def _bind_director_resume_chief_engineer_review(workspace: str, *, run_id: str) -> Path:
    """Bind trusted prior CE evidence to a new Director-only Factory run.

    A resumed run receives a new ``factory_run_id``. Director's canonical
    handoff guard intentionally reads only the review bound to that run, so the
    HTTP admission layer must derive a run-local binding from the CE evidence
    it already admitted. The original run identity remains explicit provenance;
    PM/CE are not rerun and normal Factory runs never use this path.
    """

    resolved_run_id = str(run_id or "").strip()
    source_path, source_payload = _chief_engineer_review_evidence(workspace)
    if not resolved_run_id or source_path is None or not source_payload:
        raise StructuredHTTPException(
            status_code=409,
            code="DIRECTOR_RESUME_EVIDENCE_MISSING",
            message="Director-only Factory run could not bind trusted Chief Engineer review evidence",
            details={"workspace": workspace, "run_id": resolved_run_id},
        )

    source_factory_run_id = str(source_payload.get("factory_run_id") or "").strip()
    bound_payload = dict(source_payload)
    bound_payload["factory_run_id"] = resolved_run_id
    bound_payload["director_resume_binding"] = {
        "source_factory_run_id": source_factory_run_id,
        "source_path": str(source_path),
        "bound_factory_run_id": resolved_run_id,
        "bound_at": datetime.now(timezone.utc).isoformat(),
    }
    target = Path(resolve_runtime_path(workspace, f"runtime/state/blueprints/{resolved_run_id}.review.json"))
    _write_json_text_atomic(target, bound_payload)
    return target


def _pre_director_snapshot_ready(workspace: str) -> bool:
    manifest_path = Path(workspace) / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"
    if not manifest_path.is_file():
        return False
    payload = _load_json_object(manifest_path)
    return str(payload.get("snapshot_kind") or "") == "pre_director_workspace"


def _ensure_director_resume_evidence_ready(workspace: str) -> None:
    has_current_plan = _pm_plan_task_count(workspace) > 0
    has_current_taskboard = _taskboard_record_count(workspace) > 0
    if _chief_engineer_blueprint_count(workspace) > 0 and (has_current_taskboard or not has_current_plan):
        _rehydrate_director_resume_taskboard(workspace)
    missing: list[str] = []
    if _pm_plan_task_count(workspace) <= 0:
        missing.append("runtime/tasks/plan.json")
    if _chief_engineer_blueprint_count(workspace) <= 0:
        missing.append(".polaris/blueprints/latest.review.json")
    if not _pre_director_snapshot_ready(workspace):
        missing.append(".polaris/factory_snapshots/pre_director/manifest.json")
    if missing:
        raise StructuredHTTPException(
            status_code=409,
            code="DIRECTOR_RESUME_EVIDENCE_MISSING",
            message="Director-only Factory run requires trusted PM, Chief Engineer, and pre-Director snapshot evidence",
            details={
                "workspace": workspace,
                "missing_evidence": missing,
                "required_evidence": [
                    "runtime/tasks/plan.json",
                    ".polaris/blueprints/latest.review.json",
                    ".polaris/factory_snapshots/pre_director/manifest.json",
                ],
            },
        )


__all__ = [
    "_bind_director_resume_chief_engineer_review",
    "_chief_engineer_blueprint_count",
    "_chief_engineer_review_evidence",
    "_director_resume_source_task_dirs",
    "_director_resume_task_files",
    "_director_resume_task_payloads",
    "_director_resume_task_rows_mtime",
    "_director_resume_taskboard_score",
    "_director_resume_workspace_slug",
    "_ensure_director_resume_evidence_ready",
    "_pre_director_snapshot_ready",
    "_raise_director_resume_task_runtime_failure",
    "_rehydrate_director_resume_taskboard",
    "_reset_current_director_resume_taskboard",
    "_taskboard_record_count",
]
