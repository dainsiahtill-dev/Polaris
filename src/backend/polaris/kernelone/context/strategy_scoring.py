"""Canonical score weighting model — versioned alongside the strategy framework.

The weighting model determines how sub-scores combine into overall_score.
Weights are owned by the evaluation strategy; this module provides the
canonical baseline.

Version history:
- 1.0.0 (2026-03-25): initial canonical model
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .strategy_contracts import Scorecard

# ------------------------------------------------------------------
# Canonical Weighting
# ------------------------------------------------------------------
# Quality is paramount: a wrong answer is worse than a slow one.
# Efficiency and context use share second place.
# Latency and cost are lower priority for baseline measurement.

CANONICAL_WEIGHTING: dict[str, float] = {
    "quality_score": 0.35,  # Task quality: most important
    "efficiency_score": 0.20,  # Tool call efficiency: second
    "context_score": 0.20,  # Context utilization: second
    "latency_score": 0.15,  # Streaming latency
    "cost_score": 0.10,  # Token cost: lowest priority for baseline
}

# Alternative weightings for specialized evaluations
LOW_LATENCY_WEIGHTING: dict[str, float] = {
    "quality_score": 0.30,
    "efficiency_score": 0.20,
    "context_score": 0.15,
    "latency_score": 0.25,  # Latency is more important
    "cost_score": 0.10,
}

COST_GUARDED_WEIGHTING: dict[str, float] = {
    "quality_score": 0.30,
    "efficiency_score": 0.20,
    "context_score": 0.15,
    "latency_score": 0.10,
    "cost_score": 0.25,  # Cost is more important
}


# ------------------------------------------------------------------
# Score Computation
# ------------------------------------------------------------------


def compute_overall(
    scores: Scorecard,
    weighting: dict[str, float] | None = None,
) -> float:
    """Compute weighted overall score from a scorecard.

    The weighting dict must contain keys matching Scorecard fields
    (without the "_score" suffix for the sub-score fields).
    Defaults to CANONICAL_WEIGHTING.

    All sub-scores are assumed to be in 0.0–1.0 range.
    Returns a float rounded to 4 decimal places.

    Example::

        overall = compute_overall(scorecard)
        assert 0.0 <= overall <= 1.0
    """
    if weighting is None:
        weighting = CANONICAL_WEIGHTING

    return round(
        scores.quality_score * weighting["quality_score"]
        + scores.efficiency_score * weighting["efficiency_score"]
        + scores.context_score * weighting["context_score"]
        + scores.latency_score * weighting["latency_score"]
        + scores.cost_score * weighting["cost_score"],
        4,
    )


def score_diff(
    a: Scorecard,
    b: Scorecard,
    weighting: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compute per-dimension and overall deltas between two scorecards.

    Returns a dict with keys: quality_delta, efficiency_delta, context_delta,
    latency_delta, cost_delta, overall_delta.
    Positive delta means b is better than a.
    """
    if weighting is None:
        weighting = CANONICAL_WEIGHTING

    overall_a = compute_overall(a, weighting)
    overall_b = compute_overall(b, weighting)

    return {
        "quality_delta": round(b.quality_score - a.quality_score, 4),
        "efficiency_delta": round(b.efficiency_score - a.efficiency_score, 4),
        "context_delta": round(b.context_score - a.context_score, 4),
        "latency_delta": round(b.latency_score - a.latency_score, 4),
        "cost_delta": round(b.cost_score - a.cost_score, 4),
        "overall_delta": round(overall_b - overall_a, 4),
    }


__all__ = [
    "CANONICAL_WEIGHTING",
    "COST_GUARDED_WEIGHTING",
    "LOW_LATENCY_WEIGHTING",
    "compute_overall",
    "score_diff",
]
