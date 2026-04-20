"""TUI LLM Client - 通过 Cell 公共服务使用 LLM 网关，无 kernelone 直连。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class StreamEventType(Enum):
    """流式事件类型枚举."""

    CHUNK = "chunk"
    REASONING_CHUNK = "reasoning_chunk"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class AIRequest:
    """Cell 定义的 AI 请求 DTO (替代 kernelone.tool_statekit.contracts.AIRequest)."""

    task_type: str = "dialogue"
    role: str = ""
    input: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMMessage:
    """LLM 对话消息."""

    role: str
    content: str


class TUILLMClient:
    """TUI 专用 LLM 客户端.

    通过 Cell 公共服务访问 LLM 能力，不再直接导入 kernelone。
    """

    def __init__(
        self,
        role: str,
        workspace: str = ".",
        system_prompt: str = "",
        provider_config_result: dict[str, Any] | None = None,
    ) -> None:
        self.role = role
        self.workspace = workspace
        self.system_prompt = system_prompt
        # 缓存 provider 配置以避免重复查询
        self._provider_config = provider_config_result

    def _resolve_provider_config(self) -> dict[str, Any]:
        """通过 provider_config 公共服务获取角色配置."""
        if self._provider_config is not None:
            return self._provider_config

        try:
            # 懒加载以避免循环导入
            from polaris.cells.llm.provider_config.internal.provider_context import (
                resolve_provider_request_context,
            )

            result = resolve_provider_request_context(
                workspace=self.workspace,
                cache_root="",
                provider_id=self.role,  # 使用 role 作为 provider_id
                api_key=None,
                headers=None,
            )
            self._provider_config = {
                "ok": True,
                "provider_id": self.role,  # 使用传入的 provider_id
                "provider_type": result.provider_type,
                "provider_cfg": dict(result.provider_cfg or {}),
            }
            return self._provider_config
        except (RuntimeError, ValueError) as exc:
            logger.debug("[TUI LLM] failed to resolve provider config for %s: %s", self.role, exc)
            self._provider_config = {"ok": False, "error": str(exc)}
            return self._provider_config

    def _get_provider(self):
        """获取 Provider 实例 (向后兼容)."""
        try:
            from polaris.kernelone.llm.toolkit.contracts import ServiceLocator

            return ServiceLocator.get_provider()
        except ImportError:
            logger.debug("[TUI LLM] ServiceLocator not available")
            return None

    def _build_prompt(self, messages: list[LLMMessage]) -> str:
        parts: list[str] = []

        if self.system_prompt:
            parts.append(f"<system>\n{self.system_prompt}\n</system>")

        for msg in messages:
            if msg.role == "system":
                parts.append(f"<system>\n{msg.content}\n</system>")
            elif msg.role == "user":
                parts.append(f"<user>\n{msg.content}\n</user>")
            elif msg.role == "assistant":
                parts.append(f"<assistant>\n{msg.content}\n</assistant>")

        return "\n\n".join(parts)

    def _build_request(self, messages: list[LLMMessage], *, stream: bool) -> AIRequest:
        return AIRequest(
            task_type="dialogue",
            role=self.role,
            input=self._build_prompt(messages),
            options={
                "stream": stream,
                "streaming": stream,
                "temperature": 0.7,
                "max_tokens": 4000,
            },
            context={"workspace": self.workspace},
        )

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        provider = self._get_provider()
        if provider is None:
            error_msg = "[错误] LLM Provider 未注册。"
            if on_token:
                on_token(error_msg)
            return error_msg

        request = self._build_request(messages, stream=True)
        full_response = ""

        try:
            async for chunk in provider.generate_stream(request):
                event_type = getattr(chunk, "event_type", StreamEventType.CHUNK)
                text = str(getattr(chunk, "content", "") or "")

                if event_type == StreamEventType.ERROR:
                    error_msg = f"[LLM 错误] {text or 'stream_failed'}"
                    if on_token:
                        on_token(error_msg)
                    return error_msg

                if event_type == StreamEventType.REASONING_CHUNK:
                    if text:
                        logger.debug("[TUI LLM] reasoning chunk: %s", text[:80])
                    continue

                if event_type == StreamEventType.COMPLETE:
                    break

                if text:
                    full_response += text
                    if on_token:
                        on_token(text)
                        await asyncio.sleep(0.01)
        except NotImplementedError:
            return await self.chat(messages)
        except (RuntimeError, ValueError) as exc:
            logger.exception("[TUI LLM] 流式调用异常")
            error_msg = f"[调用错误] {exc}"
            if on_token:
                on_token(error_msg)
            return error_msg

        return full_response

    async def chat(
        self,
        messages: list[LLMMessage],
    ) -> str:
        provider = self._get_provider()
        if provider is None:
            return "[错误] LLM Provider 未注册。"

        request = self._build_request(messages, stream=False)
        try:
            response = await provider.generate(request)
            return str(getattr(response, "output", "") or "")
        except (RuntimeError, ValueError) as exc:
            logger.exception("[TUI LLM] 调用异常")
            return f"[调用错误] {exc}"

    def is_configured(self) -> bool:
        """通过 Cell 公共服务检查角色是否已配置."""
        config = self._resolve_provider_config()
        if not config.get("ok"):
            return self._get_provider() is not None

        provider_id = str(config.get("provider_id") or "").strip()
        provider_type = str(config.get("provider_type") or "").strip()
        return bool(provider_id and provider_type)


def get_role_system_prompt(role: str) -> str:
    """获取角色的系统提示词."""

    prompts = {
        "architect": """你是 Architect (中书令)，负责系统架构设计。

职责：
1. 设计系统架构和模块结构
2. 定义 API 契约和接口
3. 创建技术规范文档
4. 审查和验证架构决策

思考方式：
- 考虑可扩展性和可维护性
- 定义清晰的模块边界
- 明确接口和数据流
- 合理选择技术栈

提供清晰、可操作的设计方案。""",
        "chief_engineer": """你是 Chief Engineer (工部尚书)，负责实现规划。

职责：
1. 分析现有代码库结构
2. 创建详细的实现计划
3. 定义文件组织和依赖关系
4. 生成构造蓝图

思考方式：
- 将任务分解为可执行的步骤
- 识别依赖和前置条件
- 考虑代码组织
- 规划测试和验证

提供清晰、可执行的蓝图。""",
        "director": """你是 Director (工匠)，负责代码生成和实现。

职责：
1. 编写干净、可用的代码
2. 实现函数和类
3. 编写测试
4. 修复 bug 和问题

编码原则：
- 遵循最佳实践
- 编写清晰、可维护的代码
- 包含适当的错误处理
- 添加注释和文档

生成生产就绪的代码。""",
        "pm": """你是 PM (尚书令)，负责项目管理和协调。

职责：
1. 将高层目标分解为可执行的任务
2. 协调不同角色之间的工作
3. 跟踪项目进度
4. 做出战略决策

规划原则：
- 考虑资源约束
- 识别依赖关系
- 设置优先级
- 规划迭代

提供清晰、可操作的项目计划。""",
    }
    return prompts.get(role, "你是一个有用的 AI 助手。")
