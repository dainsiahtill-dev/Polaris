"""Akashic Nexus: Working Memory Window.

Implements the WorkingMemoryWindow with Hierarchical Chunk Prioritization
to solve the "Lost in the Middle" problem.

Architecture:
    - Sliding window with Head/Tail/Middle differentiation
    - Hierarchical importance scoring for chunk prioritization
    - Preemptive compression triggered by watermark levels
    - Integration with SemanticCache for cross-tier promotion

The "Lost in the Middle" fix:
    Instead of linear append (old behavior), we now use:
    1. Head: System prompt + task goal (always preserved)
    2. Tail: Recent 2-3 turns (high recency value)
    3. Middle: Compressed with importance-weighted summarization
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .protocols import (
    WorkingMemoryConfig,
    WorkingMemoryPort,
    WorkingMemorySnapshot,
)

# Lazy import to avoid circular dependency
_token_estimator_class: Any = None


def _get_token_estimator_class() -> Any:
    """Lazy load TokenEstimator to avoid circular imports."""
    global _token_estimator_class
    if _token_estimator_class is None:
        try:
            from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

            _token_estimator_class = TokenEstimator
        except ImportError:
            pass
    return _token_estimator_class


logger = logging.getLogger(__name__)


class ChunkPriority(Enum):
    """Priority levels for chunks - higher = more important to preserve."""

    CRITICAL = 1  # System prompt, task goal
    HIGH = 2  # Tool results, decisions
    MEDIUM = 3  # Assistant reasoning
    LOW = 4  # Greetings, meta chatter
    DISCARDABLE = 5  # Low-signal content


# Terms that indicate high-value content (for importance scoring)
_HIGH_VALUE_TERMS: set[str] = {
    "error",
    "bug",
    "fix",
    "fix:",
    "refactor",
    "implement",
    "function",
    "class",
    "def ",
    "import",
    "return",
    "error:",
    "exception",
    "traceback",
    "failed",
    "task",
    "goal",
    "objective",
    "accomplish",
    "decision",
    "decided",
    "conclusion",
    "summary",
    # Chinese
    "错误",
    "修复",
    "实现",
    "函数",
    "类",
    "任务",
    "目标",
    "决策",
    "总结",
    "异常",
    "失败",
}

# Terms that indicate low-value content (for importance scoring)
_LOW_VALUE_PATTERNS: tuple[str, ...] = (
    r"^(hi|hello|hey|你好|您好|嗨)\b",
    r"^(thanks|thank you|谢谢|ok|好的|收到)\b",
    r"^(bye|再见|goodbye)\b",
    r"(换个名字|改名字|改名|叫我|叫你)",
    r"(what model are you|who are you|你是谁)",
)


@dataclass
class MemoryChunk:
    """A single chunk in the working memory window."""

    chunk_id: str
    role: str
    content: str
    priority: ChunkPriority
    importance: int  # 1-10
    estimated_tokens: int
    created_at: datetime
    turn_index: int
    metadata: dict[str, Any] = field(default_factory=dict)

    # Importance scoring components
    signal_score: float = 0.0
    recency_score: float = 1.0  # Decays with turns

    def to_message(self) -> dict[str, Any]:
        """Convert to chat message format."""
        return {
            "role": self.role,
            "content": self.content,
            "chunk_id": self.chunk_id,
        }


def _compute_signal_score(role: str, content: str, importance: int) -> float:
    """Compute signal score for a chunk based on content analysis."""
    score = 0.0

    # Role weight
    role_weights: dict[str, float] = {
        "system": 1.0,
        "user": 0.8,
        "assistant": 0.6,
        "tool": 1.2,  # Tool results are high value
    }
    score += role_weights.get(role, 0.5) * 2.0

    # Content analysis
    content_lower = content.lower()

    # High-value term detection
    term_matches = sum(1 for term in _HIGH_VALUE_TERMS if term in content_lower)
    score += min(term_matches * 0.5, 3.0)  # Cap at +3.0

    # Low-value pattern detection
    import re

    for pattern in _LOW_VALUE_PATTERNS:
        if re.search(pattern, content_lower, re.IGNORECASE):
            score -= 2.0

    # Length factor (too short = likely low value, too long = expensive)
    content_len = len(content)
    if content_len < 20:
        score -= 1.0
    elif 50 <= content_len <= 2000:
        score += 1.0

    # Importance from caller
    score += (importance / 10.0) * 2.0

    return max(0.0, score)


class WorkingMemoryWindow:
    """Hierarchical working memory window with differentiated preservation.

    This solves the "Lost in the Middle" problem by:
    1. Preserving HEAD (system + task goal) at all costs
    2. Preserving TAIL (recent N turns) with high priority
    3. Compressing MIDDLE with importance-weighted summarization

    Usage::

        window = WorkingMemoryWindow(
            config=WorkingMemoryConfig(
                max_tokens=32000,
                soft_watermark_pct=0.75,
                hard_watermark_pct=0.90,
            )
        )

        window.push("user", "Fix the login bug")
        window.push("assistant", "I'll fix the bug...")
        messages = window.get_messages(max_tokens=16000)
    """

    def __init__(
        self,
        config: WorkingMemoryConfig | None = None,
        *,
        token_estimator: Any = None,  # TokenEstimatorProtocol
    ) -> None:
        self._config = config or WorkingMemoryConfig()
        self._token_estimator = token_estimator

        # Chunk storage
        self._chunks: OrderedDict[str, MemoryChunk] = OrderedDict()
        self._turn_index: int = 0

        # Statistics
        self._total_tokens: int = 0
        self._compression_triggered: str | None = None
        self._last_compression_at: datetime | None = None

        # Tier integration
        self._episodic_promotion_queue: list[str] = []  # chunk_ids pending episodic promotion
        self._semantic_promotion_queue: list[str] = []  # chunk_ids pending semantic promotion

    @property
    def config(self) -> WorkingMemoryConfig:
        """Get current configuration."""
        return self._config

    @property
    def chunks(self) -> list[MemoryChunk]:
        """Get all chunks in order."""
        return list(self._chunks.values())

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

        Returns the chunk_id of the inserted chunk.
        """
        # Generate stable chunk ID
        chunk_id = hashlib.sha256(f"{role}:{content}:{time.time_ns()}".encode()).hexdigest()[:16]

        # Estimate tokens
        estimated_tokens = self._estimate_tokens(content)

        # Compute priority
        signal_score = _compute_signal_score(role, content, importance)
        priority = self._determine_priority(role, content, signal_score)

        # Create chunk
        chunk = MemoryChunk(
            chunk_id=chunk_id,
            role=role,
            content=content,
            priority=priority,
            importance=importance,
            estimated_tokens=estimated_tokens,
            created_at=datetime.now(timezone.utc),
            turn_index=turn_index if turn_index is not None else self._turn_index,
            metadata=metadata or {},
            signal_score=signal_score,
            recency_score=1.0,  # Will be updated on retrieval
        )

        # Insert
        self._chunks[chunk_id] = chunk
        self._total_tokens += estimated_tokens

        # Evict old chunks if we're over the hard watermark
        self._evict_if_needed()

        # Check watermark
        self._check_watermarks()

        logger.debug(
            "Pushed chunk %s (role=%s, tokens=%d, priority=%s)",
            chunk_id[:8],
            role,
            estimated_tokens,
            priority.name,
        )

        return chunk_id

    def _evict_if_needed(self) -> None:
        """Evict low-priority chunks if we're over the hard watermark.

        Eviction strategy:
        1. Never evict CRITICAL chunks (system prompts are sacred)
        2. Evict by priority (DISCARDABLE > LOW > MEDIUM > HIGH)
        3. Within same priority, evict oldest first (lowest recency_score)
        4. Stop when we're back under the hard watermark threshold
        5. Fallback: If ALL chunks are CRITICAL and we're critically over budget,
           evict the oldest CRITICAL chunks (shouldn't happen with proper prioritization)
        """
        # Guard: prevent division by zero
        if self._config.max_tokens <= 0:
            return

        # Only evict if we're over the hard watermark
        usage_ratio = self._total_tokens / self._config.max_tokens
        if usage_ratio < self._config.hard_watermark_pct:
            return

        # Target: get back under the soft watermark
        target_tokens = int(self._config.max_tokens * self._config.soft_watermark_pct)

        # Find evictable chunks sorted by priority (high enum value = low priority)
        # and then by recency_score (lowest first = oldest)
        evictable: list[MemoryChunk] = [c for c in self._chunks.values() if c.priority != ChunkPriority.CRITICAL]

        # Sort by: priority descending (lower priority first) then recency ascending (oldest first)
        evictable.sort(key=lambda c: (c.priority.value, c.recency_score))

        tokens_to_free = self._total_tokens - target_tokens
        freed_tokens = 0
        evicted_ids: list[str] = []

        for chunk in evictable:
            if freed_tokens >= tokens_to_free:
                break

            # Final safety check: never evict if it would put us under the minimum safety margin
            if self._total_tokens - freed_tokens - chunk.estimated_tokens < self._config.max_tokens * 0.5:
                break

            evicted_ids.append(chunk.chunk_id)
            freed_tokens += chunk.estimated_tokens

        # Bug 1 Fix: If ALL chunks are CRITICAL and we still need to free more,
        # fall back to evicting oldest CRITICAL chunks (should be very rare)
        if not evicted_ids and self._total_tokens - freed_tokens > target_tokens:
            logger.warning(
                "All chunks are CRITICAL but memory is critically over budget. "
                "Forcing emergency eviction of oldest chunks."
            )
            # Get all chunks sorted by recency_score (oldest first)
            all_chunks = sorted(
                self._chunks.values(),
                key=lambda c: c.recency_score,
            )
            # Only keep HEAD (first chunk) and evict the rest
            for chunk in all_chunks[1:]:
                if freed_tokens >= tokens_to_free:
                    break
                evicted_ids.append(chunk.chunk_id)
                freed_tokens += chunk.estimated_tokens

        # Remove evicted chunks
        for chunk_id in evicted_ids:
            del self._chunks[chunk_id]

        # Recalculate total tokens after eviction
        self._total_tokens = sum(c.estimated_tokens for c in self._chunks.values())

        if evicted_ids:
            logger.info(
                "Evicted %d chunks, freed ~%d tokens (%.1f%% usage now)",
                len(evicted_ids),
                freed_tokens,
                (self._total_tokens / self._config.max_tokens) * 100,
            )

    def _determine_priority(
        self,
        role: str,
        content: str,
        signal_score: float,
    ) -> ChunkPriority:
        """Determine the priority level for a chunk."""
        # Critical: system prompts
        if role == "system":
            return ChunkPriority.CRITICAL

        # High: tool results, explicit decisions
        if role == "tool":
            return ChunkPriority.HIGH

        content_lower = content.lower()

        # Check for explicit high-value markers
        if any(term in content_lower for term in ["error:", "fix:", "decision:", "conclusion:"]):
            return ChunkPriority.HIGH

        # Low: meta/social content
        if signal_score < 1.0:
            return ChunkPriority.LOW

        # Medium: everything else (assistant reasoning, user queries)
        return ChunkPriority.MEDIUM

    def _estimate_tokens(self, content: str) -> int:
        """Estimate token count for content using Tiktoken when available.

        Uses the TokenEstimator with proper tiktoken encoding (cl100k_base)
        for accurate token counting, falling back to heuristic estimation.
        """
        if not content:
            return 0

        # Try injected estimator first
        if self._token_estimator is not None:
            try:
                result = self._token_estimator.estimate_messages_tokens([{"role": "user", "content": content}])
                if isinstance(result, int) and result >= 0:
                    return result
            except (RuntimeError, ValueError) as exc:
                logger.debug("Injected token estimator failed: %s", exc)

        # Try TokenEstimator with tiktoken support
        estimator_cls = _get_token_estimator_class()
        if estimator_cls is not None:
            try:
                # Detect content type for better estimation
                content_type = "code" if self._is_code_content(content) else "general"
                result = estimator_cls.estimate(content, content_type=content_type)
                if result > 0:
                    return result
            except (RuntimeError, ValueError) as exc:
                logger.debug("TokenEstimator estimate failed: %s", exc)

        # Fallback: ~4 chars/token (rough for mixed content)
        return max(1, len(content) // 4)

    def _is_code_content(self, content: str) -> bool:
        """Detect if content appears to be code."""
        code_indicators = sum(1 for c in content if c in "{};()[]=<>+-*/%&|^~!#@")
        return code_indicators > len(content) * 0.05  # More than 5% code chars

    def _check_watermarks(self) -> None:
        """Check if compression should be triggered based on watermarks."""
        # Guard: prevent division by zero
        if self._config.max_tokens <= 0:
            return
        usage_ratio = self._total_tokens / self._config.max_tokens

        if usage_ratio >= self._config.hard_watermark_pct and self._compression_triggered != "hard":
            self._compression_triggered = "hard"
            logger.warning(
                "Hard watermark triggered: %.1f%% tokens used",
                usage_ratio * 100,
            )
        elif usage_ratio >= self._config.soft_watermark_pct and self._compression_triggered is None:
            self._compression_triggered = "soft"
            logger.info(
                "Soft watermark triggered: %.1f%% tokens used",
                usage_ratio * 100,
            )

    def get_snapshot(self) -> WorkingMemorySnapshot:
        """Get current working memory state snapshot."""
        # Compute token distribution
        head_tokens = 0
        tail_tokens = 0
        middle_tokens = 0

        chunks = self.chunks
        if chunks:
            # Head: all CRITICAL chunks (system prompts, task goals)
            # Bug 2 fix: was only counting chunks[0], now sums all CRITICAL
            head_chunks = [c for c in chunks if c.priority == ChunkPriority.CRITICAL]
            head_tokens = sum(c.estimated_tokens for c in head_chunks)

            # Tail: last N turns
            tail_turn_indices = {c.turn_index for c in chunks[-self._config.tail_preserve_count * 3 :]}
            for chunk in chunks:
                if chunk.turn_index in tail_turn_indices:
                    tail_tokens += chunk.estimated_tokens
                elif chunk.priority != ChunkPriority.CRITICAL:
                    middle_tokens += chunk.estimated_tokens

        # Guard: prevent division by zero
        usage_ratio = 0.0 if self._config.max_tokens <= 0 else self._total_tokens / self._config.max_tokens

        return WorkingMemorySnapshot(
            total_tokens=self._total_tokens,
            chunk_count=len(self._chunks),
            head_tokens=head_tokens,
            middle_tokens=middle_tokens,
            tail_tokens=tail_tokens,
            usage_ratio=usage_ratio,
            compression_triggered=self._compression_triggered,
        )

    def get_messages(
        self,
        *,
        max_tokens: int | None = None,
        include_role: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get messages from working memory with hierarchical preservation.

        Unlike the old linear-append approach, this method:
        1. Always preserves HEAD (system + task goal)
        2. Preserves TAIL (recent turns) unless token budget exhausted
        3. Compresses MIDDLE if needed, using importance scoring
        """
        chunks = self.chunks

        if not chunks:
            return []

        # Determine effective token budget
        effective_budget = max_tokens if max_tokens is not None else self._config.max_tokens
        # Leave safety margin
        effective_budget = int(effective_budget * 0.85)

        # First pass: categorize chunks
        head_chunks: list[MemoryChunk] = []
        tail_chunks: list[MemoryChunk] = []
        middle_chunks: list[MemoryChunk] = []

        for chunk in chunks:
            if chunk.priority == ChunkPriority.CRITICAL:
                head_chunks.append(chunk)
            elif self._is_in_tail(chunk):
                tail_chunks.append(chunk)
            else:
                middle_chunks.append(chunk)

        # Sort middle by importance score (highest first)
        middle_chunks.sort(key=lambda c: (c.signal_score, c.importance), reverse=True)

        # Build result with token budget
        result: list[MemoryChunk] = []
        remaining_tokens = effective_budget

        # 1. Always include HEAD first
        for chunk in head_chunks:
            if chunk.estimated_tokens <= remaining_tokens:
                result.append(chunk)
                remaining_tokens -= chunk.estimated_tokens
            else:
                # Head is critical - even if over budget, include it
                result.append(chunk)

        # 2. Add TAIL (recent turns first)
        tail_chunks.reverse()  # Most recent first
        for chunk in tail_chunks:
            if chunk.estimated_tokens <= remaining_tokens:
                result.append(chunk)
                remaining_tokens -= chunk.estimated_tokens

        # 3. Fill with MIDDLE (importance order) if budget allows
        for chunk in middle_chunks:
            if chunk.estimated_tokens <= remaining_tokens:
                result.append(chunk)
                remaining_tokens -= chunk.estimated_tokens
            else:
                # Budget exhausted - we could trigger compression here
                logger.debug(
                    "Token budget exhausted, %d middle chunks dropped",
                    len(middle_chunks) - len([c for c in middle_chunks if c in result]),
                )
                break

        # Update recency scores for excluded chunks
        included_ids = {c.chunk_id for c in result}
        for chunk in chunks:
            if chunk.chunk_id not in included_ids:
                chunk.recency_score *= 0.9  # Decay for excluded chunks

        # Filter by role if requested
        if include_role is not None:
            result = [c for c in result if c.role == include_role]

        return [c.to_message() for c in result]

    def _is_in_tail(self, chunk: MemoryChunk) -> bool:
        """Check if chunk is in the tail (recent) region."""
        if not self._chunks:
            return False

        # Get the most recent turn index
        max_turn = max(c.turn_index for c in self._chunks.values())

        # Tail = last N turns
        tail_start_turn = max_turn - (self._config.tail_preserve_count * 2) + 1

        return chunk.turn_index >= tail_start_turn

    def promote_to_episodic(self, item_id: str, reason: str) -> bool:
        """Promote an item from working to episodic memory.

        Returns True if item was found and queued for promotion.
        """
        if item_id not in self._chunks:
            return False

        # Mark for promotion
        if item_id not in self._episodic_promotion_queue:
            self._episodic_promotion_queue.append(item_id)
            logger.info(
                "Queued chunk %s for episodic promotion (reason=%s)",
                item_id[:8],
                reason,
            )

        return True

    def promote_to_semantic(self, item_id: str, reason: str) -> bool:
        """Promote an item from working directly to semantic memory.

        This bypasses episodic storage for high-importance items
        that should be preserved long-term.

        Returns True if item was found and queued for promotion.
        """
        if item_id not in self._chunks:
            return False

        # Mark for direct semantic promotion
        if item_id not in self._semantic_promotion_queue:
            self._semantic_promotion_queue.append(item_id)
            logger.info(
                "Queued chunk %s for semantic promotion (reason=%s)",
                item_id[:8],
                reason,
            )

        return True

    def get_promotion_queue(self) -> list[str]:
        """Get list of chunk IDs pending episodic promotion."""
        return list(self._episodic_promotion_queue)

    def get_semantic_promotion_queue(self) -> list[str]:
        """Get list of chunk IDs pending semantic promotion."""
        return list(self._semantic_promotion_queue)

    def clear_promotion_queue(self) -> None:
        """Clear the episodic promotion queue after items have been promoted."""
        self._episodic_promotion_queue.clear()

    def clear_semantic_promotion_queue(self) -> None:
        """Clear the semantic promotion queue after items have been promoted."""
        self._semantic_promotion_queue.clear()

    def clear(self) -> None:
        """Clear the entire working memory window."""
        self._chunks.clear()
        self._total_tokens = 0
        self._compression_triggered = None
        self._episodic_promotion_queue.clear()
        self._semantic_promotion_queue.clear()
        logger.info("Working memory window cleared")

    def reset_turn(self) -> None:
        """Increment turn counter (call at end of each turn)."""
        self._turn_index += 1


# Type annotation for the protocol
WorkingMemoryWindow.__protocol__ = WorkingMemoryPort  # type: ignore[attr-defined]


__all__ = [
    "ChunkPriority",
    "MemoryChunk",
    "WorkingMemoryConfig",
    "WorkingMemoryPort",
    "WorkingMemorySnapshot",
    "WorkingMemoryWindow",
]
