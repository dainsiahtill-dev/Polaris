"""Pure ProjectOutcomeV1 reducer for runtime.projection.

No filesystem, database, network, subprocess, environment, clock, or LLM access.
Callers supply typed facts already derived from sole owners; this module only
reduces them deterministically. It is not the owner-fact gathering adapter,
does not establish authoritative platform outcome alone, and has no
persistence or scheduling authority.
"""

from __future__ import annotations

from polaris.cells.runtime.projection.public.contracts import ProjectOutcomeQueryV1, ProjectOutcomeV1


def reduce_project_outcome(query: ProjectOutcomeQueryV1) -> ProjectOutcomeV1:
    """Reduce typed project facts into ProjectOutcomeV1.

    GR0 can only compute an unbound ``completion_candidate``. Caller-supplied
    refs are not owner facts, so ``completed_verified`` and ``authority_bound``
    remain false even when every claimed axis is green. Delivery is preserved
    independently of chain/control-plane failure. Missing and failed modalities
    remain distinct.

    This reducer is not a fact authority and cannot alone establish platform
    outcome without a future owner-fact gathering adapter.
    """
    blocking = query.candidate_blocking_axes()
    completion_candidate = not blocking
    disposition = query.candidate_disposition()
    return ProjectOutcomeV1(
        run_id=query.run_id,
        delivery=query.delivery,
        chain=query.chain,
        qa=query.qa,
        task_boundary=query.task_boundary,
        task_runtime=query.task_runtime,
        run_ledger=query.run_ledger,
        missing_required_modalities=query.missing_required_modalities,
        failed_required_modalities=query.failed_required_modalities,
        completion_candidate=completion_candidate,
        authority_bound=False,
        completed_verified=False,
        recommended_disposition=disposition,
        evidence_refs=query.evidence_refs,
        reasons=query.reasons,
        blocking_axes=blocking,
        task_count=query.task_count,
        completed_task_count=query.completed_task_count,
    )


__all__ = ["reduce_project_outcome"]
