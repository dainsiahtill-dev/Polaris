"""IFileSystemAdapterFactory - Port for filesystem adapter provisioning.

ACGA 2.0 Section 6.3: KernelOne defines interface contracts,
infrastructure provides implementations.

This port abstracts the creation of default KernelFileSystemAdapter instances
so that ``polaris.kernelone.fs.registry`` does not need to reverse-import
``polaris.infrastructure.storage.local_fs_adapter.LocalFileSystemAdapter``.

Infrastructure registers a concrete factory during bootstrap; KernelOne
consumes it through this stable interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from polaris.kernelone.fs.contracts import KernelFileSystemAdapter


@runtime_checkable
class IFileSystemAdapterFactory(Protocol):
    """Factory protocol for creating default KernelFileSystemAdapter instances.

    Infrastructure registers a concrete callable that satisfies this protocol
    during bootstrap.  ``polaris.kernelone.fs.registry`` calls the factory in
    its lazy-initialisation path instead of importing a concrete adapter class.

    Dependency direction::

        KernelOne  ──defines──▸  IFileSystemAdapterFactory (this port)
        Infrastructure  ──implements──▸  lambda: LocalFileSystemAdapter()
        Bootstrap  ──wires──▸  set_adapter_factory(factory)

    Example::

        from polaris.kernelone.ports.storage import IFileSystemAdapterFactory

        # Infrastructure provides:
        factory: IFileSystemAdapterFactory = lambda: LocalFileSystemAdapter()

        # Bootstrap wires:
        from polaris.kernelone.fs.registry import set_adapter_factory
        set_adapter_factory(factory)
    """

    def __call__(self) -> KernelFileSystemAdapter:
        """Create and return a new KernelFileSystemAdapter instance.

        Returns:
            A freshly constructed adapter satisfying the
            ``KernelFileSystemAdapter`` protocol.

        Raises:
            RuntimeError: If the adapter cannot be created (e.g. missing
                configuration or unavailable storage backend).
        """
        ...
