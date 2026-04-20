"""Storage policy taxonomy for KernelOne storage domains.

This module defines:
- Lifecycle models (permanent, active, ephemeral, history)
- Retention and archive/compression behavior

StorageCategory is the single source of truth in contracts.py.
This module imports it from there to avoid duplication.

All paths are expressed as logical paths (runtime/*, workspace/*, config/*)
and resolved to physical paths via ``polaris.kernelone.storage`` layout APIs.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any

# StorageCategory is defined once in contracts.py (str, Enum version).
# Import it here so existing callers of policy.StorageCategory continue to work.
from polaris.kernelone.constants import (
    STORAGE_RETENTION_FACTORY,
    STORAGE_RETENTION_RUNTIME_CONTROL,
    STORAGE_RETENTION_RUNTIME_STATE,
    STORAGE_RETENTION_RUNTIME_STATUS,
)
from polaris.kernelone.storage.contracts import StorageCategory


class Lifecycle(Enum):
    """Storage lifecycle model."""

    # Data that persists forever
    PERMANENT = "permanent"

    # Data for current active run/window (active overwrite)
    ACTIVE = "active"

    # Short-lived temporary data (can be cleaned)
    EPHEMERAL = "ephemeral"

    # Historical archive data (read-only, compressed)
    HISTORY = "history"


@dataclass(frozen=True)
class StoragePolicy:
    """Storage policy for a logical path prefix.

    Attributes:
        logical_prefix: The logical path prefix (e.g., "runtime/contracts")
        category: Storage category
        lifecycle: Lifecycle model
        retention_days: Days to retain (-1 for permanent)
        compress: Whether to compress during archiving
        archive_on_terminal: Whether to archive when reaching terminal state
    """

    logical_prefix: str
    category: StorageCategory
    lifecycle: Lifecycle
    retention_days: int = -1
    compress: bool = False
    archive_on_terminal: bool = False

    def should_archive(self) -> bool:
        """Check if this policy requires archiving."""
        return self.archive_on_terminal

    def should_compress(self) -> bool:
        """Check if compression should be applied."""
        return self.compress

    def get_retention_days(self) -> int:
        """Get retention period in days."""
        return self.retention_days


# Storage Policy Registry
# Maps logical path prefixes to their policies
# Order matters: more specific prefixes should come first

STORAGE_POLICY_REGISTRY: list[StoragePolicy] = [
    # =========================================================================
    # Global Config (permanent)
    # =========================================================================
    StoragePolicy(
        logical_prefix="config",
        category=StorageCategory.GLOBAL_CONFIG,
        lifecycle=Lifecycle.PERMANENT,
        retention_days=-1,
        compress=False,
        archive_on_terminal=False,
    ),
    # =========================================================================
    # Workspace Persistent (permanent or active)
    # =========================================================================
    StoragePolicy(
        logical_prefix="workspace/docs",
        category=StorageCategory.WORKSPACE_PERSISTENT,
        lifecycle=Lifecycle.PERMANENT,
        retention_days=-1,
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="workspace/brain",
        category=StorageCategory.WORKSPACE_PERSISTENT,
        lifecycle=Lifecycle.PERMANENT,
        retention_days=-1,
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="workspace/policy",
        category=StorageCategory.WORKSPACE_PERSISTENT,
        lifecycle=Lifecycle.PERMANENT,
        retention_days=-1,
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="workspace/meta",
        category=StorageCategory.WORKSPACE_PERSISTENT,
        lifecycle=Lifecycle.PERMANENT,
        retention_days=-1,
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="workspace/factory",
        category=StorageCategory.FACTORY_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=STORAGE_RETENTION_FACTORY,  # Keep last 200 factory runs
        compress=False,
        archive_on_terminal=True,  # Archive to history when terminal
    ),
    # =========================================================================
    # Workspace History (permanent)
    # =========================================================================
    StoragePolicy(
        logical_prefix="workspace/history",
        category=StorageCategory.WORKSPACE_HISTORY,
        lifecycle=Lifecycle.HISTORY,
        retention_days=-1,
        compress=True,
        archive_on_terminal=False,
    ),
    # =========================================================================
    # Runtime Current (active/ephemeral)
    # =========================================================================
    StoragePolicy(
        logical_prefix="runtime/contracts",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,  # Keep current run contracts
        compress=False,
        archive_on_terminal=True,
    ),
    StoragePolicy(
        logical_prefix="runtime/tasks",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,  # Keep current tasks as source of truth
        compress=False,
        archive_on_terminal=True,  # Archive snapshots to history
    ),
    StoragePolicy(
        logical_prefix="runtime/results",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,
        compress=False,
        archive_on_terminal=True,
    ),
    StoragePolicy(
        logical_prefix="runtime/sessions",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,  # Keep current sessions
        compress=False,
        archive_on_terminal=True,
    ),
    StoragePolicy(
        logical_prefix="runtime/state",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.EPHEMERAL,
        retention_days=STORAGE_RETENTION_RUNTIME_STATE,  # Keep 7 days of state
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="runtime/status",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.EPHEMERAL,
        retention_days=STORAGE_RETENTION_RUNTIME_STATUS,  # Keep 1 day of status
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="runtime/control",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.EPHEMERAL,
        retention_days=STORAGE_RETENTION_RUNTIME_CONTROL,  # Clean up immediately after run
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="runtime/events",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,
        compress=True,  # Compress when archiving
        archive_on_terminal=True,
    ),
    StoragePolicy(
        logical_prefix="runtime/logs",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.EPHEMERAL,
        retention_days=7,
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="runtime/memory",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="runtime/memos",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,
        compress=False,
        archive_on_terminal=False,
    ),
    StoragePolicy(
        logical_prefix="runtime/evidence",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,
        compress=False,
        archive_on_terminal=True,
    ),
    StoragePolicy(
        logical_prefix="runtime/snapshots",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,
        compress=False,
        archive_on_terminal=False,
    ),
    # =========================================================================
    # Runtime Run-Scoped (temporary, source for archiving)
    # =========================================================================
    StoragePolicy(
        logical_prefix="runtime/runs",
        category=StorageCategory.RUNTIME_RUN,
        lifecycle=Lifecycle.ACTIVE,
        retention_days=-1,  # Keep current run directory
        compress=False,
        archive_on_terminal=True,  # Archive entire run directory
    ),
    # =========================================================================
    # Fallback for unknown prefixes (should not happen)
    # =========================================================================
    StoragePolicy(
        logical_prefix="",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=Lifecycle.EPHEMERAL,
        retention_days=1,
        compress=False,
        archive_on_terminal=False,
    ),
]


def get_policy_for_path(logical_path: str) -> StoragePolicy:
    """Get the storage policy for a logical path.

    Args:
        logical_path: A logical path like "runtime/contracts/plan.md"

    Returns:
        The matching StoragePolicy (always matches due to fallback)
    """
    # Normalize path
    normalized = logical_path.replace("\\", "/").strip("/")

    # Find most specific matching policy
    best_match: StoragePolicy | None = None
    best_prefix_len = 0

    for policy in STORAGE_POLICY_REGISTRY:
        prefix = policy.logical_prefix
        if not prefix:
            # Empty prefix is fallback, only use if no other match
            if best_match is None:
                best_match = policy
            continue

        if normalized == prefix or (normalized.startswith(prefix + "/") and len(prefix) > best_prefix_len):
            best_match = policy
            best_prefix_len = len(prefix)

    if best_match is None:
        # Should not happen, but return fallback
        return STORAGE_POLICY_REGISTRY[-1]

    return best_match


def is_archive_eligible(logical_path: str, status: str) -> bool:
    """Check if a path is eligible for archiving given the run status.

    Args:
        logical_path: The logical path
        status: The terminal status (completed, failed, cancelled, blocked, timeout)

    Returns:
        True if should be archived
    """
    policy = get_policy_for_path(logical_path)
    terminal_statuses = {"completed", "failed", "cancelled", "blocked", "timeout"}
    return policy.should_archive() and status.lower() in terminal_statuses


def get_category_for_path(logical_path: str) -> StorageCategory:
    """Get the storage category for a logical path."""
    return get_policy_for_path(logical_path).category


def get_lifecycle_for_path(logical_path: str) -> Lifecycle:
    """Get the lifecycle for a logical path."""
    return get_policy_for_path(logical_path).lifecycle


# ============================================================================
# Polaris Artifact Lifecycle Policy Metadata
# ============================================================================
# DEPRECATED: Polaris-specific business artifact lifecycle metadata has been
# migrated to the Cell layer. This section now contains a minimal stub that
# emits DeprecationWarning for any remaining callers.
#
# Migration path:
#   from polaris.cells.audit.verdict.internal.artifact_service import (
#       POLARIS_ARTIFACT_POLICY_METADATA,
#       get_artifact_policy_metadata,
#       should_compress_artifact,
#       should_archive_artifact,
#   )
#
# This stub returns None / False to avoid crashing stray callers during the
# migration window. It does NOT serve as an authoritative registry.

# Empty stub dict - backward compatibility only. Emit deprecation on access.
ARTIFACT_POLICY_METADATA: dict[str, dict[str, Any]] = {}  # type: ignore[misc]


def get_artifact_policy_metadata(artifact_key: str) -> dict[str, Any] | None:
    """Get policy metadata for an artifact key.

    .. deprecated::
        Polaris artifact lifecycle metadata has moved to
        ``polaris.cells.audit.verdict.internal.artifact_service``.
        Import ``get_artifact_policy_metadata`` from there instead.
    """
    warnings.warn(
        "polaris.kernelone.storage.policy.ARTIFACT_POLICY_METADATA and "
        "get_artifact_policy_metadata() are deprecated. "
        "Import from polaris.cells.audit.verdict.internal.artifact_service instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return None


def should_compress_artifact(artifact_key: str) -> bool:
    """Check if an artifact should be compressed when archiving.

    .. deprecated::
        Polaris artifact lifecycle metadata has moved to
        ``polaris.cells.audit.verdict.internal.artifact_service``.
        Import ``should_compress_artifact`` from there instead.
    """
    warnings.warn(
        "polaris.kernelone.storage.policy.should_compress_artifact() is deprecated. "
        "Import from polaris.cells.audit.verdict.internal.artifact_service instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return False


def should_archive_artifact(artifact_key: str) -> bool:
    """Check if an artifact should be archived on terminal state.

    .. deprecated::
        Polaris artifact lifecycle metadata has moved to
        ``polaris.cells.audit.verdict.internal.artifact_service``.
        Import ``should_archive_artifact`` from there instead.
    """
    warnings.warn(
        "polaris.kernelone.storage.policy.should_archive_artifact() is deprecated. "
        "Import from polaris.cells.audit.verdict.internal.artifact_service instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return False


class StoragePolicyService:
    """Service for querying storage policies and resolving paths.

    This class provides a higher-level interface for storage policy operations,
    including path resolution and archive eligibility checking.
    """

    def __init__(self, workspace: str) -> None:
        """Initialize the service with a workspace path.

        Args:
            workspace: The workspace root path
        """
        import os

        self.workspace = os.path.abspath(workspace)

    def get_policy(self, logical_path: str) -> StoragePolicy:
        """Get storage policy for a logical path.

        Args:
            logical_path: A logical path like "runtime/contracts"

        Returns:
            The matching StoragePolicy
        """
        return get_policy_for_path(logical_path)

    def resolve_target_path(self, logical_path: str, workspace: str, runtime_root: str) -> str:
        """Resolve a logical path to a physical target path.

        Args:
            logical_path: The logical path (e.g., "runtime/contracts")
            workspace: The workspace root
            runtime_root: The runtime root path

        Returns:
            The resolved physical path
        """
        import os

        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(workspace)

        normalized = logical_path.replace("\\", "/").strip("/")

        if normalized.startswith("runtime/"):
            suffix = normalized[len("runtime/") :]
            return os.path.join(runtime_root, suffix) if suffix else runtime_root
        elif normalized.startswith("workspace/"):
            suffix = normalized[len("workspace/") :]
            return os.path.join(roots.workspace_persistent_root, suffix) if suffix else roots.workspace_persistent_root
        elif normalized == "config":
            return roots.config_root
        else:
            # Default to runtime root
            return runtime_root

    def should_archive(self, logical_path: str, terminal_status: str) -> bool:
        """Check if a path should be archived given the terminal status.

        Args:
            logical_path: The logical path
            terminal_status: Terminal status (completed, failed, cancelled, blocked, timeout)

        Returns:
            True if should be archived
        """
        return is_archive_eligible(logical_path, terminal_status)


__all__ = [
    "ARTIFACT_POLICY_METADATA",
    "STORAGE_POLICY_REGISTRY",
    "Lifecycle",
    "StorageCategory",
    "StoragePolicy",
    "StoragePolicyService",
    "get_artifact_policy_metadata",
    "get_category_for_path",
    "get_lifecycle_for_path",
    "get_policy_for_path",
    "is_archive_eligible",
    "should_archive_artifact",
    "should_compress_artifact",
]
