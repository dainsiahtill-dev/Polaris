"""GR1C bootstrap-bound Director status owner observation."""

from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from threading import Lock

from polaris.cells.runtime.projection.public.contracts import (
    DirectorStatusObservationPortV1,
    DirectorStatusObservationV1,
    DirectorStatusObservationV1Error,
)

_director_status_observation_port: DirectorStatusObservationPortV1 | None = None
_director_status_observation_port_lock = Lock()


def bind_director_status_observation_port(
    port: DirectorStatusObservationPortV1,
) -> None:
    """Bind the process-wide Director observation port during bootstrap only."""
    if not isinstance(port, DirectorStatusObservationPortV1):
        raise DirectorStatusObservationV1Error(
            "invalid_director_status_port",
            "Director status port must implement DirectorStatusObservationPortV1",
        )
    global _director_status_observation_port
    with _director_status_observation_port_lock:
        bound = _director_status_observation_port
        if bound is None:
            _director_status_observation_port = port
            return
        if bound is not port:
            raise DirectorStatusObservationV1Error(
                "director_status_port_conflicting_rebind",
                "Director status observation port is already bound to another adapter",
            )


def _bound_director_status_observation_port() -> DirectorStatusObservationPortV1:
    with _director_status_observation_port_lock:
        port = _director_status_observation_port
    if port is None:
        raise DirectorStatusObservationV1Error(
            "director_status_port_unbound",
            "Director status observation port is not bound by process bootstrap",
        )
    return port


async def observe_director_status_owner(workspace: str) -> DirectorStatusObservationV1:
    """Read and validate one exact Director status owner observation."""
    canonical_workspace = str(Path(workspace).expanduser().resolve())
    port = _bound_director_status_observation_port()
    try:
        observation = await port.observe_director_status(workspace=canonical_workspace)
    except DirectorStatusObservationV1Error:
        raise
    except Exception as exc:
        raise DirectorStatusObservationV1Error(
            "director_status_owner_query_failed",
            f"Director status owner query failed: {exc}",
        ) from exc
    if type(observation) is not DirectorStatusObservationV1:
        raise DirectorStatusObservationV1Error(
            "invalid_director_status_owner_result_type",
            "Director status owner port must return an exact DirectorStatusObservationV1",
        )
    if observation.workspace != canonical_workspace:
        raise DirectorStatusObservationV1Error(
            "director_status_owner_identity_mismatch",
            "Director status owner observation workspace does not match the request",
        )
    return observation


def observe_director_status_owner_sync(workspace: str) -> DirectorStatusObservationV1:
    """Run the observation query safely from synchronous projection call sites."""

    def _run() -> DirectorStatusObservationV1:
        return asyncio.run(observe_director_status_owner(workspace))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_run).result(timeout=5)


__all__ = [
    "bind_director_status_observation_port",
    "observe_director_status_owner",
    "observe_director_status_owner_sync",
]
