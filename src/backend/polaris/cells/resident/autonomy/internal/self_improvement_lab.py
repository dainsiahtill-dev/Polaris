"""Governed self-improvement proposals derived from counterfactual evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.domain.models.resident import ExperimentRecord, ImprovementProposal, ImprovementStatus, utc_now_iso

if TYPE_CHECKING:
    from collections.abc import Iterable

    from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage


def _target_surface(experiment: ExperimentRecord) -> str:
    token = str(experiment.counterfactual_strategy or "").strip().lower()
    if "retry" in token or "backoff" in token:
        return "retry_policy"
    if "context" in token or "retrieval" in token:
        return "context_policy"
    if "decomposition" in token or "split" in token:
        return "task_decomposition"
    if "prompt" in token:
        return "prompt_strategy"
    return "execution_strategy"


class SelfImprovementLab:
    """Promote strong experiments into governed improvement proposals."""

    def __init__(self, storage: ResidentStorage) -> None:
        self._storage = storage

    def propose(self, experiments: Iterable[ExperimentRecord]) -> list[ImprovementProposal]:
        existing = {proposal.title: proposal for proposal in self._storage.load_improvements() if proposal.title}
        proposals: list[ImprovementProposal] = []
        for experiment in experiments:
            delta = float(experiment.metrics_after.get("predicted_delta") or 0.0)
            if experiment.confidence < 0.55 or delta <= 0.0:
                continue
            target_surface = _target_surface(experiment)
            title = f"Promote {experiment.counterfactual_strategy} for {target_surface}"
            proposal = existing.get(title)
            description = (
                f"Counterfactual replay suggests `{experiment.counterfactual_strategy}` can outperform "
                f"`{experiment.baseline_strategy}` on decision `{experiment.source_decision_id}`."
            )
            if proposal is None:
                proposal = ImprovementProposal(
                    category="resident_self_improvement",
                    title=title,
                    description=description,
                    target_surface=target_surface,
                    evidence_refs=list(dict.fromkeys(experiment.evidence_refs))[:8],
                    experiment_ids=[experiment.experiment_id],
                    confidence=min(0.98, experiment.confidence),
                    rollback_plan=experiment.rollback_plan,
                    status=ImprovementStatus.PROPOSED,
                    created_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                )
            else:
                proposal.description = description
                proposal.evidence_refs = list(dict.fromkeys(proposal.evidence_refs + experiment.evidence_refs))[:10]
                proposal.experiment_ids = list(dict.fromkeys([*proposal.experiment_ids, experiment.experiment_id]))[:12]
                proposal.confidence = max(proposal.confidence, experiment.confidence)
                proposal.rollback_plan = experiment.rollback_plan
                proposal.updated_at = utc_now_iso()
            proposals.append(proposal)
        touched = {item.title for item in proposals if item.title}
        for title, proposal in existing.items():
            if title not in touched:
                proposals.append(proposal)

        proposals = sorted(
            proposals,
            key=lambda item: (item.confidence, len(item.experiment_ids)),
            reverse=True,
        )
        self._storage.save_improvements(proposals)
        return proposals
