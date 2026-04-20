"""CSV Extractor for Knowledge Pipeline.

Uses Python's built-in csv module for CSV file extraction.
"""

from __future__ import annotations

import csv
import io
import logging
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


class CsvExtractor(BaseExtractor):
    """Extractor for CSV (Comma-Separated Values) files.

    Extracts tabular data preserving row structure.
    Each row becomes a fragment for semantic chunking.

    Supported MIME types:
    - text/csv
    - application/csv
    - text/comma-separated-values
    """

    SUPPORTED_MIME_TYPES = (
        "text/csv",
        "application/csv",
        "text/comma-separated-values",
    )

    def is_available(self) -> bool:
        """CSV extraction is always available (stdlib)."""
        return True

    def _do_extract(  # type: ignore[override]
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        """Extract CSV rows as fragments.

        Each row is returned as a separate fragment with pipe-separated values.
        """
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
            ExtractedFragment,
        )

        fragments: list[ExtractedFragment] = []
        lines = text.splitlines()
        total_lines = len(lines)

        try:
            # Parse CSV from the text
            reader = csv.reader(io.StringIO(text))
            for row_num, row in enumerate(reader, start=1):
                if row_num == 1 and self._options.preserve_blank_lines:
                    fragments.append(
                        ExtractedFragment(
                            text=f"HEADER: {' | '.join(row)}",
                            line_start=row_num,
                            line_end=row_num,
                            mime_type=self.SUPPORTED_MIME_TYPES[0],
                            metadata={
                                "fragment_type": "csv_header",
                                "columns": len(row),
                            },
                        )
                    )
                    continue

                # Format the row as pipe-separated
                row_text = " | ".join(cell.strip() for cell in row if cell.strip())
                if row_text or self._options.preserve_blank_lines:
                    fragments.append(
                        ExtractedFragment(
                            text=row_text,
                            line_start=row_num,
                            line_end=row_num,
                            mime_type=self.SUPPORTED_MIME_TYPES[0],
                            metadata={
                                "fragment_type": "csv_row",
                                "columns": len(row),
                                "row_number": row_num,
                            },
                        )
                    )

            logger.debug("Extracted %d fragments from CSV (%d lines)", len(fragments), total_lines)

        except csv.Error as exc:
            logger.warning("CSV parsing error: %s", exc)
            # Fall back to line-based extraction
            return self._extract_lines(text, options)

        return fragments


__all__ = ["CsvExtractor"]
