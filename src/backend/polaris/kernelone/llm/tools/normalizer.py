from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.executor.runtime import (
    _normalize_json_value,
)
from polaris.kernelone.llm.toolkit.tool_normalization import (
    normalize_tool_arguments as normalize_tool_arguments_canonical,
    normalize_tool_name as normalize_tool_name_canonical,
)

from .contracts import ToolCall, ToolPolicy

if TYPE_CHECKING:
    from collections.abc import Sequence

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def normalize_tool_name(value: str) -> str:
    token = normalize_tool_name_canonical(str(value or "").strip().lower())
    if not token:
        return ""
    if not _TOOL_NAME_PATTERN.fullmatch(token):
        return ""
    return token


def normalize_tool_arguments(value: Any, tool_name: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized = normalize_tool_arguments_canonical(tool_name, value)
    if not isinstance(normalized, dict):
        return {}
    output: dict[str, Any] = {}
    for key, raw in normalized.items():
        safe_key = str(key or "").strip()
        if not safe_key:
            continue
        output[safe_key] = _normalize_json_value(raw)
    return output


def normalize_tool_calls(calls: Sequence[ToolCall]) -> list[ToolCall]:
    normalized: list[ToolCall] = []
    seen: set[tuple[str, str]] = set()
    for call in calls:
        safe_name = normalize_tool_name(call.name)
        if not safe_name:
            continue
        safe_args = normalize_tool_arguments(call.arguments, tool_name=safe_name)
        signature = (
            safe_name,
            json.dumps(_normalize_json_value(safe_args), ensure_ascii=False, sort_keys=True),
        )
        if signature in seen:
            continue
        seen.add(signature)
        normalized.append(
            ToolCall(
                id=str(call.id or "").strip(),
                name=safe_name,
                arguments=safe_args,  # Already a new dict from normalize_tool_arguments
                source=str(call.source or "").strip() or "unknown",
                raw=str(call.raw or ""),
            )
        )
    return normalized


def allowed_tool_set(policy: ToolPolicy) -> set[str]:
    return {normalize_tool_name(name) for name in policy.allowed_tool_names if normalize_tool_name(name)}


def apply_call_limit(calls: Sequence[ToolCall], policy: ToolPolicy) -> tuple[list[ToolCall], list[ToolCall]]:
    max_calls = int(policy.max_tool_calls or 0)
    if max_calls <= 0:
        return [], list(calls)
    allowed = list(calls[:max_calls])
    overflow = list(calls[max_calls:])
    return allowed, overflow
