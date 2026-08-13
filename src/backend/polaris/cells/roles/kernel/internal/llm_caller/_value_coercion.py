"""Generic value-coercion helpers for LLM request preparation.

Pure value normalizers (dict/list/bool/string-list coercion) shared by
``request_preparer.py`` and the resident-AGI helpers. Extracted losslessly so
both consumers import one SSoT instead of each keeping a private copy.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "_bool_option",
    "_mapping",
    "_sequence_len",
    "_string_list",
]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _sequence_len(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple, set)) else 0


def _bool_option(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    result: list[str] = []
    for item in raw_items:
        token = str(item or "").strip()
        if token and token not in result:
            result.append(token)
    return result
