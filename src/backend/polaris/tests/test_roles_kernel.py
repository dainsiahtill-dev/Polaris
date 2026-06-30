"""Tests for Role Unified Kernel

全功能联调测试：聊天入口、工作流入口、角色权限、工具执行、数据落盘。
"""

import json

# 确保在测试路径中
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from polaris.cells.control_plane.run_ledger.public import JobToken
from polaris.cells.roles.adapters.internal import runtime_dialogue
from polaris.cells.roles.adapters.public.service import WorkflowRoleAdapter
from polaris.cells.roles.kernel.internal.kernel.helpers import extract_structured_tool_calls
from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
from polaris.cells.roles.runtime.public.service import (
    RoleExecutionKernel,
    RoleExecutionMode,
    RoleProfileRegistry,
    RoleToolGateway,
    RoleTurnRequest,
    ToolAuthorizationError,
)


@pytest.fixture
def temp_workspace(tmp_path: Path):
    """临时工作区"""
    return str(tmp_path)


@pytest.fixture
def registry():
    """加载核心角色配置的注册表"""
    reg = RoleProfileRegistry()
    # 从内置配置加载
    from polaris.cells.roles.profile.internal.builtin_profiles import BUILTIN_PROFILES
    from polaris.cells.roles.profile.internal.schema import profile_from_dict

    for profile_data in BUILTIN_PROFILES:
        profile = profile_from_dict(profile_data)
        reg.register(profile)

    return reg


@pytest.fixture
def kernel(temp_workspace, registry):
    """执行内核"""
    return RoleExecutionKernel(
        workspace=temp_workspace,
        registry=registry,
    )


class TestRoleProfileRegistry:
    """测试角色配置注册表"""

    def test_load_builtin_profiles(self, registry):
        """测试加载内置配置"""
        roles = registry.list_roles()
        assert len(roles) >= 5
        assert "pm" in roles
        assert "architect" in roles
        assert "chief_engineer" in roles
        assert "director" in roles
        assert "qa" in roles
        assert "scout" in roles

    def test_get_profile(self, registry):
        """测试获取角色配置"""
        pm_profile = registry.get_profile("pm")
        assert pm_profile is not None
        assert pm_profile.role_id == "pm"
        assert pm_profile.display_name == "PM (Prime Minister)"

        # 验证策略
        assert pm_profile.prompt_policy.allow_override is False  # 禁止覆盖
        assert pm_profile.tool_policy.allow_code_write is False  # PM禁止代码写入
        assert "search_code" in pm_profile.tool_policy.whitelist

    def test_profile_fingerprint(self, registry):
        """测试Profile指纹一致性"""
        pm_profile = registry.get_profile("pm")
        fingerprint = pm_profile.profile_fingerprint

        # 相同配置应生成相同指纹
        pm_profile2 = registry.get_profile("pm")
        assert pm_profile2.profile_fingerprint == fingerprint

        # 不同角色不同指纹
        director_profile = registry.get_profile("director")
        assert director_profile.profile_fingerprint != fingerprint


