"""LLM Error Handling Module.

Provides error classification, categorization, and fallback strategies.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Error category constants
ERROR_CATEGORY_TIMEOUT = "timeout"
ERROR_CATEGORY_NETWORK = "network"
ERROR_CATEGORY_RATE_LIMIT = "rate_limit"
ERROR_CATEGORY_AUTH = "auth"
ERROR_CATEGORY_PROVIDER = "provider"
ERROR_CATEGORY_VALIDATION = "validation"
ERROR_CATEGORY_CANCELLED = "cancelled"
ERROR_CATEGORY_UNKNOWN = "unknown"

# Retryable error categories
RETRYABLE_ERROR_CATEGORIES = frozenset(
    {
        ERROR_CATEGORY_TIMEOUT,
        ERROR_CATEGORY_NETWORK,
        ERROR_CATEGORY_RATE_LIMIT,
    }
)


def classify_error(error_str: str) -> str:
    """Classify LLM error into category.

    Args:
        error_str: Error message string

    Returns:
        Error category string
    """
    error_lower = str(error_str or "").lower()

    if "timeout" in error_lower or "timed out" in error_lower:
        return ERROR_CATEGORY_TIMEOUT

    if "rate limit" in error_lower or "429" in error_lower or "too many requests" in error_lower:
        return ERROR_CATEGORY_RATE_LIMIT

    if "connection" in error_lower or "network" in error_lower or "dns" in error_lower:
        return ERROR_CATEGORY_NETWORK

    if "auth" in error_lower or "api key" in error_lower or "unauthorized" in error_lower:
        return ERROR_CATEGORY_AUTH

    if "model" in error_lower or "provider" in error_lower:
        return ERROR_CATEGORY_PROVIDER

    return ERROR_CATEGORY_UNKNOWN


def is_retryable_error(error_category: str) -> bool:
    """Check if error category is retryable.

    Args:
        error_category: Error category string

    Returns:
        True if error is retryable
    """
    token = str(error_category or "").strip().lower()
    return token in RETRYABLE_ERROR_CATEGORIES


def is_native_tool_calling_unsupported(error_text: str) -> bool:
    """Check if provider rejected native tool calling.

    Args:
        error_text: Error message from provider

    Returns:
        True if error indicates native tool calling is unsupported
    """
    lowered = str(error_text or "").strip().lower()
    if not lowered:
        return False

    indicators = (
        "unsupported parameter",
        "unknown field",
        "tools is not allowed",
        "tools not allowed",
        "tool_choice",
        "function calling not supported",
        "does not support tools",
        "not support tools",
        "invalid tools",
        "unrecognized request argument supplied: tools",
        "extra inputs are not permitted",
    )

    if any(token in lowered for token in indicators):
        return True

    return bool("tools" in lowered and ("invalid_request_error" in lowered or "bad request" in lowered))


def is_response_format_unsupported(error_text: str) -> bool:
    """Check if provider rejected response_format parameter.

    Args:
        error_text: Error message from provider

    Returns:
        True if error indicates response_format is unsupported
    """
    lowered = str(error_text or "").strip().lower()
    if not lowered:
        return False

    indicators = (
        "response_format",
        "json_schema",
        "invalid response format",
        "unsupported parameter",
        "does not support json schema",
        "does not support response_format",
        "extra inputs are not permitted",
    )

    return any(token in lowered for token in indicators)


def build_native_tool_unavailable_error(profile: Any) -> str:
    """Build error message for native tool calling unavailable.

    Args:
        profile: Role profile with tool whitelist

    Returns:
        Formatted error message string
    """
    whitelist = [
        str(name).strip()
        for name in list(getattr(getattr(profile, "tool_policy", None), "whitelist", []) or [])
        if str(name).strip()
    ]
    tools_text = ", ".join(whitelist) if whitelist else "authorized_tools"
    provider_id = str(getattr(profile, "provider_id", "") or "").strip() or "unknown-provider"
    model = str(getattr(profile, "model", "") or "").strip() or "unknown-model"

    return (
        "native_tool_calling_unavailable: "
        f"provider/model does not support native tool calling "
        f"(provider={provider_id}, model={model}, tools={tools_text})"
    )


def append_runtime_fallback_instruction(base_input: str, instruction: str) -> str:
    """Append fallback instruction to input.

    Args:
        base_input: Base input text
        instruction: Fallback instruction to append

    Returns:
        Input with appended instruction
    """
    token = str(base_input or "")
    suffix = str(instruction or "").strip()

    if not suffix:
        return token

    if suffix in token:
        return token

    return f"{token}\n\n{suffix}".strip()


def build_text_response_fallback_instruction(response_model: type) -> str:
    """Build fallback instruction for structured output.

    Args:
        response_model: Pydantic model for structured response

    Returns:
        Fallback instruction string
    """
    import json

    schema_payload = _build_native_response_format(response_model)
    schema_block = ""

    if isinstance(schema_payload, dict):
        try:
            schema_block = json.dumps(
                schema_payload.get("json_schema", {}).get("schema", {}),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (RuntimeError, ValueError):
            schema_block = ""

    model_name = getattr(response_model, "__name__", "StructuredResponse")

    return (
        "【运行时结构化输出回退】\n"
        "当前 provider 不支持 response_format。请直接返回唯一一个合法 JSON 对象，不要附加解释、代码块或前后缀。\n"
        f"目标 schema 名称: {model_name}\n" + (f"JSON Schema: {schema_block}" if schema_block else "")
    ).strip()


def _build_native_response_format(response_model: type) -> dict[str, Any] | None:
    """Build OpenAI-compatible response_format payload from Pydantic model.

    Args:
        response_model: Pydantic model class

    Returns:
        OpenAI-format response_format payload or None
    """
    schema_payload: dict[str, Any] | None = None

    if hasattr(response_model, "model_json_schema"):
        try:
            schema_candidate = response_model.model_json_schema()
            if isinstance(schema_candidate, dict):
                schema_payload = schema_candidate
        except (RuntimeError, ValueError):
            schema_payload = None

    elif hasattr(response_model, "schema"):
        try:
            schema_candidate = response_model.schema()
            if isinstance(schema_candidate, dict):
                schema_payload = schema_candidate
        except (RuntimeError, ValueError):
            schema_payload = None

    if not isinstance(schema_payload, dict) or not schema_payload:
        return None

    return {
        "type": "json_schema",
        "json_schema": {
            "name": getattr(response_model, "__name__", "StructuredResponse"),
            "strict": True,
            "schema": schema_payload,
        },
    }


__all__ = [
    "ERROR_CATEGORY_AUTH",
    "ERROR_CATEGORY_CANCELLED",
    "ERROR_CATEGORY_NETWORK",
    "ERROR_CATEGORY_PROVIDER",
    "ERROR_CATEGORY_RATE_LIMIT",
    "ERROR_CATEGORY_TIMEOUT",
    "ERROR_CATEGORY_UNKNOWN",
    "ERROR_CATEGORY_VALIDATION",
    "RETRYABLE_ERROR_CATEGORIES",
    "append_runtime_fallback_instruction",
    "build_native_tool_unavailable_error",
    "build_text_response_fallback_instruction",
    "classify_error",
    "is_native_tool_calling_unsupported",
    "is_response_format_unsupported",
    "is_retryable_error",
]
