"""Akashic Nexus: Hybrid Memory with Three-Layer Storage.

This module provides HybridMemory that combines:
1. Vector storage (embedding-based similarity search)
2. Full-text storage (keyword and phrase search)
3. Graph storage (relationship and entity tracking)

Architecture:
    HybridMemory
        ├── VectorStore: Embedding-based similarity (LanceDB or in-memory)
        ├── FullTextStore: Keyword/phrase search (JSONL + inverted index)
        └── GraphStore: Entity relationship tracking (networkx or dict-based)

Design constraints:
    - All backends are configurable via constructor injection
    - Graceful degradation if a layer is unavailable
    - Explicit UTF-8 text I/O
    - Thread-safe async operations

Usage::

    hybrid = HybridMemory(workspace=".")
    memory_id = await hybrid.add(
        text="Fixed authentication bug in login.py",
        entities=["login.py", "auth"],
        relationships=[("login.py", "contains", "auth")],
        importance=8,
    )
    results = await hybrid.search("authentication fix", top_k=5)
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.kernelone.storage import resolve_runtime_path

if TYPE_CHECKING:
    pass

from polaris.kernelone.akashic.protocols import SemanticMemoryPort

logger = logging.getLogger(__name__)


# ============================================================================
# Data Structures
# ============================================================================


@dataclass(frozen=True)
class HybridMemoryItem:
    """A single item in hybrid memory with vector, text, and graph components."""

    memory_id: str
    text: str
    importance: int
    created_at: datetime
    embedding: tuple[float, ...] | None = None
    entities: tuple[str, ...] = ()
    relationships: tuple[tuple[str, str, str], ...] = ()  # (subject, predicate, object)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """A search result with fused score from multiple layers."""

    memory_id: str
    text: str
    importance: int
    created_at: datetime
    vector_score: float = 0.0
    fulltext_score: float = 0.0
    graph_score: float = 0.0
    fused_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "memory_id": self.memory_id,
            "text": self.text,
            "importance": self.importance,
            "created_at": self.created_at.isoformat(),
            "vector_score": self.vector_score,
            "fulltext_score": self.fulltext_score,
            "graph_score": self.graph_score,
            "fused_score": self.fused_score,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class HybridMemoryConfig:
    """Configuration for HybridMemory.

    Attributes:
        enable_vector: Whether to enable vector storage.
        enable_fulltext: Whether to enable full-text storage.
        enable_graph: Whether to enable graph storage.
        embedding_model: Embedding model name for vector storage.
        fusion_weights: Tuple of (vector_weight, fulltext_weight, graph_weight).
        min_fusion_score: Minimum fusion score threshold.
        vector_top_k: Default top-k for vector search.
        fulltext_top_k: Default top-k for fulltext search.
        graph_top_k: Default top-k for graph search.
        max_jsonl_size_mb: Maximum size of JSONL file before rotation (default 10MB).
        max_rotated_files: Maximum number of rotated JSONL files to retain.
    """

    enable_vector: bool = True
    enable_fulltext: bool = True
    enable_graph: bool = True
    embedding_model: str = "nomic-embed-text"
    fusion_weights: tuple[float, float, float] = (0.4, 0.3, 0.3)
    min_fusion_score: float = 0.1
    vector_top_k: int = 10
    fulltext_top_k: int = 20
    graph_top_k: int = 5
    max_jsonl_size_mb: float = 10.0
    max_rotated_files: int = 3


# -----------------------------------------------------------------------------
# Core Types for Task-Specified API
# -----------------------------------------------------------------------------


@dataclass
class Memory:
    """A memory item to be stored in hybrid memory.

    Attributes:
        content: The text content of the memory.
        memory_id: Optional unique ID. If None, a UUID is generated.
        importance: Importance score (1-10). Higher = more important.
        metadata: Additional metadata as key-value pairs.
        tags: Semantic tags for categorization.
        created_at: Creation timestamp. If None, current UTC time is used.
    """

    content: str
    memory_id: str | None = None
    importance: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.memory_id is None:
            object.__setattr__(self, "memory_id", f"mem_{uuid.uuid4().hex[:16]}")
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.now(timezone.utc))
        if not isinstance(self.importance, int):
            object.__setattr__(self, "importance", int(self.importance))
        object.__setattr__(self, "importance", max(1, min(10, self.importance)))


@dataclass(frozen=True)
class MemoryResult:
    """A retrieved memory result with fused score.

    Attributes:
        memory_id: Unique identifier of the memory.
        content: Text content of the memory.
        score: Fused relevance score (0.0-1.0), higher = more relevant.
        importance: Original importance score (1-10).
        tags: Semantic tags.
        metadata: Original metadata.
        created_at: Creation timestamp.
        source: Which storage layers matched ("vector", "fulltext", "graph", or combined).
    """

    memory_id: str
    content: str
    score: float
    importance: int
    tags: tuple[str, ...]
    metadata: dict[str, Any]
    created_at: datetime
    source: str


@dataclass
class ScoredResult:
    """Internal scored result from a single storage layer.

    Attributes:
        memory_id: Unique identifier.
        content: Text content.
        score: Raw score from the storage layer.
        layer: Which storage layer produced this result.
    """

    memory_id: str
    content: str
    score: float
    layer: str  # "vector" | "fulltext" | "graph"


# ============================================================================
# Storage Backends
# ============================================================================


class VectorStoreBackend:
    """Vector storage backend using embedding similarity."""

    def __init__(
        self,
        embedding_model: str | None = None,
        lancedb: Any | None = None,
    ) -> None:
        self._embedding_model = embedding_model
        self._lancedb = lancedb
        self._embeddings: dict[str, tuple[float, ...]] = {}
        self._lock = asyncio.Lock()

    async def add(self, memory_id: str, text: str, embedding: tuple[float, ...] | None) -> None:
        """Add an embedding to the store."""
        if embedding is None:
            return
        async with self._lock:
            self._embeddings[memory_id] = embedding

    async def search(
        self,
        query_embedding: tuple[float, ...],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Search by embedding similarity, returns (memory_id, score)."""
        if not self._embeddings:
            return []

        scored: list[tuple[float, str]] = []
        for mid, emb in self._embeddings.items():
            score = self._cosine_similarity(query_embedding, emb)
            scored.append((score, mid))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [(mid, score) for score, mid in scored[:top_k]]

    async def delete(self, memory_id: str) -> bool:
        """Delete an embedding."""
        async with self._lock:
            if memory_id in self._embeddings:
                del self._embeddings[memory_id]
                return True
            return False

    def _cosine_similarity(self, a: tuple[float, ...], b: tuple[float, ...]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b) or not a:
            return 0.0
        dot_product = float(sum(x * y for x, y in zip(a, b, strict=True)))
        norm_a = float(sum(x * x for x in a)) ** 0.5
        norm_b = float(sum(x * x for x in b)) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        result: float = dot_product / (norm_a * norm_b)
        return result


