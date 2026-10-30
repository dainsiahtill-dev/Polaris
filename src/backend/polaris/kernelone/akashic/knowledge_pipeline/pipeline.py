"""Document Ingestion Pipeline Orchestrator.

DAG-based document ingestion pipeline using asyncio.TaskGroup for
parallel execution of independent stages.

Pipeline stages:
1. EXTRACTOR: Parse document (PDF/Word/Markdown) → ExtractedFragment[]
2. SEMANTIC_CHUNKER: Split at semantic boundaries → SemanticChunk[]
3. METADATA_ENRICHER: Add importance + hash + tags → EnrichedChunk[]
4. EMBEDDING_COMPUTER: Vectorize → VectorizedChunk[]
5. VECTOR_STORE: Persist with idempotency → memory_ids[]

Reference patterns:
- memory_manager.py TierCoordinator for asyncio.gather() parallel promotion
- workflow/engine.py for DAG scheduling with asyncio.wait()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

# Reuse AkashicSemanticMemory - must be at top before local imports
from polaris.kernelone.akashic.semantic_memory import AkashicSemanticMemory
from polaris.kernelone.storage import resolve_runtime_path

from .embedding_computer import EmbeddingComputer
from .extractors.extractor_registry import ExtractorRegistry, get_default_registry
from .idempotent_vector_store import IdempotentVectorStore
from .protocols import (
    DocumentInput,
    EmbeddingComputerPort,
    EnrichedChunk,
    ExtractedFragment,
    IdempotentVectorStorePort,
    MetadataEnricherPort,
    SemanticChunk,
    SemanticChunkerPort,
    VectorizedChunk,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineConfig:
    """Configuration for DocumentPipeline."""

    max_concurrency: int = 4  # Max parallel chunks per stage
    batch_size: int = 32  # Embedding batch size
    embedding_model: str = "nomic-embed-text"
    importance_floor: int = 3  # Minimum importance for storage
    # Paths
    workspace: str = "."
    memory_file: str | None = None  # Override for semantic memory JSONL
    # Default vector store backend: "jsonl" (default) or "lancedb"
    default_vector_store: str = "jsonl"


@dataclass
class PipelineResult:
    """Result of a pipeline run."""

    document_id: str  # Source identifier
    status: str  # "success" | "partial" | "failed"
    chunks_processed: int
    memory_ids: list[str]  # IDs in vector store
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)


class MetadataEnricher:
    """Enriches semantic chunks with computed metadata.

    Adds:
    - content_hash: SHA256 of text for deduplication
    - importance: Signal-based importance score (1-10)
    - semantic_tags: Auto-detected tags from content
    """

    def __init__(self, importance_floor: int = 3) -> None:
        self._importance_floor = importance_floor

    def enrich(self, chunk: SemanticChunk, source_file: str) -> EnrichedChunk:
        """Enrich a semantic chunk with computed metadata."""
        import hashlib

        content_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()[:32]

        # Compute importance from boundary score + signal terms
        base_importance = int(chunk.boundary_score * 10)

        # Boost for high-signal tags
        signal_boost = 0
        for tag in chunk.semantic_tags:
            if tag in ("high_density", "function_definition", "class_definition"):
                signal_boost += 1
            elif tag in ("error_handling", "test_code"):
                signal_boost += 2

        importance = max(1, min(10, base_importance + signal_boost))
        importance = max(importance, self._importance_floor)

        # Merge chunk metadata with enrichment metadata
        merged_metadata: dict[str, Any] = {}
        merged_metadata["source_file"] = source_file
        merged_metadata["semantic_tags"] = list(chunk.semantic_tags)

        return EnrichedChunk(
            chunk=chunk,
            content_hash=content_hash,
            importance=importance,
            source_file=source_file,
            created_at=datetime.now(timezone.utc),
            metadata=merged_metadata,
        )


class DocumentPipeline:
    """DAG-based document ingestion pipeline.

    Orchestrates the flow from raw document to vectorized storage:
    EXTRACTOR → SEMANTIC_CHUNKER → METADATA_ENRICHER → EMBEDDING_COMPUTER → VECTOR_STORE

    Uses asyncio.TaskGroup for parallel execution of independent stages.
    Supports ExtractorRegistry for MIME type routing.

    Usage::

        pipeline = DocumentPipeline(workspace=".")

        documents = [
            DocumentInput(
                source="readme.md",
                mime_type="text/markdown",
                content=Path("readme.md").read_text(),
            )
        ]

        results = await pipeline.run(documents)
        for result in results:
            print(f"{result.document_id}: {result.chunks_processed} chunks")
    """

    # Instance variable type annotations using Protocol types
    _chunker: SemanticChunkerPort
    _enricher: MetadataEnricherPort
    _embedding_computer: EmbeddingComputerPort | None
    _vector_store: IdempotentVectorStorePort

    def __init__(
        self,
        workspace: str = ".",
        *,
        chunker: SemanticChunkerPort | None = None,
        enricher: MetadataEnricherPort | None = None,
        embedding_computer: EmbeddingComputerPort | None = None,
        vector_store: IdempotentVectorStorePort | None = None,
        extractor_registry: ExtractorRegistry | None = None,
        config: PipelineConfig | None = None,
    ) -> None:
        self._workspace = workspace
        self._config = config or PipelineConfig(workspace=workspace)

        # Initialize components
        from .semantic_chunker import SemanticChunker

        self._chunker = chunker or SemanticChunker()

        self._enricher = enricher or MetadataEnricher(
            importance_floor=self._config.importance_floor,
        )

        # Initialize extractor registry
        self._extractor_registry = extractor_registry or get_default_registry()

        # Initialize embedding computer if not provided
        if embedding_computer is None:
            try:
                from polaris.kernelone.llm.embedding import get_default_embedding_port

                port = get_default_embedding_port()
                self._embedding_computer = EmbeddingComputer(
                    embedding_port=port,
                    model=self._config.embedding_model,
                    max_batch_size=self._config.batch_size,
                )
            except RuntimeError as exc:
                logger.warning("No embedding port available: %s", exc)
                self._embedding_computer = None
        else:
            self._embedding_computer = embedding_computer

        # Initialize vector store if not provided
        if vector_store is None:
            if self._config.default_vector_store == "lancedb":
                # Use LanceDB-backed vector store
                try:
                    from polaris.kernelone.llm.embedding import get_default_embedding_port

                    from .lancedb_adapter import KnowledgeLanceDB
                    from .lancedb_vector_adapter import LanceDBVectorAdapter

                    lancedb = KnowledgeLanceDB(workspace=workspace)
                    embedding_port = get_default_embedding_port()
                    emb_computer = EmbeddingComputer(
                        embedding_port=embedding_port,
                        model=self._config.embedding_model,
                        max_batch_size=self._config.batch_size,
                    )
                    self._embedding_computer = emb_computer
                    self._vector_store = LanceDBVectorAdapter(
                        lancedb,
                        embedding_computer=emb_computer,
                    )
                except (RuntimeError, ValueError) as exc:
                    logger.warning(
                        "LanceDB vector store unavailable, falling back to JSONL: %s",
                        exc,
                    )
                    self._vector_store = self._create_jsonl_store(workspace)
            else:
                # Default: use JSONL-backed IdempotentVectorStore
                self._vector_store = self._create_jsonl_store(workspace)
        else:
            self._vector_store = vector_store

    def _create_jsonl_store(self, workspace: str) -> IdempotentVectorStore:
        """Create a JSONL-backed IdempotentVectorStore."""
        memory_file = self._config.memory_file or resolve_runtime_path(workspace, "runtime/semantic/memory.jsonl")
        semantic = AkashicSemanticMemory(
            workspace=workspace,
            memory_file=memory_file,
        )
        return IdempotentVectorStore(semantic)

    async def run(
        self,
        documents: list[DocumentInput],
    ) -> list[PipelineResult]:
        """Run the pipeline on a list of documents.

        Processes documents in parallel using asyncio.TaskGroup,
        with per-document stage parallelism.

        Returns list of PipelineResult in same order as input documents.
        """
        if not documents:
            return []

        start_time = asyncio.get_running_loop().time()

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(self._process_document(doc)) for doc in documents]

        results = [task.result() for task in tasks]
        duration_ms = (asyncio.get_running_loop().time() - start_time) * 1000

        for result in results:
            result.duration_ms = duration_ms

        return results

    async def run_lazy(
        self,
        documents: AsyncGenerator[DocumentInput, None],
    ) -> AsyncGenerator[PipelineResult, None]:
        """Run the pipeline with lazy document consumption.

        Uses async generator pattern to handle GB-level corpora with
        constant memory footprint (O(batch_size)).

        Usage::

            async def document_source():
                for path in large_corpus:
                    yield DocumentInput(source=path, ...)

            pipeline = DocumentPipeline(workspace=".")
            async for result in pipeline.run_lazy(document_source()):
                print(f"Processed: {result.document_id}")

        Yields PipelineResult as each document completes.
        """
        batch: list[DocumentInput] = []
        batch_size = self._config.batch_size

        async for doc in documents:
            batch.append(doc)

            if len(batch) >= batch_size:
                # Process batch
                results = await self.run(batch)
                for result in results:
                    yield result
                batch.clear()

        # Process remaining documents
        if batch:
            results = await self.run(batch)
            for result in results:
                yield result

    async def _process_document(self, doc: DocumentInput) -> PipelineResult:
        """Process a single document through all pipeline stages."""

        errors: list[str] = []
        memory_ids: list[str] = []

        try:
            # Stage 1: Extract (via registry)
            fragments = await self._extract(doc)

            if not fragments:
                return PipelineResult(
                    document_id=doc.source,
                    status="failed",
                    chunks_processed=0,
                    memory_ids=[],
                    errors=["No fragments extracted"],
                )

            # Stage 2: Chunk (can parallelize across fragments)
            chunks = await self._chunk(fragments, doc.source)

            if not chunks:
                return PipelineResult(
                    document_id=doc.source,
                    status="failed",
                    chunks_processed=0,
                    memory_ids=[],
                    errors=["No chunks produced"],
                )

            # Stage 3: Enrich (embarrassingly parallel)
            enriched = await self._enrich(chunks, doc.source)

            # Stage 4: Embed (batch)
            vectorized = await self._embed(enriched)

            # Stage 5: Store (batch)
            memory_ids = await self._store(vectorized)

            return PipelineResult(
                document_id=doc.source,
                status="success" if not errors else "partial",
                chunks_processed=len(chunks),
                memory_ids=memory_ids,
                errors=errors,
            )

        except (RuntimeError, ValueError) as exc:
            logger.exception("Pipeline error for %s: %s", doc.source, exc)
            return PipelineResult(
                document_id=doc.source,
                status="failed",
                chunks_processed=0,
                memory_ids=[],
                errors=[str(exc)],
            )

    async def _extract(self, doc: DocumentInput) -> list[ExtractedFragment]:
        """Extract text fragments using registered extractors.

        Routes to the appropriate extractor based on MIME type using the
        ExtractorRegistry.
        """
        extractor = self._extractor_registry.get(doc.mime_type)

        if extractor is None:
            logger.warning(
                "No extractor registered for MIME type '%s' for document '%s'. Supported types: %s",
                doc.mime_type,
                doc.source,
                self._extractor_registry.supported_mime_types(),
            )
            return []

        try:
            fragments = await extractor.extract(doc)
            logger.debug(
                "Extracted %d fragments from '%s' using %s",
                len(fragments),
                doc.source,
                type(extractor).__name__,
            )
            return fragments
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Extractor %s failed for '%s': %s",
                type(extractor).__name__,
                doc.source,
                exc,
            )
            return []

    async def _chunk(
        self,
        fragments: list[ExtractedFragment],
        source: str,
    ) -> list[SemanticChunk]:
        """Chunk fragments into semantic chunks.

        Uses source_hint derived from mime_type or filename.
        """
        # Use the MIME type from the first fragment to derive source hint
        source_hint = self._mime_to_hint(fragments[0].mime_type if fragments else "text/plain")
        if source_hint == "auto":
            if source.endswith(".py"):
                source_hint = "python"
            elif source.endswith((".js", ".ts", ".tsx", ".jsx")):
                source_hint = "javascript"
            elif source.endswith(".md"):
                source_hint = "markdown"

        all_chunks: list[SemanticChunk] = []

        for fragment in fragments:
            chunks = self._chunker.chunk(
                fragment.text,
                source_hint=source_hint,
            )
            all_chunks.extend(chunks)

        return all_chunks

    async def _enrich(
        self,
        chunks: list[SemanticChunk],
        source_file: str,
    ) -> list[EnrichedChunk]:
        """Enrich chunks with metadata.

        Embarrassingly parallel - processes all chunks concurrently.
        """
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(self._enrich_single(chunk, source_file)) for chunk in chunks]

        return [task.result() for task in tasks]

    async def _enrich_single(
        self,
        chunk: SemanticChunk,
        source_file: str,
    ) -> EnrichedChunk:
        """Enrich a single chunk (runs in thread)."""
        # MetadataEnricher.enrich is CPU-bound, run in executor
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._enricher.enrich(chunk, source_file),
        )

    async def _embed(
        self,
        enriched: list[EnrichedChunk],
    ) -> list[VectorizedChunk]:
        """Compute embeddings for enriched chunks.

        Uses batch processing via EmbeddingComputer.
        """
        if not self._embedding_computer:
            # Fallback: zero embeddings
            return [
                VectorizedChunk(
                    enriched=e,
                    embedding=[0.0] * 384,
                    model="none",
                )
                for e in enriched
            ]

        texts = [e.chunk.text for e in enriched]
        embeddings = await self._embedding_computer.compute_batch(texts)

        return [
            VectorizedChunk(
                enriched=enriched[i],
                embedding=embeddings[i],
                model=self._config.embedding_model,
            )
            for i in range(len(enriched))
        ]

    async def _store(
        self,
        vectorized: list[VectorizedChunk],
    ) -> list[str]:
        """Store vectorized chunks in idempotent vector store.

        Batch add with idempotent dedup.
        """
        memory_ids: list[str] = []

        for vc in vectorized:
            memory_id = await self._vector_store.add(
                text=vc.enriched.chunk.text,
                metadata={
                    **vc.enriched.metadata,
                    "source_file": vc.enriched.source_file,
                    "line_start": vc.enriched.chunk.line_start,
                    "line_end": vc.enriched.chunk.line_end,
                    "embedding_model": vc.model,
                    "content_hash": vc.enriched.content_hash,
                },
                importance=vc.enriched.importance,
            )
            memory_ids.append(memory_id)

        return memory_ids

    def _mime_to_hint(self, mime_type: str) -> str:
        """Convert mime_type to source_hint for chunker."""
        mime_to_hint = {
            "text/x-python": "python",
            "text/javascript": "javascript",
            "text/typescript": "typescript",
            "text/markdown": "markdown",
            "text/plain": "auto",
            "application/json": "auto",
        }
        return mime_to_hint.get(mime_type, "auto")

    def get_stats(self) -> dict[str, Any]:
        """Get pipeline statistics."""
        return {
            "vector_store": self._vector_store.get_stats() if hasattr(self._vector_store, "get_stats") else {},
            "embedding_computer": self._embedding_computer.get_stats() if self._embedding_computer is not None else {},
            "extractor_registry": {
                "supported_mime_types": self._extractor_registry.supported_mime_types(),
                "extractor_count": len(self._extractor_registry.supported_mime_types()),
            },
            "config": asdict(self._config),
        }


__all__ = ["DocumentPipeline", "MetadataEnricher", "PipelineConfig", "PipelineResult"]
