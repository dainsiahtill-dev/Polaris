"""Provider-native context metrics shared by Engine and role qualification."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def canonical_json_chars(value: Any) -> int:
    """Count canonical JSON characters for one provider-visible value."""

    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError):
        return len(str(value))


def provider_native_request_metrics(
    *,
    body: Mapping[str, Any],
    native_protocol: str,
    context_window_tokens: int,
) -> dict[str, Any]:
    """Measure the exact provider-native request body.

    Semantic chat messages are not the final physical request for every
    provider. Anthropic moves the system prompt to ``body.system``; OpenAI
    Responses uses ``body.input``. This projection therefore measures the
    exact native body after provider conversion and keeps non-context request
    controls separate from messages/tools/response-format evidence.
    """

    protocol = str(native_protocol or "").strip()
    if protocol == "anthropic_messages":
        native_messages = body.get("messages")
        message_payload = {key: body[key] for key in ("system", "messages") if key in body}
    elif protocol == "openai_responses":
        native_messages = body.get("input")
        message_payload = {"input": body.get("input", [])}
    elif protocol == "openai_chat_completions":
        native_messages = body.get("messages")
        message_payload = {"messages": body.get("messages", [])}
    else:
        raise ValueError("provider_native_request_metrics_protocol_unsupported")
    if not isinstance(native_messages, list):
        raise TypeError("provider_native_request_metrics_messages_required")

    tools = body.get("tools", [])
    if tools is None:
        tools = []
    if not isinstance(tools, list):
        raise TypeError("provider_native_request_metrics_tools_invalid")
    response_format = body.get("response_format")
    excluded = {"system", "messages", "input", "tools", "response_format"}
    request_control_payload = {key: value for key, value in body.items() if key not in excluded}

    message_chars = canonical_json_chars(message_payload)
    tool_schema_chars = canonical_json_chars(tools)
    response_format_chars = 0 if response_format is None else canonical_json_chars(response_format)
    request_control_chars = canonical_json_chars(request_control_payload)
    message_tokens = message_chars // 4
    tool_tokens = tool_schema_chars // 4
    response_tokens = response_format_chars // 4
    request_control_tokens = request_control_chars // 4
    final_tokens = message_tokens + tool_tokens + response_tokens + request_control_tokens
    window = max(0, int(context_window_tokens))
    utilization = round(final_tokens / window, 4) if window > 0 else None
    return {
        "audit_scope": "provider_native_wire",
        "native_protocol": protocol,
        "message_count": len(native_messages),
        "message_chars": message_chars,
        "message_token_estimate": message_tokens,
        "tool_schema_count": len(tools),
        "tool_schema_chars": tool_schema_chars,
        "tool_schema_token_estimate": tool_tokens,
        "response_format_chars": response_format_chars,
        "response_format_token_estimate": response_tokens,
        "request_control_chars": request_control_chars,
        "request_control_token_estimate": request_control_tokens,
        "final_request_token_estimate": final_tokens,
        "context_window_tokens": window,
        "context_window_utilization": utilization,
        "context_underutilized": bool(window >= 8192 and final_tokens < int(window * 0.15)),
        "available_token_headroom": max(0, window - final_tokens),
    }
