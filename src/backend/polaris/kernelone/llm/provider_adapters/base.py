"""Base provider adapter interface.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §7 ProviderAdapter

设计原则：
    Provider adapter 只依赖 KernelOne 级契约，不反向依赖 cells/internal。
    Provider adapter 在 decode_response() / decode_stream_event() 中填充
    轻量 transcript item，usage 信息由调用方通过 adapter.extract_usage()
    单独获取。

公共辅助函数：
    - serialize_transcript_for_prompt(): 将 transcript 序列化为纯文本
    - serialize_input_payload(): 将原始输入解析为 JSON 参数
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = __import__("logging").getLogger(__name__)


# ============================================================================
# 公共辅助函数（可被所有 Provider 适配器复用）
# ============================================================================


def serialize_transcript_for_prompt(state: ConversationStateLike) -> str:
    """将 ConversationState.transcript 序列化为纯文本，用于 BaseProvider.invoke() prompt。

    BaseProvider.invoke(prompt, ...) 只接受字符串。
    这里将 transcript 转换为可读的多角色对话字符串。

    所有 Provider 适配器共享此实现，确保 prompt 格式一致性。
    """
    lines: list[str] = []
    for item in state.transcript:
        item_type = type(item).__name__
        if item_type == "UserMessage":
            lines.append(f"User: {item.content}")
        elif item_type == "AssistantMessage":
            if item.thinking:
                lines.append(f"<thinking>\n{item.thinking}\n</thinking>")
            if item.content:
                lines.append(f"Assistant: {item.content}")
        elif item_type == "ToolCall":
            args_str = json.dumps(item.args or {}, ensure_ascii=False)
            lines.append(f"Assistant tool call: {item.tool_name} {args_str}")
        elif item_type == "ToolResult":
            content = item.content or ""
            lines.append(f"Tool result: {content}")
        elif item_type == "ReasoningSummary":
            if item.content:
                lines.append(f"<thinking>\n{item.content}\n</thinking>")
        elif item_type == "SystemInstruction":
            if item.content:
                lines.append(f"[System]: {item.content}")
        elif item_type == "ControlEvent" and item.reason:
            lines.append(f"[Event: {item.event_type}] {item.reason}")
    lines.append("Assistant:")
    return "\n".join(lines)


def serialize_input_payload(value: Any) -> tuple[dict[str, Any], str, bool]:
    """将原始输入解析为 JSON 参数。

    Args:
        value: 原始输入，可以是 dict、str 或其他类型

    Returns:
        (parsed_dict, original_text, is_complete)
        - parsed_dict: 解析后的字典
        - original_text: 原始文本表示
        - is_complete: 是否是完整的 JSON 对象
    """
    if isinstance(value, dict):
        return dict(value), json.dumps(value, ensure_ascii=False), True
    if value is None:
        return {}, "", False
    text = str(value or "")
    if not text:
        return {}, "", False
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}, text, False
    if isinstance(parsed, dict):
        return parsed, text, True
    return {"value": parsed}, text, True


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """KernelOne-neutral assistant text delta item."""

    content: str


@dataclass(frozen=True, slots=True)
class ReasoningSummary:
    """KernelOne-neutral reasoning delta item."""

    content: str


class ConversationStateLike(Protocol):
    """Minimal state shape required by provider adapters."""

    transcript: list[Any]
    system_prompt: str | None
    model: str | None


@dataclass
class DecodedProviderOutput:
    """Provider 响应解码结果（包含轻量 transcript item + usage）.

    这是 decode_response() / decode_stream_event() 的返回值。
    usage 字段从 provider 原始响应中提取，供 KernelOne 做 token 审计。
    """

    transcript_items: list[Any] = field(default_factory=list)
    tool_calls: list[Any] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


class ProviderAdapter(ABC):
    """Provider adapter 抽象基类."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 名称，如 'openai', 'anthropic'."""
        ...

    @abstractmethod
    def build_request(
        self,
        state: ConversationStateLike,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        """从对话状态构建 provider 原生请求."""
        ...

    @abstractmethod
    def decode_response(
        self,
        raw_response: Any,
    ) -> DecodedProviderOutput:
        """解码 provider 响应为 DecodedProviderOutput."""
        ...

    @abstractmethod
    def decode_stream_event(
        self,
        raw_event: Any,
    ) -> DecodedProviderOutput | None:
        """解码 provider 流式事件为 DecodedProviderOutput."""
        ...

    @abstractmethod
    def build_tool_result_payload(
        self,
        tool_result: Any,
    ) -> Any:
        """将 ToolExecutionResult 构建为 provider 原生 tool result payload."""
        ...

    @abstractmethod
    def extract_usage(
        self,
        raw_response: Any,
    ) -> dict[str, Any]:
        """从响应中提取 usage 信息."""
        ...
