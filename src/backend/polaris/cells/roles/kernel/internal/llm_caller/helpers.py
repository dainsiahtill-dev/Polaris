"""LLM Caller Helper Functions.

Provides utility functions for request preparation and message formatting.
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from typing import Any

from polaris.kernelone.constants import DIRECTOR_TIMEOUT_SECONDS

from .tool_helpers import build_native_tool_schemas, extract_native_tool_calls, resolve_tool_call_provider

logger = logging.getLogger(__name__)

# Provider native message format support configuration
_NATIVE_MESSAGE_PROVIDERS = frozenset(
    os.environ.get("POLARIS_NATIVE_MESSAGE_PROVIDERS", "anthropic,claude,openai,gpt,kimi").lower().split(",")
)

# Director timeout configuration
_DIRECTOR_ROLE_ID = "director"
_DIRECTOR_TIMEOUT_ENV = "POLARIS_DIRECTOR_LLM_TIMEOUT_SECONDS"
_DEFAULT_DIRECTOR_TIMEOUT_SECONDS: float = DIRECTOR_TIMEOUT_SECONDS


@lru_cache(maxsize=1)
def _get_cached_director_timeout() -> int:
    """Cached director timeout to avoid repeated env var reads."""
    raw = os.environ.get(_DIRECTOR_TIMEOUT_ENV, str(int(_DEFAULT_DIRECTOR_TIMEOUT_SECONDS)))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(_DEFAULT_DIRECTOR_TIMEOUT_SECONDS)
    return max(60, min(value, 900))


def resolve_timeout_seconds(profile: Any) -> int:
    """Resolve LLM call timeout based on role profile.

    Args:
        profile: Role profile with role_id

    Returns:
        Timeout seconds (60 for non-director, configurable for director)
    """
    role_id = str(getattr(profile, "role_id", "") or "").strip().lower()

    if role_id != _DIRECTOR_ROLE_ID:
        return 60

    return _get_cached_director_timeout()


def resolve_platform_retry_max(profile: Any, requested: int) -> int:
    """Resolve platform retry max based on role.

    Args:
        profile: Role profile
        requested: Requested retry count

    Returns:
        Effective retry max (0 for director, else normalized)
    """
    role_id = str(getattr(profile, "role_id", "") or "").strip().lower()

    if role_id == _DIRECTOR_ROLE_ID:
        return 0

    try:
        normalized = int(requested)
    except (TypeError, ValueError):
        normalized = 1

    return max(0, normalized)


def messages_to_input(
    messages: list[dict[str, str]],
    *,
    format_type: str = "auto",
    provider_id: str = "",
) -> str:
    """Convert message list to input string.

    Args:
        messages: Message list with role/content
        format_type: Format type ("native", "annotated", "auto")
        provider_id: Provider ID for auto mode decision

    Returns:
        Formatted input string

    Note:
        - native: Preserve message role markers, suitable for message array providers
        - annotated: Use Chinese markers, suitable for text-only providers
        - auto: Auto-select based on provider
    """
    # Auto-select format
    if format_type == "auto":
        format_type = "native" if any(pid in provider_id.lower() for pid in _NATIVE_MESSAGE_PROVIDERS) else "annotated"

    parts = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Handle structured content (from PromptChunkAssembler._apply_cache_control)
        # Format: [{"type": "text", "text": "...", "cache_control": {...}}]
        if isinstance(content, list) and len(content) > 0:
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type", "")
                    if item_type == "text":
                        text_parts.append(str(item.get("text", "")))
                    elif item_type == "image_url":
                        # Skip image content for text-only models
                        text_parts.append("<Image Omitted>")
                    else:
                        # Skip unknown content types
                        text_parts.append(f"<{item_type.capitalize()} Omitted>")
                else:
                    text_parts.append(str(item))
            content = "\n".join(text_parts)
        elif not isinstance(content, str):
            content = str(content)

        if format_type == "native":
            # Use standard XML tags, clear and semantic
            marker_map = {
                "system": "<system>",
                "user": "<user>",
                "assistant": "<assistant>",
                "tool": "<tool>",
            }
            close_map = {
                "system": "</system>",
                "user": "</user>",
                "assistant": "</assistant>",
                "tool": "</tool>",
            }
            marker = marker_map.get(role, f"<{role}>")
            close = close_map.get(role, f"</{role}>")
            parts.append(f"{marker}\n{content}\n{close}")
        else:
            # Annotated format (Chinese markers)
            role_markers = {
                "system": "【系统指令】",
                "user": "【用户】",
                "assistant": "【助手】",
                "tool": "【工具结果】",
            }
            marker = role_markers.get(role, f"【{role}】")
            parts.append(f"{marker}\n{content}")

    return "\n\n".join(parts)


def build_native_response_format(response_model: type) -> dict[str, Any] | None:
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


def extract_json_from_text(text: str) -> dict[str, Any]:
    """Extract JSON object from text response (delegate to parse_json_payload).

    Args:
        text: Response text containing JSON

    Returns:
        Parsed JSON dictionary

    Raises:
        ValueError: If no valid JSON object found (arrays are rejected for type safety)
    """
    from polaris.kernelone.utils.json_utils import parse_json_payload

    if not text or not text.strip():
        raise ValueError("Empty text")

    result = parse_json_payload(text)
    if result is None or not isinstance(result, dict):
        raise ValueError(f"No valid JSON object found in: {text[:200]}...")
    return result


def compute_context_summary(input_text: str) -> str:
    """Compute hash summary of context.

    Args:
        input_text: Input text to hash

    Returns:
        SHA256 hash prefix (16 chars)
    """
    return hashlib.sha256(input_text.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "build_native_response_format",
    "build_native_tool_schemas",
    "compute_context_summary",
    "extract_json_from_text",
    "extract_native_tool_calls",
    "messages_to_input",
    "resolve_platform_retry_max",
    "resolve_timeout_seconds",
    "resolve_tool_call_provider",
]
