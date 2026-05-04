"""Multi-Resolution Store: content storage at multiple resolutions for adaptive recall.

This module implements ContextOS 3.0 Phase 1: Multi-Resolution Store.
Instead of binary include/drop decisions, ContextOS can now select from
multiple resolutions of the same content based on budget pressure.

Resolutions:
    L0_FULL: Original content (never auto-deleted)
    L1_EXTRACTIVE: Key original fragments (~30% of original)
    L2_STRUCTURED: Structured summary (~10% of original)
    L3_STUB: One-line index/pointer (~2% of original)

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Original content is NEVER deleted. Compression only creates new projections.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.context.context_os.content_store import ContentRef, ContentStore, RefTracker

logger = logging.getLogger(__name__)


class ResolutionLevel(str, Enum):
    """Content resolution levels for multi-resolution storage."""

    L0_FULL = "full"
    L1_EXTRACTIVE = "extractive"
    L2_STRUCTURED = "structured"
    L3_STUB = "stub"


@dataclass(frozen=True, slots=True)
class ResolutionEntry:
    """A single resolution of content stored in the Multi-Resolution Store."""

    level: ResolutionLevel
    content_ref: ContentRef
    token_count: int
    created_at: str = ""
    derived_from: str = ""  # hash of parent resolution
    compression_policy: str = ""  # "extractive" | "structured" | "stub"
    lossiness: float = 0.0  # 0.0 = lossless, 1.0 = total loss

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "content_ref": self.content_ref.hash,
            "token_count": self.token_count,
            "created_at": self.created_at,
            "derived_from": self.derived_from,
            "compression_policy": self.compression_policy,
            "lossiness": self.lossiness,
        }


@dataclass(frozen=True, slots=True)
class MultiResolutionContent:
    """Content stored at multiple resolutions for adaptive recall.

    This is the core data structure of the Multi-Resolution Store.
    Each piece of content can be accessed at different resolutions
    depending on budget pressure.
    """

    content_id: str  # SHA-256 hash of original content
    resolutions: dict[ResolutionLevel, ResolutionEntry] = field(default_factory=dict)

    def get_resolution(self, level: ResolutionLevel) -> ResolutionEntry | None:
        """Get a specific resolution of the content."""
        return self.resolutions.get(level)

    def has_resolution(self, level: ResolutionLevel) -> bool:
        """Check if a specific resolution exists."""
        return level in self.resolutions

    def get_best_available_resolution(
        self,
        preferred_level: ResolutionLevel = ResolutionLevel.L0_FULL,
    ) -> ResolutionEntry | None:
        """Get the best available resolution, falling back to lower resolutions."""
        # Try preferred level first
        if preferred_level in self.resolutions:
            return self.resolutions[preferred_level]

        # Fall back to lower resolutions
        fallback_order = [
            ResolutionLevel.L0_FULL,
            ResolutionLevel.L1_EXTRACTIVE,
            ResolutionLevel.L2_STRUCTURED,
            ResolutionLevel.L3_STUB,
        ]

        # Find the highest available resolution that's <= preferred
        preferred_idx = fallback_order.index(preferred_level)
        for level in fallback_order[preferred_idx:]:
            if level in self.resolutions:
                return self.resolutions[level]

        # If nothing found, try any available resolution
        for level in fallback_order:
            if level in self.resolutions:
                return self.resolutions[level]

        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_id": self.content_id,
            "resolutions": {level.value: entry.to_dict() for level, entry in self.resolutions.items()},
        }


class MultiResolutionStore:
    """Content store with multi-resolution support.

    When memory pressure increases:
    1. First: evict 'full' resolutions (keep summary + stub)
    2. Then: evict 'summary' resolutions (keep stub only)
    3. Finally: evict stubs (keep peek only)

    When an evicted resolution is needed:
    1. Try to reconstruct from higher-resolution version
    2. If unavailable, return placeholder with reconstruction hint

    Key Constraint (INV-6): Original content never auto-deleted.
    Compression only creates new projections.
    """

    def __init__(
        self,
        content_store: ContentStore,
        ref_tracker: RefTracker | None = None,
    ) -> None:
        self._content_store = content_store
        self._ref_tracker = ref_tracker
        self._multi_resolution_map: dict[str, MultiResolutionContent] = {}
        self._resolution_counts: dict[ResolutionLevel, int] = dict.fromkeys(ResolutionLevel, 0)

    def intern_with_resolutions(
        self,
        content: str,
        extractive_content: str | None = None,
        structured_content: str | None = None,
        stub_content: str | None = None,
    ) -> MultiResolutionContent:
        """Intern content with optional pre-computed resolutions.

        Args:
            content: Original full content
            extractive_content: Key fragments (~30% of original)
            structured_content: Structured summary (~10% of original)
            stub_content: One-line index (~2% of original)

        Returns:
            MultiResolutionContent with all available resolutions
        """
        # Intern original content (L0_FULL)
        full_ref = self._content_store.intern(content)
        if self._ref_tracker:
            self._ref_tracker.acquire(full_ref)

        content_id = full_ref.hash
        resolutions: dict[ResolutionLevel, ResolutionEntry] = {}

        resolutions[ResolutionLevel.L0_FULL] = ResolutionEntry(
            level=ResolutionLevel.L0_FULL,
            content_ref=full_ref,
            token_count=self._estimate_tokens(content),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            compression_policy="none",
            lossiness=0.0,
        )

        # Intern extractive content (L1_EXTRACTIVE)
        if extractive_content:
            extractive_ref = self._content_store.intern(extractive_content)
            if self._ref_tracker:
                self._ref_tracker.acquire(extractive_ref)
            resolutions[ResolutionLevel.L1_EXTRACTIVE] = ResolutionEntry(
                level=ResolutionLevel.L1_EXTRACTIVE,
                content_ref=extractive_ref,
                token_count=self._estimate_tokens(extractive_content),
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                derived_from=content_id,
                compression_policy="extractive",
                lossiness=0.3,
            )

        # Intern structured content (L2_STRUCTURED)
        if structured_content:
            structured_ref = self._content_store.intern(structured_content)
            if self._ref_tracker:
                self._ref_tracker.acquire(structured_ref)
            resolutions[ResolutionLevel.L2_STRUCTURED] = ResolutionEntry(
                level=ResolutionLevel.L2_STRUCTURED,
                content_ref=structured_ref,
                token_count=self._estimate_tokens(structured_content),
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                derived_from=content_id,
                compression_policy="structured",
                lossiness=0.6,
            )

        # Intern stub content (L3_STUB)
        if stub_content:
            stub_ref = self._content_store.intern(stub_content)
            if self._ref_tracker:
                self._ref_tracker.acquire(stub_ref)
            resolutions[ResolutionLevel.L3_STUB] = ResolutionEntry(
                level=ResolutionLevel.L3_STUB,
                content_ref=stub_ref,
                token_count=self._estimate_tokens(stub_content),
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                derived_from=content_id,
                compression_policy="stub",
                lossiness=0.9,
            )

        multi_res_content = MultiResolutionContent(
            content_id=content_id,
            resolutions=resolutions,
        )

        self._multi_resolution_map[content_id] = multi_res_content

        # Update counts
        for level in resolutions:
            self._resolution_counts[level] += 1

        logger.info(
            "Multi-resolution intern: content_id=%s resolutions=%d",
            content_id[:8],
            len(resolutions),
        )

        return multi_res_content

    def get_content(
        self,
        content_id: str,
        preferred_level: ResolutionLevel = ResolutionLevel.L0_FULL,
    ) -> str | None:
        """Get content at preferred resolution, falling back if needed.

        Args:
            content_id: SHA-256 hash of original content
            preferred_level: Preferred resolution level

        Returns:
            Content string, or None if not found
        """
        multi_res = self._multi_resolution_map.get(content_id)
        if multi_res is None:
            return None

        entry = multi_res.get_best_available_resolution(preferred_level)
        if entry is None:
            return None

        content = self._content_store.get(entry.content_ref)

        # If content was evicted, try to reconstruct from higher resolution
        if content is None:
            content = self._try_reconstruct(multi_res, preferred_level)

        return content

    def _try_reconstruct(
        self,
        multi_res: MultiResolutionContent,
        preferred_level: ResolutionLevel,
    ) -> str:
        """Try to reconstruct content from higher resolution if available."""
        # Try to get a higher resolution and summarize it
        higher_levels = {
            ResolutionLevel.L3_STUB: ResolutionLevel.L2_STRUCTURED,
            ResolutionLevel.L2_STRUCTURED: ResolutionLevel.L1_EXTRACTIVE,
            ResolutionLevel.L1_EXTRACTIVE: ResolutionLevel.L0_FULL,
        }

        current_level = preferred_level
        while current_level in higher_levels:
            next_level = higher_levels[current_level]
            if next_level in multi_res.resolutions:
                entry = multi_res.resolutions[next_level]
                content = self._content_store.get(entry.content_ref)
                if content is not None:
                    # Got higher resolution content
                    logger.info(
                        "Reconstructed %s from %s for content_id=%s",
                        preferred_level.value,
                        next_level.value,
                        multi_res.content_id[:8],
                    )
                    return content
            current_level = next_level

        # All resolutions evicted - return placeholder
        return f"<evicted:{multi_res.content_id}>"

    def get_content_ref(
        self,
        content_id: str,
        preferred_level: ResolutionLevel = ResolutionLevel.L0_FULL,
    ) -> ContentRef | None:
        """Get ContentRef at preferred resolution, falling back if needed."""
        multi_res = self._multi_resolution_map.get(content_id)
        if multi_res is None:
            return None

        entry = multi_res.get_best_available_resolution(preferred_level)
        if entry is None:
            return None

        return entry.content_ref

    def has_content(self, content_id: str) -> bool:
        """Check if content exists in the store."""
        return content_id in self._multi_resolution_map

    def get_resolution_count(self, level: ResolutionLevel) -> int:
        """Get count of contents at a specific resolution level."""
        return self._resolution_counts.get(level, 0)

    @property
    def stats(self) -> dict[str, Any]:
        """Get store statistics."""
        return {
            "total_contents": len(self._multi_resolution_map),
            "resolution_counts": {level.value: count for level, count in self._resolution_counts.items()},
            "content_store_stats": self._content_store.stats if hasattr(self._content_store, "stats") else {},
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count for text."""
        if not text:
            return 0
        # Simple heuristic: ASCII chars / 4, CJK chars * 1.5
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        cjk_chars = len(text) - ascii_chars
        return max(1, int(ascii_chars / 4 + cjk_chars * 1.5))


