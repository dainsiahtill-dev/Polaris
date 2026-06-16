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

import ast
import re
from collections.abc import Iterable
from typing import Any

# Gemma uses this 5-character literal as its in-content quote delimiter.
_QUOTE = '<|"|>'

# Open / close markers the model wraps its textual calls in.  Close marker is
# frequently absent, so it is never required by the parser.
_LFM_OPEN_MARKER = "<|tool_call_start|>"
_LFM_CLOSE_MARKER = "<|tool_call_end|>"
_OPEN_MARKERS = ("<|tool_call|>", "<|tool_call>", "<tool_call>", "<tool_call|>", _LFM_OPEN_MARKER)
_CLOSE_MARKERS = ("<tool_call|>", "</tool_call>", "<|/tool_call|>", "<|tool_call|>", _LFM_CLOSE_MARKER)
_PYTHONIC_OPEN_MARKERS = tuple(marker for marker in _OPEN_MARKERS if marker != _LFM_OPEN_MARKER)

# A textual call opener: ``call:NAME{`` (case-insensitive on the keyword).
_CALL_OPEN_RE = re.compile(r"call\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{", re.IGNORECASE)
_IDENTIFIER_START_RE = re.compile(r"[A-Za-z_]")
_IDENTIFIER_BODY_RE = re.compile(r"[A-Za-z0-9_]")

