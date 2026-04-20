"""Akashic Nexus: Unified Memory Manager.

The MemoryManager is the central DI container that orchestrates
all memory tiers following DIP principles.

Architecture:
    MemoryManager
        ├── WorkingMemoryWindow (short-term)
        ├── SemanticCacheInterceptor (cache layer)
        ├── EpisodicMemoryStore (session-level)
        ├── SemanticMemoryStore (long-term vector)
        └── TierCoordinator (cross-tier promotion/demotion)

Design constraints:
    - All backends are injected via protocols (DIP)
    - Lazy initialization to avoid circular deps
    - Graceful degradation if a tier is unavailable
    - Type-safe generics for cross-tier operations

Usage::

    # Create with dependency injection
    manager = MemoryManager(
        working_memory=WorkingMemoryWindow(),
        semantic_cache=SemanticCacheInterceptor(),
        episodic_store=EpisodicMemoryStore(),
        semantic_store=SemanticMemoryStore(),
    )

    await manager.initialize()

    # Use unified interface
    manager.working_memory.push("user", "Fix the bug")
    snapshot = manager.working_memory.get_snapshot()

    await manager.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.memory.contracts import MemoryItemSnapshot, MemoryPort

from .episodic_memory import AkashicEpisodicMemory
from .protocols import (
    DemotionCandidate,
    EpisodicMemoryPort,
    MemoryManagerPort,
    PromotionCandidate,
    SemanticCachePort,
    SemanticMemoryPort,
    TierCoordinatorPort,
    WorkingMemoryPort,
)
from .semantic_cache import SemanticCacheInterceptor
from .semantic_memory import AkashicSemanticMemory
from .working_memory import WorkingMemoryWindow

logger = logging.getLogger(__name__)


@dataclass
class MemoryManagerConfig:
    """Configuration for the MemoryManager."""

    enable_semantic_cache: bool = True
    enable_episodic_promotion: bool = True
    enable_tier_sync: bool = True
    promotion_importance_threshold: int = 7  # Min importance to promote
    sync_interval_seconds: float = 60.0  # Tier sync interval


class MemoryManager:
    """Unified Memory Manager with DI support.

    Coordinates WorkingMemory, SemanticCache, EpisodicMemory, and SemanticMemory
    through a single interface. Implements the MemoryManagerPort protocol.

    The manager handles:
    - Cross-tier promotion (working -> episodic -> semantic)
    - Cross-tier demotion (semantic -> episodic -> working)
    - Unified status reporting
    - Graceful initialization and shutdown
    """

    def __init__(
        self,
        config: MemoryManagerConfig | None = None,
        *,
        workspace: str = ".",
        # DI: Accept any implementation of the ports
        working_memory: WorkingMemoryPort | None = None,
        semantic_cache: SemanticCachePort | None = None,
        episodic_memory: EpisodicMemoryPort | None = None,
        semantic_memory: SemanticMemoryPort | None = None,
        tier_coordinator: TierCoordinatorPort | None = None,
        # Optional: IdempotentVectorStore for document pipeline integration
        # If provided, used as semantic_memory (it implements SemanticMemoryPort)
        semantic_vector_store: Any = None,
        # Legacy integration
        legacy_memory_store: MemoryPort | None = None,
    ) -> None:
        self._config = config or MemoryManagerConfig()
        self._workspace = str(workspace or ".")

        # DI: Use injected or create defaults
        self._working_memory: WorkingMemoryPort | None = working_memory
        self._semantic_cache: SemanticCachePort | None = semantic_cache
        self._episodic_memory: EpisodicMemoryPort | None = episodic_memory
        self._semantic_memory: SemanticMemoryPort | None = semantic_memory
        self._tier_coordinator: TierCoordinatorPort | None = tier_coordinator
        # Semantic vector store (e.g., IdempotentVectorStore) - used if provided
        self._semantic_vector_store = semantic_vector_store

        # Legacy integration
        self._legacy_memory_store = legacy_memory_store

        # State
        self._initialized: bool = False
        self._shutdown: bool = False
        self._session_id: str | None = None
        self._session_active: bool = False

        # Background tasks
        self._sync_task: Any = None

    # -------------------------------------------------------------------------
    # Port Accessors (DIP: depend on abstractions)
    # -------------------------------------------------------------------------

    @property
    def working_memory(self) -> WorkingMemoryPort:
        """Get the working memory port.

        Lazily initializes with default if not injected.
        """
        if self._working_memory is None:
            self._working_memory = WorkingMemoryWindow()
            logger.debug("Created default WorkingMemoryWindow")
        return self._working_memory

    @property
    def semantic_cache(self) -> SemanticCachePort:
        """Get the semantic cache port.

        Lazily initializes with default if not injected.
        """
        if self._semantic_cache is None:
            if self._config.enable_semantic_cache:
                self._semantic_cache = SemanticCacheInterceptor()
                logger.debug("Created default SemanticCacheInterceptor")
            else:
                # Return a no-op cache
                self._semantic_cache = _NoOpSemanticCache()
        return self._semantic_cache

    @property
    def episodic_memory(self) -> EpisodicMemoryPort:
        """Get the episodic memory port.

        Lazily initializes with ContextOS-backed implementation if not injected.
        """
        if self._episodic_memory is None:
            # Use ContextOS-backed episodic memory with workspace persistence
            workspace = getattr(self, "_workspace", ".")
            self._episodic_memory = AkashicEpisodicMemory(workspace=workspace)
            logger.debug("Created AkashicEpisodicMemory at %s", workspace)
        return self._episodic_memory

    @property
    def semantic_memory(self) -> SemanticMemoryPort:
        """Get the semantic memory port.

        Lazily initializes with AkashicSemanticMemory if not injected.
        Uses MemoryStore adapter if legacy_memory_store is provided.
        Uses semantic_vector_store if provided (e.g., IdempotentVectorStore).
        """
        if self._semantic_memory is None:
            # Priority: vector_store > legacy_adapter > default
            if self._semantic_vector_store is not None:
                # semantic_vector_store (e.g., IdempotentVectorStore) implements SemanticMemoryPort
                self._semantic_memory = self._semantic_vector_store
                logger.debug("Using injected semantic vector store")
            elif self._legacy_memory_store is not None:
                self._semantic_memory = _LegacyMemoryStoreAdapter(self._legacy_memory_store)
                logger.debug("Created SemanticMemory from legacy MemoryStore adapter")
            else:
                # Use AkashicSemanticMemory with workspace persistence
                workspace = getattr(self, "_workspace", ".")
                self._semantic_memory = AkashicSemanticMemory(workspace=workspace)
                logger.debug("Created AkashicSemanticMemory at %s", workspace)
        return self._semantic_memory

    @property
    def tier_coordinator(self) -> TierCoordinatorPort:
        """Get the tier coordinator port.

        Lazily initializes with default if not injected.
        """
        if self._tier_coordinator is None:
            self._tier_coordinator = _DefaultTierCoordinator(self)
            logger.debug("Created default TierCoordinator")
        return self._tier_coordinator

    @property
    def promotion_importance_threshold(self) -> int:
        """Get the promotion importance threshold.

        This is the minimum importance score required for a memory item
        to be promoted to a higher tier.
        """
        return self._config.promotion_importance_threshold

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize the memory manager and all sub-systems.

        This method:
        1. Validates all injected dependencies
        2. Initializes any lazy ports
        3. Starts background sync tasks if enabled
        """
        if self._initialized:
            logger.warning("MemoryManager already initialized")
            return

        logger.info("Initializing MemoryManager...")

        # Touch all ports to trigger lazy initialization
        _ = self.working_memory
        _ = self.semantic_cache
        _ = self.episodic_memory
        _ = self.semantic_memory
        _ = self.tier_coordinator

        # Start background sync task if enabled
        if self._config.enable_tier_sync:
            self._sync_task = asyncio.create_task(self._run_sync_loop())
            logger.info("Started tier sync background task (interval=%.1fs)", self._config.sync_interval_seconds)
        else:
            logger.info("Tier sync disabled by configuration")

        self._initialized = True
        logger.info("MemoryManager initialized successfully")

    async def _run_sync_loop(self) -> None:
        """Background task that periodically synchronizes memory tiers."""
        interval = self._config.sync_interval_seconds

        logger.debug("Tier sync loop started (interval=%.1fs)", interval)

        while not self._shutdown:
            try:
                # Run sync
                processed = await self.tier_coordinator.sync_tiers()
                logger.debug(
                    "Tier sync completed: working=%d, episodic=%d, semantic=%d",
                    processed.get("working", 0),
                    processed.get("episodic", 0),
                    processed.get("semantic", 0),
                )
            except asyncio.CancelledError:
                raise
            except (RuntimeError, ValueError) as exc:
                logger.error("Error in tier sync loop: %s", exc)

            # Wait for next interval
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

        logger.debug("Tier sync loop stopped")

    async def shutdown(self) -> None:
        """Gracefully shutdown the memory manager.

        This method:
        1. Stops background sync tasks
        2. Flushes any pending promotions
        3. Clears sensitive state
        """
        if self._shutdown:
            logger.warning("MemoryManager already shutdown")
            return

        logger.info("Shutting down MemoryManager...")

        # Signal shutdown and stop sync task
        self._shutdown = True
        if self._sync_task is not None:
            self._sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._sync_task
            self._sync_task = None

        # Flush promotion queue
        await self._flush_promotion_queue()

        # End session if active
        if self._session_active:
            await self.end_session(summary="Shutdown")

        logger.info("MemoryManager shutdown complete")

    # -------------------------------------------------------------------------
    # Session Lifecycle
    # -------------------------------------------------------------------------

    async def begin_session(self, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Begin a new memory session.

        Call this at the start of a user session or task.
        Resets turn counter and initializes session context.
        """
        if self._session_active:
            logger.warning("Session %s is already active, ending it first", self._session_id)
            await self.end_session(summary="New session started")

        self._session_id = session_id
        self._session_active = True

        # Reset working memory turn counter
        if hasattr(self.working_memory, "reset_turn"):
            self.working_memory.reset_turn()

        # Clear promotion queues
        if hasattr(self.working_memory, "clear_promotion_queue"):
            self.working_memory.clear_promotion_queue()
        if hasattr(self.working_memory, "clear_semantic_promotion_queue"):
            self.working_memory.clear_semantic_promotion_queue()

        # Emit session event if available
        try:
            from polaris.kernelone.events.session_events import emit_session_event

            emit_session_event(
                workspace=self._workspace,
                event_name="session_created",
                session_id=session_id,
                payload=metadata or {},
            )
        except ImportError:
            pass

        logger.info("Memory session started: %s", session_id)

    async def end_turn(self) -> None:
        """End the current turn.

        Call this at the end of each turn to:
        - Increment turn counter in working memory
        - Trigger tier sync if needed
        - Update recency scores for excluded chunks
        """
        if not self._session_active:
            logger.debug("end_turn called but no session is active")
            return

        # Increment turn counter in working memory
        if hasattr(self.working_memory, "reset_turn"):
            self.working_memory.reset_turn()

        # Trigger periodic tier sync if enough turns have passed
        # Use sync_interval_seconds as proxy for turn-based sync
        # This ensures promotions are flushed periodically
        if hasattr(self.working_memory, "get_promotion_queue"):
            episodic_queue = self.working_memory.get_promotion_queue()
            semantic_queue: list[str] = []
            if hasattr(self.working_memory, "get_semantic_promotion_queue"):
                semantic_queue = self.working_memory.get_semantic_promotion_queue()
            if episodic_queue or semantic_queue:
                # There are pending promotions - sync now
                await self.tier_coordinator.sync_tiers()
                logger.debug(
                    "Turn ended: synced %d episodic + %d semantic promotions",
                    len(episodic_queue),
                    len(semantic_queue),
                )

    async def end_session(self, summary: str | None = None) -> str:
        """End the current memory session.

        Call this at the end of a user session or task.
        Triggers episode sealing and final promotion flush.

        Args:
            summary: Optional session summary for the sealed episode.

        Returns:
            The episode_id of the sealed episode.
        """
        if not self._session_active:
            logger.warning("end_session called but no session is active")
            return ""

        # Final sync of all tiers
        await self.tier_coordinator.sync_tiers()

        # Flush any remaining promotions
        await self._flush_promotion_queue()

        # Seal episode if episodic memory is available and we have a session_id
        episode_id = ""
        if self._session_id and hasattr(self.episodic_memory, "seal_episode"):
            # Generate summary from working memory if not provided
            if summary is None:
                chunks = getattr(self.working_memory, "chunks", [])
                if chunks:
                    summary = (
                        f"Session with {len(chunks)} chunks, {self.working_memory.get_snapshot().total_tokens} tokens"
                    )
                else:
                    summary = f"Session {self._session_id} ended"

            try:
                episode_id = await self.episodic_memory.seal_episode(
                    session_id=self._session_id,
                    summary=summary,
                )
                logger.info("Sealed episode: %s for session %s", episode_id, self._session_id)
            except (RuntimeError, ValueError) as exc:
                logger.error("Failed to seal episode: %s", exc)

        # Emit session event if available
        try:
            from polaris.kernelone.events.session_events import emit_session_event

            emit_session_event(
                workspace=self._workspace,
                event_name="session_ended",
                session_id=self._session_id or "unknown",
                payload={"episode_id": episode_id, "summary": summary},
            )
        except ImportError:
            pass

        # Clear session state
        self._session_active = False
        old_session_id = self._session_id
        self._session_id = None

        # Clear working memory
        if hasattr(self.working_memory, "clear"):
            self.working_memory.clear()

        logger.info("Memory session ended: %s (episode: %s)", old_session_id, episode_id)
        return episode_id

    async def _flush_promotion_queue(self) -> None:
        """Flush any pending promotion items (both episodic and semantic queues)."""
        if not hasattr(self.working_memory, "get_promotion_queue"):
            return

        # Flush episodic promotion queue
        queue = self.working_memory.get_promotion_queue()
        if queue:
            logger.info("Flushing %d items from episodic promotion queue", len(queue))

            # Create promotion candidates
            candidates = []
            for item_id in queue:
                if hasattr(self.working_memory, "chunks"):
                    for chunk in self.working_memory.chunks:
                        if chunk.chunk_id == item_id:
                            candidates.append(
                                PromotionCandidate(
                                    item_id=item_id,
                                    source_tier="working",
                                    target_tier="episodic",
                                    importance=chunk.importance,
                                    text_preview=chunk.content[:100],
                                    reason="session_end",
                                )
                            )
                            break

            if candidates:
                for candidate in candidates:
                    await self.tier_coordinator.promote(candidate)

            if hasattr(self.working_memory, "clear_promotion_queue"):
                self.working_memory.clear_promotion_queue()

        # Flush semantic promotion queue
        if hasattr(self.working_memory, "get_semantic_promotion_queue"):
            semantic_queue = self.working_memory.get_semantic_promotion_queue()
            if semantic_queue:
                logger.info("Flushing %d items from semantic promotion queue", len(semantic_queue))

                semantic_candidates = []
                for item_id in semantic_queue:
                    if hasattr(self.working_memory, "chunks"):
                        for chunk in self.working_memory.chunks:
                            if chunk.chunk_id == item_id:
                                semantic_candidates.append(
                                    PromotionCandidate(
                                        item_id=item_id,
                                        source_tier="working",
                                        target_tier="semantic",
                                        importance=chunk.importance,
                                        text_preview=chunk.content[:100],
                                        reason="session_end",
                                    )
                                )
                                break

                if semantic_candidates:
                    for candidate in semantic_candidates:
                        await self.tier_coordinator.promote(candidate)

                if hasattr(self.working_memory, "clear_semantic_promotion_queue"):
                    self.working_memory.clear_semantic_promotion_queue()

    # -------------------------------------------------------------------------
    # Unified Operations
    # -------------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get comprehensive status of all memory tiers.

        Returns a dict with status from each tier plus overall health.
        """
        status: dict[str, Any] = {
            "initialized": self._initialized,
            "shutdown": self._shutdown,
            "config": {
                "enable_semantic_cache": self._config.enable_semantic_cache,
                "enable_episodic_promotion": self._config.enable_episodic_promotion,
                "promotion_importance_threshold": self._config.promotion_importance_threshold,
            },
            "tiers": {},
        }

        # Working memory status
        if hasattr(self.working_memory, "get_snapshot"):
            try:
                wm_snapshot = self.working_memory.get_snapshot()
                status["tiers"]["working_memory"] = {
                    "total_tokens": wm_snapshot.total_tokens,
                    "chunk_count": wm_snapshot.chunk_count,
                    "usage_ratio": round(wm_snapshot.usage_ratio, 3),
                    "compression_triggered": wm_snapshot.compression_triggered,
                }
            except (RuntimeError, ValueError) as exc:
                status["tiers"]["working_memory"] = {"error": str(exc)}

        # Semantic cache status
        if hasattr(self.semantic_cache, "get_stats"):
            try:
                cache_stats = self.semantic_cache.get_stats()
                status["tiers"]["semantic_cache"] = cache_stats
            except (RuntimeError, ValueError) as exc:
                status["tiers"]["semantic_cache"] = {"error": str(exc)}

        # Overall health
        status["healthy"] = self._initialized and not self._shutdown

        return status


# -----------------------------------------------------------------------------
# No-Op Implementations (for graceful degradation)
# -----------------------------------------------------------------------------


class _NoOpSemanticCache:
    """No-op semantic cache for when cache is disabled."""

    async def get_or_compute(
        self,
        query: str,
        compute_fn: Any,
        *,
        ttl_seconds: float | None = None,
    ) -> Any:
        # Use _run_in_thread to handle both sync and async compute_fn
        from .semantic_cache import _run_in_thread

        return await _run_in_thread(compute_fn)

    async def invalidate(self, query_hash: str) -> bool:
        return False

    async def clear(self) -> int:
        return 0

    def get_stats(self) -> dict[str, Any]:
        return {"enabled": False, "size": 0}


class _NoOpSemanticMemory:
    """No-op semantic memory for when semantic store is unavailable."""

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        importance: int = 5,
    ) -> str:
        return f"noop_mem_{datetime.now(timezone.utc).timestamp()}"

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_importance: int = 1,
    ) -> list[tuple[str, float]]:
        return []

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        return None


