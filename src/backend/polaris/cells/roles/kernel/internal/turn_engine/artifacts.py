"""TurnEngine artifacts - assistant turn parsing and sanitization.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §10 TurnEngine - Wave 2 Artifacts Extraction

职责：
    提供 assistant turn 解析和流式文本过滤的 artifacts 类型。

Wave 2 提取内容:
    - AssistantTurnArtifacts: assistant turn 结果的显式分离
    - _BracketToolWrapperFilter: 流式文本增量过滤 bracket tool wrappers
    - _BRACKET_TOOL_OPEN_RE / _BRACKET_TOOL_CLOSE_RE: 正则表达式
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Bracket tool wrapper 正则表达式
# ─────────────────────────────────────────────────────────────────────────────

_BRACKET_TOOL_OPEN_RE = re.compile(
    r"\[(tool_call|tool_calls|tool_result|tool_results)\]",
    re.IGNORECASE,
)
_BRACKET_TOOL_CLOSE_RE = re.compile(
    r"\[/\s*(tool_call|tool_calls|tool_result|tool_results)\s*\]",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# AssistantTurnArtifacts
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AssistantTurnArtifacts:
    """Explicitly separate raw parsing input from sanitized output.

    raw_content:
        The assistant content after `<thinking>` extraction. This still keeps
        textual wrapper violations such as `[TOOL_CALL]...[/TOOL_CALL]` for
        audit and sanitization. It must never be treated as an executable tool
        source.
    clean_content:
        Sanitized user/transcript-facing content. Tool wrappers are stripped and
        must never be written back into transcript history.
    thinking:
        Optional extracted thinking text.
    native_tool_calls:
        Structured tool calls emitted by the provider stream. These are the
        primary parse input for streaming paths; `raw_content` remains fallback.
    """

    raw_content: str
    clean_content: str
    thinking: str | None = None
    native_tool_calls: tuple[dict[str, Any], ...] = ()
    native_tool_provider: str = "auto"


# ─────────────────────────────────────────────────────────────────────────────
# Bracket Tool Wrapper Filter（流式文本增量过滤）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class _BracketToolWrapperFilter:
    """Incrementally strip bracket tool wrappers from visible stream text.

    This keeps streaming responsive by avoiding full-response reparsing on every
    token while still preventing textual tool wrappers from leaking into the
    user-visible content channel.
    """

    _buffer: str = ""
    _inside_wrapper: bool = False
    _tail_guard: int = 64

    def feed(self, token: str) -> str:
        chunk = str(token or "")
        if not chunk:
            return ""
        self._buffer += chunk
        visible_parts: list[str] = []

        while self._buffer:
            if self._inside_wrapper:
                close_match = _BRACKET_TOOL_CLOSE_RE.search(self._buffer)
                if close_match is not None:
                    self._buffer = self._buffer[close_match.end() :]
                    self._inside_wrapper = False
                    continue
                # No close marker yet: drop consumed wrapper body aggressively.
                # Keep only a tiny tail to detect cross-chunk closing tags.
                if "[" not in self._buffer:
                    self._buffer = ""
                else:
                    self._buffer = self._buffer[-self._tail_guard :]
                break

            # Fast path for ordinary text without wrapper markers.
            if "[" not in self._buffer:
                visible_parts.append(self._buffer)
                self._buffer = ""
                break

            open_match = _BRACKET_TOOL_OPEN_RE.search(self._buffer)
            if open_match is not None:
                if open_match.start() > 0:
                    visible_parts.append(self._buffer[: open_match.start()])
                self._buffer = self._buffer[open_match.end() :]
                self._inside_wrapper = True
                continue

            # Keep potential partial opening marker across chunk boundaries.
            last_bracket = self._buffer.rfind("[")
            if last_bracket > 0:
                visible_parts.append(self._buffer[:last_bracket])
                self._buffer = self._buffer[last_bracket:]
            break

        return "".join(visible_parts)

    def flush(self) -> str:
        if self._inside_wrapper:
            self._buffer = ""
            self._inside_wrapper = False
            return ""
        token = self._buffer
        self._buffer = ""
        return token


__all__ = [
    "_BRACKET_TOOL_CLOSE_RE",
    "_BRACKET_TOOL_OPEN_RE",
    "AssistantTurnArtifacts",
    "_BracketToolWrapperFilter",
]
