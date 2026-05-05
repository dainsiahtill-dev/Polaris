"""Polaris-specific storage layout extensions.

This module provides the Polaris business-layer view of storage layout.
It extends the generic KernelOne path primitives with Polaris-specific path policies
and the canonical PolarisStorageLayout class used throughout the application.

All paths in this module reflect Polaris-specific directory conventions
(e.g. .polaris metadata dir, Polaris config root, Polaris cache base).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.storage.layout import (
    StorageLayout as _BaseStorageLayout,
)

_logger = logging.getLogger(__name__)


def _polaris_metadata_dir_name() -> str:
    """Return Polaris's physical workspace metadata directory name.

    KernelOne's generic default is ``.polaris``. Polaris uses the same name.
    """
    configured = str(get_workspace_metadata_dir_name() or "").strip()
    return configured or ".polaris"


# ─── Polaris-specific environment-aware home / cache ────────────────────────


def polaris_home() -> str:
    """Return the Polaris home directory.

    Resolution order: KERNELONE_HOME > APPDATA/.polaris (Windows, if exists) > ~/.polaris (backward compat)

    Must match Electron's resolvepolarisRoot() in config-paths.cjs so that
    both the Python backend and the Electron main process read/write the
    same settings.json file.
    """
    from polaris.kernelone._runtime_config import resolve_env_str

    kern_home = resolve_env_str("home")
    if kern_home:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(kern_home)))

    root_override = str(os.environ.get("KERNELONE_ROOT") or "").strip()
    if root_override:
        root = os.path.abspath(os.path.expanduser(os.path.expandvars(root_override)))
        return os.path.join(root, ".polaris")

    # Match Electron: on Windows, prefer APPDATA
    if os.name == "nt":
        appdata = str(os.environ.get("APPDATA") or "").strip()
        if appdata:
            appdata_home = os.path.abspath(os.path.join(appdata, ".polaris"))
            # Backward compat: if settings exist at legacy ~/.polaris but not at
            # APPDATA/.polaris, keep using legacy path to avoid losing user config.
            legacy_home = os.path.abspath(os.path.expanduser("~/.polaris"))
            legacy_settings = os.path.join(legacy_home, "config", "settings.json")
            appdata_settings = os.path.join(appdata_home, "config", "settings.json")
            if os.path.isfile(legacy_settings) and not os.path.isfile(appdata_settings):
                return legacy_home
            return appdata_home

    return os.path.abspath(os.path.expanduser("~/.polaris"))


def default_polaris_cache_base() -> str:
    """Return the default Polaris system cache base path.

    This is the Polaris-specific counterpart of default_kernelone_cache_base().
    """
    if os.name == "nt":
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            return os.path.abspath(os.path.join(local_app_data, "Polaris", "cache"))
        return os.path.abspath(os.path.expanduser("~\\AppData\\Local\\Polaris\\cache"))
    if sys.platform == "darwin":
        return os.path.abspath(os.path.expanduser("~/Library/Caches/Polaris"))
    xdg = str(os.environ.get("XDG_CACHE_HOME") or "").strip()
    if xdg:
        return os.path.abspath(os.path.join(os.path.expanduser(xdg), "polaris"))
    return os.path.abspath(os.path.expanduser("~/.cache/polaris"))


# ─── Polaris-config-root-aware StorageRoots ────────────────────────────────────────


def resolve_polaris_roots(workspace: str, ramdisk_root: str | None = None) -> PolarisStorageRoots:
    """Resolve storage roots with Polaris-specific config root.

    Returns a PolarisStorageRoots that differs from the generic
    StorageRoots only in that config_root is anchored at <polaris_home()>/config
    rather than <kernelone_home()>/config.
    """
    import hashlib
    import re

    from polaris.kernelone.storage.layout import _runtime_base_and_mode

    workspace_abs = os.path.abspath(os.path.expanduser(workspace or os.getcwd()))
    key_base = os.path.basename(workspace_abs.rstrip("\\/")) or "workspace"
    key_slug = re.sub(r"[^a-z0-9]+", "-", key_base.lower()).strip("-") or "workspace"
    key_digest = hashlib.sha1(workspace_abs.encode("utf-8", errors="ignore")).hexdigest()[:12]
    key = f"{key_slug}-{key_digest}"

    home_root = polaris_home()
    runtime_base, runtime_mode = _runtime_base_and_mode(workspace_abs, ramdisk_root)
    metadata_dir_name = _polaris_metadata_dir_name()
    project_local_root = os.path.join(workspace_abs, metadata_dir_name)

    # Polaris-specific: config is under polaris_home(), not kernelone_home()
    config_root = os.path.join(home_root, "config")

    # Pre-compute runtime paths once to avoid redundant os.path.join calls.
    # history_root is ALWAYS workspace-anchored (never derived from runtime_base)
    # to guarantee it stays on the same drive as the workspace and does not
    # suffer from Windows cross-drive os.path.join silently discarding the
    # workspace path when runtime_base is on a different drive.
    runtime_projects = os.path.join(runtime_base, metadata_dir_name, "projects")
    history_root = os.path.join(workspace_abs, metadata_dir_name, "history")

    return PolarisStorageRoots(
        workspace_abs=workspace_abs,
        workspace_key=key,
        storage_layout_mode="project_local",
        home_root=home_root,
        global_root=home_root,
        config_root=config_root,  # Polaris-specific: uses polaris_home()
        projects_root=project_local_root,
        project_root=project_local_root,
        project_persistent_root=project_local_root,
        runtime_projects_root=runtime_projects,
        runtime_project_root=os.path.join(runtime_projects, key, "runtime"),
        workspace_persistent_root=project_local_root,
        runtime_base=runtime_base,
        runtime_root=os.path.join(runtime_projects, key, "runtime"),
        runtime_mode=runtime_mode,
        history_root=history_root,
    )


# Backward compatibility alias
resolve_polaris_roots = resolve_polaris_roots


# ─── Polaris storage roots dataclass ─────────────────────────────────────────────


class _PolarisStorageRootsImpl:
    """Polaris-specific storage roots.

    Structurally identical to kernelone.storage.layout.StorageRoots but
    config_root is resolved via polaris_home().
    """

    __slots__ = (
        "config_root",
        "global_root",
        "history_root",
        "home_root",
        "project_persistent_root",
        "project_root",
        "projects_root",
        "runtime_base",
        "runtime_mode",
        "runtime_project_root",
        "runtime_projects_root",
        "runtime_root",
        "storage_layout_mode",
        "workspace_abs",
        "workspace_key",
        "workspace_persistent_root",
    )

    def __init__(
        self,
        workspace_abs: str,
        workspace_key: str,
        storage_layout_mode: str,
        home_root: str,
        global_root: str,
        config_root: str,
        projects_root: str,
        project_root: str,
        project_persistent_root: str,
        runtime_projects_root: str,
        runtime_project_root: str,
        workspace_persistent_root: str,
        runtime_base: str,
        runtime_root: str,
        runtime_mode: str,
        history_root: str,
    ) -> None:
        self.workspace_abs = workspace_abs
        self.workspace_key = workspace_key
        self.storage_layout_mode = storage_layout_mode
        self.home_root = home_root
        self.global_root = global_root
        self.config_root = config_root  # Polaris-specific
        self.projects_root = projects_root
        self.project_root = project_root
        self.project_persistent_root = project_persistent_root
        self.runtime_projects_root = runtime_projects_root
        self.runtime_project_root = runtime_project_root
        self.workspace_persistent_root = workspace_persistent_root
        self.runtime_base = runtime_base
        self.runtime_root = runtime_root
        self.runtime_mode = runtime_mode
        self.history_root = history_root

    def __repr__(self) -> str:
        return (
            f"PolarisStorageRoots(workspace_abs={self.workspace_abs!r}, "
            f"config_root={self.config_root!r}, runtime_root={self.runtime_root!r})"
        )


# Type alias for the instance type (used by static type checkers and return types)
PolarisStorageRoots = _PolarisStorageRootsImpl


# ─── PolarisStorageLayout ─────────────────────────────────────────────────


class PolarisStorageLayout(_BaseStorageLayout):
    """Polaris-aware StorageLayout.

    This class extends the generic kernelone.storage.layout.StorageLayout
    with Polaris-specific path conventions:

    - ``config_root`` is anchored at ``<polaris_home()>/config``
      rather than the generic ``<kernelone_home()>/config``.
    - All other paths (workspace, runtime) are resolved identically to
      the base class.

    This is the canonical storage layout class used by Polaris application
    services. Use this instead of the base ``StorageLayout`` in all Polaris
    business-layer code.
    """

    def __init__(self, workspace: Path | str, runtime_base: Path | str) -> None:
        self._workspace = Path(workspace).resolve()
        self._runtime_base = Path(runtime_base).resolve()
        self._key = self._compute_workspace_key(str(self._workspace))
        metadata_dir_name = _polaris_metadata_dir_name()
        self._runtime_root = self._runtime_base / metadata_dir_name / "projects" / self._key / "runtime"
        self._workspace_root = self._workspace / metadata_dir_name
        # Polaris-specific: config root uses polaris_home(), not kernelone_home()
        self._config_root = Path(polaris_home()) / "config"

    @staticmethod
    def _compute_workspace_key(workspace_abs: str) -> str:
        import hashlib
        import re

        base = os.path.basename(workspace_abs.rstrip("\\/")) or "workspace"
        slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "workspace"
        digest = hashlib.sha1(workspace_abs.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{slug}-{digest}"

    @property
    def config_root(self) -> Path:
        return self._config_root

    def __repr__(self) -> str:
        return (
            f"PolarisStorageLayout(workspace={self._workspace!r}, "
            f"config_root={self._config_root!r}, runtime_root={self._runtime_root!r})"
        )

    def resolve_polaris_roots(self, ramdisk_root: str | None = None) -> _PolarisStorageRootsImpl:
        """Return Polaris-specific roots resolved from this layout's workspace."""
        return resolve_polaris_roots(str(self._workspace), ramdisk_root=ramdisk_root)


__all__ = [
    "PolarisStorageLayout",
    "PolarisStorageRoots",
    "default_polaris_cache_base",
    "polaris_home",
    "resolve_polaris_roots",
]