class TestRoleToolGateway:
    """测试工具网关权限控制"""

    def test_pm_tool_whitelist(self, registry, temp_workspace):
        """测试PM工具白名单"""
        pm_profile = registry.get_profile("pm")
        gateway = RoleToolGateway(pm_profile, temp_workspace)

        # 允许的工具
        can_search, _ = gateway.check_tool_permission("search_code")
        assert can_search is True

        # 禁止的工具
        can_write, reason = gateway.check_tool_permission("write_file")
        assert can_write is False
        assert "代码写入" in reason or "白名单" in reason or "whitelist" in reason.lower()

    def test_director_tool_permissions(self, registry, temp_workspace):
        """测试Director工具权限"""
        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(director_profile, temp_workspace)

        # Director允许代码写入
        can_write, _ = gateway.check_tool_permission("write_file")
        assert can_write is True
        can_create_alias, alias_reason = gateway.check_tool_permission(
            "create_file",
            {"path": "src/app.py", "content": "print('ok')\n"},
        )
        assert can_create_alias is True, alias_reason

        # 但默认禁止删除
        can_delete, _ = gateway.check_tool_permission("delete_file")
        assert can_delete is False

    def test_path_traversal_protection(self, registry, temp_workspace):
        """测试路径穿越保护"""
        pm_profile = registry.get_profile("pm")
        gateway = RoleToolGateway(pm_profile, temp_workspace)

        # 尝试路径穿越
        can_access, reason = gateway.check_tool_permission("read_file", {"path": "../../../etc/passwd"})
        assert can_access is False
        assert "穿越" in reason or "traversal" in reason.lower()

    def test_workspace_absolute_path_is_allowed_for_read_tools(self, registry, temp_workspace):
        """工作区内绝对路径应允许（避免读工具被误拒绝）。"""
        pm_profile = registry.get_profile("pm")
        gateway = RoleToolGateway(pm_profile, temp_workspace)

        inside_file = Path(temp_workspace) / "src" / "expense.py"
        inside_file.parent.mkdir(parents=True, exist_ok=True)
        inside_file.write_text("value = 1\n", encoding="utf-8")

        can_access, reason = gateway.check_tool_permission(
            "read_file",
            {"path": str(inside_file.resolve())},
        )
        assert can_access is True, reason

    def test_workspace_alias_path_is_allowed_for_read_file(self, registry, temp_workspace):
        """常见 /workspace/... 别名应在网关层被视为工作区内路径。"""
        pm_profile = registry.get_profile("pm")
        gateway = RoleToolGateway(pm_profile, temp_workspace)

        can_access, reason = gateway.check_tool_permission(
            "read_file",
            {"path": "/workspace/README.md"},
        )

        assert can_access is True, reason

    def test_dangerous_command_detection(self, registry, temp_workspace):
        """测试危险命令检测"""
        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(director_profile, temp_workspace)

        # 危险命令
        can_run, reason = gateway.check_tool_permission("execute_command", {"command": "rm -rf /"})
        assert can_run is False
        assert "危险" in reason or "dangerous" in reason.lower()

    def test_missing_tool_whitelist_fails_closed(self, temp_workspace):
        """A missing role whitelist must not become executor-level allow-all."""
        profile = SimpleNamespace(
            role_id="director",
            tool_policy=SimpleNamespace(
                policy_id="missing-whitelist",
                whitelist=None,
                blacklist=[],
                allow_code_write=True,
                allow_command_execution=True,
                allow_file_delete=True,
                max_tool_calls_per_turn=10,
            ),
        )
        gateway = RoleToolGateway(profile, temp_workspace)

        allowed, reason = gateway.check_tool_permission("read_file", {"file": "README.md"})

        assert allowed is False
        assert "白名单" in reason or "whitelist" in reason.lower()
        assert gateway._get_allowed_tools_for_executor() == frozenset()

    def test_empty_tool_whitelist_fails_closed(self, temp_workspace):
        """An explicitly empty role whitelist should reject every tool."""
        profile = SimpleNamespace(
            role_id="director",
            tool_policy=SimpleNamespace(
                policy_id="empty-whitelist",
                whitelist=[],
                blacklist=[],
                allow_code_write=True,
                allow_command_execution=True,
                allow_file_delete=True,
                max_tool_calls_per_turn=10,
            ),
        )
        gateway = RoleToolGateway(profile, temp_workspace)

        allowed, reason = gateway.check_tool_permission("write_file", {"file": "a.py", "content": "x = 1\n"})

        assert allowed is False
        assert "白名单" in reason or "whitelist" in reason.lower()
        assert gateway._get_allowed_tools_for_executor() == frozenset()

    def test_execute_tool_respects_executor_failure_payload(
        self,
        registry,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """executor 返回 ok=false 时，网关必须标记失败而不是伪成功。"""
        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(director_profile, temp_workspace)

        class _FakeExecutor:
            def __init__(self, workspace: str, **_kwargs) -> None:
                self.workspace = workspace

            def execute(self, tool_name: str, tool_args: dict):
                return {"ok": False, "error": f"handler_missing:{tool_name}", "args": tool_args}

        import polaris.kernelone.llm.toolkit as llm_toolkit_module

        monkeypatch.setattr(llm_toolkit_module, "AgentAccelToolExecutor", _FakeExecutor)

        result = gateway.execute_tool(
            "write_file",
            {"file": "src/expense/model.py", "content": "value = 1\n"},
        )

        assert result["success"] is False
        assert "handler_missing:write_file" in str(result.get("error") or "")

    def test_canonical_whitelist_preserves_explicit_repo_read_tool(
        self,
        registry,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Canonical auth must preserve explicitly whitelisted read tools."""
        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(director_profile, temp_workspace)

        class _FakeExecutor:
            def __init__(self, workspace: str, **_kwargs) -> None:
                self.workspace = workspace

            def execute(self, tool_name: str, tool_args: dict):
                return {"ok": True, "result": {"tool": tool_name, "args": tool_args}}

        import polaris.kernelone.llm.toolkit as llm_toolkit_module

        monkeypatch.setattr(llm_toolkit_module, "AgentAccelToolExecutor", _FakeExecutor)

        result = gateway.execute_tool("repo_read_head", {"file": "src/utils/helpers.py", "n": 50})
        assert result["success"] is True
        assert result["tool"] == "repo_read_head"

    def test_gateway_authorizes_tool_alias_after_canonicalization(
        self,
        registry,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tool aliases declared by ToolSpecRegistry must authorize through their canonical tool."""
        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(director_profile, temp_workspace)

        class _FakeExecutor:
            def __init__(self, workspace: str, **_kwargs) -> None:
                self.workspace = workspace

            def execute(self, tool_name: str, tool_args: dict):
                return {"ok": True, "result": {"tool": tool_name, "args": tool_args}}

        import polaris.kernelone.llm.toolkit as llm_toolkit_module

        monkeypatch.setattr(llm_toolkit_module, "AgentAccelToolExecutor", _FakeExecutor)

        result = gateway.execute_tool(
            "create_file",
            {"path": "src/app.py", "content": "print('ok')\n"},
        )

        assert result["success"] is True
        assert result["tool"] == "write_file"
        assert result["result"]["tool"] == "write_file"
        assert result["result"]["args"] == {
            "file": "src/app.py",
            "content": "print('ok')\n",
        }

    def test_execute_tools_canonicalizes_llm_create_file_before_gateway_auth(
        self,
        registry,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Batch execution receives parser-level LLM calls and canonicalizes aliases before auth."""
        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(director_profile, temp_workspace)

        class _FakeExecutor:
            def __init__(self, workspace: str, **_kwargs) -> None:
                self.workspace = workspace

            def execute(self, tool_name: str, tool_args: dict):
                return {"ok": True, "result": {"tool": tool_name, "args": tool_args}}

        import polaris.kernelone.llm.toolkit as llm_toolkit_module

        monkeypatch.setattr(llm_toolkit_module, "AgentAccelToolExecutor", _FakeExecutor)

        results = gateway.execute_tools(
            [
                {
                    "tool": "create_file",
                    "args": {"path": "src/app.py", "content": "print('ok')\n"},
                }
            ]
        )

        assert results[0]["success"] is True
        assert results[0]["tool"] == "write_file"
        assert results[0]["result"]["tool"] == "write_file"
        assert results[0]["result"]["args"] == {
            "file": "src/app.py",
            "content": "print('ok')\n",
        }

    def test_execution_count_is_turn_scoped_after_reset(
        self,
        registry,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """工具调用计数应支持按回合重置，不能跨回合污染。"""
        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(director_profile, temp_workspace)

        class _FakeExecutor:
            def __init__(self, workspace: str, **_kwargs) -> None:
                self.workspace = workspace

            def execute(self, tool_name: str, _tool_args: dict):
                return {"ok": True, "result": {"tool": tool_name}}

        import polaris.kernelone.llm.toolkit as llm_toolkit_module

        monkeypatch.setattr(llm_toolkit_module, "AgentAccelToolExecutor", _FakeExecutor)

        for _ in range(director_profile.tool_policy.max_tool_calls_per_turn):
            result = gateway.execute_tool("write_file", {"file": "src/a.py", "content": "a=1\n"})
            assert result.get("success") is True

        with pytest.raises(ToolAuthorizationError):
            gateway.execute_tool("write_file", {"file": "src/b.py", "content": "b=2\n"})

        gateway.reset_execution_count()
        retry_result = gateway.execute_tool("write_file", {"file": "src/c.py", "content": "c=3\n"})
        assert retry_result.get("success") is True

    def test_kernel_gateway_carries_request_task_id(
        self,
        kernel: RoleExecutionKernel,
        registry: RoleProfileRegistry,
        temp_workspace: str,
    ) -> None:
        """Per-request gateway must preserve task identity for tool telemetry."""
        director_profile = registry.get_profile("director")
        executor = KernelToolExecutor(kernel, temp_workspace)

        gateway = executor.create_gateway(
            director_profile,
            RoleTurnRequest(
                mode=RoleExecutionMode.CHAT,
                message="run task",
                run_id="run-task-id",
                task_id="task-77",
                metadata={"session_id": "session-77"},
            ),
        )

        assert isinstance(gateway, RoleToolGateway)
        assert getattr(gateway, "_task_id", None) == "task-77"

    def test_kernel_gateway_derives_capability_scope_from_job_token(
        self,
        kernel: RoleExecutionKernel,
        registry: RoleProfileRegistry,
        temp_workspace: str,
    ) -> None:
        """Per-request gateway must carry immutable job-token write scope."""
        director_profile = registry.get_profile("director")
        executor = KernelToolExecutor(kernel, temp_workspace)

        gateway = executor.create_gateway(
            director_profile,
            RoleTurnRequest(
                mode=RoleExecutionMode.CHAT,
                message="run task",
                run_id="run-scope",
                task_id="task-scope",
                metadata={
                    "job_token": {
                        "allowed_paths": [
                            "./src/allowed.py",
                            "../secret.py",
                            "src/allowed.py",
                            "/etc/passwd",
                        ]
                    }
                },
            ),
        )

        assert isinstance(gateway, RoleToolGateway)
        assert getattr(gateway, "_capability_scope", None) == ("src/allowed.py",)

    def test_kernel_gateway_accepts_platform_job_token_contract(
        self,
        kernel: RoleExecutionKernel,
        registry: RoleProfileRegistry,
        temp_workspace: str,
    ) -> None:
        """Per-request scope extraction must accept the platform JobToken contract."""
        director_profile = registry.get_profile("director")
        executor = KernelToolExecutor(kernel, temp_workspace)
        job_token = JobToken(
            schema_version=1,
            token_id="jt-1",
            run_id="run-platform-token",
            factory_run_id="",
            project_id="project-1",
            stage="director_mutation",
            target_files=["src/from-target.py"],
            allowed_paths=["src/from-token.py"],
        )

        gateway = executor.create_gateway(
            director_profile,
            RoleTurnRequest(
                mode=RoleExecutionMode.CHAT,
                message="run task",
                run_id="run-platform-token",
                task_id="task-platform-token",
                metadata={"job_token": job_token},
            ),
        )

        assert isinstance(gateway, RoleToolGateway)
        assert getattr(gateway, "_capability_scope", None) == ("src/from-token.py", "src/from-target.py")
        capability_token = getattr(gateway, "_capability_token", {})
        assert capability_token["token_id"] == "jt-1"
        assert capability_token["run_id"] == "run-platform-token"
        assert capability_token["project_id"] == "project-1"
        assert capability_token["stage"] == "director_mutation"
        assert capability_token["allowed_scope"] == ["src/from-token.py", "src/from-target.py"]

    def test_gateway_passes_capability_token_to_executor_receipt(
        self,
        registry: RoleProfileRegistry,
        temp_workspace: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RoleToolGateway must preserve token-scoped effect receipts."""
        captured_kwargs: dict[str, object] = {}
        capability_token = {
            "source": "control_plane.job_token",
            "token_id": "jt-receipt",
            "run_id": "run-receipt",
            "project_id": "project-receipt",
            "stage": "director_mutation",
            "contract_hash": "contract-1",
            "blueprint_hash": "blueprint-1",
            "allowed_scope": ["src/app.py"],
        }

        class _FakeExecutor:
            def __init__(self, workspace: str, **kwargs: object) -> None:
                captured_kwargs.clear()
                captured_kwargs.update(kwargs)
                self.workspace = workspace

            def execute(self, tool_name: str, tool_args: dict[str, object]) -> dict[str, object]:
                return {
                    "ok": True,
                    "result": {"tool": tool_name, "args": tool_args},
                    "effect_receipt": {
                        "operation": tool_name,
                        "file": str(tool_args.get("file") or ""),
                        "capability_token": captured_kwargs.get("capability_token"),
                    },
                }

        import polaris.kernelone.llm.toolkit as llm_toolkit_module

        monkeypatch.setattr(llm_toolkit_module, "AgentAccelToolExecutor", _FakeExecutor)

        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(
            director_profile,
            temp_workspace,
            capability_scope=["src/app.py"],
            capability_token=capability_token,
        )

        result = gateway.execute_tool("write_file", {"file": "src/app.py", "content": "value = 1\n"})

        assert captured_kwargs["capability_token"] == capability_token
        assert result["success"] is True
        assert result["effect_receipt"]["operation"] == "write_file"
        assert result["effect_receipt"]["capability_token"] == capability_token

    def test_kernel_gateway_capability_scope_blocks_tool_scope_expansion(
        self,
        kernel: RoleExecutionKernel,
        registry: RoleProfileRegistry,
        temp_workspace: str,
    ) -> None:
        """Tool arguments must not expand the per-turn runtime capability scope."""
        workspace = Path(temp_workspace)
        escaped = workspace / "src" / "escaped.py"
        escaped.parent.mkdir(parents=True, exist_ok=True)
        escaped.write_text("value = 'original'\n", encoding="utf-8")
        director_profile = registry.get_profile("director")
        executor = KernelToolExecutor(kernel, temp_workspace)
        gateway = executor.create_gateway(
            director_profile,
            RoleTurnRequest(
                mode=RoleExecutionMode.CHAT,
                message="run task",
                run_id="run-scope-block",
                task_id="task-scope-block",
                metadata={"job_token": {"allowed_paths": ["src/allowed.py"]}},
            ),
        )

        result = gateway.execute_tool(
            "write_file",
            {
                "file": "src/escaped.py",
                "target_files": ["src/escaped.py"],
                "content": "value = 'changed'\n",
            },
        )

        assert result["success"] is False
        assert result["error_type"] == "director_write_policy_denied"
        policy = result["result"]["director_policy"]
        assert policy["scope_source"] == "runtime_capability"
        assert policy["allowed_scope"] == ["src/allowed.py"]
        assert escaped.read_text(encoding="utf-8") == "value = 'original'\n"

    def test_kernel_gateway_execution_envelope_blocks_write_scope_expansion(
        self,
        kernel: RoleExecutionKernel,
        registry: RoleProfileRegistry,
        temp_workspace: str,
    ) -> None:
        """Execution envelope authorization must be a real write guard."""
        workspace = Path(temp_workspace)
        escaped = workspace / "src" / "escaped_from_envelope.py"
        escaped.parent.mkdir(parents=True, exist_ok=True)
        escaped.write_text("value = 'original'\n", encoding="utf-8")
        director_profile = registry.get_profile("director")
        executor = KernelToolExecutor(kernel, temp_workspace)
        gateway = executor.create_gateway(
            director_profile,
            RoleTurnRequest(
                mode=RoleExecutionMode.CHAT,
                message="run task",
                run_id="run-envelope-scope-block",
                task_id="task-envelope-scope-block",
                metadata={
                    "director_execution_envelope": {
                        "schema_version": "polaris.execution_envelope.v1",
                        "envelope_hash": "env-scope-hash",
                        "pm_contract": {"hash": "pm-hash"},
                        "ce_blueprint": {"hash": "blueprint-hash"},
                        "handoff_decision": {"allowed": True},
                        "authorization": {
                            "capability_token_ref": "job-env-scope",
                            "allowed_write_paths": ["src/allowed.py"],
                            "allowed_commands": ["python --version"],
                        },
                    }
                },
            ),
        )

        result = gateway.execute_tool(
            "write_file",
            {
                "file": "src/escaped_from_envelope.py",
                "target_files": ["src/escaped_from_envelope.py"],
                "content": "value = 'changed'\n",
            },
        )

        assert result["success"] is False
        assert result["error_type"] == "director_write_policy_denied"
        policy = result["result"]["director_policy"]
        assert policy["scope_source"] == "runtime_capability"
        assert policy["allowed_scope"] == ["src/allowed.py"]
        assert policy["capability_token"]["source"] == "director.execution_envelope.authorization"
        assert policy["capability_token"]["execution_envelope_hash"] == "env-scope-hash"
        assert escaped.read_text(encoding="utf-8") == "value = 'original'\n"

    def test_kernel_gateway_execution_envelope_blocks_unlisted_command(
        self,
        kernel: RoleExecutionKernel,
        registry: RoleProfileRegistry,
        temp_workspace: str,
    ) -> None:
        """Execution envelope allowed_commands must be enforced by the executor."""
        director_profile = registry.get_profile("director")
        executor = KernelToolExecutor(kernel, temp_workspace)
        gateway = executor.create_gateway(
            director_profile,
            RoleTurnRequest(
                mode=RoleExecutionMode.CHAT,
                message="run verification",
                run_id="run-envelope-command-block",
                task_id="task-envelope-command-block",
                metadata={
                    "director_execution_envelope": {
                        "schema_version": "polaris.execution_envelope.v1",
                        "envelope_hash": "env-command-hash",
                        "pm_contract": {"hash": "pm-hash"},
                        "ce_blueprint": {"hash": "blueprint-hash"},
                        "handoff_decision": {"allowed": True},
                        "authorization": {
                            "capability_token_ref": "job-env-command",
                            "allowed_write_paths": ["src/main.py"],
                            "allowed_commands": ["python --version"],
                        },
                    }
                },
            ),
        )

        result = gateway.execute_tool(
            "execute_command",
            {"command": "python -m pytest"},
        )

        assert result["success"] is False
        assert result["error_type"] == "command_capability_denied"
        assert result["effect_receipt"]["capability_token"]["allowed_commands"] == ["python --version"]
        assert result["effect_receipt"]["capability_token"]["source"] == ("director.execution_envelope.authorization")
        assert result["effect_receipt"]["capability_token"]["execution_envelope_hash"] == ("env-command-hash")

    @pytest.mark.asyncio
    async def test_kernel_recreates_gateway_for_same_run_different_task_scope(
        self,
        kernel: RoleExecutionKernel,
        registry: RoleProfileRegistry,
        temp_workspace: str,
    ) -> None:
        """Same run_id but different task_id must not reuse stale capability scope."""
        workspace = Path(temp_workspace)
        director_profile = registry.get_profile("director")
        request_a = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="run task A",
            run_id="run-shared",
            task_id="task-a",
            metadata={"job_token": {"allowed_paths": ["src/task-a.py"]}},
        )
        request_b = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="run task B",
            run_id="run-shared",
            task_id="task-b",
            metadata={"job_token": {"allowed_paths": ["src/task-b.py"]}},
        )

        result_a = await kernel._execute_single_tool(
            "write_file",
            {"file": "src/task-a.py", "content": "value = 'a'\n"},
            {"profile": director_profile, "request": request_a},
        )
        result_b = await kernel._execute_single_tool(
            "write_file",
            {"file": "src/task-b.py", "content": "value = 'b'\n"},
            {"profile": director_profile, "request": request_b},
        )

        assert result_a["success"] is True
        assert result_b["success"] is True
        assert (workspace / "src" / "task-a.py").read_text(encoding="utf-8") == 'value = "a"\n'
        assert (workspace / "src" / "task-b.py").read_text(encoding="utf-8") == 'value = "b"\n'

    def test_tool_journal_events_include_task_id(
        self,
        registry: RoleProfileRegistry,
        temp_workspace: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tool call/result events must be attributable to the selected task."""
        runtime_root = tmp_path / "runtime"

        import polaris.cells.storage.layout as storage_layout

        monkeypatch.setattr(
            storage_layout,
            "resolve_polaris_roots",
            lambda _workspace: SimpleNamespace(runtime_root=runtime_root),
        )

        class _FakeExecutor:
            def __init__(self, workspace: str, **_kwargs: object) -> None:
                self.workspace = workspace

            def execute(self, tool_name: str, tool_args: dict[str, object]) -> dict[str, object]:
                return {"ok": True, "result": {"tool": tool_name, "args": tool_args}}

        import polaris.kernelone.llm.toolkit as llm_toolkit_module

        monkeypatch.setattr(llm_toolkit_module, "AgentAccelToolExecutor", _FakeExecutor)

        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(
            director_profile,
            temp_workspace,
            run_id="run-tool-task",
            task_id="task-99",
        )

        result = gateway.execute_tool("write_file", {"file": "src/a.py", "content": "a=1\n"})

        assert result["success"] is True
        journal_path = runtime_root / "events" / "director.llm.events.jsonl"
        events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        tool_events = [event for event in events if event["event"] in {"tool_call", "tool_result"}]
        assert {event["event"] for event in tool_events} == {"tool_call", "tool_result"}
        assert all(event.get("task_id") == "task-99" for event in tool_events)
        assert all(event["data"].get("task_id") == "task-99" for event in tool_events)


class TestPromptFingerprint:
    """测试提示词指纹一致性"""

    def test_fingerprint_consistency(self, kernel):
        """测试相同输入生成相同指纹"""
        RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="测试消息",
        )

        # 构建两次指纹应相同
        profile = kernel.registry.get_profile("pm")

        fp1 = kernel._get_prompt_builder().build_fingerprint(profile, "")
        fp2 = kernel._get_prompt_builder().build_fingerprint(profile, "")

        assert fp1.full_hash == fp2.full_hash

    def test_fingerprint_with_appendix(self, kernel):
        """测试带appendix的指纹"""
        profile = kernel.registry.get_profile("pm")

        fp1 = kernel._get_prompt_builder().build_fingerprint(profile, "")
        fp2 = kernel._get_prompt_builder().build_fingerprint(profile, "额外上下文")

        # 有appendix时指纹应不同
        assert fp1.full_hash != fp2.full_hash
        assert fp2.appendix_hash is not None


class TestRoleExecutionKernel:
    """测试角色执行内核"""

    @pytest.mark.asyncio
    async def test_kernel_basic_execution(self, kernel):
        """测试内核基本执行"""
        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="Hello",
        )

        # 注意：这需要LLM provider配置，如果没有会失败
        # 在CI环境中可以使用mock
        try:
            result = await kernel.run(role="pm", request=request)
            # 验证结果结构
            assert result.content is not None
            assert result.profile_version is not None
            assert result.prompt_fingerprint is not None
            assert result.tool_policy_id is not None
        except Exception as e:
            # 如果没有LLM配置，预期会失败
            if "LLM" in str(e) or "provider" in str(e).lower():
                pytest.skip(f"LLM not configured: {e}")
            raise

    @pytest.mark.asyncio
    async def test_kernel_stream_execution(self, kernel):
        """测试内核流式执行"""
        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="Hello",
        )

        events = []
        try:
            async for event in kernel.run_stream(role="pm", request=request):
                events.append(event)
                if event.get("type") == "complete":
                    break

            assert len(events) > 0
            event_types = [e.get("type") for e in events]
            if "complete" not in event_types and "error" in event_types:
                error_event = next((e for e in events if e.get("type") == "error"), {})
                error_text = str(error_event.get("error") or "").lower()
                if any(
                    token in error_text
                    for token in ("llm", "provider", "api key", "unauthorized", "aiohttp", "stream-session")
                ):
                    pytest.skip(f"LLM not configured: {error_text}")
            assert "fingerprint" in event_types
            assert "complete" in event_types
        except Exception as e:
            if "LLM" in str(e) or "provider" in str(e).lower():
                pytest.skip(f"LLM not configured: {e}")
            raise

    @pytest.mark.asyncio
    async def test_kernel_execute_tools_handles_authorization_error(self, kernel, registry, monkeypatch):
        """内核工具执行遇到授权失败时应返回失败结果而非抛异常中断。"""
        from polaris.cells.roles.kernel.internal.kernel import tool_executor as te_module

        profile = registry.get_profile("pm")

        class _FakeGateway:
            def reset_execution_count(self) -> None:
                return None

            def execute_tool(self, _tool: str, _args: dict) -> dict:
                raise ToolAuthorizationError("forbidden_tool")

            def close(self) -> None:
                return None

        def fake_create_gateway(self, _profile, _request, _tool_gateway=None):
            return _FakeGateway()

        monkeypatch.setattr(te_module.KernelToolExecutor, "create_gateway", fake_create_gateway)

        calls = [SimpleNamespace(tool="write_file", args={"file": "a.py", "content": "x=1\n"})]
        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="执行写入",
            max_retries=0,
        )
        results = await kernel._execute_tools(profile, request, calls)

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["success"] is False
        assert results[0]["authorized"] is False
        assert "forbidden_tool" in str(results[0]["error"])

    @pytest.mark.asyncio
    async def test_kernel_director_empty_output_with_validation_reports_error(self, kernel, monkeypatch):
        """Director 空输出在 validate_output=True 时必须失败，不能误判成功。"""

        class _FakeLLMCaller:
            async def call(self, *args, **kwargs):
                return SimpleNamespace(
                    content="",
                    token_estimate=0,
                    error=None,
                    error_category=None,
                    metadata={},
                )

        kernel.inject_llm_caller(_FakeLLMCaller())

        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="请执行任务",
            validate_output=True,
            max_retries=0,
        )

        result = await kernel.run(role="director", request=request)
        assert str(result.error or "").strip()
        error_text = str(result.error or "")
        assert (
            "验证失败" in error_text
            or "assistant_visible_output_empty" in error_text
            or "model returned no visible output" in error_text
        )

    @pytest.mark.asyncio
    async def test_kernel_tool_only_thinking_turn_reports_empty_visible_output(self, kernel, monkeypatch):
        """当工具调用仅出现在 <thinking> 中时，内核必须拒绝把 thinking 当成可执行工具。"""

        class _FakeLLMCaller:
            async def call(self, *args, **kwargs):
                return SimpleNamespace(
                    content=(
                        "<thinking>\n"
                        "[WRITE_FILE]\n"
                        "file: src/expense/model.py\n"
                        "content: print('ok')\n"
                        "[/WRITE_FILE]\n"
                        "</thinking>"
                    ),
                    token_estimate=0,
                    error=None,
                    error_category=None,
                    metadata={},
                )

        class _FakeQualityChecker:
            def validate_output(self, *args, **kwargs):
                raise AssertionError("validate_output should not run after thinking-only rejection")

        kernel.inject_llm_caller(_FakeLLMCaller())
        kernel._injected_quality_checker = _FakeQualityChecker()

        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="执行代码修改",
            validate_output=True,
            max_retries=0,
        )

        result = await kernel.run(role="director", request=request)

        error_text = str(result.error or "")
        assert (
            "assistant_visible_output_empty" in error_text
            or "single_batch_contract_violation" in error_text
            or "no visible output" in error_text
        )
        assert len(result.tool_calls) == 0
        assert len(result.tool_results) == 0
        assert result.is_complete is False

    @pytest.mark.asyncio
    async def test_kernel_accepts_native_tool_calls_without_text_output(self, kernel, monkeypatch):
        """当模型返回原生 tool_calls 且文本为空时，内核应直接执行工具。"""
        call_count = {"value": 0}

        class _FakeLLMCaller:
            async def call(self, *args, **kwargs):
                nonlocal call_count
                call_count["value"] += 1
                if call_count["value"] == 1:
                    return SimpleNamespace(
                        content="",
                        token_estimate=0,
                        error=None,
                        error_category=None,
                        tool_calls=[
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"tui_runtime.md"}',
                                },
                            }
                        ],
                        tool_call_provider="openai",
                        metadata={},
                    )
                return SimpleNamespace(
                    content="检查完成",
                    token_estimate=0,
                    error=None,
                    error_category=None,
                    tool_calls=[],
                    tool_call_provider="openai",
                    metadata={},
                )

        class _FakeToolExecutor:
            async def execute(self, tool_name, args, context=None):
                return {"tool": tool_name, "success": True, "result": {"exists": True}}

        captured = {"count": 0, "tool": ""}

        class _FakeToolExecutorWithCapture:
            async def execute(self, tool_name, args, context=None):
                nonlocal captured
                captured["count"] += 1
                captured["tool"] = tool_name
                return {"tool": captured["tool"], "success": True, "result": {"exists": True}}

        kernel.inject_llm_caller(_FakeLLMCaller())
        kernel.inject_tool_executor(_FakeToolExecutorWithCapture())

        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="读取 README 内容",
            validate_output=False,
            max_retries=0,
        )

        result = await kernel.run(role="pm", request=request)

        assert result.error in (None, "")
        assert captured["count"] == 1
        assert captured["tool"] == "read_file"
        assert len(result.tool_calls) == 1
        assert len(result.tool_results) == 1

    def test_extract_structured_tool_calls_supports_mainstream_shapes(self, kernel):
        """结构化输出中的多种 tool_call 形态都应被规范化。"""
        payload = {
            "tool_calls": [
                {"tool": "search_code", "arguments": {"query": "TODO"}},
                {"name": "read_file", "args": {"path": "tui_runtime.md"}},
                {
                    "function": {
                        "name": "file_exists",
                        "arguments": '{"path":"setup.py"}',
                    }
                },
            ]
        }

        calls = extract_structured_tool_calls(payload)

        assert len(calls) == 3
        assert calls[0]["function"]["name"] == "search_code"
        assert json.loads(calls[0]["function"]["arguments"]) == {"query": "TODO"}
        assert calls[1]["function"]["name"] == "read_file"
        assert json.loads(calls[1]["function"]["arguments"]) == {"path": "tui_runtime.md"}
        assert calls[2]["function"]["name"] == "file_exists"
        assert json.loads(calls[2]["function"]["arguments"]) == {"path": "setup.py"}

    @pytest.mark.asyncio
    async def test_kernel_normalizes_structured_tool_calls_for_execution(
        self,
        temp_workspace,
        registry,
        monkeypatch,
    ):
        """开启 structured_output 时应把 response_model 透传给 LLM 调用。"""
        pytest.skip(
            "RoleExecutionKernel no longer exposes the legacy private _llm_caller path; covered by runtime tests"
        )
        structured_kernel = RoleExecutionKernel(
            workspace=temp_workspace,
            registry=registry,
            use_structured_output=True,
        )

        captured = {"native_tool_calls": [], "count": 0, "tool": "", "response_model": None}
        call_count = {"value": 0}

        async def fake_call(*_args, **_kwargs):
            captured["response_model"] = _kwargs.get("response_model")
            call_count["value"] += 1
            if call_count["value"] == 1:
                return SimpleNamespace(
                    content="",
                    token_estimate=0,
                    error=None,
                    error_category=None,
                    tool_calls=[
                        {
                            "id": "native_call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"tui_runtime.md"}',
                            },
                        }
                    ],
                    tool_call_provider="openai",
                    metadata={},
                )
            return SimpleNamespace(
                content="检查完成",
                token_estimate=0,
                error=None,
                error_category=None,
                tool_calls=[],
                tool_call_provider="openai",
                metadata={},
            )

        def fake_parse_execution_tool_calls(_text, *_, **kwargs):
            native_tool_calls = kwargs.get("native_tool_calls")
            if native_tool_calls:
                captured["native_tool_calls"] = native_tool_calls
                return [SimpleNamespace(tool="read_file", args={"path": "tui_runtime.md"})]
            return []

        async def fake_execute_single_tool(*args, **kwargs):
            call = kwargs.get("call")
            if call is None and args:
                call = args[-1]
            captured["count"] += 1
            captured["tool"] = str(getattr(call, "tool", "") or "")
            return {"tool": captured["tool"], "success": True, "result": {"exists": True}}

        monkeypatch.setattr(structured_kernel._llm_caller, "call", fake_call)
        monkeypatch.setattr(
            structured_kernel._output_parser,
            "parse_execution_tool_calls",
            fake_parse_execution_tool_calls,
        )
        monkeypatch.setattr(structured_kernel, "_execute_single_tool", fake_execute_single_tool)

        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="读取 README 内容",
            validate_output=False,
            max_retries=0,
        )
        result = await structured_kernel.run(role="pm", request=request)

        assert result.error in (None, "")
        assert captured["count"] == 1
        assert captured["tool"] == "read_file"
        assert captured["response_model"] is not None
        assert isinstance(captured["native_tool_calls"], list)
        assert len(captured["native_tool_calls"]) == 1
        assert captured["native_tool_calls"][0]["function"]["name"] == "read_file"

    @pytest.mark.asyncio
    async def test_kernel_retries_when_tool_execution_fails(self, kernel, monkeypatch):
        """工具执行失败时，内核必须触发重试并在重试耗尽后返回错误。"""
        pytest.skip("Legacy private kernel retry hook replaced by TransactionKernel retry coverage")

        llm_call_count = {"value": 0}

        async def fake_call(*_args, **_kwargs):
            llm_call_count["value"] += 1
            return SimpleNamespace(
                content="PATCH_FILE: src/fastapi_entrypoint.py\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\nEND PATCH_FILE",
                token_estimate=0,
                error=None,
                error_category=None,
                metadata={},
            )

        async def fake_execute_single_tool(*_args, **_kwargs):
            return {
                "tool": "write_file",
                "success": False,
                "error": "mock_tool_error",
            }

        monkeypatch.setattr(kernel._llm_caller, "call", fake_call)
        monkeypatch.setattr(
            kernel._quality_checker,
            "validate_output",
            lambda *_args, **_kwargs: SimpleNamespace(
                success=True,
                errors=[],
                suggestions=[],
                quality_score=100.0,
                data={},
            ),
        )
        monkeypatch.setattr(
            kernel._output_parser,
            "parse_execution_tool_calls",
            lambda *_args, **_kwargs: [
                SimpleNamespace(tool="write_file", args={"file": "src/fastapi_entrypoint.py", "content": "y"})
            ],
        )
        monkeypatch.setattr(
            kernel._output_parser,
            "parse_structured_output",
            lambda *_args, **_kwargs: {},
        )
        monkeypatch.setattr(kernel, "_execute_single_tool", fake_execute_single_tool)

        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="请执行任务",
            validate_output=True,
            max_retries=1,
        )
        result = await kernel.run(role="director", request=request)

        assert llm_call_count["value"] >= 1
        assert str(result.error or "").strip()


class TestWorkflowAdapter:
    """测试工作流适配器"""

    def test_adapter_initialization(self, temp_workspace, registry):
        """测试适配器初始化"""
        adapter = WorkflowRoleAdapter(
            workspace=temp_workspace,
            registry=registry,
        )

        assert adapter.runtime_entrypoint == "roles.runtime.execute_role_session"

    @pytest.mark.asyncio
    async def test_adapter_execute(self, temp_workspace, registry, monkeypatch):
        """测试适配器执行"""
        adapter = WorkflowRoleAdapter(
            workspace=temp_workspace,
            registry=registry,
        )

        class _FakeRoleRuntimeService:
            async def execute_role_session(self, command):
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role=command.role,
                    workspace=command.workspace,
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    output="分析完成",
                    metadata={
                        "profile_version": "test-profile",
                        "prompt_fingerprint": "fp-test",
                        "context_os_snapshot_loaded": True,
                    },
                )

        monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", lambda: _FakeRoleRuntimeService())

        result = await adapter.execute_role(
            role="pm",
            message="分析需求",
            task_id="TEST-001",
        )

        assert result.role == "pm"
        assert result.profile_version == "test-profile"
        assert result.prompt_fingerprint == "fp-test"
        assert result.metadata["role_runtime_entrypoint"] == "roles.runtime.execute_role_session"
        assert result.metadata["context_os_expected"] is True

    @pytest.mark.asyncio
    async def test_adapter_propagates_validate_output_flag(self, temp_workspace, registry, monkeypatch):
        """Workflow adapter 必须把 validate_output 透传给 RoleRuntime command metadata。"""
        adapter = WorkflowRoleAdapter(
            workspace=temp_workspace,
            registry=registry,
        )

        captured = {"validate_output": None}

        class _FakeRoleRuntimeService:
            async def execute_role_session(self, command):
                captured["validate_output"] = bool(command.metadata.get("validate_output", True))
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role=command.role,
                    workspace=command.workspace,
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    output="ok",
                    metadata={"tool_policy_id": "policy"},
                )

        monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", lambda: _FakeRoleRuntimeService())

        result = await adapter.execute_role(
            role="pm",
            message="分析需求",
            validate_output=False,
        )

        assert captured["validate_output"] is False
        assert result.success is True


class TestDataStore:
    """测试数据存储"""

    def test_data_store_creation(self, registry, temp_workspace):
        """测试数据存储创建"""
        from polaris.cells.roles.runtime.public.service import RoleDataStore

        pm_profile = registry.get_profile("pm")
        store = RoleDataStore(pm_profile, temp_workspace)

        # 验证目录结构
        assert store.base_dir.exists()
        assert store.data_dir.exists()
        assert store.logs_dir.exists()
        assert store.outputs_dir.exists()
        assert store.backups_dir.exists()

    def test_data_store_write_read(self, registry, temp_workspace):
        """测试数据存储读写"""
        from polaris.cells.roles.runtime.public.service import RoleDataStore

        pm_profile = registry.get_profile("pm")
        store = RoleDataStore(pm_profile, temp_workspace)

        # 写入JSON
        test_data = {"test": "data", "version": 1}
        store.write_json("test.json", test_data)

        # 读取
        read_data = store.read_json("test.json")
        assert read_data == test_data

    def test_data_store_path_security(self, registry, temp_workspace):
        """测试路径安全"""
        from polaris.cells.roles.runtime.public.service import PathSecurityError, RoleDataStore

        pm_profile = registry.get_profile("pm")
        store = RoleDataStore(pm_profile, temp_workspace)

        # 尝试路径穿越
        with pytest.raises(PathSecurityError):
            store.write_text("../../../etc/passwd", "test")

    def test_data_store_extension_whitelist(self, registry, temp_workspace):
        """测试扩展名白名单"""
        from polaris.cells.roles.runtime.public.service import PathSecurityError, RoleDataStore

        pm_profile = registry.get_profile("pm")
        store = RoleDataStore(pm_profile, temp_workspace)

        # 禁止的扩展名
        with pytest.raises(PathSecurityError):
            store.write_text("test.exe", "test")


class TestChatWorkflowConsistency:
    """测试聊天模式和工作流模式的一致性"""

    def test_same_role_same_fingerprint(self, kernel):
        """测试同角色在不同模式下指纹一致"""
        profile = kernel.registry.get_profile("pm")

        RoleTurnRequest(mode=RoleExecutionMode.CHAT, message="test")
        RoleTurnRequest(mode=RoleExecutionMode.WORKFLOW, message="test")

        # 构建指纹（不依赖LLM调用）
        chat_fp = kernel._get_prompt_builder().build_fingerprint(profile, "")
        workflow_fp = kernel._get_prompt_builder().build_fingerprint(profile, "")

        # 同角色同appendix时指纹应相同
        assert chat_fp.full_hash == workflow_fp.full_hash


class TestMigrationCompat:
    """测试迁移兼容性"""

    def test_deprecated_params_handling(self, kernel):
        """测试废弃参数处理"""
        import warnings

        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            message="test",
            system_prompt="自定义系统提示词",  # 废弃参数
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            appendix = kernel._process_deprecated_params(request)

            # 应发出废弃警告
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)

        # system_prompt 应被转为 appendix
        assert "自定义系统提示词" in (appendix or "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
