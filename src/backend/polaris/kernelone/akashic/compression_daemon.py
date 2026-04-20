"""Akashic Nexus: Context Compression Daemon.

Implements preemptive background context compression to solve
the "compression timing chaos" problem.

Architecture:
    - WaterlineMonitor: Tracks context token usage in real-time
    - BackgroundSummarize: Async LLM-based summarization tasks
    - IncrementalCompact: Non-blocking compression of middle region

Solves the old reactive pattern:
    OLD: [Budget exceeded] → [LLM call fails] → [Compress] → [Retry]
    NEW: [75% waterline] → [Background compress] → [Seamless continuation]

Usage::

    daemon = CompressionDaemon(
        memory_manager=manager,
        config=DaemonConfig(),
    )

    await daemon.start()
    # Daemon runs in background, monitoring and compressing

    await daemon.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from .memory_manager import MemoryManager
    from .protocols import WorkingMemorySnapshot

# Lazy import to avoid circular dependency - RoleContextCompressor is optional
_role_context_compressor: Any = None
_compaction_fallback: Any = None


def _get_compressor_components() -> tuple[Any, Any]:
    """Lazy load RoleContextCompressor and fallback functions."""
    global _role_context_compressor, _compaction_fallback
    if _role_context_compressor is None:
        try:
            from polaris.kernelone.context.compaction import (
                RoleContextCompressor,
                build_continuity_summary_text,
            )

            _role_context_compressor = RoleContextCompressor
            _compaction_fallback = build_continuity_summary_text
        except ImportError:
            _role_context_compressor = None
            _compaction_fallback = None
    return _role_context_compressor, _compaction_fallback


logger = logging.getLogger(__name__)


class DaemonState(Enum):
    """States for the compression daemon."""

    STOPPED = "stopped"
    IDLE = "idle"
    MONITORING = "monitoring"
    COMPRESSING_SOFT = "compressing_soft"
    COMPRESSING_HARD = "compressing_hard"
    STOPPING = "stopping"


@dataclass
class DaemonConfig:
    """Configuration for the CompressionDaemon."""

    check_interval_ms: int = 500  # Check waterline every 500ms
    soft_watermark_pct: float = 0.75  # Trigger soft compression
    hard_watermark_pct: float = 0.90  # Trigger hard compression
    max_concurrent_compressions: int = 2  # Max parallel compression tasks
    compression_timeout_seconds: float = DEFAULT_SHORT_TIMEOUT_SECONDS  # Max time for compression
    enable_incremental: bool = True  # Use incremental vs full compression
    min_tokens_to_compress: int = 1000  # Minimum savings to trigger compression


@dataclass
class CompressionStats:
    """Statistics for compression operations."""

    soft_compressions: int = 0
    hard_compressions: int = 0
    total_tokens_freed: int = 0
    total_compression_time_ms: int = 0
    last_compression_at: datetime | None = None
    skipped_no_threshold: int = 0


class CompressionDaemon:
    """Background daemon for preemptive context compression.

    Monitors memory usage and triggers background compression
    before token budget is exhausted.

    The daemon runs in a separate asyncio task and:
    1. Checks working memory usage every `check_interval_ms`
    2. Triggers soft compression at 75% usage
    3. Triggers hard compression at 90% usage
    4. Uses incremental compression to avoid blocking
    """

    def __init__(
        self,
        memory_manager: MemoryManager,
        config: DaemonConfig | None = None,
        *,
        llm_client: Any = None,
        workspace: str = ".",
    ) -> None:
        self._manager = memory_manager
        self._config = config or DaemonConfig()
        self._llm_client = llm_client
        self._workspace = workspace

        # State
        self._state: DaemonState = DaemonState.STOPPED
        self._task: asyncio.Task | None = None
        self._compression_tasks: list[asyncio.Task] = []
        self._lock: asyncio.Lock | None = None

        # Compressor instance (lazily created)
        self._compressor: Any = None

        # Stats
        self._stats = CompressionStats()

        # Historical tracking
        self._last_usage_ratio: float = 0.0
        self._usage_trend: str = "stable"  # stable | rising | falling

    @property
    def state(self) -> DaemonState:
        """Get current daemon state."""
        return self._state

    @property
    def stats(self) -> CompressionStats:
        """Get compression statistics."""
        return self._stats

    async def start(self) -> None:
        """Start the compression daemon.

        Creates and starts the background monitoring task.
        """
        if self._state != DaemonState.STOPPED:
            logger.warning("Daemon already running (state=%s)", self._state.value)
            return

        logger.info("Starting CompressionDaemon...")
        self._lock = asyncio.Lock()
        self._state = DaemonState.IDLE
        self._task = asyncio.create_task(self._run_loop())
        self._state = DaemonState.MONITORING
        logger.info("CompressionDaemon started (check_interval=%dms)", self._config.check_interval_ms)

    async def stop(self) -> None:
        """Stop the compression daemon gracefully.

        Waits for any in-flight compression tasks to complete.
        """
        if self._state == DaemonState.STOPPED:
            return

        logger.info("Stopping CompressionDaemon...")
        self._state = DaemonState.STOPPING

        # Cancel monitoring task
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        # Wait for compression tasks
        if self._compression_tasks:
            await asyncio.gather(*self._compression_tasks, return_exceptions=True)

        self._compression_tasks.clear()
        self._state = DaemonState.STOPPED
        logger.info("CompressionDaemon stopped")

    async def _run_loop(self) -> None:
        """Main monitoring loop."""
        check_interval = self._config.check_interval_ms / 1000.0

        while self._state == DaemonState.MONITORING:
            try:
                # Check for completion of previous compressions
                await self._cleanup_compression_tasks()

                # Check if we can start new compression
                if len(self._compression_tasks) < self._config.max_concurrent_compressions:
                    await self._check_and_trigger_compression()

                # Track usage trend
                current_snapshot = self._manager.working_memory.get_snapshot()
                current_usage = current_snapshot.usage_ratio

                if current_usage > self._last_usage_ratio + 0.01:
                    self._usage_trend = "rising"
                elif current_usage < self._last_usage_ratio - 0.01:
                    self._usage_trend = "falling"
                else:
                    self._usage_trend = "stable"

                self._last_usage_ratio = current_usage

            except asyncio.CancelledError:
                raise
            except (RuntimeError, ValueError) as exc:
                logger.error("Error in compression daemon loop: %s", exc)

            await asyncio.sleep(check_interval)

    async def _check_and_trigger_compression(self) -> None:
        """Check waterline and trigger compression if needed."""
        if self._lock is None:
            return

        async with self._lock:
            snapshot = self._manager.working_memory.get_snapshot()
            usage_ratio = snapshot.usage_ratio

            # Determine compression level
            if usage_ratio >= self._config.hard_watermark_pct and self._state != DaemonState.COMPRESSING_HARD:
                logger.warning(
                    "Hard watermark reached: %.1f%%, triggering emergency compression",
                    usage_ratio * 100,
                )
                self._state = DaemonState.COMPRESSING_HARD
                await self._trigger_compression("hard", snapshot)
                self._state = DaemonState.MONITORING

            elif (
                usage_ratio >= self._config.soft_watermark_pct
                and self._usage_trend == "rising"
                and self._state == DaemonState.MONITORING
            ):
                logger.info(
                    "Soft watermark reached: %.1f%%, triggering background compression",
                    usage_ratio * 100,
                )
                self._state = DaemonState.COMPRESSING_SOFT
                await self._trigger_compression("soft", snapshot)
                self._state = DaemonState.MONITORING

    async def _trigger_compression(
        self,
        level: str,  # "soft" or "hard"
        snapshot: Any,
    ) -> None:
        """Trigger a compression task."""
        # Calculate potential savings
        middle_tokens = snapshot.middle_tokens
        if middle_tokens < self._config.min_tokens_to_compress:
            self._stats.skipped_no_threshold += 1
            logger.debug(
                "Skipping compression: middle tokens (%d) < threshold (%d)",
                middle_tokens,
                self._config.min_tokens_to_compress,
            )
            return

        # Create compression task
        task = asyncio.create_task(self._run_compression(level, snapshot))
        self._compression_tasks.append(task)

    def _get_or_create_compressor(self) -> Any:
        """Get or create the RoleContextCompressor instance."""
        if self._compressor is not None:
            return self._compressor

        compressor_cls, _ = _get_compressor_components()
        if compressor_cls is None:
            return None

        self._compressor = compressor_cls(
            workspace=self._workspace,
            role_name="akashic_compression",
            llm_client=self._llm_client,
        )
        return self._compressor

    async def _run_compression(
        self,
        level: str,
        snapshot: WorkingMemorySnapshot,
    ) -> None:
        """Run the actual compression operation.

        Uses LLM-based summarization when available (via RoleContextCompressor),
        falling back to deterministic eviction when LLM is unavailable.
        """
        start_time = time.time()

        try:
            wm_before = self._manager.working_memory.get_snapshot()
            tokens_before = wm_before.total_tokens

            # Get current messages for compression
            # Use runtime check to access config.max_tokens on concrete implementation
            wm_config_max_tokens = getattr(self._manager.working_memory, "config", None)
            max_tokens = getattr(wm_config_max_tokens, "max_tokens", 32000) if wm_config_max_tokens else 32000

            messages = self._manager.working_memory.get_messages(max_tokens=max_tokens)

            compressed_messages = messages
            method_used = "eviction"

            # Try LLM-based compression first
            compressor = self._get_or_create_compressor()
            if compressor is not None and self._llm_client is not None and messages:
                try:
                    # Build identity from current state
                    identity = compressor.create_identity_from_task(
                        {
                            "id": "akashic_compression",
                            "goal": "context_compression",
                            "scope_paths": [],
                        }
                    )

                    # Determine focus based on compression level
                    focus = "emergency_context_reduction" if level == "hard" else "routine_context_optimization"

                    # Use LLM compact
                    compressed_messages, comp_snapshot = compressor.llm_compact(
                        messages,
                        identity,
                        focus=focus,
                    )
                    method_used = f"llm_{comp_snapshot.method}"

                    logger.info(
                        "LLM compression (%s) applied: %s, reduced %d -> %d tokens",
                        level,
                        comp_snapshot.method,
                        comp_snapshot.original_tokens,
                        comp_snapshot.compressed_tokens,
                    )
                except (RuntimeError, ValueError) as llm_err:
                    logger.warning("LLM compression failed, falling back to eviction: %s", llm_err)
                    compressed_messages = messages
                    method_used = "eviction_fallback"

            # Apply compressed messages back to working memory
            # First clear then re-add head and compressed tail
            if method_used.startswith("llm_"):
                # For LLM compression, we need to replace the middle with summary
                # Keep head chunks, replace middle with compressed version
                # Use hasattr to safely access concrete implementation attributes
                chunks = getattr(self._manager.working_memory, "chunks", [])
                head_chunks = [c for c in chunks if getattr(c, "priority", None) and c.priority.value == 1]  # CRITICAL

                # Clear and rebuild
                self._manager.working_memory.clear()

                # Re-add head chunks (using only protocol-defined push signature)
                for chunk in head_chunks:
                    self._manager.working_memory.push(
                        role=chunk.role,
                        content=chunk.content,
                        importance=chunk.importance,
                        metadata=chunk.metadata,
                    )

                # Add compressed messages
                for msg in compressed_messages:
                    if isinstance(msg, dict) and msg.get("role"):
                        self._manager.working_memory.push(
                            role=msg["role"],
                            content=msg.get("content", ""),
                            importance=5,
                            metadata={"compressed": True, "method": method_used},
                        )
            elif self._config.enable_incremental:
                # Incremental compression: compress middle region without full eviction
                # Remove low-priority middle chunks to bring usage under soft watermark
                method_used = await self._incremental_compress(level, snapshot)
            else:
                # Fallback to eviction-based compression (full eviction)
                if hasattr(self._manager.working_memory, "_evict_if_needed"):
                    self._manager.working_memory._evict_if_needed()
                method_used = "eviction"

            elapsed_ms = int((time.time() - start_time) * 1000)

            # Get updated snapshot
            wm_after = self._manager.working_memory.get_snapshot()
            tokens_freed = max(0, tokens_before - wm_after.total_tokens)

            # Update stats
            if level == "soft":
                self._stats.soft_compressions += 1
            else:
                self._stats.hard_compressions += 1

            self._stats.total_compression_time_ms += elapsed_ms
            self._stats.last_compression_at = datetime.now(timezone.utc)
            self._stats.total_tokens_freed += tokens_freed

            logger.info(
                "Compression (%s) via %s completed in %dms, freed: %d tokens (%.1f%% usage now)",
                level,
                method_used,
                elapsed_ms,
                tokens_freed,
                wm_after.usage_ratio * 100,
            )

        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError) as exc:
            logger.error("Compression failed: %s", exc)

    async def _incremental_compress(
        self,
        level: str,
        snapshot: WorkingMemorySnapshot,
    ) -> str:
        """Incrementally compress middle region without full eviction.

        This method:
        1. Identifies middle-region chunks (non-head, non-tail)
        2. Sorts by priority (lowest first) and recency_score
        3. Removes chunks until under soft watermark threshold
        4. Preserves head (CRITICAL) and tail (recent) chunks

        This is less disruptive than full eviction and maintains
        access to head and tail regions during compression.
        """
        # Get target based on level
        # Soft: get under soft watermark
        # Hard: get under 60% (more aggressive)
        target_ratio = self._config.soft_watermark_pct if level == "soft" else 0.60

        # Use hasattr to safely access chunks on concrete implementation
        chunks = getattr(self._manager.working_memory, "chunks", [])
        if not chunks:
            return "incremental_skip_empty"

        # Categorize chunks
        head_chunks = []
        tail_chunks = []
        middle_chunks = []

        # Access _is_in_tail if available (WorkingMemoryWindow concrete impl)
        is_in_tail_fn = getattr(self._manager.working_memory, "_is_in_tail", None)

        for chunk in chunks:
            priority_val = getattr(chunk, "priority", None)
            if priority_val is None:
                continue

            # CRITICAL = 1 is always head
            if priority_val.value == 1:
                head_chunks.append(chunk)
            elif is_in_tail_fn is not None and is_in_tail_fn(chunk):
                tail_chunks.append(chunk)
            else:
                middle_chunks.append(chunk)

        # Calculate head+tail tokens (must preserve these)
        preserved_tokens = sum(getattr(c, "estimated_tokens", 0) for c in head_chunks + tail_chunks)

        # Target tokens for middle region
        max_tokens = getattr(self._manager.working_memory, "config", None)
        max_tokens_val = getattr(max_tokens, "max_tokens", 32000) if max_tokens else 32000
        target_total = int(max_tokens_val * target_ratio)
        middle_budget = max(0, target_total - preserved_tokens)

        # Sort middle chunks by: priority desc (lower=more discardable), recency_score asc (older first)
        middle_chunks.sort(
            key=lambda c: (
                (getattr(c, "priority", None) and c.priority.value) or 5,
                getattr(c, "recency_score", 1.0),
            )
        )

        # Remove middle chunks until under budget
        kept_chunks: list = []
        freed_tokens = 0

        for chunk in middle_chunks:
            chunk_tokens = getattr(chunk, "estimated_tokens", 0)
            current_middle_tokens = sum(getattr(c, "estimated_tokens", 0) for c in kept_chunks)

            if current_middle_tokens + chunk_tokens <= middle_budget:
                kept_chunks.append(chunk)
            else:
                freed_tokens += chunk_tokens

        # Rebuild middle region with kept chunks
        # Note: We use the eviction mechanism which handles removal properly
        # First mark chunks for removal, then let eviction run
        kept_chunk_ids = {id(c) for c in kept_chunks}
        chunks_to_remove = {getattr(c, "chunk_id", None) for c in middle_chunks if id(c) not in kept_chunk_ids}
        chunks_to_remove.discard(None)  # Remove None if present

        if chunks_to_remove:
            # Get current chunks and rebuild without removed ones
            # Make a copy BEFORE clear since chunks property returns a new list each time
            current_chunks = list(getattr(self._manager.working_memory, "chunks", []))
            self._manager.working_memory.clear()

            for chunk in current_chunks:
                chunk_id = getattr(chunk, "chunk_id", None)
                if chunk_id in chunks_to_remove:
                    continue  # Skip removed chunks

                priority_val = getattr(chunk, "priority", None)
                if priority_val is not None and priority_val.value == 1:
                    # Skip head/tail - we'll re-add them
                    continue

                self._manager.working_memory.push(
                    role=getattr(chunk, "role", "user"),
                    content=getattr(chunk, "content", ""),
                    importance=getattr(chunk, "importance", 5),
                    metadata=getattr(chunk, "metadata", {}),
                )

            # Re-add preserved head chunks
            for chunk in head_chunks:
                self._manager.working_memory.push(
                    role=getattr(chunk, "role", "user"),
                    content=getattr(chunk, "content", ""),
                    importance=getattr(chunk, "importance", 5),
                    metadata=getattr(chunk, "metadata", {}),
                )

            # Re-add preserved tail chunks
            for chunk in tail_chunks:
                self._manager.working_memory.push(
                    role=getattr(chunk, "role", "user"),
                    content=getattr(chunk, "content", ""),
                    importance=getattr(chunk, "importance", 5),
                    metadata=getattr(chunk, "metadata", {}),
                )

        method = f"incremental_{level}_{len(chunks_to_remove)}_chunks"
        logger.debug(
            "Incremental compression: removed %d middle chunks, freed ~%d tokens",
            len(chunks_to_remove),
            freed_tokens,
        )

        return method

    async def _cleanup_compression_tasks(self) -> None:
        """Remove completed compression tasks."""
        self._compression_tasks = [t for t in self._compression_tasks if not t.done()]

    def get_status(self) -> dict[str, Any]:
        """Get daemon status for monitoring."""
        return {
            "state": self._state.value,
            "usage_trend": self._usage_trend,
            "last_usage_ratio": round(self._last_usage_ratio, 3),
            "active_compressions": len(self._compression_tasks),
            "max_concurrent": self._config.max_concurrent_compressions,
            "stats": {
                "soft_compressions": self._stats.soft_compressions,
                "hard_compressions": self._stats.hard_compressions,
                "total_tokens_freed": self._stats.total_tokens_freed,
                "total_compression_time_ms": self._stats.total_compression_time_ms,
                "last_compression_at": (
                    self._stats.last_compression_at.isoformat() if self._stats.last_compression_at else None
                ),
                "skipped_no_threshold": self._stats.skipped_no_threshold,
            },
        }


__all__ = [
    "CompressionDaemon",
    "CompressionStats",
    "DaemonConfig",
    "DaemonState",
]
