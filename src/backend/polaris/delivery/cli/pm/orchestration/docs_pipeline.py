"""Document generation pipeline for orchestration."""

import contextlib
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Any

from polaris.delivery.cli.pm.utils import read_json_file
from polaris.infrastructure.compat.io_utils import (
    read_file_safe,
    resolve_artifact_path,
    write_json_atomic,
    write_text_atomic,
)

from .blueprint_pipeline import (
    _all_tasks_terminal,
    _build_architect_docs_pipeline_payload,
    _build_tasks_status_signature,
    _compose_stage_plan,
    _compose_stage_requirements,
    _materialize_zhongshu_blueprints,
    _normalize_doc_rel_path,
    _resolve_existing_doc_path,
)
from .helpers import (
    _ARCHITECT_DOCS_PIPELINE_REL,
    _PM_DOCS_PROGRESS_REL,
    _resolve_pm_doc_stage_mode,
    _safe_int,
)

logger = logging.getLogger(__name__)


def _resolve_docs_autoinit_root(workspace_full: str) -> str:
    """Resolve docs auto initialization root path."""
    try:
        try:
            from polaris.kernelone.storage import resolve_workspace_persistent_path
        except (RuntimeError, ValueError):  # pragma: no cover - script-mode fallback
            from polaris.kernelone.storage import resolve_workspace_persistent_path  # type: ignore
        return os.path.abspath(resolve_workspace_persistent_path(workspace_full, "workspace/docs"))
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to resolve docs autoinit root from workspace %r, falling back to <workspace>/docs: %s",
            workspace_full,
            exc,
        )
        return os.path.abspath(os.path.join(workspace_full, "docs"))


def _auto_initialize_docs(workspace_full: str) -> str:
    """Auto initialize docs directory with templates."""
    docs_root = _resolve_docs_autoinit_root(workspace_full)
    templates = {
        "agent/tui_runtime.md": (
            "# Agent Documentation Index\n\n"
            "- Root requirements: `docs/product/requirements.md`\n"
            "- Runtime artifacts are managed by Polaris runtime storage.\n"
        ),
        "product/requirements.md": (
            "# Product Requirements\n\n"
            "## Goal\n"
            "- Define the project goal and expected outcomes.\n\n"
            "## Acceptance Criteria\n"
            "- Fill concrete acceptance criteria before PM planning.\n"
        ),
    }
    for rel_path, content in templates.items():
        full_path = os.path.join(docs_root, rel_path.replace("/", os.sep))
        if os.path.isfile(full_path):
            continue
        write_text_atomic(full_path, content)
    return docs_root


def _sync_plan_to_runtime(workspace_full: str, cache_root_full: str) -> None:
    """Sync plan document to runtime directory."""
    plan_src_candidates = [
        resolve_artifact_path(workspace_full, cache_root_full, "workspace/docs/product/plan.md"),
        os.path.join(workspace_full, "docs", "product", "plan.md"),
    ]
    plan_src = ""
    for candidate in plan_src_candidates:
        if candidate and os.path.isfile(candidate):
            plan_src = candidate
            break
    if not plan_src:
        return
    plan_dst = resolve_artifact_path(workspace_full, cache_root_full, "runtime/contracts/plan.md")
    os.makedirs(os.path.dirname(plan_dst), exist_ok=True)
    tmp_path = plan_dst + ".tmp"
    try:
        shutil.copy2(plan_src, tmp_path)
        os.replace(tmp_path, plan_dst)
    finally:
        if os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.remove(tmp_path)


