"""Tests for Document Extractors."""

from __future__ import annotations

import pytest
from polaris.kernelone.akashic.knowledge_pipeline.extractors import (
    BaseExtractor,
    MarkdownExtractor,
    TextExtractor,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
    ExtractionOptions,
)
from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
    DocumentInput,
)


class TestTextExtractor:
    """Tests for TextExtractor."""

    @pytest.fixture
    def extractor(self):
        """Create a text extractor for testing."""
        return TextExtractor()

    @pytest.mark.asyncio
    async def test_plain_text(self, extractor) -> None:
        """Extract plain text."""
        doc = DocumentInput(
            source="test.txt",
            mime_type="text/plain",
            content="Hello world\nThis is a test.",
        )

        fragments = await extractor.extract(doc)

        assert len(fragments) >= 1
        assert fragments[0].text == "Hello world\nThis is a test."

    @pytest.mark.asyncio
    async def test_python_code(self, extractor) -> None:
        """Extract Python code."""
        doc = DocumentInput(
            source="test.py",
            mime_type="text/x-python",
            content="def hello():\n    print('Hello')\n",
        )

        fragments = await extractor.extract(doc)

        assert len(fragments) == 1
        assert "def hello" in fragments[0].text

    @pytest.mark.asyncio
    async def test_json_content(self, extractor) -> None:
        """Extract JSON content."""
        doc = DocumentInput(
            source="test.json",
            mime_type="application/json",
            content='{"key": "value"}',
        )

        fragments = await extractor.extract(doc)

        assert len(fragments) == 1
        assert "key" in fragments[0].text

    def test_can_extract_plain_text(self, extractor) -> None:
        """Can extract plain text MIME types."""
        assert extractor.can_extract("text/plain")
        assert extractor.can_extract("text/x-python")
        assert extractor.can_extract("text/javascript")
        assert not extractor.can_extract("text/markdown")

    @pytest.mark.asyncio
    async def test_bytes_content_decoded(self, extractor) -> None:
        """Bytes content is decoded to string."""
        doc = DocumentInput(
            source="test.txt",
            mime_type="text/plain",
            content=b"Binary content",
        )

        fragments = await extractor.extract(doc)

        assert fragments[0].text == "Binary content"


class TestMarkdownExtractor:
    """Tests for MarkdownExtractor."""

    @pytest.fixture
    def extractor(self):
        """Create a markdown extractor for testing."""
        return MarkdownExtractor()

    @pytest.mark.asyncio
    async def test_simple_markdown(self, extractor) -> None:
        """Extract simple markdown."""
        doc = DocumentInput(
            source="test.md",
            mime_type="text/markdown",
            content="# Title\n\nSome content.\n",
        )

        fragments = await extractor.extract(doc)

        assert len(fragments) >= 1
        assert "# Title" in fragments[0].text or "Title" in fragments[0].text

    @pytest.mark.asyncio
    async def test_markdown_headings(self, extractor) -> None:
        """Markdown headings are detected."""
        doc = DocumentInput(
            source="test.md",
            mime_type="text/markdown",
            content="# Main Title\n\n## Section 1\n\nContent 1.\n\n## Section 2\n\nContent 2.\n",
        )

        fragments = await extractor.extract(doc)

        assert len(fragments) >= 1
        # Should have at least one fragment per section

    def test_can_extract_markdown(self, extractor) -> None:
        """Can extract markdown MIME type."""
        assert extractor.can_extract("text/markdown")
        assert not extractor.can_extract("text/plain")


class TestBaseExtractor:
    """Tests for BaseExtractor."""

    def test_extraction_options_defaults(self) -> None:
        """ExtractionOptions has sensible defaults."""
        options = ExtractionOptions()

        assert options.max_fragment_lines == 500
        assert options.preserve_blank_lines is True
        assert options.strip_trailing_whitespace is True

    @pytest.mark.asyncio
    async def test_base_extractor_simple_extraction(self) -> None:
        """BaseExtractor performs simple line extraction."""

        class SimpleExtractor(BaseExtractor):
            SUPPORTED_MIME_TYPES = ("text/custom",)

        extractor = SimpleExtractor()
        doc = DocumentInput(
            source="test.txt",
            mime_type="text/custom",
            content="Line 1\nLine 2\nLine 3\n",
        )

        fragments = await extractor.extract(doc)

        assert len(fragments) >= 1
        assert "Line" in fragments[0].text
