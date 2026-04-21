"""Intelligent Context Compressor - Smart Summarization + Importance Ranking.

This module provides intelligent context compression that:
- Scores items by importance (time decay, reference frequency, semantic signals)
- Selects the most important items greedily within token budget
- Summarizes low-priority items when they don't fit

Architecture:
    - ImportanceScorer: Computes importance scores for context items
    - IntelligentCompressor: Main compressor using greedy selection + LLM summarization

Design constraints:
    - KernelOne-only: no Polaris business semantics
    - All text I/O uses explicit UTF-8 encoding
    - 100% type annotations, complete docstrings
"""

from __future__ import annotations

import heapq
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context._token_estimator import estimate_tokens as _estimate_tokens_from_module

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.models_v2 import ContextOSProjectionV2 as ContextOSProjection
    from polaris.kernelone.llm.engine.client import LLMProvider

# Constants for importance scoring
_DECAY_HALF_LIFE_HOURS: float = 168.0  # One week half-life
_REFERENCE_BOOST: float = 0.1
_DECISION_BOOST: float = 0.5
_ERROR_BOOST: float = 0.3
_TOOL_RESULT_BOOST: float = 0.2
_PINNED_BOOST: float = 1.0

# Token estimation
_CHARS_PER_TOKEN: float = 4.0

# High-signal patterns for semantic importance
_HIGH_SIGNAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
    re.compile(r"\bexception\b", re.IGNORECASE),
    re.compile(r"\btraceback\b", re.IGNORECASE),
    re.compile(r"\bdecide[sd]?\b", re.IGNORECASE),
    re.compile(r"\bdecision\b", re.IGNORECASE),
    re.compile(r"\bselect[ed]?\b", re.IGNORECASE),
    re.compile(r"\bchoice\b", re.IGNORECASE),
    re.compile(r"\btool[_-]?result\b", re.IGNORECASE),
    re.compile(r"\bimplemented\b", re.IGNORECASE),
    re.compile(r"\bcompleted\b", re.IGNORECASE),
    re.compile(r"\bfixed\b", re.IGNORECASE),
    re.compile(r"\brefactored\b", re.IGNORECASE),
    re.compile(r"\b重构\b", re.IGNORECASE),
    re.compile(r"\b修复\b", re.IGNORECASE),
    re.compile(r"\b决定\b", re.IGNORECASE),
    re.compile(r"\b决策\b", re.IGNORECASE),
    re.compile(r"\b错误\b", re.IGNORECASE),
    re.compile(r"\b异常\b", re.IGNORECASE),
]

# Low-signal patterns that reduce importance
_LOW_SIGNAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(hi|hello|hey|你好|您好|嗨|thanks|thank you|谢谢|ok|好的|收到|稍等|bye|再见)\b", re.IGNORECASE),
    re.compile(r"(换个名字|改名字|改名|叫我|叫你|你是什么模型|what model are you|who are you)", re.IGNORECASE),
]


@dataclass(frozen=True)
class CompressionResult:
    """Result of context compression operation.

    Attributes:
        compressed_content: The assembled compressed context string.
        original_tokens: Total tokens before compression.
        compressed_tokens: Total tokens after compression.
        compression_ratio: Ratio of compressed to original (lower = better).
        preserved_key_points: Tuple of key points preserved from the original context.
    """

    compressed_content: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    preserved_key_points: tuple[str, ...] = ()


@dataclass
class ScoredItem:
    """A context item with its importance score.

    Attributes:
        score: Computed importance score.
        item: The transcript event being scored.
        estimated_tokens: Token estimate for this item.
    """

    score: float
    item: Any  # TranscriptEvent
    estimated_tokens: int

    def __lt__(self, other: ScoredItem) -> bool:
        """Compare by score for heap operations."""
        return self.score < other.score


