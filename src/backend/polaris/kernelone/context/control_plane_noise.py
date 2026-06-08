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

# 纯框架性标记（绝不承载语义）：可安全从非信号角色内容里剥离。
# - <tool_result>...</tool_result> 仅是包裹标签 → 去标签、保留中间内容。
# - 以 [system warning]/[system reminder]/[circuit breaker]/tool result: 开头的整行
#   是控制面注入行 → 整行删除。
_MARKER_TAG_PATTERNS = (re.compile(r"</?tool_result>", re.IGNORECASE),)
_MARKER_LINE_PATTERNS = (
    re.compile(r"^\s*tool result\s*:.*$", re.IGNORECASE),
    re.compile(r"^\s*\[system (warning|reminder)\].*$", re.IGNORECASE),
    re.compile(r"^\s*\[circuit breaker\].*$", re.IGNORECASE),
)

_SIGNAL_ROLES = frozenset({"user", "assistant"})


def strip_control_plane_markers(value: Any) -> str:
    """剥离纯框架性控制面标记，保留真实内容。

    用于把工具/系统等**非信号角色**内容里泄漏的框架标记（``<tool_result>`` 包裹标签、
    ``[system warning]`` 等整行）清掉,使 ProjectionEngine 兑现"turn 级排除控制面噪声"
    的承诺。信号角色(user/assistant)的内容由调用方决定是否调用——本函数本身不区分角色。
    标签只去标签、不动中间内容;整行标记按行删除。
    """
    text = str(value or "")
    if not text:
        return text
    for pattern in _MARKER_TAG_PATTERNS:
        text = pattern.sub("", text)
    kept_lines = [line for line in text.splitlines() if not any(p.match(line) for p in _MARKER_LINE_PATTERNS)]
    return "\n".join(kept_lines)


def normalize_control_plane_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def is_control_plane_noise(value: Any) -> bool:
    text = normalize_control_plane_text(value)
    if not text:
        return False
    return any(pattern.search(text) for pattern in _CONTROL_PLANE_PATTERNS)


def is_signal_role(role: Any) -> bool:
    return str(role or "").strip().lower() in _SIGNAL_ROLES


__all__ = [
    "is_control_plane_noise",
    "is_signal_role",
    "normalize_control_plane_text",
    "strip_control_plane_markers",
]
