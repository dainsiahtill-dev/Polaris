"""HTTP contract tests for explicit Factory stale-owner recovery."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.cells.factory.pipeline.internal.factory_run_admission import (
    FactoryWorkspaceRunAdmission,
)
from polaris.cells.factory.pipeline.public import FactoryConfig, FactoryRunService
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import factory as factory_router
from polaris.delivery.http.routers._shared import require_auth


class _MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _build_app(workspace: Path) -> FastAPI:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(factory_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=str(workspace), ramdisk_root=""),
    )
    return app


async def _create_service(
    tmp_path: Path,
    *,
    stale: bool,
) -> tuple[FactoryRunService, FactoryWorkspaceRunAdmission, str, int]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_root = tmp_path / "runtime"
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=runtime_root / "factory",
        lease_ttl_seconds=10,
        clock=clock,
    )
    service = FactoryRunService(
        workspace,
        cache_root=runtime_root,
        admission=admission,
    )
    run = await service.create_run(FactoryConfig(name="http-stale-owner"))
    await service.start_run(run.id)
    lease = admission.current()
    assert lease is not None
    if stale:
        clock.advance(11)
    return service, admission, run.id, lease.fencing_token


async def _client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _payload(workspace: Path, run_id: str, fencing_token: int) -> dict[str, object]:
    return {
        "workspace": str(workspace.resolve()),
        "run_id": run_id,
        "expected_fencing_token": fencing_token,
        "reason": "operator confirmed owner process exit",
    }


@pytest.mark.asyncio
async def test_recover_stale_owner_http_action_uses_bound_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admission, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    app = _build_app(service.workspace)
    monkeypatch.setattr(factory_router, "_get_service", lambda _workspace: service)

    async for client in _client(app):
        response = await client.post(
            f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner",
            json=_payload(service.workspace, run_id, fencing_token),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace"] == str(service.workspace.resolve())
    assert body["run_id"] == run_id
    assert body["expected_fencing_token"] == fencing_token
    assert body["lease"]["state"] == "released"
    current = admission.current()
    assert current is not None
    assert current.state.value == "released"


@pytest.mark.asyncio
async def test_recover_stale_owner_http_preserves_wrong_token_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    app = _build_app(service.workspace)
    monkeypatch.setattr(factory_router, "_get_service", lambda _workspace: service)

    async for client in _client(app):
        response = await client.post(
            f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner",
            json=_payload(service.workspace, run_id, fencing_token + 1),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "factory_workspace_run_fenced"


@pytest.mark.asyncio
async def test_recover_stale_owner_http_rejects_non_stale_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, run_id, fencing_token = await _create_service(tmp_path, stale=False)
    app = _build_app(service.workspace)
    monkeypatch.setattr(factory_router, "_get_service", lambda _workspace: service)

    async for client in _client(app):
        response = await client.post(
            f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner",
            json=_payload(service.workspace, run_id, fencing_token),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "factory_workspace_run_owner_not_stale"


@pytest.mark.asyncio
async def test_recover_stale_owner_http_rejects_wrong_workspace_before_service_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admission, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    wrong_workspace = tmp_path / "wrong-workspace"
    wrong_workspace.mkdir()
    app = _build_app(service.workspace)
    calls: list[str] = []

    def service_factory(workspace: str) -> FactoryRunService:
        calls.append(workspace)
        return service

    monkeypatch.setattr(factory_router, "_get_service", service_factory)
    async for client in _client(app):
        response = await client.post(
            f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner",
            json=_payload(wrong_workspace, run_id, fencing_token),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "factory_workspace_binding_mismatch"
    assert calls == []
    current = admission.current()
    assert current is not None
    assert current.state.value == "active"


@pytest.mark.asyncio
async def test_recover_stale_owner_http_rejects_run_binding_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admission, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    app = _build_app(service.workspace)
    calls: list[str] = []

    def service_factory(workspace: str) -> FactoryRunService:
        calls.append(workspace)
        return service

    monkeypatch.setattr(factory_router, "_get_service", service_factory)
    async for client in _client(app):
        response = await client.post(
            f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner",
            json=_payload(service.workspace, "factory_other", fencing_token),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "factory_run_binding_mismatch"
    assert calls == []
    current = admission.current()
    assert current is not None
    assert current.state.value == "active"


@pytest.mark.asyncio
async def test_recover_stale_owner_get_is_not_an_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admission, run_id, _ = await _create_service(tmp_path, stale=True)
    app = _build_app(service.workspace)
    calls: list[str] = []
    monkeypatch.setattr(factory_router, "_get_service", lambda workspace: calls.append(workspace))

    async for client in _client(app):
        response = await client.get(f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner")

    assert response.status_code == 405
    assert calls == []
    current = admission.current()
    assert current is not None
    assert current.state.value == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field",
    ["workspace", "run_id", "expected_fencing_token", "reason"],
)
async def test_recover_stale_owner_http_requires_all_authority_fields(
    tmp_path: Path,
    missing_field: str,
) -> None:
    service, _, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    app = _build_app(service.workspace)
    payload = _payload(service.workspace, run_id, fencing_token)
    payload.pop(missing_field)

    async for client in _client(app):
        response = await client.post(
            f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner",
            json=payload,
        )

    assert response.status_code == 422