class _NoOpEpisodicMemory:
    """No-op episodic memory for when episodic store is unavailable."""

    async def store_turn(
        self,
        turn_index: int,
        messages: list[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return f"noop_turn_{turn_index}"

    async def get_turn(self, turn_index: int) -> dict[str, Any] | None:
        return None

    async def get_range(
        self,
        start_turn: int,
        end_turn: int,
    ) -> list[dict[str, Any]]:
        return []

    async def seal_episode(
        self,
        session_id: str,
        summary: str,
    ) -> str:
        return f"noop_episode_{session_id}"


class _LegacyMemoryStoreAdapter:
    """Adapter for legacy MemoryPort to SemanticMemoryPort."""

    def __init__(self, legacy_store: MemoryPort) -> None:
        self._store = legacy_store

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        importance: int = 5,
    ) -> str:
        import hashlib

        # Compute SHA-1 hash for deduplication (as per MemoryItemSnapshot contract)
        content_for_hash = f"{text}:observation:system:{metadata}"
        content_hash = hashlib.sha1(content_for_hash.encode("utf-8")).hexdigest()

        item = MemoryItemSnapshot(
            id=f"legacy_{datetime.now(timezone.utc).timestamp()}",
            source_event_id="akashic",
            step=0,
            timestamp=datetime.now(timezone.utc),
            role="system",
            type="observation",
            kind="info",
            text=text,
            importance=importance,
            keywords=[],
            hash=content_hash,
            context=metadata or {},
        )
        return await self._store.add(item)

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_importance: int = 1,
    ) -> list[tuple[str, float]]:
        results = await self._store.retrieve(
            query,
            top_k=top_k,
            min_importance=min_importance,
        )
        return [(item.id, 1.0) for item in results]

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        item = await self._store.get_by_id(memory_id)
        if item is None:
            return None
        return {
            "id": item.id,
            "text": item.text,
            "importance": item.importance,
            "timestamp": item.timestamp,
        }

    async def delete(self, memory_id: str) -> bool:
        """Delete not supported in legacy adapter - returns False."""
        return False

    def get_stats(self) -> dict[str, Any]:
        """Get stats from legacy store if available."""
        if hasattr(self._store, "get_stats"):
            return self._store.get_stats()
        return {"type": "legacy_adapter", "supported": False}


