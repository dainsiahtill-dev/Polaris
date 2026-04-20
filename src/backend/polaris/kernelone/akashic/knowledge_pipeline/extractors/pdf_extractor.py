"""PDF Extractor for Knowledge Pipeline.

Uses pdfplumber for text extraction when available.
Provides graceful degradation if pdfplumber is not installed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
        ExtractionOptions,
    )
    from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
        DocumentInput,
        ExtractedFragment,
    )

from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
    BaseExtractor,
)

logger = logging.getLogger(__name__)

# Try to import pdfplumber; if not available, extractor will degrade gracefully
try:
    import pdfplumber

    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    pdfplumber = None  # type: ignore[assignment]


class PDFExtractor(BaseExtractor):
    """Extractor for PDF documents.

    Extracts text from PDF files using pdfplumber when available.
    Falls back to raw bytes extraction if pdfplumber is not installed.

    Supported MIME types:
    - application/pdf

    Usage::

        extractor = PDFExtractor()
        if not extractor.is_available():
            print("PDF extraction requires: pip install pdfplumber")
        fragments = await extractor.extract(DocumentInput(...))
    """

    SUPPORTED_MIME_TYPES = ("application/pdf",)

    def is_available(self) -> bool:
        """Check if PDF extraction is available (pdfplumber installed)."""
        return PDFPLUMBER_AVAILABLE

    def _do_extract(
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        """Extract from PDF using pdfplumber.

        Note: This method receives decoded text from base class, but for PDFs
        we need the raw bytes. Override extract() to handle bytes directly.
        """
        # pdfplumber needs raw PDF bytes, not decoded text
        # The base class decode handles this, but we override to use bytes
        return []

    async def extract(self, doc: DocumentInput) -> list[ExtractedFragment]:
        """Extract text fragments from a PDF document.

        Handles both raw PDF bytes and pre-decoded text fallback.
        """
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
            ExtractedFragment,
        )

        if not PDFPLUMBER_AVAILABLE:
            logger.warning("PDF extraction unavailable: pdfplumber not installed. Install with: pip install pdfplumber")
            # Fallback: try to extract any printable text from raw bytes
            return self._fallback_extract(doc)

        # Get raw bytes
        if isinstance(doc.content, str):
            # Already decoded - can't process PDF from string
            logger.warning("PDF content provided as string, not bytes")
            return []

        pdf_bytes = doc.content

        # Run blocking PDF parsing in thread pool to avoid blocking event loop
        try:
            loop = asyncio.get_running_loop()

            def _parse_pdf_bytes() -> list[tuple[int, str]]:
                """Parse PDF bytes and return list of (page_num, page_text)."""
                import io

                pages: list[tuple[int, str]] = []
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    for page_num, page in enumerate(pdf.pages, start=1):
                        page_text = page.extract_text() or ""
                        pages.append((page_num, page_text))
                return pages

            page_texts: list[tuple[int, str]] = await loop.run_in_executor(None, _parse_pdf_bytes)
        except (RuntimeError, ValueError) as exc:
            logger.warning("PDF extraction failed: %s", exc)
            return self._fallback_extract(doc)

        fragments: list[ExtractedFragment] = []
        current_lines: list[str] = []
        line_start = 1

        try:
            for page_num, page_text in page_texts:
                if not page_text.strip():
                    continue

                page_lines = page_text.splitlines()

                for i, line in enumerate(page_lines, start=line_start):
                    stripped = line.strip()

                    # Track if current_lines was empty BEFORE this iteration
                    prev_empty = not current_lines

                    if self._options.strip_trailing_whitespace:
                        line = line.rstrip()

                    if stripped or self._options.preserve_blank_lines:
                        current_lines.append(line)
                    # Blank line - emit current fragment
                    elif current_lines:
                        fragments.append(
                            ExtractedFragment(
                                text="\n".join(current_lines),
                                line_start=line_start,
                                line_end=i - 1,
                                mime_type=doc.mime_type,
                                metadata={
                                    "page": page_num,
                                    "fragment_type": "pdf_page",
                                },
                            )
                        )
                        current_lines = []
                        # Next fragment starts at this blank line
                        if prev_empty:
                            line_start = i

                    # After processing, if current_lines is now non-empty
                    # and prev_empty was True (we just started a new fragment),
                    # update line_start to current i
                    if current_lines and prev_empty:
                        line_start = i

                # Emit accumulated lines at end of page
                if current_lines:
                    end_line = line_start + len(page_lines) - 1
                    fragments.append(
                        ExtractedFragment(
                            text="\n".join(current_lines),
                            line_start=line_start,
                            line_end=end_line,
                            mime_type=doc.mime_type,
                            metadata={
                                "page": page_num,
                                "fragment_type": "pdf_page",
                            },
                        )
                    )
                    current_lines = []

                # Set line_start for next page
                line_start += len(page_lines)

            logger.debug("Extracted %d fragments from %d PDF pages", len(fragments), len(page_texts))

        except (RuntimeError, ValueError) as exc:
            logger.warning("PDF extraction failed during processing: %s", exc)
            return self._fallback_extract(doc)

        return fragments

    def _fallback_extract(self, doc: DocumentInput) -> list[ExtractedFragment]:
        """Fallback extraction when pdfplumber is unavailable.

        Attempts to extract printable text from raw PDF bytes.
        """
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
            ExtractedFragment,
        )

        if isinstance(doc.content, str):
            # Already decoded - nothing more we can do
            return [
                ExtractedFragment(
                    text=doc.content[:5000],  # Limit to first 5000 chars
                    line_start=1,
                    line_end=1,
                    mime_type=doc.mime_type,
                    metadata={"fallback": "string_decoded", "truncated": len(doc.content) > 5000},
                )
            ]

        # Try to find text in PDF bytes (very naive)
        try:
            pdf_bytes = doc.content
            # Look for text between BT (Begin Text) and ET (End Text) markers
            import re

            text_content = re.sub(rb"[\x00-\x08\x0e-\x1f]", b"", pdf_bytes)
            # Extract ASCII/UTF-8 printable sequences
            text_parts = re.findall(rb"[(](.*?)[)]", text_content)
            lines: list[str] = []
            for part in text_parts:
                try:
                    decoded = part.decode("latin-1")
                    if any(c.isalpha() for c in decoded):
                        lines.append(decoded)
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Fallback PDF text decode failed for part: %s", exc)

            if lines:
                return [
                    ExtractedFragment(
                        text="\n".join(lines[:1000]),  # Limit to first 1000 lines
                        line_start=1,
                        line_end=len(lines[:1000]),
                        mime_type=doc.mime_type,
                        metadata={
                            "fallback": "raw_bytes",
                            "fragment_type": "pdf_raw",
                        },
                    )
                ]
        except (RuntimeError, ValueError) as exc:
            logger.debug("Fallback PDF bytes extraction failed: %s", exc)

        return [
            ExtractedFragment(
                text="[PDF content could not be extracted - pdfplumber not installed]",
                line_start=1,
                line_end=1,
                mime_type=doc.mime_type,
                metadata={"error": "pdfplumber_missing"},
            )
        ]


__all__ = ["PDFExtractor"]
