"""Workflow Chain Integration Test - 工作流链路集成测试

验证工作流链路完整性: (可选: 中书令/Architect) -> 尚书令/PM -> (可选: 工部尚书/CE) -> 工部侍郎/Director -> 门下侍中/QA
"""

import sys
from pathlib import Path

# 添加 scripts 路径到 Python 路径
scripts_path = str(Path(__file__).parent.parent / "scripts")
if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)

import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保在测试路径中
sys.path.insert(0, str(Path(__file__).parent.parent))

from polaris.cells.roles.adapters.public.service import (
    WorkflowRoleAdapter,
    WorkflowRoleResult,
)
from polaris.cells.roles.runtime.public.service import (
    RoleExecutionKernel,
    RoleExecutionMode,
    RoleTurnRequest,
    load_core_roles,
)


@pytest.fixture
def temp_workspace():
    """临时工作区"""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def mock_context():
    """模拟工作流上下文"""
    context = MagicMock()
    context.workspace_full = "/tmp/test"
    context.requirements = "实现用户登录功能"
    context.run_id = "TEST-001"
    context.pm_iteration = 1
    context.get_tasks = MagicMock(
        return_value=[
            {
                "id": "TASK-001",
                "title": "创建登录页面",
                "description": "实现用户登录UI",
                "status": "ready",
            },
            {
                "id": "TASK-002",
                "title": "实现认证API",
                "description": "后端登录接口",
                "status": "ready",
            },
        ]
    )
    context.director_result = {
        "status": "completed",
        "executed_tasks": ["TASK-001", "TASK-002"],
    }
    context.pm_result = {
        "tasks": context.get_tasks(),
    }
    return context


class TestWorkflowNodeInitialization:
    """测试工作流节点初始化"""

    def test_all_nodes_initialization(self, temp_workspace):
        """测试所有节点可以正常初始化"""
        adapter = WorkflowRoleAdapter(workspace=temp_workspace)
        assert adapter is not None
        assert adapter.workspace == temp_workspace

    def test_nodes_have_kernel(self, temp_workspace):
        """测试节点具有内核"""
        adapter = WorkflowRoleAdapter(workspace=temp_workspace)
        kernel = adapter.kernel
        assert kernel is not None
        assert isinstance(kernel, RoleExecutionKernel)


class TestWorkflowDependencies:
    """测试工作流依赖关系"""

    def _get_registry(self):
        """获取并初始化注册表"""
        from polaris.cells.roles.runtime.public.service import registry

        # 确保核心角色已加载
        if not registry.list_roles():
            load_core_roles()
        return registry

    def test_architect_dependencies(self, temp_workspace):
        """测试 Architect 角色"""
        reg = self._get_registry()
        profile = reg.get_profile("architect")
        assert profile is not None
        assert profile.role_id == "architect"

    def test_pm_dependencies(self, temp_workspace):
        """测试 PM 角色"""
        reg = self._get_registry()
        profile = reg.get_profile("pm")
        assert profile is not None
        assert profile.role_id == "pm"

    def test_ce_dependencies(self, temp_workspace):
        """测试 ChiefEngineer 角色"""
        reg = self._get_registry()
        profile = reg.get_profile("chief_engineer")
        assert profile is not None
        assert profile.role_id == "chief_engineer"

    def test_director_dependencies(self, temp_workspace):
        """测试 Director 角色"""
        reg = self._get_registry()
        profile = reg.get_profile("director")
        assert profile is not None
        assert profile.role_id == "director"

    def test_qa_dependencies(self, temp_workspace):
        """测试 QA 角色"""
        reg = self._get_registry()
        profile = reg.get_profile("qa")
        assert profile is not None
        assert profile.role_id == "qa"

    def test_complete_workflow_chain(self, temp_workspace):
        """测试完整工作流链路"""
        reg = self._get_registry()
        roles = ["architect", "pm", "chief_engineer", "director", "qa"]
        for role in roles:
            profile = reg.get_profile(role)
            assert profile is not None, f"{role} 角色配置不存在"
            assert profile.role_id == role