class ImportanceScorer:
    """Computes importance scores for context items.

    The scorer evaluates items based on:
    1. Time decay: Recent items score higher
    2. Reference frequency: Items referenced more often score higher
    3. Semantic importance: Decision, error, and tool result signals
    4. User pinning: Explicitly pinned items score highest

    Example:
        scorer = ImportanceScorer()
        score = scorer.score(transcript_event)
    """

    def score(self, item: Any) -> float:
        """Compute importance score for a context item.

        Args:
            item: A TranscriptEvent or similar context item.

        Returns:
            float: Importance score between 0.0 and infinity (higher = more important).
        """
        score: float = 0.0

        # 1. Time decay score
        score += self._time_decay(item)

        # 2. Reference frequency boost
        reference_count = self._get_reference_count(item)
        score += reference_count * _REFERENCE_BOOST

        # 3. Semantic importance signals
        if self._contains_decision(item):
            score += _DECISION_BOOST
        if self._contains_error(item):
            score += _ERROR_BOOST
        if self._contains_tool_result(item):
            score += _TOOL_RESULT_BOOST

        # 4. User pinning
        if self._is_pinned(item):
            score += _PINNED_BOOST

        return score

    def _time_decay(self, item: Any) -> float:
        """Calculate time decay score based on item age.

        Uses exponential decay with a half-life of one week (168 hours).
        Items older than a week score 0.0.

        Args:
            item: A TranscriptEvent with a created_at attribute.

        Returns:
            float: Decay score between 0.0 and 1.0.
        """
        created_at = getattr(item, "created_at", None)
        if not created_at:
            return 0.0

        # Parse timestamp
        try:
            if isinstance(created_at, str):
                # Try ISO format parsing
                try:
                    timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except ValueError:
                    # Try simpler parsing
                    timestamp = datetime.fromisoformat(created_at)
            elif isinstance(created_at, datetime):
                timestamp = created_at
            else:
                return 0.0
        except (ValueError, TypeError):
            return 0.0

        # Ensure UTC
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        age_hours = (now - timestamp).total_seconds() / 3600

        # Exponential decay: score = 0.5^(age / half_life)
        if age_hours >= _DECAY_HALF_LIFE_HOURS:
            return 0.0

        decay_score: float = 0.5 ** (age_hours / _DECAY_HALF_LIFE_HOURS)
        return decay_score

    def _get_reference_count(self, item: Any) -> int:
        """Extract reference count from item metadata.

        Args:
            item: A TranscriptEvent with metadata dict.

        Returns:
            int: Number of times this item was referenced.
        """
        metadata = getattr(item, "metadata", None)
        if not isinstance(metadata, dict):
            return 0
        return max(0, int(metadata.get("reference_count", 0)))

    def _contains_decision(self, item: Any) -> bool:
        """Check if item contains decision-related content.

        Args:
            item: A TranscriptEvent with content attribute.

        Returns:
            bool: True if item contains decision signals.
        """
        content = self._get_content(item)
        if not content:
            return False

        # Check metadata flags first
        metadata = getattr(item, "metadata", None)
        if isinstance(metadata, dict) and (metadata.get("contains_decision") or metadata.get("is_decision")):
            return True

        # Check content patterns
        return any(pattern.search(content) for pattern in _HIGH_SIGNAL_PATTERNS[:6])

    def _contains_error(self, item: Any) -> bool:
        """Check if item contains error-related content.

        Args:
            item: A TranscriptEvent with content attribute.

        Returns:
            bool: True if item contains error signals.
        """
        content = self._get_content(item)
        if not content:
            return False

        # Check metadata flags first
        metadata = getattr(item, "metadata", None)
        if isinstance(metadata, dict) and (metadata.get("contains_error") or metadata.get("is_error")):
            return True

        # Check content patterns
        return any(pattern.search(content) for pattern in _HIGH_SIGNAL_PATTERNS[:4])

    def _contains_tool_result(self, item: Any) -> bool:
        """Check if item contains tool result content.

        Args:
            item: A TranscriptEvent with content attribute.

        Returns:
            bool: True if item contains tool result signals.
        """
        content = self._get_content(item)
        if not content:
            return False

        # Check metadata flags first
        metadata = getattr(item, "metadata", None)
        if isinstance(metadata, dict) and (metadata.get("contains_tool_result") or metadata.get("is_tool_result")):
            return True

        # Check kind attribute
        kind = getattr(item, "kind", None)
        if kind and "tool" in str(kind).lower():
            return True

        # Check content patterns
        return bool(_HIGH_SIGNAL_PATTERNS[8].search(content))  # tool_result pattern

    def _is_pinned(self, item: Any) -> bool:
        """Check if item is explicitly pinned by user.

        Args:
            item: A TranscriptEvent with metadata dict.

        Returns:
            bool: True if item is pinned.
        """
        metadata = getattr(item, "metadata", None)
        if not isinstance(metadata, dict):
            return False
        return bool(metadata.get("is_pinned", False) or metadata.get("pinned", False))

    def _get_content(self, item: Any) -> str:
        """Extract content string from item.

        Args:
            item: A TranscriptEvent with content attribute.

        Returns:
            str: The content string, or empty string if not available.
        """
        content = getattr(item, "content", None)
        return str(content) if content else ""


