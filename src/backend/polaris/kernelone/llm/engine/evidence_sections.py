"""Verify host-declared JSON arrays against text in the captured native request."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from typing import Any


def array_digest(items: list[Any]) -> str:
    raw = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _user_text(body: Mapping[str, Any]) -> Iterator[str | None]:
    """Yield visible user text. None means part of the carrier could not be inspected."""
    responses = "input" in body
    messages = body.get("input") if responses else body.get("messages")
    if responses and isinstance(messages, str):
        yield messages
        return
    if not isinstance(messages, list):
        yield None
        return

    for item in messages:
        if not isinstance(item, dict):
            yield None
            continue
        if responses and item.get("type") not in (None, "message"):
            if item.get("type") not in ("function_call", "function_call_output", "reasoning", "item_reference"):
                yield None
            continue
        if item.get("role") != "user":
            if item.get("role") not in ("assistant", "system", "developer", "tool", "function"):
                yield None
            continue
        content = item.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    kind = block.get("type")
                    if kind in ("text", "input_text") and isinstance(block.get("text"), str):
                        yield block["text"]
                        continue
                    if kind in (
                        "tool_result",
                        "image",
                        "image_url",
                        "input_image",
                        "input_audio",
                        "document",
                        "input_file",
                    ):
                        continue
                yield None
        else:
            yield None


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("ambiguous JSON object with duplicate keys")
    return result


_DECODER = json.JSONDecoder(object_pairs_hook=_unique_keys)
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _arrays(text: str, header: str) -> Iterator[tuple[list[Any], str]]:
    cursor = 0
    while (start := text.find(header, cursor)) != -1:
        cursor = start + len(header)
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        try:
            rows, end = _DECODER.raw_decode(text, cursor)
            if isinstance(rows, list):
                digest = array_digest(rows)
                yield rows, digest
                cursor = end
        except (ValueError, RecursionError):
            # Includes non-finite numbers and lone Unicode surrogates in escaped JSON.
            continue


def user_json_arrays(body: Mapping[str, Any], header: str) -> Iterator[list[Any]]:
    if header:
        for text in _user_text(body):
            if text is not None:
                for rows, _ in _arrays(text, header):
                    yield rows


def audit_array_sections(body: Mapping[str, Any], declarations: object, *, qualified: bool) -> dict[str, Any]:
    """Present means the declared contents are visible, without attributing authorship to a user message."""
    if not isinstance(declarations, dict):
        return {}
    observations: dict[str, Any] = {}
    for key, spec in declarations.items():
        if not isinstance(key, str):
            continue
        spec = spec if isinstance(spec, dict) else {}
        count = spec.get("entries")
        count = count if type(count) is int and count >= 0 else None
        candidate = spec.get("sha256")
        digest = candidate if isinstance(candidate, str) and _SHA256.fullmatch(candidate) else None
        header = spec.get("header")
        state = "unknown"
        seen: int | None = None
        if qualified and isinstance(header, str) and header:
            if count == 0 and (candidate is None or digest == array_digest([])):
                state, seen = "not_selected", 0
            elif count is not None and count > 0 and digest is not None:
                state, seen = "absent", 0
                for text in _user_text(body):
                    if text is None:
                        state, seen = "unknown", None
                    elif any(len(rows) == count and actual == digest for rows, actual in _arrays(text, header)):
                        state, seen = "present", count
                        break
        observations[key] = {"state": state, "expected_entries": count, "sha256": digest, "wire_entries": seen}
    return observations
