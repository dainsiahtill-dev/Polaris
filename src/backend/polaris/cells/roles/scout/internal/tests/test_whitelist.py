"""Tests: scout_probe is whitelisted for director and pm profiles (UTF-8)."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.profile.public.service import RoleProfileRegistry

_CORE_ROLES_YAML: Path = Path(__file__).resolve().parents[3] / "profile" / "config" / "roles" / "core_roles.yaml"


def _whitelist(role: str) -> list[str]:
    """Return the tool whitelist through the public role-profile boundary."""
    assert _CORE_ROLES_YAML.exists(), f"SSOT config missing: {_CORE_ROLES_YAML}"
    registry = RoleProfileRegistry()
    registry.load_from_yaml(_CORE_ROLES_YAML)
    profile = registry.get_profile(role)
    if profile is None:
        raise KeyError(f"No profile found for role: {role!r}")
    return list(profile.tool_policy.whitelist)


def test_director_whitelist_contains_scout_probe() -> None:
    assert "scout_probe" in _whitelist("director"), "director tool_policy.whitelist must include 'scout_probe'"


def test_pm_whitelist_contains_scout_probe() -> None:
    assert "scout_probe" in _whitelist("pm"), "pm tool_policy.whitelist must include 'scout_probe'"


def test_director_and_pm_can_call_scout_probe() -> None:
    for role in ("director", "pm"):
        wl = _whitelist(role)
        assert "scout_probe" in wl, f"{role} tool_policy.whitelist must contain 'scout_probe'"
