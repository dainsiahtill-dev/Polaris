"""Storage subsystem contracts for KernelOne.

This module defines the stable port surface for storage layout and policy.
Provides path resolution and lifecycle management for all KernelOne storage domains.

Architecture:
    - StorageLayoutPort: logical-to-physical path resolution
    - StoragePolicyPort: lifecycle and retention policy queries
    - StorageLayout is the default in-process implementation

Storage domains (logical prefixes):
    - config/*     → global config (~/.polaris/config)
    - workspace/* → workspace metadata (<workspace>/.polaris/*)
    - runtime/*   → runtime transient (<runtime_root>/...)

Design constraints:
    - KernelOne-only: no Polaris business semantics
    - Paths are resolved through StorageLayout, not hard-coded
    - Explicit UTF-8: all file I/O uses encoding="utf-8"
    - Polaris business layout is in polaris.cells.storage.layout (not here)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

# -----------------------------------------------------------------------------


class StorageCategory(str, Enum):
    """Storage category taxonomy — classifies storage domains by use pattern."""

    GLOBAL_CONFIG = "global_config"  # ~/.polaris/config
    WORKSPACE_PERSISTENT = "workspace_persistent"  # <workspace>/.polaris
    RUNTIME_CURRENT = "runtime_current"  # runtime/* transient
    RUNTIME_RUN = "runtime_run"  # per-run snapshots
    WORKSPACE_HISTORY = "workspace_history"  # archived data
    FACTORY_CURRENT = "factory_current"  # factory run state
    FACTORY_HISTORY = "factory_history"  # factory history


class Lifecycle(str, Enum):
    """Storage lifecycle model — governs retention and disposal."""

    PERMANENT = "permanent"  # Never auto-delete
    ACTIVE = "active"  # Current run/window, overwrite in place
    EPHEMERAL = "ephemeral"  # Short-lived, cleanable after TTL
    HISTORY = "history"  # Read-only archive, compressed


@dataclass(frozen=True)
class StoragePolicy:
    """Immutable storage policy for a logical path prefix."""

    logical_prefix: str
    category: StorageCategory
    lifecycle: Lifecycle
    retention_days: int = -1  # -1 = permanent
    compress: bool = False
    archive_on_terminal: bool = False

    def should_archive(self) -> bool:
        return self.archive_on_terminal

    def should_compress(self) -> bool:
        return self.compress

    def get_retention_days(self) -> int:
        return self.retention_days


@dataclass(frozen=True)
class StorageRoots:
    """Immutable snapshot of all resolved storage root paths."""

    workspace_abs: str
    workspace_key: str
    storage_layout_mode: str
    home_root: str
    global_root: str
    config_root: str
    projects_root: str
    project_root: str
    project_persistent_root: str
    runtime_projects_root: str
    runtime_project_root: str
    workspace_persistent_root: str
    runtime_base: str
    runtime_root: str
    runtime_mode: str
    history_root: str


# -----------------------------------------------------------------------------


class StorageLayoutPort(Protocol):
    """Abstract interface for logical-to-physical storage path resolution.

    Implementations: StorageLayout (in-process).
    """

    def resolve_runtime_path(
        self,
        workspace: str,
        rel_path: str,
        *,
        ramdisk_root: str | None = None,
    ) -> Path:
        """Resolve a runtime/* logical path to an absolute path."""
        ...

    def resolve_workspace_path(
        self,
        workspace: str,
        rel_path: str,
    ) -> Path:
        """Resolve a workspace/* logical path to an absolute path."""
        ...

    def resolve_config_path(
        self,
        rel_path: str,
    ) -> Path:
        """Resolve a config/* logical path to an absolute path."""
        ...

    def resolve_logical_path(
        self,
        workspace: str,
        rel_path: str,
        *,
        ramdisk_root: str | None = None,
    ) -> Path:
        """Resolve any logical path (runtime/workspace/config) to absolute."""
        ...

    def get_storage_roots(
        self,
        workspace: str,
        *,
        ramdisk_root: str | None = None,
    ) -> StorageRoots:
        """Get all resolved storage roots for a workspace."""
        ...

    def get_path(
        self,
        workspace: str,
        category: str,
        *parts: str,
    ) -> Path:
        """Get a path within a category (runtime, workspace, config, etc.)."""
        ...

    def ensure_dir(
        self,
        workspace: str,
        category: str,
        *parts: str,
    ) -> Path:
        """Like get_path, but also creates the directory tree."""
        ...


class StoragePolicyPort(Protocol):
    """Abstract interface for storage policy queries.

    Implementations: StoragePolicyService (in-process).
    """

    def get_policy(self, logical_path: str) -> StoragePolicy:
        """Get the storage policy for a logical path."""
        ...

    def get_category(self, logical_path: str) -> StorageCategory:
        """Get the storage category for a logical path."""
        ...

    def get_lifecycle(self, logical_path: str) -> Lifecycle:
        """Get the lifecycle model for a logical path."""
        ...

    def is_archive_eligible(
        self,
        logical_path: str,
        terminal_status: str,
    ) -> bool:
        """Check if a path should be archived on terminal state."""
        ...

    def resolve_target_path(
        self,
        logical_path: str,
        workspace: str,
        runtime_root: str,
    ) -> str:
        """Resolve a logical path to a physical target for archival."""
        ...


__all__ = [
    "Lifecycle",
    # Types
    "StorageCategory",
    # Ports
    "StorageLayoutPort",
    "StoragePolicy",
    "StoragePolicyPort",
    "StorageRoots",
]
