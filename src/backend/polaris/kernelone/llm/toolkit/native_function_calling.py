"""Native Function Calling Support for Standard Toolkit Tools.

Phase 3: 原生 Function Calling 支持。
扩展 LLM Platform 以支持 tools 参数。

DEPRECATION NOTICE:
    This module is being deprecated in favor of the unified tool contracts.
    Please use:
        from polaris.kernelone.llm.contracts import ToolCall, ToolExecutionResult
        from polaris.kernelone.llm.toolkit.executor.runtime import KernelToolCallingRuntime

    This module will be removed in a future version.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

# 导入 canonical ToolCall (frozen dataclass with to_openai_format)
from polaris.kernelone.llm.contracts.tool import ToolCall as _CanonicalToolCall

# 从core层导入契约，避免循环依赖
from .contracts import AIRequest, AIResponse

# 导入 Standard Toolkit 工具
from .definitions import create_default_registry
from .executor import AgentAccelToolExecutor

logger = logging.getLogger(__name__)


def _serialize_tool_output(output: Any) -> dict[str, Any]:
    """将工具输出规整为可 JSON 序列化的字典结构."""
    try:
        normalized = json.loads(json.dumps(output, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"error": "non-serializable", "raw": str(output)}

    if isinstance(normalized, dict):
        return normalized
    return {"value": normalized}


def _parse_tool_arguments(arguments_str: str) -> tuple[dict[str, Any], str | None]:
    """Parse tool arguments and reject malformed payloads."""
    raw = str(arguments_str or "").strip() or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON arguments: {exc}"
    if not isinstance(parsed, dict):
        return {}, "tool arguments must decode to a JSON object"
    return parsed, None


# Re-export canonical ToolCall for backward compatibility
ToolCall = _CanonicalToolCall


@dataclass
class ToolResult:
    """工具执行结果."""

    tool_call_id: str
    name: str
    output: dict[str, Any]
    is_error: bool = False

    def to_openai_format(self) -> dict[str, Any]:
        """转换为 OpenAI tool 消息格式."""
        safe_output = _serialize_tool_output(self.output)
        content = json.dumps(safe_output, ensure_ascii=False)
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "content": content,
        }


class ToolEnabledAIRequest(AIRequest):
    """支持工具的 AI 请求.

    扩展 AIRequest，添加 tools 参数支持。
    """

    def __init__(
        self,
        *args,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,  # "auto", "none", "required", or specific tool name
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tools = tools or []
        self.tool_choice = tool_choice or "auto"

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        data = super().to_dict()
        if self.tools:
            data["tools"] = self.tools
        if self.tool_choice:
            data["tool_choice"] = self.tool_choice
        return data


class ToolEnabledAIResponse(AIResponse):
    """支持工具的 AI 响应.

    扩展 AIResponse，添加 tool_calls 支持。
    """

    def __init__(self, *args, tool_calls: list[ToolCall] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tool_calls = tool_calls or []

    @property
    def has_tool_calls(self) -> bool:
        """检查是否包含工具调用."""
        return len(self.tool_calls) > 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        data = super().to_dict()
        if self.tool_calls:
            data["tool_calls"] = [tc.to_openai_format() for tc in self.tool_calls]
        return data


class NativeFunctionCallingHandler:
    """原生 Function Calling 处理器.

    处理 OpenAI/Anthropic 风格的 function calling。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.executor = AgentAccelToolExecutor(workspace)
        self.registry = create_default_registry()

    def get_available_tools(self) -> list[dict[str, Any]]:
        """获取可用工具列表（OpenAI 格式）."""
        return self.registry.to_openai_functions()

    def parse_response(self, raw_response: dict[str, Any]) -> list[ToolCall]:
        """解析 LLM 响应中的 tool_calls.

        Args:
            raw_response: LLM 原始响应

        Returns:
            工具调用列表
        """
        tool_calls = []

        # OpenAI 格式
        if "choices" in raw_response:
            for choice in raw_response.get("choices", []):
                message = choice.get("message", {})
                calls = message.get("tool_calls", [])

                for call in calls:
                    function = call.get("function", {})
                    arguments_str = function.get("arguments", "{}")
                    arguments, parse_error = _parse_tool_arguments(arguments_str)

                    tool_calls.append(
                        ToolCall(
                            id=call.get("id", ""),
                            name=function.get("name", ""),
                            arguments=arguments,
                            parse_error=parse_error,
                        )
                    )

        # Anthropic 格式
        elif "content" in raw_response:
            content = raw_response.get("content", [])
            for block in content:
                if block.get("type") == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments=block.get("input", {}),
                        )
                    )

        return tool_calls

    def execute_tool_calls(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """执行工具调用.

        Args:
            tool_calls: 工具调用列表

        Returns:
            工具执行结果列表
        """
        results = []

        for call in tool_calls:
            logger.info(f"Executing tool: {call.name}")
            if call.parse_error:
                results.append(
                    ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        output={"ok": False, "error": call.parse_error},
                        is_error=True,
                    )
                )
                continue
            output = self.executor.execute(call.name, call.arguments)

            results.append(
                ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    output=output,
                    is_error=not output.get("ok", False),
                )
            )

        return results

    def build_tool_response_message(
        self,
        tool_results: list[ToolResult],
    ) -> list[dict[str, Any]]:
        """构建工具响应消息.

        Args:
            tool_results: 工具执行结果

        Returns:
            OpenAI 格式的消息列表
        """
        return [result.to_openai_format() for result in tool_results]


