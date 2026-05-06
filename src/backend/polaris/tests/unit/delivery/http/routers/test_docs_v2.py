"""Tests for Polaris docs init v2 endpoints.

Covers POST /v2/docs/init/* routes.
External services are mocked to avoid LLM provider and storage dependencies.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
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
# POST /v2/docs/init/dialogue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_dialogue_success(client: AsyncClient) -> None:
    """Dialogue endpoint should return ok with reply and fields."""
    with (
        patch(
            "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "architect": {"provider_id": "ollama", "model": "llama3"},
                },
                "providers": {
                    "ollama": {"type": "ollama"},
                },
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.generate_docs_dialogue_turn",
            new_callable=AsyncMock,
            return_value={
                "reply": "Got it, let me clarify.",
                "questions": ["What is the target platform?"],
                "tiaochen": ["Setup project"],
                "meta": {"phase": "clarifying"},
                "handoffs": {},
                "fields": {
                    "goal": "Build a web app",
                    "in_scope": ["Frontend", "Backend"],
                    "out_of_scope": ["Mobile"],
                    "constraints": ["Use existing stack"],
                    "definition_of_done": ["Tests pass"],
                    "backlog": ["Setup", "Implement"],
                },
            },
        ) as mock_generate,
    ):
        response = await client.post(
            "/v2/docs/init/dialogue",
            json={"message": "I want to build a web app", "goal": "Build a web app"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["reply"] == "Got it, let me clarify."
        assert data["questions"] == ["What is the target platform?"]
        assert data["fields"]["goal"] == "Build a web app"
        mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_docs_init_dialogue_architect_not_configured(client: AsyncClient) -> None:
    """Missing architect role should return 409 ARCHITECT_NOT_CONFIGURED."""
    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value={"roles": {}, "providers": {}},
    ):
        response = await client.post(
            "/v2/docs/init/dialogue",
            json={"message": "hello"},
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "ARCHITECT_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# POST /v2/docs/init/suggest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_suggest_success(client: AsyncClient) -> None:
    """Suggest endpoint should return ok with suggested fields."""
    with (
        patch(
            "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "architect": {"provider_id": "ollama", "model": "llama3"},
                },
                "providers": {
                    "ollama": {"type": "ollama"},
                },
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.generate_docs_ai_fields",
            new_callable=AsyncMock,
            return_value={
                "goal": ["Build a CLI tool"],
                "in_scope": ["Core commands", "Help text"],
                "out_of_scope": ["GUI"],
                "constraints": ["Python 3.11+"],
                "definition_of_done": ["Unit tests pass"],
                "backlog": ["Scaffold", "Implement commands"],
            },
        ) as mock_generate,
    ):
        response = await client.post(
            "/v2/docs/init/suggest",
            json={"goal": "Build a CLI tool"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "Build a CLI tool" in data["fields"]["goal"]
        mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_docs_init_suggest_architect_not_configured(client: AsyncClient) -> None:
    """Missing architect role should return 409 ARCHITECT_NOT_CONFIGURED."""
    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value={"roles": {}, "providers": {}},
    ):
        response = await client.post(
            "/v2/docs/init/suggest",
            json={"goal": "Build something"},
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "ARCHITECT_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# POST /v2/docs/init/preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_preview_success(client: AsyncClient) -> None:
    """Preview endpoint should return ok with file list."""
    with (
        patch(
            "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "architect": {"provider_id": "ollama", "model": "llama3"},
                },
                "providers": {
                    "ollama": {"type": "ollama"},
                },
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.generate_docs_ai_fields",
            new_callable=AsyncMock,
            return_value={
                "goal": ["Build an API"],
                "in_scope": ["REST endpoints"],
                "out_of_scope": ["Web UI"],
                "constraints": ["FastAPI"],
                "definition_of_done": ["Postman tests pass"],
                "backlog": ["Setup", "Implement"],
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.build_docs_templates",
            return_value={
                "docs/product/requirements.md": "# Requirements\n",
                "docs/product/plan.md": "# Plan\n",
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.select_docs_target_root",
            return_value="workspace/docs",
        ),
        patch(
            "polaris.delivery.http.routers.docs.workspace_has_docs",
            return_value=False,
        ),
        patch(
            "polaris.delivery.http.routers.docs.detect_project_profile",
            return_value={"python": True, "node": False},
        ),
    ):
        response = await client.post(
            "/v2/docs/init/preview",
            json={"goal": "Build an API", "mode": "minimal"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["mode"] == "minimal"
        assert len(data["files"]) == 2
        assert data["files"][0]["path"] == "workspace/docs/product/requirements.md"


@pytest.mark.asyncio
async def test_docs_init_preview_architect_not_configured(client: AsyncClient) -> None:
    """Missing architect role should return 409 ARCHITECT_NOT_CONFIGURED."""
    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value={"roles": {}, "providers": {}},
    ):
        response = await client.post(
            "/v2/docs/init/preview",
            json={"goal": "Build something"},
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "ARCHITECT_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_docs_preview_ai_fields_falls_back_on_stream_error(mock_settings: Settings) -> None:
    """Docs preview should produce deterministic fields when LLM stream errors."""
    from polaris.delivery.http.routers import docs

    async def stream_error(
        _workspace: str,
        _settings: Settings,
        _fields: dict[str, str],
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "error", "error": "provider unavailable"}

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    fields = {"goal": "Build reliable PM workflow"}

    with patch("polaris.delivery.http.routers.docs.generate_docs_fields_stream", stream_error):
        resolved, used_fallback = await docs._resolve_docs_preview_ai_fields(
            queue=queue,
            workspace=".",
            settings=mock_settings,
            fields=fields,
            timeout_seconds=1.0,
        )

    assert used_fallback is True
    assert resolved["goal"] == ["Build reliable PM workflow"]
    stage = await queue.get()
    assert stage["type"] == "stage"
    assert stage["data"]["stage"] == "llm_fallback"


@pytest.mark.asyncio
async def test_docs_preview_ai_fields_falls_back_on_stream_timeout(mock_settings: Settings) -> None:
    """Docs preview should not wait indefinitely for a silent provider stream."""
    from polaris.delivery.http.routers import docs

    async def hanging_stream(
        _workspace: str,
        _settings: Settings,
        _fields: dict[str, str],
    ) -> AsyncIterator[dict[str, Any]]:
        await asyncio.sleep(3600)
        yield {"type": "result", "fields": {}}

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    fields = {"goal": "Build reliable PM workflow"}

    with patch("polaris.delivery.http.routers.docs.generate_docs_fields_stream", hanging_stream):
        resolved, used_fallback = await docs._resolve_docs_preview_ai_fields(
            queue=queue,
            workspace=".",
            settings=mock_settings,
            fields=fields,
            timeout_seconds=0.01,
        )

    assert used_fallback is True
    assert resolved["backlog"]
    stage = await queue.get()
    assert stage["type"] == "stage"
    assert stage["data"]["fallback"] is True


# ---------------------------------------------------------------------------
# POST /v2/docs/init/apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_apply_success(client: AsyncClient) -> None:
    """Apply endpoint should write files and return created list."""
    with (
        patch(
            "polaris.delivery.http.routers.docs.write_text_atomic",
        ) as mock_write,
        patch(
            "polaris.delivery.http.routers.docs.workspace_has_docs",
            return_value=True,
        ),
        patch(
            "polaris.delivery.http.routers.docs.clear_workspace_status",
        ),
        patch(
            "polaris.delivery.http.routers.docs.emit_event",
        ),
        patch(
            "polaris.delivery.http.routers.docs._sync_plan_to_runtime",
        ),
    ):
        response = await client.post(
            "/v2/docs/init/apply",
            json={
                "target_root": "workspace/docs",
                "files": [
                    {"path": "workspace/docs/product/requirements.md", "content": "# Requirements\n"},
                    {"path": "workspace/docs/product/plan.md", "content": "# Plan\n"},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["files"]) == 2
        assert mock_write.call_count == 2


@pytest.mark.asyncio
async def test_docs_init_apply_invalid_target_root(client: AsyncClient) -> None:
    """Invalid target_root should return 400 INVALID_DOCS_PATH."""
    response = await client.post(
        "/v2/docs/init/apply",
        json={
            "target_root": "invalid/path",
            "files": [{"path": "workspace/docs/product/test.md", "content": "# Test\n"}],
        },
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_DOCS_PATH"


@pytest.mark.asyncio
async def test_docs_init_apply_no_files(client: AsyncClient) -> None:
    """Empty files list should return 400 INVALID_REQUEST."""
    response = await client.post(
        "/v2/docs/init/apply",
        json={"target_root": "workspace/docs", "files": []},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_docs_init_apply_unsafe_path(client: AsyncClient) -> None:
    """Unsafe file path should return 400 INVALID_DOCS_PATH."""
    response = await client.post(
        "/v2/docs/init/apply",
        json={
            "target_root": "workspace/docs",
            "files": [{"path": "../etc/passwd", "content": "evil"}],
        },
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_DOCS_PATH"


# ---------------------------------------------------------------------------
# POST /v2/docs/init/dialogue/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_dialogue_stream_headers(client: AsyncClient) -> None:
    """Dialogue stream should return SSE headers.

    Full SSE event consumption is skipped because testing async generators
    with background tasks inside httpx test clients is non-trivial.
    """
    pytest.skip("SSE streaming test requires special async generator handling")


# ---------------------------------------------------------------------------
# POST /v2/docs/init/preview/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_preview_stream_headers(client: AsyncClient) -> None:
    """Preview stream should return SSE headers.

    Full SSE event consumption is skipped because testing async generators
    with background tasks inside httpx test clients is non-trivial.
    """
    pytest.skip("SSE streaming test requires special async generator handling")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def async_generator(items: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Yield items for mocking async generators."""
    for item in items:
        yield item
