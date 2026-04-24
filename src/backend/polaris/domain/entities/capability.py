"""Capability system for Director v2.

Migrated from: core/polaris_loop/director_skills.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(Enum):
    """Available roles in the Director system."""

    DIRECTOR = "director"
    REVIEWER = "reviewer"
    QA = "qa"
    PM = "pm"
    SYSTEM = "system"


class Capability(Enum):
    """Available capabilities for role-based access control."""

    READ_FILES = "read_files"
    WRITE_FILES = "write_files"
    DELETE_FILES = "delete_files"
    EXECUTE_TOOLS = "execute_tools"
    EXECUTE_COMMANDS = "execute_commands"
    EXECUTE_TESTS = "execute_tests"
    APPLY_PATCHES = "apply_patches"
    CREATE_FILES = "create_files"
    MANAGE_WORKERS = "manage_workers"
    VIEW_METRICS = "view_metrics"


# Default capability matrix per role
DEFAULT_ROLE_CAPABILITIES: dict[Role, set[Capability]] = {
    Role.DIRECTOR: {
        Capability.READ_FILES,
        Capability.WRITE_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.APPLY_PATCHES,
        Capability.CREATE_FILES,
        Capability.EXECUTE_COMMANDS,
        Capability.VIEW_METRICS,
        # Note: DELETE_FILES is NOT included by default
    },
    Role.REVIEWER: {
        Capability.READ_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.VIEW_METRICS,
    },
    Role.QA: {
        Capability.READ_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.EXECUTE_TESTS,
        Capability.EXECUTE_COMMANDS,
        Capability.VIEW_METRICS,
    },
    Role.PM: {
        Capability.READ_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.MANAGE_WORKERS,
        Capability.VIEW_METRICS,
    },
    Role.SYSTEM: {
        Capability.READ_FILES,
        Capability.WRITE_FILES,
        Capability.DELETE_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.EXECUTE_COMMANDS,
        Capability.EXECUTE_TESTS,
        Capability.APPLY_PATCHES,
        Capability.CREATE_FILES,
        Capability.MANAGE_WORKERS,
        Capability.VIEW_METRICS,
    },
}


@dataclass(frozen=True)
class RoleConfig:
    """Runtime configuration for a role."""

    role: Role
    capabilities: set[Capability] = field(default_factory=set)
    allowed_tools: set[str] = field(default_factory=set)
    blocked_tools: set[str] = field(default_factory=set)
    max_files_per_action: int = 3
    max_lines_per_action: int = 500

    def __post_init__(self) -> None:
        # Set default capabilities if not provided
        if not self.capabilities:
            object.__setattr__(self, "capabilities", set(DEFAULT_ROLE_CAPABILITIES.get(self.role, set())))

    def has_capability(self, cap: Capability) -> bool:
        """Check if this role has a capability."""
        return cap in self.capabilities

    def can_use_tool(self, tool: str) -> bool:
        """Check if this role can use a specific tool."""
        if tool in self.blocked_tools:
            return False
        return not (self.allowed_tools and tool not in self.allowed_tools)


@dataclass
class CapabilityResult:
    """Result of a capability check."""

    allowed: bool
    reason: str = ""
    mode: str = "strict"  # "strict" or "advisory"


@dataclass
class Skill:
    """A registered skill that can be loaded for specific tasks."""

    id: str
    name: str
    description: str
    prompt_fragments: dict[str, str] = field(default_factory=dict)
    required_capabilities: set[Capability] = field(default_factory=set)
    allowed_tools: set[str] = field(default_factory=set)
    blocked_tools: set[str] = field(default_factory=set)
    context_files: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class CapabilityChecker:
    """Checks if actions are allowed based on role capabilities."""

    def __init__(self, role_config: RoleConfig, policy: dict[str, Any] | None = None) -> None:
        self.role_config = role_config
        self.policy = policy or {}

    def check_read(self, files: list[str]) -> CapabilityResult:
        """Check if reading files is allowed."""
        if not self.role_config.has_capability(Capability.READ_FILES):
            return CapabilityResult(
                allowed=False,
                reason=f"Role {self.role_config.role.value} cannot read files",
            )
        return CapabilityResult(allowed=True)

    def check_write(self, files: list[str]) -> CapabilityResult:
        """Check if writing files is allowed."""
        if not self.role_config.has_capability(Capability.WRITE_FILES):
            return CapabilityResult(
                allowed=False,
                reason=f"Role {self.role_config.role.value} cannot write files",
            )
        if len(files) > self.role_config.max_files_per_action:
            return CapabilityResult(
                allowed=False,
                reason=f"Too many files: {len(files)} > {self.role_config.max_files_per_action}",
            )
        return CapabilityResult(allowed=True)

    def check_delete(self, files: list[str]) -> CapabilityResult:
        """Check if deleting files is allowed."""
        if not self.role_config.has_capability(Capability.DELETE_FILES):
            return CapabilityResult(
                allowed=False,
                reason=f"Role {self.role_config.role.value} cannot delete files",
            )
        # Check policy override
        write_tools = self.policy.get("write_tools", {})
        if not write_tools.get("allow_delete", False):
            return CapabilityResult(allowed=False, reason="File deletion is disabled by policy")
        return CapabilityResult(allowed=True)

    def check_create(self, files: list[str]) -> CapabilityResult:
        """Check if creating files is allowed."""
        if not self.role_config.has_capability(Capability.CREATE_FILES):
            return CapabilityResult(
                allowed=False,
                reason=f"Role {self.role_config.role.value} cannot create files",
            )
        return CapabilityResult(allowed=True)

    def check_tool(self, tool: str) -> CapabilityResult:
        """Check if using a tool is allowed."""
        if not self.role_config.has_capability(Capability.EXECUTE_TOOLS):
            return CapabilityResult(
                allowed=False,
                reason=f"Role {self.role_config.role.value} cannot execute tools",
            )
        if not self.role_config.can_use_tool(tool):
            return CapabilityResult(
                allowed=False,
                reason=f"Tool {tool} is not allowed for role {self.role_config.role.value}",
            )
        return CapabilityResult(allowed=True)

    def check_command(self, command: str) -> CapabilityResult:
        """Check if executing a command is allowed."""
        if not self.role_config.has_capability(Capability.EXECUTE_COMMANDS):
            return CapabilityResult(
                allowed=False,
                reason=f"Role {self.role_config.role.value} cannot execute commands",
            )
        return CapabilityResult(allowed=True)

    def check_test(self, _test_command: str) -> CapabilityResult:
        """Check if executing a test is allowed."""
        if not self.role_config.has_capability(Capability.EXECUTE_TESTS):
            return CapabilityResult(
                allowed=False,
                reason=f"Role {self.role_config.role.value} cannot execute tests",
            )
        return CapabilityResult(allowed=True)

    def check_patch(self, files: list[str]) -> CapabilityResult:
        """Check if applying patches is allowed."""
        if not self.role_config.has_capability(Capability.APPLY_PATCHES):
            return CapabilityResult(
                allowed=False,
                reason=f"Role {self.role_config.role.value} cannot apply patches",
            )
        return self.check_write(files)


def get_role_config(
    role: Role,
    policy: dict[str, Any] | None = None,
) -> RoleConfig:
    """Get role configuration with optional policy overrides.

    Args:
        role: The role to configure
        policy: Optional policy dict with overrides

    Returns:
        RoleConfig for the role
    """
    capabilities = set(DEFAULT_ROLE_CAPABILITIES.get(role, set()))

    # Apply policy overrides
    if policy:
        write_tools = policy.get("write_tools", {})
        if write_tools.get("allow_delete", False):
            capabilities.add(Capability.DELETE_FILES)

    return RoleConfig(role=role, capabilities=capabilities)


def check_action_allowed(
    role: Role,
    action: str,
    targets: list[str],
    policy: dict[str, Any] | None = None,
) -> CapabilityResult:
    """Check if an action is allowed for a role.

    Args:
        role: The role attempting the action
        action: Action type (read/write/delete/tool/command/test/patch)
        targets: Target files/tools/commands
        policy: Optional policy overrides

    Returns:
        CapabilityResult with allowed status and reason
    """
    role_config = get_role_config(role, policy)
    checker = CapabilityChecker(role_config, policy)

    action_map = {
        "read": checker.check_read,
        "write": checker.check_write,
        "delete": checker.check_delete,
        "create": checker.check_create,
        "patch": checker.check_patch,
    }

    if action in action_map:
        return action_map[action](targets)

    if action == "tool" and targets:
        return checker.check_tool(targets[0])

    if action == "command" and targets:
        return checker.check_command(targets[0])

    if action == "test" and targets:
        return checker.check_test(targets[0])

    return CapabilityResult(allowed=True)


def validate_director_action(
    action: str,
    targets: list[str],
    policy: dict[str, Any] | None = None,
) -> CapabilityResult:
    """Convenience function to validate a Director action against the capability matrix.

    Used by the Director loop to enforce role-based access control before
    executing file writes, tool calls, or other privileged operations.
    """
    return check_action_allowed(Role.DIRECTOR, action, targets, policy)


# ==================== Role Host Capabilities ====================

# 扩展：不同宿主类型的能力矩阵
# Workbench 默认比 Workflow 严格，不直接修改 workflow 状态
ROLE_HOST_CAPABILITIES: dict[tuple, set[Capability]] = {
    # PM Workbench
    ("pm", "electron_workbench"): {
        Capability.READ_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.MANAGE_WORKERS,
        Capability.VIEW_METRICS,
    },
    # PM Workflow
    ("pm", "workflow"): {
        Capability.READ_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.MANAGE_WORKERS,
        Capability.VIEW_METRICS,
        Capability.WRITE_FILES,  # Workflow can write contracts
    },
    # Director Workbench
    ("director", "electron_workbench"): {
        Capability.READ_FILES,
        Capability.WRITE_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.EXECUTE_COMMANDS,
        Capability.CREATE_FILES,
        Capability.VIEW_METRICS,
    },
    # Director Workflow
    ("director", "workflow"): {
        Capability.READ_FILES,
        Capability.WRITE_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.EXECUTE_COMMANDS,
        Capability.EXECUTE_TESTS,
        Capability.APPLY_PATCHES,
        Capability.CREATE_FILES,
        Capability.VIEW_METRICS,
    },
    # QA Workbench
    ("qa", "electron_workbench"): {
        Capability.READ_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.EXECUTE_TESTS,
        Capability.VIEW_METRICS,
    },
    # QA Workflow
    ("qa", "workflow"): {
        Capability.READ_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.EXECUTE_COMMANDS,
        Capability.EXECUTE_TESTS,
        Capability.VIEW_METRICS,
    },
    # Architect Workbench
    ("architect", "electron_workbench"): {
        Capability.READ_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.VIEW_METRICS,
    },
    # Chief Engineer Workbench
    ("chief_engineer", "electron_workbench"): {
        Capability.READ_FILES,
        Capability.WRITE_FILES,
        Capability.EXECUTE_TOOLS,
        Capability.EXECUTE_COMMANDS,
        Capability.VIEW_METRICS,
    },
}


def get_role_capabilities(
    role: str,
    host_kind: str | None = None,
) -> dict[str, list[str]]:
    """获取角色在不同宿主下的能力配置

    Args:
        role: 角色标识 (pm, director, qa, architect, chief_engineer)
        host_kind: 宿主类型。如果不提供，返回所有宿主的能力配置。

    Returns:
        能力配置字典，key 为 host_kind，value 为能力列表
    """
    result: dict[str, list[str]] = {}

    # 如果指定了 host_kind，只返回该宿主的能力
    if host_kind:
        key = (role, host_kind)
        if key in ROLE_HOST_CAPABILITIES:
            result[host_kind] = [cap.value for cap in ROLE_HOST_CAPABILITIES[key]]
        else:
            # 回退到默认能力
            default_caps = DEFAULT_ROLE_CAPABILITIES.get(Role(role.upper()), set())
            result[host_kind] = [cap.value for cap in default_caps]
        return result

    # 如果没有指定 host_kind，返回所有宿主的能力
    for (r, h), caps in ROLE_HOST_CAPABILITIES.items():
        if r == role:
            result[h] = [cap.value for cap in caps]

    # 如果没有特定配置，回退到默认
    if not result:
        default_caps = DEFAULT_ROLE_CAPABILITIES.get(Role(role.upper()), set())
        result["default"] = [cap.value for cap in default_caps]

    return result
