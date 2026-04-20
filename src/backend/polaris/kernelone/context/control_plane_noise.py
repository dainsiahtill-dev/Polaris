from __future__ import annotations

import re
from typing import Any

_CONTROL_PLANE_PATTERNS = (
    re.compile(r"<tool_result>", re.IGNORECASE),
    re.compile(r"</tool_result>", re.IGNORECASE),
    re.compile(r"^\s*tool result\s*:", re.IGNORECASE),
    re.compile(r"^\s*\[system (warning|reminder)\]", re.IGNORECASE),
    re.compile(r"^\s*\[circuit breaker\]", re.IGNORECASE),
)

_SIGNAL_ROLES = frozenset({"user", "assistant"})


def normalize_control_plane_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def is_control_plane_noise(value: Any) -> bool:
    text = normalize_control_plane_text(value)
    if not text:
        return False
    return any(pattern.search(text) for pattern in _CONTROL_PLANE_PATTERNS)


def is_signal_role(role: Any) -> bool:
    return str(role or "").strip().lower() in _SIGNAL_ROLES


__all__ = ["is_control_plane_noise", "is_signal_role", "normalize_control_plane_text"]
