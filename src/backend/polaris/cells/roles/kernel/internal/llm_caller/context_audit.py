"""Final LLM request context audit helpers."""

from __future__ import annotations

import json
from typing import Any

from .response_types import PreparedLLMRequest

_UNDERUTILIZED_WINDOW_THRESHOLD = 8192
_UNDERUTILIZED_RATIO = 0.15


def _json_chars(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(value))


def _estimate_tokens_from_chars(char_count: int) -> int:
    return max(0, int(char_count) // 4)


def _message_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            total += len(str(message))
            continue
        total += len(str(message.get("role") or ""))
        total += len(str(message.get("content") or ""))
    return total


def _request_messages(ai_request: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ctx = getattr(ai_request, "context", None)
    raw_messages: Any = None
    if isinstance(ctx, dict):
        raw_messages = ctx.get("chat_messages")
        if raw_messages is None:
            raw_messages = ctx.get("messages")
    if isinstance(raw_messages, list):
        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "")
            messages.append({"role": role, "content": content})
        if messages:
            return messages

    input_text = str(getattr(ai_request, "input", "") or "")
    if input_text.strip():
        return [{"role": "user", "content": input_text}]
    return [dict(item) for item in fallback if isinstance(item, dict)]


def _context_window_tokens(prepared: PreparedLLMRequest, profile: Any) -> int:
    raw_candidates = [
        getattr(profile, "max_context_tokens", None),
        prepared.capability_profile.get("max_context_tokens")
        if isinstance(prepared.capability_profile, dict)
        else None,
        prepared.capability_profile.get("context_window_tokens")
        if isinstance(prepared.capability_profile, dict)
        else None,
    ]
    for raw in raw_candidates:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, float) and raw > 0:
            return int(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = int(float(raw.strip()))
            except ValueError:
                continue
            if parsed > 0:
                return parsed
    return 0


def _coverage_flags(text: str) -> dict[str, bool]:
    lowered = text.lower()
    return {
        "has_pm_contract": any(
            needle in lowered
            for needle in (
                "task-",
                "acceptance",
                "depends_on",
                "任务合同",
            )
        ),
        "has_chief_engineer_blueprint": any(
            needle in lowered
            for needle in (
                "chief engineer",
                "chief_engineer",
                "blueprint",
                "scope_for_apply",
                "construction_plan",
            )
        ),
        "has_target_files": any(
            needle in lowered
            for needle in (
                "target_files",
                "scope_paths",
                "src/",
                "tests/",
            )
        ),
        "has_failure_feedback": any(
            needle in lowered
            for needle in (
                "exit_code",
                "stderr",
                "stdout",
                "failed",
                "error",
                "retry",
                "工具执行返回失败",
            )
        ),
        "has_workspace_quality_evidence": any(
            needle in lowered
            for needle in (
                "factory_workspace_quality",
                "workspace quality",
                "npm run build",
                "npm test",
                "real_run_gate",
            )
        ),
    }


def build_final_request_context_audit(
    *,
    prepared: PreparedLLMRequest,
    profile: Any,
) -> dict[str, Any]:
    return build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=profile,
    )


def build_final_request_context_audit_for_request(
    *,
    ai_request: Any,
    prepared: PreparedLLMRequest,
    profile: Any,
) -> dict[str, Any]:
    """Build stable observability for the final request sent to a model.

    ``context_result.token_estimate`` only accounts for chat messages. The
    provider request can also include native tool schemas and response_format
    contracts, so ContextOS needs this combined view to reason about actual
    context-window usage.
    """

    messages = _request_messages(ai_request, [dict(item) for item in prepared.messages if isinstance(item, dict)])
    message_chars = _message_chars(messages)
    message_token_estimate = _estimate_tokens_from_chars(message_chars)

    request_options = getattr(ai_request, "options", None)
    if isinstance(request_options, dict):
        tool_schema_payload = request_options.get("tools") if "tools" in request_options else []
        response_format_payload = request_options.get("response_format")
    else:
        prepared_options = prepared.request_options if isinstance(prepared.request_options, dict) else {}
        tool_schema_payload = prepared_options.get("tools", prepared.native_tool_schemas)
        response_format_payload = prepared_options.get("response_format", prepared.native_response_format)
    tool_schema_chars = _json_chars(tool_schema_payload)
    tool_schema_token_estimate = _estimate_tokens_from_chars(tool_schema_chars)
    tool_schema_count = len(tool_schema_payload) if isinstance(tool_schema_payload, list) else 0

    response_format_chars = _json_chars(response_format_payload)
    response_format_token_estimate = _estimate_tokens_from_chars(response_format_chars)

    final_request_token_estimate = message_token_estimate + tool_schema_token_estimate + response_format_token_estimate
    window_tokens = _context_window_tokens(prepared, profile)
    utilization = (final_request_token_estimate / window_tokens) if window_tokens > 0 else None
    message_text = "\n".join(str(message.get("content") or "") for message in messages)

    return {
        "schema_version": "llm.final_request_context_audit.v1",
        "message_count": len(messages),
        "message_chars": message_chars,
        "message_token_estimate": message_token_estimate,
        "tool_schema_count": tool_schema_count,
        "tool_schema_chars": tool_schema_chars,
        "tool_schema_token_estimate": tool_schema_token_estimate,
        "response_format_chars": response_format_chars,
        "response_format_token_estimate": response_format_token_estimate,
        "final_request_token_estimate": final_request_token_estimate,
        "context_window_tokens": window_tokens,
        "context_window_utilization": round(utilization, 4) if utilization is not None else None,
        "context_underutilized": bool(
            window_tokens >= _UNDERUTILIZED_WINDOW_THRESHOLD
            and final_request_token_estimate < int(window_tokens * _UNDERUTILIZED_RATIO)
        ),
        "coverage": _coverage_flags(message_text),
    }
