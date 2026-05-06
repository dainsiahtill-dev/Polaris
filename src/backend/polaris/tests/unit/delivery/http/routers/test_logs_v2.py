"""Tests for Polaris v2 logs router.

Covers GET /logs/v2/query, POST /logs/v2/user-action, and GET /logs/v2/channels.
External services are mocked to avoid storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
def mock_app_state(mock_settings: Settings) -> AppState:
    """Create a minimal AppState for testing."""
    return AppState(settings=mock_settings)


@pytest.fixture
async def client(mock_settings: Settings, mock_app_state: AppState) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict(
            "os.environ",
            {
                "KERNELONE_METRICS_ENABLED": "false",
                "KERNELONE_RATE_LIMIT_ENABLED": "false",
            },
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# GET /logs/v2/query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_logs_query_success(client: AsyncClient) -> None:
    """GET /logs/v2/query should return filtered log events."""
    mock_event = MagicMock()
    mock_event.model_dump.return_value = {
        "event_id": "evt-1",
        "channel": "system",
        "severity": "info",
        "message": "test",
    }
    mock_result = MagicMock()
    mock_result.events = [mock_event]
    mock_result.next_cursor = None
    mock_result.total_count = 1
    mock_result.has_more = False

    with patch(
        "polaris.delivery.http.routers.logs.LogQueryService",
    ) as mock_service_cls:
        mock_service_cls.return_value.query.return_value = mock_result

        response = await client.get("/logs/v2/query?channel=system&severity=info&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["events"] == [
            {"event_id": "evt-1", "channel": "system", "severity": "info", "message": "test"},
        ]
        assert data["total_count"] == 1
        assert data["has_more"] is False


@pytest.mark.asyncio
async def test_v2_logs_query_no_results(client: AsyncClient) -> None:
    """GET /logs/v2/query should handle empty results gracefully."""
    mock_result = MagicMock()
    mock_result.events = []
    mock_result.next_cursor = None
    mock_result.total_count = 0
    mock_result.has_more = False

    with patch(
        "polaris.delivery.http.routers.logs.LogQueryService",
    ) as mock_service_cls:
        mock_service_cls.return_value.query.return_value = mock_result

        response = await client.get("/logs/v2/query")
        assert response.status_code == 200
        data = response.json()
        assert data["events"] == []
        assert data["total_count"] == 0


# ---------------------------------------------------------------------------
# POST /logs/v2/user-action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_logs_user_action_success(client: AsyncClient) -> None:
    """POST /logs/v2/user-action should log and return confirmation."""
    with (
        patch(
            "polaris.delivery.http.routers.logs.resolve_runtime_path",
            return_value="/tmp/user_actions.jsonl",
        ),
        patch(
            "polaris.kernelone.fs.jsonl.ops.append_jsonl_atomic",
        ) as mock_append,
    ):
        response = await client.post(
            "/logs/v2/user-action",
            json={"action": "click_button", "user": "tester", "metadata": {"page": "home"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "logged"
        assert data["action"] == "click_button"
        mock_append.assert_called_once()


@pytest.mark.asyncio
async def test_v2_logs_user_action_failure(client: AsyncClient) -> None:
    """POST /logs/v2/user-action should 500 on append failure."""
    with (
        patch(
            "polaris.delivery.http.routers.logs.resolve_runtime_path",
            return_value="/tmp/user_actions.jsonl",
        ),
        patch(
            "polaris.kernelone.fs.jsonl.ops.append_jsonl_atomic",
            side_effect=RuntimeError("disk full"),
        ),
    ):
        response = await client.post(
            "/logs/v2/user-action",
            json={"action": "click"},
        )
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "LOG_USER_ACTION_FAILED"
        assert "disk full" in data["error"]["message"]


# ---------------------------------------------------------------------------
# GET /logs/v2/channels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_logs_channels(client: AsyncClient) -> None:
    """GET /logs/v2/channels should return available log channels."""
    response = await client.get("/logs/v2/channels")
    assert response.status_code == 200
    data = response.json()
    assert "channels" in data
    names = [c["name"] for c in data["channels"]]
    assert "system" in names
    assert "process" in names
    assert "llm" in names
