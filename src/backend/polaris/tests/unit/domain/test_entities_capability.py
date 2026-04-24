"""Tests for polaris.domain.entities.capability."""

from __future__ import annotations

from polaris.domain.entities.capability import (
    DEFAULT_ROLE_CAPABILITIES,
    ROLE_HOST_CAPABILITIES,
    Capability,
    CapabilityChecker,
    CapabilityResult,
    Role,
    RoleConfig,
    Skill,
    check_action_allowed,
    get_role_capabilities,
    get_role_config,
    validate_director_action,
)


class TestRoleEnum:
    def test_role_values(self) -> None:
        assert Role.DIRECTOR.value == "director"
        assert Role.REVIEWER.value == "reviewer"
        assert Role.QA.value == "qa"
        assert Role.PM.value == "pm"
        assert Role.SYSTEM.value == "system"


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


class TestDefaultRoleCapabilities:
    def test_director_has_write(self) -> None:
        assert Capability.READ_FILES in DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]
        assert Capability.WRITE_FILES in DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]
        assert Capability.DELETE_FILES not in DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]

    def test_reviewer_read_only(self) -> None:
        caps = DEFAULT_ROLE_CAPABILITIES[Role.REVIEWER]
        assert Capability.READ_FILES in caps
        assert Capability.WRITE_FILES not in caps

    def test_qa_can_test(self) -> None:
        caps = DEFAULT_ROLE_CAPABILITIES[Role.QA]
        assert Capability.EXECUTE_TESTS in caps
        assert Capability.WRITE_FILES not in caps

    def test_system_has_all(self) -> None:
        caps = DEFAULT_ROLE_CAPABILITIES[Role.SYSTEM]
        assert Capability.DELETE_FILES in caps
        assert Capability.MANAGE_WORKERS in caps


class TestRoleConfig:
    def test_default_capabilities(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR)
        assert config.capabilities == DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]

    def test_custom_capabilities(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR, capabilities={Capability.READ_FILES})
        assert config.has_capability(Capability.READ_FILES)
        assert not config.has_capability(Capability.WRITE_FILES)

    def test_has_capability(self) -> None:
        config = RoleConfig(role=Role.REVIEWER)
        assert config.has_capability(Capability.READ_FILES)
        assert not config.has_capability(Capability.WRITE_FILES)

    def test_can_use_tool_allowed(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR, allowed_tools={"read_file"})
        assert config.can_use_tool("read_file")

    def test_can_use_tool_blocked(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR, blocked_tools={"dangerous_tool"})
        assert not config.can_use_tool("dangerous_tool")

    def test_can_use_tool_no_restrictions(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR)
        assert config.can_use_tool("any_tool")


class TestCapabilityResult:
    def test_defaults(self) -> None:
        result = CapabilityResult(allowed=True)
        assert result.allowed is True
        assert result.reason == ""
        assert result.mode == "strict"


class TestSkill:
    def test_defaults(self) -> None:
        skill = Skill(id="test", name="Test", description="A test skill")
        assert skill.prompt_fragments == {}
        assert skill.required_capabilities == set()
        assert skill.tags == []


