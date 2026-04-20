"""Knowledge Pipeline Protocols and Type Definitions.

Defines the stable port surface for the Truth Crucible knowledge ingestion pipeline.
Following DIP: Abstractions (Protocols) over concretions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

# -----------------------------------------------------------------------------
# Input/Output Types
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentInput:
    """A document ready for ingestion."""

    source: str  # File path / URL / DB query identifier
    mime_type: str  # "text/markdown" / "application/pdf" / "text/x-python" / etc.
    content: str | bytes  # Raw content
    metadata: dict[str, Any] = field(default_factory=dict)  # Source-specific metadata


@dataclass(frozen=True)
class ExtractedFragment:
    """A text fragment extracted from a document."""

    text: str
    line_start: int  # 1-indexed
    line_end: int
    mime_type: str
    metadata: dict[str, Any] = field(default_factory=dict)  # page, section, etc.


@dataclass(frozen=True)
class SemanticChunk:
    """A semantically coherent chunk from a document."""

    chunk_id: str  # Stable ID: sha256(content[:200]).hexdigest()[:16]
    text: str
    line_start: int
    line_end: int
    boundary_score: float  # Semantic boundary confidence (0.0-1.0)
    semantic_tags: tuple[str, ...]  # ["function", "class_definition", "paragraph"]
    source_hint: str  # "python" / "markdown" / "auto"


@dataclass(frozen=True)
class EnrichedChunk:
    """A semantic chunk with enriched metadata."""

    chunk: SemanticChunk
    content_hash: str  # sha256 of text
    importance: int  # 1-10, computed from signal scoring
    source_file: str
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorizedChunk:
    """An enriched chunk with computed embedding vector."""

    enriched: EnrichedChunk
    embedding: list[float]
    model: str  # Embedding model used


# -----------------------------------------------------------------------------
# Port Protocols
# -----------------------------------------------------------------------------


@runtime_checkable
class ExtractorPort(Protocol):
    """Protocol for multi-modal document extraction."""

    async def extract(self, doc: DocumentInput) -> list[ExtractedFragment]:
        """Extract text fragments from a document.

        Returns list of ExtractedFragment in reading order.
        """
        ...


@runtime_checkable
class SemanticChunkerPort(Protocol):
    """Protocol for semantic document chunking."""

    def chunk(self, text: str, *, source_hint: str = "auto") -> list[SemanticChunk]:
        """Split text into semantically coherent chunks.

        Uses NLP signal scoring + boundary detection instead of fixed line counts.
        """
        ...


@runtime_checkable
class MetadataEnricherPort(Protocol):
    """Protocol for chunk metadata enrichment."""

    def enrich(self, chunk: SemanticChunk, source_file: str) -> EnrichedChunk:
        """Enrich a semantic chunk with computed metadata.

        Adds importance score, content hash, and semantic tags.
        """
        ...


@runtime_checkable
class EmbeddingComputerPort(Protocol):
    """Protocol for batch embedding computation."""

    async def compute_batch(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        """Compute embedding vectors for multiple texts.

        Returns list of embedding vectors in same order as input texts.
        """
        ...

    def get_stats(self) -> dict[str, Any]:
        """Get embedding computer statistics."""
        ...


@runtime_checkable
class IdempotentVectorStorePort(Protocol):
    """Protocol for idempotent vector storage with ghost-data-free deletion."""

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        importance: int = 5,
    ) -> str:
        """Add a memory with content-hash deduplication.

        Same content hashed twice returns the same memory_id (idempotent).
        Returns the memory ID.
        """
        ...

    async def delete(self, memory_id: str) -> bool:
        """Soft-delete a memory (ghost-data-free).

        Writes tombstone to JSONL instead of in-memory only deletion.
        Returns True if item existed.
        """
        ...

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_importance: int = 1,
    ) -> list[tuple[str, float]]:
        """Search semantic memory by query text.

        Returns list of (memory_id, similarity_score) tuples.
        """
        ...

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        """Retrieve a memory by ID.

        Returns the memory dict or None if not found / deleted.
        """
        ...

    async def vacuum(self, max_age_days: int = 30) -> int:
        """Compact tombstone file, removing entries older than max_age_days.

        Returns the number of tombstone entries removed.
        """
        ...


__all__ = [
    # Types
    "DocumentInput",
    "EmbeddingComputerPort",
    "EnrichedChunk",
    "ExtractedFragment",
    # Ports
    "ExtractorPort",
    "IdempotentVectorStorePort",
    "MetadataEnricherPort",
    "SemanticChunk",
    "SemanticChunkerPort",
    "VectorizedChunk",
]
