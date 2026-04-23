"""IRoleToolIntegration - Port interface for role-specific tool integrations.

ACGA 2.0 Section 6.3: KernelOne defines interface contracts, Cells provide implementations.

This port abstracts role-specific tool integrations that were previously imported
from Cells' internal modules. KernelOne defines the abstract interface, and
Cells provide concrete implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IRoleToolIntegration(ABC):
    """Abstract interface for role-specific tool integrations.

    Implementations are provided by Cells to maintain the KernelOne -> Cells
    dependency direction while keeping Cells-specific role semantics out of KernelOne.

    This port defines the common interface that all role tool integrations must implement:
    - PMToolIntegration
    - ArchitectToolIntegration
    - ChiefEngineerToolIntegration
    - DirectorToolIntegration
    - QAToolIntegration
    - ScoutToolIntegration

    Example:
        # KernelOne usage (abstract)
        from polaris.kernelone.ports import IRoleToolIntegration

        def get_tool_integration(port: IRoleToolIntegration, role: str, workspace: str):
            return port.get_role_integration(role, workspace)

        # Cells provides concrete implementation
        # See: polaris.cells.adapters.kernelone.role_tool_integration_adapter
    """

    @abstractmethod
    def get_role_integration(self, role: str, workspace: str) -> Any:
        """Factory method to get a role-specific tool integration.

        Args:
            role: Role identifier (pm, architect, chief_engineer, director, qa, scout)
            workspace: Path to the workspace directory

        Returns:
            Role-specific tool integration instance

        Raises:
            ValueError: If the role is not supported
        """
        ...

    @abstractmethod
    def get_supported_roles(self) -> tuple[str, ...]:
        """Return the tuple of supported role identifiers.

        Returns:
            Tuple of supported role names
        """
        ...

    @abstractmethod
    def enhance_role_prompt(self, role: str, base_prompt: str) -> str:
        """Enhance a role prompt with tool-specific instructions.

        Args:
            role: Role identifier
            base_prompt: Base prompt to enhance

        Returns:
            Enhanced prompt with tool instructions
        """
        ...