class TestCapabilityChecker:
    def test_check_read_allowed(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(config)
        result = checker.check_read(["file.py"])
        assert result.allowed is True

    def test_check_read_denied(self) -> None:
        config = RoleConfig(role=Role.REVIEWER)
        config.capabilities.discard(Capability.READ_FILES)
        checker = CapabilityChecker(config)
        result = checker.check_read(["file.py"])
        assert result.allowed is False

    def test_check_write_too_many_files(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR, max_files_per_action=2)
        checker = CapabilityChecker(config)
        result = checker.check_write(["a.py", "b.py", "c.py"])
        assert result.allowed is False
        assert "Too many files" in result.reason

    def test_check_delete_denied_by_policy(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR)
        config.capabilities.add(Capability.DELETE_FILES)
        checker = CapabilityChecker(config, policy={"write_tools": {"allow_delete": False}})
        result = checker.check_delete(["file.py"])
        assert result.allowed is False
        assert "disabled by policy" in result.reason

    def test_check_delete_allowed_by_policy(self) -> None:
        config = RoleConfig(role=Role.SYSTEM)
        checker = CapabilityChecker(config, policy={"write_tools": {"allow_delete": True}})
        result = checker.check_delete(["file.py"])
        assert result.allowed is True

    def test_check_tool_denied(self) -> None:
        config = RoleConfig(role=Role.REVIEWER)
        config.capabilities.discard(Capability.EXECUTE_TOOLS)
        checker = CapabilityChecker(config)
        result = checker.check_tool("some_tool")
        assert result.allowed is False

    def test_check_command_denied(self) -> None:
        config = RoleConfig(role=Role.REVIEWER, capabilities=set())
        checker = CapabilityChecker(config)
        result = checker.check_command("ls")
        assert result.allowed is False

    def test_check_test_denied(self) -> None:
        config = RoleConfig(role=Role.REVIEWER, capabilities=set())
        checker = CapabilityChecker(config)
        result = checker.check_test("pytest")
        assert result.allowed is False

    def test_check_patch_delegates_to_write(self) -> None:
        config = RoleConfig(role=Role.DIRECTOR)
        checker = CapabilityChecker(config)
        result = checker.check_patch(["file.py"])
        assert result.allowed is True


class TestGetRoleConfig:
    def test_defaults(self) -> None:
        config = get_role_config(Role.QA)
        assert Capability.EXECUTE_TESTS in config.capabilities

    def test_policy_override_adds_delete(self) -> None:
        config = get_role_config(Role.DIRECTOR, policy={"write_tools": {"allow_delete": True}})
        assert Capability.DELETE_FILES in config.capabilities


class TestCheckActionAllowed:
    def test_read_action(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "read", ["file.py"])
        assert result.allowed is True

    def test_unknown_action_defaults_to_allowed(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "unknown", ["file.py"])
        assert result.allowed is True

    def test_tool_action(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "tool", ["read_file"])
        assert result.allowed is True

    def test_command_action(self) -> None:
        result = check_action_allowed(Role.DIRECTOR, "command", ["ls"])
        assert result.allowed is True

    def test_test_action(self) -> None:
        result = check_action_allowed(Role.QA, "test", ["pytest"])
        assert result.allowed is True


class TestValidateDirectorAction:
    def test_read_allowed(self) -> None:
        result = validate_director_action("read", ["file.py"])
        assert result.allowed is True

    def test_write_allowed(self) -> None:
        result = validate_director_action("write", ["file.py"])
        assert result.allowed is True


class TestRoleHostCapabilities:
    def test_pm_workbench_capabilities(self) -> None:
        caps = ROLE_HOST_CAPABILITIES[("pm", "electron_workbench")]
        assert Capability.READ_FILES in caps
        assert Capability.WRITE_FILES not in caps

    def test_director_workflow_capabilities(self) -> None:
        caps = ROLE_HOST_CAPABILITIES[("director", "workflow")]
        assert Capability.EXECUTE_TESTS in caps


class TestGetRoleCapabilities:
    def test_specific_host(self) -> None:
        result = get_role_capabilities("pm", host_kind="electron_workbench")
        assert "electron_workbench" in result
        assert "read_files" in result["electron_workbench"]

    def test_all_hosts(self) -> None:
        result = get_role_capabilities("director")
        assert "electron_workbench" in result
        assert "workflow" in result

    def test_fallback_default(self) -> None:
        # When a role has no ROLE_HOST_CAPABILITIES mapping, it falls back to DEFAULT_ROLE_CAPABILITIES
        # Use a role that exists in DEFAULT_ROLE_CAPABILITIES but not in ROLE_HOST_CAPABILITIES
        result = get_role_capabilities("reviewer")
        assert "default" in result
        assert len(result["default"]) > 0
