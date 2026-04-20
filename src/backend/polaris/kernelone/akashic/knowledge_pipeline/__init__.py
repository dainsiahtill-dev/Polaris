"""Truth Crucible Knowledge Pipeline.

Semantic document ingestion pipeline with idempotent vector storage.
Replaces fixed-line chunking with semantic-aware document splitting.

Architecture:
    - DocumentPipeline: DAG orchestrator using asyncio.TaskGroup
    - SemanticChunker: NLP-aware chunking (boundaries, intent, code structure)
    - IdempotentVectorStore: Ghost-data-free + hash-deduplicated storage
    - EmbeddingComputer: Batch embedding via KernelEmbeddingPort
    - KnowledgeLanceDB: LanceDB vector search adapter

Usage::

    pipeline = DocumentPipeline(workspace=".")
    result = await pipeline.run([DocumentInput(source="doc.md", ...)])

    # Or with lazy loading for large corpora:
    async for result in pipeline.run_lazy(large_document_generator()):
        print(f"Processed: {result.document_id}")
"""

from __future__ import annotations

from polaris.kernelone.akashic.knowledge_pipeline.embedding_computer import (
    EmbeddingComputer,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors import (
    BaseExtractor,
    CsvExtractor,
    DocxExtractor,
    ExtractorRegistry,
    HtmlExtractor,
    MarkdownExtractor,
    PDFExtractor,
    PptxExtractor,
    TextExtractor,
    XlsxExtractor,
    get_default_registry,
    reset_default_registry,
)
from polaris.kernelone.akashic.knowledge_pipeline.idempotent_vector_store import (
    IdempotentVectorStore,
)
from polaris.kernelone.akashic.knowledge_pipeline.knowledge_sync import (
    KnowledgeSync,
    SyncStats,
)
from polaris.kernelone.akashic.knowledge_pipeline.lancedb_adapter import (
    KnowledgeChunkRecord,
    KnowledgeLanceDB,
    SearchResult,
    UpsertResult,
    VectorRecord,
)
from polaris.kernelone.akashic.knowledge_pipeline.lancedb_vector_adapter import (
    LanceDBVectorAdapter,
)
from polaris.kernelone.akashic.knowledge_pipeline.mime_detector import (
    MagicMimeDetector,
    get_mime_detector,
)
from polaris.kernelone.akashic.knowledge_pipeline.pipeline import (
    DocumentPipeline,
    MetadataEnricher,
    PipelineConfig,
    PipelineResult,
)
from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
    DocumentInput,
    EnrichedChunk,
    ExtractedFragment,
    SemanticChunk,
    VectorizedChunk,
)
from polaris.kernelone.akashic.knowledge_pipeline.semantic_chunker import (
    SemanticChunker,
)

__version__ = "0.1.0"

__all__ = [
    # Extractors
    "BaseExtractor",
    "CsvExtractor",
    # Types
    "DocumentInput",
    # Core pipeline
    "DocumentPipeline",
    "DocxExtractor",
    "EmbeddingComputer",
    "EnrichedChunk",
    "ExtractedFragment",
    "ExtractorRegistry",
    "HtmlExtractor",
    "IdempotentVectorStore",
    "KnowledgeChunkRecord",
    "KnowledgeLanceDB",
    "KnowledgeSync",
    "LanceDBVectorAdapter",
    # MIME detection
    "MagicMimeDetector",
    "MarkdownExtractor",
    "MetadataEnricher",
    "PDFExtractor",
    "PipelineConfig",
    "PipelineResult",
    "PptxExtractor",
    "SearchResult",
    "SemanticChunk",
    # Components
    "SemanticChunker",
    "SyncStats",
    "TextExtractor",
    "UpsertResult",
    "VectorRecord",
    "VectorizedChunk",
    "XlsxExtractor",
    "get_default_registry",
    "get_mime_detector",
    "reset_default_registry",
]
