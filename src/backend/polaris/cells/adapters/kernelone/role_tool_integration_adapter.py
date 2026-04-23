"""RoleToolIntegrationAdapter - Implements IRoleToolIntegration using Cells' role integrations.

ACGA 2.0 Section 6.3: Cells provide implementations of KernelOne port interfaces.

This adapter delegates to the Cells' role_integrations module, which contains
Polaris-specific role tool integration logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.kernelone.ports.role_tool_integration import IRoleToolIntegration

if TYPE_CHECKING:
    pass


class RoleToolIntegrationAdapter(IRoleToolIntegration):
    """Adapter that implements IRoleToolIntegration using Cells' role_integrations module.

    This adapter maintains the KernelOne -> Cells dependency direction by implementing
    the abstract port interface defined in KernelOne.

    Supported roles:
        - pm: Project Manager (尚书令)
        - architect: Architect (中书令)
        - chief_engineer: Chief Engineer (工部尚书)
        - director: Director (工部侍郎)
        - qa: Quality Assurance (门下侍中)
        - scout: Scout (探子)

    Example:
        >>> from polaris.cells.adapters.kernelone import RoleToolIntegrationAdapter
        >>> adapter = RoleToolIntegrationAdapter()
        >>> integration = adapter.get_role_integration("pm", "/path/to/project")
        >>> print(integration.get_system_prompt()[:100])
        '你是 PM（尚书令），负责项目管理与规划...'
        >>> adapter.get_supported_roles()
        ('pm', 'architect', 'chief_engineer', 'director', 'qa', 'scout')
    """

    # Supported role identifiers
    _SUPPORTED_ROLES = (
        "pm",
        "architect",
        "chief_engineer",
        "director",
        "qa",
        "scout",
    )

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
        if role not in self._SUPPORTED_ROLES:
            raise ValueError(f"Unknown role: {role}. Supported: {list(self._SUPPORTED_ROLES)}")

        # Lazy import to avoid circular dependencies
        from polaris.cells.llm.tool_runtime.internal.role_integrations import (
            ROLE_TOOL_INTEGRATIONS,
        )

        integration_class: type = ROLE_TOOL_INTEGRATIONS[role]
        return integration_class(workspace)

    def get_supported_roles(self) -> tuple[str, ...]:
        """Return the tuple of supported role identifiers.

        Returns:
            Tuple of supported role names
        """
        return self._SUPPORTED_ROLES

    def enhance_role_prompt(self, role: str, base_prompt: str) -> str:
        """Enhance a role prompt with tool-specific instructions.

        Args:
            role: Role identifier
            base_prompt: Base prompt to enhance

        Returns:
            Enhanced prompt with tool instructions

        Raises:
            ValueError: If the role is not supported
        """
        if role not in self._SUPPORTED_ROLES:
            raise ValueError(f"Unknown role: {role}. Supported: {list(self._SUPPORTED_ROLES)}")

        # Get the role integration for this workspace
        # Using "." as a temporary workspace since we're just getting the system prompt
        integration = self.get_role_integration(role, ".")

        return f"""{integration.get_system_prompt()}

---

{base_prompt}
"""
