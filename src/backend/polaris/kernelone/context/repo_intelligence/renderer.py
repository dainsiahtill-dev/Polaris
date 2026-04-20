"""Lines-of-Interest (LoI) renderer.

This module provides neighborhood rendering for code files around
specific lines of interest (LoI). It generates context-rich code
snippets centered around relevant symbols.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default context lines around LoI
DEFAULT_LOI_PAD = 5

# Max line length before truncation
MAX_LINE_LENGTH = 120

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LoIEntry:
    """A single lines-of-interest entry.

    Attributes:
        rel_fname: Relative file path.
        lines: Set of line numbers (0-indexed) that are of interest.
        content: Rendered content with context.
        snippet_count: Number of LoI snippets rendered.
    """

    rel_fname: str
    lines: set[int] = field(default_factory=set)
    content: str = ""
    snippet_count: int = 0


@dataclass
class LoIRenderResult:
    """Result of LoI rendering.

    Attributes:
        entries: List of LoI entries.
        total_tokens: Estimated token count.
        truncated: Whether the result was truncated.
    """

    entries: list[LoIEntry] = field(default_factory=list)
    total_tokens: int = 0
    truncated: bool = False

    def to_text(self) -> str:
        """Render as plain text."""
        if not self.entries:
            return ""

        lines: list[str] = []
        for entry in self.entries:
            if lines:
                lines.append("")
            lines.append(f"{entry.rel_fname}:")
            if entry.content:
                lines.append(entry.content)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# LoIRenderer
# ---------------------------------------------------------------------------


class LoIRenderer:
    """Renderer for lines-of-interest (LoI) neighborhoods.

    This renderer takes file/line pairs and generates context-rich
    code snippets centered around those lines.

    Usage:
        renderer = LoIRenderer(workspace="/repo")
        renderer.add_loi("src/main.py", [10, 25, 40])

        result = renderer.render()
        for entry in result.entries:
            print(f"=== {entry.rel_fname} ===")
            print(entry.content)
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        loi_pad: int = DEFAULT_LOI_PAD,
        max_line_length: int = MAX_LINE_LENGTH,
        max_total_lines: int = 500,
    ) -> None:
        self.workspace = str(workspace)
        self._loi_pad = loi_pad
        self._max_line_length = max_line_length
        self._max_total_lines = max_total_lines

        # LoI storage: fname -> set of line numbers (0-indexed)
        self._lois: dict[str, set[int]] = {}

        # Content cache: fname -> file content
        self._content_cache: dict[str, str] = {}

    def add_loi(self, rel_fname: str, lines: Iterable[int]) -> None:
        """Add lines of interest for a file.

        Args:
            rel_fname: Relative file path.
            lines: Iterable of line numbers (0-indexed).
        """
        if rel_fname not in self._lois:
            self._lois[rel_fname] = set()
        self._lois[rel_fname].update(lines)

    def add_loi_from_tags(
        self,
        tags: Iterable[tuple[str, int]],
    ) -> None:
        """Add LoI from file/line pairs (e.g., from ranked symbols).

        Args:
            tags: Iterable of (fname, line) tuples.
        """
        for fname, line in tags:
            self.add_loi(fname, [line])

    def clear_loi(self) -> None:
        """Clear all LoI data."""
        self._lois.clear()
        self._content_cache.clear()

    def render(
        self,
        *,
        max_entries: int | None = None,
    ) -> LoIRenderResult:
        """Render LoI neighborhoods.

        Args:
            max_entries: Maximum number of file entries to render.

        Returns:
            LoIRenderResult with rendered content.
        """
        entries: list[LoIEntry] = []
        total_lines = 0
        truncated = False

        for rel_fname, loi_lines in sorted(self._lois.items()):
            if max_entries is not None and len(entries) >= max_entries:
                truncated = True
                break

            abs_path = self._get_abs_path(rel_fname)
            content = self._load_content(abs_path)

            if not content:
                continue

            # Render this file's LoI
            entry = self._render_file(rel_fname, loi_lines, content)
            if entry.content:
                entries.append(entry)
                total_lines += entry.content.count("\n") + 1

                if total_lines > self._max_total_lines:
                    truncated = True
                    break

        return LoIRenderResult(
            entries=entries,
            total_tokens=self._estimate_tokens(entries),
            truncated=truncated,
        )

    def _get_abs_path(self, rel_fname: str) -> str:
        """Get absolute path from relative."""
        try:
            return str(Path(self.workspace) / rel_fname)
        except (RuntimeError, ValueError):
            return rel_fname

    def _load_content(self, abs_path: str) -> str:
        """Load file content (with caching)."""
        if abs_path in self._content_cache:
            return self._content_cache[abs_path]

        try:
            with open(abs_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self._content_cache[abs_path] = content
            return content
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "LoIRenderer: failed to read %s: %s",
                abs_path,
                exc,
            )
            return ""

    def _render_file(
        self,
        rel_fname: str,
        loi_lines: set[int],
        content: str,
    ) -> LoIEntry:
        """Render LoI for a single file."""
        lines = content.splitlines()
        total_lines_in_file = len(lines)

        entry = LoIEntry(rel_fname=rel_fname, lines=loi_lines)
        rendered_lines: list[str] = []

        # Expand each LoI to a neighborhood
        expanded_ranges: list[tuple[int, int]] = []
        for line in sorted(loi_lines):
            start = max(0, line - self._loi_pad)
            end = min(total_lines_in_file - 1, line + self._loi_pad)
            expanded_ranges.append((start, end))

        # Merge overlapping ranges
        merged_ranges = self._merge_ranges(expanded_ranges)

        for start, end in merged_ranges:
            for i in range(start, end + 1):
                line_text = lines[i]
                # Truncate long lines
                if len(line_text) > self._max_line_length:
                    line_text = line_text[: self._max_line_length - 3] + "..."
                rendered_lines.append(f"{i + 1:4d}: {line_text}")

        entry.content = "\n".join(rendered_lines)
        entry.snippet_count = len(merged_ranges)

        return entry

    def _merge_ranges(self, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Merge overlapping ranges."""
        if not ranges:
            return []

        sorted_ranges = sorted(ranges)
        merged = [sorted_ranges[0]]

        for current in sorted_ranges[1:]:
            last = merged[-1]
            if current[0] <= last[1] + 1:
                # Overlapping or adjacent - merge
                merged[-1] = (last[0], max(last[1], current[1]))
            else:
                merged.append(current)

        return merged

    def _estimate_tokens(self, entries: list[LoIEntry]) -> int:
        """Estimate token count for entries."""
        total_chars = sum(len(e.content) for e in entries)
        # Rough estimate: ~4 chars per token
        return total_chars // 4


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def render_loi(
    workspace: str | Path,
    loi_tags: list[tuple[str, int]],
    *,
    loi_pad: int = DEFAULT_LOI_PAD,
    max_entries: int | None = None,
) -> LoIRenderResult:
    """Convenience function to render LoI from file/line pairs.

    Args:
        workspace: Workspace root path.
        loi_tags: List of (fname, line) tuples.
        loi_pad: Context lines around each LoI.
        max_entries: Maximum number of files to render.

    Returns:
        LoIRenderResult with rendered content.
    """
    renderer = LoIRenderer(workspace, loi_pad=loi_pad)
    renderer.add_loi_from_tags(loi_tags)
    return renderer.render(max_entries=max_entries)


__all__ = [
    "LoIEntry",
    "LoIRenderResult",
    "LoIRenderer",
    "render_loi",
]
