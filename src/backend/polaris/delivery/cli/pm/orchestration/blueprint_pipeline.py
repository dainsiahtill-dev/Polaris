"""Blueprint pipeline utilities for orchestration."""

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from polaris.delivery.cli.pm.config import TERMINAL_TASK_STATUSES
from polaris.delivery.cli.pm.tasks import normalize_task_status
from polaris.kernelone.fs.text_ops import read_file_safe, write_json_atomic, write_text_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path

from .helpers import (
    _ZHONGSHU_BLUEPRINTS_MANIFEST_REL,
    _ZHONGSHU_BLUEPRINTS_ROOT_REL,
)

logger = logging.getLogger(__name__)


def _normalize_doc_rel_path(path: str) -> str:
    """Normalize document relative path."""
    token = str(path or "").strip().replace("\\", "/")
    if token.startswith("./"):
        token = token[2:]
    if token.startswith("docs/"):
        token = "workspace/" + token
    return token


def _doc_stage_sort_key(path: str) -> tuple[int, str]:
    """Generate sort key for document stage ordering."""
    token = _normalize_doc_rel_path(path).lower()
    priorities = {
        "workspace/docs/product/requirements.md": 10,
        "workspace/docs/product/interface_contract.md": 20,
        "workspace/docs/product/adr.md": 30,
        "workspace/docs/product/plan.md": 40,
    }
    return priorities.get(token, 200), token


def _doc_stage_title(path: str) -> str:
    """Generate title for document stage."""
    token = _normalize_doc_rel_path(path).lower()
    if token.endswith("/requirements.md"):
        return "Requirements"
    if token.endswith("/interface_contract.md"):
        return "Interface Contract"
    if token.endswith("/adr.md"):
        return "Architecture Decision Records"
    if token.endswith("/plan.md"):
        return "Implementation Plan"
    base = os.path.splitext(os.path.basename(token))[0]
    return base.replace("_", " ").replace("-", " ").strip().title() or "Document"


def _slugify_stage_filename(value: str) -> str:
    """Slugify stage filename."""
    token = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(value or "").strip())
    token = re.sub(r"_+", "_", token).strip("_").lower()
    return token or "stage"


def _resolve_doc_path_candidates(
    workspace_full: str,
    cache_root_full: str,
    doc_path: str,
) -> list[str]:
    """Resolve candidate paths for document."""
    token = str(doc_path or "").strip()
    if not token:
        return []
    if os.path.isabs(token):
        return [token]
    candidates: list[str] = []
    normalized = _normalize_doc_rel_path(token)
    try:
        candidates.append(resolve_artifact_path(workspace_full, cache_root_full, normalized))
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to resolve artifact path: {e}")
    candidates.append(os.path.join(workspace_full, normalized))
    if normalized.startswith("workspace/docs/"):
        docs_rel = normalized[len("workspace/") :]
        try:
            candidates.append(resolve_artifact_path(workspace_full, cache_root_full, docs_rel))
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to resolve docs artifact path: {e}")
    return [item for item in candidates if str(item or "").strip()]


def _resolve_existing_doc_path(
    workspace_full: str,
    cache_root_full: str,
    doc_path: str,
) -> str:
    """Resolve existing document path."""
    candidates = _resolve_doc_path_candidates(workspace_full, cache_root_full, doc_path)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return candidates[0] if candidates else ""


