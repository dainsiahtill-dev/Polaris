"""从 LLM 输出中提取 Action/Arguments/Status/Marker 块。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActionBlock:
    """解析后的 Action 块。"""

    tool_name: str | None
    arguments: dict[str, Any]
    status: str  # "In Progress" | "Completed"
    marker: str | None


# 正则：支持多行 JSON 参数
# 注意：Marker 使用 .+ 而非 .+?，确保即使行尾无换行也能捕获内容
ACTION_PATTERN = re.compile(
    r"\[Action\]:\s*(\w+)\s*\n"
    r"\[Arguments\]:\s*(\{.*?\})\s*\n"
    r"\[Status\]:\s*(In Progress|Completed)\s*\n"
    r"\[Marker\]:\s*(.+?)(?:\n|$)",
    re.MULTILINE | re.DOTALL,
)

# 备用正则：用于 Marker 行在文本末尾（无换行跟随）的情况
ACTION_PATTERN_FALLBACK = re.compile(
    r"\[Action\]:\s*(\w+)\s*\n"
    r"\[Arguments\]:\s*(\{.*?\})\s*\n"
    r"\[Status\]:\s*(In Progress|Completed)\s*\n"
    r"\[Marker\]:\s*(.+)",
    re.MULTILINE | re.DOTALL,
)


def parse_action_block(text: str) -> ActionBlock | None:
    """从文本中提取 Action 块。

    优先使用 ACTION_PATTERN（需要 Marker 后有换行或行尾），
    失败时回退到 ACTION_PATTERN_FALLBACK（Marker 在文本末尾无换行）。
    """
    match = ACTION_PATTERN.search(text)
    if not match:
        # 回退：Marker 在文本末尾（无换行跟随）的情况
        match = ACTION_PATTERN_FALLBACK.search(text)

    if not match:
        return None

    tool_name, args_json, status, marker = match.groups()

    # 处理空 marker：转换为 None
    marker = marker.strip() if marker else None
    if marker in {"None", ""}:
        marker = None

    try:
        args = json.loads(args_json)
    except json.JSONDecodeError:
        args = {}

    return ActionBlock(
        tool_name=tool_name,
        arguments=args,
        status=status,
        marker=marker,
    )


def extract_thinking_block(text: str) -> str | None:
    """从文本中提取 <thinking> 块内容。"""
    match = re.search(r"<thinking>\s*(.*?)\s*</thinking>", text, re.DOTALL)
    return match.group(1).strip() if match else None
