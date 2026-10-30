"""Meta-cognition over structured resident decision traces.

This module analyzes decision records to generate insights about strategy
effectiveness, failure patterns, and prediction gaps. It uses configurable
thresholds to identify strategies that are underperforming or reliable.

Threshold Documentation:
------------------------
MIN_ATTEMPTS_FOR_INSIGHT (2):
    Minimum number of attempts required before generating insights about a strategy.
    This prevents premature conclusions from insufficient data.

SUCCESS_RATE_THRESHOLDS:
    UNDERPERFORMING (0.5): Success rate below which a strategy is flagged as risky.
        - Recommendation: Route through counterfactual replay before promotion.
        - Confidence calculation: 0.45 + (attempts * 0.08), capped at 0.95

    RELIABLE (0.75): Success rate above which a strategy is flagged as a reliable default.
        - Recommendation: Codify into reusable resident skills and planning hints.
        - Confidence calculation: 0.55 + (attempts * 0.06), capped at 0.95

PREDICTION_GAP_THRESHOLD (2):
    Minimum number of prediction gaps (expected vs actual outcome mismatches)
    required to flag a strategy as having prediction issues.
    - Recommendation: Tighten expected outcome schemas and compare actual outcomes.
    - Confidence calculation: 0.50 + (prediction_gaps * 0.07), capped at 0.95

FAILURE_CLUSTER_THRESHOLD (2):
    Minimum number of failures in a specific actor/stage combination
    required to flag as a failure cluster.
    - Recommendation: Propose a governed maintenance goal for the actor/stage.
    - Confidence calculation: 0.42 + (failure_count * 0.08), capped at 0.95

MAX_INSIGHTS_RETURNED (20):
    Maximum number of insights to return in a single refresh operation.
    Prevents overwhelming the caller with too many insights.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from polaris.domain.models.resident import (
    DecisionRecord,
    DecisionVerdict,
    MetaInsight,
    utc_now_iso,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage
    from polaris.kernelone.cognitive.reasoning.meta_cognition import MetaCognitionSnapshot

# =============================================================================
# Configuration Constants
# =============================================================================

# Minimum attempts before generating insights (prevents premature conclusions)
MIN_ATTEMPTS_FOR_INSIGHT = 2

# Success rate thresholds for strategy classification
SUCCESS_RATE_UNDERPERFORMING_THRESHOLD = 0.5  # Below this = risky strategy
SUCCESS_RATE_RELIABLE_THRESHOLD = 0.75  # Above this = reliable default

# Prediction gap threshold (number of mismatches before flagging)
PREDICTION_GAP_THRESHOLD = 2

# Failure cluster threshold (failures in same actor/stage before flagging)
FAILURE_CLUSTER_THRESHOLD = 2

# Maximum insights to return in a single operation
MAX_INSIGHTS_RETURNED = 20

# Confidence calculation parameters (base + multiplier per occurrence, capped at max)
CONFIDENCE_BASE_UNDERPERFORMING = 0.45
CONFIDENCE_MULTIPLIER_UNDERPERFORMING = 0.08

CONFIDENCE_BASE_RELIABLE = 0.55
CONFIDENCE_MULTIPLIER_RELIABLE = 0.06

CONFIDENCE_BASE_PREDICTION_GAP = 0.50
CONFIDENCE_MULTIPLIER_PREDICTION_GAP = 0.07

CONFIDENCE_BASE_FAILURE_CLUSTER = 0.42
CONFIDENCE_MULTIPLIER_FAILURE_CLUSTER = 0.08

CONFIDENCE_MAX = 0.95


def selected_strategy_tags(record: DecisionRecord) -> list[str]:
    direct = [tag for tag in record.strategy_tags if str(tag).strip()]
    if direct:
        return list(dict.fromkeys(direct))
    for option in record.options:
        if option.option_id == record.selected_option_id:
            return [tag for tag in option.strategy_tags if str(tag).strip()]
    return []


def verdict_success(record: DecisionRecord) -> bool:
    return record.verdict == DecisionVerdict.SUCCESS


def outcome_gap(record: DecisionRecord) -> bool:
    expected_status = str(record.expected_outcome.get("status") or "").strip().lower()
    actual_status = str(record.actual_outcome.get("status") or "").strip().lower()
    if expected_status and actual_status and expected_status != actual_status:
        return True
    expected_success = record.expected_outcome.get("success")
    actual_success = record.actual_outcome.get("success")
    if isinstance(expected_success, bool) and isinstance(actual_success, bool):
        return expected_success != actual_success
    return False


class StrategyInsightEngine:
    """Build strategy scorecards and decision-process insights.

    This engine analyzes decision records to identify:
    1. Underperforming strategies (high failure rate)
    2. Reliable strategies (high success rate)
    3. Prediction gaps (expected vs actual outcome mismatches)
    4. Failure clusters (repeated failures in specific actor/stage combinations)

    Thresholds are configurable via module-level constants documented at the
    top of this file.

    Renamed from MetaCognitionEngine to distinguish from the cognitive-layer
    MetaCognitionEngine in ``kernelone.cognitive.reasoning.meta_cognition``.
    """

    def __init__(self, storage: ResidentStorage) -> None:
        self._storage = storage

    def refresh(self, decisions: Iterable[DecisionRecord]) -> dict[str, Any]:
        records = list(decisions)
        scorecard = self._build_strategy_scorecard(records)
        insights = self._build_insights(records, scorecard)
        self._storage.save_insights(insights)
        self._storage.save_meta_state(
            {
                "generated_at": utc_now_iso(),
                "decision_count": len(records),
                "strategy_scorecard": scorecard,
                "insight_count": len(insights),
            }
        )
        return {
            "generated_at": utc_now_iso(),
            "strategy_scorecard": scorecard,
            "insights": [item.to_dict() for item in insights],
        }

    def analyze_decisions(
        self,
        decisions: list[DecisionRecord],
        cognitive_snapshot: MetaCognitionSnapshot | None = None,
    ) -> list[MetaInsight]:
        """Analyze decisions and return strategy insights.

        Optionally calibrates confidence using a cognitive-layer
        ``MetaCognitionSnapshot``.  When provided, the snapshot's
        ``output_confidence`` is blended into each insight's confidence
        score to reflect the cognitive engine's calibrated self-assessment.

        Args:
            decisions: Decision records to analyze.
            cognitive_snapshot: Optional snapshot from the cognitive-layer
                ``MetaCognitionEngine.audit_thought_process()``.

        Returns:
            List of ``MetaInsight`` objects (capped at ``MAX_INSIGHTS_RETURNED``).
        """
        scorecard = self._build_strategy_scorecard(decisions)
        insights = self._build_insights(decisions, scorecard)

        if cognitive_snapshot is not None:
            cognitive_conf = float(cognitive_snapshot.output_confidence)
            blended: list[MetaInsight] = []
            for insight in insights:
                blended_confidence = round(
                    (insight.confidence * 0.7) + (cognitive_conf * 0.3),
                    4,
                )
                blended_confidence = min(CONFIDENCE_MAX, max(0.0, blended_confidence))
                blended.append(
                    MetaInsight(
                        insight_type=insight.insight_type,
                        strategy_tag=insight.strategy_tag,
                        summary=insight.summary,
                        recommendation=insight.recommendation,
                        confidence=blended_confidence,
                        evidence_refs=insight.evidence_refs,
                    )
                )
            insights = blended

        return insights[:MAX_INSIGHTS_RETURNED]

    def _build_strategy_scorecard(
        self,
        decisions: list[DecisionRecord],
    ) -> dict[str, dict[str, Any]]:
        stats: dict[str, dict[str, Any]] = {}
        for record in decisions:
            tags = selected_strategy_tags(record)
            for tag in tags:
                bucket = stats.setdefault(
                    tag,
                    {
                        "attempts": 0,
                        "successes": 0,
                        "failures": 0,
                        "actors": set(),
                        "stages": set(),
                        "evidence_count": 0,
                        "prediction_gaps": 0,
                    },
                )
                bucket["attempts"] += 1
                bucket["actors"].add(record.actor or "unknown")
                bucket["stages"].add(record.stage or "unknown")
                bucket["evidence_count"] += len(record.evidence_refs)
                if verdict_success(record):
                    bucket["successes"] += 1
                elif record.verdict in {DecisionVerdict.FAILURE, DecisionVerdict.BLOCKED}:
                    bucket["failures"] += 1
                if outcome_gap(record):
                    bucket["prediction_gaps"] += 1
        normalized: dict[str, dict[str, Any]] = {}
        for tag, bucket in stats.items():
            attempts = max(1, int(bucket["attempts"]))
            success_rate = float(bucket["successes"]) / float(attempts)
            normalized[tag] = {
                "attempts": attempts,
                "successes": int(bucket["successes"]),
                "failures": int(bucket["failures"]),
                "success_rate": round(success_rate, 4),
                "actors": sorted(str(item) for item in bucket["actors"]),
                "stages": sorted(str(item) for item in bucket["stages"]),
                "evidence_count": int(bucket["evidence_count"]),
                "prediction_gaps": int(bucket["prediction_gaps"]),
            }
        return normalized

    def _build_insights(
        self,
        decisions: list[DecisionRecord],
        scorecard: dict[str, dict[str, Any]],
    ) -> list[MetaInsight]:
        insights: list[MetaInsight] = []
        for tag, bucket in sorted(
            scorecard.items(),
            key=lambda item: (item[1]["success_rate"], item[1]["attempts"]),
        ):
            attempts = int(bucket.get("attempts") or 0)
            success_rate = float(bucket.get("success_rate") or 0.0)
            evidence_refs = self._collect_evidence_refs(decisions, tag)

            # Underperforming strategy detection
            if attempts >= MIN_ATTEMPTS_FOR_INSIGHT and success_rate < SUCCESS_RATE_UNDERPERFORMING_THRESHOLD:
                confidence = min(
                    CONFIDENCE_MAX,
                    CONFIDENCE_BASE_UNDERPERFORMING + (attempts * CONFIDENCE_MULTIPLIER_UNDERPERFORMING),
                )
                insights.append(
                    MetaInsight(
                        insight_type="strategy_risk",
                        strategy_tag=tag,
                        summary=f"Strategy `{tag}` underperforms with success_rate={success_rate:.2f} across {attempts} attempts.",
                        recommendation=f"Route `{tag}` through counterfactual replay before further promotion.",
                        confidence=confidence,
                        evidence_refs=evidence_refs,
                    )
                )
            # Reliable strategy detection
            elif attempts >= MIN_ATTEMPTS_FOR_INSIGHT and success_rate >= SUCCESS_RATE_RELIABLE_THRESHOLD:
                confidence = min(
                    CONFIDENCE_MAX,
                    CONFIDENCE_BASE_RELIABLE + (attempts * CONFIDENCE_MULTIPLIER_RELIABLE),
                )
                insights.append(
                    MetaInsight(
                        insight_type="strategy_strength",
                        strategy_tag=tag,
                        summary=f"Strategy `{tag}` is a reliable default with success_rate={success_rate:.2f} across {attempts} attempts.",
                        recommendation=f"Codify `{tag}` into reusable resident skills and planning hints.",
                        confidence=confidence,
                        evidence_refs=evidence_refs,
                    )
                )

            # Prediction gap detection
            prediction_gaps = int(bucket.get("prediction_gaps") or 0)
            if attempts >= MIN_ATTEMPTS_FOR_INSIGHT and prediction_gaps >= PREDICTION_GAP_THRESHOLD:
                confidence = min(
                    CONFIDENCE_MAX,
                    CONFIDENCE_BASE_PREDICTION_GAP + (prediction_gaps * CONFIDENCE_MULTIPLIER_PREDICTION_GAP),
                )
                insights.append(
                    MetaInsight(
                        insight_type="prediction_gap",
                        strategy_tag=tag,
                        summary=f"Strategy `{tag}` repeatedly diverges from expected outcomes.",
                        recommendation="Tighten expected outcome schemas and compare actual outcomes before promotion.",
                        confidence=confidence,
                        evidence_refs=evidence_refs,
                    )
                )

        # Failure cluster detection
        stage_failures: dict[str, int] = defaultdict(int)
        stage_refs: dict[str, list[str]] = defaultdict(list)
        for record in decisions:
            if record.verdict not in {DecisionVerdict.FAILURE, DecisionVerdict.BLOCKED}:
                continue
            token = f"{record.actor or 'unknown'}::{record.stage or 'unknown'}"
            stage_failures[token] += 1
            stage_refs[token].extend(record.evidence_refs[:2])

        for token, count in stage_failures.items():
            if count < FAILURE_CLUSTER_THRESHOLD:
                continue
            actor, stage = token.split("::", 1)
            confidence = min(
                CONFIDENCE_MAX,
                CONFIDENCE_BASE_FAILURE_CLUSTER + (count * CONFIDENCE_MULTIPLIER_FAILURE_CLUSTER),
            )
            insights.append(
                MetaInsight(
                    insight_type="failure_cluster",
                    strategy_tag=stage,
                    summary=f"{actor} accumulates repeated failures during `{stage}` ({count} recent events).",
                    recommendation=f"Propose a governed maintenance goal for `{actor}` stage `{stage}`.",
                    confidence=confidence,
                    evidence_refs=list(dict.fromkeys(stage_refs[token]))[:6],
                )
            )

        return insights[:MAX_INSIGHTS_RETURNED]

    def _collect_evidence_refs(
        self,
        decisions: Iterable[DecisionRecord],
        strategy_tag: str,
    ) -> list[str]:
        refs: list[str] = []
        for record in decisions:
            if strategy_tag not in selected_strategy_tags(record):
                continue
            refs.extend(record.evidence_refs[:2])
        return list(dict.fromkeys(refs))[:8]