def _materialize_zhongshu_blueprints(
    *,
    workspace_full: str,
    cache_root_full: str,
    stages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Materialize Zhongshu blueprints from stages."""
    if not stages:
        return {
            "manifest_path": "",
            "docs": [],
            "count": 0,
        }

    docs: list[dict[str, Any]] = []
    previous_doc_id = ""
    for index, stage in enumerate(stages, start=1):
        if not isinstance(stage, dict):
            continue
        doc_id = str(stage.get("id") or f"DOC-STAGE-{index:02d}").strip()
        title = str(stage.get("title") or f"Stage {index}").strip()
        doc_path = str(stage.get("doc_path") or "").strip()
        doc_full = str(stage.get("doc_full_path") or "").strip()
        if not doc_full:
            doc_full = _resolve_existing_doc_path(
                workspace_full,
                cache_root_full,
                doc_path,
            )
        doc_body = read_file_safe(doc_full) or ""
        filename = f"{index:02d}_{_slugify_stage_filename(title)}.md"
        rel_path = f"{_ZHONGSHU_BLUEPRINTS_ROOT_REL}/{filename}"
        full_path = resolve_artifact_path(
            workspace_full,
            cache_root_full,
            rel_path,
        )
        header = f"# {title}\n\n- doc_id: `{doc_id}`\n- phase: {index}\n- active_document: `{doc_path}`\n\n"
        content = header + doc_body.strip() + "\n" if doc_body.strip() else header + "(empty stage document)\n"
        write_text_atomic(full_path, content)

        manifest_entry: dict[str, Any] = {
            "doc_id": doc_id,
            "title": title,
            "scope": {
                "active_document": doc_path,
                "scope_paths": [doc_path] if doc_path else [],
            },
            "deps": [previous_doc_id] if previous_doc_id else [],
            "phase": index,
            "acceptance_criteria": [
                "PM dispatch stays within active_document scope only.",
                "Cross-stage context is excluded from current iteration payload.",
            ],
            "risk_notes": [
                "Cross-stage contamination can dilute token focus.",
                "Synthetic bootstrap outside active_document is disallowed.",
            ],
            "doc_path": rel_path,
            "source_doc_path": doc_path,
        }
        docs.append(manifest_entry)
        previous_doc_id = doc_id

    manifest_path = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        _ZHONGSHU_BLUEPRINTS_MANIFEST_REL,
    )
    write_json_atomic(
        manifest_path,
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "docs": docs,
        },
    )
    return {
        "manifest_path": manifest_path,
        "docs": docs,
        "count": len(docs),
    }


def _build_architect_docs_pipeline_payload(
    created_docs: list[str],
) -> dict[str, Any]:
    """Build architect docs pipeline payload."""
    deduped: list[str] = []
    for raw in created_docs:
        rel = _normalize_doc_rel_path(raw)
        if not rel or not rel.lower().endswith(".md"):
            continue
        if rel not in deduped:
            deduped.append(rel)
    ordered_docs = sorted(deduped, key=_doc_stage_sort_key)
    stages: list[dict[str, Any]] = []
    for idx, rel in enumerate(ordered_docs, start=1):
        stages.append(
            {
                "id": f"DOC-STAGE-{idx:02d}",
                "title": _doc_stage_title(rel),
                "doc_path": rel,
                "status": "pending",
            }
        )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "single_doc_per_iteration": True,
        "advance_rule": "all_tasks_terminal_and_contract_changed",
        "stages": stages,
    }


def _extract_tasks_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Extract tasks from payload."""
    if not isinstance(payload, dict):
        return []
    tasks_raw = payload.get("tasks")
    if not isinstance(tasks_raw, list):
        return []
    return [task for task in tasks_raw if isinstance(task, dict)]


def _build_tasks_status_signature(last_tasks: Any) -> str:
    """Build tasks status signature for tracking."""
    tasks = _extract_tasks_from_payload(last_tasks)
    if not tasks:
        return ""
    parts: list[str] = []
    for task in tasks:
        task_id = str(task.get("id") or task.get("title") or "").strip()
        status = normalize_task_status(task.get("status"))
        parts.append(f"{task_id}:{status}")
    parts = sorted([item for item in parts if item])
    if not parts:
        return ""
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _all_tasks_terminal(last_tasks: Any) -> bool:
    """Check if all tasks are in terminal status."""
    tasks = _extract_tasks_from_payload(last_tasks)
    if not tasks:
        return True
    for task in tasks:
        status = normalize_task_status(task.get("status"))
        if status not in TERMINAL_TASK_STATUSES:
            return False
    return True


def _compose_stage_requirements(
    *,
    stage: dict[str, Any],
    doc_text: str,
    active_index: int,
    total_stages: int,
) -> str:
    """Compose stage requirements text."""
    stage_id = str(stage.get("id") or "").strip()
    stage_title = str(stage.get("title") or "").strip()
    doc_path = str(stage.get("doc_path") or "").strip()
    header = (
        "[PM_DOC_STAGE]\n"
        f"active_stage_id: {stage_id}\n"
        f"active_stage_title: {stage_title}\n"
        f"active_document: {doc_path}\n"
        f"stage_progress: {active_index + 1}/{max(total_stages, 1)}\n"
        "execution_rule: read exactly this document in current PM iteration.\n"
    )
    body = str(doc_text or "").strip()
    if not body:
        body = "(active stage document is empty)"
    return f"{header}\n# Active Stage Document\n\n{body}\n"


def _compose_stage_plan(*, stage: dict[str, Any], doc_text: str) -> str:
    """Compose stage plan text."""
    doc_path = str(stage.get("doc_path") or "").strip().lower()
    if doc_path.endswith("/plan.md"):
        return str(doc_text or "")
    return (
        "# PM Stage Gate\n\n"
        f"- Active document: {str(stage.get('doc_path') or '').strip()}\n"
        "- Non-active docs are deferred to later stages.\n"
        "- Plan references from other documents are intentionally withheld in this iteration.\n"
    )


__all__ = [
    "_all_tasks_terminal",
    "_build_architect_docs_pipeline_payload",
    "_build_tasks_status_signature",
    "_compose_stage_plan",
    "_compose_stage_requirements",
    "_doc_stage_sort_key",
    "_doc_stage_title",
    "_extract_tasks_from_payload",
    "_materialize_zhongshu_blueprints",
    "_normalize_doc_rel_path",
    "_resolve_doc_path_candidates",
    "_resolve_existing_doc_path",
    "_slugify_stage_filename",
]
