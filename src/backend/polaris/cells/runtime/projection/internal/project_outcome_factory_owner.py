"""GR1C bootstrap-bound Factory chain owner observation."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from polaris.cells.runtime.projection.internal.project_outcome import reduce_project_outcome
from polaris.cells.runtime.projection.public.contracts import (
    ChainAxisV1,
    FactoryChainOwnerObservationPortV1,
    FactoryChainOwnerObservationV1,
    ProjectOutcomeEvidenceRefsV1,
    ProjectOutcomeFactoryOwnerBindingV1,
    ProjectOutcomeFactoryOwnerQueryV1,
    ProjectOutcomeOwnerObservationV1Error,
    ProjectOutcomeQueryV1,
)

_ACTIVE_FACTORY_STATUSES = frozenset({"pending", "running", "paused", "recovering"})
_factory_chain_owner_observation_port: FactoryChainOwnerObservationPortV1 | None = None
_factory_chain_owner_observation_port_lock = Lock()


def bind_factory_chain_owner_observation_port(
    port: FactoryChainOwnerObservationPortV1,
) -> None:
    """Bind process-wide owner observation port during bootstrap only."""
    if not isinstance(port, FactoryChainOwnerObservationPortV1):
        raise ProjectOutcomeOwnerObservationV1Error(
            "invalid_factory_chain_owner_port",
            "Factory chain owner port must implement FactoryChainOwnerObservationPortV1",
        )
    global _factory_chain_owner_observation_port
    with _factory_chain_owner_observation_port_lock:
        bound = _factory_chain_owner_observation_port
        if bound is None:
            _factory_chain_owner_observation_port = port
            return
        if bound is not port:
            raise ProjectOutcomeOwnerObservationV1Error(
                "factory_chain_owner_port_conflicting_rebind",
                "Factory chain owner observation port is already bound to another adapter",
            )


def _bound_factory_chain_owner_observation_port() -> FactoryChainOwnerObservationPortV1:
    with _factory_chain_owner_observation_port_lock:
        port = _factory_chain_owner_observation_port
    if port is None:
        raise ProjectOutcomeOwnerObservationV1Error(
            "factory_chain_owner_port_unbound",
            "Factory chain owner observation port is not bound by process bootstrap",
        )
    return port


def _chain_axis(observation: FactoryChainOwnerObservationV1) -> ChainAxisV1:
    if not observation.available:
        return ChainAxisV1.NOT_STARTED
    if observation.chain_completed:
        return ChainAxisV1.COMPLETED
    if observation.status in _ACTIVE_FACTORY_STATUSES:
        return ChainAxisV1.ACTIVE
    return ChainAxisV1.INCOMPLETE


async def observe_factory_chain_owner(
    query: ProjectOutcomeFactoryOwnerQueryV1,
) -> ProjectOutcomeFactoryOwnerBindingV1:
    """Combine bootstrap-observed Factory facts with non-Factory claims."""
    canonical_workspace = str(Path(query.workspace).expanduser().resolve())
    port = _bound_factory_chain_owner_observation_port()
    try:
        observation = await port.observe_factory_chain(
            workspace=canonical_workspace,
            run_id=query.run_id,
        )
    except ProjectOutcomeOwnerObservationV1Error:
        raise
    except Exception as exc:
        raise ProjectOutcomeOwnerObservationV1Error(
            "factory_chain_owner_query_failed",
            f"Factory chain owner query failed: {exc}",
        ) from exc
    if type(observation) is not FactoryChainOwnerObservationV1:
        raise ProjectOutcomeOwnerObservationV1Error(
            "invalid_factory_chain_owner_result_type",
            "Factory owner port must return an exact FactoryChainOwnerObservationV1",
        )
    if observation.workspace != canonical_workspace or observation.run_id != query.run_id:
        raise ProjectOutcomeOwnerObservationV1Error(
            "factory_chain_owner_identity_mismatch",
            "Factory owner observation workspace/run_id does not match the requested identity",
        )

    claims = query.claims
    chain_evidence = (*observation.event_refs, observation.projection_hash)
    outcome = reduce_project_outcome(
        ProjectOutcomeQueryV1(
            run_id=query.run_id,
            delivery=claims.delivery,
            chain=_chain_axis(observation),
            qa=claims.qa,
            task_boundary=claims.task_boundary,
            task_runtime=claims.task_runtime,
            run_ledger=claims.run_ledger,
            evidence_refs=ProjectOutcomeEvidenceRefsV1(
                delivery=claims.evidence_refs.delivery,
                chain=chain_evidence,
                qa=claims.evidence_refs.qa,
                task_boundary=claims.evidence_refs.task_boundary,
                task_runtime=claims.evidence_refs.task_runtime,
                run_ledger=claims.evidence_refs.run_ledger,
            ),
            missing_required_modalities=claims.missing_required_modalities,
            failed_required_modalities=claims.failed_required_modalities,
            reasons=claims.reasons,
            task_count=claims.task_count,
            completed_task_count=claims.completed_task_count,
        )
    )
    return ProjectOutcomeFactoryOwnerBindingV1(
        outcome=outcome,
        factory_chain_owner_observed=True,
        factory_chain_projection_hash=observation.projection_hash,
        factory_chain_evidence_refs=chain_evidence,
    )