class IntelligentCompressor:
    """Intelligent context compressor with importance-based selection.

    This compressor:
    1. Scores all items by importance using ImportanceScorer
    2. Greedily selects the highest-scoring items within token budget
    3. Summarizes low-priority items that don't fit using LLM
    4. Builds a compressed context string preserving key information

    Usage:
        compressor = IntelligentCompressor(llm=provider, max_tokens=32000)
        result = await compressor.compress(context_projection, target_tokens=20000)

    Attributes:
        _llm: LLM provider for summarization.
        _max_tokens: Maximum tokens allowed in compressed context.
        _scorer: ImportanceScorer instance.
        _default_compression_ratio: Default target is 70% of max_tokens.
    """

    def __init__(
        self,
        llm: LLMProvider,
        *,
        max_tokens: int = 32000,
    ) -> None:
        """Initialize the intelligent compressor.

        Args:
            llm: LLM provider for summarization tasks.
            max_tokens: Maximum tokens in compressed context. Default 32000.
        """
        self._llm = llm
        self._max_tokens = max_tokens
        self._scorer = ImportanceScorer()
        self._default_compression_ratio: float = 0.7

    async def compress(
        self,
        context: ContextOSProjection,
        target_tokens: int | None = None,
    ) -> CompressionResult:
        """Compress context using importance-based selection.

        Args:
            context: The ContextOSProjection to compress.
            target_tokens: Target token count. Defaults to 70% of max_tokens.

        Returns:
            CompressionResult with compressed content and statistics.
        """
        target = target_tokens or int(self._max_tokens * self._default_compression_ratio)

        # Extract items from active_window
        items = list(context.active_window) if context.active_window else []

        if not items:
            return CompressionResult(
                compressed_content="",
                original_tokens=0,
                compressed_tokens=0,
                compression_ratio=1.0,
                preserved_key_points=(),
            )

        # 1. Score all items
        scored_items = self._score_items(items)
        heapq.heapify(scored_items)

        # 2. Calculate original tokens
        original_tokens = sum(item.estimated_tokens for item in scored_items)

        # 3. Greedy selection within budget
        selected: list[Any] = []
        total_tokens = 0
        key_points: list[str] = []

        while scored_items:
            scored = heapq.heappop(scored_items)

            if total_tokens + scored.estimated_tokens <= target:
                selected.append(scored.item)
                total_tokens += scored.estimated_tokens
                # Extract key point from this item
                key_point = self._extract_key_point(scored.item)
                if key_point:
                    key_points.append(key_point)
            else:
                # Try to summarize if we have some items but not all
                if selected:
                    # We have some content, try to add a summary of remaining
                    remaining = [s.item for s in scored_items]
                    if remaining:
                        summary = await self._summarize_items(remaining)
                        if summary:
                            summary_tokens = self._estimate_tokens(summary)
                            if total_tokens + summary_tokens <= target:
                                selected.append({"_summary": summary})
                                total_tokens += summary_tokens
                                key_points.append(summary[:200])
                break

        # 4. Build compressed content
        compressed_content = self._build_compressed_context(selected)

        # 5. Calculate compression ratio (clamp to [0.0, 1.0] to handle edge case
        # where added summary tokens exceed saved tokens, giving ratio > 1.0)
        compression_ratio = min(1.0, total_tokens / original_tokens) if original_tokens > 0 else 1.0

        return CompressionResult(
            compressed_content=compressed_content,
            original_tokens=original_tokens,
            compressed_tokens=total_tokens,
            compression_ratio=compression_ratio,
            preserved_key_points=tuple(key_points),
        )

    def _score_items(self, items: list[Any]) -> list[ScoredItem]:
        """Score a list of items using the importance scorer.

        Args:
            items: List of TranscriptEvent items to score.

        Returns:
            List of ScoredItem objects with scores and token estimates.
        """
        scored: list[ScoredItem] = []
        for item in items:
            score = self._scorer.score(item)
            tokens = self._estimate_tokens_from_item(item)
            scored.append(ScoredItem(score=score, item=item, estimated_tokens=tokens))
        return scored

    def _estimate_tokens_from_item(self, item: Any) -> int:
        """Estimate token count for a context item.

        Args:
            item: A TranscriptEvent or dict with content.

        Returns:
            int: Estimated token count.
        """
        content = getattr(item, "content", None) or ""
        if isinstance(item, dict):
            content = item.get("content", "")

        if not content:
            return 1

        return max(1, int(len(str(content)) / _CHARS_PER_TOKEN))

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for a text string.

        Args:
            text: The text to estimate.

        Returns:
            int: Estimated token count.
        """
        return _estimate_tokens_from_module(text)

    async def _summarize_items(self, items: list[Any]) -> str | None:
        """Summarize multiple low-priority items using LLM.

        When LLM summarization fails, falls back to deterministic truncation
        to ensure compression still produces meaningful output.

        Args:
            items: List of TranscriptEvent items to summarize.

        Returns:
            str: Summary text, or None if summarization fails.
        """
        if not items or not self._llm:
            return self._fallback_summarize(items)

        # Build content from items
        content_parts: list[str] = []
        for item in items[-10:]:  # Limit to last 10 items
            content = getattr(item, "content", None) or ""
            if content:
                content_parts.append(str(content))

        if not content_parts:
            return self._fallback_summarize(items)

        combined_content = "\n---\n".join(content_parts[:20])  # Limit combined length

        prompt = self._build_summary_prompt(combined_content)

        try:
            from polaris.kernelone.llm.shared_contracts import AIRequest, TaskType

            request = AIRequest(
                task_type=TaskType.GENERATION,
                role="system",
                input=prompt,
                options={
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            response = await self._llm.invoke(request)
            if response.ok and response.output:
                return str(response.output).strip()
        except (RuntimeError, ValueError):
            # Fallback: deterministic truncation on LLM failure
            logger.warning("LLM summarization failed, using deterministic fallback")
            pass

        return self._fallback_summarize(items)

    def _fallback_summarize(self, items: list[Any]) -> str | None:
        """Deterministic fallback summarization when LLM is unavailable.

        Keeps first 50% of items, truncates rest to digest.

        Args:
            items: List of TranscriptEvent items to summarize.

        Returns:
            str: Deterministic summary text, or None if items empty.
        """
        if not items:
            return None

        # Keep first 50% of items, use rest for fallback digest
        midpoint = max(1, len(items) // 2)
        remaining = items[midpoint:]

        # Build digest from remaining items (first 200 chars each)
        fallback_parts: list[str] = []
        for item in remaining:
            content = getattr(item, "content", None) or ""
            if isinstance(item, dict):
                content = item.get("content", "")
            if content:
                truncated = str(content)[:200]
                fallback_parts.append(truncated)

        fallback_summary = "; ".join(fallback_parts[:5])  # Max 5 items in fallback
        summary = f"[Compressed context: {fallback_summary}]" if fallback_summary else None

        return summary

    def _build_summary_prompt(self, content: str) -> str:
        """Build prompt for LLM summarization.

        Args:
            content: The content to summarize.

        Returns:
            str: The summary prompt.
        """
        return f"""Summarize the following context items concisely, preserving key information:

