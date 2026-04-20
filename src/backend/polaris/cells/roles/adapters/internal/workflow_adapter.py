"""Workflow Adapter - 工作流适配器

为 PM/Director 工作流节点提供 RoleExecutionKernel 的适配层。

使用示例:
    from polaris.cells.roles.runtime.public.service.workflow_adapter import WorkflowRoleAdapter

    adapter = WorkflowRoleAdapter(workspace=".")
    result = await adapter.execute_role(
        role="pm",
        message="分析需求并创建任务",
        task_id="TASK-001"
    )
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.cells.roles.kernel.public.service import RoleExecutionKernel
from polaris.cells.roles.profile.public.service import (
    RoleExecutionMode,
    RoleProfileRegistry,
    RoleTurnRequest,
    RoleTurnResult,
    load_core_roles,
    profile_to_dict,
)
from polaris.cells.roles.session.public import RoleDataStore

logger = logging.getLogger(__name__)


class WorkflowRoleAdapter:
    """工作流角色适配器

    为工作流节点提供统一的内核调用接口。
    """

    def __init__(
        self,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
    ) -> None:
        """初始化适配器

        Args:
            workspace: 工作区路径
            registry: 角色注册表（默认使用全局实例）
        """
        self.workspace = workspace
        self.registry = registry or RoleProfileRegistry()
        self._kernel: RoleExecutionKernel | None = None
        self._data_stores: dict[str, RoleDataStore] = {}

        # 确保核心角色配置已加载
        self._ensure_core_roles_loaded()

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

    async def execute_role(
        self,
        role: str,
        message: str,
        task_id: str | None = None,
        context: dict[str, Any] | None = None,
        prompt_appendix: str | None = None,
        history: list[tuple] | None = None,
        validate_output: bool = True,
    ) -> WorkflowRoleResult:
        """执行角色（工作流模式）

        Args:
            role: 角色标识
            message: 用户消息/指令
            task_id: 关联的任务ID
            context: 额外上下文信息
            prompt_appendix: 追加提示词
            history: 历史消息
            validate_output: 是否验证输出

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
            validate_output=validate_output,
        )

        # 执行内核
        result = await self.kernel.run(role=role, request=request)

        # 存储执行数据
        if task_id:
            await self._store_execution_data(role, task_id, result)

        # 转换为工作流结果格式
        return WorkflowRoleResult.from_kernel_result(result, role)

    async def execute_role_with_tools(
        self, role: str, message: str, task_id: str | None = None, max_tool_rounds: int = 5, **kwargs
    ) -> WorkflowRoleResult:
        """Execute role with multi-turn tool handling.

        Convergence: 单次 kernel.run() 调用即可处理完整 tool loop。
        kernel.run() 内部已有 while True 工具循环（由 TurnEngine 驱动），
        不需要外层 for 循环重复调用。外层 for 循环（Phase 7 前）是历史遗留
        double-loop，已被移除。

        max_tool_rounds 参数保留但已降级为参考（由 kernel.run() 内预算控制）。
        """
        request = RoleTurnRequest(
            mode=RoleExecutionMode.WORKFLOW,
            workspace=self.workspace,
            message=message,
            history=[],  # TurnEngine 通过 kernel.run() 的 ToolLoopController 管理 transcript
            prompt_appendix=kwargs.get("prompt_appendix"),
            context_override=kwargs.get("context"),
            task_id=task_id,
            validate_output=kwargs.get("validate_output", True),
            max_retries=kwargs.get("max_retries", 1),
        )

        # 单次 kernel.run() — TurnEngine.run() 内部处理完整工具循环
        result = await self.kernel.run(role=role, request=request)

        final = WorkflowRoleResult.from_kernel_result(result, role)
        # all_tool_results = result.tool_results（kernel.run() 已累积全部结果）
        final.all_tool_results = result.tool_results if result.tool_results else []
        return final

    async def _store_execution_data(self, role: str, task_id: str, result: RoleTurnResult) -> None:
        """存储执行数据到角色数据目录"""
        try:
            if role not in self._data_stores:
                profile = self.registry.get_profile(role)
                if profile:
                    self._data_stores[role] = RoleDataStore(profile, self.workspace)

            store = self._data_stores.get(role)
            if store:
                store.append_event(
                    "workflow_execution",
                    {
                        "task_id": task_id,
                        "has_tool_calls": len(result.tool_calls) > 0,
                        "is_complete": result.is_complete,
                    },
                )
        except (RuntimeError, ValueError) as e:
            logger.debug(f"存储执行数据失败: {e}")

    def get_role_profile(self, role: str) -> dict[str, Any] | None:
        """获取角色配置"""
        profile = self.registry.get_profile(role)
        if profile:
            return profile_to_dict(profile)
        return None

    def validate_role_permission(self, role: str, tool_name: str, tool_args: dict | None = None) -> tuple[bool, str]:
        """验证角色工具权限

        Args:
            role: 角色标识
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            (是否允许, 原因)
        """
        from polaris.cells.roles.kernel.public.service import RoleToolGateway

        profile = self.registry.get_profile(role)
        if not profile:
            return False, f"未知角色: {role}"

        gateway = RoleToolGateway(profile, self.workspace)
        return gateway.check_tool_permission(tool_name, tool_args)


