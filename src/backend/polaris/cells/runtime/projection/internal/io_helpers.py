"""Projection I/O helpers with explicit UTF-8 behavior."""

from __future__ import annotations

import os
import sys
from typing import Any

# NOTE: artifact_store imports are intentionally lazy (inside proxy functions) to
# avoid a load-time circular dependency:
#   projection/__init__ → projection/public/service → io_helpers (this module)
#   → artifact_store/__init__ → artifact_store/public/service → artifacts.py
#   → projection/public/service (still loading) → ImportError
# The proxy functions below are equivalent to module-level re-exports but
# deferred to first-call, which is after all __init__.py loading completes.
from polaris.cells.runtime.projection.internal.file_io import (
    format_mtime,
    read_file_head,
    read_file_tail,
    read_incremental,
    read_json,
)
from polaris.kernelone.storage.io_paths import build_cache_root


def resolve_artifact_path(workspace: str, cache_root: str, rel_path: str) -> str:
    """Proxy to artifact_store.public.service.resolve_artifact_path (lazy load)."""
    from polaris.cells.runtime.artifact_store.public.service import (
        resolve_artifact_path as _resolve,
    )

    return _resolve(workspace, cache_root, rel_path)


def select_latest_artifact(workspace: str, cache_root: str, rel_path: str):
    """Proxy to artifact_store.public.service.select_latest_artifact (lazy load)."""
    from polaris.cells.runtime.artifact_store.public.service import (
        select_latest_artifact as _select,
    )

    return _select(workspace, cache_root, rel_path)


def get_git_status(workspace: str) -> dict[str, Any]:
    workspace_path = os.path.abspath(str(workspace or ""))
    git_path = os.path.join(workspace_path, ".git")
    present = os.path.isdir(git_path) or os.path.isfile(git_path)
    return {
        "present": present,
        "root": workspace_path if present else "",
    }


def get_lancedb_status() -> dict[str, Any]:
    try:
        import lancedb  # type: ignore
    except (RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "python": sys.executable,
        }
    version = getattr(lancedb, "__version__", None)
    return {
        "ok": True,
        "error": None,
        "python": sys.executable,
        "version": version,
    }


__all__ = [
    "build_cache_root",
    "format_mtime",
    "get_git_status",
    "get_lancedb_status",
    "read_file_head",
    "read_file_tail",
    "read_incremental",
    "read_json",
    "resolve_artifact_path",
    "select_latest_artifact",
]
