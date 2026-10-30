"""Artifact path resolution utilities.

This module provides path normalization and resolution for artifact storage.
No HTTP semantics — callers map domain exceptions to HTTP at the delivery boundary.
"""

from __future__ import annotations

import os

from polaris.domain.exceptions import ValidationError
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.storage.io_paths import (
    normalize_artifact_rel_path as _kernelone_normalize_artifact_rel_path,
    resolve_artifact_path as _kernelone_resolve_artifact_path,
)


def normalize_artifact_rel_path(rel_path: str) -> str:
    return _kernelone_normalize_artifact_rel_path(rel_path)


def _strip_artifact_root_prefix(rel_path: str) -> str:
    p = normalize_artifact_rel_path(rel_path)
    if p.startswith("runtime/"):
        return p[len("runtime/") :]
    if p.startswith("workspace/"):
        return p[len("workspace/") :]
    if p.startswith("config/"):
        return p[len("config/") :]
    return p


def _artifact_base_dir(workspace_full: str, cache_root_full: str) -> str:
    if cache_root_full:
        return cache_root_full
    roots = resolve_storage_roots(workspace_full)
    return roots.runtime_root


def _cache_join(cache_root_full: str, rel_path: str) -> str:
    if not cache_root_full:
        return ""
    normalized = normalize_artifact_rel_path(rel_path)
    if normalized == "runtime":
        return cache_root_full
    if normalized.startswith("runtime/"):
        return os.path.join(cache_root_full, normalized[len("runtime/") :])
    return ""


def _cache_join_double(cache_root_full: str, rel_path: str) -> str:
    del cache_root_full, rel_path
    return ""


def is_hot_artifact_path(rel_path: str) -> bool:
    p = normalize_artifact_rel_path(rel_path)
    return p == "runtime" or p.startswith("runtime/")


def _resolve_absolute_if_allowed(workspace_full: str, rel_path: str) -> str:
    return _kernelone_resolve_artifact_path(workspace_full, "", rel_path)


def resolve_artifact_path(workspace_full: str, cache_root_full: str, rel_path: str) -> str:
    try:
        return os.path.abspath(_kernelone_resolve_artifact_path(workspace_full, cache_root_full, rel_path))
    except ValueError:
        return ""


def resolve_safe_path(workspace_full: str, cache_root_full: str, rel_path: str) -> str:
    """Resolve and validate an artifact path.

    Raises:
        ValidationError: Path prefix not supported or path is empty.
    """
    try:
        full = resolve_artifact_path(workspace_full, cache_root_full, rel_path)
    except ValueError as err:
        raise ValidationError(
            "path prefix is not supported",
            field="rel_path",
            value=rel_path,
        ) from err
    if not full:
        raise ValidationError("path is required", field="rel_path")
    return full


def select_latest_artifact(workspace: str, cache_root: str, rel_path: str) -> str:
    try:
        path = resolve_artifact_path(workspace, cache_root, rel_path)
    except (RuntimeError, ValueError):
        return ""
    if path and os.path.isfile(path):
        return path
    return ""
