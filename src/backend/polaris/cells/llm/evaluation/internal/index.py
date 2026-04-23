"""Evaluation Framework - Index Management.

Thread-safe index management with file locking for concurrent access.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from polaris.cells.storage.layout.public.service import get_polaris_root
from polaris.kernelone.storage import resolve_runtime_path

from .utils import utc_now, write_json_atomic

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

DEFAULT_INDEX_VERSION = "2.0"

logger = logging.getLogger(__name__)


def _new_index_payload() -> dict[str, Any]:
    """Create a new empty index payload.

    Returns:
        A new index dictionary with default structure.
    """
    return {"roles": {}, "providers": {}, "version": DEFAULT_INDEX_VERSION}


# ---------------------------------------------------------------------------
# KFS directory-listing port (gap stub)
#
# KernelFileSystemAdapter (polaris.kernelone.fs.contracts) does not expose a
# list_dir / list_files operation today.  Until the KernelOne FS contract is
# extended, this Cell defines a minimal port for its own use and provides a
# concrete default implementation backed by os.listdir so that existing
# behaviour is preserved.  The gap is recorded in cell.yaml
# verification.gaps.
#
# GAP: kernelone.fs does not expose list_dir — tracked in evaluation cell.yaml
# ---------------------------------------------------------------------------


class KernelFsReportsPort(Protocol):
    """Port for listing report JSON files in a directory.

    Injected at runtime; a default os-backed implementation is used when no
    injection has been performed (bootstrap not yet wired for this operation).
    """

    def list_json_files(self, directory: str) -> list[str]:
        """Return filenames (not full paths) of *.json files in *directory*.

        Returns an empty list if the directory does not exist.
        """
        ...

    def dir_exists(self, directory: str) -> bool:
        """Return True if *directory* exists and is a directory."""
        ...


class _OsBackedReportsAdapter:
    """Default implementation of KernelFsReportsPort backed by os.listdir.

    This adapter is used when the bootstrap layer has not injected a
    KernelFsReportsPort.  It is intentionally minimal and explicit.
    """

    def list_json_files(self, directory: str) -> list[str]:
        try:
            return [f for f in os.listdir(directory) if f.endswith(".json")]
        except OSError:
            return []

    def dir_exists(self, directory: str) -> bool:
        return os.path.isdir(directory)


# C2 fix: Protected global port with RLock
_default_reports_port: KernelFsReportsPort | None = None
_reports_port_lock = threading.RLock()


def set_reports_port(port: KernelFsReportsPort) -> None:
    """Inject the KernelFsReportsPort (called by bootstrap or tests).

    Args:
        port: The port implementation to inject.
    """
    global _default_reports_port
    with _reports_port_lock:
        _default_reports_port = port


def _get_reports_port() -> KernelFsReportsPort:
    """Return the injected port, falling back to os-backed default."""
    with _reports_port_lock:
        if _default_reports_port is not None:
            return _default_reports_port
    return _OsBackedReportsAdapter()


# ---------------------------------------------------------------------------
# File locking for thread-safety (C1 fix)
# ---------------------------------------------------------------------------

# Per-path file locks for index write operations
_index_file_locks: dict[str, threading.RLock] = {}
_index_file_locks_guard = threading.Lock()


def _get_path_lock(path: str) -> threading.RLock:
    """Get or create an RLock for a specific index path.

    Args:
        path: The normalized absolute path to the index file.

    Returns:
        An RLock for protecting operations on this path.
    """
    normalized = str(Path(path).resolve())
    with _index_file_locks_guard:
        if normalized not in _index_file_locks:
            _index_file_locks[normalized] = threading.RLock()
        return _index_file_locks[normalized]


@contextmanager
def _index_write_lock(path: str) -> Generator[None, None, None]:
    """Context manager for exclusive index write access.

    Uses per-path locking to allow concurrent writes to different indexes
    while serializing writes to the same index.

    Args:
        path: The index file path to lock.

    Yields:
        None when the lock is acquired.
    """
    lock = _get_path_lock(path)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def _index_read_lock(paths: list[str]) -> Generator[None, None, None]:
    """Context manager for index read access with path-level locking.

    Acquires read locks for all paths to ensure consistent snapshot during
    read-modify-write operations.

    Args:
        paths: List of index file paths to lock.

    Yields:
        None when all locks are acquired.
    """
    # Sort paths to prevent deadlocks from lock ordering
    sorted_paths = sorted(set(paths))
    locks = [_get_path_lock(p) for p in sorted_paths]

    # Acquire all locks in order
    for lock in locks:
        lock.acquire()
    try:
        yield
    finally:
        # Release in reverse order
        for lock in reversed(locks):
            lock.release()


# ---------------------------------------------------------------------------
# Path resolution helpers (pure path math — no I/O)
# ---------------------------------------------------------------------------

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name


def _get_default_index_path() -> str:
    """获取默认索引路径"""
    workspace = os.environ.get("KERNELONE_WORKSPACE", ".")
    return str(Path(workspace) / get_workspace_metadata_dir_name() / "llm_test_index.json")


def _resolve_workspace_path(workspace: Any) -> str | None:
    if workspace is None:
        return None
    if isinstance(workspace, str):
        token = workspace.strip()
        return token or None
    candidate = getattr(workspace, "workspace", None)
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return None


def _workspace_index_path(workspace_path: str | None) -> str:
    if workspace_path:
        return str(Path(workspace_path) / get_workspace_metadata_dir_name() / "llm_test_index.json")
    return _get_default_index_path()


def _global_index_path(workspace_path: str | None) -> str:
    root = workspace_path or get_polaris_root()
    return str(Path(root) / get_workspace_metadata_dir_name() / "config" / "llm" / "llm_test_index.json")


def _resolve_index_paths(workspace: Any) -> list[str]:
    workspace_path = _resolve_workspace_path(workspace)
    candidates = [
        _global_index_path(workspace_path),
        _workspace_index_path(workspace_path),
    ]
    unique_paths: list[str] = []
    for candidate in candidates:
        # Pure path normalization — no filesystem I/O.
        normalized = str(Path(str(candidate)).resolve())
        if normalized not in unique_paths:
            unique_paths.append(normalized)
    return unique_paths


# ---------------------------------------------------------------------------
# Index I/O helpers
# ---------------------------------------------------------------------------


def _load_index_file(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_index_payload(paths: list[str], payload: dict[str, Any]) -> None:
    for path in paths:
        write_json_atomic(path, payload)


# ---------------------------------------------------------------------------
# Report-field extraction helpers (pure logic, no I/O)
# ---------------------------------------------------------------------------


def _extract_target(report: Mapping[str, Any]) -> tuple[str, str, str]:
    target_raw = report.get("target")
    target = target_raw if isinstance(target_raw, dict) else {}
    role = str(target.get("role") or report.get("role") or "").strip()
    provider_id = str(target.get("provider_id") or report.get("provider_id") or "").strip()
    model = str(target.get("model") or report.get("model") or "").strip()
    return role, provider_id, model


def _extract_ready_grade(report: Mapping[str, Any]) -> tuple[bool, str]:
    final = report.get("final") if isinstance(report.get("final"), dict) else {}
    if final:
        ready = bool(final.get("ready"))
        grade = str(final.get("grade") or ("PASS" if ready else "FAIL")).strip().upper()
        return ready, grade

    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    if summary:
        ready = bool(summary.get("ready"))
        grade = str(summary.get("grade") or ("PASS" if ready else "FAIL")).strip().upper()
        return ready, grade

    return False, "UNKNOWN"


def _extract_run_id(report: Mapping[str, Any]) -> str:
    return str(report.get("test_run_id") or report.get("run_id") or "").strip()


def _extract_suites(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    suites = report.get("suites")
    if isinstance(suites, dict):
        return {
            str(name): (value if isinstance(value, dict) else {"ok": bool(value)}) for name, value in suites.items()
        }
    if isinstance(suites, list):
        mapped: dict[str, dict[str, Any]] = {}
        for suite in suites:
            if not isinstance(suite, dict):
                continue
            name = str(suite.get("suite_name") or suite.get("name") or "").strip()
            if not name:
                continue
            total = int(suite.get("total_cases") or 0)
            passed = int(suite.get("passed_cases") or 0)
            mapped[name] = {
                "ok": passed >= total if total > 0 else False,
                "total_cases": total,
                "passed_cases": passed,
                "failed_cases": int(suite.get("failed_cases") or max(0, total - passed)),
            }
        return mapped
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_llm_test_index(workspace: Any = None) -> dict[str, Any]:
    """Load LLM test index from disk.

    Reads the index from global or workspace-local path. This is a read-only
    operation but uses locking to ensure consistent snapshot when used with
    concurrent writes.

    Args:
        workspace: Workspace identifier (str, object with .workspace, or None).

    Returns:
        The loaded index dictionary.
    """
    paths = _resolve_index_paths(workspace)

    # Use read lock to ensure consistent snapshot
    with _index_read_lock(paths):
        for path in paths:
            payload = _load_index_file(path)
            if payload is not None:
                return payload

    return _new_index_payload()


def reset_llm_test_index(workspace: Any = None) -> None:
    """Reset LLM test index to empty state.

    Creates a new empty index with version and reset timestamp.

    Args:
        workspace: Workspace identifier.
    """
    payload = _new_index_payload()
    payload["reset_at"] = utc_now()
    paths = _resolve_index_paths(workspace)

    # Use write lock on primary path (global index)
    primary_path = paths[0] if paths else _get_default_index_path()
    with _index_write_lock(primary_path):
        _write_index_payload(paths, payload)


def reconcile_llm_test_index(
    workspace: Any,
    reports_dir: Any = None,
) -> dict[str, Any]:
    """Reconcile LLM test index by scanning report directory.

    Scans *reports_dir* for JSON report files and merges them into the index.
    File-system access is delegated to the injected KernelFsReportsPort
    (set via ``set_reports_port``); if none is injected the os-backed default
    is used. No direct os.listdir / os.path calls are made in this function.

    C1 fix: Uses path-level locking to prevent concurrent read-modify-write
    races when multiple threads reconcile simultaneously.

    Args:
        workspace: Workspace identifier.
        reports_dir: Directory containing report JSON files, or None for default.

    Returns:
        The reconciled index dictionary.
    """
    workspace_path = _resolve_workspace_path(workspace)
    if workspace_path is None:
        return _new_index_payload()

    paths = _resolve_index_paths(workspace_path)
    primary_path = paths[0] if paths else _global_index_path(workspace_path)

    # C1 fix: Use write lock for the entire read-modify-write operation
    with _index_write_lock(primary_path):
        index = load_llm_test_index(workspace_path)

        # Backward compatibility: old callers pass config payload as 2nd arg.
        if isinstance(reports_dir, dict):
            reports_dir = None

        if reports_dir is None:
            reports_dir = resolve_runtime_path(workspace_path, "runtime/llm_tests/reports")

        reports_dir_str = str(reports_dir)
        port = _get_reports_port()

        if not port.dir_exists(reports_dir_str):
            return index

        # Scan reports directory - delegated to port (no direct os.listdir here)
        for filename in port.list_json_files(reports_dir_str):
            filepath = str(Path(reports_dir_str) / filename)
            try:
                with open(filepath, encoding="utf-8") as f:
                    report = json.load(f)
            except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
                continue

            # Update index
            role, provider_id, model = _extract_target(report)
            ready, grade = _extract_ready_grade(report)
            run_id = _extract_run_id(report)
            suites = _extract_suites(report)

            if provider_id:
                if "providers" not in index:
                    index["providers"] = {}
                index["providers"][provider_id] = {
                    "model": model,
                    "role": role,
                    "timestamp": report.get("timestamp"),
                    "ready": ready,
                    "grade": grade,
                    "last_run_id": run_id,
                    "suites": suites,
                }

            if role:
                if "roles" not in index:
                    index["roles"] = {}
                index["roles"][role] = {
                    "provider_id": provider_id,
                    "model": model,
                    "timestamp": report.get("timestamp"),
                    "ready": ready,
                    "grade": grade,
                    "last_run_id": run_id,
                    "suites": suites,
                }

        index["last_reconcile"] = utc_now()

        # Save index (global priority, mirrored to compatible path)
        _write_index_payload(paths, index)

        return index


def update_index_with_report(
    workspace: Any,
    report: dict[str, Any],
) -> None:
    """Update index with a single test report.

    C1 fix: Uses path-level locking to prevent concurrent read-modify-write
    races when multiple threads update the same index simultaneously.

    Args:
        workspace: Workspace identifier.
        report: The test report dictionary.
    """
    workspace_path = _resolve_workspace_path(workspace)
    if workspace_path is None:
        return

    paths = _resolve_index_paths(workspace_path)
    primary_path = paths[0] if paths else _workspace_index_path(workspace_path)

    # C1 fix: Use write lock for the entire read-modify-write operation
    with _index_write_lock(primary_path):
        index = load_llm_test_index(workspace_path)

        if "roles" not in index:
            index["roles"] = {}
        if "providers" not in index:
            index["providers"] = {}

        role, provider_id, model = _extract_target(report)
        ready, grade = _extract_ready_grade(report)
        run_id = _extract_run_id(report)
        suites = _extract_suites(report)
        suite_summary = {name: {"ok": bool(value.get("ok"))} for name, value in suites.items()}

        if role:
            index["roles"][role] = {
                "ready": ready,
                "grade": grade,
                "last_run_id": run_id,
                "timestamp": report.get("timestamp"),
                "suites": suite_summary,
            }

        if provider_id:
            index["providers"][provider_id] = {
                "ready": ready,
                "grade": grade,
                "last_run_id": run_id,
                "timestamp": report.get("timestamp"),
                "model": model,
                "role": role,
                "suites": suite_summary,
            }

        index["last_update"] = utc_now()

        _write_index_payload(paths, index)


__all__ = [
    "KernelFsReportsPort",
    "load_llm_test_index",
    "reconcile_llm_test_index",
    "reset_llm_test_index",
    "set_reports_port",
    "update_index_with_report",
]
