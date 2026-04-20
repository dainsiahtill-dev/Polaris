"""Unit tests for BeliefDecayEngine and DecayPolicy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from polaris.kernelone.cognitive.evolution.belief_decay import (
    BeliefDecayEngine,
    DecayPolicy,
)
from polaris.kernelone.cognitive.evolution.models import Belief

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_belief(
    belief_id: str = "b1",
    confidence: float = 0.8,
    created_at: str | None = None,
    verified_at: str | None = None,
) -> Belief:
    """Create a minimal Belief for testing."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    return Belief(
        belief_id=belief_id,
        content="test belief",
        source="unit_test",
        source_session=None,
        confidence=confidence,
        importance=5,
        created_at=created_at,
        verified_at=verified_at,
        falsified_at=None,
        supersedes=None,
        related_rules=(),
    )


def _days_ago(days: float) -> str:
    """ISO-format timestamp for *days* days in the past."""
    ts = datetime.now(timezone.utc) - timedelta(days=days)
    return ts.isoformat()


# ------------------------------------------------------------------
# DecayPolicy
# ------------------------------------------------------------------


class TestDecayPolicy:
    def test_defaults(self) -> None:
        p = DecayPolicy()
        assert p.half_life_days == 30.0
        assert p.min_confidence == 0.1
        assert p.reinforcement_factor == 1.5
        assert p.max_reinforced_confidence == 0.95
        assert p.stale_threshold_days == 60.0

    def test_custom(self) -> None:
        p = DecayPolicy(half_life_days=7.0, min_confidence=0.05)
        assert p.half_life_days == 7.0
        assert p.min_confidence == 0.05


# ------------------------------------------------------------------
# BeliefDecayEngine
# ------------------------------------------------------------------


class TestBeliefDecayEngine:
    def test_no_decay_for_fresh_belief(self) -> None:
        engine = BeliefDecayEngine()
        now = datetime.now(timezone.utc)
        belief = _make_belief(confidence=0.9, created_at=now.isoformat())
        result = engine.apply_decay([belief], now=now)
        assert len(result) == 1
        assert result[0].confidence == pytest.approx(0.9, abs=1e-6)

    def test_decay_after_half_life(self) -> None:
        policy = DecayPolicy(half_life_days=30.0, reinforcement_factor=1.0)
        engine = BeliefDecayEngine(policy)
        now = datetime.now(timezone.utc)
        created = (now - timedelta(days=30)).isoformat()
        belief = _make_belief(confidence=0.8, created_at=created)
        result = engine.apply_decay([belief], now=now)
        # 0.8 * 0.5^1 = 0.4
        assert result[0].confidence == pytest.approx(0.4, abs=1e-6)

    def test_decay_floored_at_min_confidence(self) -> None:
        policy = DecayPolicy(
            half_life_days=10.0,
            min_confidence=0.15,
            reinforcement_factor=1.0,
        )
        engine = BeliefDecayEngine(policy)
        now = datetime.now(timezone.utc)
        created = (now - timedelta(days=200)).isoformat()
        belief = _make_belief(confidence=0.2, created_at=created)
        result = engine.apply_decay([belief], now=now)
        assert result[0].confidence >= policy.min_confidence

    def test_reinforcement_when_recently_verified(self) -> None:
        policy = DecayPolicy(
            half_life_days=30.0,
            reinforcement_factor=2.0,
            max_reinforced_confidence=0.95,
        )
        engine = BeliefDecayEngine(policy)
        now = datetime.now(timezone.utc)
        verified = (now - timedelta(days=5)).isoformat()
        created = (now - timedelta(days=50)).isoformat()
        belief = _make_belief(
            confidence=0.5,
            created_at=created,
            verified_at=verified,
        )
        result = engine.apply_decay([belief], now=now)
        # 5 days old -> decay factor 0.5^(5/30) ~ 0.891
        # confidence * decay = 0.5 * 0.891 ~ 0.445
        # reinforced: 0.445 * 2.0 = 0.89
        assert result[0].confidence > 0.445
        assert result[0].confidence <= policy.max_reinforced_confidence

    def test_is_stale_true(self) -> None:
        policy = DecayPolicy(stale_threshold_days=60.0)
        engine = BeliefDecayEngine(policy)
        now = datetime.now(timezone.utc)
        created = (now - timedelta(days=100)).isoformat()
        belief = _make_belief(created_at=created)
        assert engine.is_stale(belief, now) is True

    def test_is_stale_false(self) -> None:
        policy = DecayPolicy(stale_threshold_days=60.0)
        engine = BeliefDecayEngine(policy)
        now = datetime.now(timezone.utc)
        created = (now - timedelta(days=10)).isoformat()
        belief = _make_belief(created_at=created)
        assert engine.is_stale(belief, now) is False

    def test_prune_stale_beliefs(self) -> None:
        policy = DecayPolicy(stale_threshold_days=60.0)
        engine = BeliefDecayEngine(policy)
        now = datetime.now(timezone.utc)
        fresh = _make_belief(belief_id="fresh", created_at=(now - timedelta(days=10)).isoformat())
        stale = _make_belief(belief_id="stale", created_at=(now - timedelta(days=100)).isoformat())
        result = engine.prune_stale_beliefs([fresh, stale], now=now)
        assert len(result) == 1
        assert result[0].belief_id == "fresh"

    def test_apply_decay_empty_list(self) -> None:
        engine = BeliefDecayEngine()
        assert engine.apply_decay([]) == []

    def test_policy_property(self) -> None:
        policy = DecayPolicy(half_life_days=7.0)
        engine = BeliefDecayEngine(policy)
        assert engine.policy is policy

    def test_two_half_lives(self) -> None:
        policy = DecayPolicy(half_life_days=30.0, reinforcement_factor=1.0)
        engine = BeliefDecayEngine(policy)
        now = datetime.now(timezone.utc)
        created = (now - timedelta(days=60)).isoformat()
        belief = _make_belief(confidence=0.8, created_at=created)
        result = engine.apply_decay([belief], now=now)
        # 0.8 * 0.5^(60/30) = 0.8 * 0.25 = 0.2
        assert result[0].confidence == pytest.approx(0.2, abs=1e-6)

    def test_multiple_beliefs_batch(self) -> None:
        policy = DecayPolicy(half_life_days=30.0, reinforcement_factor=1.0)
        engine = BeliefDecayEngine(policy)
        now = datetime.now(timezone.utc)
        b1 = _make_belief(
            belief_id="b1",
            confidence=0.8,
            created_at=(now - timedelta(days=30)).isoformat(),
        )
        b2 = _make_belief(
            belief_id="b2",
            confidence=0.8,
            created_at=(now - timedelta(days=60)).isoformat(),
        )
        b3 = _make_belief(
            belief_id="b3",
            confidence=0.8,
            created_at=now.isoformat(),
        )
        result = engine.apply_decay([b1, b2, b3], now=now)
        assert len(result) == 3
        # b3 is fresh -> no decay
        assert result[2].confidence == pytest.approx(0.8, abs=1e-6)
        # b1 decays less than b2
        assert result[0].confidence > result[1].confidence
