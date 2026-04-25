"""IContainerPort - Port for dependency-injection container access.

ACGA 2.0 Section 6.3: KernelOne defines interface contracts,
infrastructure provides implementations.

This port abstracts the infrastructure ``DIContainer`` so that KernelOne
modules (e.g. ``polaris.kernelone.events.task_trace_events``) can resolve
services without reverse-importing ``polaris.infrastructure.di.container``.

Infrastructure provides the concrete container during bootstrap; KernelOne
consumes it through this stable Protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IContainerPort(Protocol):
    """Protocol for async service resolution from a DI container.

    Captures the minimal surface of the infrastructure ``DIContainer``
    that KernelOne needs at runtime.  The full container lifecycle
    (registration, factory wiring) remains in infrastructure/bootstrap;
    KernelOne only reads through this port.

    Dependency direction::

        KernelOne  ──defines──▸  IContainerPort (this port)
        Infrastructure  ──implements──▸  DIContainer
        Bootstrap  ──wires──▸  set_container_port(container)

    Example::

        from polaris.kernelone.ports.container import IContainerPort

        async def get_director(container: IContainerPort) -> Any:
            return await container.resolve_async("DirectorService")
    """

    async def resolve_async(self, interface: Any) -> Any:
        """Resolve a service by type or name.

        Args:
            interface: The service type (``type``) or string name to resolve.

        Returns:
            The resolved service instance.

        Raises:
            KeyError: If no registration exists for *interface*.
            RuntimeError: If resolution requires async but is called
                from a synchronous context.
        """
        ...

    def has_registration(self, interface: type) -> bool:
        """Check whether *interface* is registered.

        Args:
            interface: The service type to look up.

        Returns:
            ``True`` if a registration exists, ``False`` otherwise.
        """
        ...