class WorkflowRoleResult:
    """工作流角色执行结果

    封装 RoleTurnResult 为工作流友好的格式。
    """

    def __init__(
        self,
        success: bool,
        content: str,
        role: str,
        thinking: str | None = None,
        structured_output: dict | None = None,
        tool_calls: list[dict] | None = None,
        tool_results: list[dict] | None = None,
        all_tool_results: list[dict] | None = None,
        profile_version: str = "",
        prompt_fingerprint: str | None = None,
        tool_policy_id: str = "",
        is_complete: bool = True,
        error: str | None = None,
    ) -> None:
        self.success = success
        self.content = content
        self.role = role
        self.thinking = thinking
        self.structured_output = structured_output
        self.tool_calls = tool_calls or []
        self.tool_results = tool_results or []
        self.all_tool_results = all_tool_results or []
        self.profile_version = profile_version
        self.prompt_fingerprint = prompt_fingerprint
        self.tool_policy_id = tool_policy_id
        self.is_complete = is_complete
        self.error = error

    @classmethod
    def from_kernel_result(cls, result: RoleTurnResult, role: str) -> WorkflowRoleResult:
        """从内核结果创建工作流结果"""
        success = result.error is None

        return cls(
            success=success,
            content=result.content,
            role=role,
            thinking=result.thinking,
            structured_output=result.structured_output,
            tool_calls=result.tool_calls,
            tool_results=result.tool_results,
            profile_version=result.profile_version,
            prompt_fingerprint=result.prompt_fingerprint.full_hash if result.prompt_fingerprint else None,
            tool_policy_id=result.tool_policy_id,
            is_complete=result.is_complete,
            error=result.error,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "success": self.success,
            "content": self.content,
            "role": self.role,
            "thinking": self.thinking,
            "structured_output": self.structured_output,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "profile_version": self.profile_version,
            "prompt_fingerprint": self.prompt_fingerprint,
            "tool_policy_id": self.tool_policy_id,
            "is_complete": self.is_complete,
            "error": self.error,
        }


# 便捷函数
async def execute_workflow_role(
    role: str, message: str, workspace: str = "", task_id: str | None = None, **kwargs
) -> WorkflowRoleResult:
    """便捷函数：执行工作流角色

    无需创建适配器实例的便捷调用方式。

    Args:
        role: 角色标识
        message: 消息
        workspace: 工作区路径
        task_id: 任务ID
        **kwargs: 其他参数

    Returns:
        WorkflowRoleResult
    """
    adapter = WorkflowRoleAdapter(workspace=workspace)
    return await adapter.execute_role(role=role, message=message, task_id=task_id, **kwargs)
