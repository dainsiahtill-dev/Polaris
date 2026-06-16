"""Deterministic diff crusher (T2-B).

Strategy for unified diffs: keep the signal (``+``/``-`` change lines and
``@@`` hunk headers) and collapse long runs of unchanged context lines (lines
starting with a single space) into a count marker. Git plumbing noise
(``diff --git``, ``index ``, ``similarity index``) is dropped.

No LLM, deterministic, fail-closed: a non-shrinking result is rejected by
:func:`~polaris.kernelone.context.crushers.base.finalize`.
"""

from __future__ import annotations

from polaris.kernelone.context.crushers.base import CrushKind, CrushResult, finalize

# Runs of context lines longer than this are collapsed.
_MAX_CONTEXT_RUN: int = 3

# Git plumbing prefixes that carry little reasoning signal.
_NOISE_PREFIXES: tuple[str, ...] = (
    "diff --git ",
    "index ",
    "similarity index ",
    "dissimilarity index ",
    "new file mode ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
)


def _is_context_line(line: str) -> bool:
    """Return True for an unchanged context line in a unified diff.

    Args:
        line: A diff line.

    Returns:
        True if the line is an unchanged context line (single leading space).
    """
    return line.startswith(" ")


def _is_noise(line: str) -> bool:
    """Return True for git plumbing noise that can be dropped.

    Args:
        line: A diff line.

    Returns:
        True if the line is droppable plumbing.
    """
    return any(line.startswith(prefix) for prefix in _NOISE_PREFIXES)


def crush_diff(text: str) -> CrushResult:
    """Crush a unified diff by trimming noise and collapsing context runs.

    Args:
        text: The raw diff text.

    Returns:
        A :class:`CrushResult`. ``kind`` is NONE when the crushed form is not
        strictly smaller.
    """
    lines = text.split("\n")
    out_lines: list[str] = []
    context_run: list[str] = []

    def _flush_context() -> None:
        """Emit any accumulated context run, collapsing long runs."""
        if not context_run:
            return
        if len(context_run) <= _MAX_CONTEXT_RUN:
            out_lines.extend(context_run)
        else:
            # Keep first context line for anchoring, collapse the rest.
            out_lines.append(context_run[0])
            out_lines.append(f" ... ({len(context_run) - 1} unchanged context lines)")
        context_run.clear()

    for line in lines:
        if _is_noise(line):
            _flush_context()
            continue
        if _is_context_line(line):
            context_run.append(line)
            continue
        # Signal line (+/-/@@/file headers): flush context, keep verbatim.
        _flush_context()
        out_lines.append(line)

    _flush_context()

    crushed_text = "\n".join(out_lines)
    return finalize(text, crushed_text, CrushKind.DIFF)


__all__ = ["crush_diff"]
