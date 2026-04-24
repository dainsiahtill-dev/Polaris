"""ChiefEngineer LLM Tools Integration.

【Task #49 架构约束 — FROZEN, ZERO CONSUMERS】
此文件已冻结，禁止使用。其实现的 tool loop 已迁移至
RoleRuntimeService → RoleExecutionKernel 统一路径。
如需 LLM 工具调用能力，请通过 RoleRuntimeService facade。
"""

from __future__ import annotations

__frozen__ = True

import json
import logging
import os
from pathlib import Path
from typing import Any

# 导入 LLM 运行时调用
from polaris.cells.llm.provider_runtime.public import invoke_role_runtime_provider

# 导入 Context Gateway
from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway

# 导入 RoleProfile
from polaris.cells.roles.profile.internal.schema import RoleProfile
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

# 导入 Standard Toolkit 工具系统
from polaris.kernelone.llm.toolkit import (  # type: ignore[attr-defined]
    ChiefEngineerToolIntegration,
    has_tool_calls,
)

logger = logging.getLogger(__name__)


class ChiefEngineerLLMClient:
    """支持工具调用的 ChiefEngineer LLM 客户端.

    包装现有 LLM 调用，添加 Standard Toolkit 工具支持。
    """

    def __init__(
        self,
        workspace: str,
        role: str = "chiefengineer",
        max_tool_iterations: int = 3,
        enable_tools: bool = True,
    ) -> None:
        self.workspace = workspace
        self.role = role
        self.max_tool_iterations = max_tool_iterations
        self.enable_tools = enable_tools

        if self.enable_tools:
            self.tool_integration = ChiefEngineerToolIntegration(workspace)
        else:
            self.tool_integration = None

        # 初始化 RoleContextGateway 以修复旁路问题
        # PR-05/06: 通过 gateway 构建上下文，避免直接构建 messages
        _profile = RoleProfile(
            role_id=role,
            display_name=role,
            description=f"ChiefEngineer role profile for workspace {workspace}",
        )
        self._role_context_gateway = RoleContextGateway(
            profile=_profile,
            workspace=Path(workspace) if workspace else Path.cwd(),
        )

    async def invoke_with_tools(self, prompt: str, system_prompt: str | None = None, **kwargs) -> dict[str, Any]:
        """调用 LLM，支持工具调用.

        Args:
            prompt: 用户提示
            system_prompt: 系统提示
            **kwargs: 其他参数

        Returns:
            包含输出和元信息的字典
        """
        if not self.enable_tools:
            # 回退到普通调用
            return self._invoke_plain(prompt, system_prompt, **kwargs)

        # 构建消息 - PR-05/06: 通过 RoleContextGateway 构建上下文
        messages: list[dict[str, str]] = []

        # 构建完整系统提示
        full_system = ""
        if self.tool_integration:
            tool_prompt = self.tool_integration.get_system_prompt()
            full_system = f"{tool_prompt}\n\n---\n\n{system_prompt}" if system_prompt else tool_prompt

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
                # 没有工具调用，返回结果
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

                # 添加 assistant 消息（包含工具调用）
                messages.append({"role": "assistant", "content": content})

                # 添加工具结果
                tool_results = self.tool_integration.build_tool_results_prompt(result["tools_executed"])
                messages.append({"role": "user", "content": tool_results})

        # 达到最大迭代次数，执行最后一次调用
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
        result = invoke_role_runtime_provider(
            role=self.role,
            workspace=self.workspace,
            prompt=prompt,
            fallback_model=kwargs.get("model", ""),
            timeout=kwargs.get("timeout", 120),
        )
        return {
            "output": result.output if hasattr(result, "output") else str(result),
            "raw": result,
        }

    def _invoke_messages(self, messages: list[dict[str, str]], **kwargs) -> dict[str, Any]:
        """使用消息列表调用 LLM."""
        # 将消息列表转换为提示文本
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


