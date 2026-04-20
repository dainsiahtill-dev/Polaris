from __future__ import annotations

import logging
import os
from pathlib import Path

from polaris.kernelone.runtime.run_id import validate_run_id
from polaris.kernelone.storage import (
    UNSUPPORTED_PATH_PREFIX,
    normalize_logical_rel_path,
    resolve_logical_path,
    resolve_runtime_path,
    resolve_storage_roots,
)

_logger = logging.getLogger(__name__)


def _emit_audit_internal_failure(error_type: str, error_details: dict) -> None:
    """Attempt to emit internal audit event; degrade gracefully on failure."""
    try:
        from polaris.kernelone.audit.contracts import KernelAuditEventType
        from polaris.kernelone.audit.runtime import KernelAuditRuntime

        runtime = KernelAuditRuntime.get_instance(Path.cwd())
        runtime._emit_internal_event(
            KernelAuditEventType.INTERNAL_AUDIT_FAILURE,
            {"source_module": "evidence_paths", "error_type": error_type, **error_details},
        )
    except (RuntimeError, ValueError):
        _logger.warning("Audit internal failure (degraded): %s %s", error_type, error_details)


def _is_within_path(parent: str, child: str) -> bool:
    try:
        parent_abs = os.path.abspath(parent)
        child_abs = os.path.abspath(child)
        return os.path.commonpath([parent_abs, child_abs]) == parent_abs
    except (RuntimeError, ValueError) as exc:
        _emit_audit_internal_failure("path_check_error", {"parent": parent, "child": child, "error": str(exc)})
        return False


def _logical_path_from_absolute(workspace: str, absolute_path: str) -> str:
    roots = resolve_storage_roots(workspace)
    candidates = (
        ("runtime", roots.runtime_root),
        ("workspace", roots.workspace_persistent_root),
        ("config", roots.config_root),
    )
    for prefix, root in candidates:
        if not _is_within_path(root, absolute_path):
            continue
        rel = os.path.relpath(os.path.abspath(absolute_path), os.path.abspath(root))
        rel_norm = rel.replace("\\", "/")
        if rel_norm in {".", ""}:
            return prefix
        return f"{prefix}/{rel_norm}"
    raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {absolute_path}")


def resolve_evidence_artifact_reference(workspace: str, artifact_path: str) -> tuple[str, str]:
    """Resolve artifact reference to canonical absolute+logical paths.

    Returns:
        tuple[absolute_path, logical_path]
    """
    raw = str(artifact_path or "").strip()
    if not raw:
        raise ValueError("artifact_path is required")

    workspace_value = str(workspace or "").strip() or os.getcwd()
    if os.path.isabs(raw):
        absolute = os.path.abspath(raw)
        logical = _logical_path_from_absolute(workspace_value, absolute)
        return absolute, logical

    logical = normalize_logical_rel_path(raw)
    absolute = os.path.abspath(resolve_logical_path(workspace_value, logical))
    return absolute, logical


def normalize_failure_run_id(run_id: str) -> str:
    token = str(run_id or "").strip()
    if not validate_run_id(token):
        raise ValueError("invalid run_id")
    return token


def resolve_failure_hops_output_path(workspace: str, run_id: str) -> str:
    normalized_run_id = normalize_failure_run_id(run_id)
    return resolve_runtime_path(
        workspace,
        f"runtime/artifacts/runs/{normalized_run_id}/failure_hops.json",
    )


def ensure_runtime_scoped_directory(workspace: str, directory: str) -> str:
    raw = str(directory or "").strip()
    if not raw:
        raise ValueError("run_dir is required")
    run_dir = os.path.abspath(raw)
    runtime_root = resolve_storage_roots(workspace).runtime_root
    if not _is_within_path(runtime_root, run_dir):
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {directory}")
    return run_dir
