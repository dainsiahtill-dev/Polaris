"""Workspace filesystem utilities.

This module provides workspace path resolution and validation.
No HTTP semantics — domain exceptions are mapped to HTTP at the delivery boundary.
"""

from __future__ import annotations

import logging
import os

from polaris.cells.policy.workspace_guard.service import ensure_workspace_target_allowed
from polaris.domain.exceptions import NotFoundError, ValidationError
from polaris.kernelone.storage import resolve_workspace_persistent_path

logger = logging.getLogger(__name__)


def workspace_status_path(workspace: str) -> str:
    if not workspace:
        return ""
    return resolve_workspace_persistent_path(workspace, "workspace/meta/workspace_status.json")


def validate_workspace(
    path: str,
    *,
    self_upgrade_mode: bool | None = None,
) -> str:
    """Validate and resolve a workspace path.

    Raises:
        ValidationError: Path is empty or invalid.
        NotFoundError: Resolved path does not exist.
    """
    if not path:
        raise ValidationError("workspace is required", field="path")
    try:
        full = str(
            ensure_workspace_target_allowed(
                path,
                self_upgrade_mode=self_upgrade_mode,
            )
        )
    except ValueError as exc:
        raise ValidationError(str(exc), field="path", cause=exc)
    if not os.path.isdir(full):
        raise NotFoundError(
            resource_type="workspace",
            resource_id=path,
            message=f"workspace path not found: {path}",
        )
    return full


def get_abs_path(workspace: str, path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(workspace, path)


def workspace_has_docs(workspace: str) -> bool:
    if not workspace:
        return False
    if os.path.isdir(os.path.join(workspace, "docs")):
        return True
    try:
        docs_root = resolve_workspace_persistent_path(workspace, "workspace/docs")
        return os.path.isdir(docs_root)
    except (RuntimeError, ValueError) as exc:
        logger.debug("resolve_workspace_persistent_path failed in workspace_has_docs: %s", exc)
        return False


def normalize_rel_path(rel_path: str) -> str:
    raw = (rel_path or "").replace("\\", "/").lstrip("/")
    norm = os.path.normpath(raw).replace("\\", "/")
    return norm
