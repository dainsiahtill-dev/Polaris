"""Real ASGI transport, with labelled controlled delays for scheduling only."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest
from fastapi._compat.v2 import ModelField
from fastapi.responses import JSONResponse, PlainTextResponse
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.tests.test_engineering_observation import database_dump, seed_current_candidate
from polaris.delivery.http.tests.test_engineering_runs import _request_lookup_app


@pytest.mark.parametrize("path", ["runs/observation", "runs/current", "requests/observation-key"])
async def test_http_query_observes_inputs_once_without_repeating_identity_resolution(tmp_path, monkeypatch, path):
    service, _scope = await seed_current_candidate(tmp_path)
    app = _request_lookup_app(service.workspace)
    app.state.engineering_run_service = service
    captures = []
    original = EngineeringVerification._manifest

    def capture(self, *, include_outputs):
        captures.append(include_outputs)
        return original(self, include_outputs=include_outputs)

    monkeypatch.setattr(EngineeringVerification, "_manifest", capture)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/v2/engineering/{path}", headers={"Authorization": "Bearer test-engineering-token"}
        )
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "passed"
    assert response.json()["qa_verdict"] == "rejected"
    assert captures == [True]


@pytest.mark.parametrize("phase", ["read", "encoding"])
@pytest.mark.parametrize("path", ["runs/probe", "runs/current", "requests/probe-key"])
async def test_asgi_control_can_respond_during_observation_and_encoding(tmp_path: Path, monkeypatch, phase, path):
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits

    app = _request_lookup_app(tmp_path)
    service = app.state.engineering_run_service
    service.store.create_run(
        RoleRunCommand("probe", "director", "Scheduling fixture only", tmp_path),
        scope=PermissionScope((), (), frozenset(), False),
        limits=RunLimits(None, 10000, 1000),
        engine_version=service.engine_version,
        request_key="probe-key",
    )
    started = threading.Event()
    finished = threading.Event()
    release = threading.Event()
    loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def slow_boundary():
        worker_threads.append(threading.get_ident())
        started.set()
        # Finite even on the old blocking adapter: failure cannot hang the suite.
        release.wait(0.3)
        finished.set()

    original_read = service.snapshot
    original_render = JSONResponse.render
    original_serialize = ModelField.serialize_json

    def read(*args, **kwargs):
        if phase == "read":
            slow_boundary()
        return original_read(*args, **kwargs)

    def render(self, content):
        if phase == "encoding" and isinstance(content, dict) and content.get("session_id") == "probe":
            slow_boundary()
        return original_render(self, content)

    def serialize(self, content, **kwargs):
        if phase == "encoding" and isinstance(content, dict) and content.get("session_id") == "probe":
            slow_boundary()
        return original_serialize(self, content, **kwargs)

    monkeypatch.setattr(service, "snapshot", read)
    monkeypatch.setattr(JSONResponse, "render", render)
    monkeypatch.setattr(ModelField, "serialize_json", serialize)

    @app.get("/control")
    async def control():
        return PlainTextResponse("responsive" if started.is_set() and not finished.is_set() else "starved")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(
            client.get(f"/v2/engineering/{path}", headers={"Authorization": "Bearer test-engineering-token"})
        )
        try:
            async with asyncio.timeout(2):
                while not started.is_set():
                    await asyncio.sleep(0.001)
                response = await client.get("/control")
            assert response.text == "responsive"
            assert all(thread != loop_thread for thread in worker_threads)
        finally:
            release.set()
            result = await pending
    assert result.status_code == 200 and result.json()["session_id"] == "probe"


async def test_cancelled_http_query_finishes_readonly_and_next_query_reobserves(tmp_path, monkeypatch):
    service, _scope = await seed_current_candidate(tmp_path)
    app = _request_lookup_app(service.workspace)
    app.state.engineering_run_service = service
    before = database_dump(service)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    results = []
    original = service.snapshot

    def delayed(*args, **kwargs):
        started.set()
        release.wait(1)
        try:
            result = original(*args, **kwargs)
            results.append(result["verification"])
            return result
        finally:
            finished.set()

    monkeypatch.setattr(service, "snapshot", delayed)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(
            client.get("/v2/engineering/runs/observation", headers={"Authorization": "Bearer test-engineering-token"})
        )
        try:
            async with asyncio.timeout(2):
                while not started.is_set():
                    await asyncio.sleep(0.001)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            (service.workspace / "source.txt").write_text("changed while cancelled reader drains", encoding="utf-8")
        finally:
            release.set()
            async with asyncio.timeout(2):
                while not finished.is_set():
                    await asyncio.sleep(0.001)
        assert results == ["unknown"]
        row = await client.get(
            "/v2/engineering/runs/observation", headers={"Authorization": "Bearer test-engineering-token"}
        )
    assert row.status_code == 200 and row.json()["verification"] == "unknown"
    assert row.json()["project_outcome"] == "unknown"
    assert database_dump(service) == before
    assert not service._tasks and not service._cancellations
