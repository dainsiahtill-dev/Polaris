"""DI Container Scope - Test isolation through scoped lifecycle management.

This module provides DIContainerScope for managing dependency injection scopes
with automatic cleanup support, enabling proper test isolation by resetting
all registered singletons when a scope is cleaned up.

Design Principles:
1. Thread/async-safe scope management via contextvars
2. Automatic registration tracking for global singletons
3. Unified cleanup interface for all scoped singletons
4. Backward compatible with existing global state patterns

Usage:
    # Create a scope
    scope = DIContainerScope()

    # Register singletons that should be scoped
    scope.register_singleton(KernelEmbeddingPort, lambda _: MyEmbeddingPort())
    scope.register_singleton(EventRegistry, lambda _: EventRegistry())

    # Use the scope
    port = scope.resolve(KernelEmbeddingPort)

    # Cleanup for test isolation
    scope.cleanup_scope()
"""

from __future__ import annotations

import asyncio
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")


# Module-level registry of all DIContainerScope instances
# This allows centralized cleanup of all scopes
_scope_registry: list[DIContainerScope] = []
_scope_registry_lock = threading.Lock()


def _register_scope(scope: DIContainerScope) -> None:
    """Register a scope in the global registry."""
    with _scope_registry_lock:
        _scope_registry.append(scope)


def _unregister_scope(scope: DIContainerScope) -> None:
    """Unregister a scope from the global registry."""
    with _scope_registry_lock:
        if scope in _scope_registry:
            _scope_registry.remove(scope)


def cleanup_all_scopes() -> int:
    """Cleanup all registered scopes.

    Returns:
        Number of scopes cleaned up.
    """
    with _scope_registry_lock:
        scopes = list(_scope_registry)
        _scope_registry.clear()

    for scope in scopes:
        scope.cleanup_scope()
    return len(scopes)


# Context variable for the current scope
_current_scope: ContextVar[DIContainerScope | None] = ContextVar("current_scope", default=None)


@dataclass
class _ScopedRegistration:
    """A scoped service registration."""

    factory: Callable[[], Any]
    instance: Any | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    is_async: bool = False


