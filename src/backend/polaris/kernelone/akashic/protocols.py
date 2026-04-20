"""Akashic Nexus: Memory Subsystem Protocols.

Defines the stable port surface for the Akashic memory engine.
Following DIP: Abstractions (Protocols) over concretions.

Architecture:
    - MemoryManagerPort: Unified orchestration interface
    - SemanticCachePort: Embedding-based similarity caching
    - WorkingMemoryPort: Sliding window context management
    - EpisodicMemoryPort: Session-level history storage
    - TierCoordinatorPort: Cross-tier promote/demote orchestration
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

# -----------------------------------------------------------------------------
# Generic Type Variables


T_memory_item = TypeVar("T_memory_item", bound="MemoryItemBase")
T_snapshot = TypeVar("T_snapshot", bound="SnapshotBase")


@dataclass(frozen=True)
class SnapshotBase:
    """Base class for immutable memory snapshots."""

    id: str
    created_at: datetime


@dataclass(frozen=True)
class MemoryItemBase(SnapshotBase):
    """Base class for memory items across tiers."""

    text: str
    importance: int  # 1-10
    embedding: tuple[float, ...] | None = None
    tier: str = "working"  # working | episodic | semantic


# -----------------------------------------------------------------------------
# Tier Coordinator Types


@dataclass(frozen=True)
class PromotionCandidate:
    """A memory item candidate for promotion to a higher tier."""

    item_id: str
    source_tier: str
    target_tier: str
    importance: int
    text_preview: str  # First 100 chars for logging
    reason: str  # "session_end" | "importance_threshold" | "explicit_request"


@dataclass(frozen=True)
class DemotionCandidate:
    """A memory item candidate for demotion to a lower tier."""

    item_id: str
    source_tier: str
    target_tier: str
    reason: str  # "token_budget" | "staleness" | "explicit_request"


# -----------------------------------------------------------------------------
# Working Memory Types


@dataclass
class WorkingMemoryConfig:
    """Configuration for WorkingMemoryWindow."""

    max_tokens: int = 32_000
    soft_watermark_pct: float = 0.75  # Trigger background compression
    hard_watermark_pct: float = 0.90  # Trigger emergency compression
    head_preserve_tokens: int = 8_000  # System prompt + task goal
    tail_preserve_count: int = 3  # Recent N turns
    middle_compress_enabled: bool = True


@dataclass
class WorkingMemorySnapshot:
    """Immutable snapshot of working memory state."""

    total_tokens: int
    chunk_count: int
    head_tokens: int
    middle_tokens: int
    tail_tokens: int
    usage_ratio: float  # total_tokens / max_tokens
    compression_triggered: str | None  # None | "soft" | "hard"


# -----------------------------------------------------------------------------
# Semantic Cache Types

# Supported embedding models for semantic cache
AVAILABLE_EMBEDDING_MODELS: tuple[str, ...] = (
    "nomic-embed-text",  # Default - Nomic AI
    "mxbai-embed-large",  # Mixedbread AI large
    "bge-m3",  # BGE M3 embedding
    "snowflake-arctic-embed",  # Snowflake Arctic
    "ggml-model-qwen2",  # Qwen2 via GGML
    # Ollama models (require Ollama server)
    "llama3",  # Llama 3
    "mistral",  # Mistral
    "nomic-embed-text:latest",  # With explicit tag
)


@dataclass(frozen=True)
class SemanticCacheEntry:
    """An entry in the semantic cache."""

    query_hash: str
    embedding: tuple[float, ...]
    response: str
    created_at: datetime
    hit_count: int = 0
    last_accessed: datetime | None = None


@dataclass
class SemanticCacheConfig:
    """Configuration for SemanticCacheInterceptor.

    Attributes:
        similarity_threshold: Cosine similarity threshold for cache hits (0.0-1.0).
            Higher values = stricter matching. Default 0.92 works well for semantic duplicates.
        max_entries: Maximum number of cache entries. Default 1024.
        ttl_seconds: Time-to-live for cache entries in seconds. Default 3600 (1 hour).
        embedding_model: Embedding model to use. If None, uses KERNELONE_EMBEDDING_MODEL
            env var or defaults to "nomic-embed-text".

    Example::

        config = SemanticCacheConfig(
            similarity_threshold=0.92,
            max_entries=1024,
            ttl_seconds=3600,
            embedding_model="mxbai-embed-large",
        )
    """

    similarity_threshold: float = 0.92
    max_entries: int = 1024
    ttl_seconds: float = 3600.0  # 1 hour default
    embedding_model: str | None = None  # Uses default if None


# -----------------------------------------------------------------------------
# Compression Daemon Types


@dataclass
class CompressionTask:
    """A background compression task."""

    task_id: str
    priority: int  # 0 = highest
    source_tier: str
    target_tokens: int
    created_at: datetime
    status: str = "pending"  # pending | running | completed | failed


# -----------------------------------------------------------------------------
# Port Protocols


@runtime_checkable
class WorkingMemoryPort(Protocol):
    """Protocol for working memory (short-term context window) operations."""

    def push(
        self,
        role: str,
        content: str,
        *,
        importance: int = 5,
        turn_index: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Push a message into the working memory window.

        Returns the ID of the inserted chunk.
        """
        ...

    def get_snapshot(self) -> WorkingMemorySnapshot:
        """Get current working memory state snapshot."""
        ...

    def get_messages(
        self,
        *,
        max_tokens: int | None = None,
        include_role: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get messages from working memory, optionally filtered by token budget."""
        ...

    def promote_to_episodic(
        self,
        item_id: str,
        reason: str,
    ) -> bool:
        """Promote an item from working to episodic memory."""
        ...

    def promote_to_semantic(
        self,
        item_id: str,
        reason: str,
    ) -> bool:
        """Promote an item from working to semantic memory."""
        ...

    def get_promotion_queue(self) -> list[str]:
        """Get the episodic promotion queue (item_ids pending promotion)."""
        ...

    def get_semantic_promotion_queue(self) -> list[str]:
        """Get the semantic promotion queue (item_ids pending semantic promotion)."""
        ...

    def clear_promotion_queue(self) -> None:
        """Clear the episodic promotion queue."""
        ...

    def clear_semantic_promotion_queue(self) -> None:
        """Clear the semantic promotion queue."""
        ...

    def reset_turn(self) -> None:
        """Reset turn counter at the start of a new turn."""
        ...

    def clear(self) -> None:
        """Clear the working memory window."""
        ...


@runtime_checkable
class SemanticCachePort(Protocol):
    """Protocol for semantic (embedding-based) cache operations."""

    async def get_or_compute(
        self,
        query: str,
        compute_fn: Callable[[], Any],
        *,
        ttl_seconds: float | None = None,
    ) -> Any:
        """Get cached response or compute and cache new one.

        Uses embedding similarity for cache key matching.
        """
        ...

    async def invalidate(self, query_hash: str) -> bool:
        """Invalidate a cache entry by hash.

        Returns True if entry existed and was removed.
        """
        ...

    async def clear(self) -> int:
        """Clear all cache entries.

        Returns the number of entries cleared.
        """
        ...

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics (hits, misses, size, etc.)."""
        ...


@runtime_checkable
class EpisodicMemoryPort(Protocol):
    """Protocol for episodic (session-level) memory operations."""

    async def store_turn(
        self,
        turn_index: int,
        messages: list[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a complete turn's messages.

        Returns the turn ID.
        """
        ...

    async def get_turn(self, turn_index: int) -> dict[str, Any] | None:
        """Retrieve a specific turn by index."""
        ...

    async def get_range(
        self,
        start_turn: int,
        end_turn: int,
    ) -> list[dict[str, Any]]:
        """Retrieve a range of turns."""
        ...

    async def seal_episode(
        self,
        session_id: str,
        summary: str,
    ) -> str:
        """Seal an episode (session) with a summary.

        Returns the episode ID.
        """
        ...

    async def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        """Retrieve a specific episode by ID."""
        ...

    async def get_recent_episodes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get the most recent episodes."""
        ...

    def get_status(self) -> dict[str, Any]:
        """Get episodic memory status (sync snapshot)."""
        ...


@runtime_checkable
class SemanticMemoryPort(Protocol):
    """Protocol for semantic (long-term vector) memory operations."""

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        importance: int = 5,
    ) -> str:
        """Add a memory to semantic storage.

        Returns the memory ID.
        """
        ...

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_importance: int = 1,
    ) -> list[tuple[str, float]]:  # (memory_id, similarity_score)
        """Search semantic memory by query text."""
        ...

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        """Retrieve a specific memory by ID."""
        ...

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory item by ID.

        Returns True if item existed and was deleted.
        """
        ...

    def get_stats(self) -> dict[str, Any]:
        """Get semantic memory statistics."""
        ...


@runtime_checkable
class TierCoordinatorPort(Protocol):
    """Protocol for cross-tier memory promotion/demote orchestration."""

    async def evaluate_promotions(
        self,
        candidates: list[PromotionCandidate],
    ) -> list[PromotionCandidate]:
        """Evaluate and filter promotion candidates.

        Returns only the candidates that should actually be promoted.
        """
        ...

    async def promote(
        self,
        candidate: PromotionCandidate,
    ) -> bool:
        """Promote a memory item to the target tier.

        Returns True if promotion succeeded.
        """
        ...

    async def demote(
        self,
        candidate: DemotionCandidate,
    ) -> bool:
        """Demote a memory item to the target tier.

        Returns True if demotion succeeded.
        """
        ...

    async def promote_many(
        self,
        candidates: list[PromotionCandidate],
    ) -> list[str]:
        """Promote multiple memory items to their target tiers.

        Returns list of item_ids that were successfully promoted.
        """
        ...

    async def promote_with_rollback(
        self,
        candidates: list[PromotionCandidate],
    ) -> tuple[list[str], list[str]]:
        """Promote multiple items with transaction semantics.

        If any promotion fails, rolls back all already-promoted items.
        Returns (successful_ids, failed_ids).
        """
        ...

    async def sync_tiers(self) -> dict[str, int]:
        """Synchronize all tiers (garbage collection, consistency).

        Returns a dict of tier_name -> items_processed.
        """
        ...


@runtime_checkable
class MemoryManagerPort(Protocol):
    """Unified interface for the Akashic Memory Manager.

    This is the main entry point for memory operations.
    Coordinates WorkingMemory, EpisodicMemory, SemanticMemory, and TierCoordinator.
    """

    # Tier accessors
    @property
    def working_memory(self) -> WorkingMemoryPort: ...
    @property
    def semantic_cache(self) -> SemanticCachePort: ...
    @property
    def episodic_memory(self) -> EpisodicMemoryPort: ...
    @property
    def semantic_memory(self) -> SemanticMemoryPort: ...
    @property
    def tier_coordinator(self) -> TierCoordinatorPort: ...

    # Unified operations
    async def initialize(self) -> None:
        """Initialize the memory manager and all sub-systems."""
        ...

    async def shutdown(self) -> None:
        """Gracefully shutdown the memory manager."""
        ...

    def get_status(self) -> dict[str, Any]:
        """Get comprehensive status of all memory tiers."""
        ...

    # Session lifecycle
    async def begin_session(self, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Begin a new memory session.

        Call this at the start of a user session or task.
        Resets turn counter and initializes session context.
        """
        ...

    async def end_turn(self) -> None:
        """End the current turn.

        Call this at the end of each turn to:
        - Increment turn counter in working memory
        - Trigger tier sync if needed
        - Update recency scores for excluded chunks
        """
        ...

    async def end_session(self, summary: str | None = None) -> str:
        """End the current memory session.

        Call this at the end of a user session or task.
        Triggers episode sealing and final promotion flush.

        Args:
            summary: Optional session summary for the sealed episode.

        Returns:
            The episode_id of the sealed episode.
        """
        ...


__all__ = [
    "CompressionTask",
    "DemotionCandidate",
    "EpisodicMemoryPort",
    "MemoryItemBase",
    "MemoryManagerPort",
    "PromotionCandidate",
    "SemanticCacheConfig",
    "SemanticCacheEntry",
    "SemanticCachePort",
    "SemanticMemoryPort",
    # Types
    "SnapshotBase",
    "TierCoordinatorPort",
    "WorkingMemoryConfig",
    # Ports
    "WorkingMemoryPort",
    "WorkingMemorySnapshot",
]
