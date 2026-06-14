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
from polaris.cells.roles.profile.public.contracts import GetRoleProfileQueryV1, RoleProfileResultV1


def get_profile(
    query: GetRoleProfileQueryV1,
    *,
    profile_registry: RoleProfileRegistry | None = None,
) -> RoleProfileResultV1:
    """Return one role profile through the public query/result contract."""
    if not isinstance(query, GetRoleProfileQueryV1):
        raise TypeError("query must be a GetRoleProfileQueryV1")

    source = profile_registry or registry
    profile = source.get_profile(query.role_id)
    if profile is None:
        return RoleProfileResultV1(
            ok=False,
            role_id=query.role_id,
            error_code="profile_not_found",
            error_message=f"role profile {query.role_id!r} was not found",
        )

    payload = profile_to_dict(profile)
    payload["profile_fingerprint"] = profile.profile_fingerprint
    payload["profile_ref"] = f"roles.profile:{profile.role_id}:profile:{profile.profile_fingerprint}"
    return RoleProfileResultV1(ok=True, role_id=profile.role_id, payload=payload)


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
    "get_profile",
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
