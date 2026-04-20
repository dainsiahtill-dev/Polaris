"""Unified storage layout for Polaris KernelOne.

Single source of truth for all runtime path taxonomy:
- Global persistent data: `<~/.polaris>` (e.g. ~/.polaris/config)
- Workspace persistent data: `<workspace>/.polaris/*`
- Runtime hot data: `<ramdisk>/.polaris/projects/<workspace_key>/runtime/*`

Three-Layer Structure:
- RamDisk:   <ramdisk_path>/.polaris/  (volatile runtime artifacts)
- Workspace: <workspace>/.polaris/      (persistent workspace artifacts)
- Global:    ~/.polaris/               (global configuration)

The workspace metadata directory name is a logical prefix injected by the bootstrap
layer. KernelOne default is '.polaris'; the Polaris
bootstrap confirms '.polaris' via set_workspace_metadata_dir_name().

Polaris-specific path conventions (e.g. .polaris metadata dir, Polaris config root)
are provided by ``PolarisStorageLayout`` in the ``storage.layout`` cell.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from polaris.kernelone._runtime_config import (
    get_workspace_metadata_dir_default,
    get_workspace_metadata_dir_name,
    resolve_env_bool,
    resolve_env_str,
)


class StorageLayer(Enum):
    """The three canonical storage layers for Polaris artifacts."""

    RAMDISK = "ramdisk"  # Volatile runtime artifacts (<ramdisk>/.polaris/)
    WORKSPACE = "workspace"  # Persistent workspace artifacts (<workspace>/.polaris/)
    GLOBAL = "global"  # Global configuration (~/.polaris/)


@dataclass(frozen=True)
class ResolvedPath:
    """A fully resolved path with layer classification.

    Attributes:
        absolute: Absolute physical path
        layer: Which canonical storage layer this belongs to
        logical: Original logical path (e.g., "runtime/contracts")
        relative: Path relative to the layer root
    """

    absolute: str
    layer: StorageLayer
    logical: str
    relative: str


class PathResolver:
    """Unified 3-layer path resolution API.

    This is the single entry point for all path resolution in Polaris.
    All cells should use this instead of direct path construction.

    Usage::

        resolver = PathResolver(workspace="/path/to/workspace")
        runtime_path = resolver.resolve_runtime("events/audit.jsonl")
        workspace_path = resolver.resolve_workspace("meta/context_catalog/")
        config_path = resolver.resolve_config("llm/providers.yaml")
    """

    def __init__(
        self,
        workspace: str,
        *,
        ramdisk_root: str | None = None,
    ) -> None:
        """Initialize resolver for a workspace.

        Args:
            workspace: Absolute workspace path
            ramdisk_root: Optional explicit ramdisk root (overrides env/config)
        """
        self._workspace = os.path.abspath(os.path.expanduser(workspace))
        self._ramdisk_root = ramdisk_root
        self._roots = resolve_storage_roots(self._workspace, ramdisk_root=ramdisk_root)

    @property
    def ramdisk_root(self) -> str:
        """Ramdisk layer root: <ramdisk_path>/.polaris/projects/<workspace_key>/runtime"""
        return self._roots.runtime_project_root

    @property
    def workspace_root(self) -> str:
        """Workspace layer root: <workspace>/.polaris"""
        return self._roots.project_persistent_root

    @property
    def global_root(self) -> str:
        """Global layer root: ~/.polaris"""
        return self._roots.global_root

    def resolve(self, logical_path: str) -> ResolvedPath:
        """Resolve any logical path to a fully classified ResolvedPath.

        Args:
            logical_path: Logical path like "runtime/contracts", "workspace/meta", "config/llm"

        Returns:
            ResolvedPath with absolute path, layer, and relative components

        Raises:
            ValueError: If logical_path has unsupported prefix
        """
        normalized = normalize_logical_rel_path(logical_path)

        if normalized.startswith("runtime/"):
            layer = StorageLayer.RAMDISK
            suffix = normalized[len("runtime/") :]
            base = self.ramdisk_root
        elif normalized.startswith("workspace/"):
            layer = StorageLayer.WORKSPACE
            suffix = normalized[len("workspace/") :]
            base = self.workspace_root
        elif normalized.startswith("config/"):
            layer = StorageLayer.GLOBAL
            suffix = normalized[len("config/") :]
            base = self.global_root
        else:
            raise ValueError(f"UNSUPPORTED_PATH_PREFIX: {logical_path}")

        absolute = _join_under(base, suffix) if suffix else base
        return ResolvedPath(
            absolute=os.path.abspath(absolute),
            layer=layer,
            logical=logical_path,
            relative=suffix,
        )

    def resolve_runtime(self, rel_path: str) -> str:
        """Resolve a runtime/* path to ramdisk layer."""
        return self.resolve(f"runtime/{rel_path}").absolute

    def resolve_workspace(self, rel_path: str) -> str:
        """Resolve a workspace/* path to workspace layer."""
        return self.resolve(f"workspace/{rel_path}").absolute

    def resolve_config(self, rel_path: str) -> str:
        """Resolve a config/* path to global layer."""
        return self.resolve(f"config/{rel_path}").absolute

    def get_layer(self, logical_path: str) -> StorageLayer:
        """Get the storage layer for a logical path without full resolution."""
        normalized = normalize_logical_rel_path(logical_path)
        if normalized.startswith("runtime/"):
            return StorageLayer.RAMDISK
        elif normalized.startswith("workspace/"):
            return StorageLayer.WORKSPACE
        elif normalized.startswith("config/"):
            return StorageLayer.GLOBAL
        raise ValueError(f"UNSUPPORTED_PATH_PREFIX: {logical_path}")

    def is_volatile(self, logical_path: str) -> bool:
        """Check if path resides in volatile (ramdisk) layer."""
        return self.get_layer(logical_path) == StorageLayer.RAMDISK


_logger = logging.getLogger(__name__)

UNSUPPORTED_PATH_PREFIX = "UNSUPPORTED_PATH_PREFIX"
_ALLOWED_PREFIXES = ("runtime", "workspace", "config")
_LEGACY_LOGICAL_PREFIX_ALIASES = {
    "docs": "workspace/docs",
    "tasks": "runtime/tasks",
    "dispatch": "runtime/dispatch",
}
_STATE_TO_RAMDISK_KEY = "state_to_ramdisk"

# ---------------------------------------------------------------------------
# Business-layer root resolver registration
# ---------------------------------------------------------------------------
# KernelOne cannot import Cell internal modules directly. Instead, the
# Polaris bootstrap layer registers a resolver callable here via
# ``register_business_roots_resolver()``. This callable is invoked when the
# workspace metadata dir is set to a product-specific name (e.g. ".polaris")
# so that config_root is anchored to the product home rather than kernelone_home.
#
# If no resolver is registered, resolve_storage_roots() falls back to the
# generic KernelOne root resolution.
# ---------------------------------------------------------------------------

# Callable signature: (workspace_abs: str, ramdisk_root: Optional[str]) -> StorageRoots | None
# Returning None signals "I don't handle this workspace, use generic resolution".
_BusinessRootsResolverT = Callable[[str, str | None], Optional["StorageRoots"]]
_business_roots_resolver: _BusinessRootsResolverT | None = None
_business_roots_resolver_lock = threading.Lock()


def register_business_roots_resolver(
    resolver: _BusinessRootsResolverT,
) -> None:
    """Register a business-layer storage roots resolver.

    Called by the Polaris bootstrap to inject product-specific path
    resolution (e.g. anchoring config_root at polaris_home() rather
    than kernelone_home()).

    The resolver must be a callable with signature:
        (workspace_abs: str, ramdisk_root: Optional[str]) -> StorageRoots | None

    Returning ``None`` from the resolver means "fall through to generic resolution".
    Only one resolver may be registered at a time; a second call replaces the first.
    """
    global _business_roots_resolver
    with _business_roots_resolver_lock:
        _business_roots_resolver = resolver


def clear_business_roots_resolver() -> None:
    """Unregister any previously registered business roots resolver. Useful for testing."""
    global _business_roots_resolver
    with _business_roots_resolver_lock:
        _business_roots_resolver = None


# Storage roots cache: cache key -> (StorageRoots, timestamp)
# This prevents repeated filesystem probing which can block on network drives.
_storage_roots_cache: dict[tuple[str, ...], tuple[StorageRoots, float]] = {}
_storage_roots_cache_ttl_seconds = 60.0
_storage_roots_cache_max_size = 64
_storage_roots_cache_lock = threading.Lock()

# Ramdisk check cache to avoid spawning threads repeatedly
_ramdisk_check_cache: dict[str, tuple[bool, float]] = {}
_ramdisk_check_cache_ttl_seconds = 60.0
_ramdisk_check_lock = threading.Lock()


def _storage_roots_cache_key(workspace: str, ramdisk_root: str | None = None) -> tuple[str, ...]:
    return (
        workspace,
        ramdisk_root or "",
        resolve_env_str("home"),
        resolve_env_str("runtime_root"),
        resolve_env_str("state_to_ramdisk"),
        resolve_env_str("ramdisk_root"),
        resolve_env_str("runtime_cache_root"),
        str(os.environ.get("LOCALAPPDATA") or ""),
        str(os.environ.get("XDG_CACHE_HOME") or ""),
        get_workspace_metadata_dir_name(),
    )


def _get_cached_storage_roots(workspace: str, ramdisk_root: str | None = None) -> StorageRoots | None:
    """Get cached storage roots if available and not expired."""
    cache_key = _storage_roots_cache_key(workspace, ramdisk_root)
    with _storage_roots_cache_lock:
        if cache_key in _storage_roots_cache:
            roots, timestamp = _storage_roots_cache[cache_key]
            if time.monotonic() - timestamp < _storage_roots_cache_ttl_seconds:
                _storage_roots_cache[cache_key] = (roots, time.monotonic())
                return roots
            del _storage_roots_cache[cache_key]
    return None


def _set_cached_storage_roots(workspace: str, ramdisk_root: str | None, roots: StorageRoots) -> None:
    """Cache storage roots with current timestamp.

    Enforces max cache size with LRU eviction when at capacity.
    """
    cache_key = _storage_roots_cache_key(workspace, ramdisk_root)
    with _storage_roots_cache_lock:
        if cache_key in _storage_roots_cache:
            _storage_roots_cache[cache_key] = (roots, time.monotonic())
            return
        if len(_storage_roots_cache) >= _storage_roots_cache_max_size:
            _evict_expired_or_oldest_storage_roots()
        _storage_roots_cache[cache_key] = (roots, time.monotonic())


def _evict_expired_or_oldest_storage_roots() -> None:
    """Evict expired entries, or oldest if none expired."""
    now = time.monotonic()
    expired_keys: list[tuple[str, ...]] = []
    for key, (_, timestamp) in _storage_roots_cache.items():
        if now - timestamp >= _storage_roots_cache_ttl_seconds:
            expired_keys.append(key)
    if expired_keys:
        for key in expired_keys:
            del _storage_roots_cache[key]
    else:
        oldest_key = min(_storage_roots_cache, key=lambda k: _storage_roots_cache[k][1])
        del _storage_roots_cache[oldest_key]


def clear_storage_roots_cache() -> None:
    """Clear the storage roots cache. Useful for testing."""
    with _storage_roots_cache_lock:
        _storage_roots_cache.clear()
    with _ramdisk_check_lock:
        _ramdisk_check_cache.clear()


@dataclass(frozen=True)
class StorageRoots:
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


def _truthy_env(value: str, default: bool = True) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def state_to_ramdisk_enabled() -> bool:
    return resolve_env_bool(_STATE_TO_RAMDISK_KEY)


def normalize_ramdisk_root(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    raw = os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(raw):
        return ""
    if re.match(r"^[A-Za-z]:$", raw):
        raw = raw + "\\"
    raw = os.path.abspath(raw).rstrip("\\/")
    if re.match(r"^[A-Za-z]:$", raw):
        raw = raw + "\\"
    return raw


def default_ramdisk_root() -> str:
    """Get default ramdisk root with caching to avoid repeated thread spawning."""
    if os.name != "nt":
        return ""

    drive = "X:\\"
    now = time.monotonic()

    # Check cache first
    with _ramdisk_check_lock:
        if drive in _ramdisk_check_cache:
            cached_result, timestamp = _ramdisk_check_cache[drive]
            if now - timestamp < _ramdisk_check_cache_ttl_seconds:
                return drive if cached_result else ""

    # Perform the check with timeout
    drive_path = _check_drive_exists_with_timeout(drive, timeout=1.0)

    # Cache the result
    with _ramdisk_check_lock:
        _ramdisk_check_cache[drive] = (bool(drive_path), now)

    return drive_path


def _check_drive_exists_with_timeout(path: str, timeout: float = 1.0) -> str:
    """Check if a path exists with a timeout to avoid blocking.

    This function uses a background thread to check path existence.
    The result is cached to avoid spawning threads repeatedly.

    Note: The thread is only spawned when the cache misses, which is
    controlled by _ramdisk_check_cache_ttl_seconds.
    """
    result = ""

    def check() -> None:
        nonlocal result
        try:
            if os.path.exists(path):
                result = path
        except (OSError, ValueError):
            result = ""

    thread = threading.Thread(target=check)
    thread.daemon = True
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        return ""

    return result


def resolve_ramdisk_root(cli_value: str | None = None) -> str:
    if cli_value is not None and str(cli_value).strip():
        return normalize_ramdisk_root(str(cli_value))
    env_value = resolve_env_str("ramdisk_root")
    if env_value:
        return normalize_ramdisk_root(env_value)
    return normalize_ramdisk_root(default_ramdisk_root())


def kernelone_home() -> str:
    """Return the KernelOne global home directory.

    Priority: KERNELONE_HOME > POLARIS_HOME > ~/.polaris

    This is the product-agnostic counterpart. Polaris-specific callers
    should use ``PolarisStorageLayout`` or ``polaris_home()`` from
    ``polaris.cells.storage.layout``.
    """
    raw = resolve_env_str("home")
    if raw:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(raw)))
    # Default fallback: ~/.polaris
    default_home = os.path.expanduser("~/.polaris")
    return os.path.abspath(os.path.expanduser(default_home))


# NOTE: The backward-compat alias `polaris_home = kernelone_home` has been removed.
# For Polaris-specific home paths, use polaris_home() from
# polaris.cells.storage.layout.internal.layout_business instead.


def default_kernelone_cache_base() -> str:
    """Get the default KernelOne system cache base path.

    This is the product-agnostic counterpart.
    Polaris-specific callers should use ``default_polaris_cache_base()``
    from ``polaris.cells.storage.layout``.
    """
    if os.name == "nt":
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            return os.path.abspath(os.path.join(local_app_data, "KernelOne", "cache"))
        return os.path.abspath(os.path.expanduser("~\\AppData\\Local\\KernelOne\\cache"))
    if sys_platform_is_macos():
        return os.path.abspath(os.path.expanduser("~/Library/Caches/KernelOne"))
    xdg = str(os.environ.get("XDG_CACHE_HOME") or "").strip()
    if xdg:
        return os.path.abspath(os.path.join(os.path.expanduser(xdg), "kernelone"))
    return os.path.abspath(os.path.expanduser("~/.cache/kernelone"))


# Backward-compat alias — prefer ``default_kernelone_cache_base()`` in new code.
default_system_cache_base = default_kernelone_cache_base


def sys_platform_is_macos() -> bool:
    """Check if running on macOS using sys.platform for reliability."""

    return sys.platform == "darwin"


def workspace_key(workspace: str) -> str:
    workspace_abs = os.path.abspath(os.path.expanduser(workspace or os.getcwd()))
    base = os.path.basename(workspace_abs.rstrip("\\/")) or "workspace"
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    slug = slug or "workspace"
    digest = hashlib.sha1(workspace_abs.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{slug}-{digest}"


def _is_within_path(parent: str, child: str) -> bool:
    try:
        parent_abs = os.path.abspath(parent)
        child_abs = os.path.abspath(child)
        return os.path.commonpath([parent_abs, child_abs]) == parent_abs
    except (OSError, ValueError):
        return False


_writable_base_cache: dict[str, tuple[bool, float]] = {}
_writable_base_cache_ttl = 300.0


def _is_runtime_base_writable(base: str) -> bool:
    """Check if a runtime base is writable with caching."""
    candidate = os.path.abspath(base)

    now = time.monotonic()
    if candidate in _writable_base_cache:
        result, timestamp = _writable_base_cache[candidate]
        if now - timestamp < _writable_base_cache_ttl:
            return result

    try:
        os.makedirs(candidate, exist_ok=True)
        probe_dir = os.path.join(candidate, get_workspace_metadata_dir_name() + "-probe")
        os.makedirs(probe_dir, exist_ok=True)
        probe_file = os.path.join(probe_dir, ".write")
        with open(probe_file, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe_file)
        os.rmdir(probe_dir)
        _writable_base_cache[candidate] = (True, now)
        return True
    except (OSError, ValueError):
        _writable_base_cache[candidate] = (False, now)
        return False


def _runtime_base_and_mode(workspace_abs: str, ramdisk_root: str | None) -> tuple[str, str]:
    explicit_runtime_root = resolve_env_str("runtime_root")
    if explicit_runtime_root:
        base = os.path.abspath(os.path.expanduser(os.path.expandvars(explicit_runtime_root)))
        if not _is_within_path(workspace_abs, base) and _is_runtime_base_writable(base):
            return base, "explicit_runtime_root"

    if state_to_ramdisk_enabled():
        ramdisk = resolve_ramdisk_root(ramdisk_root)
        if (
            ramdisk
            and os.path.exists(ramdisk)
            and not _is_within_path(workspace_abs, ramdisk)
            and _is_runtime_base_writable(ramdisk)
        ):
            return ramdisk, "ramdisk"

    explicit_cache_root = resolve_env_str("runtime_cache_root")
    if explicit_cache_root:
        base = os.path.abspath(os.path.expanduser(os.path.expandvars(explicit_cache_root)))
        if not _is_within_path(workspace_abs, base) and _is_runtime_base_writable(base):
            return base, "explicit_runtime_cache"

    system_cache_base = default_kernelone_cache_base()
    if not _is_within_path(workspace_abs, system_cache_base) and _is_runtime_base_writable(system_cache_base):
        return system_cache_base, "system_cache"

    raise RuntimeError(
        "No writable runtime base is available. Configure KERNELONE_RUNTIME_ROOT, "
        "KERNELONE_RUNTIME_CACHE_ROOT, or a writable system cache location."
    )


def _resolve_storage_roots_impl(workspace: str, ramdisk_root: str | None = None) -> StorageRoots:
    """Internal resolver — replaceable via DI / patching for testing.

    Contains all caching and resolution logic. ``resolve_storage_roots()`` is a
    thin wrapper that calls this function so that callers can patch it without
    needing to clear the module-level cache first.
    """
    normalized_workspace = os.path.abspath(os.path.expanduser(workspace or os.getcwd()))
    cached = _get_cached_storage_roots(normalized_workspace, ramdisk_root)
    if cached is not None:
        return cached

    # Delegate to the registered business-layer resolver if available.
    # This replaces the former direct import of polaris.cells.storage.layout.internal.
    with _business_roots_resolver_lock:
        resolver = _business_roots_resolver

    if resolver is not None and get_workspace_metadata_dir_name() != get_workspace_metadata_dir_default():
        try:
            business_roots = resolver(normalized_workspace, ramdisk_root)
            if business_roots is not None:
                # Wrap in a standard StorageRoots for API compat if needed.
                if isinstance(business_roots, StorageRoots):
                    roots = business_roots
                else:
                    roots = StorageRoots(
                        workspace_abs=business_roots.workspace_abs,
                        workspace_key=business_roots.workspace_key,
                        storage_layout_mode=business_roots.storage_layout_mode,
                        home_root=business_roots.home_root,
                        global_root=business_roots.global_root,
                        config_root=business_roots.config_root,
                        projects_root=business_roots.projects_root,
                        project_root=business_roots.project_root,
                        project_persistent_root=business_roots.project_persistent_root,
                        runtime_projects_root=business_roots.runtime_projects_root,
                        runtime_project_root=business_roots.runtime_project_root,
                        workspace_persistent_root=business_roots.workspace_persistent_root,
                        runtime_base=business_roots.runtime_base,
                        runtime_root=business_roots.runtime_root,
                        runtime_mode=business_roots.runtime_mode,
                        history_root=business_roots.history_root,
                    )
                _set_cached_storage_roots(normalized_workspace, ramdisk_root, roots)
                return roots
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as _resolver_exc:
            # Resolver raised unexpectedly — fall through to generic resolution.
            _logger.debug(
                "Business roots resolver raised; falling back to generic resolution: %s",
                _resolver_exc,
            )

    workspace_abs = normalized_workspace
    key = workspace_key(workspace_abs)
    home_root = kernelone_home()
    runtime_base, runtime_mode = _runtime_base_and_mode(workspace_abs, ramdisk_root)
    metadata_dir_name = get_workspace_metadata_dir_name()
    project_local_root = os.path.join(workspace_abs, metadata_dir_name)

    global_root = home_root
    config_root = os.path.join(home_root, "config")
    projects_root = project_local_root
    project_root = project_local_root
    project_persistent_root = project_local_root
    # Avoid double .polaris nesting when runtime_base already contains metadata_dir_name as a path segment
    # e.g., runtime_base="C:/Temp/FileServer/.polaris/runtime" already contains ".polaris"
    # so joining with metadata_dir_name=".polaris" produces double nesting
    runtime_base_parts = runtime_base.replace("\\", "/").split("/")
    if metadata_dir_name in runtime_base_parts:
        runtime_projects_root = os.path.join(runtime_base, "projects")
    else:
        runtime_projects_root = os.path.join(runtime_base, metadata_dir_name, "projects")
    runtime_project_root = os.path.join(runtime_projects_root, key, "runtime")

    roots = StorageRoots(
        workspace_abs=workspace_abs,
        workspace_key=key,
        storage_layout_mode="project_local",
        home_root=home_root,
        global_root=global_root,
        config_root=config_root,
        projects_root=projects_root,
        project_root=project_root,
        project_persistent_root=project_persistent_root,
        runtime_projects_root=runtime_projects_root,
        runtime_project_root=runtime_project_root,
        workspace_persistent_root=project_persistent_root,
        runtime_base=runtime_base,
        runtime_root=runtime_project_root,
        runtime_mode=runtime_mode,
        history_root=os.path.join(project_persistent_root, "history"),
    )

    _set_cached_storage_roots(normalized_workspace, ramdisk_root, roots)
    return roots


def resolve_storage_roots(workspace: str, ramdisk_root: str | None = None) -> StorageRoots:
    """Resolve storage roots with caching to avoid repeated filesystem probing.

    When a business-layer roots resolver has been registered via
    ``register_business_roots_resolver()``, this function delegates to it so
    that product-specific path conventions (e.g. Polaris's config_root
    anchored at polaris_home()) are honoured.  If the resolver returns
    ``None``, or if no resolver is registered, generic KernelOne resolution
    is used instead.

    This is a thin wrapper around ``_resolve_storage_roots_impl`` to allow
    tests to patch the internal resolver without needing to clear the module
    cache first.
    """
    return _resolve_storage_roots_impl(workspace, ramdisk_root)


def normalize_logical_rel_path(rel_path: str) -> str:
    raw = str(rel_path or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {raw}")
    p = raw.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")

    metadata_dir_name = get_workspace_metadata_dir_name()
    if p.startswith(metadata_dir_name + "/"):
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {raw}")

    p = os.path.normpath(p).replace("\\", "/")
    if p == ".":
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {raw}")
    if p.startswith("../") or p == "..":
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {raw}")
    prefix = p.split("/", 1)[0]
    legacy_target = _LEGACY_LOGICAL_PREFIX_ALIASES.get(prefix.lower())
    if legacy_target:
        suffix = p[len(prefix) :].lstrip("/")
        p = legacy_target if not suffix else f"{legacy_target}/{suffix}"
        prefix = p.split("/", 1)[0]
    if prefix not in _ALLOWED_PREFIXES:
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {raw}")
    return p


def _join_under(root: str, rel_path: str) -> str:
    abs_root = os.path.abspath(root)
    full = os.path.abspath(os.path.join(abs_root, rel_path))
    if os.path.commonpath([abs_root, full]) != abs_root:
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {rel_path}")
    return full


def resolve_global_path(rel_path: str) -> str:
    normalized = normalize_logical_rel_path(rel_path)
    roots = resolve_storage_roots("")
    if normalized == "config":
        suffix = ""
    elif normalized.startswith("config/"):
        suffix = normalized[len("config/") :]
    else:
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {rel_path}")
    root = roots.config_root
    return _join_under(root, suffix)


def resolve_workspace_persistent_path(workspace: str, rel_path: str) -> str:
    normalized = normalize_logical_rel_path(rel_path)
    if normalized == "workspace":
        suffix = ""
    elif normalized.startswith("workspace/"):
        suffix = normalized[len("workspace/") :]
    else:
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {rel_path}")
    roots = resolve_storage_roots(workspace)
    return _join_under(roots.project_persistent_root, suffix)


def resolve_runtime_path(
    workspace: str,
    rel_path: str,
    *,
    ramdisk_root: str | None = None,
) -> str:
    normalized = normalize_logical_rel_path(rel_path)
    if normalized == "runtime":
        suffix = ""
    elif normalized.startswith("runtime/"):
        suffix = normalized[len("runtime/") :]
    else:
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {rel_path}")
    roots = resolve_storage_roots(workspace, ramdisk_root=ramdisk_root)
    return _join_under(roots.runtime_project_root, suffix)


def resolve_logical_path(
    workspace: str,
    rel_path: str,
    *,
    ramdisk_root: str | None = None,
) -> str:
    normalized = normalize_logical_rel_path(rel_path)
    if normalized == "runtime" or normalized.startswith("runtime/"):
        return resolve_runtime_path(
            workspace,
            normalized,
            ramdisk_root=ramdisk_root,
        )
    if normalized == "workspace" or normalized.startswith("workspace/"):
        return resolve_workspace_persistent_path(workspace, normalized)
    if normalized == "config" or normalized.startswith("config/"):
        return resolve_global_path(normalized)
    raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {rel_path}")


class StorageLayout:
    """Path resolver for runtime/workspace/config storage domains.

    This is the authoritative storage layout object used by application services.
    """

    def __init__(self, workspace: Path | str, runtime_base: Path | str) -> None:
        self._workspace = Path(workspace).resolve()
        self._runtime_base = Path(runtime_base).resolve()
        self._key = workspace_key(str(self._workspace))
        self._runtime_root = self._runtime_base / get_workspace_metadata_dir_name() / "projects" / self._key / "runtime"
        self._workspace_root = self._workspace / get_workspace_metadata_dir_name()
        self._config_root = Path(kernelone_home()) / "config"

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def runtime_root(self) -> Path:
        return self._runtime_root

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def config_root(self) -> Path:
        return self._config_root

    def get_path(self, category: str, *parts: str) -> Path:
        if category == "runtime":
            base = self._runtime_root
        elif category == "workspace":
            base = self._workspace_root
        elif category == "config":
            base = self._config_root
        elif category == "logs":
            base = self._runtime_root / "logs"
        elif category == "control":
            base = self._runtime_root / "control"
        elif category == "status":
            base = self._runtime_root / "status"
        elif category == "events":
            base = self._runtime_root / "events"
        elif category == "results":
            base = self._runtime_root / "results"
        elif category == "contracts":
            base = self._runtime_root / "contracts"
        else:
            raise ValueError(f"Unknown category: {category}")

        return base.joinpath(*parts)

    def ensure_dir(self, category: str, *parts: str) -> Path:
        path = self.get_path(category, *parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_artifact_path(self, rel_path: str) -> Path:
        normalized = normalize_logical_rel_path(str(rel_path or ""))
        if normalized == "runtime":
            suffix = ""
            root = str(self._runtime_root)
        elif normalized.startswith("runtime/"):
            suffix = normalized[len("runtime/") :]
            root = str(self._runtime_root)
        elif normalized == "workspace":
            suffix = ""
            root = str(self._workspace_root)
        elif normalized.startswith("workspace/"):
            suffix = normalized[len("workspace/") :]
            root = str(self._workspace_root)
        elif normalized == "config":
            suffix = ""
            root = str(self._config_root)
        elif normalized.startswith("config/"):
            suffix = normalized[len("config/") :]
            root = str(self._config_root)
        else:
            raise ValueError(f"Unsupported artifact path prefix: {rel_path}")
        resolved = _join_under(root, suffix)
        return Path(resolved)


__all__ = [
    "UNSUPPORTED_PATH_PREFIX",
    "PathResolver",
    "ResolvedPath",
    "StorageLayer",
    "StorageLayout",
    "StorageRoots",
    "clear_business_roots_resolver",
    "clear_storage_roots_cache",
    "default_kernelone_cache_base",
    "default_ramdisk_root",
    "default_system_cache_base",
    "kernelone_home",
    "normalize_logical_rel_path",
    "normalize_ramdisk_root",
    "register_business_roots_resolver",
    "resolve_global_path",
    "resolve_logical_path",
    "resolve_ramdisk_root",
    "resolve_runtime_path",
    "resolve_storage_roots",
    "resolve_workspace_persistent_path",
    "state_to_ramdisk_enabled",
    "workspace_key",
]
