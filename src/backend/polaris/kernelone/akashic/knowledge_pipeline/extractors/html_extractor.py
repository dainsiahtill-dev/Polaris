"""HTML Extractor for Knowledge Pipeline.

Uses Python's built-in html.parser for text extraction from HTML documents.
No external dependencies required.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
    BaseExtractor,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
        ExtractionOptions,
    )
    from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
        ExtractedFragment,
    )


class _HtmlTextExtractor(HTMLParser):
    """HTML parser that extracts visible text content."""

    def __init__(self) -> None:
        super().__init__()
        self._text_parts: list[str] = []
        self._in_script = False
        self._in_style = False
        self._in_nav = False
        self._depth = 0
        # Block-level tags that should produce paragraph breaks
        self._block_tags = {
            "p",
            "div",
            "article",
            "section",
            "header",
            "footer",
            "main",
            "aside",
            "nav",
            "blockquote",
            "pre",
            "ul",
            "ol",
            "table",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        self._depth += 1
        if tag_lower in ("script", "style"):
            self._in_script = True
        elif tag_lower in ("nav",):
            self._in_nav = True

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        self._depth -= 1
        if tag_lower in ("script", "style"):
            self._in_script = False
        elif tag_lower in ("nav",):
            self._in_nav = False
        if tag_lower in self._block_tags and self._depth == 0:
            self._text_parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._in_script or self._in_style or self._in_nav:
            return
        text = data.strip()
        if text:
            self._text_parts.append(text + " ")

    def get_text(self) -> str:
        result = "".join(self._text_parts)
        # Collapse multiple spaces
        result = re.sub(r" {2,}", " ", result)
        return result.strip()


class HtmlExtractor(BaseExtractor):
    """Extractor for HTML documents.

    Uses stdlib html.parser to extract visible text content.
    Strips script/style/nav elements and normalizes whitespace.

    Supported MIME types:
    - text/html
    - application/xhtml+xml
    """

    SUPPORTED_MIME_TYPES = (
        "text/html",
        "application/xhtml+xml",
    )

    def is_available(self) -> bool:
        """HTML extraction is always available (stdlib)."""
        return True

    def _do_extract(  # type: ignore[override]
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        """Extract visible text from HTML."""
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
            ExtractedFragment,
        )

        try:
            parser = _HtmlTextExtractor()
            parser.feed(text)
            plain_text = parser.get_text()
        except (RuntimeError, ValueError) as exc:
            logger.warning("HTML parsing failed: %s", exc)
            return self._extract_lines(text, options)

        if not plain_text:
            return []

        fragments: list[ExtractedFragment] = []
        current_lines: list[str] = []
        line_start = 1

        for i, line in enumerate(plain_text.splitlines(), start=1):
            stripped = line.strip()

            if options.strip_trailing_whitespace:
                line = line.rstrip()

            if stripped or options.preserve_blank_lines:
                if not current_lines:
                    line_start = i
                current_lines.append(line)
            elif current_lines:
                fragments.append(
                    ExtractedFragment(
                        text="\n".join(current_lines),
                        line_start=line_start,
                        line_end=i - 1,
                        mime_type=self.SUPPORTED_MIME_TYPES[0],
                        metadata={"fragment_type": "html_block"},
                    )
                )
                current_lines = []

        if current_lines:
            fragments.append(
                ExtractedFragment(
                    text="\n".join(current_lines),
                    line_start=line_start,
                    line_end=len(plain_text.splitlines()),
                    mime_type=self.SUPPORTED_MIME_TYPES[0],
                    metadata={"fragment_type": "html_block"},
                )
            )

        logger.debug("Extracted %d fragments from HTML", len(fragments))
        return fragments


__all__ = ["HtmlExtractor"]
