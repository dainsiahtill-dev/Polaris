"""Document Extractors for Knowledge Pipeline.

Provides multi-modal document extraction:
- TextExtractor: Plain text and code files
- MarkdownExtractor: Markdown with structural awareness
- PDFExtractor: PDF documents (requires pdfplumber)
- DocxExtractor: Word .docx documents (requires python-docx)

All extractors implement ExtractorPort protocol.
Use ExtractorRegistry for MIME type routing.
"""

from __future__ import annotations

from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
    BaseExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.csv_extractor import (
    CsvExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.docx_extractor import (
    DocxExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.extractor_registry import (
    ExtractorRegistry,
    get_default_registry,
    reset_default_registry,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.html_extractor import (
    HtmlExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.markdown_extractor import (
    MarkdownExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.pdf_extractor import (
    PDFExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.pptx_extractor import (
    PptxExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.text_extractor import (
    TextExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.xlsx_extractor import (
    XlsxExtractor,
)

__all__ = [
    "BaseExtractor",
    "CsvExtractor",
    "DocxExtractor",
    "ExtractorRegistry",
    "HtmlExtractor",
    "MarkdownExtractor",
    "PDFExtractor",
    "PptxExtractor",
    "TextExtractor",
    "XlsxExtractor",
    "get_default_registry",
    "reset_default_registry",
]
