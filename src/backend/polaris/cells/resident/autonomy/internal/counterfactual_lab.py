"""Offline counterfactual replay for resident decision traces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.cells.resident.autonomy.internal.meta_cognition import selected_strategy_tags
from polaris.domain.models.resident import (
    DecisionRecord,
    DecisionVerdict,
    ExperimentRecord,
    ExperimentStatus,
    utc_now_iso,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage


def _success_rate(records: Iterable[DecisionRecord], tag: str) -> tuple[int, float]:
    attempts = 0
    successes = 0
    for record in records:
        if tag not in selected_strategy_tags(record):
            continue
        attempts += 1
        if record.verdict == DecisionVerdict.SUCCESS:
            successes += 1
    rate = float(successes) / float(attempts) if attempts > 0 else 0.0
    return attempts, round(rate, 4)


class CounterfactualLab:
    """Generate simulated experiments from failed decisions."""

    def __init__(self, storage: ResidentStorage) -> None:
        self._storage = storage

    def replay(self, decisions: Iterable[DecisionRecord]) -> list[ExperimentRecord]:
        decision_list = list(decisions)
        existing = {
            (item.source_decision_id, item.counterfactual_strategy): item for item in self._storage.load_experiments()
        }
        experiments: list[ExperimentRecord] = []
        for record in decision_list:
            if record.verdict not in {DecisionVerdict.FAILURE, DecisionVerdict.BLOCKED, DecisionVerdict.PARTIAL}:
                continue
            baseline = self._baseline_strategy(record)
            if not baseline:
                continue
            candidate = self._select_candidate(record, decision_list, baseline)
            if not candidate:
                continue
            key = (record.decision_id, candidate)
            experiment = existing.get(key)
            attempts_before, success_before = _success_rate(decision_list, baseline)
            attempts_after, success_after = _success_rate(decision_list, candidate)
            delta = round(success_after - success_before, 4)
            confidence = min(
                0.95,
                max(
                    0.25,
                    0.35 + (attempts_after * 0.08) + (0.20 if delta > 0 else 0.0),
                ),
            )
            metrics_before = {
                "strategy": baseline,
                "attempts": attempts_before,
                "success_rate": success_before,
                "verdict": record.verdict.value,
            }
            metrics_after = {
                "strategy": candidate,
                "attempts": attempts_after,
                "success_rate": success_after,
                "predicted_delta": delta,
            }
            recommendation = (
                f"Shadow-test `{candidate}` for actor `{record.actor}` stage `{record.stage}`."
                if delta > 0
                else f"Keep `{candidate}` as an alternative only after more evidence."
            )
            if experiment is None:
                experiment = ExperimentRecord(
                    source_decision_id=record.decision_id,
                    baseline_strategy=baseline,
                    counterfactual_strategy=candidate,
                    metrics_before=metrics_before,
                    metrics_after=metrics_after,
                    confidence=confidence,
                    recommendation=recommendation,
                    rollback_plan="Revert to the last approved resident strategy baseline if shadow metrics regress.",
                    status=ExperimentStatus.SIMULATED,
                    evidence_refs=list(dict.fromkeys(record.evidence_refs))[:8],
                    created_at=utc_now_iso(),
                )
            else:
                experiment.metrics_before = metrics_before
                experiment.metrics_after = metrics_after
                experiment.confidence = confidence
                experiment.recommendation = recommendation
                experiment.rollback_plan = (
                    "Revert to the last approved resident strategy baseline if shadow metrics regress."
                )
                experiment.evidence_refs = list(dict.fromkeys(experiment.evidence_refs + record.evidence_refs))[:10]
            experiments.append(experiment)
        touched = {(item.source_decision_id, item.counterfactual_strategy) for item in experiments}
        for key, experiment in existing.items():
            if key not in touched:
                experiments.append(experiment)

        experiments = sorted(
            experiments,
            key=lambda item: (
                float(item.metrics_after.get("predicted_delta") or 0.0),
                item.confidence,
            ),
            reverse=True,
        )
        self._storage.save_experiments(experiments)
        return experiments

    def _baseline_strategy(self, record: DecisionRecord) -> str:
        tags = selected_strategy_tags(record)
        return str(tags[0] if tags else "").strip()

    def _select_candidate(
        self,
        failed_record: DecisionRecord,
        decisions: list[DecisionRecord],
        baseline: str,
    ) -> str:
        candidate_scores: dict[str, float] = {}
        for option in failed_record.options:
            for tag in option.strategy_tags:
                token = str(tag or "").strip()
                if token and token != baseline:
                    candidate_scores[token] = max(candidate_scores.get(token, 0.0), 0.55)
        for record in decisions:
            if record.actor != failed_record.actor or record.stage != failed_record.stage:
                continue
            if record.verdict != DecisionVerdict.SUCCESS:
                continue
            for tag in selected_strategy_tags(record):
                if tag == baseline:
                    continue
                attempts, success_rate = _success_rate(decisions, tag)
                candidate_scores[tag] = max(
                    candidate_scores.get(tag, 0.0),
                    success_rate + min(attempts, 5) * 0.03,
                )
        ranked = sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)
        return str(ranked[0][0] if ranked else "").strip()