# Qwen3-Coder / qwen-agent equals-style tool-call markup, e.g.::
#
#     <function=write_file>
#     <parameter=file>
#     README.md
#     </parameter>
#     <parameter=content>
#     ...code...
#     </parameter>
#     </function>
#
# vLLM serving qwen3-coder variants occasionally leaves this textual form in
# ``content`` (instead of converting it to native ``tool_calls``).  Distinct
# from the ``name="..."`` attribute style handled by ``xml_based.XMLToolParser``.
_QWEN3CODER_FUNCTION_OPEN_RE = re.compile(r"<function\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*>", re.IGNORECASE)
_QWEN3CODER_FUNCTION_CLOSE_RE = re.compile(r"</function\s*>", re.IGNORECASE)
_QWEN3CODER_PARAM_RE = re.compile(
    r"<parameter\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*>(.*?)</parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Counts ``<parameter=`` openers (closed or not) for the truncation guard.
_QWEN3CODER_PARAM_OPEN_RE = re.compile(r"<parameter\s*=\s*[A-Za-z_][A-Za-z0-9_]*\s*>", re.IGNORECASE)

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


def _find_matching_delimiter(text: str, open_index: int, open_char: str, close_char: str) -> int:
    """Return matching delimiter index while ignoring quoted regions."""
    depth = 1
    i = open_index + 1
    in_string = False
    string_char = ""
    length = len(text)
    while i < length:
        char = text[i]
        if in_string:
            if char == "\\" and i + 1 < length:
                i += 2
                continue
            if char == string_char:
                in_string = False
            i += 1
            continue
        if char in ("'", '"'):
            in_string = True
            string_char = char
            i += 1
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_top_level_char(text: str, target: str) -> int:
    """Return target index outside quotes and nested delimiters, or -1."""
    in_string = False
    string_char = ""
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if in_string:
            if char == "\\" and i + 1 < length:
                i += 2
                continue
            if char == string_char:
                in_string = False
            i += 1
            continue
        if char in ("'", '"'):
            in_string = True
            string_char = char
            i += 1
            continue
        if char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth > 0:
            paren_depth -= 1
        elif char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth > 0:
            bracket_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth > 0:
            brace_depth -= 1
        elif char == target and paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
            return i
        i += 1
    return -1


def _split_pythonic_args(body: str) -> list[str]:
    """Split Python-call keyword args on top-level commas."""
    parts: list[str] = []
    start = 0
    cursor = 0
    while cursor < len(body):
        comma_index = _find_top_level_char(body[cursor:], ",")
        if comma_index == -1:
            break
        absolute = cursor + comma_index
        part = body[start:absolute].strip()
        if part:
            parts.append(part)
        cursor = absolute + 1
        start = cursor
    tail = body[start:].strip()
    if tail:
        parts.append(tail)
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


def _coerce_pythonic_value(raw: str) -> Any:
    """Coerce a Python-call argument value without executing code."""
    token = raw.strip()
    if not token:
        return ""
    try:
        return ast.literal_eval(token)
    except (SyntaxError, ValueError):
        return _coerce_value(token)


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


def _parse_pythonic_body(body: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for part in _split_pythonic_args(body):
        separator_index = _find_top_level_char(part, "=")
        if separator_index <= 0:
            continue
        key = part[:separator_index].strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        args[key] = _coerce_pythonic_value(part[separator_index + 1 :])
    return args


def _iter_pythonic_calls(segment: str, base_offset: int) -> list[tuple[int, int, str, dict[str, Any]]]:
    """Yield LFM/Pythonic ``tool(arg=value)`` calls from a safe segment."""
    results: list[tuple[int, int, str, dict[str, Any]]] = []
    i = 0
    length = len(segment)
    while i < length:
        char = segment[i]
        if not _IDENTIFIER_START_RE.fullmatch(char):
            i += 1
            continue

        name_start = i
        i += 1
        while i < length and _IDENTIFIER_BODY_RE.fullmatch(segment[i]):
            i += 1
        name = segment[name_start:i]
        while i < length and segment[i].isspace():
            i += 1
        if i >= length or segment[i] != "(":
            continue

        close_index = _find_matching_delimiter(segment, i, "(", ")")
        if close_index == -1:
            break
        args = _parse_pythonic_body(segment[i + 1 : close_index])
        results.append((base_offset + name_start, base_offset + close_index + 1, name, args))
        i = close_index + 1
    return results


def _iter_lfm_segments(text: str) -> list[tuple[int, int]]:
    """Return parseable LFM segments bounded by markers or a whole bracket list."""
    segments: list[tuple[int, int]] = []
    search_from = 0
    while True:
        start = text.find(_LFM_OPEN_MARKER, search_from)
        if start == -1:
            break
        payload_start = start + len(_LFM_OPEN_MARKER)
        end = text.find(_LFM_CLOSE_MARKER, payload_start)
        payload_end = len(text) if end == -1 else end
        segments.append((payload_start, payload_end))
        search_from = payload_end + (0 if end == -1 else len(_LFM_CLOSE_MARKER))

    if segments:
        return segments

    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]") and "(" in stripped:
        leading = len(text) - len(text.lstrip())
        return [(leading, leading + len(stripped))]
    return []


def _iter_lfm_calls(text: str) -> list[tuple[int, int, str, dict[str, Any]]]:
    results: list[tuple[int, int, str, dict[str, Any]]] = []
    for start, end in _iter_lfm_segments(text):
        segment = text[start:end].strip()
        leading = len(text[start:end]) - len(text[start:end].lstrip())
        segment_offset = start + leading
        if segment.startswith("[") and segment.endswith("]"):
            results.extend(_iter_pythonic_calls(segment[1:-1], segment_offset + 1))
        else:
            results.extend(_iter_pythonic_calls(segment, segment_offset))
    return results


def _find_earliest_close_marker(text: str, start: int) -> int:
    """Return the earliest close-marker index at/after start, or -1."""
    candidates = [index for marker in _CLOSE_MARKERS if (index := text.find(marker, start)) != -1]
    return min(candidates) if candidates else -1


def _iter_marked_pythonic_segments(text: str) -> list[tuple[int, int]]:
    """Return Pythonic call segments bounded by explicit tool-call markers."""
    segments: list[tuple[int, int]] = []
    for marker in _PYTHONIC_OPEN_MARKERS:
        search_from = 0
        while True:
            start = text.find(marker, search_from)
            if start == -1:
                break
            payload_start = start + len(marker)
            close_start = _find_earliest_close_marker(text, payload_start)
            payload_end = len(text) if close_start == -1 else close_start
            if payload_end > payload_start:
                segments.append((payload_start, payload_end))
            search_from = payload_end + 1
    return sorted(set(segments))


def _iter_marked_pythonic_calls(text: str) -> list[tuple[int, int, str, dict[str, Any]]]:
    results: list[tuple[int, int, str, dict[str, Any]]] = []
    for start, end in _iter_marked_pythonic_segments(text):
        raw_segment = text[start:end]
        segment = raw_segment.strip()
        leading = len(raw_segment) - len(raw_segment.lstrip())
        segment_offset = start + leading
        if segment.startswith("[") and segment.endswith("]"):
            results.extend(_iter_pythonic_calls(segment[1:-1], segment_offset + 1))
        else:
            results.extend(_iter_pythonic_calls(segment, segment_offset))
    return results


def _iter_qwen3coder_xml_calls(text: str) -> list[tuple[int, int, str, dict[str, Any]]]:
    """Recover Qwen3-Coder equals-style XML tool calls from ``content``.

    Parses ``<function=NAME> <parameter=KEY>VALUE</parameter> ... </function>``.
    The function body runs to the closing ``</function>``, the next
    ``<function=`` opener, or end-of-text (tolerating truncated output).  Only
    calls that carry at least one closed ``<parameter>`` block are recovered; a
    call truncated mid-value yields nothing here and is left to the forced-write
    re-ask, so we never materialise half a file from a guess.
    """
    results: list[tuple[int, int, str, dict[str, Any]]] = []
    for fn_match in _QWEN3CODER_FUNCTION_OPEN_RE.finditer(text):
        tool_name = fn_match.group(1).strip()
        if not tool_name:
            continue
        body_start = fn_match.end()
        close_match = _QWEN3CODER_FUNCTION_CLOSE_RE.search(text, body_start)
        next_open = _QWEN3CODER_FUNCTION_OPEN_RE.search(text, body_start)
        boundaries = [m.start() for m in (close_match, next_open) if m is not None]
        body_end = min(boundaries) if boundaries else len(text)
        span_end = close_match.end() if close_match is not None and close_match.start() == body_end else body_end
        body = text[body_start:body_end]
        args: dict[str, Any] = {}
        for param_match in _QWEN3CODER_PARAM_RE.finditer(body):
            key = param_match.group(1).strip()
            if key:
                args[key] = param_match.group(2).strip()
        if not args:
            continue
        # Truncation guard: a ``<parameter=`` opened but never closed means the
        # output was cut off mid-value (e.g. a file body truncated at the token
        # budget).  Recovering only the closed params would materialise a write
        # with missing content — worse than a clean re-ask.  Skip the whole call.
        if len(_QWEN3CODER_PARAM_OPEN_RE.findall(body)) != len(args):
            continue
        results.append((fn_match.start(), span_end, tool_name, args))
    return results


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
    results.extend(_iter_lfm_calls(text))
    results.extend(_iter_marked_pythonic_calls(text))
    results.extend(_iter_qwen3coder_xml_calls(text))
    return sorted(results, key=lambda item: item[0])


def has_textual_tool_calls(text: str | None) -> bool:
    """Cheap pre-check: does the text look like it carries a textual call?"""
    token = str(text or "")
    if not token:
        return False
    if any(marker in token for marker in _OPEN_MARKERS):
        return True
    if _iter_lfm_segments(token):
        return True
    if _CALL_OPEN_RE.search(token) is not None:
        return True
    return _QWEN3CODER_FUNCTION_OPEN_RE.search(token) is not None


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
