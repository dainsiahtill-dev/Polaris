"""Tests for Polaris v2 Role Chat router.

Covers role chat endpoints: ping, status, roles list, llm-events,
cache-stats, and cache-clear. External services are mocked to avoid
LLM provider and storage dependencies.
"""

from __future__ import annotations

import asyncio
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
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# Role Chat Ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_chat_ping(client: AsyncClient) -> None:
    """Role chat ping should return ok with supported roles."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_registered_roles",
        return_value=["pm", "architect", "director", "qa", "chief_engineer"],
    ):
        response = await client.get("/v2/role/chat/ping")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "pm" in data["supported_roles"]
        assert data["supported_roles"] == ["pm", "architect", "director", "qa", "chief_engineer"]


@pytest.mark.asyncio
async def test_role_chat_ping_empty_roles(client: AsyncClient) -> None:
    """Role chat ping should handle empty roles list gracefully."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_registered_roles",
        return_value=[],
    ):
        response = await client.get("/v2/role/chat/ping")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["supported_roles"] == []


# ---------------------------------------------------------------------------
# Role Chat Status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_chat_status_not_configured(client: AsyncClient) -> None:
    """Role chat status for unconfigured role should report not ready."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={"roles": {}, "providers": {}},
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False
        assert data["error"] == "Role not configured"
        assert data["debug"]["supported_roles"]


@pytest.mark.asyncio
async def test_role_chat_status_configured(client: AsyncClient) -> None:
    """Role chat status for configured role should report ready."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={"roles": {"pm": {"ready": True}}},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "pm": {"provider_id": "openai", "model": "gpt-4", "profile": "default"},
                },
                "providers": {
                    "openai": {"type": "openai"},
                },
            },
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["configured"] is True
        assert data["llm_test_ready"] is True
        assert data["role_config"]["provider_id"] == "openai"
        assert data["role_config"]["model"] == "gpt-4"
        assert data["provider_type"] == "openai"


@pytest.mark.asyncio
async def test_role_chat_status_accepts_workspace_query_override(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Role chat readiness should read LLM state from the requested desktop workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"

    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={"roles": {"pm": {"ready": True}}},
        ) as mock_index,
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "pm": {"provider_id": "openai", "model": "gpt-4", "profile": "default"},
                },
                "providers": {
                    "openai": {"type": "openai"},
                },
            },
        ) as mock_config,
    ):
        response = await client.get(
            "/v2/role/pm/chat/status",
            params={"workspace": "C:/Temp/Product"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["workspace"] == "C:/Temp/Product"
    mock_index.assert_called_once_with("C:/Temp/Product")
    assert mock_config.call_args.args[0] == "C:/Temp/Product"
    assert str(mock_settings.workspace) == "C:/Repo/Polaris"


@pytest.mark.asyncio
async def test_role_chat_generation_uses_requested_workspace(client: AsyncClient, mock_settings: Settings) -> None:
    """Non-streaming role chat should pin readiness and generation to the requested workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"

    with (
        patch("polaris.delivery.http.routers.role_chat.get_registered_roles", return_value=["pm"]),
        patch("polaris.delivery.http.routers.role_chat.ensure_required_roles_ready") as mock_ready,
        patch(
            "polaris.delivery.http.routers.role_chat.execute_role_chat_nonstreaming",
            new_callable=AsyncMock,
            return_value={"response": "plan ready", "role": "pm"},
        ) as mock_generate,
    ):
        response = await client.post(
            "/v2/role/pm/chat",
            params={"workspace": "C:/Temp/Product"},
            json={"message": "Create plan", "context": {"task_count": 3}},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] == "C:/Temp/Product"
    assert data["response"] == "plan ready"
    ready_state = mock_ready.call_args.args[0]
    assert str(ready_state.settings.workspace).replace("\\", "/") == "C:/Temp/Product"
    generate_args = mock_generate.await_args
    assert generate_args is not None
    assert generate_args.kwargs["workspace"] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_role_chat_jetstream_uses_context_workspace(client: AsyncClient, mock_settings: Settings) -> None:
    """JetStream role chat should use context.workspace when the query is omitted."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"

    with (
        patch("polaris.delivery.http.routers.role_chat.get_registered_roles", return_value=["director"]),
        patch("polaris.delivery.http.routers.role_chat.ensure_required_roles_ready") as mock_ready,
        patch(
            "polaris.delivery.http.routers.role_chat.execute_role_chat_jetstream",
            new_callable=AsyncMock,
        ) as mock_jetstream,
    ):
        response = await client.post(
            "/v2/role/director/chat/jetstream",
            json={"message": "Run task", "context": {"workspace": "C:/Temp/Product"}},
        )
        await asyncio.sleep(0)

    assert response.status_code == 200
    data = response.json()
    assert data["transport"] == "nats-jetstream"
    assert data["channel"].startswith("chat:")
    ready_state = mock_ready.call_args.args[0]
    assert str(ready_state.settings.workspace).replace("\\", "/") == "C:/Temp/Product"
    jetstream_args = mock_jetstream.await_args
    assert jetstream_args is not None
    assert jetstream_args.kwargs["workspace"] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_role_chat_status_missing_provider(client: AsyncClient) -> None:
    """Role chat status should report not ready when provider is missing."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "pm": {"provider_id": "missing_provider", "model": "gpt-4"},
                },
                "providers": {},
            },
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False
        assert data["error"] == "Provider not found"
        assert data["debug"]["role_config"]["provider_id"] == "missing_provider"
        assert data["debug"]["available_providers"] == []


@pytest.mark.asyncio
async def test_role_chat_status_missing_model(client: AsyncClient) -> None:
    """Role chat status should report not ready when model is empty."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "pm": {"provider_id": "openai", "model": ""},
                },
                "providers": {
                    "openai": {"type": "openai"},
                },
            },
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False
        assert "provider or model not set" in data["error"]


