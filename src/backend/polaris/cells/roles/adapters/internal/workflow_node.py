"""Workflow Role Node - 统一工作流角色节点

为 PM/Director/CE/QA 工作流节点提供基于 RoleExecutionKernel 的统一基类。

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

from polaris.cells.roles.kernel.public.service import RoleExecutionKernel
from polaris.cells.roles.profile.public.service import (
    RoleExecutionMode,
    RoleProfileRegistry,
    RoleTurnRequest,
    load_core_roles,
    profile_to_dict,
)

from .workflow_adapter import WorkflowRoleResult

logger = logging.getLogger(__name__)


class WorkflowRoleNode(ABC):
    """统一工作流角色节点基类

    基于 RoleExecutionKernel 的角色工作流节点实现。
    """

    def __init__(
        self,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
    ) -> None:
        self.workspace = workspace
        self.registry = registry or RoleProfileRegistry()
        self._kernel: RoleExecutionKernel | None = None

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
    def kernel(self) -> RoleExecutionKernel:
        """获取或创建执行内核"""
        if self._kernel is None:
            self._kernel = RoleExecutionKernel(
                workspace=self.workspace,
                registry=self.registry,
            )
        return self._kernel

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
        # 构建内核请求
        request = RoleTurnRequest(
            mode=RoleExecutionMode.WORKFLOW,
            workspace=self.workspace,
            message=message,
            history=history or [],
            prompt_appendix=prompt_appendix,
            context_override=context,
            task_id=task_id,
        )

        # 执行内核
        result = await self.kernel.run(role=self.role_id, request=request)

        # 如果需要自动处理工具调用
        if handle_tools and result.tool_calls and not result.is_complete:
            return await self._handle_tool_rounds(
                request=request,
                initial_result=result,
                max_rounds=max_tool_rounds,
            )

        # 转换为工作流结果
        return WorkflowRoleResult.from_kernel_result(result, self.role_id)

    async def _handle_tool_rounds(
        self,
        request: RoleTurnRequest,
        initial_result: Any,
        max_rounds: int,
    ) -> WorkflowRoleResult:
        """Handle multi-turn tool calls using transcript-driven history injection.

        Rounds accumulate (role, content) tuples in history so that the LLM
        receives a faithful transcript rather than a string-concatenated prompt.
        """
        current_result = initial_result
        all_tool_results: list[dict[str, Any]] = list(initial_result.tool_results or [])

        # Seed history with the original user message.
        accumulated_history: list[tuple[str, str]] = list(request.history or [])
        if request.message:
            accumulated_history.append(("user", request.message))

        for _round_num in range(max_rounds):
            if not current_result.tool_calls or current_result.is_complete:
                break

            # Build the next request with transcript-driven history.
            next_request = RoleTurnRequest(
                mode=RoleExecutionMode.WORKFLOW,
                workspace=request.workspace,
                message="",  # driven by history
                history=list(accumulated_history),
                prompt_appendix=request.prompt_appendix,
                context_override=request.context_override,
                task_id=request.task_id,
            )

            current_result = await self.kernel.run(role=self.role_id, request=next_request)
            if current_result.tool_results:
                all_tool_results.extend(current_result.tool_results)

            # Inject this turn into transcript for the next round.
            if current_result.content:
                accumulated_history.append(("assistant", current_result.content))
            for tr in current_result.tool_results or []:
                tool_name = str(tr.get("tool", "tool")).strip() or "tool"
                tool_text = f"[{tool_name}] success={tr.get('success', False)}"
                if tr.get("error"):
                    tool_text += f" error={tr.get('error')}"
                elif tr.get("result"):
                    tool_text += f" result={str(tr.get('result'))[:240]}"
                accumulated_history.append(("tool", tool_text))

        final_result = WorkflowRoleResult.from_kernel_result(current_result, self.role_id)
        final_result.all_tool_results = all_tool_results
        return final_result

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
