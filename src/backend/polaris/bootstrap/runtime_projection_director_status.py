"""Bootstrap composition adapter for Director-owned status observations."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.execution.service import DirectorService
from polaris.cells.runtime.projection.public.bootstrap import (
    bind_director_status_observation_port,
)
from polaris.cells.runtime.projection.public.contracts import (
    DirectorStatusObservationV1,
    DirectorStatusObservationV1Error,
)
from polaris.infrastructure.di.container import get_container


class DirectorStatusObservationAdapter:
    """Translate the Director service snapshot into a projection-owned DTO."""

    async def observe_director_status(
        self,
        *,
        workspace: str,
    ) -> DirectorStatusObservationV1:
        canonical_workspace = str(Path(workspace).expanduser().resolve())
        container = await get_container()
        service = await container.resolve_async(DirectorService)
        payload = await service.get_status()
        if type(payload) is not dict:
            raise DirectorStatusObservationV1Error(
                "invalid_director_status_owner_payload",
                "DirectorService.get_status() must return an exact dict",
            )
        payload_workspace = payload.get("workspace")
        if (
            type(payload_workspace) is not str
            or str(Path(payload_workspace).expanduser().resolve()) != canonical_workspace
        ):
            raise DirectorStatusObservationV1Error(
                "director_status_owner_identity_mismatch",
                "Director status workspace does not match the requested identity",
            )
        return DirectorStatusObservationV1(
            workspace=canonical_workspace,
            available=True,
            status=payload,
        )


DIRECTOR_STATUS_OBSERVATION_ADAPTER = DirectorStatusObservationAdapter()


def configure_runtime_projection_director_status() -> None:
    """Bind the singleton adapter into runtime projection during bootstrap."""
    bind_director_status_observation_port(DIRECTOR_STATUS_OBSERVATION_ADAPTER)


__all__ = [
    "DIRECTOR_STATUS_OBSERVATION_ADAPTER",
    "DirectorStatusObservationAdapter",
    "configure_runtime_projection_director_status",
]
