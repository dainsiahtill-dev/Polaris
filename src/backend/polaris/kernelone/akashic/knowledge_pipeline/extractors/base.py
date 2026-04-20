"""Base Extractor for Knowledge Pipeline.

Provides the base class for document extraction following the ExtractorPort protocol.
All extractors should inherit from BaseExtractor and implement _do_extract().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
        DocumentInput,
        ExtractedFragment,
    )


@dataclass
class ExtractionOptions:
    """Options for extraction behavior."""

    max_fragment_lines: int = 500  # Max lines per fragment
    preserve_blank_lines: bool = True  # Keep blank lines as structure markers
    strip_trailing_whitespace: bool = True  # Clean up lines


class BaseExtractor:
    """Base class for document extractors.

    Provides common functionality for extracting text from documents.
    Subclasses must implement _do_extract() for specific formats.

    Usage::

        class MyExtractor(BaseExtractor):
            def _do_extract(self, text: str, options: ExtractionOptions) -> list[ExtractedFragment]:
                # Custom extraction logic
                return fragments

        extractor = MyExtractor()
        fragments = await extractor.extract(DocumentInput(...))
    """

    # Override in subclass for specific MIME types
    SUPPORTED_MIME_TYPES: tuple[str, ...] = ("text/plain",)

    def __init__(
        self,
        *,
        options: ExtractionOptions | None = None,
    ) -> None:
        self._options = options or ExtractionOptions()

    async def extract(
        self,
        doc: DocumentInput,
    ) -> list[ExtractedFragment]:
        """Extract text fragments from a document.

        Args:
            doc: DocumentInput with source, mime_type, and content

        Returns:
            List of ExtractedFragment in reading order
        """
        # Decode content if bytes
        text = self._decode_content(doc)

        # Delegate to subclass implementation
        return self._do_extract(text, self._options)

    def _decode_content(self, doc: DocumentInput) -> str:
        """Decode content bytes to string if necessary."""
        if isinstance(doc.content, str):
            return doc.content

        # Try UTF-8 first
        try:
            return doc.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            logger.warning("UTF-8 decode failed for %s: %s", doc.source, exc)

        # Fall back to UTF-8 with replacement
        return doc.content.decode("utf-8", errors="replace")

    def _do_extract(
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        """Extract fragments from decoded text.

        Override in subclass for custom extraction logic.

        Args:
            text: Decoded text content
            options: Extraction options

        Returns:
            List of ExtractedFragment
        """
        # Default implementation: simple line-based extraction
        return self._extract_lines(text, options)

    def _extract_lines(
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        """Simple line-based extraction for plain text.

        Groups consecutive non-blank lines into fragments.
        """
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
            ExtractedFragment,
        )

        lines = text.splitlines()
        if not lines:
            return []

        fragments: list[ExtractedFragment] = []
        current_lines: list[str] = []
        line_start = 1

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()

            if options.strip_trailing_whitespace:
                line = line.rstrip()

            if stripped or options.preserve_blank_lines:
                if not current_lines:
                    line_start = i
                current_lines.append(line)
            # Blank line - emit current fragment if non-empty
            elif current_lines:
                fragments.append(
                    ExtractedFragment(
                        text="\n".join(current_lines),
                        line_start=line_start,
                        line_end=i - 1,
                        mime_type=self.SUPPORTED_MIME_TYPES[0],
                        metadata={},
                    )
                )
                current_lines = []

        # Don't forget last fragment
        if current_lines:
            fragments.append(
                ExtractedFragment(
                    text="\n".join(current_lines),
                    line_start=line_start,
                    line_end=len(lines),
                    mime_type=self.SUPPORTED_MIME_TYPES[0],
                    metadata={},
                )
            )

        return fragments

    def can_extract(self, mime_type: str) -> bool:
        """Check if this extractor supports the given MIME type."""
        return mime_type in self.SUPPORTED_MIME_TYPES


__all__ = ["BaseExtractor", "ExtractionOptions"]
