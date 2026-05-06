"""Comprehensive error handling tests for Polaris v2 routes.

Covers standardized error responses (400, 403, 404, 409, 500) across multiple
v2 routers. All external services are mocked to trigger each error condition
and verify the response follows the {error: {code, message, details}} structure.
"""

from __future__ import annotations

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
# Helpers
# ---------------------------------------------------------------------------


def _assert_structured_error(response_data: dict[str, Any], expected_code: str) -> None:
    """Assert that the response contains a structured error with the expected code."""
    assert "error" in response_data
    assert response_data["error"]["code"] == expected_code
    assert "message" in response_data["error"]


# ============================================================================
# 400 BAD_REQUEST
# ============================================================================


@pytest.mark.asyncio
async def test_role_chat_empty_message_400(client: AsyncClient) -> None:
    """POST /v2/role/pm/chat with empty message returns 400 INVALID_REQUEST."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_registered_roles",
        return_value=["pm"],
    ):
        response = await client.post("/v2/role/pm/chat", json={"message": ""})
        assert response.status_code == 400
        data = response.json()
        _assert_structured_error(data, "INVALID_REQUEST")
        assert "message is required" in data["error"]["message"]


@pytest.mark.asyncio
async def test_role_chat_unsupported_role_400(client: AsyncClient) -> None:
    """POST /v2/role/unknown/chat returns 400 UNSUPPORTED_ROLE."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_registered_roles",
        return_value=["pm", "architect"],
    ):
        response = await client.post("/v2/role/unknown/chat", json={"message": "hello"})
        assert response.status_code == 400
        data = response.json()
        _assert_structured_error(data, "UNSUPPORTED_ROLE")
        assert "unknown" in data["error"]["message"]


@pytest.mark.asyncio
async def test_docs_init_apply_invalid_target_root_400(client: AsyncClient) -> None:
    """POST /v2/docs/init/apply with invalid target_root returns 400 INVALID_DOCS_PATH."""
    response = await client.post(
        "/v2/docs/init/apply",
        json={
            "target_root": "invalid/path",
            "files": [{"path": "docs/test.md", "content": "hello"}],
        },
    )
    assert response.status_code == 400
    data = response.json()
    _assert_structured_error(data, "INVALID_DOCS_PATH")
    assert "workspace/docs" in data["error"]["message"]


# ============================================================================
# 403 FORBIDDEN
# ============================================================================


@pytest.mark.asyncio
async def test_role_cache_clear_rbac_denied_403(client: AsyncClient) -> None:
    """POST /v2/role/cache-clear with insufficient role returns 403."""
    # The default auth in the fixture allows all, but the RBAC middleware
    # assigns VIEWER role when no auth_context is present. We need to ensure
    # the request goes through the RBAC checker with a non-admin role.
    with patch(
        "polaris.delivery.http.middleware.rbac.extract_role_from_request",
        return_value=MagicMock(value="viewer"),
    ):
        response = await client.post("/v2/role/cache-clear")
        assert response.status_code == 403
        data = response.json()
        # RBAC uses raw HTTPException which returns {"detail": ...}
        assert "detail" in data
        assert "not authorized" in data["detail"].lower()


# ============================================================================
# 404 NOT_FOUND
# ============================================================================


@pytest.mark.asyncio
async def test_factory_run_not_found_404(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{id} for non-existent run returns 404 RUN_NOT_FOUND."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/nonexistent")
        assert response.status_code == 404
        data = response.json()
        _assert_structured_error(data, "RUN_NOT_FOUND")
        assert "nonexistent" in data["error"]["message"]


@pytest.mark.asyncio
async def test_llm_test_report_not_found_404(client: AsyncClient) -> None:
    """GET /v2/llm/test/{run_id} for non-existent test run returns 404 REPORT_NOT_FOUND."""
    with patch(
        "polaris.delivery.http.routers.tests.os.path.isfile",
        return_value=False,
    ):
        response = await client.get("/v2/llm/test/nonexistent-run")
        assert response.status_code == 404
        data = response.json()
        _assert_structured_error(data, "REPORT_NOT_FOUND")
        assert "report not found" in data["error"]["message"]


@pytest.mark.asyncio
async def test_court_actor_not_found_404(client: AsyncClient) -> None:
    """GET /v2/court/actors/{role_id} for non-existent actor returns 404 ROLE_NOT_FOUND."""
    with (
        patch(
            "polaris.delivery.http.routers.court._get_engine_status",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.court._get_pm_status",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.court._get_director_status",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.court.map_engine_to_court_state",
            return_value={"actors": {}},
        ),
    ):
        response = await client.get("/v2/court/actors/nonexistent_role")
        assert response.status_code == 404
        data = response.json()
        _assert_structured_error(data, "ROLE_NOT_FOUND")
        assert "nonexistent_role" in data["error"]["message"]


# ============================================================================
# 409 CONFLICT
# ============================================================================


@pytest.mark.asyncio
async def test_pm_chat_llm_not_ready_409(client: AsyncClient) -> None:
    """POST /v2/pm/chat when PM LLM not ready returns 409 via status endpoint."""
    # The pm_chat_status endpoint returns 409 when PM role is not configured.
    mock_config: dict[str, object] = {"roles": {}, "providers": {}}

    with (
        patch(
            "polaris.delivery.http.routers.pm_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.pm_chat.llm_config.load_llm_config",
            return_value=mock_config,
        ),
    ):
        response = await client.get("/v2/pm/chat/status")
        assert response.status_code == 409
        data = response.json()
        _assert_structured_error(data, "PM_ROLE_NOT_CONFIGURED")
        assert "not configured" in data["error"]["message"]


