"""Deterministic log crusher (T2-B).

Strategy for build/test/runtime logs: extract a per-line "template" by
normalizing volatile tokens (numbers, hex, UUIDs, timestamps), then collapse
runs of lines that share a template into a single representative line plus a
count. Critical lines (error/fail/exception/traceback) are always preserved,
as are the first and last lines for context.

No LLM, deterministic, fail-closed: a non-shrinking result is rejected by
:func:`~polaris.kernelone.context.crushers.base.finalize`.
"""

from __future__ import annotations

import re

from polaris.kernelone.context.crushers.base import CrushKind, CrushResult, finalize

# Volatile-token patterns normalized away when deriving a line template.
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_NUM_RE = re.compile(r"\b\d+\b")

# Lines containing any of these substrings (case-insensitive) are never folded.
_CRITICAL_TOKENS: tuple[str, ...] = (
    "error",
    "fail",
    "exception",
    "traceback",
    "fatal",
    "panic",
)

_HEAD_KEEP: int = 3
_TAIL_KEEP: int = 3


def _template(line: str) -> str:
    """Derive a volatile-token-normalized template for a log line.

    Args:
        line: A single log line.

    Returns:
        The line with timestamps/UUIDs/hex/numbers replaced by placeholders.
    """
    out = _TIMESTAMP_RE.sub("<TS>", line)
    out = _UUID_RE.sub("<UUID>", out)
    out = _HEX_RE.sub("<HEX>", out)
    out = _LONG_HEX_RE.sub("<HEX>", out)
    out = _NUM_RE.sub("<N>", out)
    return out.strip()


def _is_critical(line: str) -> bool:
    """Return True when the line should never be folded away.

    Args:
        line: A single log line.

    Returns:
        True if the line contains a critical keyword.
    """
    lowered = line.lower()
    return any(token in lowered for token in _CRITICAL_TOKENS)


def crush_log(text: str) -> CrushResult:
    """Crush a log blob by collapsing repeated templated lines.

    Args:
        text: The raw log text.

    Returns:
        A :class:`CrushResult`. ``kind`` is NONE when the crushed form is not
        strictly smaller (e.g. no repetition to collapse).
    """
    lines = text.split("\n")
    n = len(lines)
    if n <= _HEAD_KEEP + _TAIL_KEEP:
        return finalize(text, text, CrushKind.LOG)

    head_idx = set(range(min(_HEAD_KEEP, n)))
    tail_idx = set(range(max(0, n - _TAIL_KEEP), n))
    pinned = head_idx | tail_idx

    out_lines: list[str] = []
    i = 0
    while i < n:
        line = lines[i]
        # Always emit pinned (head/tail) and critical lines verbatim.
        if i in pinned or _is_critical(line):
            out_lines.append(line)
            i += 1
            continue

        # Count a run of consecutive lines sharing this template, but stop at
        # pinned/critical boundaries so they stay verbatim.
        tmpl = _template(line)
        run_end = i + 1
        while (
            run_end < n
            and run_end not in pinned
            and not _is_critical(lines[run_end])
            and _template(lines[run_end]) == tmpl
        ):
            run_end += 1

        run_len = run_end - i
        if run_len > 1:
            out_lines.append(f"{line}  ... (x{run_len} similar lines collapsed)")
        else:
            out_lines.append(line)
        i = run_end

    crushed_text = "\n".join(out_lines)
    return finalize(text, crushed_text, CrushKind.LOG)


__all__ = ["crush_log"]
