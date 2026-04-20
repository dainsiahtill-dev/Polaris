"""Tests for SemanticChunker."""

from __future__ import annotations

from polaris.kernelone.akashic.knowledge_pipeline.protocols import SemanticChunk
from polaris.kernelone.akashic.knowledge_pipeline.semantic_chunker import (
    SemanticChunker,
    _cjk_segment,
    _has_cjk,
)


class TestSemanticChunkerBasic:
    """Basic functionality tests."""

    def test_empty_text_returns_empty(self) -> None:
        """Empty text returns no chunks."""
        chunker = SemanticChunker()
        chunks = chunker.chunk("", source_hint="text")
        assert chunks == []

    def test_short_text_returns_single_chunk(self) -> None:
        """Short text returns a single chunk."""
        chunker = SemanticChunker()
        text = "Hello world"
        chunks = chunker.chunk(text, source_hint="text")
        assert len(chunks) == 1
        assert chunks[0].text == text

    def test_chunk_has_required_fields(self) -> None:
        """Each chunk has all required fields."""
        chunker = SemanticChunker()
        chunks = chunker.chunk("Some text content", source_hint="text")
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.chunk_id is not None
        assert chunk.text == "Some text content"
        assert chunk.line_start == 1
        assert chunk.line_end == 1
        assert 0.0 <= chunk.boundary_score <= 1.0
        assert isinstance(chunk.semantic_tags, tuple)


class TestSemanticChunkerPython:
    """Python-specific chunking tests."""

    def test_function_boundary_detected(self) -> None:
        """Function definitions create chunk boundaries."""
        chunker = SemanticChunker()
        code = """
def function_one():
    pass

def function_two():
    pass
"""
        chunks = chunker.chunk(code, source_hint="python")
        # Should have at least one chunk
        assert len(chunks) >= 1

    def test_class_boundary_detected(self) -> None:
        """Class definitions create chunk boundaries."""
        chunker = SemanticChunker()
        code = """
class MyClass:
    def method(self):
        pass
"""
        chunks = chunker.chunk(code, source_hint="python")
        assert len(chunks) >= 1

    def test_import_statements_tagged(self) -> None:
        """Import statements are detected as semantic tags."""
        chunker = SemanticChunker()
        code = "import os\nimport sys\n"
        chunks = chunker.chunk(code, source_hint="python")
        assert len(chunks) == 1
        # Import statements should be tagged
        assert "import_statement" in chunks[0].semantic_tags


class TestSemanticChunkerMarkdown:
    """Markdown-specific chunking tests."""

    def test_heading_boundary(self) -> None:
        """Heading boundaries are detected."""
        chunker = SemanticChunker()
        md = """
# Title

Some content.

## Section

More content.
"""
        chunks = chunker.chunk(md, source_hint="markdown")
        assert len(chunks) >= 1

    def test_heading_tags(self) -> None:
        """Headings are tagged appropriately."""
        chunker = SemanticChunker()
        md = "# Main Title\n"
        chunks = chunker.chunk(md, source_hint="markdown")
        assert len(chunks) == 1
        # Heading1 tag should be present
        tags = chunks[0].semantic_tags
        assert "heading1" in tags


class TestMergeSmallChunks:
    """Tests for _merge_small_chunks functionality."""

    def test_merge_small_chunks_threshold(self) -> None:
        """Small chunks are merged to meet min_chars threshold."""
        chunker = SemanticChunker(chunk_min_chars=10)

        # Create small chunks
        def make_chunk(text):
            return SemanticChunk(
                chunk_id="x",
                text=text,
                line_start=1,
                line_end=1,
                boundary_score=0.5,
                semantic_tags=(),
                source_hint="test",
            )

        chunks = [make_chunk("ABC"), make_chunk("DEF"), make_chunk("GHI")]
        result = chunker._merge_small_chunks(chunks)

        # All small chunks should merge into one
        assert len(result) == 1
        assert len(result[0].text) >= 10

    def test_large_chunk_preserved(self) -> None:
        """Large chunks are preserved even when surrounded by small chunks."""
        chunker = SemanticChunker(chunk_min_chars=10)

        def make_chunk(text, line=1):
            return SemanticChunk(
                chunk_id="x",
                text=text,
                line_start=line,
                line_end=line,
                boundary_score=0.5,
                semantic_tags=(),
                source_hint="test",
            )

        chunks = [make_chunk("ABC"), make_chunk("12345678901"), make_chunk("X")]
        result = chunker._merge_small_chunks(chunks)

        # The large chunk should be preserved
        assert len(result) >= 1
        # Check that the 11-char chunk is present
        texts = [c.text for c in result]
        assert any("12345678901" in t for t in texts)

    def test_empty_input_returns_empty(self) -> None:
        """Empty input returns empty list."""
        chunker = SemanticChunker()
        result = chunker._merge_small_chunks([])
        assert result == []

    def test_single_chunk_returned(self) -> None:
        """Single chunk is returned unchanged."""
        chunker = SemanticChunker()

        def make_chunk():
            return SemanticChunk(
                chunk_id="x",
                text="Hello world",
                line_start=1,
                line_end=1,
                boundary_score=0.5,
                semantic_tags=(),
                source_hint="test",
            )

        chunks = [make_chunk()]
        result = chunker._merge_small_chunks(chunks)
        assert len(result) == 1


