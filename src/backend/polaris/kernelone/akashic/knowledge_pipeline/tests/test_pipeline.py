"""Tests for DocumentPipeline."""

from __future__ import annotations

import pytest
from polaris.kernelone.akashic.knowledge_pipeline.pipeline import (
    DocumentPipeline,
    MetadataEnricher,
    PipelineConfig,
)
from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
    DocumentInput,
)


class TestMetadataEnricher:
    """Tests for MetadataEnricher."""

    def test_enrich_adds_content_hash(self) -> None:
        """Enrich adds content hash to metadata."""
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import SemanticChunk

        enricher = MetadataEnricher()
        chunk = SemanticChunk(
            chunk_id="test",
            text="Test content",
            line_start=1,
            line_end=1,
            boundary_score=0.5,
            semantic_tags=(),
            source_hint="text",
        )

        enriched = enricher.enrich(chunk, "test.txt")

        assert enriched.content_hash is not None
        assert len(enriched.content_hash) == 32

    def test_enrich_adds_source_file(self) -> None:
        """Enrich adds source file to metadata."""
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import SemanticChunk

        enricher = MetadataEnricher()
        chunk = SemanticChunk(
            chunk_id="test",
            text="Test content",
            line_start=1,
            line_end=1,
            boundary_score=0.5,
            semantic_tags=(),
            source_hint="text",
        )

        enriched = enricher.enrich(chunk, "myfile.py")

        assert enriched.source_file == "myfile.py"

    def test_enrich_importance_within_range(self) -> None:
        """Enrich ensures importance is within 1-10 range."""
        from polaris.kernelone.akashic.knowledge_pipeline.protocols import SemanticChunk

        enricher = MetadataEnricher()
        chunk = SemanticChunk(
            chunk_id="test",
            text="Test content",
            line_start=1,
            line_end=1,
            boundary_score=0.5,
            semantic_tags=(),
            source_hint="text",
        )

        enriched = enricher.enrich(chunk, "test.txt")

        assert 1 <= enriched.importance <= 10


class TestDocumentPipeline:
    """Tests for DocumentPipeline."""

    @pytest.fixture
    def pipeline(self):
        """Create a pipeline for testing."""
        return DocumentPipeline(
            workspace=".",
            config=PipelineConfig(max_concurrency=1),
        )

    @pytest.mark.asyncio
    async def test_run_empty_documents(self, pipeline) -> None:
        """Run with empty document list returns empty results."""
        results = await pipeline.run([])
        assert results == []

    @pytest.mark.asyncio
    async def test_run_single_document(self, pipeline) -> None:
        """Run with single document returns result."""
        docs = [
            DocumentInput(
                source="test.py",
                mime_type="text/x-python",
                content="def hello():\n    print('Hello')\n",
            )
        ]

        results = await pipeline.run(docs)

        assert len(results) == 1
        assert results[0].document_id == "test.py"
        assert results[0].status in ("success", "partial", "failed")

    @pytest.mark.asyncio
    async def test_run_multiple_documents(self, pipeline) -> None:
        """Run with multiple documents processes all."""
        docs = [
            DocumentInput(
                source="file1.py",
                mime_type="text/x-python",
                content="def func1():\n    pass\n",
            ),
            DocumentInput(
                source="file2.py",
                mime_type="text/x-python",
                content="def func2():\n    pass\n",
            ),
        ]

        results = await pipeline.run(docs)

        assert len(results) == 2
        assert results[0].document_id == "file1.py"
        assert results[1].document_id == "file2.py"

    @pytest.mark.asyncio
    async def test_run_lazy_generator(self, pipeline) -> None:
        """Run lazy processes documents from generator."""

        async def doc_source():
            for i in range(3):
                yield DocumentInput(
                    source=f"lazy_{i}.py",
                    mime_type="text/x-python",
                    content=f"def func{i}():\n    pass\n",
                )

        results = []
        async for result in pipeline.run_lazy(doc_source()):
            results.append(result)

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_result_has_required_fields(self, pipeline) -> None:
        """Result has all required fields."""
        docs = [
            DocumentInput(
                source="test.py",
                mime_type="text/x-python",
                content="x = 1\n",
            )
        ]

        results = await pipeline.run(docs)
        result = results[0]

        assert result.document_id == "test.py"
        assert isinstance(result.status, str)
        assert isinstance(result.chunks_processed, int)
        assert isinstance(result.memory_ids, list)
        assert isinstance(result.errors, list)

    def test_get_stats(self, pipeline) -> None:
        """Get stats returns pipeline statistics."""
        stats = pipeline.get_stats()
        assert "config" in stats
        assert "vector_store" in stats


class TestPipelineConfig:
    """Tests for PipelineConfig."""

    def test_default_config(self) -> None:
        """Default config has sensible values."""
        config = PipelineConfig()

        assert config.max_concurrency == 4
        assert config.batch_size == 32
        assert config.embedding_model == "nomic-embed-text"
        assert config.importance_floor == 3

    def test_custom_config(self) -> None:
        """Custom config overrides defaults."""
        config = PipelineConfig(
            max_concurrency=8,
            batch_size=64,
            importance_floor=5,
        )

        assert config.max_concurrency == 8
        assert config.batch_size == 64
        assert config.importance_floor == 5
