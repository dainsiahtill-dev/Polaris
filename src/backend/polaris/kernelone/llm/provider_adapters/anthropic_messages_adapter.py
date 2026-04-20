"""Anthropic provider adapter (Messages API).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §7 ProviderAdapter

IMPORTANT: Adapter 层构建的 messages 必须在 Provider 层被正确使用。
不要在此文件中重新构建 messages，应该使用 config['messages']。

职责：
    在 ConversationState (typed transcript) 与 Anthropic Messages API 格式之间做双向转换。
    依赖底层 BaseProvider (polaris.infrastructure.llm.providers.AnthropicCompatProvider)
    处理 HTTP/流式通信和工具格式。

设计约束：
    1. build_request() 从 ConversationState.transcript 构建 Anthropic messages 格式，
       放入返回 dict 的 "prompt" (用于 BaseProvider.invoke) 和 "config" 字段。
       Anthropic config 特殊字段: system (str), tools (list), max_tokens, messages。
    2. decode_response() / decode_stream_event() 从 BaseProvider.InvokeResult.raw
       提取 TranscriptDelta 兼容条目。
    3. build_tool_result_payload() 将 tool result 转换为 Anthropic tool_result content block 格式。
    4. Phase 3 集成点：TurnEngine 用 adapter 替代 kernel._llm_caller.call()。

公共辅助函数：
    - serialize_transcript_for_prompt(): 从 base.py 复用
    - serialize_input_payload(): 从 base.py 复用
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


def _build_anthropic_messages_from_transcript(
    state: ConversationStateLike,
) -> list[dict[str, Any]]:
    """将 ConversationState.transcript 转换为 Anthropic Messages API 格式。

    Anthropic 格式：
        - user: {"role": "user", "content": [{"type": "text", "text": "..."}]}
        - assistant: {"role": "assistant", "content": [...]} 含 text/tool_use blocks
        - tool result: {"role": "user", "content": [{"type": "tool_result", ...}]}
        - system: 单独 system 字段，不作为消息
    """
    messages: list[dict[str, Any]] = []

    for item in state.transcript:
        item_type = type(item).__name__

        if item_type == "SystemInstruction":
            # System instructions handled separately in build_request
            continue

        elif item_type == "UserMessage":
            content_str = item.content or ""
            msg: dict[str, Any] = {
                "role": "user",
                "content": [{"type": "text", "text": content_str}],
            }
            messages.append(msg)

        elif item_type == "AssistantMessage":
            content_blocks: list[dict[str, Any]] = []
            tool_calls_out: list[dict[str, Any]] = []

            if item.content:
                content_blocks.append({"type": "text", "text": item.content})

            # Append any accumulated tool calls
            if tool_calls_out:
                content_blocks.extend(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    }
                    for tc in tool_calls_out
                )

            msg = {"role": "assistant", "content": content_blocks}
            if tool_calls_out:
                msg["tool_calls"] = tool_calls_out
            messages.append(msg)

        elif item_type == "ToolCall":
            tc = item
            func_call: dict[str, Any] = {
                "name": tc.tool_name or "",
                "arguments": json.dumps(tc.args or {}, ensure_ascii=False),
            }
            tool_call_entry: dict[str, Any] = {
                "id": tc.call_id or f"tool_{tc.tool_name}_{tc.tool_name}",
                "type": "function",
                "function": func_call,
            }
            # Try to append to last assistant message
            existing = messages[-1] if messages else None
            if existing and existing.get("role") == "assistant":
                # Append as tool_use block and tool_calls entry
                if "tool_calls" not in existing:
                    existing["tool_calls"] = []
                existing["tool_calls"].append(tool_call_entry)
                existing.setdefault("content", []).append(
                    {
                        "type": "tool_use",
                        "id": tool_call_entry["id"],
                        "name": tool_call_entry["function"]["name"],
                        "input": json.loads(tool_call_entry["function"]["arguments"]),
                    }
                )
            else:
                # Create new assistant message with tool call
                content_blocks = [
                    {
                        "type": "tool_use",
                        "id": tool_call_entry["id"],
                        "name": tool_call_entry["function"]["name"],
                        "input": json.loads(tool_call_entry["function"]["arguments"]),
                    }
                ]
                msg = {
                    "role": "assistant",
                    "content": content_blocks,
                    "tool_calls": [tool_call_entry],
                }
                messages.append(msg)

        elif item_type == "ToolResult":
            content_str = item.content or ""
            tool_msg: dict[str, Any] = {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": item.call_id or "",
                        "content": content_str,
                    }
                ],
            }
            messages.append(tool_msg)

        elif item_type == "ReasoningSummary":
            if item.content:
                messages.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"<thinking>\n{item.content}\n</thinking>"}],
                    }
                )

        elif item_type == "ControlEvent" and item.reason:
            messages.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": f"[Event: {item.event_type}] {item.reason}"}],
                }
            )

    return messages


class AnthropicMessagesAdapter(ProviderAdapter):
    """Anthropic Messages API adapter.

    转换链路：
        ConversationState → build_request() → BaseProvider.invoke(config)
        InvokeResult.raw → decode_response() → DecodedProviderOutput
        SSE chunk dict → decode_stream_event() → DecodedProviderOutput | None

    Anthropic Messages API 特殊格式：
        - System prompt: 单独 config["system"] 字段（不是消息）
        - Messages: role + content block 列表（text/tool_use/tool_result）
        - Tool calls: assistant 消息的 tool_calls 字段
        - Tool results: user 消息含 tool_result content block
        - max_tokens: 必需，控制生成长度
    """

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def build_request(
        self,
        state: ConversationStateLike,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        """将 ConversationState 构建为 Anthropic Messages API 请求格式。

        Returns:
            dict with keys:
            - "prompt": str — transcript 纯文本（用于 BaseProvider.invoke prompt 参数）
            - "config": dict — provider config with messages, tools, system, stream, etc.
              (config 会被传入 BaseProvider.invoke/config，或在 Phase 3 直接使用 messages)
        """
        messages = _build_anthropic_messages_from_transcript(state)

        # System prompt from state.system_prompt as separate config field
        system_prompt = state.system_prompt or ""

        prompt_text = serialize_transcript_for_prompt(state)

        config: dict[str, Any] = {
            "messages": messages,
            "system": system_prompt,
            "stream": stream,
            "model": state.model or "claude-3-haiku-20240307",
            "max_tokens": 4096,  # Anthropic requires max_tokens
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

        if not isinstance(raw, dict):
            return DecodedProviderOutput(
                transcript_items=transcript_items,
                tool_calls=tool_calls_out,
                usage={},
                raw=raw,
            )

        # Anthropic response: {"content": [...], "stop_reason": "...", "usage": {...}}
        content_blocks = raw.get("content", [])
        if not isinstance(content_blocks, list):
            content_blocks = []

        for block in content_blocks:
            if not isinstance(block, dict):
                continue

            block_type = str(block.get("type") or "").strip().lower()

            if block_type == "text":
                text = str(block.get("text") or "")
                if text:
                    transcript_items.append(AssistantMessage(content=text))

            elif block_type == "tool_use":
                # Tool call from assistant
                tool_calls_out.append(
                    {
                        "tool": str(block.get("name") or ""),
                        "arguments": block.get("input") or {},
                        "call_id": str(block.get("id") or ""),
                    }
                )

        # Usage: Anthropic uses input_tokens / output_tokens / total_tokens
        usage_dict: dict[str, Any] = {}
        usage = raw.get("usage")
        if isinstance(usage, dict):
            usage_dict = {
                "prompt_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
                "total_tokens": int(
                    usage.get("total_tokens")
                    or (int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0))
                ),
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
        """将 Anthropic SSE chunk dict 解码为 DecodedProviderOutput。

        Anthropic SSE 事件类型：
            - content_block_delta: delta with text or thinking
            - content_block_stop: end of a content block
            - message_stop: end of the message
            - ping: heartbeat, ignore

        Returns:
            DecodedProviderOutput（含增量内容/工具调用）或 None（ping/done 事件）。
        """
        if not isinstance(raw_event, dict):
            return None

        event_type = str(raw_event.get("type") or "").strip().lower()

        if event_type == "ping":
            return None

        if event_type in ("message_stop", "content_block_stop"):
            return None

        transcript_items: list[Any] = []
        tool_calls_out: list[dict[str, Any]] = []

        if event_type == "content_block_delta":
            delta = raw_event.get("delta", {})
            if not isinstance(delta, dict):
                delta = {}

            delta_type = str(delta.get("type") or "").strip().lower()

            if delta_type in {"thinking_delta", "thinking"}:
                # Native thinking content
                thinking_text = str(delta.get("thinking") or delta.get("text") or "")
                if thinking_text:
                    transcript_items.append(ReasoningSummary(content=thinking_text))

            elif delta_type == "text_delta":
                text = str(delta.get("text") or "")
                if text:
                    transcript_items.append(AssistantMessage(content=text))

            elif delta_type == "input_json_delta":
                partial_json = str(delta.get("partial_json") or "")
                if partial_json:
                    tool_calls_out.append(
                        {
                            "tool": "",
                            "arguments": {},
                            "arguments_text": partial_json,
                            "arguments_complete": False,
                            "call_id": "",
                            "content_block_index": raw_event.get("index"),
                        }
                    )

        elif event_type == "content_block_start":
            block = raw_event.get("content_block", {})
            if isinstance(block, dict):
                block_type = str(block.get("type") or "").strip().lower()
                if block_type == "tool_use":
                    arguments, arguments_text, arguments_complete = serialize_input_payload(block.get("input"))
                    tool_calls_out.append(
                        {
                            "tool": str(block.get("name") or ""),
                            "arguments": arguments,
                            "arguments_text": arguments_text,
                            "arguments_complete": arguments_complete,
                            "call_id": str(block.get("id") or ""),
                            "content_block_index": raw_event.get("index"),
                        }
                    )

        elif event_type == "message_delta":
            # Final message data (e.g., stop reason)
            pass

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
        """将 tool execution result 转换为 Anthropic tool_result content block 格式。

        Anthropic tool result 格式（Messages API）：
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_abc",
                        "content": "...",
                    }
                ]
            }

        Returns the content block (caller wraps in user message if needed).
        """
        call_id = str(
            tool_result.get("call_id") or tool_result.get("tool_call_id") or tool_result.get("tool_use_id") or ""
        )
        content = ""
        if isinstance(tool_result.get("result"), dict):
            content = json.dumps(tool_result["result"], ensure_ascii=False, indent=2)
        elif tool_result.get("result") is not None:
            content = str(tool_result["result"])
        if not content and tool_result.get("error"):
            content = f"Error: {tool_result.get('error')}"

        return {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": content,
        }

    def extract_usage(
        self,
        raw_response: InvokeResult | dict[str, Any],
    ) -> dict[str, Any]:
        """从 Anthropic response 提取 usage 信息。"""
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

        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        return {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
        }
