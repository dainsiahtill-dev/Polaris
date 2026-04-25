"""Application-layer facade for storage layout administration.

This module provides a thin application-layer facade that wraps the
``storage.layout`` Cell's public contracts and KernelOne storage
primitives so that the delivery layer (CLI, HTTP) never needs to
import Cell ``internal/`` modules or low-level ``kernelone`` storage
helpers directly.

Call chain:
    delivery -> application.storage_admin -> cells.storage.layout.public
                                           -> kernelone.storage (contracts)

Public surface exposed here:
    - ``StorageAdminService``: stateless facade that aggregates storage
      layout resolution, global/workspace path queries, cache root
      construction, environment variable resolution, and policy
      introspection into a single cohesive API.
    - ``StorageLayoutSnapshot``: frozen dataclass returned by
      ``resolve_full_layout`` containing everything the runtime
      storage-layout endpoint needs in one shot.
    - ``StorageEnvironment``: frozen dataclass for relevant
      environment variable values.

Architecture constraints (AGENTS.md):
    - This module imports from ``cells.storage.layout.public`` and
      ``kernelone`` contracts ONLY. It NEVER imports from ``internal/``.
    - No business logic lives here; the facade delegates everything.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "StorageAdminError",
    "StorageAdminService",
    "StorageEnvironment",
    "StorageLayoutSnapshot",
    "StoragePolicySummary",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StorageAdminError(RuntimeError):
    """Application-layer error for storage administration operations.

    Wraps lower-level Cell or KernelOne errors so delivery never catches
    infrastructure-specific exception types.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "storage_admin_error",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause


# ---------------------------------------------------------------------------
# Value objects returned to delivery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoragePolicySummary:
    """Summarised view of a single storage policy entry."""

    prefix: str
    category: str
    lifecycle: str
    retention_days: int | None
    compress: bool
    archive_on_terminal: bool


@dataclass(frozen=True, slots=True)
class StorageEnvironment:
    """Relevant KernelOne / Polaris environment variable values."""

    kernelone_home: str
    runtime_root: str
    runtime_cache_root: str
    state_to_ramdisk: str


@dataclass(frozen=True, slots=True)
class StorageLayoutSnapshot:
    """Complete storage layout resolved for a workspace.

    This dataclass carries everything the ``/runtime/storage-layout``
    HTTP endpoint (or an equivalent CLI command) needs, so delivery
    never has to call multiple low-level functions.
    """

    # -- workspace identity -----------------------------------------------
    workspace: str
    workspace_abs: str
    workspace_key: str

    # -- mode flags -------------------------------------------------------
    storage_layout_mode: str
    runtime_mode: str

    # -- root paths -------------------------------------------------------
    ramdisk_root: str
    home_root: str
    global_root: str
    projects_root: str
    project_root: str
    config_root: str
    workspace_persistent_root: str
    project_persistent_root: str
    runtime_base: str
    runtime_root: str
    runtime_project_root: str
    history_root: str

    # -- well-known resolved paths ----------------------------------------
    paths: dict[str, str] = field(default_factory=dict)

    # -- environment ------------------------------------------------------
    env: StorageEnvironment | None = None

    # -- policies ---------------------------------------------------------
    policies: tuple[StoragePolicySummary, ...] = ()

    # -- metadata ---------------------------------------------------------
    migration_version: int = 2


# ---------------------------------------------------------------------------
# StorageAdminService
# ---------------------------------------------------------------------------


