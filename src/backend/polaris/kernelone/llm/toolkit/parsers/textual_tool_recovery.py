"""Textual tool-call recovery for non-function-calling models.

Some ``openai_compat`` inference servers are backed by models that do NOT
support native OpenAI function-calling (e.g. Gemma served by llama.cpp without
a tool-call grammar).  Given a correct native ``tools`` request they still emit
the tool call as plain ``content`` text using the model's own syntax, e.g.::

    <|tool_call>call:repo_read_head{file:<|"|>src/utils/helpers.py<|"|>,n:50}<tool_call|>

with ``tool_calls`` left ``null``.  This module recovers such textual tool
calls into the canonical ``{"tool", "arguments", "call_id"}`` shape so the rest
of the runtime can treat them as if they were native.

This is a *provider/decoding-layer compatibility* concern, gated to trigger only
when no native tool call was produced.  It is intentionally distinct from the
deprecated role-layer "text tool protocol" (where Polaris itself instructed a
model to emit ``[TOOL_CALL]`` markers).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Gemma uses this 5-character literal as its in-content quote delimiter.
_QUOTE = '<|"|>'

# Open / close markers the model wraps its textual calls in.  Close marker is
# frequently absent, so it is never required by the parser.
_OPEN_MARKERS = ("<|tool_call|>", "<|tool_call>", "<tool_call>", "<tool_call|>")
_CLOSE_MARKERS = ("<tool_call|>", "</tool_call>", "<|/tool_call|>", "<|tool_call|>")

# A textual call opener: ``call:NAME{`` (case-insensitive on the keyword).
_CALL_OPEN_RE = re.compile(r"call\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{", re.IGNORECASE)

_INT_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+\.\d+")


def _normalize_names(names: Iterable[str] | None) -> set[str] | None:
    if names is None:
        return None
    normalized = {str(name or "").strip().lower() for name in names}
    normalized.discard("")
    return normalized


def _find_brace_end(text: str, open_index: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at ``open_index``.

    Quoted regions delimited by ``_QUOTE`` are skipped so a ``}`` inside a
    string value does not terminate the body.  Returns ``-1`` if unmatched.
    """
    i = open_index + 1
    in_quote = False
    length = len(text)
    while i < length:
        if text.startswith(_QUOTE, i):
            in_quote = not in_quote
            i += len(_QUOTE)
            continue
        char = text[i]
        if not in_quote and char == "}":
            return i
        i += 1
    return -1


def _split_top_level_commas(body: str) -> list[str]:
    """Split on commas outside quoted regions and outside ``[...]`` brackets.

    Bracket-depth tracking keeps array values like ``[<|"|>a<|"|>,<|"|>b<|"|>]``
    intact instead of splitting on the comma between array elements.
    """
    parts: list[str] = []
    current: list[str] = []
    i = 0
    in_quote = False
    depth = 0
    length = len(body)
    while i < length:
        if body.startswith(_QUOTE, i):
            in_quote = not in_quote
            current.append(_QUOTE)
            i += len(_QUOTE)
            continue
        char = body[i]
        if not in_quote:
            if char == "[":
                depth += 1
            elif char == "]":
                depth = max(0, depth - 1)
            elif char == "," and depth == 0:
                parts.append("".join(current))
                current = []
                i += 1
                continue
        current.append(char)
        i += 1
    if current:
        parts.append("".join(current))
    return parts


def _coerce_value(raw: str) -> Any:
    token = raw.strip()
    if not token:
        return ""
    if token.startswith("[") and token.endswith("]"):
        inner = token[1:-1].strip()
        if not inner:
            return []
        return [_coerce_value(element) for element in _split_top_level_commas(inner)]
    if token.startswith(_QUOTE) and token.endswith(_QUOTE) and len(token) >= 2 * len(_QUOTE):
        return token[len(_QUOTE) : -len(_QUOTE)]
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    lowered = token.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "none"):
        return None
    if _INT_RE.fullmatch(token):
        return int(token)
    if _FLOAT_RE.fullmatch(token):
        return float(token)
    return token


