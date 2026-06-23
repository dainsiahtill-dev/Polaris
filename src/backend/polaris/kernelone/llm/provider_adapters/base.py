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


def _flatten_stream_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_stream_text(item))
        return out
    if isinstance(value, dict):
        out_dict: list[str] = []
        for key in ("text", "content", "value", "delta"):
            nested = value.get(key)
            if isinstance(nested, str) and nested:
                out_dict.append(nested)
            elif isinstance(nested, (list, dict)):
                out_dict.extend(_flatten_stream_text(nested))
        return out_dict
    text = str(value or "")
    return [text] if text else []


def decode_common_stream_transcript_items(raw_event: Any) -> list[Any]:
    """Normalize common provider stream text deltas into transcript items.

    This is intentionally narrow: it only accepts well-known text/reasoning
    delta shapes emitted by OpenAI-compatible, Responses API, Gemini-like, and
    Anthropic-compatible gateways. Unknown objects remain undecoded.
    """

    if not isinstance(raw_event, dict):
        return []

    event_type = str(raw_event.get("type") or raw_event.get("event") or "").strip().lower()
    if event_type in {
        "ping",
        "done",
        "session.complete",
        "message_stop",
        "content_block_stop",
        "response.completed",
        "response.done",
    }:
        return []

    items: list[Any] = []
    seen: set[tuple[str, str]] = set()

    def append_text(kind: str, value: Any) -> None:
        for text in _flatten_stream_text(value):
            if not text:
                continue
            key = (kind, text)
            if key in seen:
                continue
            seen.add(key)
            if kind == "reasoning":
                items.append(ReasoningSummary(content=text))
            else:
                items.append(AssistantMessage(content=text))

    for key in ("reasoning_content", "reasoning", "thinking"):
        append_text("reasoning", raw_event.get(key))

    if event_type in {"content_chunk", "text_delta", "message_delta", "output_text_delta"}:
        append_text("assistant", raw_event.get("content") or raw_event.get("text") or raw_event.get("delta"))
    elif event_type in {
        "thinking_delta",
        "reasoning_delta",
        "response.reasoning_text.delta",
        "response.reasoning.delta",
        "response.reasoning_summary_text.delta",
    }:
        append_text(
            "reasoning",
            raw_event.get("thinking")
            or raw_event.get("reasoning")
            or raw_event.get("text")
            or raw_event.get("content")
            or raw_event.get("delta"),
        )
    elif event_type in {"response.output_text.delta", "response.content_part.delta"}:
        append_text("assistant", raw_event.get("delta") or raw_event.get("text") or raw_event.get("content"))

    delta = raw_event.get("delta")
    if isinstance(delta, dict):
        delta_type = str(delta.get("type") or "").strip().lower()
        if "reason" in delta_type or "think" in delta_type:
            append_text("reasoning", delta.get("thinking") or delta.get("reasoning") or delta.get("text"))
        else:
            append_text("assistant", delta.get("content") or delta.get("text"))
        for key in ("reasoning_content", "reasoning", "thinking"):
            append_text("reasoning", delta.get(key))

    message = raw_event.get("message")
    if isinstance(message, dict):
        append_text("assistant", message.get("content") or message.get("text"))

    # Some gateways emit bare {"content": "..."} / {"text": "..."} chunks.
    if "content" in raw_event:
        append_text("assistant", raw_event.get("content"))
    elif "text" in raw_event and "reason" not in event_type and "think" not in event_type:
        append_text("assistant", raw_event.get("text"))

    candidates = raw_event.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        append_text("assistant", part.get("text") or part.get("content"))

    return items


def decode_common_stream_error(raw_event: Any) -> str | None:
    """Extract provider-native stream errors without turning them into text."""

    if not isinstance(raw_event, dict):
        return None

    event_type = str(raw_event.get("type") or raw_event.get("event") or "").strip().lower()
    error_value = raw_event.get("error")
    if event_type == "error" or error_value:
        if isinstance(error_value, dict):
            message = str(error_value.get("message") or error_value.get("type") or error_value.get("code") or "")
            return message.strip() or "Provider stream error"
        message = str(error_value or raw_event.get("message") or "")
        return message.strip() or "Provider stream error"

    if event_type in {"response.failed", "response.incomplete"}:
        response = raw_event.get("response")
        if not isinstance(response, dict):
            response = raw_event
        response_error = response.get("error")
        if isinstance(response_error, dict):
            message = str(response_error.get("message") or response_error.get("code") or "")
            if message.strip():
                return message.strip()
        incomplete = response.get("incomplete_details")
        if isinstance(incomplete, dict):
            reason = str(incomplete.get("reason") or "")
            if reason.strip():
                return f"Response incomplete: {reason.strip()}"
        status = str(response.get("status") or event_type)
        return status.strip() or "Provider stream error"

    return None


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
    error: str | None = None


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