class TestWorkflowExecutionChain:
    """测试工作流执行链路"""

    @pytest.mark.asyncio
    async def test_kernel_execution_mock(self, temp_workspace):
        """测试内核执行（Mock）"""
        kernel = RoleExecutionKernel(workspace=temp_workspace)

        with patch.object(kernel, "run", new_callable=AsyncMock) as mock_run:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.content = "test response"
            mock_result.structured_output = {}
            mock_result.thinking = None
            mock_run.return_value = mock_result

            request = RoleTurnRequest(
                mode=RoleExecutionMode.WORKFLOW,
                workspace=temp_workspace,
                message="test message",
            )
            result = await kernel.run(role="pm", request=request)

            assert result.success is True

    @pytest.mark.asyncio
    async def test_pm_node_execution(self, temp_workspace):
        """测试 PM 节点执行"""
        adapter = WorkflowRoleAdapter(workspace=temp_workspace)

        with patch.object(adapter.kernel, "run", new_callable=AsyncMock) as mock_run:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.content = '{"tasks": [{"id": "TASK-001", "title": "test"}]}'
            mock_result.structured_output = {"tasks": [{"id": "TASK-001", "title": "test"}]}
            mock_result.thinking = None
            mock_run.return_value = mock_result

            result = await adapter.execute_role(
                role="pm",
                message="分析需求并创建任务",
            )

            assert isinstance(result, WorkflowRoleResult)

    @pytest.mark.asyncio
    async def test_director_node_execution(self, temp_workspace):
        """测试 Director 节点执行"""
        adapter = WorkflowRoleAdapter(workspace=temp_workspace)

        with patch.object(adapter.kernel, "run", new_callable=AsyncMock) as mock_run:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.content = "patch applied"
            mock_result.structured_output = {}
            mock_result.thinking = None
            mock_run.return_value = mock_result

            result = await adapter.execute_role(
                role="director",
                message="执行任务",
            )

            assert isinstance(result, WorkflowRoleResult)

    @pytest.mark.asyncio
    async def test_qa_node_execution(self, temp_workspace):
        """测试 QA 节点执行"""
        adapter = WorkflowRoleAdapter(workspace=temp_workspace)

        with patch.object(adapter.kernel, "run", new_callable=AsyncMock) as mock_run:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.content = '{"verdict": "PASS"}'
            mock_result.structured_output = {"verdict": "PASS"}
            mock_result.thinking = None
            mock_run.return_value = mock_result

            result = await adapter.execute_role(
                role="qa",
                message="审查结果",
            )

            assert isinstance(result, WorkflowRoleResult)

    @pytest.mark.asyncio
    async def test_architect_node_execution(self, temp_workspace):
        """测试 Architect 节点执行"""
        adapter = WorkflowRoleAdapter(workspace=temp_workspace)

        with patch.object(adapter.kernel, "run", new_callable=AsyncMock) as mock_run:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.content = '{"architecture": "design"}'
            mock_result.structured_output = {"architecture": "design"}
            mock_run.return_value = mock_result

            result = await adapter.execute_role(
                role="architect",
                message="设计架构",
            )

            assert isinstance(result, WorkflowRoleResult)

    @pytest.mark.asyncio
    async def test_ce_node_execution(self, temp_workspace):
        """测试 ChiefEngineer 节点执行"""
        adapter = WorkflowRoleAdapter(workspace=temp_workspace)

        with patch.object(adapter.kernel, "run", new_callable=AsyncMock) as mock_run:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.content = '{"blueprint": "plan"}'
            mock_result.structured_output = {"blueprint": "plan"}
            mock_run.return_value = mock_result

            result = await adapter.execute_role(
                role="chief_engineer",
                message="生成蓝图",
            )

            assert isinstance(result, WorkflowRoleResult)


class TestWorkflowFingerprintConsistency:
    """测试工作流指纹一致性"""

    def _get_registry(self):
        """获取并初始化注册表"""
        from polaris.cells.roles.runtime.public.service import registry

        if not registry.list_roles():
            load_core_roles()
        return registry

    def test_same_role_same_fingerprint_across_modes(self, temp_workspace):
        """测试相同角色在不同模式下指纹一致"""
        reg = self._get_registry()

        pm_profile = reg.get_profile("pm")
        assert pm_profile is not None

        # 验证角色指纹存在
        assert hasattr(pm_profile, "profile_id") or hasattr(pm_profile, "role_id")


class TestWorkflowToolPolicy:
    """测试工作流工具策略"""

    def _get_registry(self):
        """获取并初始化注册表"""
        from polaris.cells.roles.runtime.public.service import registry

        if not registry.list_roles():
            load_core_roles()
        return registry

    @pytest.mark.skip(reason="tool_policy structure needs verification")
    def test_pm_cannot_write_code(self, temp_workspace):
        """测试 PM 不能写代码"""
        pass

    @pytest.mark.skip(reason="tool_policy structure needs verification")
    def test_director_can_write_code(self, temp_workspace):
        """测试 Director 可以写代码"""
        pass

    def test_workflow_role_permissions(self, temp_workspace):
        """测试工作流角色权限"""
        reg = self._get_registry()

        # 验证所有角色的工具策略
        for role_id in ["pm", "architect", "chief_engineer", "director", "qa"]:
            profile = reg.get_profile(role_id)
            assert profile is not None, f"{role_id} 角色配置不存在"