class StorageAdminService:
    """Application-layer facade for storage layout operations.

    This is the single entrypoint that delivery should use for all
    storage-path resolution, environment introspection, and policy
    queries. It is stateless and cheap to construct.
    """

    # -- polaris_home -----------------------------------------------------

    @staticmethod
    def get_polaris_home() -> str:
        """Return the Polaris home directory.

        Delegates to ``cells.storage.layout.public.polaris_home`` which
        resolves ``KERNELONE_HOME`` > ``~/.polaris``.

        Raises:
            StorageAdminError: if resolution fails.
        """
        try:
            from polaris.cells.storage.layout.public import polaris_home

            return polaris_home()
        except (ImportError, RuntimeError, ValueError) as exc:
            raise StorageAdminError(
                f"Failed to resolve polaris_home: {exc}",
                code="polaris_home_error",
                cause=exc,
            ) from exc

    # -- environment variable helpers -------------------------------------

    @staticmethod
    def resolve_env(key: str) -> str:
        """Resolve a KernelOne runtime configuration value by key.

        Thin wrapper around ``kernelone._runtime_config.resolve_env_str``
        so delivery does not import private KernelOne modules.

        Args:
            key: The config key, e.g. ``"runtime_root"``,
                ``"runtime_cache_root"``, ``"state_to_ramdisk"``.

        Returns:
            The resolved string value (may be empty if unset).
        """
        try:
            from polaris.kernelone._runtime_config import resolve_env_str

            return resolve_env_str(key)
        except (ImportError, RuntimeError, ValueError) as exc:
            logger.debug("resolve_env(%s) failed: %s", key, exc)
            return ""

    @staticmethod
    def get_storage_environment() -> StorageEnvironment:
        """Build a snapshot of relevant storage environment variables.

        Returns:
            ``StorageEnvironment`` with current values.
        """
        return StorageEnvironment(
            kernelone_home=StorageAdminService.get_polaris_home(),
            runtime_root=StorageAdminService.resolve_env("runtime_root"),
            runtime_cache_root=StorageAdminService.resolve_env("runtime_cache_root"),
            state_to_ramdisk=StorageAdminService.resolve_env("state_to_ramdisk"),
        )

    # -- path resolution --------------------------------------------------

    @staticmethod
    def resolve_global_path(relative_path: str) -> str:
        """Resolve a path relative to the global Polaris config root.

        Args:
            relative_path: e.g. ``"config/settings.json"``.

        Returns:
            Absolute filesystem path.

        Raises:
            StorageAdminError: if resolution fails.
        """
        try:
            from polaris.kernelone.storage import resolve_global_path

            return resolve_global_path(relative_path)
        except (ImportError, RuntimeError, ValueError) as exc:
            raise StorageAdminError(
                f"resolve_global_path({relative_path!r}) failed: {exc}",
                code="global_path_error",
                cause=exc,
            ) from exc

    @staticmethod
    def resolve_workspace_persistent_path(
        workspace: str,
        relative_path: str,
    ) -> str:
        """Resolve a path under the workspace persistent root.

        Args:
            workspace: Workspace directory.
            relative_path: e.g. ``"workspace/brain"``.

        Returns:
            Absolute filesystem path.

        Raises:
            StorageAdminError: if resolution fails.
        """
        try:
            from polaris.kernelone.storage import (
                resolve_workspace_persistent_path,
            )

            return resolve_workspace_persistent_path(workspace, relative_path)
        except (ImportError, RuntimeError, ValueError) as exc:
            raise StorageAdminError(
                f"resolve_workspace_persistent_path({workspace!r}, {relative_path!r}) failed: {exc}",
                code="workspace_path_error",
                cause=exc,
            ) from exc

    @staticmethod
    def build_cache_root(ramdisk_root: str, workspace: str) -> str:
        """Build the cache root for a workspace.

        Args:
            ramdisk_root: Ramdisk root path (may be empty).
            workspace: Workspace directory.

        Returns:
            Resolved cache root path.

        Raises:
            StorageAdminError: if resolution fails.
        """
        try:
            from polaris.kernelone.storage.io_paths import build_cache_root

            return build_cache_root(ramdisk_root, workspace)
        except (ImportError, RuntimeError, ValueError) as exc:
            raise StorageAdminError(
                f"build_cache_root failed: {exc}",
                code="cache_root_error",
                cause=exc,
            ) from exc

    # -- storage roots resolution -----------------------------------------

    @staticmethod
    def resolve_storage_roots(
        workspace: str,
        *,
        ramdisk_root: str | None = None,
    ) -> Any:
        """Resolve storage roots for a workspace.

        Returns the raw ``StorageRoots`` object from KernelOne so that
        callers can access any attribute they need.

        Args:
            workspace: Workspace directory path.
            ramdisk_root: Optional ramdisk root override.

        Raises:
            StorageAdminError: if resolution fails.
        """
        try:
            from polaris.kernelone.storage import resolve_storage_roots

            return resolve_storage_roots(workspace, ramdisk_root=ramdisk_root)
        except (ImportError, RuntimeError, ValueError) as exc:
            raise StorageAdminError(
                f"resolve_storage_roots({workspace!r}) failed: {exc}",
                code="storage_roots_error",
                cause=exc,
            ) from exc

    # -- policy introspection ---------------------------------------------

    @staticmethod
    def list_storage_policies() -> tuple[StoragePolicySummary, ...]:
        """Return summarised storage policies from the KernelOne registry.

        Returns:
            Tuple of ``StoragePolicySummary`` entries.
        """
        try:
            from polaris.kernelone.storage import STORAGE_POLICY_REGISTRY

            seen_prefixes: set[str] = set()
            summaries: list[StoragePolicySummary] = []
            for policy in STORAGE_POLICY_REGISTRY:
                prefix = getattr(policy, "logical_prefix", None)
                if not prefix or prefix in seen_prefixes:
                    continue
                seen_prefixes.add(prefix)
                summaries.append(
                    StoragePolicySummary(
                        prefix=prefix,
                        category=str(
                            getattr(
                                getattr(policy, "category", None),
                                "value",
                                "",
                            )
                        ),
                        lifecycle=str(
                            getattr(
                                getattr(policy, "lifecycle", None),
                                "value",
                                "",
                            )
                        ),
                        retention_days=getattr(policy, "retention_days", None),
                        compress=bool(getattr(policy, "compress", False)),
                        archive_on_terminal=bool(getattr(policy, "archive_on_terminal", False)),
                    )
                )
            return tuple(summaries)
        except (ImportError, RuntimeError, ValueError) as exc:
            logger.warning("list_storage_policies failed: %s", exc)
            return ()

    # -- well-known workspace paths ---------------------------------------

    @staticmethod
    def resolve_well_known_paths(workspace: str) -> dict[str, str]:
        """Resolve the set of well-known workspace paths.

        This is the same set of paths that the ``/runtime/storage-layout``
        endpoint returns under the ``"paths"`` key.

        Args:
            workspace: Workspace directory.

        Returns:
            Dict mapping logical names to absolute paths.
        """
        _resolve_global = StorageAdminService.resolve_global_path
        _resolve_ws = StorageAdminService.resolve_workspace_persistent_path

        return {
            "settings": _resolve_global("config/settings.json"),
            "llm_config": _resolve_global("config/llm/llm_config.json"),
            "llm_test_index": _resolve_global("config/llm/llm_test_index.json"),
            "global_settings": _resolve_global("config/settings.json"),
            "global_llm_config": _resolve_global("config/llm/llm_config.json"),
            "global_llm_test_index": _resolve_global("config/llm/llm_test_index.json"),
            "brain": _resolve_ws(workspace, "workspace/brain"),
            "lancedb": _resolve_ws(workspace, "workspace/lancedb"),
            "verify": _resolve_ws(workspace, "workspace/verify"),
            "policy": _resolve_ws(workspace, "workspace/policy"),
            "meta": _resolve_ws(workspace, "workspace/meta"),
            "history_runs": _resolve_ws(workspace, "workspace/history/runs"),
        }

    # -- full layout snapshot (single-call convenience) -------------------

    @staticmethod
    def resolve_full_layout(
        workspace: str,
        *,
        ramdisk_root: str = "",
    ) -> StorageLayoutSnapshot:
        """Resolve a complete storage layout snapshot for a workspace.

        This is the primary method for the HTTP storage-layout endpoint.
        It aggregates roots, well-known paths, environment, and policies
        into a single ``StorageLayoutSnapshot``.

        Args:
            workspace: Workspace directory.
            ramdisk_root: Ramdisk root override (may be empty).

        Returns:
            ``StorageLayoutSnapshot`` ready for serialisation.

        Raises:
            StorageAdminError: if root resolution fails.
        """
        roots = StorageAdminService.resolve_storage_roots(workspace, ramdisk_root=ramdisk_root or None)
        env = StorageAdminService.get_storage_environment()
        policies = StorageAdminService.list_storage_policies()
        paths = StorageAdminService.resolve_well_known_paths(workspace)

        return StorageLayoutSnapshot(
            workspace=getattr(roots, "workspace_abs", workspace),
            workspace_abs=getattr(roots, "workspace_abs", workspace),
            workspace_key=getattr(roots, "workspace_key", ""),
            storage_layout_mode=getattr(roots, "storage_layout_mode", ""),
            runtime_mode=getattr(roots, "runtime_mode", ""),
            ramdisk_root=ramdisk_root,
            home_root=getattr(roots, "home_root", ""),
            global_root=getattr(roots, "global_root", ""),
            projects_root=getattr(roots, "projects_root", ""),
            project_root=getattr(roots, "project_root", ""),
            config_root=getattr(roots, "config_root", ""),
            workspace_persistent_root=getattr(roots, "workspace_persistent_root", ""),
            project_persistent_root=getattr(roots, "project_persistent_root", ""),
            runtime_base=getattr(roots, "runtime_base", ""),
            runtime_root=getattr(roots, "runtime_root", ""),
            runtime_project_root=getattr(roots, "runtime_project_root", ""),
            history_root=getattr(roots, "history_root", ""),
            paths=paths,
            env=env,
            policies=policies,
            migration_version=2,
        )