@pytest.mark.asyncio
async def test_role_chat_status_exception(client: AsyncClient) -> None:
    """Role chat status should handle exceptions gracefully."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            side_effect=RuntimeError("config load failed"),
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False
        assert data["llm_test_ready"] is False
        assert data["message"] == "Status check failed"
        assert data["code"] == "internal_error"
        assert "config load failed" in data["details"]["exception"]


# ---------------------------------------------------------------------------
# List Supported Roles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_supported_roles(client: AsyncClient) -> None:
    """List supported roles endpoint should return all roles."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_registered_roles",
        return_value=["pm", "architect", "director", "qa", "chief_engineer"],
    ):
        response = await client.get("/v2/role/chat/roles")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5
        assert "pm" in data["roles"]
        assert "chief_engineer" in data["roles"]


# ---------------------------------------------------------------------------
# Role Chat Status - Additional Roles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_chat_status_architect(client: AsyncClient) -> None:
    """Role chat status for architect should work when configured."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={"roles": {"architect": {"ready": True}}},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "architect": {"provider_id": "anthropic", "model": "claude-3"},
                },
                "providers": {
                    "anthropic": {"type": "anthropic"},
                },
            },
        ),
    ):
        response = await client.get("/v2/role/architect/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["role_config"]["provider_id"] == "anthropic"


@pytest.mark.asyncio
async def test_role_chat_status_chief_engineer(client: AsyncClient) -> None:
    """Role chat status for Chief Engineer should work when configured."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={"roles": {"chief_engineer": {"ready": True}}},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "chief_engineer": {"provider_id": "openai", "model": "gpt-5", "profile": "ce"},
                },
                "providers": {
                    "openai": {"type": "openai"},
                },
            },
        ),
    ):
        response = await client.get("/v2/role/chief_engineer/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["configured"] is True
        assert data["llm_test_ready"] is True
        assert data["role"] == "chief_engineer"
        assert data["role_config"]["provider_id"] == "openai"
        assert data["role_config"]["model"] == "gpt-5"
        assert data["provider_type"] == "openai"


@pytest.mark.asyncio
async def test_role_chat_status_director_not_configured(client: AsyncClient) -> None:
    """Role chat status for director should report not ready when missing."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {"pm": {"provider_id": "openai", "model": "gpt-4"}},
                "providers": {"openai": {"type": "openai"}},
            },
        ),
    ):
        response = await client.get("/v2/role/director/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["error"] == "Role not configured"
        assert data["debug"]["roles_keys"] == ["pm"]


@pytest.mark.asyncio
async def test_role_chat_status_with_test_index_only(client: AsyncClient) -> None:
    """Test index ready should not override missing config."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={"roles": {"qa": {"ready": True}}},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={"roles": {}, "providers": {}},
        ),
    ):
        response = await client.get("/v2/role/qa/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False


# ---------------------------------------------------------------------------
# Role Kernel Diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_llm_events_returns_role_scoped_kernel_events(client: AsyncClient) -> None:
    """Role LLM events should return filtered events from the shared emitter."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_call_start"
    mock_event.metadata = {"workspace": "."}
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "role": "pm",
        "run_id": "run-1",
        "task_id": "PM-1",
        "attempt": 1,
    }

    with patch(
        "polaris.delivery.http.routers.role_chat.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/role/pm/llm-events?run_id=run-1&task_id=PM-1&limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "pm"
    assert data["run_id"] == "run-1"
    assert data["task_id"] == "PM-1"
    assert data["events"] == [mock_event.to_dict.return_value]
    assert data["stats"]["total"] == 1
    assert data["stats"]["call_start"] == 1
    mock_emitter.get_events.assert_called_once_with(
        run_id="run-1",
        task_id="PM-1",
        role="pm",
        limit=5,
    )


@pytest.mark.asyncio
async def test_role_llm_events_filters_requested_workspace(client: AsyncClient) -> None:
    """Role LLM events should not mix events from another desktop workspace."""
    matching_event = MagicMock()
    matching_event.event_type = "llm_call_start"
    matching_event.metadata = {"workspace": "C:/Temp/Product"}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "role": "pm",
        "run_id": "run-1",
        "task_id": "PM-1",
        "workspace": "C:/Temp/Product",
    }
    other_event = MagicMock()
    other_event.event_type = "llm_call_start"
    other_event.metadata = {"workspace": "D:/Other/Product"}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "role": "pm",
        "run_id": "run-1",
        "task_id": "PM-1",
        "workspace": "D:/Other/Product",
    }

    with patch(
        "polaris.delivery.http.routers.role_chat.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/role/pm/llm-events?run_id=run-1&workspace=C:/Temp/Product")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] == "C:/Temp/Product"
    assert data["events"] == [matching_event.to_dict.return_value]
    assert data["stats"]["total"] == 1


@pytest.mark.asyncio
async def test_role_all_llm_events_returns_filtered_kernel_events(client: AsyncClient) -> None:
    """All-role LLM events should expose shared emitter events and counts."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_call_end"
    mock_event.metadata = {"workspace": "."}
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_end",
        "role": "director",
        "run_id": "run-2",
        "task_id": None,
        "attempt": 1,
    }

    with patch(
        "polaris.delivery.http.routers.role_chat.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/role/llm-events?run_id=run-2&role=director&limit=10")

    assert response.status_code == 200
    data = response.json()
    assert data["events"] == [mock_event.to_dict.return_value]
    assert data["count"] == 1
    mock_emitter.get_events.assert_called_once_with(
        run_id="run-2",
        task_id=None,
        role="director",
        limit=10,
    )


