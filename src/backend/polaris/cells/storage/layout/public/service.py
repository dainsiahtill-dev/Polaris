"""Public service exports for `storage.layout` cell."""

from __future__ import annotations

import logging

from polaris.cells.storage.layout.internal.layout_business import (
    PolarisStorageLayout,
    PolarisStorageRoots,
    default_polaris_cache_base,
    polaris_home,
    resolve_polaris_roots,
)
from polaris.cells.storage.layout.internal.settings_utils import (
    get_polaris_root,
    get_settings_path,
    load_persisted_settings,
    save_persisted_settings,
    sync_process_settings_environment,
)
from polaris.cells.storage.layout.public.contracts import (
    RefreshStorageLayoutCommandV1,
    ResolveRuntimePathQueryV1,
    ResolveStorageLayoutQueryV1,
    ResolveWorkspacePathQueryV1,
    StorageLayoutError,
    StorageLayoutErrorV1,
    StorageLayoutResolvedEventV1,
    StorageLayoutResultV1,
)

_logger = logging.getLogger(__name__)

__all__ = [
    "PolarisStorageLayout",
    "PolarisStorageRoots",
    "PolarisStorageLayout",
    "PolarisStorageRoots",
    "RefreshStorageLayoutCommandV1",
    "ResolveRuntimePathQueryV1",
    "ResolveStorageLayoutQueryV1",
    "ResolveWorkspacePathQueryV1",
    "StorageLayoutError",
    "StorageLayoutErrorV1",
    "StorageLayoutResolvedEventV1",
    "StorageLayoutResultV1",
    "default_polaris_cache_base",
    "default_polaris_cache_base",
    "get_polaris_root",
    "get_settings_path",
    "polaris_home",
    "load_persisted_settings",
    "polaris_home",
    "refresh_storage_layout",
    "resolve_polaris_roots",
    "resolve_polaris_roots",
    "resolve_storage_layout",
    "save_persisted_settings",
    "sync_process_settings_environment",
]

# Backward compatibility imports
PolarisStorageLayout = PolarisStorageLayout
PolarisStorageRoots = PolarisStorageRoots
default_polaris_cache_base = default_polaris_cache_base
polaris_home = polaris_home
resolve_polaris_roots = resolve_polaris_roots


# ─── Path compliance audit logging ─────────────────────────────────────────────
# Structured audit events for all path resolution decisions.
# Each call to resolve_storage_layout emits an audit record so that security
# reviews and regression tests can replay the decision chain.
# ────────────────────────────────────────────────────────────────────────────────


def _log_storage_layout_audit(
    workspace: str,
    mode: str,
    runtime_root: str,
    config_root: str,
    history_root: str,
    workspace_key: str,
) -> None:
    """Emit structured audit log for a storage layout resolution.

    This is the canonical audit trail for all path decisions made by this cell.
    It is emitted at INFO level so that normal operation produces observable
    evidence without requiring debug logging.
    """
    _logger.info(
        "[storage.layout] path_compliance_audit: "
        "workspace=%s mode=%s workspace_key=%s "
        "runtime_root=%s config_root=%s history_root=%s",
        workspace,
        mode,
        workspace_key,
        runtime_root,
        config_root,
        history_root,
    )


# ─── Public query / command handlers ───────────────────────────────────────────


def _invalidate_cache() -> None:
    """Clear all storage-roots caches (KernelOne + ramdisk cache).

    This is the single place of control for cache invalidation, used by
    ``refresh_storage_layout`` when the ``force`` flag is set.
    """
    from polaris.kernelone.storage.layout import clear_storage_roots_cache

    clear_storage_roots_cache()


def refresh_storage_layout(command: RefreshStorageLayoutCommandV1) -> StorageLayoutResultV1:
    """Refresh the storage layout for *command.workspace*.

    This is the canonical handler for ``RefreshStorageLayoutCommandV1``.
    When ``command.force`` is ``True`` it evicts the in-process cache entry
    before re-resolving, guaranteeing fresh filesystem probes on every call.
    When ``command.force`` is ``False`` the call is equivalent to
    ``resolve_storage_layout`` — no cache is touched.

    Parameters
    ----------
    command:
        ``RefreshStorageLayoutCommandV1`` containing the workspace path and
        the ``force`` flag.

    Returns
    -------
    ``StorageLayoutResultV1`` — freshly resolved layout.

    Raises
    ------
    StorageLayoutErrorV1
        If the workspace path is empty or resolution fails.
    """
    workspace = command.workspace.strip()
    if not workspace:
        raise StorageLayoutErrorV1(
            "refresh_storage_layout failed: workspace is empty",
            code="empty_workspace",
        )

    if command.force:
        _invalidate_cache()
        _logger.debug("[storage.layout] storage roots cache invalidated for workspace=%s", workspace)

    # Delegate to the query handler (both force and non-force paths converge here)
    return resolve_storage_layout(ResolveStorageLayoutQueryV1(workspace=workspace))


def resolve_storage_layout(query: ResolveStorageLayoutQueryV1) -> StorageLayoutResultV1:
    """Resolve the full storage layout for *query.workspace*.

    This is the canonical handler for ``ResolveStorageLayoutQueryV1``.
    It delegates to the KernelOne storage roots resolver (which uses
    Polaris-specific config_root anchoring via ``polaris_home()``),
    then emits a structured path-compliance audit log before returning.

    Parameters
    ----------
    query:
        ``ResolveStorageLayoutQueryV1`` containing the workspace path.

    Returns
    -------
    ``StorageLayoutResultV1`` populated with all storage root paths.

    Raises
    ------
    StorageLayoutErrorV1
        If the workspace path is empty or resolution fails.
    """
    workspace = query.workspace.strip()
    if not workspace:
        raise StorageLayoutErrorV1(
            "resolve_storage_layout failed: workspace is empty",
            code="empty_workspace",
        )

    try:
        roots = resolve_polaris_roots(workspace)
    except (RuntimeError, ValueError) as exc:
        raise StorageLayoutErrorV1(
            f"resolve_storage_layout failed for workspace={workspace!r}: {exc}",
            code="resolution_error",
            details={"workspace": workspace, "inner": str(exc)},
        ) from exc

    _log_storage_layout_audit(
        workspace=workspace,
        mode=roots.storage_layout_mode,
        runtime_root=roots.runtime_root,
        config_root=roots.config_root,
        history_root=roots.history_root,
        workspace_key=roots.workspace_key,
    )

    return StorageLayoutResultV1(
        workspace=workspace,
        runtime_root=roots.runtime_root,
        history_root=roots.history_root,
        meta_root=roots.project_root,
        extras={
            "config_root": roots.config_root,
            "workspace_key": roots.workspace_key,
            "runtime_mode": roots.runtime_mode,
            "runtime_base": roots.runtime_base,
            "workspace_persistent_root": roots.workspace_persistent_root,
        },
    )
