"""Tests for `roles.profile` public service facade."""

from __future__ import annotations

from polaris.cells.roles.profile.internal.registry import RoleProfileRegistry
from polaris.cells.roles.profile.internal.schema import (
    RoleContextPolicy,
    RoleDataPolicy,
    RoleLibraryPolicy,
    RoleProfile,
    RolePromptPolicy,
    RoleToolPolicy,
)
from polaris.cells.roles.profile.public.contracts import GetRoleProfileQueryV1, RoleProfileResultV1
from polaris.cells.roles.profile.public.service import get_profile


def _profile(role_id: str = "pm") -> RoleProfile:
    return RoleProfile(
        role_id=role_id,
        display_name="Project Manager",
        description="Plans governed task-market work",
        prompt_policy=RolePromptPolicy(core_template_id=role_id),
        tool_policy=RoleToolPolicy(whitelist=["task_market.publish"]),
        context_policy=RoleContextPolicy(),
        data_policy=RoleDataPolicy(data_subdir=role_id),
        library_policy=RoleLibraryPolicy(),
    )


def test_get_profile_returns_contract_result_from_registry() -> None:
    profile_registry = RoleProfileRegistry()
    profile_registry.register(_profile("pm"))

    result = get_profile(GetRoleProfileQueryV1(role_id="pm"), profile_registry=profile_registry)

    assert isinstance(result, RoleProfileResultV1)
    assert result.ok is True
    assert result.role_id == "pm"
    assert result.payload["role_id"] == "pm"
    assert result.payload["prompt_policy"]["core_template_id"] == "pm"
    assert result.payload["data_policy"]["data_subdir"] == "pm"


def test_get_profile_missing_role_returns_structured_contract_failure() -> None:
    result = get_profile(GetRoleProfileQueryV1(role_id="missing"), profile_registry=RoleProfileRegistry())

    assert result.ok is False
    assert result.role_id == "missing"
    assert result.error_code == "profile_not_found"
