"""LLM Caller Helper Functions.

Provides utility functions for request preparation and message formatting.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from polaris.kernelone.constants import DIRECTOR_TIMEOUT_SECONDS
from polaris.kernelone.llm.budget_policy import (
    BUDGET_CONTEXT_KEYS_CANONICAL,
    CANONICAL_NESTED_CONTAINER_KEYS,
    OUTPUT_BUDGET_CONTEXT_KEYS,
    STRATEGY_NESTED_BUDGET_KEYS,
    TIMEOUT_CEILING_CONTEXT_KEYS,
    TIMEOUT_OVERRIDE_CONTEXT_KEYS,
    clamp_output_tokens,
)

from .request_facts import request_fact_source
from .tool_helpers import (
    build_native_tool_call_envelope_payloads,
    build_native_tool_call_envelopes,
    build_native_tool_schemas,
    extract_native_tool_calls,
    native_tool_call_envelopes_from_metadata,
    native_tool_call_name,
    resolve_tool_call_provider,
)

logger = logging.getLogger(__name__)

# Provider native message format support configuration
_NATIVE_MESSAGE_PROVIDERS = frozenset(
    os.environ.get("KERNELONE_NATIVE_MESSAGE_PROVIDERS", "anthropic,claude,openai,gpt,kimi").lower().split(",")
)

# Director timeout configuration
_DIRECTOR_ROLE_ID = "director"
_DIRECTOR_TIMEOUT_ENV = "KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS"
_DIRECTOR_TIMEOUT_MAX_ENV = "KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS"
_DEFAULT_DIRECTOR_TIMEOUT_SECONDS: float = DIRECTOR_TIMEOUT_SECONDS
_DEFAULT_DIRECTOR_TIMEOUT_MAX_SECONDS = 1800


@lru_cache(maxsize=1)
def _get_cached_director_timeout() -> int:
    """Cached director timeout to avoid repeated env var reads."""
    raw = os.environ.get(_DIRECTOR_TIMEOUT_ENV, str(int(_DEFAULT_DIRECTOR_TIMEOUT_SECONDS)))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(_DEFAULT_DIRECTOR_TIMEOUT_SECONDS)
    return max(60, min(value, _director_timeout_max_seconds()))


def _director_timeout_max_seconds() -> int:
    raw = os.environ.get(_DIRECTOR_TIMEOUT_MAX_ENV, "")
    try:
        value = int(float(str(raw).strip())) if str(raw).strip() else _DEFAULT_DIRECTOR_TIMEOUT_MAX_SECONDS
    except (TypeError, ValueError):
        value = _DEFAULT_DIRECTOR_TIMEOUT_MAX_SECONDS
    return max(900, value)


def _coerce_context_timeout_override(raw: Any) -> int | None:
    """Parse a per-request timeout override from trusted runtime context."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return max(1, min(int(value), _director_timeout_max_seconds()))


def _resolve_context_timeout_override(context_override: Any) -> int | None:
    if not isinstance(context_override, dict):
        return None
    for key in TIMEOUT_OVERRIDE_CONTEXT_KEYS:
        timeout = _coerce_context_timeout_override(context_override.get(key))
        if timeout is not None:
            return timeout
    return None


def _resolve_context_timeout_ceiling(context_override: Any) -> int | None:
    if not isinstance(context_override, dict):
        return None
    for key in TIMEOUT_CEILING_CONTEXT_KEYS:
        timeout = _coerce_context_timeout_override(context_override.get(key))
        if timeout is not None:
            return timeout
    return None


