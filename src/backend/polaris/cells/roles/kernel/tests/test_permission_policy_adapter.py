"""Real RoleGateway decisions survive the policy-owner adapter boundary."""

from __future__ import annotations

from contextvars import copy_context
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.policy.permission.public.service import PermissionService
from polaris.cells.roles.kernel.public.permission_policy_adapter import (
    RoleKernelToolPolicyAdapter,
    get_permission_tool_policy_adapter,
)
from polaris.cells.roles.kernel.public.service import RoleToolGateway
from polaris.cells.roles.profile.public.service import RoleProfile, RoleProfileRegistry, RoleToolPolicy

# Captured through the original gateway and PermissionService before production edits.
_MATRIX: list[dict[str, Any]] = [
    {
        "role": "pm",
        "available": [
            "read_file",
            "repo_read_head",
            "repo_read_slice",
            "repo_read_tail",
            "repo_read_around",
            "repo_tree",
            "repo_rg",
            "search_code",
            "grep",
            "ripgrep",
            "glob",
            "list_directory",
            "file_exists",
            "scout_probe",
        ],
        "effective_permissions": [
            "tool:execute:file_exists",
            "tool:execute:glob",
            "tool:execute:grep",
            "tool:execute:list_directory",
            "tool:execute:read_file",
            "tool:execute:repo_read_around",
            "tool:execute:repo_read_head",
            "tool:execute:repo_read_slice",
            "tool:execute:repo_read_tail",
            "tool:execute:repo_rg",
            "tool:execute:repo_tree",
            "tool:execute:ripgrep",
            "tool:execute:scout_probe",
            "tool:execute:search_code",
        ],
        "cases": [
            {
                "case": "read",
                "tool": "read_file",
                "arguments": {"file": "src/a.py"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "alias_read",
                "tool": "repo_read_file",
                "arguments": {"path": "src/a.py"},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "write",
                "tool": "write_file",
                "arguments": {"file": "src/a.py", "content": "x"},
                "gateway_result": [False, "工具 'write_file' 不在角色白名单中 [拒绝: 不能写入/禁止写入/危险操作]"],
            },
            {
                "case": "alias_write",
                "tool": "create_file",
                "arguments": {"path": "src/a.py", "content": "x"},
                "gateway_result": [
                    False,
                    "工具 'create_file (canonical: write_file)' 不在角色白名单中 [拒绝: 不能写入/禁止写入/危险操作]",
                ],
            },
            {
                "case": "exec",
                "tool": "execute_command",
                "arguments": {"command": "python -V", "cwd": "."},
                "gateway_result": [False, "工具 'execute_command' 不在角色白名单中 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "danger",
                "tool": "execute_command",
                "arguments": {"command": "rm -rf /", "cwd": "."},
                "gateway_result": [False, "工具 'execute_command' 不在角色白名单中 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "traversal",
                "tool": "read_file",
                "arguments": {"file": "../outside.py"},
                "gateway_result": [False, "路径 '../outside.py' 包含穿越序列 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "unknown_tool",
                "tool": "unknown_cut_tool",
                "arguments": {},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
        ],
    },
    {
        "role": "chief_engineer",
        "available": [
            "read_file",
            "repo_read_head",
            "repo_read_slice",
            "repo_read_tail",
            "repo_read_around",
            "repo_tree",
            "repo_rg",
            "search_code",
            "grep",
            "ripgrep",
            "glob",
            "list_directory",
            "file_exists",
            "scout_probe",
        ],
        "effective_permissions": [
            "tool:execute:file_exists",
            "tool:execute:glob",
            "tool:execute:grep",
            "tool:execute:list_directory",
            "tool:execute:read_file",
            "tool:execute:repo_read_around",
            "tool:execute:repo_read_head",
            "tool:execute:repo_read_slice",
            "tool:execute:repo_read_tail",
            "tool:execute:repo_rg",
            "tool:execute:repo_tree",
            "tool:execute:ripgrep",
            "tool:execute:scout_probe",
            "tool:execute:search_code",
        ],
        "cases": [
            {
                "case": "read",
                "tool": "read_file",
                "arguments": {"file": "src/a.py"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "alias_read",
                "tool": "repo_read_file",
                "arguments": {"path": "src/a.py"},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "write",
                "tool": "write_file",
                "arguments": {"file": "src/a.py", "content": "x"},
                "gateway_result": [False, "工具 'write_file' 不在角色白名单中 [拒绝: 不能写入/禁止写入/危险操作]"],
            },
            {
                "case": "alias_write",
                "tool": "create_file",
                "arguments": {"path": "src/a.py", "content": "x"},
                "gateway_result": [
                    False,
                    "工具 'create_file (canonical: write_file)' 不在角色白名单中 [拒绝: 不能写入/禁止写入/危险操作]",
                ],
            },
            {
                "case": "exec",
                "tool": "execute_command",
                "arguments": {"command": "python -V", "cwd": "."},
                "gateway_result": [False, "工具 'execute_command' 不在角色白名单中 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "danger",
                "tool": "execute_command",
                "arguments": {"command": "rm -rf /", "cwd": "."},
                "gateway_result": [False, "工具 'execute_command' 不在角色白名单中 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "traversal",
                "tool": "read_file",
                "arguments": {"file": "../outside.py"},
                "gateway_result": [False, "路径 '../outside.py' 包含穿越序列 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "unknown_tool",
                "tool": "unknown_cut_tool",
                "arguments": {},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
        ],
    },
    {
        "role": "director",
        "available": [
            "read_file",
            "repo_read_head",
            "repo_read_slice",
            "repo_read_tail",
            "repo_read_around",
            "repo_tree",
            "glob",
            "file_exists",
            "repo_rg",
            "edit_blocks",
            "repo_apply_diff",
            "write_file",
            "search_replace",
            "edit_file",
            "append_to_file",
            "execute_command",
            "scout_probe",
        ],
        "effective_permissions": [
            "api:execute:execute_command",
            "file:write:**/*",
            "tool:execute:append_to_file",
            "tool:execute:edit_blocks",
            "tool:execute:edit_file",
            "tool:execute:execute_command",
            "tool:execute:file_exists",
            "tool:execute:glob",
            "tool:execute:read_file",
            "tool:execute:repo_apply_diff",
            "tool:execute:repo_read_around",
            "tool:execute:repo_read_head",
            "tool:execute:repo_read_slice",
            "tool:execute:repo_read_tail",
            "tool:execute:repo_rg",
            "tool:execute:repo_tree",
            "tool:execute:scout_probe",
            "tool:execute:search_replace",
            "tool:execute:write_file",
        ],
        "cases": [
            {
                "case": "read",
                "tool": "read_file",
                "arguments": {"file": "src/a.py"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "alias_read",
                "tool": "repo_read_file",
                "arguments": {"path": "src/a.py"},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "write",
                "tool": "write_file",
                "arguments": {"file": "src/a.py", "content": "x"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "alias_write",
                "tool": "create_file",
                "arguments": {"path": "src/a.py", "content": "x"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "exec",
                "tool": "execute_command",
                "arguments": {"command": "python -V", "cwd": "."},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "danger",
                "tool": "execute_command",
                "arguments": {"command": "rm -rf /", "cwd": "."},
                "gateway_result": [False, "命令包含危险操作 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "traversal",
                "tool": "read_file",
                "arguments": {"file": "../outside.py"},
                "gateway_result": [False, "路径 '../outside.py' 包含穿越序列 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "unknown_tool",
                "tool": "unknown_cut_tool",
                "arguments": {},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
        ],
    },
    {
        "role": "qa",
        "available": [
            "read_file",
            "search_code",
            "grep",
            "ripgrep",
            "glob",
            "list_directory",
            "file_exists",
            "scout_probe",
            "execute_command",
        ],
        "effective_permissions": [
            "api:execute:execute_command",
            "tool:execute:execute_command",
            "tool:execute:file_exists",
            "tool:execute:glob",
            "tool:execute:grep",
            "tool:execute:list_directory",
            "tool:execute:read_file",
            "tool:execute:ripgrep",
            "tool:execute:scout_probe",
            "tool:execute:search_code",
        ],
        "cases": [
            {
                "case": "read",
                "tool": "read_file",
                "arguments": {"file": "src/a.py"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "alias_read",
                "tool": "repo_read_file",
                "arguments": {"path": "src/a.py"},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "write",
                "tool": "write_file",
                "arguments": {"file": "src/a.py", "content": "x"},
                "gateway_result": [False, "工具 'write_file' 不在角色白名单中 [拒绝: 不能写入/禁止写入/危险操作]"],
            },
            {
                "case": "alias_write",
                "tool": "create_file",
                "arguments": {"path": "src/a.py", "content": "x"},
                "gateway_result": [
                    False,
                    "工具 'create_file (canonical: write_file)' 不在角色白名单中 [拒绝: 不能写入/禁止写入/危险操作]",
                ],
            },
            {
                "case": "exec",
                "tool": "execute_command",
                "arguments": {"command": "python -V", "cwd": "."},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "danger",
                "tool": "execute_command",
                "arguments": {"command": "rm -rf /", "cwd": "."},
                "gateway_result": [False, "命令包含危险操作 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "traversal",
                "tool": "read_file",
                "arguments": {"file": "../outside.py"},
                "gateway_result": [False, "路径 '../outside.py' 包含穿越序列 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "unknown_tool",
                "tool": "unknown_cut_tool",
                "arguments": {},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
        ],
    },
    {
        "role": "cut_custom",
        "available": ["read_file", "write_file", "execute_command"],
        "effective_permissions": [
            "api:execute:execute_command",
            "file:write:**/*",
            "tool:execute:execute_command",
            "tool:execute:read_file",
            "tool:execute:write_file",
        ],
        "cases": [
            {
                "case": "read",
                "tool": "read_file",
                "arguments": {"file": "src/a.py"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "alias_read",
                "tool": "repo_read_file",
                "arguments": {"path": "src/a.py"},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "write",
                "tool": "write_file",
                "arguments": {"file": "src/a.py", "content": "x"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "alias_write",
                "tool": "create_file",
                "arguments": {"path": "src/a.py", "content": "x"},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "exec",
                "tool": "execute_command",
                "arguments": {"command": "python -V", "cwd": "."},
                "gateway_result": [True, "授权通过"],
            },
            {
                "case": "danger",
                "tool": "execute_command",
                "arguments": {"command": "rm -rf /", "cwd": "."},
                "gateway_result": [False, "命令包含危险操作 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "traversal",
                "tool": "read_file",
                "arguments": {"file": "../outside.py"},
                "gateway_result": [False, "路径 '../outside.py' 包含穿越序列 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "unknown_tool",
                "tool": "unknown_cut_tool",
                "arguments": {},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
        ],
    },
    {
        "role": "cut_empty",
        "available": [],
        "effective_permissions": ["api:execute:execute_command", "file:write:**/*"],
        "cases": [
            {
                "case": "read",
                "tool": "read_file",
                "arguments": {"file": "src/a.py"},
                "gateway_result": [False, "工具 'read_file' 不在角色白名单中 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "alias_read",
                "tool": "repo_read_file",
                "arguments": {"path": "src/a.py"},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "write",
                "tool": "write_file",
                "arguments": {"file": "src/a.py", "content": "x"},
                "gateway_result": [False, "工具 'write_file' 不在角色白名单中 [拒绝: 不能写入/禁止写入/危险操作]"],
            },
            {
                "case": "alias_write",
                "tool": "create_file",
                "arguments": {"path": "src/a.py", "content": "x"},
                "gateway_result": [
                    False,
                    "工具 'create_file (canonical: write_file)' 不在角色白名单中 [拒绝: 不能写入/禁止写入/危险操作]",
                ],
            },
            {
                "case": "exec",
                "tool": "execute_command",
                "arguments": {"command": "python -V", "cwd": "."},
                "gateway_result": [False, "工具 'execute_command' 不在角色白名单中 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "danger",
                "tool": "execute_command",
                "arguments": {"command": "rm -rf /", "cwd": "."},
                "gateway_result": [False, "工具 'execute_command' 不在角色白名单中 [拒绝: 不能执行/禁止执行/危险命令]"],
            },
            {
                "case": "traversal",
                "tool": "read_file",
                "arguments": {"file": "../outside.py"},
                "gateway_result": [False, "工具 'read_file' 不在角色白名单中 [拒绝: 不能/禁止/不允许]"],
            },
            {
                "case": "unknown_tool",
                "tool": "unknown_cut_tool",
                "arguments": {},
                "gateway_result": [False, "工具规范化失败 [拒绝: 不能/禁止/不允许]"],
            },
        ],
    },
]


@pytest.fixture
def profiles() -> RoleProfileRegistry:
    source = Path(__file__).resolve().parents[2] / "profile/config/roles/core_roles.yaml"
    result = RoleProfileRegistry()
    result.load_from_yaml(source)
    custom = RoleProfile(
        role_id="cut_custom",
        display_name="Custom",
        description="Permission golden",
        tool_policy=RoleToolPolicy(
            whitelist=["read_file", "write_file", "execute_command"],
            allow_code_write=True,
            allow_command_execution=True,
        ),
    )
    result.register(custom)
    result.register(
        replace(
            custom,
            role_id="cut_empty",
            tool_policy=RoleToolPolicy(whitelist=[], allow_code_write=True, allow_command_execution=True),
        )
    )
    return result


@pytest.mark.parametrize("row", _MATRIX, ids=[row["role"] for row in _MATRIX])
@pytest.mark.asyncio
async def test_real_adapter_keeps_original_gateway_matrix(
    tmp_path: Path, profiles: RoleProfileRegistry, row: dict[str, Any]
) -> None:
    adapter = RoleKernelToolPolicyAdapter(str(tmp_path), profile_registry=profiles)
    assert sorted(adapter.role_permissions()[row["role"]]) == row["effective_permissions"]
    profile = profiles.get_profile(row["role"])
    assert profile is not None
    assert RoleToolGateway(profile, str(tmp_path)).get_available_tools() == row["available"]
    for case in row["cases"]:
        assert (
            list(adapter.check_tool_permission(row["role"], case["tool"], case["arguments"])) == case["gateway_result"]
        )


def test_port_uses_current_profile_and_reuses_only_stable_workspace_binding(
    tmp_path: Path, profiles: RoleProfileRegistry
) -> None:
    adapter = RoleKernelToolPolicyAdapter(str(tmp_path), profile_registry=profiles)
    args = {"file": "src/a.py", "content": "x"}
    assert adapter.check_tool_permission("cut_custom", "write_file", args)[0]
    old = profiles.get_profile("cut_custom")
    assert old is not None
    profiles.register(replace(old, tool_policy=RoleToolPolicy(whitelist=[])))
    assert not adapter.check_tool_permission("cut_custom", "write_file", args)[0]
    assert adapter.check_tool_permission("unknown", "write_file", args) == (False, "tool_policy_unknown_role")
    assert get_permission_tool_policy_adapter(str(tmp_path)) is get_permission_tool_policy_adapter(str(tmp_path))
    assert get_permission_tool_policy_adapter(str(tmp_path)) is not get_permission_tool_policy_adapter(
        str(tmp_path / "other")
    )


def test_real_gateway_scope_alias_danger_and_symlink_decisions_are_not_reimplemented(
    tmp_path: Path, profiles: RoleProfileRegistry
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("old", encoding="utf-8")
    (tmp_path / "outside.py").write_text("outside", encoding="utf-8")
    (tmp_path / "src/link.py").symlink_to(tmp_path / "outside.py")

    def bound(profile: RoleProfile, workspace: str) -> RoleToolGateway:
        return RoleToolGateway(profile, workspace, capability_scope=("src",))

    adapter = RoleKernelToolPolicyAdapter(str(tmp_path), profile_registry=profiles, gateway_factory=bound)
    profile = profiles.get_profile("cut_custom")
    assert profile is not None
    for tool, args in (
        ("read_file", {"file": "src/a.py"}),
        ("read_file", {"file": "src/link.py"}),
        ("read_file", {"file": "../outside.py"}),
        ("create_file", {"path": "src/a.py", "content": "x"}),
        ("execute_command", {"command": "rm -rf /", "cwd": str(tmp_path)}),
        ("execute_command", {"command": "python -V", "cwd": str(tmp_path / "other")}),
    ):
        assert adapter.check_tool_permission("cut_custom", tool, args) == bound(
            profile, str(tmp_path)
        ).check_tool_permission(tool, args)


def test_cached_adapter_captures_current_tool_spec_instead_of_reusing_an_old_snapshot(
    tmp_path: Path, profiles: RoleProfileRegistry
) -> None:
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    def probe() -> None:
        name = "permission_port_fresh_tool"
        profiles.register(
            RoleProfile(
                role_id="cut_fresh",
                display_name="Fresh",
                description="Snapshot freshness",
                tool_policy=RoleToolPolicy(whitelist=[name], allow_code_write=True, allow_command_execution=False),
            )
        )
        adapter = RoleKernelToolPolicyAdapter(str(tmp_path), profile_registry=profiles)
        assert not adapter.check_tool_permission("cut_fresh", name, {})[0]
        ToolSpecRegistry.register(
            name, {"category": "write", "description": "test-only", "aliases": [], "arguments": []}
        )
        assert adapter.check_tool_permission("cut_fresh", name, {})[0]

    # Registry mutations stay in this copied ContextVar context without private registry hooks.
    copy_context().run(probe)


@pytest.mark.asyncio
async def test_real_adapter_gateway_failure_denies_the_policy_consumer(
    tmp_path: Path, profiles: RoleProfileRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = RoleKernelToolPolicyAdapter(str(tmp_path), profile_registry=profiles)
    service = PermissionService(str(tmp_path), tool_policy_port=adapter)
    await service.initialize()

    def broken(*args: Any, **kwargs: Any) -> tuple[bool, str]:
        raise RuntimeError("gateway unavailable")

    monkeypatch.setattr(RoleToolGateway, "check_tool_permission", broken)
    result = await service.check_tool_permission("pm", "read_file", {"file": "src/a.py"})
    assert not result.allowed
    assert "unavailable" in result.reason
