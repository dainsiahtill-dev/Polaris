"""Utility functions for KernelOne tool execution."""

from __future__ import annotations

import logging
import re
import shlex
from typing import Any

from polaris.kernelone.runtime.shared_types import append_log as _append_log_impl, safe_int as _safe_int_impl

logger = logging.getLogger(__name__)


def safe_int(value: Any, default: int = -1) -> int:
    """Convert a value to int with fallback."""
    return _safe_int_impl(value, default)


def append_log(log_path: str, text: str) -> None:
    """Append text to log file with explicit UTF-8."""
    _append_log_impl(log_path, text)


def sanitize_tool_name(name: str) -> str:
    """Sanitize a tool name for use in file paths."""
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(name or "").strip())
    return cleaned or "tool"


def as_list(value: Any) -> list[Any]:
    """Convert a value to a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    return []


def split_tool_step(text: str) -> list[str]:
    """Split a tool step string into tokens."""
    if not text:
        return []
    try:
        return shlex.split(text)
    except (RuntimeError, ValueError) as exc:
        logger.debug("shlex.split failed: %s, falling back to str.split", exc)
        return text.split()


def split_list_value(value: str) -> list[str]:
    """Split a comma-separated list value into items."""
    if not value:
        return []
    cleaned = value.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    parts = []
    for part in cleaned.split(","):
        part = part.strip().strip("'\"")
        if part:
            parts.append(part)
    return parts
