"""Tests: scout_probe is whitelisted for director and pm profiles (UTF-8)."""

from __future__ import annotations

from polaris.cells.roles.profile.internal.builtin_profiles import BUILTIN_PROFILES


def _whitelist(role: str) -> list[str]:
    """Return the tool whitelist for a builtin profile dict by role_id."""
    for profile in BUILTIN_PROFILES:
        if profile.get("role_id") == role:
            return list(profile.get("tool_policy", {}).get("whitelist", []))
    raise KeyError(f"No builtin profile found for role: {role!r}")


def test_director_whitelist_contains_scout_probe() -> None:
    assert "scout_probe" in _whitelist("director"), "director tool_policy.whitelist must include 'scout_probe'"


def test_pm_whitelist_contains_scout_probe() -> None:
    assert "scout_probe" in _whitelist("pm"), "pm tool_policy.whitelist must include 'scout_probe'"


def test_director_and_pm_can_call_scout_probe() -> None:
    for role in ("director", "pm"):
        wl = _whitelist(role)
        assert "scout_probe" in wl, f"{role} tool_policy.whitelist must contain 'scout_probe'"
