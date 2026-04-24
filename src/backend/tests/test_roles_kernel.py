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

from polaris.cells.roles.runtime.public.service import (
    RoleExecutionKernel,
    RoleExecutionMode,
    RoleProfileRegistry,
    RoleToolGateway,
    RoleTurnRequest,
    ToolAuthorizationError,
    WorkflowRoleAdapter,
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
        assert pm_profile.display_name == "尚书令 (Prime Minister)"

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

    def test_whitelist_gate_runs_before_llm_mapping(
        self,
        registry,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """白名单拦截应基于请求层工具名，而非映射后的执行工具名。"""
        director_profile = registry.get_profile("director")
        gateway = RoleToolGateway(director_profile, temp_workspace)

        class _FakeExecutor:
            def __init__(self, workspace: str, **_kwargs) -> None:
                self.workspace = workspace

            def execute(self, tool_name: str, tool_args: dict):
                return {"ok": True, "result": {"tool": tool_name, "args": tool_args}}

        import polaris.kernelone.llm.toolkit as llm_toolkit_module

        monkeypatch.setattr(llm_toolkit_module, "AgentAccelToolExecutor", _FakeExecutor)

        # Director 白名单包含 repo_read_head，不包含 read_file。
        # 若先映射再拦截会被拒绝；正确行为是先放行 repo_read_head，再允许映射执行。
        result = gateway.execute_tool("repo_read_head", {"file": "src/utils/helpers.py", "n": 50})
        assert result["success"] is True
        assert result["tool"] == "repo_read_head"

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
                if any(token in error_text for token in ("llm", "provider", "api key", "unauthorized")):
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
        assert "验证失败" in str(result.error or "") or "assistant_visible_output_empty" in str(result.error or "")

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

        assert "assistant_visible_output_empty" in str(result.error or "")
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

        calls = kernel._extract_structured_tool_calls(payload)

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

        # 验证内核已创建
        assert adapter.kernel is not None

    @pytest.mark.asyncio
    async def test_adapter_execute(self, temp_workspace, registry):
        """测试适配器执行"""
        adapter = WorkflowRoleAdapter(
            workspace=temp_workspace,
            registry=registry,
        )

        try:
            result = await adapter.execute_role(
                role="pm",
                message="分析需求",
                task_id="TEST-001",
            )

            # 验证结果
            assert result.role == "pm"
            assert result.profile_version is not None
            assert result.prompt_fingerprint is not None
        except Exception as e:
            if "LLM" in str(e) or "provider" in str(e).lower():
                pytest.skip(f"LLM not configured: {e}")
            raise

    @pytest.mark.asyncio
    async def test_adapter_propagates_validate_output_flag(self, temp_workspace, registry, monkeypatch):
        """Workflow adapter 必须把 validate_output 透传给 RoleTurnRequest。"""
        adapter = WorkflowRoleAdapter(
            workspace=temp_workspace,
            registry=registry,
        )

        captured = {"validate_output": None}

        async def fake_run(*_args, **_kwargs):
            request = _kwargs.get("request")
            captured["validate_output"] = bool(getattr(request, "validate_output", True))
            return SimpleNamespace(
                content="ok",
                thinking=None,
                structured_output={},
                tool_calls=[],
                tool_results=[],
                profile_version="test",
                prompt_fingerprint=None,
                tool_policy_id="policy",
                is_complete=True,
                error=None,
            )

        monkeypatch.setattr(adapter.kernel, "run", fake_run)

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