def _parse_body(body: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for pair in _split_top_level_commas(body):
        if ":" not in pair:
            continue
        key_raw, _, value_raw = pair.partition(":")
        key = key_raw.strip()
        if key.startswith(_QUOTE) and key.endswith(_QUOTE) and len(key) >= 2 * len(_QUOTE):
            key = key[len(_QUOTE) : -len(_QUOTE)]
        key = key.strip().strip("'\"").strip()
        if not key:
            continue
        args[key] = _coerce_value(value_raw)
    return args


def _iter_textual_calls(text: str) -> list[tuple[int, int, str, dict[str, Any]]]:
    """Yield ``(start, end, tool_name, args)`` for each textual call found.

    ``start``/``end`` bound the ``call:NAME{...}`` span (excluding wrappers).
    """
    results: list[tuple[int, int, str, dict[str, Any]]] = []
    search_from = 0
    while True:
        match = _CALL_OPEN_RE.search(text, search_from)
        if match is None:
            break
        tool_name = match.group(1).strip()
        brace_open = match.end() - 1
        brace_end = _find_brace_end(text, brace_open)
        if brace_end == -1:
            break
        body = text[brace_open + 1 : brace_end]
        args = _parse_body(body)
        results.append((match.start(), brace_end + 1, tool_name, args))
        search_from = brace_end + 1
    return results


def has_textual_tool_calls(text: str | None) -> bool:
    """Cheap pre-check: does the text look like it carries a textual call?"""
    token = str(text or "")
    if not token:
        return False
    if any(marker in token for marker in _OPEN_MARKERS):
        return True
    return _CALL_OPEN_RE.search(token) is not None


def recover_textual_tool_calls(
    text: str | None,
    allowed_tool_names: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Recover textual tool calls from model content.

    Args:
        text: Raw model content (possibly multiple textual calls).
        allowed_tool_names: When provided, only calls whose tool name is in this
            set are recovered (case-insensitive).  This is the primary guard
            against false positives / hallucinated names.

    Returns:
        List of ``{"tool", "arguments", "call_id"}`` dicts in textual order.
    """
    token = str(text or "")
    if not token:
        return []
    allowed = _normalize_names(allowed_tool_names)
    recovered: list[dict[str, Any]] = []
    for _start, _end, tool_name, args in _iter_textual_calls(token):
        if not tool_name:
            continue
        if allowed is not None and tool_name.lower() not in allowed:
            continue
        recovered.append({"tool": tool_name, "arguments": args, "call_id": ""})
    return recovered


def strip_textual_tool_call_markers(
    text: str | None,
    allowed_tool_names: Iterable[str] | None = None,
) -> str:
    """Remove recovered textual call spans and stray markers from ``text``.

    Only spans whose tool name passes ``allowed_tool_names`` are removed, so
    unrelated prose mentioning ``call:`` is preserved.
    """
    token = str(text or "")
    if not token:
        return ""
    allowed = _normalize_names(allowed_tool_names)
    spans: list[tuple[int, int]] = []
    for start, end, tool_name, _args in _iter_textual_calls(token):
        if not tool_name:
            continue
        if allowed is not None and tool_name.lower() not in allowed:
            continue
        spans.append((start, end))

    if spans:
        pieces: list[str] = []
        cursor = 0
        for start, end in spans:
            pieces.append(token[cursor:start])
            cursor = end
        pieces.append(token[cursor:])
        token = "".join(pieces)

    for marker in (*_OPEN_MARKERS, *_CLOSE_MARKERS, _QUOTE):
        token = token.replace(marker, "")
    return token.strip()


__all__ = [
    "has_textual_tool_calls",
    "recover_textual_tool_calls",
    "strip_textual_tool_call_markers",
]
