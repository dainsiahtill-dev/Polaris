"""Text Extractor for Knowledge Pipeline.

Plain text file extraction with line-based fragmenting.
"""

from __future__ import annotations

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


class TextExtractor(BaseExtractor):
    """Extractor for plain text files.

    Performs basic text extraction with optional code-specific handling.
    Does not attempt to parse structure, just emits contiguous text blocks.

    Supported MIME types:
    - text/plain
    - text/x-python
    - text/javascript
    - text/typescript
    - application/json
    - application/xml
    """

    SUPPORTED_MIME_TYPES = (
        "text/plain",
        "text/x-python",
        "text/javascript",
        "text/typescript",
        "text/x-java",
        "text/x-c",
        "text/x-cpp",
        "text/x-csharp",
        "text/x-go",
        "text/x-rust",
        "text/x-php",
        "text/x-ruby",
        "text/x-sql",
        "text/x-sh",
        "application/json",
        "application/xml",
        "text/yaml",
    )

    def _do_extract(
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        """Extract from plain text.

        Uses line-based extraction, grouping consecutive lines into fragments
        up to max_fragment_lines.
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
            if options.strip_trailing_whitespace:
                line = line.rstrip()

            # Check if we need to start a new fragment due to size limit
            if len(current_lines) >= options.max_fragment_lines:
                # Emit current fragment
                fragments.append(
                    ExtractedFragment(
                        text="\n".join(current_lines),
                        line_start=line_start,
                        line_end=i - 1,
                        mime_type=self.SUPPORTED_MIME_TYPES[0],
                        metadata={"fragment_type": "text"},
                    )
                )
                current_lines = []
                line_start = i

            current_lines.append(line)

        # Don't forget last fragment
        if current_lines:
            fragments.append(
                ExtractedFragment(
                    text="\n".join(current_lines),
                    line_start=line_start,
                    line_end=len(lines),
                    mime_type=self.SUPPORTED_MIME_TYPES[0],
                    metadata={"fragment_type": "text"},
                )
            )

        return fragments


__all__ = ["TextExtractor"]