class FullTextStoreBackend:
    """Full-text storage backend with inverted index."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._inverted_index: dict[str, set[str]] = {}  # term -> memory_ids
        self._lock = asyncio.Lock()

    async def add(self, memory_id: str, text: str, metadata: dict[str, Any]) -> None:
        """Add an item with tokenization for inverted index."""
        async with self._lock:
            self._items[memory_id] = {
                "memory_id": memory_id,
                "text": text,
                "metadata": metadata,
            }
            # Update inverted index
            tokens = self._tokenize(text)
            for token in tokens:
                if token not in self._inverted_index:
                    self._inverted_index[token] = set()
                self._inverted_index[token].add(memory_id)

    async def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Search by keyword matching, returns (memory_id, score)."""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        # Count matches per item
        scores: dict[str, int] = {}
        for token in query_tokens:
            if token in self._inverted_index:
                for mid in self._inverted_index[token]:
                    scores[mid] = scores.get(mid, 0) + 1

        # Normalize by max possible score
        max_score = len(query_tokens)
        scored: list[tuple[float, str]] = []
        for mid, count in scores.items():
            score = count / max_score if max_score > 0 else 0.0
            scored.append((score, mid))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [(mid, score) for score, mid in scored[:top_k]]

    async def delete(self, memory_id: str) -> bool:
        """Delete an item and update inverted index."""
        async with self._lock:
            if memory_id not in self._items:
                return False

            text = self._items[memory_id].get("text", "")
            tokens = self._tokenize(text)
            for token in tokens:
                if token in self._inverted_index:
                    self._inverted_index[token].discard(memory_id)
                    if not self._inverted_index[token]:
                        del self._inverted_index[token]

            del self._items[memory_id]
            return True

    def _tokenize(self, text: str) -> set[str]:
        """Tokenize text into normalized terms."""
        if not text:
            return set()
        tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text.lower())
        return set(tokens)


