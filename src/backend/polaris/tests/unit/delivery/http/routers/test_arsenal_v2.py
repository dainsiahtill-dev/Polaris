"""Tests for Polaris arsenal v2 endpoints.

Covers GET /v2/vision/status, POST /v2/vision/analyze,
GET /v2/scheduler/status, POST /v2/scheduler/start, POST /v2/scheduler/stop,
GET /v2/code_map, POST /v2/code/index, POST /v2/code/search,
GET /v2/mcp/status, GET /v2/director/capabilities.
External services are mocked to avoid vision, scheduler, code indexer,
MCP service, and director capability dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
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
    settings.workspace = Path(".")
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
# GET /v2/vision/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_vision_status_success(client: AsyncClient) -> None:
    """Vision status should return service status when available."""
    mock_service = MagicMock()
    mock_service.get_status.return_value = {
        "pil_available": True,
        "advanced_available": True,
        "model_loaded": True,
    }

    with patch(
        "polaris.delivery.http.routers.arsenal.get_vision_service",
        return_value=mock_service,
    ):
        response = await client.get("/arsenal/v2/vision/status")
        assert response.status_code == 200
        data = response.json()
        assert data["pil_available"] is True
        assert data["advanced_available"] is True
        assert data["model_loaded"] is True
        mock_service.get_status.assert_called_once()


@pytest.mark.asyncio
async def test_v2_vision_status_unavailable(client: AsyncClient) -> None:
    """Vision status should return defaults when service is unavailable."""
    with patch(
        "polaris.delivery.http.routers.arsenal.get_vision_service",
        side_effect=RuntimeError("vision service not configured"),
    ):
        response = await client.get("/arsenal/v2/vision/status")
        assert response.status_code == 200
        data = response.json()
        assert data["pil_available"] is False
        assert data["advanced_available"] is False
        assert data["model_loaded"] is False


# ---------------------------------------------------------------------------
# POST /v2/vision/analyze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_vision_analyze_success(client: AsyncClient) -> None:
    """Vision analyze should return analysis result."""
    mock_service = MagicMock()
    mock_service.is_loaded = True
    mock_service.analyze_image.return_value = {
        "result": "detected 3 objects",
        "confidence": 0.95,
    }

    with patch(
        "polaris.delivery.http.routers.arsenal.get_vision_service",
        return_value=mock_service,
    ):
        response = await client.post(
            "/arsenal/v2/vision/analyze",
            json={"image": "base64encodeddata", "task": "<OD>"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "detected 3 objects"
        assert data["confidence"] == 0.95
        mock_service.analyze_image.assert_called_once_with("base64encodeddata", "<OD>")


@pytest.mark.asyncio
async def test_v2_vision_analyze_auto_load(client: AsyncClient) -> None:
    """Vision analyze should auto-load model if not already loaded."""
    mock_service = MagicMock()
    mock_service.is_loaded = False
    mock_service.analyze_image.return_value = {"result": "analysis"}

    with patch(
        "polaris.delivery.http.routers.arsenal.get_vision_service",
        return_value=mock_service,
    ):
        response = await client.post(
            "/arsenal/v2/vision/analyze",
            json={"image": "base64data", "task": "<OCR>"},
        )
        assert response.status_code == 200
        mock_service.load_model.assert_called_once()
        mock_service.analyze_image.assert_called_once_with("base64data", "<OCR>")


# ---------------------------------------------------------------------------
# GET /v2/scheduler/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_scheduler_status(client: AsyncClient) -> None:
    """Scheduler status should return turbo_disabled status."""
    response = await client.get("/arsenal/v2/scheduler/status")
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False
    assert data["active"] is False
    assert data["reason"] == "turbo_disabled"


# ---------------------------------------------------------------------------
# POST /v2/scheduler/start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_scheduler_start(client: AsyncClient) -> None:
    """Scheduler start should return turbo_disabled status with message."""
    response = await client.post("/arsenal/v2/scheduler/start")
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False
    assert data["active"] is False
    assert data["reason"] == "turbo_disabled"
    assert data["message"] == "turbo feature is disabled"


# ---------------------------------------------------------------------------
# POST /v2/scheduler/stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_scheduler_stop(client: AsyncClient) -> None:
    """Scheduler stop should return turbo_disabled status with message."""
    response = await client.post("/arsenal/v2/scheduler/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False
    assert data["active"] is False
    assert data["reason"] == "turbo_disabled"
    assert data["message"] == "turbo feature is disabled"


# ---------------------------------------------------------------------------
# GET /v2/code_map
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_code_map_success(client: AsyncClient) -> None:
    """Code map should return project map with points."""
    with (
        patch(
            "polaris.delivery.http.routers.arsenal.get_state",
            return_value=MagicMock(settings=MagicMock(workspace=".")),
        ),
        patch(
            "polaris.delivery.http.routers.arsenal.os.path.isdir",
            return_value=True,
        ),
        patch(
            "polaris.delivery.http.routers.arsenal.os.walk",
            return_value=[
                (".", [], ["main.py", "README.md"]),
            ],
        ),
        patch(
            "polaris.delivery.http.routers.arsenal.os.path.getsize",
            return_value=100,
        ),
        patch(
            "builtins.open",
            MagicMock(return_value=MagicMock(read=lambda: "print('hello')")),
        ),
    ):
        response = await client.get("/arsenal/v2/code_map")
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert data["mode"] == "cpu"
        assert data["engine_active"] is False


@pytest.mark.asyncio
async def test_v2_code_map_invalid_workspace(client: AsyncClient) -> None:
    """Code map should return 400 when workspace is invalid."""
    with (
        patch(
            "polaris.delivery.http.routers.arsenal.get_state",
            return_value=MagicMock(settings=MagicMock(workspace="/nonexistent")),
        ),
        patch(
            "polaris.delivery.http.routers.arsenal.os.path.isdir",
            return_value=False,
        ),
    ):
        response = await client.get("/arsenal/v2/code_map")
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_WORKSPACE"


@pytest.mark.asyncio
async def test_v2_code_map_arrow_format(client: AsyncClient) -> None:
    """Code map should return Arrow IPC when format=arrow and service is available."""
    mock_arrow = MagicMock()
    mock_arrow.available = True
    mock_arrow.to_arrow_ipc.return_value = b"arrow_ipc_bytes"

    with (
        patch(
            "polaris.delivery.http.routers.arsenal.get_state",
            return_value=MagicMock(settings=MagicMock(workspace=".")),
        ),
        patch(
            "polaris.delivery.http.routers.arsenal.os.path.isdir",
            return_value=True,
        ),
        patch(
            "polaris.delivery.http.routers.arsenal.os.walk",
            return_value=[(".", [], ["main.py"])],
        ),
        patch(
            "polaris.delivery.http.routers.arsenal.os.path.getsize",
            return_value=50,
        ),
        patch(
            "builtins.open",
            MagicMock(return_value=MagicMock(read=lambda: "x = 1")),
        ),
        patch(
            "polaris.delivery.http.routers.arsenal.get_arrow_service",
            return_value=mock_arrow,
        ),
    ):
        response = await client.get("/arsenal/v2/code_map?format=arrow")
        assert response.status_code == 200
        assert response.content == b"arrow_ipc_bytes"
        assert response.headers["content-type"] == "application/vnd.apache.arrow.stream"


# ---------------------------------------------------------------------------
# POST /v2/code/index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_code_index_success(client: AsyncClient) -> None:
    """Code index should return ok with indexing result."""
    with (
        patch(
            "polaris.delivery.http.routers.arsenal.get_state",
            return_value=MagicMock(settings=MagicMock(workspace=".")),
        ),
        patch(
            "polaris.infrastructure.db.repositories.lancedb_code_search.index_workspace",
            return_value=[{"file": "main.py", "status": "indexed"}],
        ),
    ):
        response = await client.post("/arsenal/v2/code/index")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["result"] == [{"file": "main.py", "status": "indexed"}]


@pytest.mark.asyncio
async def test_v2_code_index_failure(client: AsyncClient) -> None:
    """Code index should handle indexing failure gracefully."""
    with (
        patch(
            "polaris.delivery.http.routers.arsenal.get_state",
            return_value=MagicMock(settings=MagicMock(workspace=".")),
        ),
        patch(
            "polaris.infrastructure.db.repositories.lancedb_code_search.index_workspace",
            side_effect=RuntimeError("lancedb not available"),
        ),
    ):
        response = await client.post("/arsenal/v2/code/index")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "lancedb not available" in data["error"]


# ---------------------------------------------------------------------------
# POST /v2/code/search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_code_search_success(client: AsyncClient) -> None:
    """Code search should return matching results."""
    with (
        patch(
            "polaris.delivery.http.routers.arsenal.get_state",
            return_value=MagicMock(settings=MagicMock(workspace=".")),
        ),
        patch(
            "polaris.infrastructure.db.repositories.lancedb_code_search.search_code",
            return_value=[{"file": "main.py", "score": 0.95}],
        ),
    ):
        response = await client.post(
            "/arsenal/v2/code/search",
            json={"query": "hello world", "limit": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["results"]) == 1
        assert data["results"][0]["file"] == "main.py"


@pytest.mark.asyncio
async def test_v2_code_search_failure(client: AsyncClient) -> None:
    """Code search should handle search failure gracefully."""
    with (
        patch(
            "polaris.delivery.http.routers.arsenal.get_state",
            return_value=MagicMock(settings=MagicMock(workspace=".")),
        ),
        patch(
            "polaris.infrastructure.db.repositories.lancedb_code_search.search_code",
            side_effect=ValueError("invalid query"),
        ),
    ):
        response = await client.post(
            "/arsenal/v2/code/search",
            json={"query": "test", "limit": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "invalid query" in data["error"]
        assert data["results"] == []


# ---------------------------------------------------------------------------
# GET /v2/mcp/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_mcp_status_file_not_found(client: AsyncClient) -> None:
    """MCP status should return unavailable when server file is missing."""
    with patch(
        "polaris.delivery.http.routers.arsenal.os.path.isfile",
        return_value=False,
    ):
        response = await client.get("/arsenal/v2/mcp/status")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["healthy"] is False
        assert data["error"] == "Server file not found"
        assert data["tools"] == []


@pytest.mark.asyncio
async def test_v2_mcp_status_healthy(client: AsyncClient) -> None:
    """MCP status should return healthy when server responds correctly."""
    import json

    mock_proc = AsyncMock()
    health_data = {
        "status": "healthy",
        "version": "1.0.0",
        "tools_available": ["health"],
        "uptime_seconds": 120,
        "workspace": ".",
    }
    # The text field contains a JSON-stringified object, so we must double-encode.
    health_json = json.dumps(health_data)
    result_obj = {"content": [{"text": health_json}]}
    stdout_text = (
        '{"jsonrpc":"2.0","id":1,"result":{}}\n' + json.dumps({"jsonrpc": "2.0", "id": 2, "result": result_obj}) + "\n"
    )
    mock_proc.communicate.return_value = (
        stdout_text.encode("utf-8"),
        b"",
    )

    with (
        patch(
            "polaris.delivery.http.routers.arsenal.os.path.isfile",
            return_value=True,
        ),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ),
    ):
        response = await client.get("/arsenal/v2/mcp/status")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["healthy"] is True
        assert data["server_version"] == "1.0.0"
        assert "health" in data["tools"]


@pytest.mark.asyncio
async def test_v2_mcp_status_timeout(client: AsyncClient) -> None:
    """MCP status should handle health check timeout."""
    import asyncio

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        side_effect=asyncio.TimeoutError("timed out"),
    )
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with (
        patch(
            "polaris.delivery.http.routers.arsenal.os.path.isfile",
            return_value=True,
        ),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ),
    ):
        response = await client.get("/arsenal/v2/mcp/status")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["healthy"] is False
        assert "timed out" in data["health_check"]["error"]


# ---------------------------------------------------------------------------
# GET /v2/director/capabilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_director_capabilities_success(client: AsyncClient) -> None:
    """Director capabilities should return role capabilities."""
    with patch(
        "polaris.domain.entities.capability.get_role_capabilities",
        return_value={"can_execute": True, "can_plan": True},
    ):
        response = await client.get("/arsenal/v2/director/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "director"
        assert data["capabilities"] == {"can_execute": True, "can_plan": True}


@pytest.mark.asyncio
async def test_v2_director_capabilities_failure(client: AsyncClient) -> None:
    """Director capabilities should return 500 when capability loading fails."""
    with patch(
        "polaris.domain.entities.capability.get_role_capabilities",
        side_effect=RuntimeError("capability registry unavailable"),
    ):
        response = await client.get("/arsenal/v2/director/capabilities")
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "CAPABILITY_LOAD_FAILED"
        assert "capability registry unavailable" in data["error"]["message"]
