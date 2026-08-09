"""Factory Router must reuse the lifespan-owned service instance."""

from __future__ import annotations

from pathlib import Path

from polaris.bootstrap.factory_run_driver_runtime import (
    FactoryRunDriverRuntimeV1,
    bind_factory_run_driver_runtime,
    clear_factory_run_driver_runtime,
)
from polaris.cells.factory.pipeline.public import FactoryRunService
from polaris.delivery.http.routers import factory as factory_router


async def _execute(_service: object, _run_id: str, _payload: object, _state: object) -> None:
    return None


def test_factory_router_reuses_lifespan_driver_service_for_bound_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(workspace)
    runtime = FactoryRunDriverRuntimeV1(
        workspace=str(workspace),
        service=service,
        state=object(),
        execute=_execute,
        build_recovery_payload=lambda _run, _workspace: object(),
    )
    bind_factory_run_driver_runtime(runtime)
    try:
        assert factory_router._get_service(str(workspace)) is service
        assert factory_router._get_service(str(tmp_path / "other")) is not service
    finally:
        clear_factory_run_driver_runtime(runtime)
