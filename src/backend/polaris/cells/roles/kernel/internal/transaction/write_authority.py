"""Authoritative write classification for mutation workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS

_PATH_KEYS: tuple[str, ...] = ("file", "filepath", "path", "target", "target_file", "file_path")
_CONTROL_WRITE_BASENAMES: frozenset[str] = frozenset({"session_patch.md"})


def normalize_write_target_path(path: str | None) -> str:
    """Normalize a write target path for policy classification."""
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def extract_target_path_from_payload(payload: Any) -> str | None:
    """Recursively extract a file path token from common invocation/receipt payloads."""
    if isinstance(payload, Mapping):
        for key in _PATH_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        for nested_key in ("arguments", "result", "effect_receipt"):
            nested_path = extract_target_path_from_payload(payload.get(nested_key))
            if nested_path:
                return nested_path
    elif isinstance(payload, list):
        for item in payload:
            nested_path = extract_target_path_from_payload(item)
            if nested_path:
                return nested_path
    return None


def is_control_plane_write_path(path: str | None) -> bool:
    """Return True when the target path is a control-plane/runtime helper write."""
    normalized = normalize_write_target_path(path).lower()
    if not normalized:
        return False
    basename = normalized.rsplit("/", 1)[-1]
    if basename in _CONTROL_WRITE_BASENAMES:
        return True
    sentinel = f"/{normalized.lstrip('/')}"
    return "/.polaris/" in sentinel


def is_authoritative_write_path(path: str | None) -> bool:
    """Return True when the path should satisfy mutation materialization."""
    normalized = normalize_write_target_path(path)
    if not normalized:
        # Pathless patch tools are treated conservatively as authoritative so
        # diff-based writes are not under-counted.
        return True
    return not is_control_plane_write_path(normalized)


def _extract_tool_name(payload: Any) -> str:
    if isinstance(payload, Mapping):
        return str(payload.get("tool_name") or payload.get("tool") or "").strip()
    return str(getattr(payload, "tool_name", "") or getattr(payload, "tool", "") or "").strip()


def _extract_execution_mode(payload: Any) -> str:
    if isinstance(payload, Mapping):
        return str(payload.get("execution_mode") or "").strip()
    return str(getattr(payload, "execution_mode", "") or "").strip()


def is_authoritative_write_invocation(invocation: Any) -> bool:
    """Return True when an invocation is a write aimed at authoritative targets."""
    tool_name = _extract_tool_name(invocation)
    mode = _extract_execution_mode(invocation)
    if tool_name not in WRITE_TOOLS and mode != "write_serial":
        return False
    target_path = extract_target_path_from_payload(
        invocation.get("arguments") if isinstance(invocation, Mapping) else getattr(invocation, "arguments", None)
    )
    return is_authoritative_write_path(target_path)


def is_authoritative_write_result(result_item: Any) -> bool:
    """Return True when a tool execution result represents authoritative materialization."""
    if not isinstance(result_item, Mapping):
        return False
    tool_name = _extract_tool_name(result_item)
    if tool_name not in WRITE_TOOLS:
        return False
    target_path = extract_target_path_from_payload(result_item)
    return is_authoritative_write_path(target_path)
