"""Bootstrap-only binding surface for runtime projection owner ports."""

from __future__ import annotations

from polaris.cells.runtime.projection.internal.director_status_owner import (
    bind_director_status_observation_port as _bind_director_status_observation_port,
)
from polaris.cells.runtime.projection.internal.project_outcome_authority import (
    bind_project_outcome_non_factory_owner_observation_port as _bind_project_outcome_non_factory_owner_observation_port,
)
from polaris.cells.runtime.projection.internal.project_outcome_factory_owner import (
    bind_factory_chain_owner_observation_port as _bind_factory_chain_owner_observation_port,
)
from polaris.cells.runtime.projection.internal.workflow_runtime_owner import (
    WorkflowRuntimeProjectionOwnerPortV1,
    bind_workflow_runtime_projection_owner_port as _bind_workflow_runtime_projection_owner_port,
)
from polaris.cells.runtime.projection.public.contracts import (
    DirectorStatusObservationPortV1,
    FactoryChainOwnerObservationPortV1,
    ProjectOutcomeNonFactoryOwnerObservationPortV1,
)


def bind_factory_chain_owner_observation_port(
    port: FactoryChainOwnerObservationPortV1,
) -> None:
    """Bind the process composition adapter; only bootstrap may call this API."""
    _bind_factory_chain_owner_observation_port(port)


def bind_director_status_observation_port(
    port: DirectorStatusObservationPortV1,
) -> None:
    """Bind the Director status adapter; only bootstrap may call this API."""
    _bind_director_status_observation_port(port)


def bind_project_outcome_non_factory_owner_observation_port(
    port: ProjectOutcomeNonFactoryOwnerObservationPortV1,
) -> None:
    """Bind non-Factory ProjectOutcome owner facts; bootstrap only."""
    _bind_project_outcome_non_factory_owner_observation_port(port)


def bind_workflow_runtime_projection_owner_port(
    port: WorkflowRuntimeProjectionOwnerPortV1,
) -> None:
    """Bind the workflow-runtime reader in the process composition root."""

    _bind_workflow_runtime_projection_owner_port(port)


__all__ = [
    "bind_director_status_observation_port",
    "bind_factory_chain_owner_observation_port",
    "bind_project_outcome_non_factory_owner_observation_port",
    "bind_workflow_runtime_projection_owner_port",
]