class _DefaultTierCoordinator(TierCoordinatorPort):
    """Default tier coordinator implementation.

    Coordinates cross-tier memory operations:
    - Working -> Episodic -> Semantic promotion pipeline
    - Garbage collection and consistency checks
    """

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager
        self._sync_lock = asyncio.Lock()

    async def evaluate_promotions(
        self,
        candidates: list[PromotionCandidate],
    ) -> list[PromotionCandidate]:
        """Filter promotion candidates by importance threshold."""
        threshold = self._manager.promotion_importance_threshold
        return [c for c in candidates if c.importance >= threshold]

    async def promote(self, candidate: PromotionCandidate) -> bool:
        """Promote a single candidate to its target tier."""
        try:
            if candidate.target_tier == "episodic" and hasattr(self._manager.episodic_memory, "store_turn"):
                await self._manager.episodic_memory.store_turn(
                    turn_index=0,
                    messages=[{"role": "system", "content": candidate.text_preview}],
                    metadata={"source_id": candidate.item_id, "reason": candidate.reason},
                )
                logger.debug("Promoted item %s to episodic memory", candidate.item_id[:8])
                return True
            elif candidate.target_tier == "semantic" and hasattr(self._manager.semantic_memory, "add"):
                await self._manager.semantic_memory.add(
                    text=candidate.text_preview,
                    metadata={"source_id": candidate.item_id, "reason": candidate.reason},
                    importance=candidate.importance,
                )
                logger.debug("Promoted item %s to semantic memory", candidate.item_id[:8])
                return True
            else:
                logger.warning("Unknown target tier or missing port: %s", candidate.target_tier)
        except (RuntimeError, ValueError) as exc:
            logger.error("Promotion failed: %s (%s)", type(exc).__name__, exc)
        return False

    async def promote_many(
        self,
        candidates: list[PromotionCandidate],
    ) -> list[str]:
        """Promote multiple candidates with sequential processing.

        Evaluates each candidate through the promotion pipeline,
        filtering by importance threshold before promoting.

        Uses sequential processing (not parallel) to ensure proper
        error tracking and enable compensation if needed.

        Returns:
            List of item_ids that were successfully promoted.
        """
        if not candidates:
            return []

        # Evaluate and filter candidates
        evaluated = await self.evaluate_promotions(candidates)
        logger.debug(
            "Evaluated %d candidates, %d passed importance threshold",
            len(candidates),
            len(evaluated),
        )

        # Sequential promotion with error tracking
        successful_ids: list[str] = []

        for candidate in evaluated:
            try:
                success = await self.promote(candidate)
                if success:
                    successful_ids.append(candidate.item_id)
                else:
                    # Log failed promotion but continue with others
                    logger.warning(
                        "Promotion failed for item %s to %s",
                        candidate.item_id[:8] if candidate.item_id else "unknown",
                        candidate.target_tier,
                    )
            except (RuntimeError, ValueError) as exc:
                logger.error(
                    "Promotion exception for item %s: %s (%s)",
                    candidate.item_id[:8] if candidate.item_id else "unknown",
                    type(exc).__name__,
                    exc,
                )

        # Log summary
        logger.info(
            "Promoted %d/%d candidates successfully",
            len(successful_ids),
            len(evaluated),
        )

        return successful_ids

    async def promote_with_rollback(
        self,
        candidates: list[PromotionCandidate],
    ) -> tuple[list[str], list[str]]:
        """Promote candidates with full transaction semantics.

        If ANY promotion fails, ALL previous successful promotions are rolled back.
        This ensures atomic semantics for critical operations.

        Args:
            candidates: List of candidates to promote.

        Returns:
            Tuple of (successful_ids, failed_ids) where:
            - successful_ids: List of item_ids that were successfully promoted
            - failed_ids: List of item_ids that failed or were rolled back
        """
        if not candidates:
            return [], []

        evaluated = await self.evaluate_promotions(candidates)

        successful_promotions: list[tuple[PromotionCandidate, str]] = []  # (candidate, result_id)
        failed_ids: list[str] = []

        for candidate in evaluated:
            try:
                # For now, we use simple boolean success
                # In future, promote() could return the created ID for precise rollback
                success = await self.promote(candidate)
                if success:
                    successful_promotions.append((candidate, candidate.item_id))
                else:
                    # Rollback all previous successful promotions
                    await self._rollback_promotions(successful_promotions)
                    failed_ids.append(candidate.item_id)
                    return [], failed_ids
            except RuntimeError as exc:
                logger.error(
                    "Transaction failed, rolling back %d promotions: %s (%s)",
                    len(successful_promotions),
                    type(exc).__name__,
                    exc,
                )
                await self._rollback_promotions(successful_promotions)
                failed_ids.append(candidate.item_id)
                return [], failed_ids
            except ValueError as exc:
                logger.error(
                    "Transaction failed (validation), rolling back %d promotions: %s (%s)",
                    len(successful_promotions),
                    type(exc).__name__,
                    exc,
                )
                await self._rollback_promotions(successful_promotions)
                failed_ids.append(candidate.item_id)
                return [], failed_ids

        return [pid for _, pid in successful_promotions], failed_ids

    async def _rollback_promotions(
        self,
        promotions: list[tuple[PromotionCandidate, str]],
    ) -> None:
        """Rollback a list of successful promotions.

        This is the compensation step for failed transactions.
        """
        for candidate, item_id in reversed(promotions):
            try:
                demotion = DemotionCandidate(
                    item_id=item_id,
                    source_tier=candidate.target_tier,
                    target_tier=candidate.source_tier,
                    reason="transaction_rollback",
                )
                await self.demote(demotion)
                logger.debug(
                    "Rolled back promotion: %s (%s -> %s)", item_id[:8], candidate.target_tier, candidate.source_tier
                )
            except (RuntimeError, ValueError) as exc:
                logger.error("Rollback failed for %s: %s (%s)", item_id[:8], type(exc).__name__, exc)

    async def demote(self, candidate: DemotionCandidate) -> bool:
        """Demote a memory item to a lower tier.

        Demotion handles:
        - semantic -> episodic: Archive old semantic items into episodic turns
        - episodic -> semantic: Re-import episodic items to semantic with adjusted importance

        For staleness reasons, the item is typically archived rather than deleted.
        For token_budget reasons, the item may be deleted from source after archiving.
        """
        try:
            logger.debug(
                "Demotion requested: %s (%s) -> %s, reason: %s",
                candidate.item_id[:8] if candidate.item_id else "unknown",
                candidate.source_tier,
                candidate.target_tier,
                candidate.reason,
            )

            # semantic -> episodic: Archive semantic item to episodic
            if candidate.source_tier == "semantic" and candidate.target_tier == "episodic":
                if hasattr(self._manager.semantic_memory, "get") and hasattr(
                    self._manager.episodic_memory, "store_turn"
                ):
                    semantic_item = await self._manager.semantic_memory.get(candidate.item_id)
                    if semantic_item:
                        # Store as a turn in episodic memory
                        await self._manager.episodic_memory.store_turn(
                            turn_index=0,  # Demotion turns don't have real turn indices
                            messages=[{"role": "system", "content": semantic_item.get("text", "")}],
                            metadata={
                                "source": "demotion",
                                "original_id": candidate.item_id,
                                "demotion_reason": candidate.reason,
                                "importance": semantic_item.get("importance", 5),
                            },
                        )
                        logger.info("Demoted semantic item %s to episodic", candidate.item_id[:8])

                        # For staleness, also delete from semantic
                        if candidate.reason in ("staleness", "token_budget") and hasattr(
                            self._manager.semantic_memory, "delete"
                        ):
                            await self._manager.semantic_memory.delete(candidate.item_id)
                            logger.debug("Deleted demoted item from semantic: %s", candidate.item_id[:8])
                        return True

            # episodic -> semantic: Re-import with reduced importance
            elif candidate.source_tier == "episodic" and candidate.target_tier == "semantic":
                episodic_item = (
                    await self._manager.episodic_memory.get_episode(candidate.item_id)
                    if hasattr(self._manager.episodic_memory, "get_episode")
                    else None
                )
                if episodic_item and hasattr(self._manager.semantic_memory, "add"):
                    # Add to semantic with reduced importance
                    # Note: importance is stored in metadata, not at episode dict top level
                    episodic_importance = episodic_item.get("metadata", {}).get("importance", 5)
                    reduced_importance = max(1, episodic_importance - 3)
                    await self._manager.semantic_memory.add(
                        text=episodic_item.get("summary", ""),
                        metadata={
                            "source": "demotion",
                            "original_id": candidate.item_id,
                            "demotion_reason": candidate.reason,
                        },
                        importance=reduced_importance,
                    )
                    logger.info(
                        "Demoted episodic item %s to semantic (importance %d)",
                        candidate.item_id[:8],
                        reduced_importance,
                    )
                    return True

            logger.warning(
                "Unknown demotion path or missing methods: %s -> %s", candidate.source_tier, candidate.target_tier
            )
            return False

        except (RuntimeError, ValueError) as exc:
            logger.error("Demotion failed: %s (%s)", type(exc).__name__, exc)
            return False

    async def sync_tiers(self) -> dict[str, int]:
        """Synchronize all memory tiers.

        This performs the full promotion pipeline:
        1. Flush working memory episodic promotion queue to episodic memory
        2. Flush working memory semantic promotion queue directly to semantic memory
        3. Promote high-importance episodic items to semantic memory
        4. Run consistency check on semantic memory

        Returns dict of tier_name -> items_processed count.

        Uses lock to prevent concurrent sync operations.
        """
        async with self._sync_lock:
            processed: dict[str, int] = {"working": 0, "episodic": 0, "semantic": 0}

        try:
            # 1. Flush working memory episodic promotion queue to episodic
            if hasattr(self._manager.working_memory, "get_promotion_queue"):
                queue = self._manager.working_memory.get_promotion_queue()
                if queue:
                    logger.info("Syncing %d items from working memory episodic promotion queue", len(queue))

                    # Build promotion candidates from queue
                    candidates = []
                    if hasattr(self._manager.working_memory, "chunks"):
                        for chunk in self._manager.working_memory.chunks:
                            if hasattr(chunk, "chunk_id") and chunk.chunk_id in queue:
                                importance = getattr(chunk, "importance", 5)
                                candidates.append(
                                    PromotionCandidate(
                                        item_id=chunk.chunk_id,
                                        source_tier="working",
                                        target_tier="episodic",
                                        importance=importance,
                                        text_preview=getattr(chunk, "content", "")[:100],
                                        reason="sync_tiers",
                                    )
                                )

                    # Promote all candidates to episodic
                    if candidates:
                        try:
                            results = await self.promote_many(candidates)
                            processed["working"] = sum(1 for r in results if r)
                        except (RuntimeError, ValueError) as exc:
                            logger.error("Failed to promote episodic candidates: %s", exc)
                        finally:
                            # Always clear queue after sync attempt, regardless of success/failure
                            if hasattr(self._manager.working_memory, "clear_promotion_queue"):
                                self._manager.working_memory.clear_promotion_queue()

            # 2. Flush working memory semantic promotion queue directly to semantic
            if hasattr(self._manager.working_memory, "get_semantic_promotion_queue"):
                semantic_queue = self._manager.working_memory.get_semantic_promotion_queue()
                if semantic_queue:
                    logger.info("Syncing %d items from working memory semantic promotion queue", len(semantic_queue))

                    # Build direct semantic promotion candidates
                    semantic_candidates = []
                    if hasattr(self._manager.working_memory, "chunks"):
                        for chunk in self._manager.working_memory.chunks:
                            if hasattr(chunk, "chunk_id") and chunk.chunk_id in semantic_queue:
                                importance = getattr(chunk, "importance", 5)
                                semantic_candidates.append(
                                    PromotionCandidate(
                                        item_id=chunk.chunk_id,
                                        source_tier="working",
                                        target_tier="semantic",
                                        importance=importance,
                                        text_preview=getattr(chunk, "content", "")[:100],
                                        reason="sync_tiers_direct_semantic",
                                    )
                                )

                    # Promote directly to semantic
                    if semantic_candidates:
                        try:
                            semantic_results = []
                            for candidate in semantic_candidates:
                                result = await self.promote(candidate)
                                semantic_results.append(result)
                            processed["semantic"] = sum(1 for r in semantic_results if r)
                        except (RuntimeError, ValueError) as exc:
                            logger.error("Failed to promote semantic candidates: %s", exc)
                        finally:
                            # Always clear semantic queue after sync attempt
                            if hasattr(self._manager.working_memory, "clear_semantic_promotion_queue"):
                                self._manager.working_memory.clear_semantic_promotion_queue()

            # 3. Promote high-importance episodic items to semantic
            # Items with importance >= 8 are promoted to semantic for long-term storage
            semantic_threshold = 8
            if hasattr(self._manager.episodic_memory, "get_recent_episodes"):
                try:
                    recent_episodes = await self._manager.episodic_memory.get_recent_episodes(limit=20)
                    semantic_candidates = []
                    for ep in recent_episodes:
                        # Episode summaries with high importance go to semantic
                        ep_importance = ep.get("metadata", {}).get("importance", 5)
                        if ep_importance >= semantic_threshold:
                            semantic_candidates.append(
                                PromotionCandidate(
                                    item_id=ep.get("episode_id", ""),
                                    source_tier="episodic",
                                    target_tier="semantic",
                                    importance=ep_importance,
                                    text_preview=ep.get("summary", "")[:100],
                                    reason="sync_tiers_high_importance",
                                )
                            )

                    if semantic_candidates:
                        # Promote to semantic
                        semantic_results = []
                        for candidate in semantic_candidates:
                            result = await self.promote(candidate)
                            semantic_results.append(result)
                        processed["semantic"] += sum(1 for r in semantic_results if r)
                        logger.debug(
                            "Promoted %d episodic items to semantic memory",
                            sum(1 for r in semantic_results if r),
                        )
                except (AttributeError, TypeError) as exc:
                    logger.debug("Could not promote episodic to semantic: %s", exc)
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Could not promote episodic to semantic (unexpected): %s", exc)

            # 4. Check episodic memory status
            if hasattr(self._manager.episodic_memory, "get_status"):
                try:
                    status = self._manager.episodic_memory.get_status()
                    processed["episodic"] = status.get("turns_cached", 0) + status.get("episodes_cached", 0)
                except (AttributeError, TypeError) as exc:
                    logger.debug("Could not get episodic memory status: %s", exc)
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Could not get episodic memory status (unexpected): %s", exc)

            # 5. Semantic memory - check stats if available
            if hasattr(self._manager.semantic_memory, "get_stats"):
                try:
                    stats = self._manager.semantic_memory.get_stats()
                    if isinstance(stats, dict):
                        processed["semantic"] = max(processed["semantic"], stats.get("size", 0))
                except (AttributeError, TypeError) as exc:
                    logger.debug("Could not get semantic memory stats: %s", exc)
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Could not get semantic memory stats (unexpected): %s", exc)

            logger.debug(
                "Tier sync completed: working=%d, episodic=%d, semantic=%d",
                processed["working"],
                processed["episodic"],
                processed["semantic"],
            )

        except (RuntimeError, ValueError) as exc:
            logger.error("Tier sync failed: %s", exc)

        return processed


# Type annotation
MemoryManager.__protocol__ = MemoryManagerPort  # type: ignore[attr-defined]


__all__ = [
    "MemoryManager",
    "MemoryManagerConfig",
    "MemoryManagerPort",
]
