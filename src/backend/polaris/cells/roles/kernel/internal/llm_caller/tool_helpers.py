"""LLM Caller Tool Helpers.

Provides tool schema building and tool call extraction utilities.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def resolve_tool_call_provider(*, provider_id: str, model: str) -> str:
    """Resolve tool call format provider hint.

    Args:
        provider_id: Provider identifier
        model: Model name

    Returns:
        Provider hint string (anthropic, openai, or auto)
    """
    token = " ".join([str(provider_id or "").strip().lower(), str(model or "").strip().lower()])

    if any(keyword in token for keyword in ("anthropic", "claude", "kimi")):
        return "anthropic"

    if any(keyword in token for keyword in ("openai", "gpt", "codex")):
        return "openai"

    return "auto"


def build_native_tool_schemas(profile: Any) -> list[dict[str, Any]]:
    """Build OpenAI-format tool schemas from profile tool whitelist.

    Args:
        profile: Role profile with tool_policy.whitelist

    Returns:
        List of OpenAI-format tool schemas
    """
    whitelist = list(getattr(getattr(profile, "tool_policy", None), "whitelist", []) or [])
    if not whitelist:
        return []

    try:
        from polaris.kernelone.llm.toolkit.definitions import create_default_registry
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name
        from polaris.kernelone.tool_execution import contracts as tool_contracts
    except (RuntimeError, ValueError):
        return []

    registry = create_default_registry()
    tool_schemas: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_name in whitelist:
        normalized_name = normalize_tool_name(raw_name)
        if not normalized_name or normalized_name in seen:
            continue

        definition = registry.get(normalized_name)
        if definition is not None:
            seen.add(normalized_name)
            tool_schemas.append(definition.to_openai_function())
            continue

        contract_schema = _build_contract_native_tool_schema(normalized_name, tool_contracts=tool_contracts)
        if contract_schema is None:
            continue

        schema_name = str(
            (contract_schema.get("function") or {}).get("name") if isinstance(contract_schema, dict) else ""
        ).strip()

        if not schema_name or schema_name in seen:
            continue

        seen.add(schema_name)
        tool_schemas.append(contract_schema)

    return tool_schemas


def _build_contract_native_tool_schema(
    tool_name: str,
    *,
    tool_contracts: Any,
) -> dict[str, Any] | None:
    """Build tool schema from tool_contracts.

    Args:
        tool_name: Canonical tool name
        tool_contracts: Tool contracts module

    Returns:
        OpenAI-format tool schema or None
    """
    canonical_name = str(
        tool_contracts.canonicalize_tool_name(tool_name, keep_unknown=False)
        if hasattr(tool_contracts, "canonicalize_tool_name")
        else ""
    ).strip()

    if not canonical_name:
        return None

    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    spec = ToolSpecRegistry.get_all_specs().get(canonical_name)
    if not isinstance(spec, dict):
        return None

    def _build_param_schema(arg_spec: Any) -> dict[str, Any]:
        token = (
            str((arg_spec or {}).get("type") if isinstance(arg_spec, dict) else "string").strip().lower() or "string"
        )
        schema: dict[str, Any] = {"type": token}
        default_value = (arg_spec or {}).get("default") if isinstance(arg_spec, dict) else None
        if default_value is not None:
            schema["default"] = default_value
        if token == "array":
            schema["items"] = {"type": "string"}
        return schema

    arguments = list(spec.get("arguments") or [])
    arg_index: dict[str, dict[str, Any]] = {}
    properties: dict[str, Any] = {}
    required: list[str] = []

    for argument in arguments:
        if not isinstance(argument, dict):
            continue
        name = str(argument.get("name") or "").strip()
        if not name:
            continue
        arg_index[name] = argument
        param_schema = _build_param_schema(argument)
        properties[name] = param_schema
        if bool(argument.get("required")):
            required.append(name)

    alias_map = spec.get("arg_aliases")
    if isinstance(alias_map, dict):
        for alias_name, canonical_arg in alias_map.items():
            alias_token = str(alias_name or "").strip()
            canonical_token = str(canonical_arg or "").strip()
            if not alias_token or not canonical_token or alias_token in properties or canonical_token not in arg_index:
                continue
            alias_schema = dict(_build_param_schema(arg_index[canonical_token]))
            alias_schema["description"] = f"Alias of `{canonical_token}` for compatibility."
            properties[alias_token] = alias_schema

    if not properties:
        properties = {}

    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required

    description = str(spec.get("description") or "").strip() or f"Tool `{canonical_name}`."

    return {
        "type": "function",
        "function": {
            "name": canonical_name,
            "description": description,
            "parameters": parameters,
        },
    }


def extract_native_tool_calls(
    raw_payload: dict[str, Any],
    *,
    provider_id: str,
    model: str,
    response_text: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Extract native tool calls from LLM response.

    Supports three extraction layers:
    1. OpenAI format: tool_calls at top level or in choices
    2. Anthropic format: content[].tool_use blocks
    3. Text format fallback: JSON tool calls in response text

    Args:
        raw_payload: Raw response payload from provider
        provider_id: Provider identifier
        model: Model name
        response_text: Optional response text for fallback parsing

    Returns:
        Tuple of (tool_calls list, provider hint string)
    """
    if not isinstance(raw_payload, dict):
        return [], "auto"

    provider_hint = resolve_tool_call_provider(provider_id=provider_id, model=model)

    openai_calls: list[dict[str, Any]] = []
    anthropic_calls: list[dict[str, Any]] = []

    # Layer 1: OpenAI format at top level
    top_level_calls = raw_payload.get("tool_calls")
    if isinstance(top_level_calls, list):
        openai_calls.extend([item for item in top_level_calls if isinstance(item, dict)])

    # Layer 1: OpenAI format in choices
    choices = raw_payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            message_calls = message.get("tool_calls")
            if isinstance(message_calls, list):
                openai_calls.extend([item for item in message_calls if isinstance(item, dict)])

    # Layer 1: OpenAI format in message
    top_level_message = raw_payload.get("message")
    if isinstance(top_level_message, dict):
        message_calls = top_level_message.get("tool_calls")
        if isinstance(message_calls, list):
            openai_calls.extend([item for item in message_calls if isinstance(item, dict)])

    # Layer 2: Anthropic format
    content_blocks = raw_payload.get("content")
    if isinstance(content_blocks, list):
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() == "tool_use":
                anthropic_calls.append(block)

    # Return native tool calls if found
    if openai_calls:
        return openai_calls, "openai"
    if anthropic_calls:
        return anthropic_calls, "anthropic"

    # Layer 3: Text format fallback
    if response_text:
        text_calls = _extract_tool_calls_from_text(response_text, provider_hint=provider_hint)
        if text_calls:
            logger.debug("[LLMCaller] Fallback: extracted %d tool calls from text", len(text_calls))
            return text_calls, "text_fallback"

    return [], provider_hint