@pytest.mark.asyncio
async def test_role_all_llm_events_filters_active_workspace_by_default(client: AsyncClient) -> None:
    """All-role LLM events should filter to active workspace without query workspace."""
    matching_event = MagicMock()
    matching_event.event_type = "llm_call_end"
    matching_event.metadata = {"workspace": "."}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_end",
        "role": "director",
        "run_id": "run-active",
    }
    other_event = MagicMock()
    other_event.event_type = "llm_call_end"
    other_event.metadata = {"workspace": "/tmp/other-workspace"}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_end",
        "role": "director",
        "run_id": "run-other",
    }

    with patch(
        "polaris.delivery.http.routers.role_chat.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/role/llm-events?role=director&limit=10")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] == "."
    assert data["count"] == 1
    assert data["events"][0]["run_id"] == "run-active"


@pytest.mark.asyncio
async def test_role_all_llm_events_filters_requested_workspace(client: AsyncClient) -> None:
    """All-role LLM events should support workspace-scoped desktop diagnostics."""
    matching_event = MagicMock()
    matching_event.event_type = "llm_call_end"
    matching_event.metadata = {"extra_fields": {"workspace": "C:/Temp/Product"}}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_end",
        "role": "director",
        "run_id": "run-2",
        "workspace": "C:/Temp/Product",
    }
    other_event = MagicMock()
    other_event.event_type = "llm_call_end"
    other_event.metadata = {"extra_fields": {"workspace": "D:/Other/Product"}}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_end",
        "role": "director",
        "run_id": "run-2",
        "workspace": "D:/Other/Product",
    }

    with patch(
        "polaris.delivery.http.routers.role_chat.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/role/llm-events?role=director&workspace=C:/Temp/Product")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] == "C:/Temp/Product"
    assert data["events"] == [matching_event.to_dict.return_value]
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_role_cache_stats_returns_shared_kernel_cache_stats(client: AsyncClient) -> None:
    """Role cache stats should return the shared role-kernel cache payload."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {"hits": 4, "misses": 2, "size": 6}
        mock_get_cache.return_value = mock_cache

        response = await client.get("/v2/role/cache-stats")

    assert response.status_code == 200
    assert response.json() == {"hits": 4, "misses": 2, "size": 6}
    mock_cache.get_stats.assert_called_once_with()


@pytest.mark.asyncio
async def test_role_cache_clear_rejects_forged_admin_role(client: AsyncClient) -> None:
    """Cache clear must be denied before role headers can influence RBAC."""
    response = await client.post("/v2/role/cache-clear", headers={"X-User-Role": "admin"})
    assert response.status_code == 403
    assert response.json()["detail"] == "role 'viewer' not authorized for this resource"
