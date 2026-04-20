"""Path utilities for Harborpilot Loop.

Hard-cut storage layout:
- runtime/*
- workspace/*
- config/*
"""

from __future__ import annotations

import logging
import os

from polaris.kernelone.storage import (
    normalize_logical_rel_path,
    resolve_logical_path,
    resolve_storage_roots,
)

logger = logging.getLogger(__name__)

ARTIFACT_ROOT = "runtime"
LEGACY_ARTIFACT_ROOT = ""
ARTIFACT_NAMESPACE = ""
LEGACY_ARTIFACT_NAMESPACE = ""

# ---------------------------------------------------------------------------
# Workspace sentinel directory
# ---------------------------------------------------------------------------
# The sentinel directory is used to detect the workspace root by traversing
# upward until a directory with this name is found.
# Defaults to "docs" for backward compatibility.  Override via the environment
# variable KERNELONE_WORKSPACE_SENTINEL or by passing ``sentinel_dir`` explicitly
# to ``find_workspace_root()`` / ``resolve_workspace_path()``.
# ---------------------------------------------------------------------------
_DEFAULT_WORKSPACE_SENTINEL = os.environ.get("KERNELONE_WORKSPACE_SENTINEL", "docs")


def find_workspace_root(start: str, *, sentinel_dir: str | None = None) -> str:
    """Traverse upward from ``start`` to find the workspace root.

    The workspace root is the first ancestor directory that contains a
    ``sentinel_dir`` subdirectory (default: ``docs``, or the value of
    the ``KERNELONE_WORKSPACE_SENTINEL`` environment variable).

    Args:
        start: Starting directory path.
        sentinel_dir: Override the sentinel directory name.

    Returns:
        Absolute path to the workspace root, or ``""`` if not found.
    """
    sentinel = sentinel_dir or _DEFAULT_WORKSPACE_SENTINEL
    current = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(current, sentinel)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return ""


def resolve_workspace_path(
    path: str,
    *,
    require_docs: bool = True,
    sentinel_dir: str | None = None,
) -> str:
    """Resolve and validate a workspace path.

    Args:
        path: Candidate workspace path.
        require_docs: If True, raise ValueError when the sentinel directory
            is not found at or above ``path``.
        sentinel_dir: Override the sentinel directory name used for detection.

    Returns:
        Absolute workspace root path.
    """
    sentinel = sentinel_dir or _DEFAULT_WORKSPACE_SENTINEL
    start = (path or "").strip()
    if not start:
        start = os.getcwd()
    start = os.path.abspath(start)
    if not os.path.isdir(start):
        raise ValueError(f"Workspace path does not exist: {start}")
    root = find_workspace_root(start, sentinel_dir=sentinel)
    if not root:
        if require_docs:
            raise ValueError(f"No {sentinel!r} directory found at or above workspace: {start}")
        return start
    if os.path.abspath(root) != start:
        logger.info("[workspace] Using '%s' (found %r above '%s').", root, sentinel, start)
    return root


def workspace_has_docs(workspace: str, *, sentinel_dir: str | None = None) -> bool:
    """Check whether a workspace contains the sentinel directory.

    Args:
        workspace: Workspace root path.
        sentinel_dir: Override the sentinel directory name.

    Returns:
        True if the sentinel directory exists directly inside the workspace
        or inside the workspace's persistent storage root.
    """
    if not workspace:
        return False
    sentinel = sentinel_dir or _DEFAULT_WORKSPACE_SENTINEL
    if os.path.isdir(os.path.join(workspace, sentinel)):
        return True
    try:
        roots = resolve_storage_roots(workspace)
        return os.path.isdir(os.path.join(roots.workspace_persistent_root, sentinel))
    except (OSError, ValueError) as exc:
        logger.debug("workspace_has_docs: could not resolve storage roots: workspace=%s error=%s", workspace, exc)
        return False


