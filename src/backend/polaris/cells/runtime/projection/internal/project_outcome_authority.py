"""All-owner ProjectOutcome authority binding.

This module owns no upstream facts. It observes bootstrap-injected public
owner ports, checks one exact project/run/contract identity, and wraps the
existing pure reducer result with authority evidence. No static dependency on
Factory, QA, Run Ledger, TaskRuntime, or TaskBoundary implementations exists.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from polaris.cells.runtime.projection.internal.project_outcome_factory_owner import (
    observe_factory_chain_owner,
)
from polaris.cells.runtime.projection.public.contracts import (
    _PROJECT_OUTCOME_AUTHORITY_BINDING_TOKEN,
    _PROJECT_OUTCOME_AUTHORITY_TOKEN,
    ProjectOutcomeAuthorityBindingV1,
    ProjectOutcomeAuthorityQueryV1,
    ProjectOutcomeFactoryOwnerBindingV1,
    ProjectOutcomeFactoryOwnerQueryV1,
    ProjectOutcomeNonFactoryClaimsV1,
    ProjectOutcomeNonFactoryOwnerObservationPortV1,
    ProjectOutcomeNonFactoryOwnerObservationV1,
    ProjectOutcomeOwnerObservationV1Error,
    ProjectOutcomeV1,
    RecommendedDispositionV1,
)

_project_outcome_non_factory_owner_observation_port: ProjectOutcomeNonFactoryOwnerObservationPortV1 | None = None
_project_outcome_non_factory_owner_observation_port_lock = Lock()


def bind_project_outcome_non_factory_owner_observation_port(
    port: ProjectOutcomeNonFactoryOwnerObservationPortV1,
) -> None:
    """Bind the non-Factory owner adapter exactly once during bootstrap."""
    if not isinstance(port, ProjectOutcomeNonFactoryOwnerObservationPortV1):
        raise ProjectOutcomeOwnerObservationV1Error(
            "invalid_project_outcome_non_factory_owner_port",
            "Port must implement ProjectOutcomeNonFactoryOwnerObservationPortV1",
        )
    global _project_outcome_non_factory_owner_observation_port
    with _project_outcome_non_factory_owner_observation_port_lock:
        bound = _project_outcome_non_factory_owner_observation_port
        if bound is None:
            _project_outcome_non_factory_owner_observation_port = port
            return
        if bound is not port:
            raise ProjectOutcomeOwnerObservationV1Error(
                "project_outcome_non_factory_owner_port_conflicting_rebind",
                "Non-Factory ProjectOutcome owner port is already bound to another adapter",
            )


def _bound_project_outcome_non_factory_owner_observation_port() -> ProjectOutcomeNonFactoryOwnerObservationPortV1:
    with _project_outcome_non_factory_owner_observation_port_lock:
        port = _project_outcome_non_factory_owner_observation_port
    if port is None:
        raise ProjectOutcomeOwnerObservationV1Error(
            "project_outcome_non_factory_owner_port_unbound",
            "Non-Factory ProjectOutcome owner port is not bound by process bootstrap",
        )
    return port


async def observe_authoritative_project_outcome(
    query: ProjectOutcomeAuthorityQueryV1,
) -> ProjectOutcomeAuthorityBindingV1:
    """Observe both owner ports and bind one authoritative outcome result."""
    canonical_workspace = str(Path(query.workspace).expanduser().resolve())
    port = _bound_project_outcome_non_factory_owner_observation_port()
    try:
        observation = await port.observe_project_outcome_non_factory(
            workspace=canonical_workspace,
            project_id=query.project_id,
            run_id=query.run_id,
            completion_contract_hash=query.completion_contract_hash,
        )
    except ProjectOutcomeOwnerObservationV1Error:
        raise
    except Exception as exc:
        raise ProjectOutcomeOwnerObservationV1Error(
            "project_outcome_non_factory_owner_query_failed",
            f"Non-Factory ProjectOutcome owner query failed: {exc}",
        ) from exc
    if type(observation) is not ProjectOutcomeNonFactoryOwnerObservationV1:
        raise ProjectOutcomeOwnerObservationV1Error(
            "invalid_project_outcome_non_factory_owner_result_type",
            "Owner port must return an exact ProjectOutcomeNonFactoryOwnerObservationV1",
        )
    observed_identity = (
        observation.workspace,
        observation.project_id,
        observation.run_id,
        observation.completion_contract_hash,
    )
    requested_identity = (
        canonical_workspace,
        query.project_id,
        query.run_id,
        query.completion_contract_hash,
    )
    if observed_identity != requested_identity:
        raise ProjectOutcomeOwnerObservationV1Error(
            "project_outcome_non_factory_owner_identity_mismatch",
            "Owner observation workspace/project/run/contract does not match the query",
        )

    factory_binding = await observe_factory_chain_owner(
        ProjectOutcomeFactoryOwnerQueryV1(
            workspace=canonical_workspace,
            run_id=query.run_id,
            claims=ProjectOutcomeNonFactoryClaimsV1(
                delivery=observation.delivery,
                qa=observation.qa,
                task_boundary=observation.task_boundary,
                task_runtime=observation.task_runtime,
                run_ledger=observation.run_ledger,
                evidence_refs=observation.evidence_refs,
                missing_required_modalities=observation.missing_required_modalities,
                failed_required_modalities=observation.failed_required_modalities,
                reasons=observation.reasons,
                task_count=observation.task_count,
                completed_task_count=observation.completed_task_count,
            ),
        )
    )
    if type(factory_binding) is not ProjectOutcomeFactoryOwnerBindingV1:
        raise ProjectOutcomeOwnerObservationV1Error(
            "invalid_factory_owner_binding_result_type",
            "Factory owner observation must return an exact ProjectOutcomeFactoryOwnerBindingV1",
        )

    missing_evidence_axes = observation.evidence_refs.empty_axes()
    missing_hash_axes = observation.projection_hashes.empty_axes()
    if missing_evidence_axes or missing_hash_axes:
        raise ProjectOutcomeOwnerObservationV1Error(
            "project_outcome_owner_evidence_incomplete",
            "Every non-Factory owner axis requires projection hash and evidence refs; "
            f"missing_evidence={missing_evidence_axes!r}, missing_hash={missing_hash_axes!r}",
        )
    for axis in ("delivery", "qa", "task_boundary", "task_runtime", "run_ledger"):
        projection_hash = getattr(observation.projection_hashes, axis)
        evidence_refs = getattr(observation.evidence_refs, axis)
        if projection_hash not in evidence_refs:
            raise ProjectOutcomeOwnerObservationV1Error(
                "project_outcome_owner_projection_hash_not_bound",
                f"{axis} projection hash must be present in its owner evidence refs",
            )

    candidate = factory_binding.outcome
    authoritative_outcome = ProjectOutcomeV1(
        run_id=candidate.run_id,
        delivery=candidate.delivery,
        chain=candidate.chain,
        qa=candidate.qa,
        task_boundary=candidate.task_boundary,
        task_runtime=candidate.task_runtime,
        run_ledger=candidate.run_ledger,
        missing_required_modalities=candidate.missing_required_modalities,
        failed_required_modalities=candidate.failed_required_modalities,
        completion_candidate=candidate.completion_candidate,
        authority_bound=True,
        completed_verified=candidate.completion_candidate,
        recommended_disposition=(
            RecommendedDispositionV1.COMPLETE if candidate.completion_candidate else candidate.recommended_disposition
        ),
        evidence_refs=candidate.evidence_refs,
        reasons=candidate.reasons,
        blocking_axes=candidate.blocking_axes,
        task_count=candidate.task_count,
        completed_task_count=candidate.completed_task_count,
        _authority_token=_PROJECT_OUTCOME_AUTHORITY_TOKEN,
    )
    return ProjectOutcomeAuthorityBindingV1(
        outcome=authoritative_outcome,
        workspace=canonical_workspace,
        project_id=query.project_id,
        run_id=query.run_id,
        completion_contract_hash=query.completion_contract_hash,
        factory_chain_projection_hash=factory_binding.factory_chain_projection_hash,
        factory_chain_evidence_refs=factory_binding.factory_chain_evidence_refs,
        non_factory_projection_hashes=observation.projection_hashes,
        non_factory_evidence_refs=observation.evidence_refs,
        _authority_token=_PROJECT_OUTCOME_AUTHORITY_BINDING_TOKEN,
    )


__all__ = [
    "bind_project_outcome_non_factory_owner_observation_port",
    "observe_authoritative_project_outcome",
]
