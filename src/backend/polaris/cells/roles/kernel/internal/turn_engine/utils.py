"""TurnEngine utility functions - standalone helpers for turn processing.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §10 TurnEngine - Wave 3 Utils Extraction

职责：
    提供静态工具函数，供 TurnEngine.run() / run_stream() 使用。
    这些函数不依赖实例状态，可独立调用。

Wave 3 提取内容:
    - _tool_call_signature_from_parsed: 构建稳定的工具调用签名
    - _resolve_empty_visible_output_error: 检测空输出错误
    - _normalize_stream_tool_call_payload: 归一化流式工具事件
    - _merge_stream_thinking: 合并解析思考内容与流式思考块
    - _append_transcript_cycle: 追加 assistant turn 到 transcript
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.turn_engine.artifacts import AssistantTurnArtifacts

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 工具调用签名与去重
# ─────────────────────────────────────────────────────────────────────────────


def tool_call_signature_from_parsed(call: Any) -> str:
    """Build stable signature for parsed tool calls (tool + canonical args).

    Args:
        call: Parsed tool call object with tool/name and args/arguments.

    Returns:
        Stable signature string for deduplication.
    """
    tool = str(getattr(call, "tool", "") or getattr(call, "name", "") or "").strip().lower()
    raw_args = getattr(call, "args", {}) or getattr(call, "arguments", {}) or {}
    args = raw_args if isinstance(raw_args, dict) else {}
    try:
        args_token = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except (RuntimeError, ValueError):
        args_token = str(args)
    return f"{tool}::{args_token}"


def dedupe_parsed_tool_calls(calls: list[Any]) -> list[Any]:
    """Deduplicate identical parsed tool calls in the same round.

    This prevents duplicate execution when parser paths (textual/native)
    converge on the same tool payload.

    Args:
        calls: List of parsed tool calls.

    Returns:
        Deduplicated list of tool calls.
    """
    deduped: list[Any] = []
    seen: set[str] = set()
    for call in calls:
        signature = tool_call_signature_from_parsed(call)
        if signature in seen:
            logger.info(
                "[TurnEngine] Skip duplicate parsed tool call in same round: %s",
                signature,
            )
            continue
        seen.add(signature)
        deduped.append(call)
    return deduped


# ─────────────────────────────────────────────────────────────────────────────
# 输出验证
# ─────────────────────────────────────────────────────────────────────────────


def resolve_empty_visible_output_error(
    turn: AssistantTurnArtifacts,
    parsed_tool_calls: list[Any],
) -> str | None:
    """Return a deterministic error when a turn has no visible outcome.

    A turn is considered valid if it has any of:
    - Parsed tool calls (from native tool call parsing)
    - Native tool calls (even if parsing failed, indicating the model did attempt to call tools)
    - Visible content (clean_content)
    - Thinking content

    Args:
        turn: Assistant turn artifacts.
        parsed_tool_calls: Parsed tool calls from the turn.

    Returns:
        None if turn is valid, error string otherwise.
    """
    # If we have parsed tool calls, the turn is valid
    if parsed_tool_calls:
        return None
    # If we have native tool calls (even if parsing failed), the model attempted to call tools
    # This is a valid turn - the parsing failure should be handled separately
    if turn.native_tool_calls:
        return None
    # Check for visible content
    if str(turn.clean_content or "").strip():
        return None
    # Thinking-only response is valid but flagged
    if str(turn.thinking or "").strip():
        return "assistant_visible_output_empty: model returned thinking-only response"
    return "assistant_visible_output_empty: model returned no visible output or tool calls"


# ─────────────────────────────────────────────────────────────────────────────
# 流式工具事件归一化
# ─────────────────────────────────────────────────────────────────────────────


def normalize_stream_tool_call_payload(
    *,
    tool_name: str,
    tool_args: dict[str, Any] | None,
    call_id: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Normalize stream tool events into an executable native payload.

    `call_stream()` emits provider-neutral tool events after the underlying
    adapter/stream executor has already decoded raw SSE deltas. At this
    point the payload shape is the contract, and provider metadata should be
    treated only as debug context instead of a hard parser selector.

    Args:
        tool_name: Tool name from stream event.
        tool_args: Tool arguments from stream event.
        call_id: Call ID from stream event.
        metadata: Optional metadata containing native tool call info.

    Returns:
        Tuple of (normalized_payload, provider_type).
        provider_type is "openai", "anthropic", or "auto".
    """
    safe_args = dict(tool_args) if isinstance(tool_args, dict) else {}
    safe_metadata = dict(metadata) if isinstance(metadata, dict) else {}

    raw_native = safe_metadata.get("native_tool_call")
    if not isinstance(raw_native, dict):
        raw_native = safe_metadata.get("tool_call")
    candidate = dict(raw_native) if isinstance(raw_native, dict) else {}
    candidate_type = str(candidate.get("type") or "").strip().lower()

    if candidate_type == "function" and isinstance(candidate.get("function"), dict):
        return candidate, "openai"
    if candidate_type == "tool_use":
        return candidate, "anthropic"

    candidate_tool_name = str(candidate.get("tool") or candidate.get("name") or tool_name or "").strip()
    candidate_args = candidate.get("arguments")
    if not isinstance(candidate_args, dict):
        candidate_args = candidate.get("input")
    if not isinstance(candidate_args, dict):
        candidate_args = safe_args
    candidate_call_id = str(candidate.get("call_id") or candidate.get("id") or call_id or "").strip()

    if not candidate_tool_name:
        return None, "auto"

    return (
        {
            "id": candidate_call_id,
            "type": "function",
            "function": {
                "name": candidate_tool_name,
                "arguments": json.dumps(candidate_args, ensure_ascii=False),
            },
        },
        "openai",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 思考内容合并
# ─────────────────────────────────────────────────────────────────────────────


def merge_stream_thinking(
    *,
    parsed_thinking: str | None,
    streamed_thinking_parts: list[str],
) -> str | None:
    """Merge parsed `<thinking>` content with explicit reasoning chunks.

    Args:
        parsed_thinking: Thinking content parsed from response.
        streamed_thinking_parts: Thinking chunks from stream events.

    Returns:
        Merged thinking content, or None if empty.
    """
    parsed = str(parsed_thinking or "").strip()
    streamed = "".join(str(item) for item in streamed_thinking_parts if str(item or "")).strip()

    if not streamed:
        return parsed or None
    if not parsed:
        return streamed or None
    if streamed == parsed:
        return streamed
    if streamed in parsed:
        return parsed
    if parsed in streamed:
        return streamed
    return f"{streamed}\n\n{parsed}"


# ─────────────────────────────────────────────────────────────────────────────
# Transcript 追加
# ─────────────────────────────────────────────────────────────────────────────


def append_transcript_cycle(
    *,
    controller: Any,
    turn: AssistantTurnArtifacts,
    tool_results: list[dict[str, Any]],
) -> None:
    """Persist only sanitized assistant output into transcript history.

    Args:
        controller: ToolLoopController instance.
        turn: Assistant turn artifacts.
        tool_results: List of tool execution results.
    """
    controller.append_tool_cycle(
        assistant_message=turn.clean_content,
        tool_results=tool_results,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool call signature for stream deduplication
# ─────────────────────────────────────────────────────────────────────────────


def tool_call_signature(tool: str, args: dict[str, Any] | None) -> str:
    """Build stable signature for tool calls in stream context.

    Args:
        tool: Tool name.
        args: Tool arguments.

    Returns:
        Stable signature string.
    """
    tool_name = str(tool or "").strip().lower()
    safe_args = args if isinstance(args, dict) else {}
    try:
        args_token = json.dumps(safe_args, ensure_ascii=False, sort_keys=True)
    except (RuntimeError, ValueError):
        args_token = str(safe_args)
    return f"{tool_name}::{args_token}"


# ─────────────────────────────────────────────────────────────────────────────
# Visible delta computation for streaming
# ─────────────────────────────────────────────────────────────────────────────


def visible_delta(current: str | None, emitted: str) -> tuple[str, str]:
    """Return incremental visible text while keeping monotonic output.

    We only emit when the newly materialized visible text extends the
    previously emitted prefix. This prevents mid-stream parser rewrites
    from leaking duplicated/non-monotonic content.

    Args:
        current: Current visible text (from materialized turn).
        emitted: Previously emitted text.

    Returns:
        Tuple of (delta_text, new_emitted_state).
    """
    current_text = str(current or "")
    emitted_text = str(emitted or "")
    if not current_text or current_text == emitted_text:
        return "", emitted_text
    if current_text.startswith(emitted_text):
        return current_text[len(emitted_text) :], current_text
    # Non-monotonic rewrite (or shrink): hold until final flush.
    return "", emitted_text


__all__ = [
    "append_transcript_cycle",
    "dedupe_parsed_tool_calls",
    "merge_stream_thinking",
    "normalize_stream_tool_call_payload",
    "resolve_empty_visible_output_error",
    "tool_call_signature",
    "tool_call_signature_from_parsed",
    "visible_delta",
]
