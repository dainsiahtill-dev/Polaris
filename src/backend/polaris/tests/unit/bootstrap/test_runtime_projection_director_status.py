"""GR1C-FIX bootstrap adapter tests for Director status observation."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.runtime.projection.internal import director_status_owner
from polaris.cells.runtime.projection.public import (
    DirectorStatusObservationV1,
    DirectorStatusObservationV1Error,
)


@pytest.fixture(autouse=True)
def _reset_director_status_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        director_status_owner,
        "_director_status_observation_port",
        None,
        raising=False,
    )


class _DirectorService:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    async def get_status(self) -> object:
        return self.payload


class _Container:
    def __init__(self, service: _DirectorService) -> None:
        self.service = service
        self.requested: list[type[object]] = []

    async def resolve_async(self, service_type: type[object]) -> _DirectorService:
        self.requested.append(service_type)
        return self.service


@pytest.mark.asyncio
async def test_adapter_translates_exact_director_status_to_projection_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_director_status as adapter_module

    payload = {
        "state": "RUNNING",
        "started_at": 123.0,
        "workspace": str(tmp_path.resolve()),
        "tasks": {"total": 1},
    }
    container = _Container(_DirectorService(payload))

    async def get_container() -> _Container:
        return container

    monkeypatch.setattr(adapter_module, "get_container", get_container)

    observation = await adapter_module.DirectorStatusObservationAdapter().observe_director_status(
        workspace=str(tmp_path),
    )

    assert type(observation) is DirectorStatusObservationV1
    assert observation.workspace == str(tmp_path.resolve())
    assert observation.available is True
    assert observation.status == payload
    assert container.requested == [adapter_module.DirectorService]


@pytest.mark.asyncio
async def test_adapter_rejects_non_exact_status_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_director_status as adapter_module

    container = _Container(_DirectorService(object()))

    async def get_container() -> _Container:
        return container

    monkeypatch.setattr(adapter_module, "get_container", get_container)
    with pytest.raises(DirectorStatusObservationV1Error) as exc_info:
        await adapter_module.DirectorStatusObservationAdapter().observe_director_status(
            workspace=str(tmp_path),
        )

    assert exc_info.value.error_code == "invalid_director_status_owner_payload"


@pytest.mark.asyncio
async def test_adapter_rejects_director_workspace_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_director_status as adapter_module

    container = _Container(_DirectorService({"state": "IDLE", "workspace": str((tmp_path / "other").resolve())}))

    async def get_container() -> _Container:
        return container

    monkeypatch.setattr(adapter_module, "get_container", get_container)
    with pytest.raises(DirectorStatusObservationV1Error) as exc_info:
        await adapter_module.DirectorStatusObservationAdapter().observe_director_status(
            workspace=str(tmp_path),
        )

    assert exc_info.value.error_code == "director_status_owner_identity_mismatch"


def test_bootstrap_configure_binds_singleton_idempotently(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.bootstrap import runtime_projection_director_status as adapter_module

    bound: list[object] = []
    monkeypatch.setattr(
        adapter_module,
        "bind_director_status_observation_port",
        bound.append,
    )

    adapter_module.configure_runtime_projection_director_status()
    adapter_module.configure_runtime_projection_director_status()

    assert bound == [adapter_module.DIRECTOR_STATUS_OBSERVATION_ADAPTER] * 2


def test_http_app_factory_invokes_both_projection_owner_bindings() -> None:
    app_factory_path = Path(__file__).resolve().parents[3] / "delivery" / "http" / "app_factory.py"
    source = app_factory_path.read_text(encoding="utf-8")

    assert "configure_runtime_projection_factory_owner" in source
    assert "configure_runtime_projection_director_status" in source


def test_projection_source_has_no_director_execution_import() -> None:
    backend_root = Path(__file__).resolve().parents[4]
    projection_root = backend_root / "polaris" / "cells" / "runtime" / "projection"
    offenders: list[Path] = []
    for source_root in (projection_root / "internal", projection_root / "public"):
        for path in source_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "polaris.cells.director.execution" in source:
                offenders.append(path.relative_to(backend_root))

    assert offenders == []
