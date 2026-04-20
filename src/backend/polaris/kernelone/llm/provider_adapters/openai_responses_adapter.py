"""OpenAI provider adapter (Responses API / Chat Completions).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §7 ProviderAdapter

职责：
    在 ConversationState (typed transcript) 与 OpenAI Chat Completions 格式之间做双向转换。
    依赖底层 BaseProvider (polaris.infrastructure.llm.providers.OpenAICompatProvider)
    处理 HTTP/流式通信和工具格式。

设计约束：
    1. build_request() 从 ConversationState.transcript 构建 OpenAI messages 格式，
       放入返回 dict 的 "prompt" (用于 BaseProvider.invoke) 和 "config" 字段。
    2. decode_response() / decode_stream_event() 从 BaseProvider.InvokeResult.raw
       提取 TranscriptDelta 兼容条目。
    3. build_tool_result_payload() 将 tool result 转换为 OpenAI tool role 消息格式。
    4. Phase 3 集成点：TurnEngine 用 adapter 替代 kernel._llm_caller.call()。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.kernelone.llm.provider_adapters.base import (
    AssistantMessage,
    ConversationStateLike,
    DecodedProviderOutput,
    ProviderAdapter,
    ReasoningSummary,
    serialize_input_payload,
    serialize_transcript_for_prompt,
)
from polaris.kernelone.llm.types import InvokeResult

logger = logging.getLogger(__name__)


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_text(item))
        return out
    if isinstance(value, dict):
        out_dict: list[str] = []  # renamed to avoid redef
        for key in ("text", "content", "value"):
            nested = value.get(key)
            if isinstance(nested, str) and nested:
                out_dict.append(nested)
            elif isinstance(nested, (list, dict)):
                out_dict.extend(_flatten_text(nested))
        return out_dict
    text = str(value or "")
    return [text] if text else []


def _extract_content_items(delta: dict[str, Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    transcript_items: list[Any] = []
    tool_calls_out: list[dict[str, Any]] = []

    for key in ("reasoning_content", "reasoning", "thinking"):
        for text in _flatten_text(delta.get(key)):
            if not text:
                continue
            transcript_items.append(ReasoningSummary(content=text))

    content_value = delta.get("content")
    if isinstance(content_value, str):
        if content_value:
            transcript_items.append(AssistantMessage(content=content_value))
    elif isinstance(content_value, list):
        for item in content_value:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            payloads = _flatten_text(item)
            if "reason" in item_type or "think" in item_type:
                for text in payloads:
                    if text:
                        transcript_items.append(ReasoningSummary(content=text))
            else:
                for text in payloads:
                    if text:
                        transcript_items.append(AssistantMessage(content=text))

    raw_tcs = delta.get("tool_calls")
    if isinstance(raw_tcs, list):
        for tc in raw_tcs:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", {})
            if not isinstance(func, dict):
                continue
            arguments, arguments_text, arguments_complete = serialize_input_payload(func.get("arguments"))
            tool_calls_out.append(
                {
                    "tool": str(func.get("name") or ""),
                    "arguments": arguments,
                    "arguments_text": arguments_text,
                    "arguments_complete": arguments_complete,
                    "call_id": str(tc.get("id") or ""),
                    "index": tc.get("index"),
                }
            )

    return transcript_items, tool_calls_out


def _build_messages_from_transcript(state: ConversationStateLike) -> list[dict[str, Any]]:
    """将 ConversationState.transcript 转换为 OpenAI messages 格式。

    用于 BaseProvider.invoke() 的 config["messages"] 覆盖，
    以及最终放入返回的 config dict 供 Phase 3 TurnEngine 直接使用。
    """
    messages: list[dict[str, Any]] = []

    for item in state.transcript:
        item_type = type(item).__name__

        if item_type == "SystemInstruction":
            msg: dict[str, Any] = {"role": "system", "content": item.content or ""}
            messages.append(msg)

        elif item_type == "UserMessage":
            messages.append({"role": "user", "content": item.content or ""})

        elif item_type == "AssistantMessage":
            assistant_content: str | list[dict[str, Any]] = ""
            tool_calls_out: list[dict[str, Any]] = []

            if item.content:
                assistant_content = item.content

            if tool_calls_out:
                msg = {
                    "role": "assistant",
                    "content": assistant_content if isinstance(assistant_content, str) else "",
                    "tool_calls": tool_calls_out,
                }
            else:
                msg = {"role": "assistant", "content": assistant_content}
            messages.append(msg)

        elif item_type == "ToolCall":
            tc = item
            func_call: dict[str, Any] = {
                "name": tc.tool_name or "",
                "arguments": json.dumps(tc.args or {}, ensure_ascii=False),
            }
            tool_call_entry: dict[str, Any] = {
                "id": tc.call_id or f"call_{tc.tool_name}_{tc.tool_name}",
                "type": "function",
                "function": func_call,
            }
            existing = messages[-1] if messages else None
            if existing and existing.get("role") == "assistant" and "tool_calls" in existing:
                existing["tool_calls"].append(tool_call_entry)
            else:
                msg = {"role": "assistant", "content": "", "tool_calls": [tool_call_entry]}
                messages.append(msg)

        elif item_type == "ToolResult":
            content_str = item.content or ""
            tool_msg: dict[str, Any] = {
                "role": "tool",
                "tool_call_id": item.call_id or "",
                "content": content_str,
            }
            messages.append(tool_msg)

        elif item_type == "ReasoningSummary":
            if item.content:
                messages.append({"role": "assistant", "content": f"<thinking>\n{item.content}\n</thinking>"})

        elif item_type == "ControlEvent":
            if item.reason:
                messages.append({"role": "system", "content": f"[Event: {item.event_type}] {item.reason}"})

    return messages


def _parse_openai_tool_calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """从 OpenAI response raw dict 解析 tool_calls 列表。"""
    tool_calls: list[dict[str, Any]] = []
    choices = raw.get("choices", []) if isinstance(raw, dict) else []
    if not choices:
        return tool_calls
    delta = choices[0].get("delta", {}) if isinstance(choices[0], dict) else {}
    if not isinstance(delta, dict):
        return tool_calls
    raw_calls = delta.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        return tool_calls
    for tc in raw_calls:
        if not isinstance(tc, dict):
            continue
        func = tc.get("function", {})
        if not isinstance(func, dict):
            continue
        try:
            args = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            args = {}
        tool_calls.append(
            {
                "tool": func.get("name", ""),
                "arguments": args,
                "call_id": tc.get("id", ""),
            }
        )
    return tool_calls


class OpenAIResponsesAdapter(ProviderAdapter):
    """OpenAI Responses / Chat Completions API adapter.

    转换链路：
        ConversationState → build_request() → BaseProvider.invoke(config)
        InvokeResult.raw → decode_response() → DecodedProviderOutput
        SSE chunk dict → decode_stream_event() → DecodedProviderOutput | None

    工具格式：
        复用 BaseProvider.invoke() config["tools"] 机制。
        工具结果通过 build_tool_result_payload() 转换为 tool role 消息，
        再追加到 ConversationState.transcript。
    """

    @property
    def provider_name(self) -> str:
        return "openai"

    def build_request(
        self,
        state: ConversationStateLike,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        """将 ConversationState 构建为 OpenAI API 请求格式。

        Returns:
            dict with keys:
            - "prompt": str — transcript 纯文本（用于 BaseProvider.invoke prompt 参数）
            - "config": dict — provider config with messages, tools, system_prompt, stream, etc.
              (config 会被传入 BaseProvider.invoke/config，或在 Phase 3 直接使用 messages)
        """
        messages = _build_messages_from_transcript(state)

        # system prompt 从 state.system_prompt 注入为第一条 system 消息
        if state.system_prompt:
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = state.system_prompt + "\n" + messages[0].get("content", "")
            else:
                messages.insert(0, {"role": "system", "content": state.system_prompt})

        prompt_text = serialize_transcript_for_prompt(state)

        config: dict[str, Any] = {
            "messages": messages,
            "stream": stream,
            "model": state.model or "gpt-4o",
        }

        if hasattr(state, "temperature"):
            config["temperature"] = getattr(state, "temperature", 0.7)

        return {
            "prompt": prompt_text,
            "config": config,
        }

    def decode_response(
        self,
        raw_response: InvokeResult | dict[str, Any],
    ) -> DecodedProviderOutput:
        """将 BaseProvider InvokeResult.raw 解码为 DecodedProviderOutput。

        Args:
            raw_response: BaseProvider.invoke() 返回的 InvokeResult，
                         或直接传入 .raw dict。

        Returns:
            DecodedProviderOutput — 包含 transcript_items, tool_calls, usage。
        """
        raw: dict[str, Any]
        if isinstance(raw_response, InvokeResult):
            raw = raw_response.raw or {}
        else:
            raw = raw_response if isinstance(raw_response, dict) else {}

        transcript_items: list[Any] = []
        tool_calls_out: list[dict[str, Any]] = []

        choices = raw.get("choices", []) if isinstance(raw, dict) else []
        if choices and isinstance(choices[0], dict):
            choice = choices[0]
            delta = choice.get("delta", {})

            if isinstance(delta, dict):
                transcript_items, tool_calls_out = _extract_content_items(delta)

        usage_dict: dict[str, Any] = {}
        if isinstance(raw, dict):
            usage = raw.get("usage")
            if isinstance(usage, dict):
                usage_dict = {
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                }

        return DecodedProviderOutput(
            transcript_items=transcript_items,
            tool_calls=tool_calls_out,
            usage=usage_dict,
            raw=raw,
        )

    def decode_stream_event(
        self,
        raw_event: dict[str, Any],
    ) -> DecodedProviderOutput | None:
        """将 OpenAI SSE chunk dict 解码为 DecodedProviderOutput。

        OpenAI SSE 格式示例：
            data: {"choices":[{"delta":{"content":"hello"},"index":0}]}

        Returns:
            DecodedProviderOutput（含增量内容/工具调用）或 None（ping/done 事件）。
        """
        if not isinstance(raw_event, dict):
            return None

        event_type = str(raw_event.get("event") or "").strip().lower()
        if event_type in ("ping", "session.complete"):
            return None

        choices = raw_event.get("choices")
        if not isinstance(choices, list) or not choices:
            return None

        delta = choices[0].get("delta", {})
        if not isinstance(delta, dict):
            return None

        transcript_items: list[Any] = []
        tool_calls_out: list[dict[str, Any]] = []

        transcript_items, tool_calls_out = _extract_content_items(delta)

        if not transcript_items and not tool_calls_out:
            return None

        return DecodedProviderOutput(
            transcript_items=transcript_items,
            tool_calls=tool_calls_out,
            usage={},
            raw=raw_event,
        )

    def build_tool_result_payload(
        self,
        tool_result: dict[str, Any],
    ) -> dict[str, Any]:
        """将 tool execution result 转换为 OpenAI tool role 消息格式。

        OpenAI tool result 格式（Messages API）：
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "content": "...",
            }
        """
        call_id = str(tool_result.get("call_id") or tool_result.get("tool_call_id") or "")
        content = ""
        if isinstance(tool_result.get("result"), dict):
            content = json.dumps(tool_result["result"], ensure_ascii=False, indent=2)
        elif tool_result.get("result") is not None:
            content = str(tool_result["result"])
        if not content and tool_result.get("error"):
            content = f"Error: {tool_result.get('error')}"

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": content,
        }

    def extract_usage(
        self,
        raw_response: InvokeResult | dict[str, Any],
    ) -> dict[str, Any]:
        """从 OpenAI response 提取 usage 信息。"""
        raw: dict[str, Any]
        if isinstance(raw_response, InvokeResult):
            raw = raw_response.raw or {}
        elif isinstance(raw_response, dict):
            raw = raw_response
        else:
            return {}

        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        if not isinstance(usage, dict):
            return {}
        return {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
