"""Markdown Extractor for Knowledge Pipeline.

Markdown extraction with structural awareness:
- Heading-based sections
- Code block preservation
- List and table detection
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
        ExtractionOptions,
    )
    from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
        ExtractedFragment,
    )

from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
    BaseExtractor,
)


class MarkdownExtractor(BaseExtractor):
    """Extractor for Markdown files with structural awareness.

    Preserves document structure by:
    - Splitting at heading boundaries (H1, H2, H3)
    - Keeping code blocks together
    - Detecting lists and tables as semantic units

    Supported MIME types:
    - text/markdown
    """

    SUPPORTED_MIME_TYPES = ("text/markdown",)

    # Heading patterns
    HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")
    # Code fence patterns
    CODE_FENCE_PATTERN = re.compile(r"^```")
    # Blank line pattern
    BLANK_LINE_PATTERN = re.compile(r"^\s*$")
    # List item pattern
    LIST_ITEM_PATTERN = re.compile(r"^(\s*)[-*+]\s+")
    # Numbered list pattern
    NUMBERED_LIST_PATTERN = re.compile(r"^(\s*)\d+\.\s+")
    # Table separator pattern
    TABLE_PATTERN = re.compile(r"^\|.+\|$")

    def _do_extract(
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        """Extract from Markdown with structural awareness."""
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
            ExtractedFragment,
        )

        lines = text.splitlines()
        if not lines:
            return []

        fragments: list[ExtractedFragment] = []
        current_lines: list[str] = []
        current_heading: str = ""
        line_start = 1
        in_code_block = False
        current_ul_indent = 0
        current_list_type: str | None = None

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Track code block state
            if self.CODE_FENCE_PATTERN.match(stripped):
                in_code_block = not in_code_block
                current_lines.append(line)
                continue

            # Inside code block - just accumulate
            if in_code_block:
                current_lines.append(line)
                continue

            # Blank line
            if not stripped:
                if options.preserve_blank_lines:
                    current_lines.append(line)
                continue

            # Check for heading (potential section boundary)
            heading_match = self.HEADING_PATTERN.match(stripped)
            if heading_match:
                # Emit current fragment if non-empty
                if current_lines:
                    fragment_text = self._join_lines(current_lines, options)
                    if fragment_text.strip():
                        fragments.append(
                            ExtractedFragment(
                                text=fragment_text,
                                line_start=line_start,
                                line_end=i - 1,
                                mime_type=self.SUPPORTED_MIME_TYPES[0],
                                metadata={
                                    "heading": current_heading,
                                    "fragment_type": "section",
                                },
                            )
                        )
                # Start new fragment at heading
                current_lines = [line]
                current_heading = heading_match.group(2).strip()
                line_start = i
                continue

            # Check for list continuation or new list
            list_match = self.LIST_ITEM_PATTERN.match(stripped)
            numbered_match = self.NUMBERED_LIST_PATTERN.match(stripped)

            if list_match:
                indent = len(list_match.group(1))
                list_type = "ul"

                if current_list_type == list_type and indent == current_ul_indent:
                    # Continuation of same list - accumulate
                    current_lines.append(line)
                else:
                    # New list or different indent
                    if current_lines:
                        # Emit current
                        fragment_text = self._join_lines(current_lines, options)
                        if fragment_text.strip():
                            fragments.append(
                                ExtractedFragment(
                                    text=fragment_text,
                                    line_start=line_start,
                                    line_end=i - 1,
                                    mime_type=self.SUPPORTED_MIME_TYPES[0],
                                    metadata={
                                        "heading": current_heading,
                                        "fragment_type": "section",
                                    },
                                )
                            )
                    # Start new
                    current_lines = [line]
                    current_list_type = list_type
                    current_ul_indent = indent
                    line_start = i
                continue

            if numbered_match:
                indent = len(numbered_match.group(1))
                list_type = "ol"

                if current_list_type == list_type and indent == current_ul_indent:
                    current_lines.append(line)
                else:
                    if current_lines:
                        fragment_text = self._join_lines(current_lines, options)
                        if fragment_text.strip():
                            fragments.append(
                                ExtractedFragment(
                                    text=fragment_text,
                                    line_start=line_start,
                                    line_end=i - 1,
                                    mime_type=self.SUPPORTED_MIME_TYPES[0],
                                    metadata={
                                        "heading": current_heading,
                                        "fragment_type": "section",
                                    },
                                )
                            )
                    current_lines = [line]
                    current_list_type = list_type
                    current_ul_indent = indent
                    line_start = i
                continue

            # Reset list tracking for non-list content
            if current_list_type is not None:
                current_list_type = None
                current_ul_indent = 0

            # Regular content - check if we should emit due to size
            if len(current_lines) >= options.max_fragment_lines:
                # Emit and start new
                fragment_text = self._join_lines(current_lines, options)
                if fragment_text.strip():
                    fragments.append(
                        ExtractedFragment(
                            text=fragment_text,
                            line_start=line_start,
                            line_end=i - 1,
                            mime_type=self.SUPPORTED_MIME_TYPES[0],
                            metadata={
                                "heading": current_heading,
                                "fragment_type": "section",
                            },
                        )
                    )
                current_lines = [line]
                line_start = i
            else:
                current_lines.append(line)

        # Don't forget last fragment
        if current_lines:
            fragment_text = self._join_lines(current_lines, options)
            if fragment_text.strip():
                fragments.append(
                    ExtractedFragment(
                        text=fragment_text,
                        line_start=line_start,
                        line_end=len(lines),
                        mime_type=self.SUPPORTED_MIME_TYPES[0],
                        metadata={
                            "heading": current_heading,
                            "fragment_type": "section",
                        },
                    )
                )

        return fragments

    def _join_lines(
        self,
        lines: list[str],
        options: ExtractionOptions,
    ) -> str:
        """Join lines with optional whitespace cleanup."""
        if options.strip_trailing_whitespace:
            return "\n".join(line.rstrip() for line in lines)
        return "\n".join(lines)


__all__ = ["MarkdownExtractor"]
