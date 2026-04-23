"""IRoleProvider - Port interface for role normalization.

ACGA 2.0 Section 6.3: KernelOne defines interface contracts, Cells provide implementations.

This port abstracts role alias normalization, which is a Polaris-specific
concern that should not leak into KernelOne core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IRoleProvider(ABC):
    """Abstract interface for role-related operations.

    Implementations are provided by Cells to maintain the KernelOne → Cells
    dependency direction while keeping Cells-specific logic out of KernelOne.

    Example:
        # KernelOne usage (abstract)
        from polaris.kernelone.ports import IRoleProvider

        def process_role(port: IRoleProvider, role: str) -> str:
            return port.normalize_role_alias(role)

        # Cells provides concrete implementation
        # See: polaris.cells.adapters.kernelone.role_provider_adapter
    """

    @abstractmethod
    def normalize_role_alias(self, role: str) -> str:
        """Resolve a role identifier to its canonical form.

        Applies Polaris role alias mapping first, then lowercases and strips
        whitespace. If no alias matches, returns the input lowercased.

        Args:
            role: A role identifier (may be canonical, aliased, or arbitrary).

        Returns:
            The canonical role string (lowercase, stripped).

        Example:
            >>> adapter = RoleProviderAdapter()
            >>> adapter.normalize_role_alias("auditor")
            'qa'
            >>> adapter.normalize_role_alias("ARCHITECT")
            'architect'
        """
        ...

    @abstractmethod
    def get_role_aliases(self) -> dict[str, str]:
        """Return the complete role alias mapping table.

        Returns:
            Dict mapping alias keys to canonical role IDs.
        """
        ...
