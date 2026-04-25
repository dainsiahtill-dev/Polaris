"""Standard Toolkit Tool Chain Adapter.

将 Standard Toolkit 工具集成到现有的 Tool Chain 系统中。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.kernelone.workflow.task_status import WorkflowTaskStatus

from .executor import AgentAccelToolExecutor
from .parsers import CanonicalToolCallParser

logger = logging.getLogger(__name__)


class AgentAccelToolChainAdapter:
    """Standard Toolkit Tool Chain 适配器.

    将 Standard Toolkit 工具包装为 Director Tool Chain 兼容的格式。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.executor = AgentAccelToolExecutor(workspace)

    def get_tool_handlers(self) -> dict[str, Any]:
        """获取 Tool Chain 处理器映射.

        返回:
            工具名称到处理函数的映射（含 session memory 工具）
        """
        return {
            # 4个基础工具
            "read_file": self._handle_read_file,
            "write_file": self._handle_write_file,
            "execute_command": self._handle_execute_command,
            "search_code": self._handle_search_code,
            # 文件/搜索增强工具
            "glob": self._handle_glob,
            "list_directory": self._handle_list_directory,  # Legacy alias for repo_tree
            "repo_tree": self._handle_list_directory,
            "file_exists": self._handle_file_exists,
            "grep": self._handle_grep,
            "ripgrep": self._handle_ripgrep,
            # 编辑工具
            "search_replace": self._handle_search_replace,
            "edit_file": self._handle_edit_file,
            "append_to_file": self._handle_append_to_file,
            # session memory tools
            "search_memory": self._handle_search_memory,
            "read_artifact": self._handle_read_artifact,
            "read_episode": self._handle_read_episode,
            "get_state": self._handle_get_state,
        }

    def _handle_read_file(self, **kwargs) -> dict[str, Any]:
        """处理文件读取."""
        return self.executor.execute("read_file", kwargs)

    def _handle_write_file(self, **kwargs) -> dict[str, Any]:
        """处理文件写入."""
        return self.executor.execute("write_file", kwargs)

    def _handle_execute_command(self, **kwargs) -> dict[str, Any]:
        """处理命令执行."""
        return self.executor.execute("execute_command", kwargs)

    def _handle_search_code(self, **kwargs) -> dict[str, Any]:
        """处理代码搜索."""
        return self.executor.execute("search_code", kwargs)

    def _handle_glob(self, **kwargs) -> dict[str, Any]:
        """处理 glob 文件匹配."""
        return self.executor.execute("glob", kwargs)

    def _handle_list_directory(self, **kwargs) -> dict[str, Any]:
        """处理目录列表."""
        return self.executor.execute("list_directory", kwargs)

    def _handle_file_exists(self, **kwargs) -> dict[str, Any]:
        """处理文件存在检查."""
        return self.executor.execute("file_exists", kwargs)

    def _handle_grep(self, **kwargs) -> dict[str, Any]:
        """处理 grep 搜索."""
        return self.executor.execute("grep", kwargs)

    def _handle_ripgrep(self, **kwargs) -> dict[str, Any]:
        """处理 ripgrep 搜索."""
        return self.executor.execute("ripgrep", kwargs)

    def _handle_search_replace(self, **kwargs) -> dict[str, Any]:
        """处理搜索替换."""
        return self.executor.execute("search_replace", kwargs)

    def _handle_edit_file(self, **kwargs) -> dict[str, Any]:
        """处理文件编辑."""
        return self.executor.execute("edit_file", kwargs)

    def _handle_append_to_file(self, **kwargs) -> dict[str, Any]:
        """处理文件追加."""
        return self.executor.execute("append_to_file", kwargs)

    def _handle_search_memory(self, **kwargs) -> dict[str, Any]:
        """处理 session memory 搜索."""
        return self.executor.execute("search_memory", kwargs)

    def _handle_read_artifact(self, **kwargs) -> dict[str, Any]:
        """处理 artifact 读取."""
        return self.executor.execute("read_artifact", kwargs)

    def _handle_read_episode(self, **kwargs) -> dict[str, Any]:
        """处理 episode 读取."""
        return self.executor.execute("read_episode", kwargs)

    def _handle_get_state(self, **kwargs) -> dict[str, Any]:
        """处理 session state 读取."""
        return self.executor.execute("get_state", kwargs)