class DIContainerScope:
    """DI Container Scope with test isolation support.

    Features:
    - Singleton lifecycle within scope
    - Automatic cleanup via cleanup_scope()
    - Context variable integration for async safety
    - Registration tracking for debugging

    Example:
        scope = DIContainerScope()

        # Register
        scope.register_singleton(MyService, lambda: MyService())

        # Resolve
        service = scope.resolve(MyService)

        # Cleanup (for test isolation)
        scope.cleanup_scope()
    """

    def __init__(self, name: str | None = None) -> None:
        """Initialize a new scope.

        Args:
            name: Optional name for debugging.
        """
        self._name = name or f"scope_{id(self)}"
        self._registrations: dict[type, _ScopedRegistration] = {}
        self._async_registrations: dict[type, _ScopedRegistration] = {}
        self._instances: dict[type, Any] = {}
        self._lock = threading.RLock()
        self._is_cleaned_up = False
        _register_scope(self)

    @property
    def name(self) -> str:
        """Scope name for debugging."""
        return self._name

    @property
    def is_cleaned_up(self) -> bool:
        """Check if scope has been cleaned up."""
        return self._is_cleaned_up

    def register_singleton(
        self,
        interface: type[T],
        factory: Callable[[], T],
    ) -> None:
        """Register a singleton factory in this scope.

        Args:
            interface: The interface/type to register.
            factory: Factory function to create the instance.
        """
        if self._is_cleaned_up:
            raise RuntimeError(f"Cannot register on cleaned-up scope: {self._name}")

        with self._lock:
            self._registrations[interface] = _ScopedRegistration(
                factory=factory,
                instance=None,
                is_async=False,
            )

    def register_async_singleton(
        self,
        interface: type[T],
        factory: Callable[[], T],
    ) -> None:
        """Register an async singleton factory in this scope.

        Args:
            interface: The interface/type to register.
            factory: Async factory function to create the instance.
        """
        if self._is_cleaned_up:
            raise RuntimeError(f"Cannot register on cleaned-up scope: {self._name}")

        with self._lock:
            self._async_registrations[interface] = _ScopedRegistration(
                factory=factory,
                instance=None,
                is_async=True,
            )

    def register_instance(
        self,
        interface: type[T],
        instance: T,
    ) -> None:
        """Register a pre-created instance as singleton.

        Args:
            interface: The interface/type to register.
            instance: Pre-created instance.
        """
        if self._is_cleaned_up:
            raise RuntimeError(f"Cannot register on cleaned-up scope: {self._name}")

        # Capture instance in closure with explicit type
        captured_instance: T = instance

        def factory() -> T:
            return captured_instance

        with self._lock:
            self._registrations[interface] = _ScopedRegistration(
                factory=factory,
                instance=instance,
                is_async=False,
            )

    def resolve(self, interface: type[T]) -> T:
        """Resolve a synchronous registration.

        Args:
            interface: The interface/type to resolve.

        Returns:
            The singleton instance.

        Raises:
            KeyError: If interface is not registered.
        """
        if self._is_cleaned_up:
            raise RuntimeError(f"Cannot resolve from cleaned-up scope: {self._name}")

        with self._lock:
            reg = self._registrations.get(interface)
            if reg is None:
                raise KeyError(f"No registration for {interface}")

            if reg.instance is not None:
                return reg.instance

            # Create instance under lock
            instance = reg.factory()
            reg.instance = instance
            return instance

    async def resolve_async(self, interface: type[T]) -> T:
        """Resolve a registration (supports both sync and async factories).

        Args:
            interface: The interface/type to resolve.

        Returns:
            The singleton instance.

        Raises:
            KeyError: If interface is not registered.
        """
        if self._is_cleaned_up:
            raise RuntimeError(f"Cannot resolve from cleaned-up scope: {self._name}")

        # Check async registrations first (under sync lock)
        with self._lock:
            async_reg = self._async_registrations.get(interface)
            if async_reg is not None and async_reg.instance is not None:
                return async_reg.instance

        # Async lock needed - release sync lock first
        if async_reg is not None:
            # Create async instance with async lock
            async with async_reg.lock:
                if async_reg.instance is None:
                    instance = async_reg.factory()
                    if asyncio.iscoroutine(instance):
                        instance = await instance
                    async_reg.instance = instance
                return async_reg.instance

        # Fall back to sync registrations (still under sync lock)
        with self._lock:
            reg = self._registrations.get(interface)
            if reg is None:
                raise KeyError(f"No registration for {interface}")

            if reg.instance is not None:
                return reg.instance

            if reg.instance is None:
                instance = reg.factory()
                if asyncio.iscoroutine(instance):
                    instance = await instance
                reg.instance = instance
            return reg.instance

    def has_registration(self, interface: type) -> bool:
        """Check if interface is registered in this scope."""
        with self._lock:
            return interface in self._registrations or interface in self._async_registrations

    def cleanup_scope(self) -> None:
        """Cleanup this scope, clearing all singleton instances.

        This method:
        1. Clears all registered singleton instances
        2. Resets internal state
        3. Marks scope as cleaned up

        After cleanup, the scope cannot be reused.
        """
        if self._is_cleaned_up:
            return

        with self._lock:
            # Clear all instances
            self._instances.clear()

            # Reset registration instances
            for reg in self._registrations.values():
                reg.instance = None
            for reg in self._async_registrations.values():
                reg.instance = None

            self._is_cleaned_up = True

        _unregister_scope(self)

    def get_registration_count(self) -> int:
        """Get number of registrations in this scope."""
        with self._lock:
            return len(self._registrations) + len(self._async_registrations)

    def __enter__(self) -> DIContainerScope:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - automatically cleanup."""
        self.cleanup_scope()


class ScopeContext:
    """Async context manager for managing DIContainerScope in async code.

    Example:
        async with ScopeContext() as scope:
            scope.register_singleton(MyService, lambda: MyService())
            service = await scope.resolve_async(MyService)
            # Scope auto-cleans on exit
    """

    def __init__(self, name: str | None = None) -> None:
        """Initialize scope context.

        Args:
            name: Optional name for the scope.
        """
        self._scope: DIContainerScope | None = None
        self._name = name

    async def __aenter__(self) -> DIContainerScope:
        """Enter async context."""
        self._scope = DIContainerScope(name=self._name)
        _current_scope.set(self._scope)
        return self._scope

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context - cleanup scope."""
        _current_scope.set(None)
        if self._scope is not None:
            self._scope.cleanup_scope()
            self._scope = None


def get_current_scope() -> DIContainerScope | None:
    """Get the current scope from context variable."""
    return _current_scope.get()


def get_or_create_scope(name: str | None = None) -> DIContainerScope:
    """Get current scope or create a new one.

    Args:
        name: Optional name for new scope.

    Returns:
        Current scope if exists, otherwise new scope.
    """
    scope = _current_scope.get()
    if scope is not None:
        return scope
    return DIContainerScope(name=name)


# Convenience functions for backward compatibility with global state
# These wrap global state access with scope-aware behavior

_global_state_resetters: dict[str, Callable[[], None]] = {}


def register_resetter(name: str, resetter: Callable[[], None]) -> None:
    """Register a global state resetter for centralized cleanup.

    Args:
        name: Unique name for the resetter.
        resetter: Function to call for resetting.
    """
    _global_state_resetters[name] = resetter


def reset_all_global_state() -> dict[str, bool]:
    """Reset all registered global state.

    Returns:
        Dict mapping resetter names to success status.
    """
    results = {}
    for name, resetter in _global_state_resetters.items():
        try:
            resetter()
            results[name] = True
        except (RuntimeError, ValueError):
            results[name] = False
    return results