---
{content[:3000]}
---

Provide a brief summary (2-3 sentences) that captures the key points."""

    def _extract_key_point(self, item: Any) -> str:
        """Extract a key point string from an item.

        Args:
            item: A TranscriptEvent or dict.

        Returns:
            str: A key point string, or empty string if extraction fails.
        """
        content = getattr(item, "content", None) or ""
        if isinstance(item, dict):
            content = item.get("content", "")

        if not content:
            return ""

        content_str = str(content)

        # Truncate long content
        if len(content_str) > 150:
            return content_str[:147] + "..."

        return content_str

    def _build_compressed_context(self, selected: list[Any]) -> str:
        """Build the compressed context string from selected items.

        Args:
            selected: List of selected items (TranscriptEvents or dicts).

        Returns:
            str: The compressed context string.
        """
        if not selected:
            return ""

        parts: list[str] = []

        for item in selected:
            # Handle summary items
            if isinstance(item, dict) and "_summary" in item:
                parts.append(f"[Summary of compressed context]:\n{item['_summary']}")
                continue

            # Extract content from TranscriptEvent or dict
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content", "")

            if content:
                role = (
                    getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None) or "assistant"
                )
                parts.append(f"[{role}]: {content}")

        return "\n\n".join(parts)


__all__ = [
    "CompressionResult",
    "ImportanceScorer",
    "IntelligentCompressor",
]
