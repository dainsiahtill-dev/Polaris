"""Public service exports for `roles.profile` cell."""

from __future__ import annotations

from polaris.cells.roles.profile.internal.registry import RoleProfileRegistry, load_core_roles, registry
from polaris.cells.roles.profile.internal.schema import (
    Action,
    PermissionCheckResult,
    Policy,
    PolicyEffect,
    PromptFingerprint,
    Resource,
    ResourceType,
    RoleContextPolicy,
    RoleDataPolicy,
    RoleExecutionMode,
    RoleLibraryPolicy,
    RoleProfile,
    RoleProfileDict,
    RolePromptPolicy,
    RoleToolPolicy,
    RoleTurnRequest,
    RoleTurnResult,
    SequentialConfig,
    SequentialMode,
    SequentialStatsResult,
    SequentialTraceLevel,
    Subject,
    SubjectType,
    profile_from_dict,
    profile_to_dict,
)

__all__ = [
    "Action",
    "PermissionCheckResult",
    "Policy",
    "PolicyEffect",
    "PromptFingerprint",
    "Resource",
    "ResourceType",
    "RoleContextPolicy",
    "RoleDataPolicy",
    "RoleExecutionMode",
    "RoleLibraryPolicy",
    "RoleProfile",
    "RoleProfileDict",
    "RoleProfileRegistry",
    "RolePromptPolicy",
    "RoleToolPolicy",
    "RoleTurnRequest",
    "RoleTurnResult",
    "SequentialConfig",
    "SequentialMode",
    "SequentialStatsResult",
    "SequentialTraceLevel",
    "Subject",
    "SubjectType",
    "load_core_roles",
    "profile_from_dict",
    "profile_to_dict",
    "registry",
    "reset_role_profile_registry_for_test",
]


def reset_role_profile_registry_for_test() -> None:
    """Reset the global RoleProfileRegistry for test isolation.

    This function clears all registered profiles and loaded files from the
    global registry to ensure a clean state between tests.
    """
    registry.reset_for_testing()
