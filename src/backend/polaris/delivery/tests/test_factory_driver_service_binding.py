"""Factory Router must reuse the lifespan-owned service instance."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.bootstrap.factory_run_driver_runtime import (
    FactoryRunDriverRuntimeV1,
    bind_factory_run_driver_runtime,
    clear_factory_run_driver_runtime,
)
from polaris.cells.factory.pipeline.public import FactoryRunService
from polaris.delivery.http.routers import factory as factory_router


async def _execute(_service: object, _run_id: str, _payload: object, _state: object) -> None:
    return None


def test_factory_service_sees_run_when_env_is_already_project_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Live L1-10: serve --runtime-root=KFS + resolve nest => HTTP RUN_NOT_FOUND."""
    from polaris.kernelone.storage.layout import clear_storage_roots_cache, resolve_storage_roots

    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    cache_base = tmp_path / "runtime-cache"
    cache_base.mkdir()
    workspace = tmp_path / "f21e79dac015d4f121370610"
    workspace.mkdir()
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(cache_base))
    clear_storage_roots_cache()
    project_runtime = Path(resolve_storage_roots(str(workspace)).runtime_root)
    run_id = "factory_6647fd444bb7"
    run_dir = project_runtime / "factory" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(project_runtime))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(project_runtime))
    clear_storage_roots_cache()

    service = FactoryRunService(workspace)
    assert Path(service.cache_root).resolve() == project_runtime.resolve()
    assert service.store.list_runs() == [run_id]


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
