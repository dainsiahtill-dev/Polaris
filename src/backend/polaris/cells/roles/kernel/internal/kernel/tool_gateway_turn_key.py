"""Stable turn-key resolution for role tool-gateway cache boundaries."""

from __future__ import annotations

from typing import Any


def _normalize_turn_key_part(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int):
        return str(value)
    return ""


def resolve_explicit_turn_key(turn_id: str) -> str:
    """Return the cache key for an authoritative explicit turn id."""
    normalized_turn_id = _normalize_turn_key_part(turn_id)
    if not normalized_turn_id:
        return ""
    return f"turn_id:{normalized_turn_id}"


def resolve_tool_gateway_turn_key(request_obj: Any) -> str:
    """Resolve a stable per-turn cache key for tool-gateway counters."""
    run_id = _normalize_turn_key_part(getattr(request_obj, "run_id", None))
    task_id = _normalize_turn_key_part(getattr(request_obj, "task_id", None))
    if run_id and task_id:
        return f"{run_id}:task:{task_id}"
    if run_id:
        return run_id

    turn_id = _normalize_turn_key_part(getattr(request_obj, "turn_id", None))
    if turn_id and task_id:
        return f"turn_id:{turn_id}:task:{task_id}"
    if turn_id:
        return f"turn_id:{turn_id}"
    if task_id:
        return f"task_id:{task_id}"
    return f"request_obj:{id(request_obj)}"


__all__ = [
    "resolve_explicit_turn_key",
    "resolve_tool_gateway_turn_key",
]
