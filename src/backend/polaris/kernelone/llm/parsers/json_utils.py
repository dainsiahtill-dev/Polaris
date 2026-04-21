"""JSON parsing utilities for LLM responses.

Provides common functions for extracting and parsing JSON from LLM output.
"""

from __future__ import annotations

import re


def _balanced_json_fragments(text: str) -> list[str]:
    """Extract balanced JSON fragments from text.

    Finds all substrings that represent complete JSON objects (balanced braces).

    Args:
        text: Input text to search for JSON fragments.

    Returns:
        List of JSON object strings, sorted by length (longest first).
    """
    fragments: list[str] = []
    start = -1
    depth = 0
    in_string = False
    escape = False

    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
            continue

        if ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                fragments.append(text[start : idx + 1])
                start = -1

    fragments.sort(key=len, reverse=True)
    return fragments


def iter_json_candidates(text: str) -> list[str]:
    """Iterate possible JSON candidates from LLM output.

    Extracts JSON from:
    1. The full text itself
    2. JSON code blocks (```json ... ```)
    3. Balanced JSON fragments

    Args:
        text: Raw LLM output text.

    Returns:
        Deduplicated list of potential JSON strings.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(value: str) -> None:
        candidate = str(value or "").strip()
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    _append(text)

    for match in re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE):
        _append(match.group(1))

    for fragment in _balanced_json_fragments(text):
        _append(fragment)

    return candidates
