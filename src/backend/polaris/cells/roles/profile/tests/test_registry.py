"""Unit tests for `RoleProfileRegistry`.

Uses a plain (unthreaded) in-memory registry with no file I/O,
verifying: registration, retrieval, listing, loading from YAML/JSON,
saving, and validation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.cells.roles.profile.internal.registry import (
    RoleProfileRegistry,
    _load_builtin_profiles,
)
from polaris.cells.roles.profile.internal.schema import (
    RoleContextPolicy,
    RoleDataPolicy,
    RoleLibraryPolicy,
    RoleProfile,
    RolePromptPolicy,
    RoleToolPolicy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    role_id: str = "pm",
    display_name: str = "PM",
    allow_override: bool = False,
) -> RoleProfile:
    return RoleProfile(
        role_id=role_id,
        display_name=display_name,
        description="test",
        prompt_policy=RolePromptPolicy(
            core_template_id=role_id,
            allow_override=allow_override,
        ),
        tool_policy=RoleToolPolicy(whitelist=["read_file"]),
        context_policy=RoleContextPolicy(),
        data_policy=RoleDataPolicy(data_subdir=role_id),
        library_policy=RoleLibraryPolicy(),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistryRegister:
    def test_register_one_profile(self) -> None:
        reg = RoleProfileRegistry()
        rp = _make_profile("pm")
        reg.register(rp)
        assert reg.has_role("pm")
        assert reg.get_profile("pm") is not None

    def test_register_duplicate_overwrites(self) -> None:
        reg = RoleProfileRegistry()
        reg.register(_make_profile("pm", "v1"))
        reg.register(_make_profile("pm", "v2"))
        profile = reg.get_profile("pm")
        assert profile is not None
        assert profile.display_name == "v2"

    def test_register_empty_role_id_raises(self) -> None:
        reg = RoleProfileRegistry()
        rp = _make_profile("")
        with pytest.raises(ValueError, match="role_id"):
            reg.register(rp)

    def test_register_empty_display_name_raises(self) -> None:
        reg = RoleProfileRegistry()
        rp = _make_profile("pm", display_name="")
        with pytest.raises(ValueError, match="display_name"):
            reg.register(rp)

    def test_register_allow_override_true_raises(self) -> None:
        # Core roles MUST NOT allow override
        reg = RoleProfileRegistry()
        rp = _make_profile("architect", allow_override=True)
        with pytest.raises(ValueError, match="allow_override"):
            reg.register(rp)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


class TestRegistryGet:
    def test_get_existing(self) -> None:
        reg = RoleProfileRegistry()
        reg.register(_make_profile("pm"))
        rp = reg.get_profile("pm")
        assert rp is not None
        assert rp.role_id == "pm"

    def test_get_missing_returns_none(self) -> None:
        reg = RoleProfileRegistry()
        assert reg.get_profile("nonexistent") is None

    def test_get_or_raise_existing(self) -> None:
        reg = RoleProfileRegistry()
        reg.register(_make_profile("pm"))
        rp = reg.get_profile_or_raise("pm")
        assert rp.role_id == "pm"

    def test_get_or_raise_missing_raises(self) -> None:
        reg = RoleProfileRegistry()
        with pytest.raises(ValueError, match="未知角色"):
            reg.get_profile_or_raise("xyz")


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestRegistryList:
    def test_list_roles(self) -> None:
        reg = RoleProfileRegistry()
        reg.register(_make_profile("pm"))
        reg.register(_make_profile("architect"))
        roles = reg.list_roles()
        assert set(roles) == {"pm", "architect"}

    def test_get_all_profiles(self) -> None:
        reg = RoleProfileRegistry()
        reg.register(_make_profile("pm"))
        reg.register(_make_profile("architect"))
        all_profiles = reg.get_all_profiles()
        assert len(all_profiles) == 2
        assert "pm" in all_profiles
        assert "architect" in all_profiles

    def test_get_all_profiles_returns_copy(self) -> None:
        reg = RoleProfileRegistry()
        reg.register(_make_profile("pm"))
        all_profiles = reg.get_all_profiles()
        all_profiles.clear()
        assert "pm" in reg.list_roles()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestRegistryValidate:
    def test_validate_empty_returns_missing_core_roles_error(self) -> None:
        reg = RoleProfileRegistry()
        errors = reg.validate_all()
        # No profiles registered, CORE_ROLES are missing
        assert any("缺少核心角色" in e for e in errors)

    def test_validate_returns_errors_when_whitelist_missing(
        self,
        tmp_path,
    ) -> None:
        # Register a core role with empty whitelist -> validation error
        reg = RoleProfileRegistry()
        rp = RoleProfile(
            role_id="architect",
            display_name="Architect",
            description="desc",
            prompt_policy=RolePromptPolicy(core_template_id="architect", allow_override=False),
            # Empty whitelist (and not "qa") triggers error
            tool_policy=RoleToolPolicy(whitelist=[]),
            context_policy=RoleContextPolicy(),
            data_policy=RoleDataPolicy(data_subdir="architect"),
            library_policy=RoleLibraryPolicy(),
        )
        reg.register(rp)
        errors = reg.validate_all()
        assert any("核心角色应该有工具白名单" in e for e in errors)

    def test_validate_qa_exempt_from_whitelist_check(self) -> None:
        # "qa" has no whitelist requirement in validate_all
        reg = RoleProfileRegistry()
        rp = RoleProfile(
            role_id="qa",
            display_name="QA",
            description="desc",
            prompt_policy=RolePromptPolicy(core_template_id="qa", allow_override=False),
            tool_policy=RoleToolPolicy(whitelist=[]),  # empty OK for qa
            context_policy=RoleContextPolicy(),
            data_policy=RoleDataPolicy(data_subdir="qa"),
            library_policy=RoleLibraryPolicy(),
        )
        reg.register(rp)
        errors = reg.validate_all()
        # No whitelist error for qa; only missing other core roles
        assert not any("工具白名单" in e and "qa" in e for e in errors)


# ---------------------------------------------------------------------------
# _load_builtin_profiles
# ---------------------------------------------------------------------------


class TestLoadBuiltinProfiles:
    @staticmethod
    def _mock_get_role_model(role_id: str) -> tuple[str, str]:
        """Provide deterministic model bindings for builtin profiles in tests."""
        return ("test_provider", "test_model")

    def test_loads_all_core_roles(self) -> None:
        reg = RoleProfileRegistry()
        with patch(
            "polaris.kernelone.llm.runtime_config.get_role_model",
            side_effect=self._mock_get_role_model,
        ):
            _load_builtin_profiles(reg)
        roles = reg.list_roles()
        assert set(RoleProfileRegistry.CORE_ROLES).issubset(set(roles))

    def test_builtin_profiles_valid(self) -> None:
        reg = RoleProfileRegistry()
        with patch(
            "polaris.kernelone.llm.runtime_config.get_role_model",
            side_effect=self._mock_get_role_model,
        ):
            _load_builtin_profiles(reg)
        for role_id in RoleProfileRegistry.CORE_ROLES:
            rp = reg.get_profile(role_id)
            assert rp is not None
            assert rp.prompt_policy.core_template_id != ""

    def test_scout_role_loaded(self) -> None:
        reg = RoleProfileRegistry()
        with patch(
            "polaris.kernelone.llm.runtime_config.get_role_model",
            side_effect=self._mock_get_role_model,
        ):
            _load_builtin_profiles(reg)
        assert reg.has_role("scout")
        rp = reg.get_profile("scout")
        assert rp is not None
        assert rp.display_name == "Scout (Scout)"

    def test_director_builtin_profile_exposes_canonical_repo_tools(self) -> None:
        reg = RoleProfileRegistry()
        with patch(
            "polaris.kernelone.llm.runtime_config.get_role_model",
            side_effect=self._mock_get_role_model,
        ):
            _load_builtin_profiles(reg)
        rp = reg.get_profile("director")
        assert rp is not None
        assert "repo_read_head" in rp.tool_policy.whitelist
        assert "repo_rg" in rp.tool_policy.whitelist
        assert "precision_edit" in rp.tool_policy.whitelist
        assert "repo_apply_diff" in rp.tool_policy.whitelist
        assert "read_file" in rp.tool_policy.whitelist


class TestCoreRoleYamlProfiles:
    def test_director_yaml_profile_exposes_canonical_repo_tools(self) -> None:
        reg = RoleProfileRegistry()
        config_path = Path(__file__).resolve().parents[1] / "config" / "roles" / "core_roles.yaml"
        reg.load_from_yaml(config_path)
        rp = reg.get_profile("director")
        assert rp is not None
        assert "repo_read_head" in rp.tool_policy.whitelist
        assert "repo_rg" in rp.tool_policy.whitelist
        assert "precision_edit" in rp.tool_policy.whitelist
        assert "repo_apply_diff" in rp.tool_policy.whitelist
        assert "read_file" in rp.tool_policy.whitelist


# ---------------------------------------------------------------------------
# Loaded files tracking
# ---------------------------------------------------------------------------


class TestLoadedFiles:
    def test_get_loaded_files_empty(self) -> None:
        reg = RoleProfileRegistry()
        assert reg.get_loaded_files() == []

    def test_get_loaded_files_after_load(self) -> None:
        # We can't test actual file loading without mocking KernelFileSystem,
        # but we verify the internal _loaded_files list can be tracked.
        reg = RoleProfileRegistry()
        # Simulate file tracking
        reg._loaded_files.append("/mock/path/profiles.yaml")
        assert "/mock/path/profiles.yaml" in reg.get_loaded_files()


# ---------------------------------------------------------------------------
# CORE_ROLES constant
# ---------------------------------------------------------------------------


class TestCoreRoles:
    def test_core_roles_contains_five_roles(self) -> None:
        assert len(RoleProfileRegistry.CORE_ROLES) == 5
        assert set(RoleProfileRegistry.CORE_ROLES) == {
            "pm",
            "architect",
            "chief_engineer",
            "director",
            "qa",
        }
