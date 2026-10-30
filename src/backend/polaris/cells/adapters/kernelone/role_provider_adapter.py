"""RoleProviderAdapter - Implements IRoleProvider using Cells' role_alias module.

ACGA 2.0 Section 6.3: Cells provide implementations of KernelOne port interfaces.

This adapter delegates to the Cells' role_alias module, which contains Polaris-specific
role normalization logic.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.public.role_alias import (
    ROLE_ALIASES,
    normalize_role_alias as _normalize,
)
from polaris.kernelone.ports.role_provider import IRoleProvider


class RoleProviderAdapter(IRoleProvider):
    """Adapter that implements IRoleProvider using Cells' role_alias module.

    This adapter maintains the KernelOne → Cells dependency direction by
    implementing the abstract port interface defined in KernelOne.

    Example:
        >>> from polaris.cells.adapters.kernelone import RoleProviderAdapter
        >>> adapter = RoleProviderAdapter()
        >>> adapter.normalize_role_alias("auditor")
        'qa'
        >>> adapter.normalize_role_alias("ARCHITECT")
        'architect'
        >>> adapter.get_role_aliases()
        {'docs': 'architect', 'auditor': 'qa'}
    """

    def normalize_role_alias(self, role: str) -> str:
        """Resolve a role identifier to its canonical form.

        Delegates to Cells' role_alias module to maintain KernelOne purity.

        Args:
            role: A role identifier (may be canonical, aliased, or arbitrary).

        Returns:
            The canonical role string (lowercase, stripped).
        """
        return _normalize(role)

    def get_role_aliases(self) -> dict[str, str]:
        """Return the complete role alias mapping table.

        Returns:
            Dict mapping alias keys to canonical role IDs.
        """
        return dict(ROLE_ALIASES)
