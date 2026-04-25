"""IProviderRegistryPort registration and consumption.

This module provides thread-safe registration and access to the IProviderRegistryPort
for KernelOne LLM modules, enabling proper dependency injection during bootstrap.

Architecture
------------
::

    bootstrap/assembly.py -> set_provider_manager_port(provider_adapter)
                                    |
                                    v
    KernelOne LLM modules -> get_provider_manager_port() -> IProviderRegistryPort

Migration Note
-------------
Once bootstrap universally wires this port, the get_provider_manager_port() function
should raise an error if no port is registered, instead of falling back to
ServiceLocator.get_provider_manager().
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.ports.provider_registry import IProviderRegistryPort

_provider_port: IProviderRegistryPort | None = None
_provider_port_lock = threading.Lock()


def set_provider_manager_port(port: IProviderRegistryPort) -> None:
    """Register an IProviderRegistryPort for KernelOne LLM modules.

    This function should be called during bootstrap to wire the provider
    management port. After this is called, all LLM modules should use
    get_provider_manager_port() instead of importing provider_manager directly.

    Args:
        port: An IProviderRegistryPort implementation (e.g., ProviderAdapter).

    Example:
        >>> from polaris.infrastructure.llm.provider_bootstrap import ProviderAdapter
        >>> from polaris.infrastructure.llm.providers.provider_registry import provider_manager
        >>> adapter = ProviderAdapter(provider_manager)
        >>> set_provider_manager_port(adapter)
    """
    global _provider_port
    with _provider_port_lock:
        _provider_port = port


def reset_provider_manager_port() -> None:
    """Reset the registered provider manager port (for testing).

    This function should only be used in test fixtures to ensure
    a clean state between tests.
    """
    global _provider_port
    with _provider_port_lock:
        _provider_port = None


def get_provider_manager_port() -> IProviderRegistryPort | None:
    """Get the currently registered IProviderRegistryPort.

    Returns:
        The registered IProviderRegistryPort, or None if not registered.

    Migration Note:
        Once bootstrap wiring is complete, this should raise an error
        instead of returning None, to enforce proper initialization.
    """
    with _provider_port_lock:
        return _provider_port


def has_provider_manager_port() -> bool:
    """Check if a provider manager port is registered.

    Returns:
        True if a port is registered, False otherwise.
    """
    with _provider_port_lock:
        return _provider_port is not None


__all__ = [
    "get_provider_manager_port",
    "has_provider_manager_port",
    "reset_provider_manager_port",
    "set_provider_manager_port",
]
