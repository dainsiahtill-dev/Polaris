from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    settings = MagicMock(spec=Settings)
    settings.workspace = str(tmp_path / "workspace")
    settings.workspace_path = settings.workspace
    settings.ramdisk_root = ""
    settings.nats = SimpleNamespace(enabled=False, required=False, url="")
    settings.server = SimpleNamespace(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = SimpleNamespace(enable_debug_tracing=False)
    Path(settings.workspace).mkdir(parents=True, exist_ok=True)
    return settings


@pytest.fixture
async def client(mock_settings: Settings) -> AsyncIterator[AsyncClient]:
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()
    app.state.app_state = AppState(settings=mock_settings)

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch("polaris.bootstrap.assembly.assemble_core_services"),
        patch("polaris.infrastructure.di.container.get_container", new_callable=AsyncMock) as mock_container,
        patch("polaris.delivery.http.app_factory.sync_process_settings_environment"),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_fact_stream_probe_is_unavailable_outside_e2e(client: AsyncClient) -> None:
    response = await client.post("/v2/runtime/fact-stream/probe", json={"marker": "blocked"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_fact_stream_probe_appends_and_queries_via_public_service(
    client: AsyncClient,
    mock_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_E2E", "1")

    response = await client.post("/v2/runtime/fact-stream/probe", json={"marker": "fact-stream-e2e"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["stream"] == "e2e.fact_stream_probe"
    assert payload["storage_path"] == "runtime/events/e2e.fact_stream_probe.jsonl"
    assert payload["artifact_exists"] is True
    assert payload["queried_total"] >= 1
    assert payload["queried_events"][0]["payload"]["marker"] == "fact-stream-e2e"

    event_path = Path(str(payload["absolute_path"]))
    assert event_path.is_file()
    assert event_path.name == "e2e.fact_stream_probe.jsonl"


@pytest.mark.asyncio
async def test_traceability_probe_is_unavailable_outside_e2e(client: AsyncClient) -> None:
    response = await client.post("/v2/runtime/traceability/probe", json={"marker": "blocked"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_traceability_probe_persists_non_empty_matrix(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_E2E", "1")

    response = await client.post("/v2/runtime/traceability/probe", json={"marker": "traceability-e2e"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["node_count"] >= 3
    assert payload["link_count"] >= 2
    assert payload["artifact_exists"] is True
    assert payload["storage_path"].endswith(".matrix.json")
    assert set(payload["node_kinds"]) >= {"doc", "task", "qa_verdict"}

    matrix_path = Path(str(payload["absolute_path"]))
    assert matrix_path.is_file()


@pytest.mark.asyncio
async def test_traceability_probe_sanitizes_marker_before_persisting_path(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_E2E", "1")

    response = await client.post("/v2/runtime/traceability/probe", json={"marker": "../bad marker/../../matrix"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"].startswith("bad-marker-matrix")
    assert payload["storage_path"].startswith("runtime/traceability/bad-marker-matrix")
    assert ".." not in payload["storage_path"]
    assert "/" not in payload["storage_path"].removeprefix("runtime/traceability/")
