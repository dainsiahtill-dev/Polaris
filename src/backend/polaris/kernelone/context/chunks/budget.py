"""Chunk-level token budget tracking for KernelOne prompt assembly.

Architecture:
    ChunkBudgetTracker wraps ContextBudgetGate with per-chunk accounting.
    It tracks which chunks were admitted, evicted, and why for observability.

Design constraints:
    - All text uses UTF-8 encoding.
    - Immutable chunk budget snapshots (ChunkBudget is frozen dataclass).
    - Thread-safe for async use (per-instance, no shared mutable state).
    - Uses char-based fallback when no token estimator is available.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.context.chunks.taxonomy import PromptChunk


@dataclass(frozen=True)
class ChunkBudget:
    """Immutable snapshot of chunk-level budget state.

    This mirrors ContextBudget but adds per-chunk breakdown.
    """

    total_tokens: int
    admitted_chunks: int
    evicted_chunks: int
    model_window: int
    safety_margin: float

    @property
    def effective_limit(self) -> int:
        """Hard ceiling after safety margin."""
        return int(self.model_window * self.safety_margin)

    @property
    def usage_ratio(self) -> float:
        """How full the budget is (0.0-1.0+)."""
        if self.effective_limit <= 0:
            return 0.0
        return self.total_tokens / self.effective_limit

    @property
    def headroom(self) -> int:
        """Tokens still available."""
        return self.effective_limit - self.total_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "admitted_chunks": self.admitted_chunks,
            "evicted_chunks": self.evicted_chunks,
            "model_window": self.model_window,
            "safety_margin": self.safety_margin,
            "effective_limit": self.effective_limit,
            "usage_ratio": self.usage_ratio,
            "headroom": self.headroom,
        }


@dataclass
class ChunkBudgetTracker:
    """Token budget tracker with per-chunk admission tracking.

    Usage::

        tracker = ChunkBudgetTracker(
            model_window=128_000,
            safety_margin=0.85,
        )

        # Try to admit chunks
        admitted, evicted = tracker.try_admit_many(chunks)

        # Get current state
        budget = tracker.get_current_budget()
    """

    model_window: int
    safety_margin: float = 0.85
    _current_tokens: int = field(default=0, repr=False)
    _admitted_count: int = field(default=0, repr=False)
    _evicted_count: int = field(default=0, repr=False)
    _eviction_log: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _admission_log: list[dict[str, Any]] = field(default_factory=list, repr=False)
    # Sync-context lock: chunk budget is managed from sync code paths.
    # Use threading.Lock (not RLock) since no reentrancy is needed.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        # Ensure lock is always a Lock instance (dataclass might reset it)
        if not hasattr(self, "_lock") or not isinstance(self._lock, threading.Lock):
            object.__setattr__(self, "_lock", threading.Lock())

    def __init__(
        self,
        model_window: int,
        safety_margin: float = 0.85,
        *,
        initial_tokens: int = 0,
    ) -> None:
        if not isinstance(model_window, int) or model_window <= 0:
            raise ValueError(f"model_window must be positive int, got {model_window!r}")
        if not (0.0 < safety_margin <= 1.0):
            raise ValueError(f"safety_margin must be in (0.0, 1.0], got {safety_margin!r}")
        object.__setattr__(self, "model_window", model_window)
        object.__setattr__(self, "safety_margin", safety_margin)
        object.__setattr__(self, "_current_tokens", initial_tokens)
        object.__setattr__(self, "_admitted_count", 0)
        object.__setattr__(self, "_evicted_count", 0)
        object.__setattr__(self, "_eviction_log", [])
        object.__setattr__(self, "_admission_log", [])
        object.__setattr__(self, "_lock", threading.Lock())

    def get_current_budget(self) -> ChunkBudget:
        """Return immutable snapshot of current budget state.

        Thread-safe: holds lock during read to ensure consistent snapshot.
        """
        with self._lock:
            return ChunkBudget(
                total_tokens=self._current_tokens,
                admitted_chunks=self._admitted_count,
                evicted_chunks=self._evicted_count,
                model_window=self.model_window,
                safety_margin=self.safety_margin,
            )

    def try_admit(self, chunk: PromptChunk) -> tuple[bool, str]:
        """Try to admit a single chunk into the budget.

        Thread-safe: uses RLock to prevent TOCTOU race conditions in concurrent access.

        Returns:
            (True, "") if admitted
            (False, reason) if evicted due to budget constraints
        """
        tokens = chunk.tokens
        effective_limit = int(self.model_window * self.safety_margin)

        with self._lock:
            # Check if it fits (under lock to prevent TOCTOU race)
            if self._current_tokens + tokens <= effective_limit:
                object.__setattr__(self, "_current_tokens", self._current_tokens + tokens)
                object.__setattr__(self, "_admitted_count", self._admitted_count + 1)
                self._admission_log.append(
                    {
                        "chunk_type": chunk.chunk_type.value,
                        "tokens": tokens,
                        "char_count": chunk.chars,
                        "admitted": True,
                    }
                )
                return True, ""

            # Calculate eviction candidates
            ratio = self._current_tokens / effective_limit if effective_limit > 0 else 1.0
            reason = self._compute_eviction_reason(ratio)

            object.__setattr__(self, "_evicted_count", self._evicted_count + 1)
            self._eviction_log.append(
                {
                    "chunk_type": chunk.chunk_type.value,
                    "tokens": tokens,
                    "char_count": chunk.chars,
                    "reason": reason,
                }
            )
            return False, reason

    def try_admit_many(self, chunks: list[PromptChunk]) -> tuple[list[PromptChunk], list[PromptChunk]]:
        """Try to admit multiple chunks, evicting on budget overflow.

        Chunks are admitted in order, with lower-priority chunks evicted first.

        Returns:
            (admitted_chunks, evicted_chunks)
        """
        # Sort by eviction priority (higher priority first)
        sorted_chunks = sorted(chunks, key=lambda c: (-c.chunk_type.eviction_priority, c.tokens))

        admitted: list[PromptChunk] = []
        evicted: list[PromptChunk] = []

        for chunk in sorted_chunks:
            ok, _reason = self.try_admit(chunk)
            if ok:
                admitted.append(chunk)
            else:
                evicted.append(chunk)

        # Return in original order for deterministic output
        admitted.sort(key=chunks.index)
        evicted.sort(key=chunks.index)
        return admitted, evicted

    def can_add(self, tokens: int) -> tuple[bool, str]:
        """Check if adding tokens would stay within budget."""
        if tokens < 0:
            return False, "tokens may not be negative"
        effective_limit = int(self.model_window * self.safety_margin)
        if self._current_tokens + tokens <= effective_limit:
            return True, ""
        headroom = effective_limit - self._current_tokens
        return False, f"Adding {tokens} tokens exceeds budget ({headroom} headroom)"

    def record_usage(self, tokens: int) -> None:
        """Record consumed tokens (e.g., from final assembly)."""
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        object.__setattr__(self, "_current_tokens", self._current_tokens + tokens)

    def reset(self) -> None:
        """Reset all counters (start of new assembly pass)."""
        object.__setattr__(self, "_current_tokens", 0)
        object.__setattr__(self, "_admitted_count", 0)
        object.__setattr__(self, "_evicted_count", 0)
        self._eviction_log.clear()
        self._admission_log.clear()

    def get_admission_log(self) -> list[dict[str, Any]]:
        """Return admission decisions for debugging."""
        return list(self._admission_log)

    def get_eviction_log(self) -> list[dict[str, Any]]:
        """Return eviction decisions for debugging."""
        return list(self._eviction_log)

    def get_token_breakdown(self) -> dict[str, int]:
        """Return per-chunk-type token breakdown from admission log."""
        breakdown: dict[str, int] = {}
        for entry in self._admission_log:
            chunk_type = entry.get("chunk_type", "unknown")
            breakdown[chunk_type] = breakdown.get(chunk_type, 0) + entry.get("tokens", 0)
        return breakdown

    def _compute_eviction_reason(self, current_ratio: float) -> str:
        """Generate human-readable eviction reason."""
        pct = int(current_ratio * 100)
        if pct < 50:
            return f"Budget healthy ({pct}%), chunk exceeded hard limit"
        if pct < 75:
            return f"Budget approaching ({pct}%), chunk evicted for headroom"
        if pct < 90:
            return f"Budget critical ({pct}%), chunk evicted"
        return f"Budget overflow ({pct}%), chunk evicted"


__all__ = [
    "ChunkBudget",
    "ChunkBudgetTracker",
]