def _coerce_context_max_tokens_override(raw: Any) -> int | None:
    """Parse a per-request max-token override from trusted runtime context."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return clamp_output_tokens(value)


def _mapping_payload(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def resolve_context_output_budget_tokens(context_override: Any) -> int | None:
    """Resolve the output-token budget declared in trusted runtime context.

    THE one canonical key scan (budget_policy blueprint Phase 1):
    ``kernel/transaction_factory`` delegates here instead of keeping its
    mirrored copy. Key lists are owned by ``polaris.kernelone.llm.budget_policy``.
    """
    if not isinstance(context_override, dict):
        return None
    for key in OUTPUT_BUDGET_CONTEXT_KEYS:
        max_tokens = _coerce_context_max_tokens_override(context_override.get(key))
        if max_tokens is not None:
            return max_tokens
    for payload_key in BUDGET_CONTEXT_KEYS_CANONICAL:
        payload = _mapping_payload(context_override.get(payload_key))
        nested_containers = [_mapping_payload(payload.get(key)) for key in CANONICAL_NESTED_CONTAINER_KEYS]
        for nested_key in STRATEGY_NESTED_BUDGET_KEYS:
            max_tokens = _coerce_context_max_tokens_override(payload.get(nested_key))
            for container in nested_containers:
                if max_tokens is not None:
                    break
                max_tokens = _coerce_context_max_tokens_override(container.get(nested_key))
            if max_tokens is not None:
                return max_tokens
    return None


def _resolve_context_max_tokens_override(context_override: Any) -> int | None:
    return resolve_context_output_budget_tokens(context_override)


def resolve_timeout_seconds(profile: Any, context_override: Any | None = None) -> int:
    """Resolve LLM call timeout based on role profile.

    Args:
        profile: Role profile with role_id
        context_override: Optional trusted runtime context with per-call timeout

    Returns:
        Timeout seconds (60 for non-director, configurable for director)
    """
    role_id = str(getattr(profile, "role_id", "") or "").strip().lower()

    if role_id != _DIRECTOR_ROLE_ID:
        context_timeout = _resolve_context_timeout_override(context_override)
        if context_timeout is not None:
            return context_timeout
        return 60

    director_timeout = _get_cached_director_timeout()
    context_timeout = _resolve_context_timeout_override(context_override)
    timeout = max(director_timeout, context_timeout) if context_timeout is not None else director_timeout
    context_ceiling = _resolve_context_timeout_ceiling(context_override)
    if context_ceiling is not None:
        return max(1, min(timeout, context_ceiling))
    return timeout


def resolve_max_tokens(requested: Any, context_override: Any | None = None) -> int:
    """Resolve LLM output token budget from trusted runtime context."""

    context_max_tokens = _resolve_context_max_tokens_override(context_override)
    if context_max_tokens is not None:
        return context_max_tokens

    try:
        value = int(requested)
    except (TypeError, ValueError):
        value = 4000
    return clamp_output_tokens(value)


_TRANSACTION_KERNEL_TEMPERATURE_OVERRIDE_KEY = "_transaction_kernel_temperature_override"


@dataclass(frozen=True, slots=True)
class ResolvedRequestTemperature:
    """Effective provider temperature and its structured source."""

    value: float
    source: str

    def to_context(self) -> dict[str, Any]:
        """Project the decision into final-request audit context."""

        return {
            "schema_version": "roles.kernel.request_sampling.v1",
            "temperature": self.value,
            "temperature_source": self.source,
        }


def _coerce_context_temperature_override(raw: Any) -> float | None:
    """Parse a per-request temperature override from trusted runtime context."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return min(value, 2.0)


def resolve_temperature_with_source(
    requested: float,
    context_override: Any | None = None,
) -> ResolvedRequestTemperature:
    """Resolve sampling temperature once from structured request facts.

    ADR-0090 W2.6 (phase-aware decoding): mutation-retry escalation injects a
    deterministic low temperature through the transaction-kernel override
    channel. Explicit role-turn context/metadata follows next, then execution
    contracts. Zero is valid; negatives and malformed values fall back.
    """
    if isinstance(context_override, dict):
        override = _coerce_context_temperature_override(
            context_override.get(_TRANSACTION_KERNEL_TEMPERATURE_OVERRIDE_KEY)
        )
        if override is not None:
            return ResolvedRequestTemperature(
                value=override,
                source="transaction_kernel.retry_temperature_override",
            )
        override = _coerce_context_temperature_override(context_override.get("temperature"))
        if override is not None:
            return ResolvedRequestTemperature(
                value=override,
                source=request_fact_source(
                    context_override,
                    "temperature",
                    "role_turn.context.temperature",
                ),
            )
        request_sampling = _mapping_payload(context_override.get("request_sampling"))
        override = _coerce_context_temperature_override(request_sampling.get("temperature"))
        if override is not None:
            return ResolvedRequestTemperature(
                value=override,
                source=str(request_sampling.get("temperature_source") or "context.request_sampling.temperature"),
            )
        for payload_key in (
            "task_execution_contract",
            "director_execution_contract",
            "task_execution_strategy",
            "director_execution_strategy",
            "director_execution_profile",
            "task_execution_profile",
        ):
            payload = _mapping_payload(context_override.get(payload_key))
            sampling = _mapping_payload(payload.get("sampling"))
            override = _coerce_context_temperature_override(payload.get("temperature"))
            if override is None:
                override = _coerce_context_temperature_override(sampling.get("temperature"))
            if override is not None:
                return ResolvedRequestTemperature(
                    value=override,
                    source=f"{payload_key}.temperature",
                )
    requested_value = _coerce_context_temperature_override(requested)
    return ResolvedRequestTemperature(
        value=requested_value if requested_value is not None else 0.7,
        source="llm_invoker.argument",
    )


def resolve_temperature(requested: float, context_override: Any | None = None) -> float:
    """Compatibility value projection for the canonical temperature decision."""

    return resolve_temperature_with_source(requested, context_override).value


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
    "build_native_tool_call_envelope_payloads",
    "build_native_tool_call_envelopes",
    "build_native_tool_schemas",
    "compute_context_summary",
    "extract_json_from_text",
    "extract_native_tool_calls",
    "messages_to_input",
    "native_tool_call_envelopes_from_metadata",
    "native_tool_call_name",
    "resolve_context_output_budget_tokens",
    "resolve_max_tokens",
    "resolve_platform_retry_max",
    "resolve_timeout_seconds",
    "resolve_tool_call_provider",
]
