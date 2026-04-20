"""Hot-swap mechanism for profession switching within a session.

This module enables dynamic switching of professions without disrupting
the existing session context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SwapReason(Enum):
    """Reason for profession swap."""

    USER_REQUEST = "user_request"
    TASK_COMPLEXITY = "task_complexity"
    SECURITY_TRIGGER = "security_trigger"
    FALLBACK = "fallback"
    MANUAL = "manual"


@dataclass
class SwapEvent:
    """Record of a profession swap event."""

    from_profession: str | None
    to_profession: str
    reason: SwapReason
    context_snapshot: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FallbackChain:
    """Defines fallback chain for profession switches."""

    primary: str
    fallbacks: list[str] = field(default_factory=list)

    def get_all(self) -> list[str]:
        """Get all professions in fallback order."""
        return [self.primary, *self.fallbacks]


@dataclass
class PromptModifier:
    """Modifier to be applied to the prompt during hot-swap.

    This allows adding format overrides, standards additions, etc.
    without modifying the underlying profession configuration.
    """

    modifier_type: str
    content: str
    priority: int = 0  # Higher priority applied later


@dataclass
class HotSwapContext:
    """Context maintained during a hot-swap operation."""

    active_profession: str
    swap_history: list[SwapEvent] = field(default_factory=list)
    active_persona: str | None = None
    active_anchor: str | None = None
    pending_modifiers: list[PromptModifier] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def record_swap(self, event: SwapEvent) -> None:
        """Record a swap event."""
        self.swap_history.append(event)
        self.active_profession = event.to_profession

    def can_swap_to(self, profession_id: str, max_swaps: int = 5) -> bool:
        """Check if swap is allowed (rate limiting)."""
        # Count swaps to same profession
        same_profession_swaps = sum(1 for e in self.swap_history if e.to_profession == profession_id)
        return same_profession_swaps < max_swaps

    def add_modifier(self, modifier: PromptModifier) -> None:
        """Add a prompt modifier to be applied."""
        self.pending_modifiers.append(modifier)
        self.pending_modifiers.sort(key=lambda m: m.priority)

    def get_modifiers(self) -> list[PromptModifier]:
        """Get all pending modifiers."""
        return self.pending_modifiers.copy()

    def clear_modifiers(self) -> None:
        """Clear all pending modifiers."""
        self.pending_modifiers.clear()


class HotSwapEngine:
    """Engine for managing profession hot-swap operations."""

    def __init__(self) -> None:
        self._contexts: dict[str, HotSwapContext] = {}  # session_id -> context
        self._fallback_chains: dict[str, FallbackChain] = {}

    def get_or_create_context(self, session_id: str) -> HotSwapContext:
        """Get or create a hot-swap context for a session."""
        if session_id not in self._contexts:
            self._contexts[session_id] = HotSwapContext(
                active_profession="",
                swap_history=[],
            )
        return self._contexts[session_id]

    def register_fallback_chain(self, profession_id: str, chain: FallbackChain) -> None:
        """Register a fallback chain for a profession."""
        self._fallback_chains[profession_id] = chain

    def swap(
        self,
        session_id: str,
        new_profession: str,
        reason: SwapReason,
        context_snapshot: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Perform a hot-swap to a new profession.

        Args:
            session_id: Session identifier
            new_profession: Target profession ID
            reason: Reason for the swap
            context_snapshot: Current context snapshot
            metadata: Optional additional metadata

        Returns:
            True if swap succeeded, False if blocked (rate limit)
        """
        context = self.get_or_create_context(session_id)

        # Check rate limit
        if not context.can_swap_to(new_profession):
            logger.warning(f"Hot-swap blocked for session {session_id} to {new_profession}: rate limit exceeded")
            return False

        # Create swap event
        event = SwapEvent(
            timestamp=datetime.now(),
            from_profession=context.active_profession,
            to_profession=new_profession,
            reason=reason,
            context_snapshot=context_snapshot,
            metadata=metadata or {},
        )

        # Record the swap
        context.record_swap(event)

        logger.info(
            f"Hot-swap performed: session={session_id}, "
            f"from={event.from_profession}, to={new_profession}, "
            f"reason={reason.value}"
        )

        return True

    def swap_with_fallback(
        self,
        session_id: str,
        primary_profession: str,
        reason: SwapReason,
        context_snapshot: dict[str, Any],
    ) -> str | None:
        """Swap with automatic fallback if primary fails.

        Args:
            session_id: Session identifier
            primary_profession: Primary target profession
            reason: Reason for the swap
            context_snapshot: Current context snapshot

        Returns:
            The profession that was successfully swapped to, or None
        """
        # Try primary first (self.swap() already checks can_swap_to internally)
        if self.swap(session_id, primary_profession, reason, context_snapshot):
            return primary_profession

        # Try fallback chain
        if primary_profession in self._fallback_chains:
            chain = self._fallback_chains[primary_profession]
            for fallback_profession in chain.fallbacks:
                if self.swap(session_id, fallback_profession, SwapReason.FALLBACK, context_snapshot):
                    return fallback_profession

        logger.warning(f"Hot-swap with fallback failed for session {session_id}, primary={primary_profession}")
        return None

    def rollback(self, session_id: str, context_snapshot: dict[str, Any]) -> bool:
        """Rollback to the previous profession.

        Args:
            session_id: Session identifier
            context_snapshot: Current context snapshot

        Returns:
            True if rollback succeeded, False if no previous state
        """
        context = self.get_or_create_context(session_id)

        if not context.swap_history:
            return False

        # Find the previous profession
        last_swap = context.swap_history[-1]
        if last_swap.from_profession:
            return self.swap(
                session_id,
                last_swap.from_profession,
                SwapReason.FALLBACK,
                context_snapshot,
            )

        logger.warning(
            f"Rollback failed for session {session_id}: last swap had no previous profession (initial state)"
        )
        return False

    def get_swap_history(self, session_id: str) -> list[SwapEvent]:
        """Get the swap history for a session."""
        context = self.get_or_create_context(session_id)
        return context.swap_history.copy()

    def add_modifier(self, session_id: str, modifier_type: str, content: str, priority: int = 0) -> None:
        """Add a prompt modifier for the session."""
        context = self.get_or_create_context(session_id)
        context.add_modifier(PromptModifier(modifier_type=modifier_type, content=content, priority=priority))

    def get_modifiers(self, session_id: str) -> list[PromptModifier]:
        """Get all pending modifiers for the session."""
        context = self.get_or_create_context(session_id)
        return context.get_modifiers()

    def clear_context(self, session_id: str) -> None:
        """Clear the hot-swap context for a session."""
        if session_id in self._contexts:
            del self._contexts[session_id]


# Global hot-swap engine instance
_hot_swap_engine: HotSwapEngine | None = None


def get_hot_swap_engine() -> HotSwapEngine:
    """Get the global HotSwapEngine instance."""
    global _hot_swap_engine
    if _hot_swap_engine is None:
        _hot_swap_engine = HotSwapEngine()
    return _hot_swap_engine
