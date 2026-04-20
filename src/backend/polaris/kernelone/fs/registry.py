"""KernelOne filesystem registry.

This module provides a global registry for the default KernelFileSystemAdapter.
Lazy initialization ensures tests and standalone tooling work without explicit bootstrap.

For test isolation, use reset_default_adapter() to clear the singleton.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.fs.contracts import KernelFileSystemAdapter

_default_adapter: KernelFileSystemAdapter | None = None
_lock = threading.Lock()
_initialization_attempted = False


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
    has been set. It injects a LocalFileSystemAdapter as the default, which is
    sufficient for most use cases including tests and standalone tooling.

    Note: This uses a flag to prevent infinite recursion if the lazy initialization
    itself requires a filesystem adapter.
    """
    global _default_adapter, _initialization_attempted

    if _initialization_attempted:
        # Already tried to initialize, don't recurse
        return

    _initialization_attempted = True

    # Only inject if not already set (another thread might have set it)
    if _default_adapter is None:
        try:
            # Import the concrete adapter directly to avoid the package-level
            # storage module pulling fs package exports back into this lazy
            # initialization path.
            from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

            _default_adapter = LocalFileSystemAdapter()
        except (RuntimeError, ValueError):
            # If even the default injection fails, leave _default_adapter as None
            # so the original RuntimeError will be raised
            pass


def get_default_adapter() -> KernelFileSystemAdapter:
    """Get the default filesystem adapter.

    If no adapter has been set, this method will automatically inject a
    LocalFileSystemAdapter as the default (lazy initialization). This ensures
    that tests and standalone tooling work without requiring explicit bootstrap.

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
            "or explicitly set a default adapter with set_default_adapter()."
        )
    return adapter
