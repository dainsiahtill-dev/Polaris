from __future__ import annotations

import logging
import os
from pathlib import Path

from polaris.kernelone.storage import normalize_logical_rel_path, resolve_logical_path, resolve_storage_roots

from .errors import DatabasePathError, DatabasePolicyError

_logger = logging.getLogger(__name__)

_SQLITE_MEMORY_PATH = ":memory:"


def _expand_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def _is_within(parent: str, child: str) -> bool:
    try:
        parent_abs = os.path.abspath(parent)
        child_abs = os.path.abspath(child)
        return os.path.commonpath([parent_abs, child_abs]) == parent_abs
    except (RuntimeError, ValueError):
        # Fail closed: if we cannot determine containment, deny access for security
        _logger.debug("Path containment check failed: parent=%s child=%s", parent, child)
        return False


def managed_storage_roots(workspace: str) -> tuple[str, str, str]:
    roots = resolve_storage_roots(workspace)
    return (
        os.path.abspath(roots.runtime_root),
        os.path.abspath(roots.workspace_persistent_root),
        os.path.abspath(roots.config_root),
    )


def is_managed_storage_path(workspace: str, path: str) -> bool:
    target = os.path.abspath(path)
    roots = managed_storage_roots(workspace)
    return any(_is_within(root, target) for root in roots)


def _resolve_relative_database_path(workspace: str, token: str, *, kind: str, allow_unmanaged: bool) -> str:
    """Separate managed namespaces from explicitly allowed relative DB paths."""
    prefix = token.replace("\\", "/").split("/", 1)[0]
    if prefix in {"runtime", "workspace", "config"}:
        try:
            logical = normalize_logical_rel_path(token)
            return resolve_logical_path(workspace, logical)
        except (RuntimeError, ValueError) as exc:
            raise DatabasePathError(f"managed {kind} path resolution failed: {token}") from exc
    try:
        # The layout normalizer validates containment under a known prefix;
        # it deliberately does not accept ordinary DB filenames directly.
        logical = normalize_logical_rel_path(f"workspace/{token}")
        if not logical.startswith("workspace/"):
            raise ValueError("relative DB path escaped workspace")
        resolved = _expand_path(os.path.join(workspace, logical[len("workspace/") :]))
        if not _is_within(workspace, resolved):
            raise ValueError("relative DB path escaped workspace")
    except (RuntimeError, ValueError) as exc:
        raise DatabasePathError(f"invalid {kind} path: {token}") from exc
    if not allow_unmanaged and not is_managed_storage_path(workspace, resolved):
        raise DatabasePathError(f"invalid {kind} path: {token}")
    return resolved


def resolve_sqlite_path(
    workspace: str,
    raw_path: str,
    *,
    allow_unmanaged_absolute: bool,
    ensure_parent: bool,
    default_logical_path: str = "runtime/db/default.sqlite",
) -> str:
    token = str(raw_path or "").strip()
    if not token:
        token = default_logical_path
    if token == _SQLITE_MEMORY_PATH:
        return token
    if token.startswith("file:"):
        return token

    if os.path.isabs(token):
        expanded = os.path.expandvars(os.path.expanduser(token))
        resolved = _expand_path(expanded)
        if not allow_unmanaged_absolute and not is_managed_storage_path(workspace, resolved):
            raise DatabasePolicyError(f"absolute sqlite path is outside managed storage roots: {resolved}")
    else:
        resolved = _resolve_relative_database_path(
            workspace, token, kind="sqlite", allow_unmanaged=allow_unmanaged_absolute
        )

    if ensure_parent:
        Path(resolved).parent.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_lancedb_path(
    workspace: str,
    raw_path: str,
    *,
    allow_unmanaged_absolute: bool,
    ensure_exists: bool,
    default_logical_path: str = "workspace/lancedb",
) -> str:
    token = str(raw_path or "").strip()
    if not token:
        token = default_logical_path

    expanded = os.path.expandvars(os.path.expanduser(token))
    if os.path.isabs(expanded):
        resolved = _expand_path(expanded)
        if not allow_unmanaged_absolute and not is_managed_storage_path(workspace, resolved):
            raise DatabasePolicyError(f"absolute LanceDB path is outside managed storage roots: {resolved}")
    else:
        resolved = _resolve_relative_database_path(
            workspace, token, kind="LanceDB", allow_unmanaged=allow_unmanaged_absolute
        )

    if ensure_exists:
        os.makedirs(resolved, exist_ok=True)
    return resolved