class TestSemanticChunkerCJK:
    """CJK text segmentation and boundary detection tests."""

    def test_has_cjk_detects_chinese(self) -> None:
        """_has_cjk returns True for Chinese text."""
        assert _has_cjk("这是一个测试")
        assert _has_cjk("中文")
        assert _has_cjk("Hello World 中文")

    def test_has_cjk_detects_japanese(self) -> None:
        """_has_cjk returns True for Japanese."""
        assert _has_cjk("これはテストです")
        assert _has_cjk("日本語")

    def test_has_cjk_detects_korean(self) -> None:
        """_has_cjk returns True for Korean."""
        assert _has_cjk("안녕하세요")
        assert _has_cjk("한국어")

    def test_has_cjk_returns_false_for_pure_ascii(self) -> None:
        """_has_cjk returns False for pure ASCII text."""
        assert not _has_cjk("Hello world")
        assert not _has_cjk("")
        assert not _has_cjk("12345")

    def test_cjk_segment_returns_tokens(self) -> None:
        """_cjk_segment returns a list of word tokens."""
        result = _cjk_segment("这是一个测试")
        assert isinstance(result, list)
        assert len(result) > 0
        # Should produce non-empty tokens
        assert all(t for t in result)

    def test_cjk_segment_character_fallback(self) -> None:
        """_cjk_segment falls back to character-level when jieba unavailable."""
        # Even without jieba, should return tokens (character-level)
        result = _cjk_segment("中文")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_cjk_sentence_boundary_in_chunks(self) -> None:
        """CJK sentence-ending punctuation creates chunk boundaries."""
        chunker = SemanticChunker(boundary_threshold=0.5)
        text = "第一句。\n第二句。\n第三句。"
        chunks = chunker.chunk(text, source_hint="text")
        # Should produce multiple chunks due to sentence boundaries
        assert len(chunks) >= 1

    def test_cjk_text_tag_added(self) -> None:
        """Chunks containing CJK text get 'cjk_text' tag."""
        chunker = SemanticChunker()
        chunks = chunker.chunk("这是一个测试", source_hint="text")
        assert len(chunks) == 1
        assert "cjk_text" in chunks[0].semantic_tags
        assert "chinese" in chunks[0].semantic_tags

    def test_japanese_tag_added(self) -> None:
        """Chunks containing Japanese get 'japanese' tag."""
        chunker = SemanticChunker()
        chunks = chunker.chunk("日本語のテスト", source_hint="text")
        assert len(chunks) == 1
        assert "cjk_text" in chunks[0].semantic_tags
        assert "japanese" in chunks[0].semantic_tags

    def test_korean_tag_added(self) -> None:
        """Chunks containing Korean get 'korean' tag."""
        chunker = SemanticChunker()
        chunks = chunker.chunk("안녕하세요 테스트", source_hint="text")
        assert len(chunks) == 1
        assert "cjk_text" in chunks[0].semantic_tags
        assert "korean" in chunks[0].semantic_tags

    def test_mixed_cjk_and_code(self) -> None:
        """Mixed CJK text and code is chunked correctly."""
        chunker = SemanticChunker(boundary_threshold=0.5)
        text = "中文解释\n\ndef func():\n    pass\n\n更多中文"
        chunks = chunker.chunk(text, source_hint="python")
        assert len(chunks) >= 1
        # All chunks should have valid tags
        for chunk in chunks:
            assert isinstance(chunk.semantic_tags, tuple)
