"""Workflow Role Node - 统一工作流角色节点

为 PM/Director/CE/QA 工作流节点提供基于 RoleRuntime 合同层的统一基类。

使用示例:
    from polaris.cells.roles.runtime.public.service.workflow_node import WorkflowRoleNode

    class PMNodeV2(WorkflowRoleNode):
        @property
        def role_id(self) -> str:
            return "pm"

        async def execute(self, context: RoleContext) -> RoleResult:
            return await self.execute_kernel(
                message="分析需求并创建任务",
                task_id=context.task_id,
            )
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from polaris.cells.roles.profile.public.service import (
    RoleProfileRegistry,
    load_core_roles,
    profile_to_dict,
)

from .workflow_adapter import WorkflowRoleAdapter, WorkflowRoleResult

logger = logging.getLogger(__name__)


class WorkflowRoleNode(ABC):
    """统一工作流角色节点基类

    基于 RoleRuntime 的角色工作流节点实现。
    """

    def __init__(
        self,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
    ) -> None:
        self.workspace = workspace
        self.registry = registry or RoleProfileRegistry()
        self._adapter = WorkflowRoleAdapter(workspace=workspace, registry=self.registry)

        # 确保核心角色配置已加载
        self._ensure_core_roles_loaded()

    @property
    @abstractmethod
    def role_id(self) -> str:
        """角色标识 (pm/architect/chief_engineer/director/qa)"""
        ...

    @property
    def role_name(self) -> str:
        """角色显示名（默认从profile获取）"""
        profile = self.registry.get_profile(self.role_id)
        return profile.display_name if profile else self.role_id

    def _ensure_core_roles_loaded(self) -> None:
        """确保核心角色配置已加载"""
        if not self.registry.list_roles():
            load_core_roles()

    @property
    def runtime_entrypoint(self) -> str:
        """返回生产执行入口，便于测试和审计。"""
        return self._adapter.runtime_entrypoint

    async def execute_kernel(
        self,
        message: str,
        task_id: str | None = None,
        context: dict[str, Any] | None = None,
        prompt_appendix: str | None = None,
        history: list[tuple] | None = None,
        validate_output: bool = True,
        handle_tools: bool = True,
        max_tool_rounds: int = 5,
    ) -> WorkflowRoleResult:
        """通过内核执行角色

        Args:
            message: 用户消息/指令
            task_id: 关联的任务ID
            context: 额外上下文信息
            prompt_appendix: 追加提示词
            history: 历史消息
            validate_output: 是否验证输出
            handle_tools: 是否自动处理工具调用
            max_tool_rounds: 最大工具调用轮数

        Returns:
            WorkflowRoleResult
        """
        _ = handle_tools, max_tool_rounds
        return await self._adapter.execute_role(
            role=self.role_id,
            message=message,
            task_id=task_id,
            context=context,
            prompt_appendix=prompt_appendix,
            history=history,
            validate_output=validate_output,
        )

    def get_profile(self) -> dict[str, Any] | None:
        """获取角色配置"""
        profile = self.registry.get_profile(self.role_id)
        if profile:
            return profile_to_dict(profile)
        return None

    def validate_tool_permission(self, tool_name: str, tool_args: dict | None = None) -> tuple[bool, str]:
        """验证工具权限"""
        from polaris.cells.roles.kernel.public.service import RoleToolGateway

        profile = self.registry.get_profile(self.role_id)
        if not profile:
            return False, f"未知角色: {self.role_id}"

        gateway = RoleToolGateway(profile, self.workspace)
        return gateway.check_tool_permission(tool_name, tool_args)


# 便捷函数：快速执行工作流角色
async def run_workflow_role(role: str, message: str, workspace: str = "", **kwargs) -> WorkflowRoleResult:
    """便捷函数：快速执行工作流角色

    Args:
        role: 角色标识
        message: 消息
        workspace: 工作区路径
        **kwargs: 其他参数

    Returns:
        WorkflowRoleResult
    """

    class _AdhocNode(WorkflowRoleNode):
        @property
        def role_id(self) -> str:
            return role

    node = _AdhocNode(workspace=workspace)
    return await node.execute_kernel(message=message, **kwargs)
