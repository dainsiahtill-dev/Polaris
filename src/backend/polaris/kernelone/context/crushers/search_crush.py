"""Deterministic search-results crusher (T2-B).

Strategy for grep/ripgrep-style search output: deduplicate lines. Repeated or
near-identical hit lines are collapsed to their first occurrence with an
``(xN)`` repeat count, preserving order of first appearance.

No LLM, deterministic, fail-closed: a non-shrinking result is rejected by
:func:`~polaris.kernelone.context.crushers.base.finalize`.
"""

from __future__ import annotations

import re

from polaris.kernelone.context.crushers.base import CrushKind, CrushResult, finalize

# Normalize a "path:line:col:" location prefix so hits that differ only by line
# number are recognized as duplicates of the same match shape when collapsing.
_LOCATION_RE = re.compile(r"^(?P<path>[^\s:]+):\d+(?::\d+)?:")


def _normalize(line: str) -> str:
    """Normalize a search line for dedup comparison.

    Collapses internal whitespace and the numeric portion of a leading
    ``path:line:col:`` prefix so identical matches at different lines dedup.

    Args:
        line: A single search-result line.

    Returns:
        A normalized comparison key.
    """
    stripped = line.strip()
    normalized = _LOCATION_RE.sub(lambda m: f"{m.group('path')}:<L>:", stripped)
    return re.sub(r"\s+", " ", normalized)


def crush_search(text: str) -> CrushResult:
    """Crush search results by deduplicating repeated lines.

    Args:
        text: The raw search-results text.

    Returns:
        A :class:`CrushResult`. ``kind`` is NONE when the crushed form is not
        strictly smaller (e.g. no duplicates to collapse).
    """
    lines = text.split("\n")
    order: list[str] = []
    counts: dict[str, int] = {}
    representative: dict[str, str] = {}

    for line in lines:
        if not line.strip():
            continue
        key = _normalize(line)
        if key not in counts:
            counts[key] = 0
            order.append(key)
            representative[key] = line
        counts[key] += 1

    out_lines: list[str] = []
    for key in order:
        count = counts[key]
        if count > 1:
            out_lines.append(f"{representative[key]}  (x{count})")
        else:
            out_lines.append(representative[key])

    crushed_text = "\n".join(out_lines)
    return finalize(text, crushed_text, CrushKind.SEARCH)


__all__ = ["crush_search"]
