from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..storage.index_cache import count_jsonl_lines, load_index_rows
from ..utils import normalize_path_str as _normalize_path

if TYPE_CHECKING:
    from pathlib import Path


@runtime_checkable
class JobManagerProtocol(Protocol):
    """Protocol for job manager to avoid direct dependency on verify module."""

    def get_all_jobs(self) -> list[Any]: ...


def _get_file_mtime_iso(path: Path) -> str | None:
    """Get file modification time as ISO string."""
    try:
        if path.exists():
            mtime = path.stat().st_mtime
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            return dt.isoformat()
    except OSError:
        pass
    return None


def get_project_stats(
    index_dir: Path,
    project_dir: Path,
) -> dict[str, Any]:
    """Get project statistics from the index.

    Args:
        index_dir: Path to the index directory.
        project_dir: Path to the project directory.

    Returns:
        Dict with project statistics.
    """
    symbols_path = index_dir / "symbols.jsonl"
    refs_path = index_dir / "references.jsonl"
    deps_path = index_dir / "deps.jsonl"

    if not symbols_path.exists():
        return {
            "status": "no_index",
            "message": "Symbol index not found. Run accel_index_build first.",
            "overview": {},
            "symbol_distribution": {},
            "relation_stats": {},
        }

    all_symbols = load_index_rows(index_dir, kind="symbols", key_field="file")

    files: set[str] = set()
    languages: dict[str, int] = defaultdict(int)
    kinds: dict[str, int] = defaultdict(int)

    for row in all_symbols:
        file_path = _normalize_path(str(row.get("file", "")))
        if file_path:
            files.add(file_path)

        lang = str(row.get("lang", "")).strip().lower()
        if lang:
            languages[lang] += 1

        kind = str(row.get("kind", "")).strip().lower()
        if kind:
            kinds[kind] += 1

    refs_count = count_jsonl_lines(refs_path) if refs_path.exists() else 0
    deps_count = count_jsonl_lines(deps_path) if deps_path.exists() else 0

    last_indexed = _get_file_mtime_iso(symbols_path)

    return {
        "status": "ok",
        "overview": {
            "total_files": len(files),
            "total_symbols": len(all_symbols),
            "languages": dict(sorted(languages.items(), key=lambda x: -x[1])),
            "last_indexed": last_indexed,
            "project_path": str(project_dir),
        },
        "symbol_distribution": dict(sorted(kinds.items(), key=lambda x: -x[1])),
        "relation_stats": {
            "references_count": refs_count,
            "dependencies_count": deps_count,
        },
        "index_files": {
            "symbols": {
                "path": str(symbols_path),
                "exists": symbols_path.exists(),
                "rows": len(all_symbols),
            },
            "references": {
                "path": str(refs_path),
                "exists": refs_path.exists(),
                "rows": refs_count,
            },
            "dependencies": {
                "path": str(deps_path),
                "exists": deps_path.exists(),
                "rows": deps_count,
            },
        },
    }


def get_health_status(
    index_dir: Path,
    project_dir: Path,
    paths: dict[str, Path],
    job_manager: JobManagerProtocol | None = None,
) -> dict[str, Any]:
    """Get system health status.

    Args:
        index_dir: Path to the index directory.
        project_dir: Path to the project directory.
        paths: Project storage paths.
        job_manager: Optional job manager instance for job statistics.

    Returns:
        Dict with health status information.
    """
    symbols_path = index_dir / "symbols.jsonl"
    index_exists = symbols_path.exists()

    index_stale = False
    index_age_hours: float | None = None
    last_indexed = _get_file_mtime_iso(symbols_path)

    if index_exists:
        try:
            mtime = symbols_path.stat().st_mtime
            age_seconds = datetime.now(tz=timezone.utc).timestamp() - mtime
            index_age_hours = round(age_seconds / 3600, 2)
            index_stale = age_seconds > 86400
        except OSError:
            pass

    file_count = 0
    if index_exists:
        file_count = count_jsonl_lines(symbols_path)

    semantic_cache_path = paths.get("state", index_dir.parent / "state") / "semantic_cache.db"
    semantic_cache_ok = False
    semantic_cache_size = 0
    try:
        if semantic_cache_path.exists():
            semantic_cache_ok = True
            semantic_cache_size = int(semantic_cache_path.stat().st_size / 1024)
    except OSError:
        pass

    receipt_store_path = paths.get("state", index_dir.parent / "state") / "session_receipts.db"
    receipt_store_ok = False
    try:
        if receipt_store_path.exists():
            receipt_store_ok = True
    except OSError:
        pass

    active_jobs = 0
    pending_jobs = 0
    total_jobs = 0

    if job_manager is not None:
        all_jobs = job_manager.get_all_jobs()
        total_jobs = len(all_jobs)
        for job in all_jobs:
            state = getattr(job, "state", "")
            if state == "running":
                active_jobs += 1
            elif state == "pending":
                pending_jobs += 1

    overall_status = "healthy"
    issues: list[str] = []

    if not index_exists:
        overall_status = "degraded"
        issues.append("Symbol index not found")
    elif index_stale:
        overall_status = "warning"
        issues.append("Index may be stale (>24h old)")

    if not receipt_store_ok:
        if overall_status == "healthy":
            overall_status = "warning"
        issues.append("Receipt store not found")

    return {
        "status": overall_status,
        "issues": issues,
        "index": {
            "exists": index_exists,
            "stale": index_stale,
            "last_update": last_indexed,
            "age_hours": index_age_hours,
            "file_count": file_count,
        },
        "cache": {
            "semantic_cache_ok": semantic_cache_ok,
            "semantic_cache_size_kb": semantic_cache_size,
            "receipt_store_ok": receipt_store_ok,
        },
        "jobs": {
            "active": active_jobs,
            "pending": pending_jobs,
            "total_tracked": total_jobs,
        },
        "paths": {
            "project": str(project_dir),
            "index": str(index_dir),
            "state": str(paths.get("state", "")),
        },
    }