class ConversationalToolExecutor:
    """对话式工具执行器.

    支持多轮工具调用的对话。
    """

    def __init__(
        self,
        workspace: str,
        llm_client,
        max_iterations: int = 5,
    ) -> None:
        self.workspace = workspace
        self.llm_client = llm_client
        self.max_iterations = max_iterations
        self.tool_handler = NativeFunctionCallingHandler(workspace)

    async def execute(self, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        """执行带工具调用的对话.

        Args:
            messages: 对话历史
            **kwargs: 额外参数

        Returns:
            最终响应
        """
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            # 获取可用工具
            tools = self.tool_handler.get_available_tools()

            # 调用 LLM（带 tools）
            response = await self._call_llm_with_tools(messages=messages, tools=tools, **kwargs)

            # 解析 tool_calls
            tool_calls = self.tool_handler.parse_response(response)

            if not tool_calls:
                # 没有工具调用，返回最终响应
                return {
                    "ok": True,
                    "content": response.get("content", ""),
                    "raw": response,
                    "iterations": iteration,
                }

            # 执行工具调用
            results = self.tool_handler.execute_tool_calls(tool_calls)

            # 添加 assistant 消息（含 tool_calls）
            messages.append(
                {
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": [tc.to_openai_format() for tc in tool_calls],
                }
            )

            # 添加 tool 响应消息
            for result in results:
                messages.append(result.to_openai_format())

        # 达到最大迭代次数
        return {
            "ok": True,
            "content": "Maximum iterations reached",
            "messages": messages,
            "iterations": iteration,
        }

    async def _call_llm_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs
    ) -> dict[str, Any]:
        """调用 LLM（带工具支持）."""
        # 这里需要根据实际情况调用 LLM
        # 假设 llm_client 支持 tools 参数
        if hasattr(self.llm_client, "chat_with_tools"):
            return await self.llm_client.chat_with_tools(messages=messages, tools=tools, **kwargs)
        else:
            # 回退到普通调用
            return await self.llm_client.chat(messages, **kwargs)


# ============== Provider 扩展 ==============


class ToolEnabledProviderMixin:
    """Provider 工具支持 Mixin.

    为 Provider 添加工具调用能力。
    """

    def build_payload_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """构建带工具的请求 payload.

        Args:
            messages: 消息列表
            tools: 工具定义列表
            tool_choice: 工具选择策略
            **kwargs: 其他参数

        Returns:
            请求 payload
        """
        payload = {"messages": messages, **kwargs}

        if tools:
            payload["tools"] = tools

            if tool_choice:
                if tool_choice == "auto":
                    payload["tool_choice"] = "auto"
                elif tool_choice == "none":
                    payload["tool_choice"] = "none"
                elif tool_choice == "required":
                    payload["tool_choice"] = "required"
                else:
                    # 指定特定工具
                    payload["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}

        return payload

    def parse_tool_calls_from_response(self, data: dict[str, Any]) -> list[ToolCall]:
        """从响应中解析 tool_calls."""
        tool_calls: list[ToolCall] = []

        choices = data.get("choices", [])
        if not choices:
            return tool_calls

        message = choices[0].get("message", {})
        calls = message.get("tool_calls", [])

        for call in calls:
            if call.get("type") == "function":
                function = call.get("function", {})
                arguments_str = function.get("arguments", "{}")
                arguments, parse_error = _parse_tool_arguments(arguments_str)

                tool_calls.append(
                    ToolCall(
                        id=call.get("id", ""),
                        name=function.get("name", ""),
                        arguments=arguments,
                        parse_error=parse_error,
                    )
                )

        return tool_calls


# ============== 便捷函数 ==============


def create_tool_request(
    task_type,
    role: str,
    input_text: str,
    workspace: str,
    tool_choice: str = "auto",
) -> ToolEnabledAIRequest:
    """创建带工具的 AI 请求.

    Args:
        task_type: 任务类型
        role: 角色
        input_text: 输入文本
        workspace: 工作区路径
        tool_choice: 工具选择策略

    Returns:
        工具支持的 AI 请求
    """
    registry = create_default_registry()

    return ToolEnabledAIRequest(
        task_type=task_type,
        role=role,
        input=input_text,
        tools=registry.to_openai_functions(),
        tool_choice=tool_choice,
    )


async def execute_with_native_function_calling(
    llm_client,
    workspace: str,
    messages: list[dict[str, Any]],
    max_iterations: int = 5,
) -> dict[str, Any]:
    """便捷函数：使用原生 Function Calling 执行.

    Args:
        llm_client: LLM 客户端
        workspace: 工作区路径
        messages: 对话消息
        max_iterations: 最大迭代次数

    Returns:
        执行结果
    """
    executor = ConversationalToolExecutor(
        workspace=workspace,
        llm_client=llm_client,
        max_iterations=max_iterations,
    )
    return await executor.execute(messages)
