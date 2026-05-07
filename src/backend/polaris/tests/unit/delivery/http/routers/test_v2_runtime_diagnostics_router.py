"""Tests for v2 runtime diagnostics router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState


@pytest.fixture
def mock_settings() -> Settings:
    """Create minimal Settings for runtime diagnostics tests."""

    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=True, required=True, url="nats://user:secret@127.0.0.1:4222")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
async def client(mock_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""

    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.app_state = AppState(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()
    app.state.connection_state.active_connections = 2
    app.state.connection_state.total_connections = 5
    app.state.connection_state.last_event = "open"
    app.state.connection_state.channels = {"llm", "director"}

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch("polaris.bootstrap.assembly.assemble_core_services"),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch("polaris.delivery.http.app_factory.sync_process_settings_environment"),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_runtime_diagnostics_exposes_nats_ws_and_rate_limit(client: AsyncClient) -> None:
    """Diagnostics should aggregate runtime state without leaking NATS credentials."""

    server_snapshot = {
        "configured_url": "nats://127.0.0.1:4222",
        "managed": True,
        "host": "127.0.0.1",
        "port": 4222,
        "tcp_reachable": True,
        "executable_found": True,
        "executable_path": "C:/Tools/nats-server.exe",
        "storage_root": "C:/Temp/polaris/runtime/nats/jetstream",
        "stdout_log_path": "C:/Temp/polaris/runtime/nats/nats-server.stdout.log",
        "stderr_log_path": "C:/Temp/polaris/runtime/nats/nats-server.stderr.log",
        "process_pid": 1234,
        "process_running": True,
    }
    client_snapshot = {
        "default_client_exists": True,
        "state": "connected",
        "is_connected": True,
        "server_info": {"server_url": "nats://127.0.0.1:4222", "connected": True},
        "last_connect_failure": {
            "error_type": None,
            "message": None,
            "age_seconds": None,
            "cooldown_seconds": 5.0,
        },
    }
    rate_limit_snapshot = {
        "enabled": True,
        "requests_per_second": 10.0,
        "burst_size": 20,
        "excluded_paths": [],
        "exempt_loopback": False,
        "store": {
            "entry_count": 1,
            "blocked_count": 0,
            "total_violations": 0,
            "clients": [{"client_key_hash": "abc123", "total_violations": 0}],
        },
    }

    with (
        patch(
            "polaris.delivery.http.v2.runtime_diagnostics.get_managed_nats_runtime_snapshot",
            return_value=server_snapshot,
        ),
        patch(
            "polaris.delivery.http.v2.runtime_diagnostics.get_default_client_snapshot",
            return_value=client_snapshot,
        ),
        patch(
            "polaris.delivery.http.v2.runtime_diagnostics.get_rate_limit_diagnostics",
            return_value=rate_limit_snapshot,
        ),
    ):
        response = await client.get("/v2/runtime/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "runtime_diagnostics.v1"
    assert data["nats"]["state"] == "connected"
    assert data["nats"]["details"]["managed_server"]["configured_url"] == "nats://127.0.0.1:4222"
    assert "secret" not in str(data["nats"])
    assert data["websocket"]["details"]["active_connections"] == 2
    assert data["websocket"]["details"]["channels"] == ["director", "llm"]
    assert data["rate_limit"]["state"] == "active"
    assert data["rate_limit"]["details"]["store"]["clients"][0]["client_key_hash"] == "abc123"
