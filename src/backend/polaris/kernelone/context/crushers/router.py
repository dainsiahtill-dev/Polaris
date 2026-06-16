"""Content-type detection + crush router (T2-B).

``crush_by_type`` is the single entry point: it heuristically detects the
content type (or accepts an explicit hint), dispatches to the matching
deterministic crusher, and skips crushing for inputs below a byte threshold.

All crushers are tokenizer-validated and fail-closed: any unexpected error or a
non-shrinking result yields a NONE (verbatim) :class:`CrushResult`.
"""

from __future__ import annotations

import json
import re

from polaris.kernelone.context.crushers.base import (
    MIN_CRUSH_BYTES,
    CrushKind,
    CrushResult,
    no_op,
)
from polaris.kernelone.context.crushers.diff_crush import crush_diff
from polaris.kernelone.context.crushers.json_crush import crush_json
from polaris.kernelone.context.crushers.log_crush import crush_log
from polaris.kernelone.context.crushers.search_crush import crush_search

# Heuristic detection patterns.
_DIFF_HEADER_RE = re.compile(r"^(diff --git |@@ |\+\+\+ |--- )", re.MULTILINE)
_LOG_LEVEL_RE = re.compile(r"\b(INFO|WARN|WARNING|ERROR|DEBUG|TRACE|CRITICAL|FATAL)\b")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
_SEARCH_LOCATION_RE = re.compile(r"^[^\s:]+:\d+(?::\d+)?:", re.MULTILINE)

# A type is "dominant" when a sufficient fraction of non-empty lines match.
_LINE_FRACTION_THRESHOLD: float = 0.4


def _line_fraction(lines: list[str], pattern: re.Pattern[str]) -> float:
    """Fraction of non-empty lines that match ``pattern``.

    Args:
        lines: Candidate lines.
        pattern: Compiled regex to test each line against.

    Returns:
        A fraction in ``[0.0, 1.0]``.
    """
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return 0.0
    matched = sum(1 for line in non_empty if pattern.search(line))
    return matched / len(non_empty)


def detect_content_type(text: str) -> CrushKind:
    """Heuristically classify ``text`` into a crusher kind.

    Deterministic, ordered short-circuit. Returns :attr:`CrushKind.NONE` when no
    type is confidently detected.

    Args:
        text: The raw text to classify.

    Returns:
        The detected :class:`CrushKind`.
    """
    stripped = text.lstrip()

    # 1. JSON: parses cleanly and starts like a JSON container.
    if stripped[:1] in ("{", "["):
        try:
            json.loads(text)
            return CrushKind.JSON
        except (json.JSONDecodeError, ValueError, RecursionError):
            pass

    lines = text.split("\n")

    # 2. Diff: explicit diff headers present.
    if _DIFF_HEADER_RE.search(text):
        return CrushKind.DIFF

    # 3. Log: many lines carry timestamps or level tokens.
    if _line_fraction(lines, _LOG_LEVEL_RE) >= _LINE_FRACTION_THRESHOLD:
        return CrushKind.LOG
    if _line_fraction(lines, _TIMESTAMP_RE) >= _LINE_FRACTION_THRESHOLD:
        return CrushKind.LOG

    # 4. Search results: many "path:line:" location-prefixed lines.
    if _line_fraction(lines, _SEARCH_LOCATION_RE) >= _LINE_FRACTION_THRESHOLD:
        return CrushKind.SEARCH

    return CrushKind.NONE


def crush_by_type(text: str, content_type: str | CrushKind | None = None) -> CrushResult:
    """Route ``text`` to a deterministic crusher and return the result.

    Args:
        text: The raw text to crush.
        content_type: Optional explicit type hint (a :class:`CrushKind` or its
            string value). When omitted, the type is detected heuristically.

    Returns:
        A :class:`CrushResult`. Returns a NONE (verbatim) result when the input
        is below :data:`~polaris.kernelone.context.crushers.base.MIN_CRUSH_BYTES`,
        the type is unknown, a crusher raises, or the crushed form is not
        strictly smaller.
    """
    if not text:
        return no_op(text)

    # Skip tiny inputs: the crush overhead is not worth it.
    if len(text.encode("utf-8")) < MIN_CRUSH_BYTES:
        return no_op(text)

    kind = _coerce_kind(content_type) if content_type is not None else detect_content_type(text)

    try:
        if kind is CrushKind.JSON:
            return crush_json(text)
        if kind is CrushKind.LOG:
            return crush_log(text)
        if kind is CrushKind.DIFF:
            return crush_diff(text)
        if kind is CrushKind.SEARCH:
            return crush_search(text)
    except Exception:  # noqa: BLE001 - fail-closed: never expand, never raise.
        return no_op(text)

    return no_op(text)


def _coerce_kind(content_type: str | CrushKind) -> CrushKind:
    """Coerce an explicit hint to a :class:`CrushKind`, defaulting to NONE.

    Args:
        content_type: A :class:`CrushKind` or its string value.

    Returns:
        The matching :class:`CrushKind`, or NONE when unrecognized.
    """
    if isinstance(content_type, CrushKind):
        return content_type
    try:
        return CrushKind(content_type.lower())
    except ValueError:
        return CrushKind.NONE


__all__ = ["crush_by_type", "detect_content_type"]
