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
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from polaris.kernelone.fs.contracts import KernelFileSystemAdapter
    from polaris.kernelone.ports.storage import IFileSystemAdapterFactory

logger = logging.getLogger(__name__)

_default_adapter: KernelFileSystemAdapter | None = None
_adapter_factory: IFileSystemAdapterFactory | None = None
_lock = threading.RLock()
_state_changed = threading.Condition(_lock)
_initialization_attempted = False
_initialization_in_progress = False
_initialization_owner: int | None = None
_initialization_error: Exception | None = None
_RECOVERABLE_INITIALIZATION_ERRORS = (
    ImportError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class DefaultAdapterInitializationError(RuntimeError):
    """Raised when lazy default-adapter initialization has failed.

    The original failure is retained as ``__cause__`` so callers can distinguish
    a missing bootstrap binding from a concrete factory or fallback failure.
    """


def _raise_initialization_error(error: Exception) -> NoReturn:
    """Raise the stable lazy-initialization failure with its causal exception."""
    raise DefaultAdapterInitializationError(
        "Default KernelFileSystemAdapter lazy initialization failed. "
        "Call reset_default_adapter() or set_adapter_factory() before retrying."
    ) from error


def set_adapter_factory(factory: IFileSystemAdapterFactory) -> None:
    """Register the factory used for lazy adapter creation.

    Infrastructure calls this during bootstrap to provide a callable that
    creates a concrete ``KernelFileSystemAdapter`` (e.g. ``LocalFileSystemAdapter``)
    without KernelOne importing infrastructure modules.

    Args:
        factory: A callable returning a ``KernelFileSystemAdapter`` instance.
    """
    global _adapter_factory, _initialization_attempted, _initialization_error
    with _state_changed:
        _adapter_factory = factory
        if _default_adapter is None and not _initialization_in_progress:
            _initialization_attempted = False
            _initialization_error = None
        _state_changed.notify_all()


def set_default_adapter(adapter: KernelFileSystemAdapter) -> None:
    """Set the default filesystem adapter for KernelOne."""
    global _default_adapter, _initialization_attempted, _initialization_error
    with _state_changed:
        _default_adapter = adapter
        _initialization_attempted = True
        _initialization_error = None
        _state_changed.notify_all()


def reset_default_adapter() -> None:
    """Reset the default filesystem adapter.

    This function is primarily for test isolation. It clears the singleton
    and the initialization flag, allowing tests to start with a clean state.

    A reset is the explicit retry boundary after a failed lazy initialization.
    It intentionally preserves the registered factory so bootstrap configuration
    does not need to be repeated.
    """
    global _default_adapter, _initialization_attempted, _initialization_error
    with _state_changed:
        _default_adapter = None
        _initialization_attempted = False
        _initialization_error = None
        _state_changed.notify_all()


def _create_fallback_adapter() -> KernelFileSystemAdapter:
    """Create the migration fallback adapter without publishing it."""
    from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

    return LocalFileSystemAdapter()


def _ensure_default_adapter() -> None:
    """Ensure a default adapter is set by lazy initialization.

    This function is called automatically by get_default_adapter() when no adapter
    has been set. It uses the registered ``IFileSystemAdapterFactory`` if available,
    falling back to a direct infrastructure import during the migration period.

    Exactly one caller owns one lazy-initialization attempt. Other callers wait
    for that attempt to publish a fully constructed adapter or a stable failure;
    they can never observe an in-progress singleton as initialized.
    """
    global _default_adapter
    global _initialization_attempted
    global _initialization_error
    global _initialization_in_progress
    global _initialization_owner

    with _state_changed:
        while _initialization_in_progress:
            if _initialization_owner == threading.get_ident():
                raise RuntimeError("Recursive default filesystem adapter initialization")
            _state_changed.wait()
            if _default_adapter is not None:
                return

        if _default_adapter is not None:
            return
        if _initialization_attempted and _initialization_error is not None:
            _raise_initialization_error(_initialization_error)
        if _initialization_attempted:
            # This state can only be produced by legacy callers mutating the
            # private flag. Normal transitions never mark an attempt complete
            # before they publish an adapter or record a failure.
            return

        _initialization_in_progress = True
        _initialization_owner = threading.get_ident()
        factory = _adapter_factory

    try:
        adapter: KernelFileSystemAdapter
        if factory is not None:
            try:
                adapter = factory()
            except _RECOVERABLE_INITIALIZATION_ERRORS:
                logger.warning(
                    "IFileSystemAdapterFactory failed; attempting migration fallback",
                    exc_info=True,
                )
                adapter = _create_fallback_adapter()
        else:
            adapter = _create_fallback_adapter()

        if adapter is None:
            raise RuntimeError("IFileSystemAdapterFactory returned None")
    except _RECOVERABLE_INITIALIZATION_ERRORS as exc:
        with _state_changed:
            _initialization_in_progress = False
            _initialization_owner = None
            if _default_adapter is not None:
                _initialization_attempted = True
                _initialization_error = None
                _state_changed.notify_all()
                return
            _initialization_attempted = True
            _initialization_error = exc
            _state_changed.notify_all()
        _raise_initialization_error(exc)
    except BaseException:
        with _state_changed:
            _initialization_in_progress = False
            _initialization_owner = None
            _state_changed.notify_all()
        raise

    with _state_changed:
        if _default_adapter is None:
            _default_adapter = adapter
        _initialization_in_progress = False
        _initialization_owner = None
        _initialization_attempted = True
        _initialization_error = None
        _state_changed.notify_all()


def get_default_adapter() -> KernelFileSystemAdapter:
    """Get the default filesystem adapter.

    If no adapter has been set, this method will attempt to create one using
    the registered ``IFileSystemAdapterFactory`` port, or fall back to a direct
    ``LocalFileSystemAdapter`` import during the migration period.

    Raises:
        RuntimeError: If no adapter is set and lazy initialization fails.
    """
    with _state_changed:
        adapter = _default_adapter

    if adapter is None:
        _ensure_default_adapter()
        with _state_changed:
            adapter = _default_adapter

    if adapter is None:
        raise RuntimeError(
            "Default KernelFileSystemAdapter not set and lazy initialization failed. "
            "Ensure ensure_minimal_kernelone_bindings() is called during bootstrap, "
            "or register a factory with set_adapter_factory()."
        )
    return adapter