class ChiefEngineerToolEnabledAnalysis:
    """支持 LLM 工具的 ChiefEngineer 分析.

    替代/增强原有的 run_chief_engineer_analysis，
    让 LLM 自主决定何时使用代码分析工具。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.llm_client = ChiefEngineerLLMClient(workspace)

    async def analyze_task_with_llm_tools(
        self,
        task_description: str,
        target_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """使用 LLM 工具分析任务.

        LLM 可以自主决定：
        1. 是否需要分析代码变更
        2. 是否需要获取语义上下文
        3. 是否需要进行影响分析

        Args:
            task_description: 任务描述
            target_files: 目标文件列表（可选）

        Returns:
            分析结果
        """
        # 构建分析提示
        files_section = ""
        if target_files:
            files_list = "\n".join(f"- {f}" for f in target_files)
            files_section = f"\n\n目标文件:\n{files_list}"

        prompt = f"""请分析以下开发任务并设计架构蓝图.

任务描述:
{task_description}{files_section}

请按照以下步骤工作:
1. 使用 search_code 或 read_file 了解相关代码结构
2. 如果需要修改文件，使用 search_code 分析变更影响
3. 基于分析结果，输出 ConstructionBlueprint

请直接开始分析，根据需要调用工具。"""

        # 调用带工具的 LLM
        result = await self.llm_client.invoke_with_tools(
            prompt=prompt,
            system_prompt="你是 ChiefEngineer（工部尚书），负责设计代码架构蓝图。",
        )

        return {
            "output": result.get("output", ""),
            "tools_executed": result.get("tools_executed", []),
            "iterations": result.get("iterations", 0),
        }

    async def generate_construction_plan_with_tools(
        self,
        task: dict[str, Any],
        blueprint: dict[str, Any],
    ) -> dict[str, Any]:
        """使用 LLM 工具生成构造计划.

        Args:
            task: 任务信息
            blueprint: 蓝图信息

        Returns:
            构造计划
        """
        task_id = task.get("id", "")
        task_desc = task.get("description", "")
        target_files = task.get("target_files", [])

        prompt = f"""请为以下任务生成详细的构造计划.

任务 ID: {task_id}
任务描述: {task_desc}
目标文件: {target_files}

蓝图信息:
- 模块数: {len(blueprint.get("modules", {}))}
- 文件数: {len(blueprint.get("files", {}))}

请:
1. 使用工具获取相关代码上下文
2. 分析影响范围
3. 生成详细的 file_plans 和 method_catalog

输出格式要求:
- file_plans: 每个文件的修改计划
- method_catalog: 方法级实现目录
- entry_point: 切入点建议
- test_strategy: 测试策略"""

        result = await self.llm_client.invoke_with_tools(
            prompt=prompt,
            system_prompt="你是 ChiefEngineer，负责生成详细的代码构造计划。",
        )

        # 尝试解析输出为结构化数据
        try:
            # 查找 JSON 块
            output = result.get("output", "")

            # 尝试提取 ```json ... ``` 块
            import re

            json_block = re.search(r"```(?:json)?\s*\n(.*?)\n```", output, re.DOTALL)
            json_str = json_block.group(1) if json_block else output

            construction_plan = json.loads(json_str)
        except (json.JSONDecodeError, AttributeError):
            construction_plan = {
                "raw_output": result.get("output", ""),
                "parse_error": "Failed to parse as JSON",
            }

        return {
            "construction_plan": construction_plan,
            "tools_executed": result.get("tools_executed", []),
            "iterations": result.get("iterations", 0),
        }


# ============== 便捷函数 ==============


def enhance_chief_engineer_with_llm_tools(
    workspace: str,
    enable: bool = True,
) -> ChiefEngineerLLMClient:
    """创建支持 LLM 工具的 ChiefEngineer 客户端.

    Args:
        workspace: 工作区路径
        enable: 是否启用工具

    Returns:
        LLM 客户端
    """
    return ChiefEngineerLLMClient(
        workspace=workspace,
        enable_tools=enable,
    )


def patch_chief_engineer_module() -> None:
    """修补 ChiefEngineer 模块，添加工具支持.

    在导入时调用，自动增强现有功能。
    """
    try:
        import chief_engineer

        # 保存原始函数
        if hasattr(chief_engineer, "run_chief_engineer_analysis"):
            chief_engineer._original_run_chief_engineer_analysis = chief_engineer.run_chief_engineer_analysis

        logger.info("ChiefEngineer module patched with LLM tools support")
    except (RuntimeError, ValueError) as e:
        logger.warning(f"Failed to patch ChiefEngineer: {e}")


# 自动修补（如果环境变量启用）
if os.getenv("KERNELONE_ENABLE_LLM_TOOLS", "0") == "1":
    patch_chief_engineer_module()
