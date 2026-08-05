"""Bootstrap composition adapter for Factory-owned chain observations."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.factory.pipeline.public import (
    FactoryChainProjectionV1,
    GetFactoryChainProjectionQueryV1,
    get_factory_chain_projection,
)
from polaris.cells.factory.pipeline.public.contracts import (
    compute_factory_chain_projection_hash,
)
from polaris.cells.runtime.projection.public.bootstrap import (
    bind_factory_chain_owner_observation_port,
)
from polaris.cells.runtime.projection.public.contracts import (
    FactoryChainOwnerObservationV1,
    ProjectOutcomeOwnerObservationV1Error,
)


class FactoryChainOwnerObservationAdapter:
    """Translate exact Factory public DTOs into projection-owned observations."""

    async def observe_factory_chain(
        self,
        *,
        workspace: str,
        run_id: str,
    ) -> FactoryChainOwnerObservationV1:
        canonical_workspace = str(Path(workspace).expanduser().resolve())
        projection = await get_factory_chain_projection(
            GetFactoryChainProjectionQueryV1(
                workspace=canonical_workspace,
                run_id=run_id,
            )
        )
        if type(projection) is not FactoryChainProjectionV1:
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_owner_result_type",
                "Factory owner query must return an exact FactoryChainProjectionV1",
            )
        if projection.workspace != canonical_workspace or projection.run_id != run_id:
            raise ProjectOutcomeOwnerObservationV1Error(
                "factory_chain_owner_identity_mismatch",
                "Factory owner projection workspace/run_id does not match the requested identity",
            )
        expected_hash = compute_factory_chain_projection_hash(
            workspace=projection.workspace,
            run_id=projection.run_id,
            available=projection.available,
            status=projection.status,
            configured_stages=projection.configured_stages,
            completed_stages=projection.completed_stages,
            failed_stages=projection.failed_stages,
            missing_stages=projection.missing_stages,
            chain_completed=projection.chain_completed,
            event_count=projection.event_count,
            event_refs=projection.event_refs,
            completion_event_ref=projection.completion_event_ref,
            source=projection.source,
            schema_version=projection.schema_version,
        )
        if projection.projection_hash != expected_hash or projection.event_count != len(projection.event_refs):
            raise ProjectOutcomeOwnerObservationV1Error(
                "factory_chain_owner_evidence_invalid",
                "Factory owner projection hash or event evidence is invalid",
            )
        if projection.completion_event_ref is not None and projection.completion_event_ref not in projection.event_refs:
            raise ProjectOutcomeOwnerObservationV1Error(
                "factory_chain_owner_evidence_invalid",
                "Factory completion evidence is not present in event_refs",
            )
        return FactoryChainOwnerObservationV1(
            workspace=projection.workspace,
            run_id=projection.run_id,
            available=projection.available,
            status=projection.status,
            chain_completed=projection.chain_completed,
            event_refs=projection.event_refs,
            completion_event_ref=projection.completion_event_ref,
            projection_hash=projection.projection_hash,
        )


FACTORY_CHAIN_OWNER_OBSERVATION_ADAPTER = FactoryChainOwnerObservationAdapter()


def configure_runtime_projection_factory_owner() -> None:
    """Bind the singleton adapter into runtime projection during bootstrap."""
    bind_factory_chain_owner_observation_port(FACTORY_CHAIN_OWNER_OBSERVATION_ADAPTER)


__all__ = [
    "FACTORY_CHAIN_OWNER_OBSERVATION_ADAPTER",
    "FactoryChainOwnerObservationAdapter",
    "configure_runtime_projection_factory_owner",
]
