"""Dependency Injection Container for Polaris backend.

Provides a simple but complete DI container that supports:
- Singleton lifecycle (one instance shared)
- Factory registration
- Type-based and name-based resolution
- Lazy initialization

Note: Service assembly logic has been moved to polaris.bootstrap.assembly.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class Registration:
    """A service registration."""

    factory: Callable[..., Any]
    is_singleton: bool = True
    instance: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DIContainer:
    """Simple dependency injection container.

    Example:
        container = DIContainer()

        # Register singleton
        container.register_instance(Settings, settings)

        # Register factory
        container.register_factory(PMService, lambda c: PMService(
            settings=c.resolve(Settings),
            storage=c.resolve(StorageLayout)
        ))

        # Resolve
        pm_service = await container.resolve_async(PMService)
    """

    def __init__(self) -> None:
        self._registrations: dict[type, Registration] = {}
        self._named_registrations: dict[str, Registration] = {}

    def register_instance(self, interface: type[T], instance: T) -> None:
        """Register a pre-created instance as singleton."""
        self._registrations[interface] = Registration(
            factory=lambda: instance,
            is_singleton=True,
            instance=instance,
        )

    def register_factory(
        self,
        interface: type[T],
        factory: Callable[[DIContainer], T],
        is_singleton: bool = True,
    ) -> None:
        """Register a factory function."""
        self._registrations[interface] = Registration(
            factory=factory,
            is_singleton=is_singleton,
            instance=None,
        )

    def register_singleton(self, interface: type[T], factory: Callable[[DIContainer], T]) -> None:
        """Register a singleton factory."""
        self.register_factory(interface, factory, is_singleton=True)

    def register_transient(self, interface: type[T], factory: Callable[[DIContainer], T]) -> None:
        """Register a transient factory (new instance each time)."""
        self.register_factory(interface, factory, is_singleton=False)

    def _find_registration(self, interface: type[T]) -> Registration | None:
        """Find registration by identity first, then by module+qualname fallback.

        The fallback handles class identity drift caused by module reloads during tests.
        """
        reg = self._registrations.get(interface)
        if reg is not None:
            return reg

        module_name = getattr(interface, "__module__", "")
        qual_name = getattr(interface, "__qualname__", "")
        if not module_name or not qual_name:
            return None

        for registered_type, candidate in self._registrations.items():
            if (
                getattr(registered_type, "__module__", "") == module_name
                and getattr(registered_type, "__qualname__", "") == qual_name
            ):
                return candidate
        return None

    def resolve(self, interface: type[T]) -> T:
        """Synchronous resolution (for already created singletons)."""
        reg = self._find_registration(interface)
        if reg is None:
            raise KeyError(f"No registration for {interface}")

        if reg.is_singleton and reg.instance is not None:
            return reg.instance

        # For async factories, we need async resolve
        raise RuntimeError(f"Use resolve_async for {interface}")

    async def resolve_async(self, interface: type[T]) -> T:
        """Asynchronous resolution."""
        reg = self._find_registration(interface)
        if reg is None:
            raise KeyError(f"No registration for {interface}")

        if reg.is_singleton:
            if reg.instance is not None:
                return reg.instance

            async with reg.lock:
                # Double-check after acquiring lock
                if reg.instance is not None:
                    return reg.instance

                # Create instance
                instance = reg.factory(self)
                if inspect.isawaitable(instance):
                    instance = await instance
                reg.instance = instance
                return instance
        else:
            # Transient
            instance = reg.factory(self)
            if inspect.isawaitable(instance):
                instance = await instance
            return instance

    def has_registration(self, interface: type) -> bool:
        """Check if interface is registered."""
        return self._find_registration(interface) is not None

    def clear(self) -> None:
        """Clear all registrations (for testing)."""
        self._registrations.clear()


# Global container instance
_container: DIContainer | None = None
_container_lock = asyncio.Lock()


async def get_container() -> DIContainer:
    """Get or create the global DI container."""
    global _container
    if _container is None:
        async with _container_lock:
            if _container is None:
                _container = await _create_container()
    return _container


def reset_container() -> None:
    """Reset container (for testing)."""
    global _container
    _container = None


async def _create_container() -> DIContainer:
    """Create and configure the DI container."""
    from polaris.bootstrap.assembly import assemble_core_services

    container = DIContainer()
    assemble_core_services(container)
    return container
