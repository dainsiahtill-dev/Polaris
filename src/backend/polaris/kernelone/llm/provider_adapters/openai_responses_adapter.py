"""OpenAI provider adapter (Responses API / Chat Completions).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §7 ProviderAdapter

职责：
    在 ConversationState (typed transcript) 与 OpenAI Chat Completions 格式之间做双向转换。
    依赖底层 BaseProvider (polaris.infrastructure.llm.providers.OpenAIProvider)
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
    decode_common_stream_error,
    decode_common_stream_transcript_items,
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

    function_call = delta.get("function_call")
    if isinstance(function_call, dict):
        arguments, arguments_text, arguments_complete = serialize_input_payload(function_call.get("arguments"))
        tool_calls_out.append(
            {
                "tool": str(function_call.get("name") or ""),
                "arguments": arguments,
                "arguments_text": arguments_text,
                "arguments_complete": arguments_complete,
                "call_id": str(delta.get("id") or function_call.get("id") or ""),
            }
        )

    return transcript_items, tool_calls_out


def _coerce_token_count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_usage_dict(raw: dict[str, Any]) -> dict[str, Any]:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        response = raw.get("response")
        if isinstance(response, dict):
            usage = response.get("usage")
    if not isinstance(usage, dict):
        return {}

    prompt_tokens = _coerce_token_count(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _coerce_token_count(usage.get("completion_tokens") or usage.get("output_tokens"))
    total_tokens = _coerce_token_count(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens

    usage_dict: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    if isinstance(prompt_details, dict):
        cached_tokens = _coerce_token_count(prompt_details.get("cached_tokens"))
        if cached_tokens:
            usage_dict["cached_tokens"] = cached_tokens
    for key in (
        "prompt_tokens_details",
        "completion_tokens_details",
        "input_tokens_details",
        "output_tokens_details",
    ):
        value = usage.get(key)
        if isinstance(value, dict):
            usage_dict[key] = dict(value)
    return usage_dict


def _extract_responses_content_block(block: dict[str, Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    block_type = str(block.get("type") or "").strip().lower()
    transcript_items: list[Any] = []
    tool_calls_out: list[dict[str, Any]] = []

    if block_type in {"output_text", "text", "refusal"}:
        for text in _flatten_text(block.get("text") or block.get("content")):
            transcript_items.append(AssistantMessage(content=text))
    elif "reason" in block_type or "think" in block_type or block_type == "summary_text":
        for text in _flatten_text(block.get("text") or block.get("content") or block.get("summary")):
            transcript_items.append(ReasoningSummary(content=text))
    elif block_type in {"function_call", "tool_call"}:
        arguments, arguments_text, arguments_complete = serialize_input_payload(block.get("arguments"))
        tool_calls_out.append(
            {
                "tool": str(block.get("name") or block.get("tool") or ""),
                "arguments": arguments,
                "arguments_text": arguments_text,
                "arguments_complete": arguments_complete,
                "call_id": str(block.get("call_id") or block.get("id") or ""),
                "index": block.get("index"),
            }
        )
    else:
        for text in _flatten_text(block):
            transcript_items.append(AssistantMessage(content=text))

    return transcript_items, tool_calls_out


def _extract_responses_output_item(item: dict[str, Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    item_type = str(item.get("type") or "").strip().lower()
    transcript_items: list[Any] = []
    tool_calls_out: list[dict[str, Any]] = []

    if item_type == "message":
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                nested_items, nested_tools = _extract_responses_content_block(block)
                transcript_items.extend(nested_items)
                tool_calls_out.extend(nested_tools)
        elif content:
            for text in _flatten_text(content):
                transcript_items.append(AssistantMessage(content=text))
    elif item_type == "reasoning":
        for key in ("summary", "content", "text", "reasoning"):
            value = item.get(key)
            if isinstance(value, list):
                for block in value:
                    if isinstance(block, dict):
                        nested_items, _ = _extract_responses_content_block(block)
                        transcript_items.extend(
                            ReasoningSummary(content=nested.content) if isinstance(nested, AssistantMessage) else nested
                            for nested in nested_items
                        )
                    else:
                        for text in _flatten_text(block):
                            transcript_items.append(ReasoningSummary(content=text))
            else:
                for text in _flatten_text(value):
                    transcript_items.append(ReasoningSummary(content=text))
    elif item_type in {"function_call", "tool_call"}:
        nested_items, nested_tools = _extract_responses_content_block(item)
        transcript_items.extend(nested_items)
        tool_calls_out.extend(nested_tools)
    else:
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    nested_items, nested_tools = _extract_responses_content_block(block)
                    transcript_items.extend(nested_items)
                    tool_calls_out.extend(nested_tools)

    return transcript_items, tool_calls_out


def _extract_responses_output(raw: dict[str, Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    transcript_items: list[Any] = []
    tool_calls_out: list[dict[str, Any]] = []

    output = raw.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            nested_items, nested_tools = _extract_responses_output_item(item)
            transcript_items.extend(nested_items)
            tool_calls_out.extend(nested_tools)

    for text in _flatten_text(raw.get("output_text")):
        transcript_items.append(AssistantMessage(content=text))

    return transcript_items, tool_calls_out


def _extract_responses_stream_event(raw_event: dict[str, Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    event_type = str(raw_event.get("type") or raw_event.get("event") or "").strip().lower()
    transcript_items: list[Any] = []
    tool_calls_out: list[dict[str, Any]] = []

    if event_type in {"response.output_item.added", "response.output_item.done"}:
        item = raw_event.get("item")
        if isinstance(item, dict):
            return _extract_responses_output_item(item)
    elif event_type in {"response.content_part.added", "response.content_part.done"}:
        part = raw_event.get("part")
        if isinstance(part, dict):
            return _extract_responses_content_block(part)
    elif event_type in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
        arguments_text = str(raw_event.get("delta") or raw_event.get("arguments") or "")
        arguments, parsed_text, arguments_complete = serialize_input_payload(arguments_text)
        tool_calls_out.append(
            {
                "tool": str(raw_event.get("name") or ""),
                "arguments": arguments,
                "arguments_text": parsed_text or arguments_text,
                "arguments_complete": event_type.endswith(".done") and arguments_complete,
                "call_id": str(raw_event.get("call_id") or raw_event.get("item_id") or ""),
                "index": raw_event.get("output_index"),
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
        Provider stream chunk dict → decode_stream_event() → DecodedProviderOutput | None

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
            delta = choice.get("message") or choice.get("delta") or {}

            if isinstance(delta, dict):
                transcript_items, tool_calls_out = _extract_content_items(delta)

        if isinstance(raw, dict):
            responses_items, responses_tools = _extract_responses_output(raw)
            transcript_items.extend(responses_items)
            tool_calls_out.extend(responses_tools)
        usage_dict = _extract_usage_dict(raw) if isinstance(raw, dict) else {}

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
        """将 OpenAI provider stream chunk dict 解码为 DecodedProviderOutput。

        OpenAI provider stream 格式示例：
            data: {"choices":[{"delta":{"content":"hello"},"index":0}]}

        Returns:
            DecodedProviderOutput（含增量内容/工具调用）或 None（ping/done 事件）。
        """
        if not isinstance(raw_event, dict):
            return None

        event_type = str(raw_event.get("event") or "").strip().lower()
        if event_type in ("ping", "session.complete"):
            return None
        error = decode_common_stream_error(raw_event)
        if error:
            return DecodedProviderOutput(
                transcript_items=[],
                tool_calls=[],
                usage=_extract_usage_dict(raw_event),
                raw=raw_event,
                error=error,
            )

        usage_dict = _extract_usage_dict(raw_event)
        responses_items, responses_tools = _extract_responses_stream_event(raw_event)
        if responses_items or responses_tools or usage_dict:
            return DecodedProviderOutput(
                transcript_items=responses_items,
                tool_calls=responses_tools,
                usage=usage_dict,
                raw=raw_event,
            )

        choices = raw_event.get("choices")
        if not isinstance(choices, list) or not choices:
            common_items = decode_common_stream_transcript_items(raw_event)
            if not common_items:
                return None
            return DecodedProviderOutput(
                transcript_items=common_items,
                tool_calls=[],
                usage=usage_dict,
                raw=raw_event,
            )

        if not isinstance(choices[0], dict):
            return None

        delta = choices[0].get("delta", {})
        if not isinstance(delta, dict):
            common_items = decode_common_stream_transcript_items(raw_event)
            if not common_items:
                return None
            return DecodedProviderOutput(
                transcript_items=common_items,
                tool_calls=[],
                usage=usage_dict,
                raw=raw_event,
            )

        transcript_items: list[Any] = []
        tool_calls_out: list[dict[str, Any]] = []

        transcript_items, tool_calls_out = _extract_content_items(delta)
        if not transcript_items and not tool_calls_out:
            transcript_items = decode_common_stream_transcript_items(raw_event)

        if not transcript_items and not tool_calls_out:
            return None

        return DecodedProviderOutput(
            transcript_items=transcript_items,
            tool_calls=tool_calls_out,
            usage=usage_dict,
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

        return _extract_usage_dict(raw)
