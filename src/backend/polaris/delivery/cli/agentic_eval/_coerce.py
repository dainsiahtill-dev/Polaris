"""Leaf coercion / normalization helpers for the agentic-eval CLI.

This is the foundation module: it has NO back-imports into any other
``agentic_eval`` submodule, so it can be safely imported by every other
submodule without creating a cycle.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "_CHECK_CATEGORY_ORDER",
    "_as_dict",
    "_as_list",
    "_format_counter",
    "_normalise_case_ids",
    "_normalize_matrix_transport",
    "_normalize_tokens",
    "_to_float",
    "_to_int",
    "_to_percent",
    "_truncate_text",
]

_CHECK_CATEGORY_ORDER = {
    "safety": 0,
    "contract": 1,
    "tooling": 2,
    "evidence": 3,
}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (RuntimeError, ValueError, TypeError):
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (RuntimeError, ValueError, TypeError):
        return float(default)


def _to_percent(value: Any) -> float:
    return round(_to_float(value) * 100.0, 2)


def _normalise_case_ids(values: Iterable[Any] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_tokens(values: Iterable[Any] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = str(item or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_matrix_transport(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"stream", "non_stream"}:
        return token
    return "stream"


def _truncate_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _format_counter(counter: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, value in counter.items():
        token = str(key or "").strip() or "unknown"
        parts.append(f"{token}:{_to_int(value, 0)}")
    return ", ".join(parts)
