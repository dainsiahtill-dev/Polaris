"""Context snapshot storage candidate resolution.

This module is platform infrastructure, not a Bench-specific adapter.  Instance
registry records may be produced by the launcher, factory_bench, or external
agent-driven starts; ContextOS uses them only to resolve the runtime root for a
workspace-bound context snapshot.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from polaris.cells.instances.public.service import list_instances
from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.storage.io_paths import resolve_storage_roots
from polaris.kernelone.storage.layout import default_kernelone_cache_base, workspace_key

logger = logging.getLogger(__name__)


def context_snapshot_candidates(workspace: str, context_hash: str) -> list[tuple[str, Path]]:
    """Return bounded storage candidates for a ContextOS snapshot hash."""
    candidates: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def add(source: str, runtime_root: str | Path) -> None:
        root = Path(runtime_root)
        if not str(root).strip():
            return
        file_path = root / "contexts" / context_hash[:2] / context_hash
        path_key = str(file_path)
        if path_key in seen:
            return
        seen.add(path_key)
        candidates.append((source, file_path))

    add("active_runtime_root", Path(resolve_storage_roots(workspace).runtime_root))
    for source, runtime_root in instance_runtime_root_candidates(workspace):
        add(source, runtime_root)
    add("kernelone_system_cache", default_kernelone_runtime_root(workspace))
    return candidates


def default_kernelone_runtime_root(workspace: str) -> Path:
    """Return the default KernelOne runtime root for ``workspace``."""
    workspace_abs = os.path.abspath(os.path.expanduser(str(workspace or os.getcwd())))
    cache_base = Path(default_kernelone_cache_base())
    metadata_dir = get_workspace_metadata_dir_name()
    cache_parts = cache_base.as_posix().split("/")
    projects_root = (
        cache_base / "projects"
        if metadata_dir in cache_parts
        else cache_base / metadata_dir / "projects"
    )
    return projects_root / workspace_key(workspace_abs) / "runtime"


def instance_runtime_root_candidates(workspace: str) -> list[tuple[str, Path]]:
    """Return registered instance runtime roots that exactly match ``workspace``."""
    requested_workspace = _normalize_workspace(workspace)
    if not requested_workspace:
        return []
    try:
        records = list_instances()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("context_snapshot_candidates: failed to list instances: %s", exc)
        return []

    matches: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        record_workspace = _normalize_workspace(record.get("workspace"))
        runtime_root = _string_field(record.get("runtime_root"))
        if record_workspace == requested_workspace and runtime_root:
            matches.append(record)

    def sort_key(record: dict[str, Any]) -> tuple[int, str]:
        status = _string_field(record.get("status")).lower()
        running_rank = 0 if status == "running" else 1
        updated = _string_field(
            record.get("updated_at") or record.get("last_started_at") or record.get("created_at")
        )
        return (running_rank, updated)

    matches.sort(key=sort_key)
    return [
        (
            f"instance_runtime_root:{_string_field(record.get('instance_id')) or 'unknown'}",
            Path(_string_field(record["runtime_root"])),
        )
        for record in matches
    ]


def _normalize_workspace(value: object) -> str:
    token = _string_field(value)
    if not token:
        return ""
    return os.path.abspath(os.path.expanduser(token)).rstrip(os.sep)


def _string_field(value: object) -> str:
    return str(value or "").strip() if isinstance(value, str) or value is not None else ""