def create_stub_content(content: str, max_chars: int = 200) -> str:
    """Create a stub (one-line index) from content."""
    if len(content) <= max_chars:
        return content
    # Extract first line or first max_chars
    first_line = content.split("\n")[0][:max_chars]
    return f"{first_line}..."


def create_extractive_content(content: str, max_ratio: float = 0.3) -> str:
    """Create extractive content (key fragments) from content."""
    if not content:
        return ""
    target_len = int(len(content) * max_ratio)
    if len(content) <= target_len:
        return content
    # Simple extractive: first 70% + last 30%
    head_len = int(target_len * 0.7)
    tail_len = target_len - head_len
    if head_len <= 0 or tail_len <= 0:
        return content[:target_len]
    return f"{content[:head_len]}\n...[extracted]...\n{content[-tail_len:]}"


def create_structured_content(content: str, max_ratio: float = 0.1) -> str:
    """Create structured summary from content."""
    if not content:
        return ""
    target_len = int(len(content) * max_ratio)
    if len(content) <= target_len:
        return content
    # Simple structured: first line + line count + last line
    lines = content.split("\n")
    if len(lines) <= 1:
        # Single line content - just truncate
        return content[:target_len]
    first_line = lines[0] if lines else ""
    last_line = lines[-1] if lines else ""
    return f"{first_line}\n[{len(lines)} lines total]\n{last_line}"
