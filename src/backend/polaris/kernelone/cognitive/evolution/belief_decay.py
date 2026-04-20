"""Belief Decay Engine - Time-based belief confidence attenuation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from polaris.kernelone.cognitive.evolution.models import Belief


@dataclass(frozen=True)
class DecayPolicy:
    """Configuration for belief decay behavior."""

    half_life_days: float = 30.0
    min_confidence: float = 0.1
    reinforcement_factor: float = 1.5
    max_reinforced_confidence: float = 0.95
    stale_threshold_days: float = 60.0


class BeliefDecayEngine:
    """Time- and reinforcement-based automatic belief attenuation.

    Algorithm per belief:
    1. Compute days since last verification (``verified_at`` if present,
       else ``created_at``).
    2. Apply exponential decay: ``confidence *= 0.5 ** (days / half_life)``.
    3. If the belief was referenced recently (``verified_at`` within the
       current half-life window), apply reinforcement:
       ``confidence *= reinforcement_factor``, capped at
       ``max_reinforced_confidence``.
    4. If the resulting confidence drops below ``min_confidence``, mark the
       belief as stale (confidence set to ``min_confidence``).
    """

    def __init__(self, policy: DecayPolicy | None = None) -> None:
        self._policy = policy or DecayPolicy()

    @property
    def policy(self) -> DecayPolicy:
        """Return the active decay policy."""
        return self._policy

    def apply_decay(
        self,
        beliefs: list[Belief],
        now: datetime | None = None,
    ) -> list[Belief]:
        """Apply time-based decay to a batch of beliefs.

        Args:
            beliefs: Beliefs to decay.
            now: Override for the current time (useful in tests).

        Returns:
            New list of ``Belief`` instances with updated confidence values.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        result: list[Belief] = []
        for belief in beliefs:
            days = self._days_since_verified(belief, now)
            if days <= 0:
                result.append(belief)
                continue

            decay_factor = self._calculate_decay_factor(days)

            new_confidence = belief.confidence * decay_factor

            # Reinforcement: if the belief was verified within the current
            # half-life window, boost confidence.
            if days < self._policy.half_life_days and belief.verified_at is not None:
                new_confidence *= self._policy.reinforcement_factor
                new_confidence = min(
                    new_confidence,
                    self._policy.max_reinforced_confidence,
                )

            # Floor at min_confidence
            new_confidence = max(new_confidence, self._policy.min_confidence)
            new_confidence = min(new_confidence, 1.0)

            result.append(
                Belief(
                    belief_id=belief.belief_id,
                    content=belief.content,
                    source=belief.source,
                    source_session=belief.source_session,
                    confidence=round(new_confidence, 6),
                    importance=belief.importance,
                    created_at=belief.created_at,
                    verified_at=belief.verified_at,
                    falsified_at=belief.falsified_at,
                    supersedes=belief.supersedes,
                    related_rules=belief.related_rules,
                )
            )

        return result

    def is_stale(self, belief: Belief, now: datetime | None = None) -> bool:
        """Check whether a single belief is stale.

        A belief is stale when it has been longer than
        ``stale_threshold_days`` since its last verification and its
        confidence has decayed to ``min_confidence``.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        days = self._days_since_verified(belief, now)
        return days > self._policy.stale_threshold_days

    def prune_stale_beliefs(self, beliefs: list[Belief], now: datetime | None = None) -> list[Belief]:
        """Remove beliefs that have been stale beyond the threshold.

        Returns:
            List of non-stale beliefs.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        return [b for b in beliefs if not self.is_stale(b, now)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _days_since_verified(self, belief: Belief, now: datetime) -> float:
        """Return the number of days since the belief was last verified."""
        ref_str = belief.verified_at or belief.created_at
        try:
            ref_dt = datetime.fromisoformat(ref_str)
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return 0.0

        delta = now - ref_dt
        return max(0.0, delta.total_seconds() / 86400.0)

    def _calculate_decay_factor(self, days_since_verified: float) -> float:
        """Compute the exponential decay factor."""
        return 0.5 ** (days_since_verified / self._policy.half_life_days)
