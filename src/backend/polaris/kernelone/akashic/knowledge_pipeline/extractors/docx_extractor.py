"""Word DOCX Extractor for Knowledge Pipeline.

Uses python-docx for text extraction from .docx files.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

# python-docx is a required dependency (confirmed available)
import docx
from docx.oxml.table import CT_Tbl
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
    BaseExtractor,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
        ExtractionOptions,
    )
    from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
        DocumentInput,
        ExtractedFragment,
    )


class DocxExtractor(BaseExtractor):
    """Extractor for Microsoft Word .docx files.

    Extracts text with structural awareness:
    - Paragraphs as primary content units
    - Tables detected and preserved
    - Headings identified via style names

    Supported MIME types:
    - application/vnd.openxmlformats-officedocument.wordprocessingml.document

    Usage::

        extractor = DocxExtractor()
        fragments = await extractor.extract(DocumentInput(...))
    """

    SUPPORTED_MIME_TYPES = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",  # Legacy .doc (limited support via docx fallback)
    )

    def is_available(self) -> bool:
        """Check if DOCX extraction is available."""
        return True

    async def extract(self, doc: DocumentInput) -> list[ExtractedFragment]:  # type: ignore[override]
        """Extract text fragments from a Word document.

        Handles python-docx Document objects directly.
        """
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
            ExtractedFragment,
        )

        # python-docx needs file bytes or a file path
        # We receive content as bytes
        if isinstance(doc.content, str):
            logger.warning("DOCX content provided as string, not bytes")
            return [
                ExtractedFragment(
                    text=doc.content[:5000],
                    line_start=1,
                    line_end=1,
                    mime_type=doc.mime_type,
                    metadata={"warning": "string_content_not_processed"},
                )
            ]

        try:
            import io

            # Run CPU-bound DOCX parsing in thread pool to avoid blocking event loop
            loop = asyncio.get_running_loop()

            def _parse_docx_bytes() -> DocxDocument:
                # mypy: doc.content is str | bytes; isinstance check above guarantees bytes here
                assert not isinstance(doc.content, str)
                return docx.Document(io.BytesIO(doc.content))

            docx_doc = await loop.run_in_executor(None, _parse_docx_bytes)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to parse DOCX: %s", exc)
            return [
                ExtractedFragment(
                    text=f"[DOCX parsing failed: {exc}]",
                    line_start=1,
                    line_end=1,
                    mime_type=doc.mime_type,
                    metadata={"error": str(exc)},
                )
            ]

        fragments: list[ExtractedFragment] = []
        current_lines: list[str] = []
        line_start = 1
        current_heading = ""
        current_style: str | None = None
        global_line = 1
        # Track the enumerate position of the first line in current fragment
        # Used to compute correct line_end independent of global_line increments
        fragment_start_line = 1

        def _flush_current() -> ExtractedFragment | None:
            """Flush accumulated lines as a fragment."""
            nonlocal current_lines, line_start, current_heading, current_style, fragment_start_line
            if not current_lines:
                return None
            text = "\n".join(current_lines)
            frag_end = fragment_start_line + len(current_lines) - 1
            frag = ExtractedFragment(
                text=text,
                line_start=line_start,
                line_end=frag_end,
                mime_type=doc.mime_type,
                metadata={
                    "heading": current_heading,
                    "style": current_style or "Normal",
                    "fragment_type": "docx_paragraph",
                },
            )
            # After flush, next fragment starts at current line_start position
            # (global_line has already been incremented past this fragment)
            fragment_start_line = line_start
            current_lines = []
            current_heading = ""
            current_style = None
            return frag

        def _is_heading_style(style_name: str | None) -> bool:
            """Check if style name indicates a heading."""
            if not style_name:
                return False
            name = style_name.lower()
            return "heading" in name or "title" in name or "caption" in name or "toc" in name

        # Process all block-level items (paragraphs + tables)
        for element in docx_doc.element.body:
            if isinstance(element, CT_Tbl):
                # Table - extract as structured text
                table = DocxTable(element, docx_doc)
                table_lines: list[str] = []
                for row in table.rows:
                    row_cells = [cell.text.strip() for cell in row.cells]
                    if any(row_cells):
                        table_lines.append(" | ".join(row_cells))

                if table_lines:
                    # Flush current paragraph first
                    frag = _flush_current()
                    if frag:
                        fragments.append(frag)

                    table_text = "\n".join(table_lines)
                    fragments.append(
                        ExtractedFragment(
                            text=table_text,
                            line_start=global_line,
                            line_end=global_line + len(table_lines) - 1,
                            mime_type=doc.mime_type,
                            metadata={
                                "heading": current_heading,
                                "fragment_type": "docx_table",
                            },
                        )
                    )
                    global_line += len(table_lines)
                continue

            # Paragraph
            para = Paragraph(element, docx_doc)
            para_text = para.text.strip()
            style_name = para.style.name if para.style else None

            # Track heading
            if _is_heading_style(style_name):
                # Flush current before heading
                frag = _flush_current()
                if frag:
                    fragments.append(frag)

                current_heading = para_text
                current_lines = [para_text]
                line_start = global_line
                current_style = style_name
                global_line += 1
            elif para_text:
                # Regular paragraph - flush if style changed significantly
                should_flush = (
                    current_lines
                    and current_style
                    and not _is_heading_style(current_style)
                    and style_name != current_style
                )
                if should_flush:
                    frag = _flush_current()
                    if frag:
                        fragments.append(frag)
                    # Start new fragment at current global_line before incrementing
                    line_start = global_line

                current_lines.append(para_text)
                current_style = style_name
                global_line += 1
            else:
                # Empty paragraph - flush current
                frag = _flush_current()
                if frag:
                    fragments.append(frag)
                global_line += 1

        # Don't forget last fragment
        frag = _flush_current()
        if frag:
            fragments.append(frag)

        logger.debug("Extracted %d fragments from DOCX", len(fragments))
        return fragments

    def _do_extract(  # type: ignore[override]
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        """Not used - override extract() handles bytes directly."""
        return []


__all__ = ["DocxExtractor"]