def _write_architect_docs_pipeline(
    workspace_full: str,
    cache_root_full: str,
    created_docs: list[str],
) -> dict[str, Any]:
    """Write architect docs pipeline to runtime."""
    payload = _build_architect_docs_pipeline_payload(created_docs)
    stages = payload.get("stages") if isinstance(payload.get("stages"), list) else []

    pipeline_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        _ARCHITECT_DOCS_PIPELINE_REL,
    )
    write_json_atomic(pipeline_full, payload)

    progress_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        _PM_DOCS_PROGRESS_REL,
    )
    active_stage_id = ""
    if stages and isinstance(stages[0], dict):
        active_stage_id = str(stages[0].get("id") or "").strip()
    write_json_atomic(
        progress_full,
        {
            "schema_version": 1,
            "active_stage_index": 0,
            "active_stage_id": active_stage_id,
            "last_planned_stage_id": "",
            "last_planned_iteration": 0,
            "last_tasks_signature_before_plan": "",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    blueprint_bundle = _materialize_zhongshu_blueprints(
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        stages=stages,  # type: ignore[arg-type]
    )

    return {
        "pipeline_path": pipeline_full,
        "progress_path": progress_full,
        "stage_count": len(stages) if isinstance(stages, list) else 0,  # type: ignore[arg-type]
        "blueprint_manifest_path": str(blueprint_bundle.get("manifest_path") or "").strip(),
        "blueprint_doc_count": int(blueprint_bundle.get("count") or 0),
    }


def _resolve_docs_stage_context(
    *,
    workspace_full: str,
    cache_root_full: str,
    iteration: int,
    last_tasks: Any,
    requirements: str,
    plan_text: str,
) -> tuple[str, str, dict[str, Any]]:
    """Resolve docs stage context for current iteration."""
    mode = _resolve_pm_doc_stage_mode()
    if mode == "off":
        return requirements, plan_text, {"enabled": False, "mode": mode}

    pipeline_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        _ARCHITECT_DOCS_PIPELINE_REL,
    )
    pipeline_payload = read_json_file(pipeline_full)
    if not isinstance(pipeline_payload, dict):
        return requirements, plan_text, {"enabled": False, "mode": mode}

    raw_stages = pipeline_payload.get("stages")
    if not isinstance(raw_stages, list):
        return requirements, plan_text, {"enabled": False, "mode": mode}

    stages: list[dict[str, Any]] = []
    for item in raw_stages:
        if not isinstance(item, dict):
            continue
        stage_id = str(item.get("id") or "").strip()
        doc_path = _normalize_doc_rel_path(str(item.get("doc_path") or "").strip())
        if not stage_id or not doc_path:
            continue
        stage = dict(item)
        stage["doc_path"] = doc_path
        stage["doc_full_path"] = _resolve_existing_doc_path(
            workspace_full,
            cache_root_full,
            doc_path,
        )
        stages.append(stage)
    if not stages:
        return requirements, plan_text, {"enabled": False, "mode": mode}

    progress_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        _PM_DOCS_PROGRESS_REL,
    )
    progress_payload = read_json_file(progress_full)
    progress = progress_payload if isinstance(progress_payload, dict) else {}

    active_index = _safe_int(progress.get("active_stage_index"), default=0)
    active_index = max(active_index, 0)
    if active_index >= len(stages):
        active_index = len(stages) - 1

    active_stage = stages[active_index]
    tasks_signature = _build_tasks_status_signature(last_tasks)
    last_planned_stage_id = str(progress.get("last_planned_stage_id") or "").strip()
    last_planned_iteration = _safe_int(progress.get("last_planned_iteration"), default=0)
    signature_before_plan = str(progress.get("last_tasks_signature_before_plan") or "").strip()

    advanced = False
    advance_reason = ""
    if (
        last_planned_stage_id
        and last_planned_stage_id == str(active_stage.get("id") or "").strip()
        and last_planned_iteration > 0
        and last_planned_iteration < int(iteration or 0)
    ):
        contract_changed = tasks_signature != signature_before_plan
        if contract_changed and _all_tasks_terminal(last_tasks):
            if active_index < len(stages) - 1:
                active_index += 1
                active_stage = stages[active_index]
                advanced = True
                advance_reason = "previous_stage_tasks_terminal"
            else:
                advance_reason = "pipeline_complete"
        elif not contract_changed:
            advance_reason = "waiting_for_new_contract"
        else:
            advance_reason = "waiting_for_terminal_status"

    active_doc_full = str(active_stage.get("doc_full_path") or "").strip()
    doc_text = read_file_safe(active_doc_full) or ""
    if not doc_text and active_index == 0:
        doc_text = requirements
    stage_requirements = _compose_stage_requirements(
        stage=active_stage,
        doc_text=doc_text,
        active_index=active_index,
        total_stages=len(stages),
    )
    stage_plan = _compose_stage_plan(stage=active_stage, doc_text=doc_text)

    write_json_atomic(
        progress_full,
        {
            "schema_version": 1,
            "active_stage_index": active_index,
            "active_stage_id": str(active_stage.get("id") or "").strip(),
            "last_planned_stage_id": str(active_stage.get("id") or "").strip(),
            "last_planned_iteration": int(iteration or 0),
            "last_tasks_signature_before_plan": tasks_signature,
            "advanced": advanced,
            "advance_reason": advance_reason,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    stage_info = {
        "enabled": True,
        "mode": mode,
        "pipeline_path": pipeline_full,
        "progress_path": progress_full,
        "active_stage_index": active_index,
        "total_stages": len(stages),
        "active_stage_id": str(active_stage.get("id") or "").strip(),
        "active_stage_title": str(active_stage.get("title") or "").strip(),
        "active_doc_path": str(active_stage.get("doc_path") or "").strip(),
        "active_doc_full_path": active_doc_full,
        "advanced": advanced,
        "advance_reason": advance_reason,
    }
    return stage_requirements, stage_plan, stage_info


__all__ = [
    "_auto_initialize_docs",
    "_resolve_docs_stage_context",
    "_sync_plan_to_runtime",
    "_write_architect_docs_pipeline",
]