class AgentAccelToolChainStep:
    """Standard Toolkit Tool Chain 步骤.

    表示一个工具调用步骤，兼容现有 Tool Chain 系统。
    """

    def __init__(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        step_id: str | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.step_id = step_id or f"step_{id(self)}"
        self.result: dict[str, Any] | None = None
        self.status = WorkflowTaskStatus.PENDING.value  # pending, running, completed, failed

    def execute(self, adapter: AgentAccelToolChainAdapter) -> dict[str, Any]:
        """执行步骤."""
        self.status = WorkflowTaskStatus.RUNNING.value

        handlers = adapter.get_tool_handlers()
        handler = handlers.get(self.tool_name)

        if handler is None:
            self.status = WorkflowTaskStatus.FAILED.value
            return {
                "ok": False,
                "error": f"Unknown tool: {self.tool_name}",
            }

        try:
            self.result = handler(**self.arguments)
            self.status = (
                WorkflowTaskStatus.COMPLETED.value if self.result.get("ok") else WorkflowTaskStatus.FAILED.value
            )
            return self.result
        except (RuntimeError, ValueError) as e:
            logger.exception(f"Tool step execution failed: {self.tool_name}")
            self.status = WorkflowTaskStatus.FAILED.value
            return {
                "ok": False,
                "error": str(e),
            }

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        return {
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "status": self.status,
            "result": self.result,
        }


class AgentAccelToolChainPlan:
    """Standard Toolkit Tool Chain 计划.

    包含多个工具调用步骤的计划。
    """

    def __init__(self, steps: list[AgentAccelToolChainStep] | None = None) -> None:
        self.steps = steps or []
        self.results: list[dict[str, Any]] = []

    @classmethod
    def parse_from_llm_output(cls, text: str) -> AgentAccelToolChainPlan:
        """从 LLM 输出解析工具计划.

        支持格式:
        <tool_chain>
        1. ripgrep(query="class User", file_patterns=["*.py"])
        2. read_file(file="src/auth/role_agent_service.py")
        </tool_chain>

        Note: This method uses CanonicalToolCallParser with JSON text format.
        For full tool chain format support, use the native function calling path.
        """
        # Use CanonicalToolCallParser for JSON text format
        # P0-002: parse() now returns list[ToolCall] with 'name' field (not 'tool_name')
        parser = CanonicalToolCallParser()
        parsed = parser._parse_json_text(text, allowed_tools=None)

        steps = []
        for i, call in enumerate(parsed):
            steps.append(
                AgentAccelToolChainStep(
                    step_id=f"step_{i}",
                    tool_name=call.name,  # ToolCall.name (formerly tool_name)
                    arguments=call.arguments,
                )
            )

        return cls(steps)

    def execute(self, workspace: str) -> list[dict[str, Any]]:
        """执行所有步骤."""
        adapter = AgentAccelToolChainAdapter(workspace)
        self.results = []

        for step in self.steps:
            result = step.execute(adapter)
            self.results.append(result)

            # 如果步骤失败，可以选择停止或继续
            if not result.get("ok"):
                logger.warning(f"Step {step.step_id} failed: {result.get('error')}")

        return self.results

    def to_summary(self) -> str:
        """生成执行摘要."""
        lines = ["Standard Toolkit Tool Chain Execution Summary:", "=" * 50]

        for i, (step, result) in enumerate(zip(self.steps, self.results, strict=True)):
            status_icon = "✓" if result.get("ok") else "✗"
            lines.append(f"\n{i + 1}. {status_icon} {step.tool_name}")
            lines.append(f"   Status: {step.status}")

            if result.get("ok"):
                result_data = result.get("result", {})
                if "affected_files" in result_data:
                    lines.append(f"   Affected files: {len(result_data['affected_files'])}")
                if "risk_level" in result_data:
                    lines.append(f"   Risk level: {result_data['risk_level']}")
            else:
                lines.append(f"   Error: {result.get('error', 'Unknown error')}")

        return "\n".join(lines)


# ============== Director Tools V2 集成 ==============


def integrate_with_director_tools_v2(director_tools, workspace: str) -> None:
    """将 Standard Toolkit 工具集成到 DirectorToolsV2.

    修改 DirectorToolsV2 的 handlers 字典，添加 Standard Toolkit 工具。

    Args:
        director_tools: DirectorToolsV2 实例
        workspace: 工作区路径
    """
    adapter = AgentAccelToolChainAdapter(workspace)
    handlers = adapter.get_tool_handlers()

    # 注入到 DirectorToolsV2 的 _get_handler 方法
    original_get_handler = director_tools._get_handler

    def patched_get_handler(tool_name: str) -> Any:
        # 先检查 Standard Toolkit 工具
        if tool_name in handlers:
            return handlers[tool_name]
        # 回退到原始处理器
        return original_get_handler(tool_name)

    director_tools._get_handler = patched_get_handler
    logger.info("Standard Toolkit tools integrated into DirectorToolsV2")


# ============== Tool Chain 执行器扩展 ==============


class AgentAccelToolChainExecutor:
    """扩展的 Tool Chain 执行器.

    支持 Standard Toolkit 工具的 Tool Chain 执行器。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.adapter = AgentAccelToolChainAdapter(workspace)

    def execute_plan_text(self, plan_text: str) -> dict[str, Any]:
        """执行计划文本.

        Args:
            plan_text: 包含 <tool_chain> 的文本

        Returns:
            执行结果
        """
        plan = AgentAccelToolChainPlan.parse_from_llm_output(plan_text)

        if not plan.steps:
            return {
                "ok": False,
                "error": "No tool steps found in plan",
            }

        results = plan.execute(self.workspace)

        return {
            "ok": all(r.get("ok") for r in results),
            "steps_executed": len(plan.steps),
            "results": results,
            "summary": plan.to_summary(),
        }

    def execute_single_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """执行单个工具.

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            执行结果
        """
        step = AgentAccelToolChainStep(tool_name, arguments)
        return step.execute(self.adapter)


# ============== 便捷函数 ==============


def execute_tool_chain(
    workspace: str,
    plan_text: str,
) -> dict[str, Any]:
    """便捷函数：执行 Tool Chain 计划文本."""
    executor = AgentAccelToolChainExecutor(workspace)
    return executor.execute_plan_text(plan_text)


def create_tool_chain_prompt(tools: list[dict[str, Any]]) -> str:
    """创建 Tool Chain 格式的提示.

    Args:
        tools: 工具调用列表，每项包含 name 和 arguments

    Returns:
        Tool Chain 格式的文本
    """
    lines = ["<tool_chain>"]

    for i, tool in enumerate(tools, 1):
        name = tool["name"]
        args = tool.get("arguments", {})

        # 格式化为 function(args) 格式
        args_str = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())

        lines.append(f"{i}. {name}({args_str})")

    lines.append("</tool_chain>")

    return "\n".join(lines)
