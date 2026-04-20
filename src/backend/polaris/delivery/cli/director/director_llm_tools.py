"""Director LLM Tools Integration.

【Task #49 架构约束 — FROZEN, ZERO CONSUMERS】
此文件已冻结，禁止使用。其实现的 tool loop 已迁移至
RoleRuntimeService → RoleExecutionKernel 统一路径。
如需 LLM 工具调用能力，请通过 RoleRuntimeService facade。
"""

from __future__ import annotations

__frozen__ = True

import logging
import os
from pathlib import Path
from typing import Any

# 导入 Context Gateway
from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway

# 导入 RoleProfile
from polaris.cells.roles.profile.internal.schema import RoleProfile
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

# 导入 Standard Toolkit 工具系统
from polaris.kernelone.llm.toolkit import (  # type: ignore[attr-defined]
    AgentAccelToolExecutor,
    DirectorToolIntegration,
    has_tool_calls,
)

logger = logging.getLogger(__name__)


class DirectorLLMClient:
    """支持工具调用的 Director LLM 客户端.

    包装现有 LLM 调用，添加代码分析和验证工具。
    """

    def __init__(
        self,
        workspace: str,
        role: str = "director",
        max_tool_iterations: int = 3,
        enable_tools: bool = True,
    ) -> None:
        self.workspace = workspace
        self.role = role
        self.max_tool_iterations = max_tool_iterations
        self.enable_tools = enable_tools
        self.tool_executor: AgentAccelToolExecutor | None

        if self.enable_tools:
            self.tool_integration = DirectorToolIntegration(workspace)
            self.tool_executor = AgentAccelToolExecutor(workspace)
        else:
            self.tool_integration = None
            self.tool_executor = None

        # 初始化 RoleContextGateway 以修复旁路问题
        # PR-05/06: 通过 gateway 构建上下文，避免直接构建 messages
        _profile = RoleProfile(
            role_id=role,
            display_name=role,
            description=f"Director role profile for workspace {workspace}",
        )
        self._role_context_gateway = RoleContextGateway(
            profile=_profile,
            workspace=Path(workspace) if workspace else Path.cwd(),
        )

    async def invoke_with_tools(
        self, prompt: str, system_prompt: str | None = None, blueprint_context: dict[str, Any] | None = None, **kwargs
    ) -> dict[str, Any]:
        """调用 LLM，支持工具调用.

        Args:
            prompt: 用户提示
            system_prompt: 系统提示
            blueprint_context: ChiefEngineer 蓝图上下文
            **kwargs: 其他参数

        Returns:
            包含输出和元信息的字典
        """
        if not self.enable_tools:
            return self._invoke_plain(prompt, system_prompt, **kwargs)

        # 构建消息 - PR-05/06: 通过 RoleContextGateway 构建上下文
        messages: list[dict[str, str]] = []

        # 构建完整系统提示
        full_system = ""
        if self.tool_integration:
            tool_prompt = self.tool_integration.get_system_prompt()

            # 添加蓝图约束
            blueprint_section = ""
            if blueprint_context:
                constraints = blueprint_context.get("constraints", {})
                scope = blueprint_context.get("scope_for_apply", [])
                if constraints or scope:
                    blueprint_section = f"""

## ChiefEngineer 蓝图约束
- 必须遵守: {constraints.get("must_follow", [])}
- 应用范围: {scope}
- 技术栈: {constraints.get("tech_stack", "未指定")}
"""

            if system_prompt:
                full_system = f"{tool_prompt}\n{blueprint_section}\n---\n{system_prompt}"
            else:
                full_system = f"{tool_prompt}{blueprint_section}"

        # PR-05/06 修复: 使用 RoleContextGateway.build_context() 构建初始上下文
        # 这避免了直接构建 messages 列表的旁路问题
        if self._role_context_gateway is None and full_system:
            raise RuntimeError(
                "RoleContextGateway not available but required for context building. "
                "PR-05/06 bypass fix requires gateway for system prompt injection."
            )

        if full_system:
            request = ContextRequest(
                message=prompt,
                history=(),
                context_override={"system_hint": full_system},
            )
            context_result = await self._role_context_gateway.build_context(request)
            messages = list(context_result.messages)
        else:
            # No system prompt needed, build minimal messages
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

        # 多轮工具调用循环
        iteration = 0
        all_tools_executed: list[dict[str, Any]] = []

        while iteration < self.max_tool_iterations:
            iteration += 1

            # 调用 LLM
            response = self._invoke_messages(messages, **kwargs)
            content = response.get("output", "")

            # 检查工具调用
            if not has_tool_calls(content):
                return {
                    "output": content,
                    "iterations": iteration,
                    "tools_executed": all_tools_executed,
                    "raw_response": response,
                }

            # 执行工具调用
            result = self.tool_integration.process_llm_response(content)

            if result["has_tools"]:
                all_tools_executed.extend(result["tools_executed"])

                # 添加 assistant 消息
                messages.append({"role": "assistant", "content": content})

                # 添加工具结果
                tool_results = self.tool_integration.build_tool_results_prompt(result["tools_executed"])
                messages.append({"role": "user", "content": tool_results})

        # 达到最大迭代次数
        response = self._invoke_messages(messages, **kwargs)
        return {
            "output": response.get("output", ""),
            "iterations": iteration,
            "tools_executed": all_tools_executed,
            "raw_response": response,
            "max_iterations_reached": True,
        }

    def _invoke_plain(self, prompt: str, system_prompt: str | None = None, **kwargs) -> dict[str, Any]:
        """普通 LLM 调用（无工具）."""
        # 尝试使用运行时调用
        try:
            from polaris.cells.llm.provider_runtime.public import invoke_role_runtime_provider

            full_prompt = prompt
            if system_prompt:
                full_prompt = f"{system_prompt}\n\n{prompt}"

            result = invoke_role_runtime_provider(
                role=self.role,
                workspace=self.workspace,
                prompt=full_prompt,
                fallback_model=kwargs.get("model", ""),
                timeout=kwargs.get("timeout", 120),
            )
            return {
                "output": result.output if hasattr(result, "output") else str(result),
                "raw": result,
            }
        except ImportError:
            return {
                "output": f"[LLM not available] Would process: {prompt[:100]}...",
                "raw": None,
            }

    def _invoke_messages(self, messages: list[dict[str, str]], **kwargs) -> dict[str, Any]:
        """使用消息列表调用 LLM."""
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                prompt_parts.append(f"[System]\n{content}")
            elif role == "user":
                prompt_parts.append(f"[User]\n{content}")
            elif role == "assistant":
                prompt_parts.append(f"[Assistant]\n{content}")

        full_prompt = "\n\n".join(prompt_parts)
        return self._invoke_plain(full_prompt, **kwargs)