@pytest.mark.asyncio
async def test_docs_init_dialogue_architect_not_configured_409(client: AsyncClient) -> None:
    """POST /v2/docs/init/dialogue when architect not configured returns 409 ARCHITECT_NOT_CONFIGURED."""
    mock_config: dict[str, object] = {"roles": {}, "providers": {}}

    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value=mock_config,
    ):
        response = await client.post(
            "/v2/docs/init/dialogue",
            json={
                "message": "hello",
                "goal": "test",
                "in_scope": "",
                "out_of_scope": "",
                "constraints": "",
                "definition_of_done": "",
                "backlog": "",
            },
        )
        assert response.status_code == 409
        data = response.json()
        _assert_structured_error(data, "ARCHITECT_NOT_CONFIGURED")


# ============================================================================
# 500 INTERNAL_ERROR
# ============================================================================


@pytest.mark.asyncio
async def test_role_chat_generation_error_500(client: AsyncClient) -> None:
    """POST /v2/role/pm/chat when generation fails returns 500 GENERATION_FAILED."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.generate_role_response",
            new_callable=AsyncMock,
            side_effect=RuntimeError("model timeout"),
        ),
    ):
        response = await client.post("/v2/role/pm/chat", json={"message": "hello"})
        assert response.status_code == 500
        data = response.json()
        _assert_structured_error(data, "GENERATION_FAILED")
        assert "model timeout" in data["error"]["message"]


@pytest.mark.asyncio
async def test_provider_health_runtime_error_500(client: AsyncClient) -> None:
    """POST /v2/llm/providers/{type}/health when provider runtime fails returns 500 INTERNAL_ERROR."""
    from polaris.cells.llm.provider_runtime.public.contracts import LlmProviderRuntimeError

    with (
        patch(
            "polaris.delivery.http.routers.providers.resolve_provider_request_context",
        ) as mock_resolve,
    ):
        mock_ctx = MagicMock()
        mock_ctx.provider_cfg = {"type": "openai"}
        mock_ctx.provider_type = "openai"
        mock_ctx.api_key = "test-key"
        mock_resolve.return_value = mock_ctx

        with patch(
            "polaris.delivery.http.routers.providers.run_provider_action",
            side_effect=LlmProviderRuntimeError("provider runtime failure"),
        ):
            response = await client.post(
                "/v2/llm/providers/openai/health",
                json={"api_key": "test-key"},
            )
            assert response.status_code == 500
            data = response.json()
            _assert_structured_error(data, "INTERNAL_ERROR")


# ============================================================================
# Parameterized error path matrix
# ============================================================================


@pytest.mark.parametrize(
    (
        "method",
        "path",
        "payload",
        "mock_target",
        "mock_return",
        "expected_status",
        "expected_code",
        "expected_message_substring",
    ),
    [
        # 400 - empty message on role chat
        pytest.param(
            "POST",
            "/v2/role/pm/chat",
            {"message": ""},
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            ["pm"],
            400,
            "INVALID_REQUEST",
            "message is required",
            id="400_role_chat_empty_message",
        ),
        # 400 - unsupported role
        pytest.param(
            "POST",
            "/v2/role/unknown/chat",
            {"message": "hello"},
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            ["pm", "architect"],
            400,
            "UNSUPPORTED_ROLE",
            "unknown",
            id="400_role_chat_unsupported_role",
        ),
        # 404 - factory run not found
        pytest.param(
            "GET",
            "/v2/factory/runs/nonexistent",
            None,
            "polaris.delivery.http.routers.factory.FactoryRunService",
            None,
            404,
            "RUN_NOT_FOUND",
            "nonexistent",
            id="404_factory_run_not_found",
        ),
        # 404 - LLM test report not found
        pytest.param(
            "GET",
            "/v2/llm/test/nonexistent-run",
            None,
            "polaris.delivery.http.routers.tests.os.path.isfile",
            False,
            404,
            "REPORT_NOT_FOUND",
            "report not found",
            id="404_llm_test_report_not_found",
        ),
    ],
)
@pytest.mark.asyncio
async def test_error_path_matrix(
    client: AsyncClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    mock_target: str,
    mock_return: Any,
    expected_status: int,
    expected_code: str,
    expected_message_substring: str,
) -> None:
    """Parameterized matrix covering key error paths across v2 routers."""
    if "FactoryRunService" in mock_target:
        mock_svc = MagicMock()
        mock_svc.return_value.get_run = AsyncMock(return_value=None)
        with patch(mock_target, mock_svc):
            response = await client.request(method, path, json=payload)
            assert response.status_code == expected_status
            data = response.json()
            _assert_structured_error(data, expected_code)
            assert expected_message_substring in data["error"]["message"]
        return

    with patch(mock_target, return_value=mock_return):
        response = await client.request(method, path, json=payload)
        assert response.status_code == expected_status
        data = response.json()
        _assert_structured_error(data, expected_code)
        assert expected_message_substring in data["error"]["message"]