class GraphStoreBackend:
    """Graph storage backend for entity relationships."""

    def __init__(self) -> None:
        self._entities: dict[str, set[str]] = {}  # memory_id -> entity set
        self._relationships: dict[str, list[tuple[str, str, str]]] = {}  # entity -> (subj, pred, obj)
        self._entity_index: dict[str, set[str]] = {}  # entity name -> memory_ids
        self._lock = asyncio.Lock()

    async def add(
        self,
        memory_id: str,
        entities: tuple[str, ...],
        relationships: tuple[tuple[str, str, str], ...],
    ) -> None:
        """Add entities and relationships."""
        async with self._lock:
            self._entities[memory_id] = set(entities)

            for subj, pred, obj in relationships:
                if pred not in self._relationships:
                    self._relationships[pred] = []
                self._relationships[pred].append((subj, pred, obj))

                # Index entities
                for entity in (subj, obj):
                    if entity not in self._entity_index:
                        self._entity_index[entity] = set()
                    self._entity_index[entity].add(memory_id)

    async def search(
        self,
        query_entities: list[str],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Search by entity/relationship matching, returns (memory_id, score)."""
        if not query_entities:
            return []

        scores: dict[str, int] = {}
        for entity in query_entities:
            entity_lower = entity.lower()
            if entity_lower in self._entity_index:
                for mid in self._entity_index[entity_lower]:
                    scores[mid] = scores.get(mid, 0) + 1

        # Also check relationship predicates
        for entity in query_entities:
            entity_lower = entity.lower()
            if entity_lower in self._relationships:
                for subj, _pred, _obj in self._relationships[entity_lower]:
                    if subj in self._entity_index:
                        for mid in self._entity_index[subj]:
                            scores[mid] = scores.get(mid, 0) + 1

        max_score = max(len(query_entities), 1)
        scored: list[tuple[float, str]] = []
        for mid, count in scores.items():
            score = count / max_score if max_score > 0 else 0.0
            scored.append((score, mid))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [(mid, score) for score, mid in scored[:top_k]]

    async def delete(self, memory_id: str) -> bool:
        """Delete entities and relationships for a memory item."""
        async with self._lock:
            if memory_id not in self._entities:
                return False

            entities = self._entities[memory_id]

            # Remove from entity index
            for entity in entities:
                entity_lower = entity.lower()
                if entity_lower in self._entity_index:
                    self._entity_index[entity_lower].discard(memory_id)
                    if not self._entity_index[entity_lower]:
                        del self._entity_index[entity_lower]

            # Clean up empty relationship lists
            for pred in list(self._relationships.keys()):
                self._relationships[pred] = [(s, p, o) for s, p, o in self._relationships[pred] if s != memory_id]
                if not self._relationships[pred]:
                    del self._relationships[pred]

            del self._entities[memory_id]
            return True


# ============================================================================
# Hybrid Memory Implementation
# ============================================================================


class HybridMemory:
    """Three-layer hybrid memory combining vector, full-text, and graph storage.

    This memory system provides:
    - Vector search: Embedding-based similarity using configurable backend
    - Full-text search: Keyword and phrase matching via inverted index
    - Graph search: Entity and relationship matching

    The search results are fused using weighted scoring across all layers.

    Usage::

        config = HybridMemoryConfig(
            vector_store_path="/path/to/lancedb",
            fulltext_index_path="/path/to/whoosh",
            graph_db_uri="bolt://localhost:7687",
        )
        hybrid = HybridMemory(config)
        memory_id = await hybrid.store(Memory(content="Fixed authentication bug"))
        results = await hybrid.retrieve("authentication fix", top_k=5)
        for result in results:
            print(f"{result.memory_id}: {result.content} (score={result.score:.3f})")
    """

    def __init__(
        self,
        config: HybridMemoryConfig | None = None,
        *,
        # Backend injection for testing
        vector_backend: VectorStoreBackend | None = None,
        fulltext_backend: FullTextStoreBackend | None = None,
        graph_backend: GraphStoreBackend | None = None,
        workspace: str | None = None,
    ) -> None:
        """Initialize hybrid memory.

        Args:
            config: Hybrid memory configuration.
            workspace: Deprecated, kept for backward compatibility.
            vector_backend: Optional vector store backend (for testing).
            fulltext_backend: Optional full-text store backend (for testing).
            graph_backend: Optional graph store backend (for testing).
        """
        self._config = config or HybridMemoryConfig()

        # Initialize backends
        self._vector = vector_backend or VectorStoreBackend(
            embedding_model=self._config.embedding_model,
        )
        self._fulltext = fulltext_backend or FullTextStoreBackend()
        self._graph = graph_backend or GraphStoreBackend()

        # In-memory item storage
        self._items: dict[str, HybridMemoryItem] = {}
        self._lock = asyncio.Lock()

        # Persistence (for backward compatibility with old add() method)
        self._workspace = str(workspace) if workspace else "."
        self._storage_file = resolve_runtime_path(self._workspace, "runtime/hybrid_memory/memory.jsonl")
        os.makedirs(os.path.dirname(self._storage_file) or ".", exist_ok=True)

        # Lazy-loaded embedding port
        self._embedding_port: Any = None

        # Load existing items
        self._load()

    def _get_embedding_port(self) -> Any:
        """Get the embedding port (lazy initialization)."""
        if self._embedding_port is None:
            try:
                from polaris.kernelone.llm.embedding import get_default_embedding_port

                self._embedding_port = get_default_embedding_port()
            except (RuntimeError, ValueError):
                logger.debug("Could not get default embedding port")
        return self._embedding_port

    async def _compute_embedding(self, text: str) -> list[float] | None:
        """Compute embedding for text."""
        port = self._get_embedding_port()
        if port is None:
            return self._fallback_embedding(text)

        try:
            model = self._config.embedding_model
            embedding = port.get_embedding(text, model=model)
            return embedding if embedding else None
        except (RuntimeError, ValueError):
            logger.debug("Embedding computation failed, using fallback")
            return self._fallback_embedding(text)

    def _fallback_embedding(self, text: str) -> list[float]:
        """Generate a simple deterministic embedding from text hash.

        This is a fallback for testing when no embedding service is available.
        """
        hash_obj = hashlib.sha256(text.encode("utf-8"))
        hash_bytes = hash_obj.digest()
        embedding: list[float] = []
        for i in range(384):
            byte_idx = i % len(hash_bytes)
            embedding.append(hash_bytes[byte_idx] / 255.0)
        return embedding

    def _load(self) -> None:
        """Load items from JSONL file."""
        if not os.path.exists(self._storage_file):
            return
        try:
            with open(self._storage_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if isinstance(data.get("created_at"), str):
                        data["created_at"] = datetime.fromisoformat(data["created_at"])
                    if isinstance(data.get("embedding"), list):
                        data["embedding"] = tuple(data["embedding"]) if data["embedding"] else None
                    if isinstance(data.get("entities"), list):
                        data["entities"] = tuple(data["entities"])
                    if isinstance(data.get("relationships"), list):
                        data["relationships"] = tuple(
                            tuple(r) if isinstance(r, list) else r for r in data["relationships"]
                        )
                    item = HybridMemoryItem(**data)
                    self._items[item.memory_id] = item
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Failed to load hybrid memory from %s: %s", self._storage_file, exc)

    def _persist(self, item: HybridMemoryItem) -> None:
        """Append item to JSONL file with rotation if size limit exceeded."""
        data = asdict(item)
        if isinstance(data.get("created_at"), datetime):
            data["created_at"] = data["created_at"].isoformat()
        if data.get("embedding") is None:
            data.pop("embedding", None)

        max_size_bytes = self._config.max_jsonl_size_mb * 1024 * 1024

        if os.path.exists(self._storage_file) and os.path.getsize(self._storage_file) >= max_size_bytes:
            self._rotate_jsonl()

        with open(self._storage_file, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _rotate_jsonl(self) -> None:
        """Rotate current JSONL file to compressed form and start new file."""
        if not os.path.exists(self._storage_file):
            return

        import time

        rotated_name = f"{self._storage_file}.{int(time.time())}.gz"
        try:
            with open(self._storage_file, "rb") as f_in, gzip.open(rotated_name, "wb", compresslevel=6) as f_out:
                while True:
                    chunk = f_in.read(8192)
                    if not chunk:
                        break
                    f_out.write(chunk)
            os.remove(self._storage_file)
            logger.info("Rotated hybrid memory JSONL to %s", rotated_name)
        except OSError as exc:
            logger.warning("Failed to rotate hybrid memory JSONL: %s", exc)
            return

        self._cleanup_rotated_files()

    def _cleanup_rotated_files(self) -> None:
        """Remove old rotated files beyond max_rotated_files limit."""
        dir_path = os.path.dirname(self._storage_file) or "."
        base_name = os.path.basename(self._storage_file)
        prefix = f"{base_name}."

        try:
            rotated_files: list[tuple[float, str]] = []
            for fname in os.listdir(dir_path):
                if fname.startswith(prefix):
                    fpath = os.path.join(dir_path, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                        rotated_files.append((mtime, fpath))
                    except OSError:
                        continue

            rotated_files.sort(key=lambda x: x[0], reverse=True)

            for _mtime, fpath in rotated_files[self._config.max_rotated_files :]:
                try:
                    os.remove(fpath)
                    logger.debug("Removed old rotated file: %s", fpath)
                except OSError as exc:
                    logger.warning("Failed to remove rotated file %s: %s", fpath, exc)
        except OSError as exc:
            logger.warning("Failed to list rotated files: %s", exc)

    async def add(
        self,
        text: str,
        *,
        entities: tuple[str, ...] | None = None,
        relationships: tuple[tuple[str, str, str], ...] | None = None,
        importance: int = 5,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add a memory item to hybrid storage.

        Args:
            text: The memory text content.
            entities: Optional tuple of entity names mentioned in the text.
            relationships: Optional tuple of (subject, predicate, object) triples.
            importance: Importance score (1-10).
            metadata: Optional additional metadata.

        Returns:
            The memory_id of the added item.
        """
        memory_id = f"hybrid_{uuid.uuid4().hex[:16]}"

        # Compute embedding if vector is enabled
        embedding: tuple[float, ...] | None = None
        if self._config.enable_vector:
            emb_list = await self._compute_embedding(text)
            if emb_list is not None:
                embedding = tuple(emb_list)

        # Create item
        item = HybridMemoryItem(
            memory_id=memory_id,
            text=text,
            importance=max(1, min(10, importance)),
            created_at=datetime.now(timezone.utc),
            embedding=embedding,
            entities=entities or (),
            relationships=relationships or (),
            metadata=metadata or {},
        )

        async with self._lock:
            self._items[memory_id] = item

        # Persist
        self._persist(item)

        # Add to vector store
        if self._config.enable_vector and embedding is not None:
            await self._vector.add(memory_id, text, embedding)

        # Add to full-text store
        if self._config.enable_fulltext:
            await self._fulltext.add(memory_id, text, item.metadata)

        # Add to graph store
        if self._config.enable_graph and (entities or relationships):
            await self._graph.add(memory_id, entities or (), relationships or ())

        logger.debug("Added hybrid memory item: %s (importance=%d)", memory_id[:12], importance)
        return memory_id

    async def _empty_vector_search(self) -> list[tuple[str, float]]:
        """Return empty list when vector search is disabled."""
        return []

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_importance: int = 1,
        entity_filter: list[str] | None = None,
    ) -> list[SearchResult]:
        """Search hybrid memory using fused scoring.

        Args:
            query: Search query text.
            top_k: Maximum number of results to return.
            min_importance: Minimum importance score filter.
            entity_filter: Optional list of entities to filter by.

        Returns:
            List of SearchResult sorted by fused score (descending).
        """
        w_vector, w_fulltext, w_graph = self._config.fusion_weights

        # Parallel search across all enabled layers
        search_tasks = []

        query_embedding: tuple[float, ...] | None = None
        if self._config.enable_vector:
            emb_list = await self._compute_embedding(query)
            if emb_list is not None:
                query_embedding = tuple(emb_list)
                search_tasks.append(
                    self._vector.search(
                        query_embedding,
                        self._config.vector_top_k,
                    )
                )
            else:
                search_tasks.append(self._empty_vector_search())

        if self._config.enable_fulltext:
            search_tasks.append(self._fulltext.search(query, self._config.fulltext_top_k))

        if self._config.enable_graph:
            filter_entities = entity_filter or self._extract_entities(query)
            search_tasks.append(self._graph.search(filter_entities, self._config.graph_top_k))

        # Execute searches in parallel
        results_list = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Extract scores by memory_id
        vector_scores: dict[str, float] = {}
        fulltext_scores: dict[str, float] = {}
        graph_scores: dict[str, float] = {}

        def extract_scores(results: list[Any], idx: int) -> list[tuple[str, float]]:
            if idx < len(results) and isinstance(results[idx], list):
                return list(results[idx])
            return []

        if self._config.enable_vector:
            for mid, score in extract_scores(results_list, 0):
                vector_scores[mid] = score

        if self._config.enable_fulltext:
            idx = 1 if self._config.enable_vector else 0
            for mid, score in extract_scores(results_list, idx):
                fulltext_scores[mid] = score

        if self._config.enable_graph:
            idx = 0
            if self._config.enable_vector:
                idx += 1
            if self._config.enable_fulltext:
                idx += 1
            for mid, score in extract_scores(results_list, idx):
                graph_scores[mid] = score

        # Fuse scores
        candidate_ids = set(vector_scores) | set(fulltext_scores) | set(graph_scores)
        fused_results: list[SearchResult] = []

        for memory_id in candidate_ids:
            async with self._lock:
                if memory_id not in self._items:
                    continue
                item = self._items[memory_id]

            # Skip items below importance threshold
            if item.importance < min_importance:
                continue

            # Get normalized scores (0-1 range)
            v_score = vector_scores.get(memory_id, 0.0)
            ft_score = fulltext_scores.get(memory_id, 0.0)
            g_score = graph_scores.get(memory_id, 0.0)

            # Weighted fusion
            fused = (w_vector * v_score) + (w_fulltext * ft_score) + (w_graph * g_score)

            # Skip if below minimum fusion threshold
            if fused < self._config.min_fusion_score:
                continue

            result = SearchResult(
                memory_id=memory_id,
                text=item.text,
                importance=item.importance,
                created_at=item.created_at,
                vector_score=v_score,
                fulltext_score=ft_score,
                graph_score=g_score,
                fused_score=fused,
                metadata=item.metadata,
            )
            fused_results.append(result)

        # Sort by fused score descending
        fused_results.sort(key=lambda x: x.fused_score, reverse=True)

        return fused_results[:top_k]

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        """Retrieve a specific memory item by ID.

        Args:
            memory_id: The memory ID to retrieve.

        Returns:
            Dictionary representation of the item, or None if not found.
        """
        async with self._lock:
            if memory_id not in self._items:
                return None
            item = self._items[memory_id]

        return {
            "memory_id": item.memory_id,
            "text": item.text,
            "importance": item.importance,
            "created_at": item.created_at.isoformat(),
            "entities": list(item.entities),
            "relationships": [list(r) for r in item.relationships],
            "metadata": item.metadata,
        }

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory item from all storage layers.

        Args:
            memory_id: The memory ID to delete.

        Returns:
            True if the item was deleted, False if not found.
        """
        async with self._lock:
            if memory_id not in self._items:
                return False
            del self._items[memory_id]

        # Delete from all backends
        if self._config.enable_vector:
            await self._vector.delete(memory_id)
        if self._config.enable_fulltext:
            await self._fulltext.delete(memory_id)
        if self._config.enable_graph:
            await self._graph.delete(memory_id)

        # Note: JSONL compaction should be done periodically
        logger.debug("Deleted hybrid memory item: %s", memory_id[:12])
        return True

    def get_stats(self) -> dict[str, Any]:
        """Get hybrid memory statistics.

        Returns:
            Dictionary with statistics from all layers.
        """
        total_items = len(self._items)
        avg_importance = sum(i.importance for i in self._items.values()) / total_items if total_items > 0 else 0.0

        stats: dict[str, Any] = {
            "total_items": total_items,
            "avg_importance": round(avg_importance, 2),
            "config": {
                "enable_vector": self._config.enable_vector,
                "enable_fulltext": self._config.enable_fulltext,
                "enable_graph": self._config.enable_graph,
                "fusion_weights": self._config.fusion_weights,
            },
        }

        return stats

    def _extract_entities(self, text: str) -> list[str]:
        """Extract potential entities from text using simple heuristics.

        Looks for capitalized phrases, file paths, and other patterns.
        """
        entities: list[str] = []

        # Capitalized phrases (potential proper nouns)
        capitalized = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
        entities.extend(capitalized)

        # File paths
        file_paths = re.findall(r"[\w\-./]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh)\b", text)
        entities.extend(file_paths)

        # CamelCase identifiers
        camel_case = re.findall(r"\b[a-z]+[A-Z][a-zA-Z]*\b", text)
        entities.extend(camel_case)

        # Remove duplicates while preserving order
        seen: set[str] = set()
        unique_entities: list[str] = []
        for e in entities:
            if e.lower() not in seen:
                seen.add(e.lower())
                unique_entities.append(e)

        return unique_entities

    async def store(self, memory: Memory) -> str:
        """Store a memory item to all three storage layers.

        Args:
            memory: Memory item to store.

        Returns:
            The memory_id of the stored item.
        """
        # Ensure memory_id is not None (Memory.__post_init__ should set it, but mypy doesn't know)
        if memory.memory_id is None:
            raise ValueError("memory.memory_id cannot be None")

        # Compute embedding
        emb_list = await self._compute_embedding(memory.content)
        embedding: tuple[float, ...] | None = tuple(emb_list) if emb_list else None

        # Create item (use HybridMemoryItem internally for compatibility)
        item = HybridMemoryItem(
            memory_id=memory.memory_id,
            text=memory.content,
            importance=memory.importance,
            created_at=memory.created_at or datetime.now(timezone.utc),
            embedding=embedding,
            entities=memory.tags,  # Use tags as entities
            relationships=(),
            metadata=memory.metadata,
        )

        async with self._lock:
            self._items[memory.memory_id] = item

        # Add to vector store
        if self._config.enable_vector and embedding is not None:
            await self._vector.add(memory.memory_id, memory.content, embedding)

        # Add to full-text store
        if self._config.enable_fulltext:
            await self._fulltext.add(memory.memory_id, memory.content, memory.metadata)

        # Add to graph store
        entities = self._extract_entities(memory.content)
        if self._config.enable_graph and entities:
            relationships: tuple[tuple[str, str, str], ...] = ()
            await self._graph.add(memory.memory_id, tuple(entities), relationships)

        logger.debug("Stored memory: %s (importance=%d)", memory.memory_id[:12], memory.importance)
        return memory.memory_id

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[MemoryResult]:
        """Retrieve memories using hybrid search across all layers.

        Uses Reciprocal Rank Fusion to combine results from:
        - Vector similarity search
        - Full-text keyword search
        - Knowledge graph traversal

        Args:
            query: Search query string.
            top_k: Number of results to return.

        Returns:
            List of MemoryResult sorted by fused relevance score.
        """
        # Compute query embedding
        emb_list = await self._compute_embedding(query)
        query_embedding: tuple[float, ...] | None = tuple(emb_list) if emb_list else None

        # Parallel search across all layers
        search_tasks = []

        if self._config.enable_vector and query_embedding:
            search_tasks.append(self._vector.search(query_embedding, top_k * 2))
        else:
            search_tasks.append(self._empty_vector_search())

        if self._config.enable_fulltext:
            search_tasks.append(self._fulltext.search(query, top_k * 2))

        if self._config.enable_graph:
            filter_entities = self._extract_entities(query)
            search_tasks.append(self._graph.search(filter_entities, top_k * 2))

        # Execute searches in parallel
        results_list = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Extract scores by memory_id
        vector_scores: dict[str, float] = {}
        fulltext_scores: dict[str, float] = {}
        graph_scores: dict[str, float] = {}

        def extract_scores(results: list[Any], idx: int) -> list[tuple[str, float]]:
            if idx < len(results) and isinstance(results[idx], list):
                return list(results[idx])
            return []

        if self._config.enable_vector:
            for mid, score in extract_scores(results_list, 0):
                vector_scores[mid] = score

        if self._config.enable_fulltext:
            idx = 1 if self._config.enable_vector else 0
            for mid, score in extract_scores(results_list, idx):
                fulltext_scores[mid] = score

        if self._config.enable_graph:
            idx = 0
            if self._config.enable_vector:
                idx += 1
            if self._config.enable_fulltext:
                idx += 1
            for mid, score in extract_scores(results_list, idx):
                graph_scores[mid] = score

        # Fuse results using hybrid merge
        return await self._hybrid_merge(vector_scores, fulltext_scores, graph_scores, top_k)

    async def _hybrid_merge(
        self,
        vector_scores: dict[str, float],
        fulltext_scores: dict[str, float],
        graph_scores: dict[str, float],
        top_k: int,
    ) -> list[MemoryResult]:
        """Merge results from all layers using Reciprocal Rank Fusion.

        RRF formula: score(d) = sum(1 / (k + rank(d))) for each result d
        where k is a constant (typically 60) and rank(d) is the rank in each list.

        Args:
            vector_scores: Scores from vector search.
            fulltext_scores: Scores from full-text search.
            graph_scores: Scores from graph search.
            top_k: Number of results to return.

        Returns:
            List of MemoryResult sorted by fused score.
        """
        k = 60
        w_vector, w_fulltext, w_graph = self._config.fusion_weights

        def compute_rrf(scores: dict[str, float], weight: float) -> dict[str, float]:
            """Compute weighted RRF scores from a score dict."""
            result: dict[str, float] = {}
            sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (mid, _score) in enumerate(sorted_items):
                rrf = 1.0 / (k + rank + 1)
                result[mid] = rrf * weight
            return result

        # Compute RRF scores for each layer
        vector_rrf = compute_rrf(vector_scores, w_vector)
        fulltext_rrf = compute_rrf(fulltext_scores, w_fulltext)
        graph_rrf = compute_rrf(graph_scores, w_graph)

        # Merge all scores
        all_ids = set(vector_rrf) | set(fulltext_rrf) | set(graph_rrf)
        final_scores: list[tuple[float, str]] = []

        for memory_id in all_ids:
            fused = vector_rrf.get(memory_id, 0.0) + fulltext_rrf.get(memory_id, 0.0) + graph_rrf.get(memory_id, 0.0)
            final_scores.append((fused, memory_id))

        # Sort by fused score descending
        final_scores.sort(key=lambda x: x[0], reverse=True)

        # Convert to MemoryResult
        results: list[MemoryResult] = []
        for _fused_score, memory_id in final_scores[:top_k]:
            async with self._lock:
                if memory_id not in self._items:
                    continue
                item = self._items[memory_id]

            # Determine which layers matched
            sources: set[str] = set()
            if memory_id in vector_scores and vector_scores[memory_id] > 0:
                sources.add("vector")
            if memory_id in fulltext_scores and fulltext_scores[memory_id] > 0:
                sources.add("fulltext")
            if memory_id in graph_scores and graph_scores[memory_id] > 0:
                sources.add("graph")
            source_str = "+".join(sorted(sources)) if len(sources) > 1 else next(iter(sources), "unknown")

            # Get primary score from best layer
            primary_score = max(
                vector_scores.get(memory_id, 0.0),
                fulltext_scores.get(memory_id, 0.0),
                graph_scores.get(memory_id, 0.0),
            )

            results.append(
                MemoryResult(
                    memory_id=memory_id,
                    content=item.text,
                    score=primary_score,
                    importance=item.importance,
                    tags=item.entities,
                    metadata=item.metadata,
                    created_at=item.created_at,
                    source=source_str,
                )
            )

        return results


# Type annotation
HybridMemory.__protocol__ = SemanticMemoryPort  # type: ignore[attr-defined]


__all__ = [
    "FullTextStoreBackend",
    "GraphStoreBackend",
    "HybridMemory",
    "HybridMemoryConfig",
    "HybridMemoryItem",
    "Memory",
    "MemoryResult",
    "ScoredResult",
    "SearchResult",
    "VectorStoreBackend",
]
