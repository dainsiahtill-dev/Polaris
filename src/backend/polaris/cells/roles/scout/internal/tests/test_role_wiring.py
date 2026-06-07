"""Tests: scout_probe is in the LOADED whitelist (from core_roles.yaml SSOT)
for every consumer role (pm, architect, chief_engineer, director, qa), and
is intentionally ABSENT from the scout role's own whitelist.

Uses ``load_from_yaml`` on the real core_roles.yaml file, which is the same
code-path exercised by ``load_core_roles()`` — without requiring a live
``llm_config.json`` (model-binding enrichment only happens in
``load_core_roles``; raw ``load_from_yaml`` skips it).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.profile.public.service import RoleProfileRegistry

# Absolute path to the SSOT config consumed at runtime by load_core_roles().
# parents[3] = cells/roles  →  / "profile" / "config" / "roles" / "core_roles.yaml"
_CORE_ROLES_YAML: Path = (
    Path(__file__).resolve().parents[3]  # cells/roles
    / "profile"
    / "config"
    / "roles"
    / "core_roles.yaml"
)

# Roles that MUST have scout_probe in their loaded whitelist.
_CONSUMER_ROLES: tuple[str, ...] = ("pm", "architect", "chief_engineer", "director", "qa")


@pytest.fixture(scope="module")
def loaded_registry() -> RoleProfileRegistry:
    """Fresh registry populated from the real core_roles.yaml SSOT."""
    assert _CORE_ROLES_YAML.exists(), f"SSOT config missing: {_CORE_ROLES_YAML}"
    reg = RoleProfileRegistry()
    count = reg.load_from_yaml(_CORE_ROLES_YAML)
    assert count >= len(_CONSUMER_ROLES), f"Expected at least {len(_CONSUMER_ROLES)} roles, got {count}"
    return reg


@pytest.mark.parametrize("role_id", _CONSUMER_ROLES)
def test_consumer_role_whitelist_contains_scout_probe(
    loaded_registry: RoleProfileRegistry,
    role_id: str,
) -> None:
    """After loading core_roles.yaml, each consumer role's whitelist must
    contain 'scout_probe'."""
    profile = loaded_registry.get_profile(role_id)
    assert profile is not None, f"Role '{role_id}' not found in loaded registry"
    whitelist = profile.tool_policy.whitelist
    assert "scout_probe" in whitelist, (
        f"Role '{role_id}': tool_policy.whitelist does not contain 'scout_probe'. Current whitelist: {whitelist}"
    )


def test_scout_role_whitelist_does_not_contain_scout_probe(
    loaded_registry: RoleProfileRegistry,
) -> None:
    """The scout role itself must NOT have scout_probe in its whitelist
    (self-call would be pointless)."""
    profile = loaded_registry.get_profile("scout")
    assert profile is not None, "Scout role not found in loaded registry"
    whitelist = profile.tool_policy.whitelist
    assert "scout_probe" not in whitelist, (
        f"scout tool_policy.whitelist must NOT contain 'scout_probe'. Current whitelist: {whitelist}"
    )
