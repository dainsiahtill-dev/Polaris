"""Dynamic role extension for custom role template support.

This module provides:
- RoleTemplate: Dataclass for role definition
- RoleProfile: Created role profile from template
- DynamicRoleManager: Manages dynamic role templates

Architecture principle:
    Dynamic roles live in kernelone so that any cell can create
    custom roles based on templates without cross-cell import cycles.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.errors import KernelOneError


class DynamicRoleError(KernelOneError):
    """Base error for dynamic role operations."""


class TemplateNotFoundError(DynamicRoleError):
    """Raised when a template is not found."""

    def __init__(self, template_name: str) -> None:
        self.template_name = template_name
        super().__init__(f"Template not found: {template_name}")


class RoleAlreadyExistsError(DynamicRoleError):
    """Raised when attempting to register a duplicate template."""

    def __init__(self, template_name: str) -> None:
        self.template_name = template_name
        super().__init__(f"Template already registered: {template_name}")


@dataclass(frozen=True)
class RoleTemplate:
    """Role template definition.

    A template defines the base structure for creating roles with
    tools, prompts, constraints, and capabilities.

    Attributes:
        name: Unique template identifier
        description: Human-readable template description
        tools: Tuple of tool names available to this role
        prompts: Dict of prompt templates (key -> template string)
        constraints: Tuple of constraint strings for the role
        capabilities: Tuple of capability identifiers
    """

    name: str
    description: str
    tools: tuple[str, ...]
    prompts: Mapping[str, str]
    constraints: tuple[str, ...]
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Template name cannot be empty")
        if not self.description:
            raise ValueError("Template description cannot be empty")


@dataclass(frozen=True)
class RoleProfile:
    """Created role profile from template.

    A profile is an instantiated role with merged tools/prompts/constraints
    from a base template and customizations.

    Attributes:
        name: Name of the created role
        template_name: Source template name
        tools: Merged tuple of tool names
        prompts: Merged dict of prompt templates
        constraints: Merged tuple of constraints
        metadata: Additional role metadata
    """

    name: str
    template_name: str
    tools: tuple[str, ...]
    prompts: Mapping[str, str]
    constraints: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Role name cannot be empty")


class DynamicRoleManager:
    """Manages dynamic role templates and role creation.

    This manager provides:
    - Template registration and storage
    - Role creation from templates with customizations
    - Template discovery and retrieval

    Example:
        >>> manager = DynamicRoleManager()
        >>> manager.register_role(my_template)
        >>> profile = manager.create_role(
        ...     name="custom_pm",
        ...     base_role="pm",
        ...     customizations={"tools": ("tool_a", "tool_b")}
        ... )
    """

    def __init__(self) -> None:
        self._templates: dict[str, RoleTemplate] = {}

    def register_role(self, template: RoleTemplate) -> None:
        """Register a new role template.

        Args:
            template: RoleTemplate to register

        Raises:
            RoleAlreadyExistsError: If template name already registered
        """
        if template.name in self._templates:
            raise RoleAlreadyExistsError(template.name)
        self._templates[template.name] = template

    def create_role(
        self,
        name: str,
        base_role: str,
        customizations: dict[str, Any],
    ) -> RoleProfile:
        """Create a role profile from a base template with customizations.

        Args:
            name: Name for the created role
            base_role: Template name to base the role on
            customizations: Dict with keys like tools, prompts, constraints,
                          capabilities, metadata to override or extend

        Returns:
            RoleProfile instance

        Raises:
            TemplateNotFoundError: If base_role template not found
        """
        template = self._templates.get(base_role)
        if template is None:
            raise TemplateNotFoundError(base_role)

        # Merge tools
        base_tools = template.tools
        custom_tools = customizations.get("tools")
        merged_tools = tuple(set(base_tools) | set(custom_tools)) if custom_tools is not None else base_tools

        # Merge prompts
        base_prompts = dict(template.prompts)
        custom_prompts = customizations.get("prompts")
        if custom_prompts is not None:
            base_prompts.update(custom_prompts)
        merged_prompts = base_prompts

        # Merge constraints
        base_constraints = template.constraints
        custom_constraints = customizations.get("constraints")
        if custom_constraints is not None:
            merged_constraints = tuple(set(base_constraints) | set(custom_constraints))
        else:
            merged_constraints = base_constraints

        # Merge capabilities
        base_caps = template.capabilities
        custom_caps = customizations.get("capabilities")
        merged_caps = tuple(set(base_caps) | set(custom_caps)) if custom_caps is not None else base_caps

        # Build metadata
        metadata = dict(customizations.get("metadata", {}))
        metadata["created_from"] = base_role
        metadata["capabilities"] = merged_caps

        return RoleProfile(
            name=name,
            template_name=base_role,
            tools=merged_tools,
            prompts=merged_prompts,
            constraints=merged_constraints,
            metadata=metadata,
        )

    def list_templates(self) -> list[str]:
        """List all registered template names.

        Returns:
            Sorted list of template names
        """
        return sorted(self._templates.keys())

    def get_template(self, name: str) -> RoleTemplate | None:
        """Get a template by name.

        Args:
            name: Template name to retrieve

        Returns:
            RoleTemplate if found, None otherwise
        """
        return self._templates.get(name)

    def unregister_role(self, name: str) -> bool:
        """Unregister a role template.

        Args:
            name: Template name to remove

        Returns:
            True if template was removed, False if not found
        """
        if name in self._templates:
            del self._templates[name]
            return True
        return False


__all__ = [
    "DynamicRoleError",
    "DynamicRoleManager",
    "RoleAlreadyExistsError",
    "RoleProfile",
    "RoleTemplate",
    "TemplateNotFoundError",
]
