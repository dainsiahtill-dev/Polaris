"""ILayoutResolverPort / IStorageRoots - Ports for storage layout resolution.

ACGA 2.0 Section 6.3: KernelOne defines interface contracts,
Cells provide implementations.

These ports abstract ``polaris.cells.storage.layout.resolve_polaris_roots``
and its ``PolarisStorageRoots`` result so that KernelOne modules
(e.g. ``polaris.kernelone.events.uep_publisher``) can resolve storage paths
without reverse-importing Cell modules.

The ``storage.layout`` Cell registers a concrete resolver during bootstrap;
KernelOne consumes it through these stable Protocols.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IStorageRoots(Protocol):
    """Protocol for resolved storage root paths.

    Represents the minimal set of root path attributes that KernelOne
    needs from a storage-layout resolution result.  The full
    ``PolarisStorageRoots`` dataclass lives in the ``storage.layout`` Cell
    and satisfies this protocol structurally.

    Example::

        def get_events_dir(roots: IStorageRoots) -> str:
            import os
            return os.path.join(roots.runtime_root, "events")
    """

    @property
    def runtime_root(self) -> str:
        """Absolute path to the project runtime root directory.

        Typical value: ``<runtime_base>/.polaris/projects/<key>/runtime``
        """
        ...

    @property
    def workspace_abs(self) -> str:
        """Absolute path to the workspace directory."""
        ...


@runtime_checkable
class ILayoutResolverPort(Protocol):
    """Callable protocol for resolving storage layout roots.

    Abstracts ``polaris.cells.storage.layout.resolve_polaris_roots`` so that
    KernelOne can resolve workspace paths without importing Cell modules.

    Dependency direction::

        KernelOne  ──defines──▸  ILayoutResolverPort (this port)
        Cells (storage.layout)  ──implements──▸  resolve_polaris_roots
        Bootstrap  ──wires──▸  set_layout_resolver(resolve_polaris_roots)

    Example::

        from polaris.kernelone.ports.layout import ILayoutResolverPort

        def get_runtime_root(resolver: ILayoutResolverPort, workspace: str) -> str:
            roots = resolver(workspace)
            return roots.runtime_root
    """

    def __call__(self, workspace: str) -> IStorageRoots:
        """Resolve storage roots for the given workspace.

        Args:
            workspace: Path to the workspace directory.

        Returns:
            An object satisfying :class:`IStorageRoots` with at least
            ``runtime_root`` and ``workspace_abs`` attributes.

        Raises:
            RuntimeError: If the workspace cannot be resolved.
            ValueError: If *workspace* is empty or invalid.
        """
        ...
