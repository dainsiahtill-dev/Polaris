# ruff: noqa: E402
"""Tests for polaris.domain.entities.capability module.

Covers:
- Role and Capability enums
- DEFAULT_ROLE_CAPABILITIES matrix
- RoleConfig dataclass (defaults, has_capability, can_use_tool)
- CapabilityResult and Skill dataclasses
- CapabilityChecker (all check_* methods)
- get_role_config, check_action_allowed, validate_director_action
- ROLE_HOST_CAPABILITIES and get_role_capabilities
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.entities.capability import (
    DEFAULT_ROLE_CAPABILITIES,
    ROLE_HOST_CAPABILITIES,
    Capability,
    CapabilityChecker,
    CapabilityResult,
    Role,
    RoleConfig,
    Skill,
    _get_role_enum,
    check_action_allowed,
    get_role_capabilities,
    get_role_config,
    validate_director_action,
)

# =============================================================================
# Enums
# =============================================================================


class TestRoleEnum:
    def test_role_values(self) -> None:
        assert Role.DIRECTOR.value == "director"
        assert Role.REVIEWER.value == "reviewer"
        assert Role.QA.value == "qa"
        assert Role.PM.value == "pm"
        assert Role.SYSTEM.value == "system"

    def test_role_from_string(self) -> None:
        assert Role("director") == Role.DIRECTOR
        assert Role("qa") == Role.QA

    def test_role_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            Role("invalid")


class TestCapabilityEnum:
    def test_capability_values(self) -> None:
        assert Capability.READ_FILES.value == "read_files"
        assert Capability.WRITE_FILES.value == "write_files"
        assert Capability.DELETE_FILES.value == "delete_files"
        assert Capability.EXECUTE_TOOLS.value == "execute_tools"
        assert Capability.EXECUTE_COMMANDS.value == "execute_commands"
        assert Capability.EXECUTE_TESTS.value == "execute_tests"
        assert Capability.APPLY_PATCHES.value == "apply_patches"
        assert Capability.CREATE_FILES.value == "create_files"
        assert Capability.MANAGE_WORKERS.value == "manage_workers"
        assert Capability.VIEW_METRICS.value == "view_metrics"


# =============================================================================
# DEFAULT_ROLE_CAPABILITIES
# =============================================================================


class TestDefaultRoleCapabilities:
    def test_director_has_expected_caps(self) -> None:
        caps = DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]
        assert Capability.READ_FILES in caps
        assert Capability.WRITE_FILES in caps
        assert Capability.DELETE_FILES not in caps

    def test_system_has_all_caps(self) -> None:
        caps = DEFAULT_ROLE_CAPABILITIES[Role.SYSTEM]
        all_caps = set(Capability)
        assert caps == all_caps

    def test_reviewer_lacks_write(self) -> None:
        caps = DEFAULT_ROLE_CAPABILITIES[Role.REVIEWER]
        assert Capability.READ_FILES in caps
        assert Capability.WRITE_FILES not in caps

    def test_qa_has_test_capability(self) -> None:
        caps = DEFAULT_ROLE_CAPABILITIES[Role.QA]
        assert Capability.EXECUTE_TESTS in caps

    def test_pm_has_manage_workers(self) -> None:
        caps = DEFAULT_ROLE_CAPABILITIES[Role.PM]
        assert Capability.MANAGE_WORKERS in caps

    def test_all_roles_present(self) -> None:
        assert set(DEFAULT_ROLE_CAPABILITIES.keys()) == set(Role)


# =============================================================================
# RoleConfig
# =============================================================================


class TestRoleConfig:
    def test_defaults(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        assert rc.capabilities == DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]
        assert rc.allowed_tools == set()
        assert rc.blocked_tools == set()
        assert rc.max_files_per_action == 3
        assert rc.max_lines_per_action == 500

    def test_custom_capabilities_preserved(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, capabilities={Capability.READ_FILES})
        assert rc.capabilities == {Capability.READ_FILES}

    def test_empty_capabilities_filled_from_defaults(self) -> None:
        rc = RoleConfig(role=Role.QA, capabilities=set())
        assert rc.capabilities == DEFAULT_ROLE_CAPABILITIES[Role.QA]

    def test_has_capability_true(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        assert rc.has_capability(Capability.READ_FILES) is True

    def test_has_capability_false(self) -> None:
        rc = RoleConfig(role=Role.REVIEWER)
        assert rc.has_capability(Capability.WRITE_FILES) is False

    def test_can_use_tool_no_restrictions(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        assert rc.can_use_tool("any_tool") is True

    def test_can_use_tool_blocked(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, blocked_tools={"bad_tool"})
        assert rc.can_use_tool("bad_tool") is False

    def test_can_use_tool_allowed_list_exclusive(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, allowed_tools={"good_tool"})
        assert rc.can_use_tool("good_tool") is True
        assert rc.can_use_tool("other_tool") is False

    def test_can_use_tool_allowed_plus_blocked(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, allowed_tools={"good_tool"}, blocked_tools={"good_tool"})
        assert rc.can_use_tool("good_tool") is False

    def test_role_config_is_frozen(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        with pytest.raises(AttributeError):
            rc.max_files_per_action = 10

    def test_role_config_unhashable_due_to_set_fields(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        with pytest.raises(TypeError, match="unhashable"):
            hash(rc)


# =============================================================================
# CapabilityResult and Skill
# =============================================================================


class TestCapabilityResult:
    def test_defaults(self) -> None:
        cr = CapabilityResult(allowed=True)
        assert cr.reason == ""
        assert cr.mode == "strict"

    def test_full_construction(self) -> None:
        cr = CapabilityResult(allowed=False, reason="nope", mode="advisory")
        assert cr.allowed is False
        assert cr.reason == "nope"
        assert cr.mode == "advisory"


class TestSkill:
    def test_defaults(self) -> None:
        skill = Skill(id="s1", name="Skill1", description="desc")
        assert skill.prompt_fragments == {}
        assert skill.required_capabilities == set()
        assert skill.allowed_tools == set()
        assert skill.blocked_tools == set()
        assert skill.context_files == []
        assert skill.tags == []

    def test_full_construction(self) -> None:
        skill = Skill(
            id="s1",
            name="Skill1",
            description="desc",
            prompt_fragments={"a": "b"},
            required_capabilities={Capability.READ_FILES},
            allowed_tools={"t1"},
            blocked_tools={"t2"},
            context_files=["c1"],
            tags=["tag1"],
        )
        assert skill.prompt_fragments == {"a": "b"}


# =============================================================================
# CapabilityChecker
# =============================================================================


class TestCapabilityCheckerRead:
    def test_read_allowed(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(rc)
        result = checker.check_read(["file.py"])
        assert result.allowed is True

    def test_read_denied(self) -> None:
        rc = RoleConfig(role=Role.REVIEWER, capabilities={Capability.WRITE_FILES})
        checker = CapabilityChecker(rc)
        result = checker.check_read(["file.py"])
        assert result.allowed is False
        assert "cannot read files" in result.reason


class TestCapabilityCheckerWrite:
    def test_write_allowed(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(rc)
        result = checker.check_write(["file.py"])
        assert result.allowed is True

    def test_write_denied_no_capability(self) -> None:
        rc = RoleConfig(role=Role.REVIEWER)
        checker = CapabilityChecker(rc)
        result = checker.check_write(["file.py"])
        assert result.allowed is False

    def test_write_denied_too_many_files(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, max_files_per_action=2)
        checker = CapabilityChecker(rc)
        result = checker.check_write(["a.py", "b.py", "c.py"])
        assert result.allowed is False
        assert "Too many files" in result.reason

    def test_write_allowed_at_limit(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, max_files_per_action=2)
        checker = CapabilityChecker(rc)
        result = checker.check_write(["a.py", "b.py"])
        assert result.allowed is True


class TestCapabilityCheckerDelete:
    def test_delete_allowed(self) -> None:
        rc = RoleConfig(role=Role.SYSTEM)
        checker = CapabilityChecker(rc, policy={"write_tools": {"allow_delete": True}})
        result = checker.check_delete(["file.py"])
        assert result.allowed is True

    def test_delete_denied_no_capability(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(rc)
        result = checker.check_delete(["file.py"])
        assert result.allowed is False
        assert "cannot delete files" in result.reason

    def test_delete_denied_by_policy(self) -> None:
        rc = RoleConfig(role=Role.SYSTEM)
        checker = CapabilityChecker(rc, policy={"write_tools": {"allow_delete": False}})
        result = checker.check_delete(["file.py"])
        assert result.allowed is False
        assert "disabled by policy" in result.reason

    def test_delete_denied_no_policy(self) -> None:
        rc = RoleConfig(role=Role.SYSTEM)
        checker = CapabilityChecker(rc)
        result = checker.check_delete(["file.py"])
        assert result.allowed is False
        assert "disabled by policy" in result.reason


class TestCapabilityCheckerCreate:
    def test_create_allowed(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(rc)
        result = checker.check_create(["file.py"])
        assert result.allowed is True

    def test_create_denied(self) -> None:
        rc = RoleConfig(role=Role.REVIEWER)
        checker = CapabilityChecker(rc)
        result = checker.check_create(["file.py"])
        assert result.allowed is False


class TestCapabilityCheckerTool:
    def test_tool_allowed(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(rc)
        result = checker.check_tool("ls")
        assert result.allowed is True

    def test_tool_denied_no_capability(self) -> None:
        rc = RoleConfig(role=Role.REVIEWER, capabilities={Capability.READ_FILES})
        checker = CapabilityChecker(rc)
        result = checker.check_tool("ls")
        assert result.allowed is False
        assert "cannot execute tools" in result.reason

    def test_tool_denied_not_in_allowed(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, allowed_tools={"cat"})
        checker = CapabilityChecker(rc)
        result = checker.check_tool("ls")
        assert result.allowed is False
        assert "not allowed" in result.reason

    def test_tool_denied_blocked(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, blocked_tools={"rm"})
        checker = CapabilityChecker(rc)
        result = checker.check_tool("rm")
        assert result.allowed is False


class TestCapabilityCheckerCommand:
    def test_command_allowed(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(rc)
        result = checker.check_command("echo hi")
        assert result.allowed is True

    def test_command_denied(self) -> None:
        rc = RoleConfig(role=Role.PM)
        checker = CapabilityChecker(rc)
        result = checker.check_command("echo hi")
        assert result.allowed is False


class TestCapabilityCheckerTest:
    def test_test_allowed(self) -> None:
        rc = RoleConfig(role=Role.QA)
        checker = CapabilityChecker(rc)
        result = checker.check_test("pytest")
        assert result.allowed is True

    def test_test_denied(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(rc)
        result = checker.check_test("pytest")
        assert result.allowed is False


class TestCapabilityCheckerPatch:
    def test_patch_allowed(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(rc)
        result = checker.check_patch(["file.py"])
        assert result.allowed is True

    def test_patch_denied_no_apply_capability(self) -> None:
        rc = RoleConfig(role=Role.REVIEWER)
        checker = CapabilityChecker(rc)
        result = checker.check_patch(["file.py"])
        assert result.allowed is False

    def test_patch_delegates_to_write_check(self) -> None:
        rc = RoleConfig(role=Role.DIRECTOR, max_files_per_action=1)
        checker = CapabilityChecker(rc)
        result = checker.check_patch(["a.py", "b.py"])
        assert result.allowed is False
        assert "Too many files" in result.reason


# =============================================================================
# get_role_config
# =============================================================================


class TestGetRoleConfig:
    def test_defaults(self) -> None:
        rc = get_role_config(Role.DIRECTOR)
        assert rc.capabilities == DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]

    def test_policy_adds_delete(self) -> None:
        rc = get_role_config(Role.DIRECTOR, policy={"write_tools": {"allow_delete": True}})
        assert Capability.DELETE_FILES in rc.capabilities

    def test_policy_does_not_add_when_false(self) -> None:
        rc = get_role_config(Role.DIRECTOR, policy={"write_tools": {"allow_delete": False}})
        assert Capability.DELETE_FILES not in rc.capabilities


# =============================================================================
# check_action_allowed
# =============================================================================


class TestCheckActionAllowed:
    def test_read_action(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "read", ["file.py"])
        assert result.allowed is True

    def test_write_action_denied_for_reviewer(self) -> None:
        result = check_action_allowed(Role.REVIEWER, "write", ["file.py"])
        assert result.allowed is False

    def test_delete_action_with_policy(self) -> None:
        result = check_action_allowed(
            Role.SYSTEM, "delete", ["file.py"], policy={"write_tools": {"allow_delete": True}}
        )
        assert result.allowed is True

    def test_tool_action(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "tool", ["cat"])
        assert result.allowed is True

    def test_tool_action_empty_targets(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "tool", [])
        assert result.allowed is True  # falls through

    def test_command_action(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "command", ["echo hi"])
        assert result.allowed is True

    def test_test_action(self) -> None:
        result = check_action_allowed(Role.QA, "test", ["pytest"])
        assert result.allowed is True

    def test_patch_action(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "patch", ["file.py"])
        assert result.allowed is True

    def test_create_action(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "create", ["file.py"])
        assert result.allowed is True

    def test_unknown_action_defaults_true(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "dance", ["file.py"])
        assert result.allowed is True


# =============================================================================
# validate_director_action
# =============================================================================


class TestValidateDirectorAction:
    def test_director_can_read(self) -> None:
        result = validate_director_action("read", ["file.py"])
        assert result.allowed is True

    def test_director_cannot_delete_by_default(self) -> None:
        result = validate_director_action("delete", ["file.py"])
        assert result.allowed is False

    def test_director_can_write(self) -> None:
        result = validate_director_action("write", ["file.py"])
        assert result.allowed is True

    def test_director_can_execute_tool(self) -> None:
        result = validate_director_action("tool", ["ls"])
        assert result.allowed is True


# =============================================================================
# ROLE_HOST_CAPABILITIES
# =============================================================================


class TestRoleHostCapabilities:
    def test_pm_workbench_has_read(self) -> None:
        caps = ROLE_HOST_CAPABILITIES[("pm", "electron_workbench")]
        assert Capability.READ_FILES in caps

    def test_director_workflow_has_tests(self) -> None:
        caps = ROLE_HOST_CAPABILITIES[("director", "workflow")]
        assert Capability.EXECUTE_TESTS in caps

    def test_qa_workbench_lacks_write(self) -> None:
        caps = ROLE_HOST_CAPABILITIES[("qa", "electron_workbench")]
        assert Capability.WRITE_FILES not in caps

    def test_all_keys_are_tuple(self) -> None:
        for key in ROLE_HOST_CAPABILITIES:
            assert isinstance(key, tuple)
            assert len(key) == 2


# =============================================================================
# _get_role_enum
# =============================================================================


class TestGetRoleEnum:
    def test_valid_director(self) -> None:
        assert _get_role_enum("director") == Role.DIRECTOR

    def test_valid_mixed_case(self) -> None:
        assert _get_role_enum("Director") == Role.DIRECTOR

    def test_invalid_returns_none(self) -> None:
        assert _get_role_enum("king") is None

    def test_empty_returns_none(self) -> None:
        assert _get_role_enum("") is None


# =============================================================================
# get_role_capabilities
# =============================================================================


class TestGetRoleCapabilities:
    def test_specific_host_kind(self) -> None:
        result = get_role_capabilities("director", host_kind="workflow")
        assert "workflow" in result
        assert "execute_tests" in result["workflow"]

    def test_specific_host_kind_not_found_fallback(self) -> None:
        result = get_role_capabilities("director", host_kind="unknown_host")
        assert "unknown_host" in result
        # Should fall back to default role capabilities
        assert "read_files" in result["unknown_host"]

    def test_all_hosts_for_role(self) -> None:
        result = get_role_capabilities("director")
        assert "electron_workbench" in result
        assert "workflow" in result

    def test_unknown_role_fallback(self) -> None:
        result = get_role_capabilities("invalid_role")
        assert "default" in result
        assert result["default"] == []

    def test_pm_role(self) -> None:
        result = get_role_capabilities("pm")
        assert "electron_workbench" in result
        assert "manage_workers" in result["electron_workbench"]

    def test_architect_role(self) -> None:
        result = get_role_capabilities("architect")
        assert "electron_workbench" in result
        assert "read_files" in result["electron_workbench"]
