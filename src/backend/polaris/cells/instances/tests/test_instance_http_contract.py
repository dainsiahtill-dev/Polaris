"""Native Supervisor HTTP contract must preserve fresh-instance enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from polaris.cells.instances.internal.service import InstanceRecord, InstanceRegistry, InstanceSupervisor
from polaris.delivery.http.dependencies import require_auth
from polaris.delivery.http.v2 import instances


@pytest.mark.asyncio
async def test_http_fresh_collision_does_not_reuse_stop_or_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = InstanceRegistry(tmp_path / "registry", publish_events=False)
    original = registry.save(
        InstanceRecord(
            instance_id="owned-collision",
            name="original",
            kind="bench_project",
            polaris_root=str(tmp_path),
            workspace=str(tmp_path / "workspace"),
            runtime_root=str(tmp_path / "workspace" / ".polaris" / "runtime"),
            backend_port=60101,
            frontend_port=60102,
            backend_url="http://127.0.0.1:60101",
            frontend_url="http://127.0.0.1:60102",
            token="old-project-token",
            status="running",
        )
    )
    before = registry.registry_path.read_bytes()
    supervisor = InstanceSupervisor(registry)
    monkeypatch.setattr(supervisor, "_build_record", lambda _request: original)
    monkeypatch.setattr(supervisor, "_start_backend", lambda *_a: pytest.fail("fresh collision spawned"))
    monkeypatch.setattr(
        supervisor,
        "_terminate_record_processes_for_replacement",
        lambda *_a: pytest.fail("fresh collision stopped old instance"),
    )
    monkeypatch.setattr(
        supervisor, "_with_health", lambda *_a, **_k: pytest.fail("fresh collision reused old instance")
    )
    monkeypatch.setattr(instances, "get_instance_supervisor", lambda: supervisor)
    app = FastAPI()
    app.include_router(instances.router, prefix="/v2")
    app.dependency_overrides[require_auth] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.post(
            "/v2/instances/start",
            json={
                "instance_id": "owned-collision",
                "workspace": original.workspace,
                "require_fresh_instance": True,
            },
        )
    assert response.status_code == 503
    assert "fresh instance identity collision" in response.json()["detail"]
    assert registry.registry_path.read_bytes() == before


@pytest.mark.asyncio
async def test_http_start_preserves_payload_and_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    class Supervisor:
        def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
            captured.append(request)
            return {"instance_id": request["instance_id"], "metadata": request["metadata"], "token": "project-token"}

    monkeypatch.setattr(instances, "get_instance_supervisor", Supervisor)
    app = FastAPI()
    app.include_router(instances.router, prefix="/v2")

    def deny() -> None:
        raise HTTPException(status_code=401, detail="fixture unauthorized")

    app.dependency_overrides[require_auth] = deny
    payload = {
        "instance_id": "fresh-owned",
        "workspace": "/fixture",
        "require_fresh_instance": True,
        "backend_reload": False,
        "metadata": {"instance_launch_receipt": {"run_id": "run-fixture"}},
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as client:
        denied = await client.post("/v2/instances/start", json=payload)
        assert denied.status_code == 401
        assert captured == []
        app.dependency_overrides[require_auth] = lambda: None
        response = await client.post("/v2/instances/start", json=payload)
    assert response.status_code == 200
    assert captured[0]["require_fresh_instance"] is True
    assert captured[0]["backend_reload"] is False
    assert captured[0]["metadata"] == payload["metadata"]
    assert response.json()["instance"]["token"] == "project-token"
