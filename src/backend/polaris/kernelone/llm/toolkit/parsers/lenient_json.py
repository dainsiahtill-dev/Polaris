"""Lenient JSON repair for weak-model tool-call arguments (ADR-0090).

Weak local models emit *almost*-valid JSON: trailing commas, single quotes,
raw newlines inside strings, a missing closing brace, or a code fence around
the object. Strict ``json.loads`` rejects all of these, which previously made
the whole tool call vanish. This module applies a bounded, deterministic
repair pipeline and reports whether a repair was needed.

Contract:
- strict-valid input is returned untouched (``repaired=False``);
- repairs never run mid-stream on partial fragments — callers invoke this only
  on COMPLETE buffers (end-of-stream flush, decode-time arguments);
- anything still unparseable after the pipeline returns ``None`` so existing
  fail-closed handling (decode-failure feedback, validators) stays in charge.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["parse_lenient_json_object"]

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_SMART_QUOTE_MAP = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)
_MAX_APPENDED_CLOSERS = 8
_MAX_INPUT_CHARS = 200_000


def _try_parse_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _strip_code_fence(text: str) -> str:
    token = text.strip()
    if not token.startswith("```"):
        return token
    first_newline = token.find("\n")
    if first_newline == -1:
        return token.strip("`").strip()
    token = token[first_newline + 1 :]
    stripped = token.rstrip()
    if stripped.endswith("```"):
        token = stripped[:-3]
    return token.strip()


def _escape_control_chars_in_strings(text: str) -> tuple[str, bool]:
    """Escape raw newlines/tabs inside double-quoted strings.

    Returns (fixed_text, ended_inside_string) — the latter signals an
    unterminated string literal that still needs a closing quote.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
                out.append(ch)
                continue
            if ch == "\\":
                escaped = True
                out.append(ch)
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out), in_string


def _append_missing_closers(text: str) -> str | None:
    """Append the closing brackets a truncated object is missing (bounded)."""
    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
    if not stack:
        return text
    if len(stack) > _MAX_APPENDED_CLOSERS:
        return None
    return text + "".join(reversed(stack))


def _swap_single_quoted_strings(text: str) -> str | None:
    """Swap quote style when the payload clearly uses single quotes only."""
    if "'" in text and '"' not in text:
        return text.replace("'", '"')
    return None


def _repair_candidates(text: str) -> list[str]:
    candidates: list[str] = []

    def push(candidate: str | None) -> None:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    base = text.translate(_SMART_QUOTE_MAP)
    push(base)

    swapped = _swap_single_quoted_strings(base)
    if swapped is not None:
        base = swapped
        push(base)

    fixed, unterminated = _escape_control_chars_in_strings(base)
    if unterminated:
        fixed += '"'
    push(fixed)

    no_trailing = _TRAILING_COMMA_RE.sub(r"\1", fixed)
    push(no_trailing)

    closed = _append_missing_closers(no_trailing)
    push(closed)

    return candidates


def parse_lenient_json_object(text: Any) -> tuple[dict[str, Any] | None, bool]:
    """Parse a JSON object, attempting bounded weak-model repairs on failure.

    Returns ``(obj, repaired)``: ``obj`` is None when the text remains
    unparseable after every repair stage; ``repaired`` is True only when a
    repair (not the strict parse) produced the object.
    """
    if not isinstance(text, str):
        return None, False
    token = text.strip()
    if not token or len(token) > _MAX_INPUT_CHARS:
        return None, False

    strict = _try_parse_object(token)
    if strict is not None:
        return strict, False

    fenced = _strip_code_fence(token)
    if fenced != token:
        strict = _try_parse_object(fenced)
        if strict is not None:
            return strict, True
        token = fenced

    for candidate in _repair_candidates(token):
        parsed = _try_parse_object(candidate)
        if parsed is not None:
            return parsed, True

    return None, False
