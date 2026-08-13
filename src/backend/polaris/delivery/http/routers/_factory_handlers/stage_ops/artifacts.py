# ruff: noqa: E402, F403
"""Factory stage-ops helpers — run artifact listing and merging.

Extracted verbatim from the former single-file ``stage_ops`` module during the
lossless decomposition of that god-module.

``factory.py``'s ``_rebind_helper_module`` rebinds these callables into the host
router namespace; the package ``__init__`` rewrites ``__module__`` so the rebind
treats them as package-owned. Cross-module free names are injected by
``_wire_cross_module_namespace``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from polaris.cells.factory.pipeline.public import FactoryRun, FactoryRunService

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from ..mapping import *
from ._common import _resolve_runtime_path, _resolve_task_identifier


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


__all__ = [
    "_artifact_item_from_path",
    "_artifact_item_from_stage_ref",
    "_artifact_response_path",
    "_build_artifacts_response",
    "_extract_task_id_from_payload",
    "_list_run_artifacts",
    "_list_stage_artifacts",
    "_merge_artifact_items",
    "_task_id_from_artifact_file",
    "_task_id_from_artifact_name",
]
