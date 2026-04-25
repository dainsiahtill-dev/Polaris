"""KernelOne filesystem registry.

This module provides a global registry for the default KernelFileSystemAdapter.
Lazy initialization ensures tests and standalone tooling work without explicit bootstrap.

For test isolation, use reset_default_adapter() to clear the singleton.

KernelOne Purity Note (2026-04-25):
    The lazy fallback uses an ``IFileSystemAdapterFactory`` port instead of
    directly importing ``polaris.infrastructure.storage.local_fs_adapter``.
    Infrastructure registers the factory via ``set_adapter_factory()`` during
    bootstrap, preserving the KernelOne -> Infrastructure dependency direction.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.fs.contracts import KernelFileSystemAdapter
    from polaris.kernelone.ports.storage import IFileSystemAdapterFactory

logger = logging.getLogger(__name__)

_default_adapter: KernelFileSystemAdapter | None = None
_adapter_factory: IFileSystemAdapterFactory | None = None
_lock = threading.Lock()
_initialization_attempted = False


def set_adapter_factory(factory: IFileSystemAdapterFactory) -> None:
    """Register the factory used for lazy adapter creation.

    Infrastructure calls this during bootstrap to provide a callable that
    creates a concrete ``KernelFileSystemAdapter`` (e.g. ``LocalFileSystemAdapter``)
    without KernelOne importing infrastructure modules.

    Args:
        factory: A callable returning a ``KernelFileSystemAdapter`` instance.
    """
    global _adapter_factory
    with _lock:
        _adapter_factory = factory


def set_default_adapter(adapter: KernelFileSystemAdapter) -> None:
    """Set the default filesystem adapter for KernelOne."""
    global _default_adapter
    with _lock:
        _default_adapter = adapter


def reset_default_adapter() -> None:
    """Reset the default filesystem adapter.

    This function is primarily for test isolation. It clears the singleton
    and the initialization flag, allowing tests to start with a clean state.

    Note: Does not clear _initialization_attempted flag to prevent recursion
    if lazy initialization is triggered during reset.
    """
    global _default_adapter
    with _lock:
        _default_adapter = None


def _ensure_default_adapter() -> None:
    """Ensure a default adapter is set by lazy initialization.

    This function is called automatically by get_default_adapter() when no adapter
    has been set. It uses the registered ``IFileSystemAdapterFactory`` if available,
    falling back to a direct infrastructure import during the migration period.

    Note: This uses a flag to prevent infinite recursion if the lazy initialization
    itself requires a filesystem adapter.
    """
    global _default_adapter, _initialization_attempted

    if _initialization_attempted:
        # Already tried to initialize, don't recurse
        return

    _initialization_attempted = True

    # Only inject if not already set (another thread might have set it)
    if _default_adapter is not None:
        return

    # Preferred path: use the registered port factory
    factory = _adapter_factory
    if factory is not None:
        try:
            _default_adapter = factory()
            return
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "IFileSystemAdapterFactory failed, attempting migration fallback: %s",
                exc,
            )

    # Migration fallback: direct import until all bootstrap paths wire the factory.
    # TODO(kernelone-purity): Remove this fallback once bootstrap universally calls
    # set_adapter_factory().
    try:
        from polaris.infrastructure.storage.local_fs_adapter import (
            LocalFileSystemAdapter,
        )

        _default_adapter = LocalFileSystemAdapter()
    except (RuntimeError, ValueError):
        # If even the default injection fails, leave _default_adapter as None
        # so the original RuntimeError will be raised
        pass


def get_default_adapter() -> KernelFileSystemAdapter:
    """Get the default filesystem adapter.

    If no adapter has been set, this method will attempt to create one using
    the registered ``IFileSystemAdapterFactory`` port, or fall back to a direct
    ``LocalFileSystemAdapter`` import during the migration period.

    Raises:
        RuntimeError: If no adapter is set and lazy initialization fails.
    """
    global _default_adapter

    with _lock:
        adapter = _default_adapter

    if adapter is None:
        _ensure_default_adapter()
        with _lock:
            adapter = _default_adapter

    if adapter is None:
        raise RuntimeError(
            "Default KernelFileSystemAdapter not set and lazy initialization failed. "
            "Ensure ensure_minimal_kernelone_bindings() is called during bootstrap, "
            "or register a factory with set_adapter_factory()."
        )
    return adapter