def normalize_artifact_rel_path(rel_path: str) -> str:
    raw = str(rel_path or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    return normalize_logical_rel_path(raw)


def _artifact_base_dir(workspace_full: str, cache_root_full: str) -> str:
    if cache_root_full:
        return cache_root_full
    return resolve_storage_roots(workspace_full).runtime_root


def _strip_artifact_root_prefix(rel_path: str) -> str:
    p = normalize_artifact_rel_path(rel_path)
    if p.startswith("runtime/"):
        return p[len("runtime/") :]
    if p.startswith("workspace/"):
        return p[len("workspace/") :]
    if p.startswith("config/"):
        return p[len("config/") :]
    return p


def build_cache_root(ramdisk_root: str, workspace_full: str) -> str:
    roots = resolve_storage_roots(workspace_full, ramdisk_root=ramdisk_root or None)
    return roots.runtime_root


def is_hot_artifact_path(rel_path: str) -> bool:
    p = normalize_artifact_rel_path(rel_path)
    return p == "runtime" or p.startswith("runtime/")


def resolve_run_dir(workspace_full: str, cache_root_full: str, run_id: str) -> str:
    if not run_id:
        return ""
    runtime_root = cache_root_full or resolve_storage_roots(workspace_full).runtime_root
    return os.path.join(runtime_root, "runs", run_id)


def update_latest_pointer(workspace_full: str, cache_root_full: str, run_id: str) -> None:
    try:
        from polaris.kernelone.fs.text_ops import write_json_atomic
    except ImportError:  # pragma: no cover - script-mode fallback
        from polaris.kernelone.fs.text_ops import write_json_atomic  # type: ignore

    if not run_id:
        return
    runtime_root = cache_root_full or resolve_storage_roots(workspace_full).runtime_root
    latest_dir = os.path.join(runtime_root, "runs", "latest")
    run_dir = resolve_run_dir(workspace_full, cache_root_full, run_id)
    pointer_path = os.path.join(runtime_root, "latest_run.json")
    write_json_atomic(pointer_path, {"run_id": run_id, "path": run_dir})
    if os.path.exists(latest_dir):
        try:
            if os.path.islink(latest_dir):
                os.remove(latest_dir)
        except (RuntimeError, ValueError, OSError) as e:
            logger.debug(f"Failed to remove old symlink: {e}")
    try:
        os.symlink(run_dir, latest_dir, target_is_directory=True)
    except (RuntimeError, ValueError, OSError) as e:
        logger.debug(f"Failed to create symlink: {e}")


def resolve_artifact_path(
    workspace_full: str,
    cache_root_full: str,
    rel_path: str,
    run_id: str | None = None,
) -> str:
    try:
        from polaris.kernelone.fs.text_ops import is_run_artifact
    except ImportError:  # pragma: no cover - script-mode fallback
        from polaris.kernelone.fs.text_ops import is_run_artifact  # type: ignore

    if not rel_path:
        return ""
    raw = str(rel_path).strip()
    if os.path.isabs(raw):
        absolute = os.path.abspath(raw)
        roots = resolve_storage_roots(workspace_full)
        allowed_roots = [
            os.path.abspath(roots.runtime_root),
            os.path.abspath(roots.workspace_persistent_root),
            os.path.abspath(roots.config_root),
        ]
        for root in allowed_roots:
            try:
                if os.path.commonpath([root, absolute]) == root:
                    return absolute
            except (RuntimeError, ValueError) as exc:
                # commonpath raises ValueError on Windows for cross-drive paths.
                # Log so security rejections are observable; the outer
                # ValueError below keeps this fail-closed.
                logger.warning(
                    "resolve_artifact_path: path safety check error, skipping root: root=%r absolute=%r error=%s",
                    root,
                    absolute,
                    exc,
                )
                continue
        raise ValueError(f"UNSUPPORTED_PATH_PREFIX: {rel_path}")

    normalized = normalize_artifact_rel_path(raw)
    if run_id and is_run_artifact(normalized):
        run_dir = resolve_run_dir(workspace_full, cache_root_full, run_id)
        basename = os.path.basename(normalized)
        return os.path.join(run_dir, basename)
    if normalized == "runtime" or normalized.startswith("runtime/"):
        runtime_root = cache_root_full or resolve_storage_roots(workspace_full).runtime_root
        if normalized == "runtime":
            return runtime_root
        return os.path.join(runtime_root, normalized[len("runtime/") :])
    return resolve_logical_path(workspace_full, normalized)
