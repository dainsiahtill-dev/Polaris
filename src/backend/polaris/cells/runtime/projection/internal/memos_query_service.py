import json
import logging
import os
from datetime import datetime
from typing import Any

from polaris.cells.runtime.projection.public.service import format_mtime
from polaris.kernelone.runtime.defaults import DEFAULT_WORKSPACE
from polaris.kernelone.storage.io_paths import (
    build_cache_root,
    normalize_artifact_rel_path,
    resolve_artifact_path,
)

logger = logging.getLogger(__name__)


def _record_key(record: dict[str, Any]) -> str:
    rel = str(record.get("rel_path") or record.get("path") or "").strip()
    if rel:
        return rel
    stamp = str(record.get("timestamp") or "")
    task_id = str(record.get("task_id") or "")
    return f"{stamp}:{task_id}"


def _read_index_records(index_path: str) -> tuple[list[dict[str, Any]], set[str]]:
    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    if not index_path or not os.path.isfile(index_path):
        return records, seen_keys
    try:
        with open(index_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (RuntimeError, ValueError) as exc:
                    logger.warning("Failed to parse memo index line in %s: %s", index_path, exc)
                    continue
                if not isinstance(record, dict):
                    continue
                key = _record_key(record)
                if key and key in seen_keys:
                    continue
                if key:
                    seen_keys.add(key)
                records.append(record)
    except (RuntimeError, ValueError) as e:
        logger.warning("Failed to read memo index %s: %s", index_path, e)
    return records, seen_keys


def _scan_memo_dir(
    memos_dir: str,
    seen_keys: set[str],
    workspace: str,
    cache_root: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not memos_dir or not os.path.isdir(memos_dir):
        return records
    try:
        for entry in os.scandir(memos_dir):
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".md"):
                continue
            if entry.name.lower().startswith("pm_memo_summary"):
                continue
            rel_path = normalize_artifact_rel_path(f"runtime/memos/{entry.name}")
            if rel_path in seen_keys:
                continue
            seen_keys.add(rel_path)
            records.append(
                {
                    "timestamp": format_mtime(entry.path),
                    "rel_path": rel_path,
                    "task_id": "",
                    "task_title": "",
                    "summary": "",
                }
            )
    except (RuntimeError, ValueError) as e:
        logger.warning("Failed to scan memo dir %s: %s", memos_dir, e)
    return records


def _resolve_memo_path(rel_path: str, workspace: str, cache_root: str) -> str:
    # Lazy import to break circular dependency: artifact_store -> projection -> memos_query -> artifact_store
    from polaris.cells.runtime.artifact_store.public.service import resolve_safe_path

    if not rel_path:
        return ""
    try:
        return resolve_safe_path(workspace, cache_root, rel_path)
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to resolve memo path %s: %s", rel_path, exc)
        return ""


def _record_sort_key(item: dict[str, Any]) -> float:
    raw = str(item.get("timestamp") or "")
    try:
        return datetime.fromisoformat(raw).timestamp()
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to parse memo timestamp %s: %s", raw, exc)
        return 0.0


def list_memos(workspace: str, ramdisk_root: str, limit: int = 200) -> dict[str, Any]:
    workspace = workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root(ramdisk_root or "", workspace)
    memos_dir = resolve_artifact_path(workspace, cache_root, "runtime/memos")
    index_path = resolve_artifact_path(workspace, cache_root, "runtime/memos/index.jsonl")

    records, seen_keys = _read_index_records(index_path)
    if not records:
        records = _scan_memo_dir(memos_dir, seen_keys, workspace, cache_root)

    records.sort(key=_record_sort_key, reverse=True)
    trimmed = records[: max(1, limit)]

    items: list[dict[str, Any]] = []
    for record in trimmed:
        rel_path = str(record.get("rel_path") or "")
        full_path = _resolve_memo_path(rel_path, workspace, cache_root)
        item_path = full_path or rel_path
        items.append(
            {
                "name": os.path.basename(rel_path) if rel_path else os.path.basename(full_path),
                "path": item_path,
                "mtime": format_mtime(full_path) if full_path else "",
                "summary": record.get("summary") or "",
                "task_id": record.get("task_id") or "",
                "task_title": record.get("task_title") or "",
                "status": record.get("status") or "",
                "acceptance": record.get("acceptance"),
                "run_id": record.get("run_id") or "",
                "director_attempt": record.get("director_attempt") or None,
            }
        )

    return {"items": items, "count": len(items)}