def _extract_tool_calls_from_text(text: str, *, provider_hint: str = "auto") -> list[dict[str, Any]]:
    """Extract tool calls from plain text response.

    Args:
        text: Response text that may contain JSON tool calls
        provider_hint: Provider hint for parsing

    Returns:
        List of tool calls in OpenAI-like format
    """
    if not text or not isinstance(text, str):
        return []

    # Simple regex for JSON tool call patterns
    simple_pattern = re.compile(r'\{"[^"]*":\s*"[^"]*"[^}]*\}', re.DOTALL)
    results: list[dict[str, Any]] = []

    # Strategy 1: Parse entire text as JSON
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                tool_call = _convert_json_to_tool_call(parsed)
                if tool_call:
                    results.append(tool_call)
                    return results
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 2: Extract JSON objects that look like tool calls
    try:
        from polaris.kernelone.llm.toolkit.parsers.json_based import JSONToolParser

        parser = JSONToolParser()
        parsed_calls = parser.parse(text)
        for call in parsed_calls:
            name = str(getattr(call, "name", "") or "").strip()
            arguments = getattr(call, "arguments", {})
            if name and isinstance(arguments, dict):
                results.append(
                    {
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                )
    except (RuntimeError, ValueError):
        # Fallback to simple regex
        for match in simple_pattern.finditer(text):
            json_str = match.group(0)
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    tool_call = _convert_json_to_tool_call(parsed)
                    if tool_call:
                        results.append(tool_call)
            except (json.JSONDecodeError, TypeError):
                continue

    return results


def _convert_json_to_tool_call(data: dict[str, Any]) -> dict[str, Any] | None:
    """Convert JSON dict to OpenAI tool call format.

    Args:
        data: Parsed JSON dictionary

    Returns:
        Tool call dict or None if invalid
    """
    if not isinstance(data, dict):
        return None

    # Normalize keys to lowercase
    data_lower = {k.lower(): v for k, v in data.items()}

    # Extract tool name
    name = None
    for key in ("name", "tool", "function", "action"):
        value = data_lower.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break

    if not name:
        return None

    # Validate name format
    if not re.match(r"^[a-z][a-z0-9_]{0,63}$", name, re.IGNORECASE):
        return None

    # Extract arguments
    arguments = {}
    for key in ("arguments", "args", "params", "parameters"):
        value = data_lower.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            arguments = value
            break
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    arguments = parsed
                    break
            except (json.JSONDecodeError, TypeError):
                pass

    return {
        "id": str(uuid.uuid4()),
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments) if isinstance(arguments, dict) else "{}",
        },
    }


__all__ = [
    "build_native_tool_schemas",
    "extract_native_tool_calls",
    "resolve_tool_call_provider",
]