class DirectorToolEnabledImplementation:
    """支持 LLM 工具的 Director 实现.

    让 LLM 在编码过程中自主使用工具：
    - 理解现有代码
    - 验证修改
    - 查找相关代码
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.llm_client = DirectorLLMClient(workspace)

    async def implement_task_with_tools(
        self,
        task_description: str,
        blueprint: dict[str, Any],
        target_files: list[str],
    ) -> dict[str, Any]:
        """使用工具实现任务.

        Args:
            task_description: 任务描述
            blueprint: ChiefEngineer 蓝图
            target_files: 目标文件

        Returns:
            实现结果
        """
        # 构建实现提示
        prompt = f"""请实现以下编码任务.

任务描述:
{task_description}

目标文件:
{target_files}

蓝图指导:
- 模块: {blueprint.get("modules", {}).keys()}
- 约束: {blueprint.get("architecture_constraints", {})}

请按照以下步骤:
1. 使用 read_file 理解现有代码结构
2. 如果需要，使用 search_code 评估影响
3. 生成代码实现
4. 使用 file_exists 验证导入正确性

请输出修改后的文件内容."""

        result = await self.llm_client.invoke_with_tools(
            prompt=prompt,
            system_prompt="你是 Director（工部侍郎），负责实现代码。",
            blueprint_context=blueprint,
        )

        return {
            "implementation": result.get("output", ""),
            "tools_executed": result.get("tools_executed", []),
            "iterations": result.get("iterations", 0),
        }

    async def analyze_code_with_tools(
        self,
        file_path: str,
        analysis_type: str = "context",
    ) -> dict[str, Any]:
        """使用工具分析代码.

        Args:
            file_path: 文件路径
            analysis_type: 分析类型 (context, impact, search)

        Returns:
            分析结果
        """
        if analysis_type == "context":
            prompt = f"请分析文件 {file_path} 的代码结构，使用 read_file 工具。"
        elif analysis_type == "impact":
            prompt = f"请分析修改文件 {file_path} 的影响范围，使用 search_code 工具。"
        else:
            prompt = f"请搜索与 {file_path} 相关的代码，使用 search_code 工具。"

        result = await self.llm_client.invoke_with_tools(prompt=prompt)

        return {
            "analysis": result.get("output", ""),
            "tools_executed": result.get("tools_executed", []),
        }


class DirectorToolsV2WithAgentAccel:
    """扩展 DirectorToolsV2，集成 Standard Toolkit 工具."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.accel_executor = AgentAccelToolExecutor(workspace)

    def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """执行 Standard Toolkit 工具."""
        return self.accel_executor.execute(tool_name, args)


# ============== 便捷函数 ==============


def enhance_director_with_llm_tools(
    workspace: str,
    enable: bool = True,
) -> DirectorLLMClient:
    """创建支持 LLM 工具的 Director 客户端.

    Args:
        workspace: 工作区路径
        enable: 是否启用工具

    Returns:
        LLM 客户端
    """
    return DirectorLLMClient(
        workspace=workspace,
        enable_tools=enable,
    )


def create_enhanced_director_tools(workspace: str) -> DirectorToolsV2WithAgentAccel:
    """创建增强的 Director 工具.

    Args:
        workspace: 工作区路径

    Returns:
        增强的 Director 工具实例
    """
    return DirectorToolsV2WithAgentAccel(workspace)


def patch_director_runtime() -> None:
    """初始化 Director 工具增强（当前无需 legacy monkey patch）."""
    logger.info("Director runtime tooling initialized")


# 自动修补（如果环境变量启用）
if os.getenv("POLARIS_ENABLE_LLM_TOOLS", "0") == "1":
    patch_director_runtime()
