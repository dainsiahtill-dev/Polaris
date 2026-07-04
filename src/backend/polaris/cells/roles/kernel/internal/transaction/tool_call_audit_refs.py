"""Audit-safe projections for decoded transaction tool invocations.

UTF-8 编码验证: 本文所有文本使用 UTF-8。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


def _mapping_value(source: Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _clean_string(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value or "").strip()
    return str(value or "").strip()


def _target_file_from_arguments(invocation: Any) -> str:
    arguments = _mapping_value(invocation, "arguments")
    if not isinstance(arguments, Mapping):
        return ""
    for key in ("file", "path", "filepath", "target"):
        value = _clean_string(arguments.get(key))
        if value:
            return value
    return ""


def tool_invocation_audit_ref(
    invocation: Any,
    *,
    reason: str,
    tool_name: str = "",
    execution_mode: str = "",
    target_file: str = "",
) -> dict[str, str]:
    """Return a stable, audit-safe reference for one decoded tool invocation."""

    ref = {"reason": _clean_string(reason)}
    normalized_tool_name = (
        _clean_string(tool_name)
        or _clean_string(_mapping_value(invocation, "tool_name"))
        or _clean_string(_mapping_value(invocation, "tool"))
        or _clean_string(_mapping_value(invocation, "name"))
    )
    if normalized_tool_name:
        ref["tool_name"] = normalized_tool_name

    call_id = _clean_string(_mapping_value(invocation, "call_id"))
    if call_id:
        ref["call_id"] = call_id

    normalized_execution_mode = _clean_string(execution_mode) or _clean_string(
        _mapping_value(invocation, "execution_mode")
    )
    if normalized_execution_mode:
        ref["execution_mode"] = normalized_execution_mode

    normalized_target_file = _clean_string(target_file) or _target_file_from_arguments(invocation)
    if normalized_target_file:
        ref["target_file"] = normalized_target_file

    return ref
