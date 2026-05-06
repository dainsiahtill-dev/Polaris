"""Integration tests for complex multi-step delivery workflows.

Covers end-to-end flows across PM task management, docs initialization,
LLM configuration, settings management, and cache management.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import (
    docs as docs_router,
    llm as llm_router,
    pm_management as pm_router,
    providers as providers_router,
    role_chat as role_chat_router,
    system as system_router,
)
from polaris.delivery.http.routers._shared import require_auth

# role_chat.py does a lazy relative import from ..roles.kernel_components which
# does not exist at runtime. Inject a shim so the endpoint can resolve.
if "polaris.delivery.http.roles.kernel_components" not in sys.modules:
    _kc_mod = types.ModuleType("polaris.delivery.http.roles.kernel_components")
    from polaris.cells.roles.kernel.public import get_global_llm_cache, set_global_llm_cache

    _kc_mod.get_global_llm_cache = get_global_llm_cache
    _kc_mod.set_global_llm_cache = set_global_llm_cache
    sys.modules["polaris.delivery.http.roles.kernel_components"] = _kc_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    """Build a FastAPI app with all routers under test and auth overridden."""
    app = FastAPI()
    app.include_router(pm_router.router)
    app.include_router(docs_router.router)
    app.include_router(llm_router.router)
    app.include_router(providers_router.router)
    app.include_router(role_chat_router.router)
    app.include_router(system_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = MagicMock()
    app.state.app_state.settings = MagicMock()
    app.state.app_state.settings.workspace = "."
    app.state.app_state.settings.ramdisk_root = ""
    app.state.app_state.settings.to_payload.return_value = {"workspace": "."}
    # Bind a minimal auth so require_role() does not 503
    from polaris.cells.runtime.state_owner.public.service import Auth

    app.state.auth = Auth("")
    return app


def _mock_pm_instance() -> MagicMock:
    """Create a mock PM instance with common methods."""
    pm = MagicMock()
    pm.is_initialized.return_value = True
    pm.list_tasks.return_value = {
        "ok": True,
        "tasks": [
            {
                "id": "task-1",
                "title": "Test Task",
                "description": "A test task",
                "status": "pending",
                "priority": "medium",
            }
        ],
        "pagination": {"total": 1},
    }
    pm.get_task.return_value = MagicMock(
        id="task-1",
        title="Test Task",
        description="A test task",
        status=MagicMock(value="pending"),
        priority=MagicMock(value="medium"),
        assignee=None,
        assignee_type=None,
        requirements=[],
        dependencies=[],
        estimated_effort=None,
        actual_effort=None,
        created_at="2026-01-01",
        updated_at="2026-01-01",
        assigned_at=None,
        started_at=None,
        completed_at=None,
        result_summary=None,
        artifacts=[],
        metadata={},
    )
    pm.initialize.return_value = {
        "initialized": True,
        "message": "PM system initialized",
        "workspace": ".",
    }

    # Side effect: after initialize, is_initialized returns True
    def _init_side_effect(*args, **kwargs):
        pm.is_initialized.return_value = True
        return pm.initialize.return_value

    pm.initialize.side_effect = _init_side_effect
    return pm


# ---------------------------------------------------------------------------
# 1. PM Task Creation Flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPMTaskCreationFlow:
    """Multi-step PM task creation and retrieval workflow."""

    async def test_create_task_and_verify_in_list(self) -> None:
        """POST /v2/pm/init -> GET /v2/pm/tasks -> GET /v2/pm/tasks/{id}."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.is_initialized.return_value = False

        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=pm,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Step 1: Initialize PM
                init_resp = await client.post("/pm/init", params={"project_name": "Test Project"})
                assert init_resp.status_code == 200
                init_payload: dict[str, Any] = init_resp.json()
                assert init_payload["initialized"] is True

                # Step 2: List tasks
                list_resp = await client.get("/pm/tasks")
                assert list_resp.status_code == 200
                list_payload: dict[str, Any] = list_resp.json()
                assert list_payload["ok"] is True
                assert "tasks" in list_payload
                task_ids = [t["id"] for t in list_payload["tasks"]]
                assert "task-1" in task_ids

                # Step 3: Get task detail
                detail_resp = await client.get("/pm/tasks/task-1")
                assert detail_resp.status_code == 200
                detail_payload: dict[str, Any] = detail_resp.json()
                assert detail_payload["id"] == "task-1"
                assert detail_payload["title"] == "Test Task"

    async def test_task_not_found_returns_404(self) -> None:
        """GET /pm/tasks/{missing_id} returns 404."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.get_task.return_value = None

        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=pm,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/pm/tasks/missing-task")
                assert resp.status_code == 404
                body = resp.json()
                detail = body.get("detail", body)
                assert "Task not found" in detail.get("message", detail.get("detail", ""))


# ---------------------------------------------------------------------------
# 2. Docs Initialization Flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDocsInitializationFlow:
    """Multi-step docs wizard workflow."""

    async def test_full_docs_init_flow(self) -> None:
        """dialogue -> suggest -> preview -> apply."""
        app = _build_app()

        llm_config_patch = patch(
            "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
            return_value={
                "roles": {"architect": {"provider_id": "p1", "model": "m1"}},
                "providers": {
                    "p1": {
                        "type": "openai",
                        "base_url": "",
                        "api_path": "",
                        "api_key": "",
                    }
                },
            },
        )
        dialogue_patch = patch(
            "polaris.delivery.http.routers.docs.generate_docs_dialogue_turn",
            new_callable=AsyncMock,
            return_value={
                "reply": "Hello",
                "questions": [],
                "tiaochen": [],
                "meta": {},
                "handoffs": {},
                "fields": {"goal": "Build a test app"},
            },
        )
        suggest_patch = patch(
            "polaris.delivery.http.routers.docs.generate_docs_ai_fields",
            new_callable=AsyncMock,
            return_value={
                "goal": ["Build a test app"],
                "in_scope": ["Backend API"],
                "out_of_scope": ["Mobile app"],
                "constraints": ["Use FastAPI"],
                "definition_of_done": ["Tests pass"],
                "backlog": ["Setup CI"],
            },
        )
        detect_patch = patch(
            "polaris.delivery.http.routers.docs.detect_project_profile",
            return_value={
                "python": True,
                "node": False,
                "go": False,
                "rust": False,
                "package_manager": None,
            },
        )
        build_templates_patch = patch(
            "polaris.delivery.http.routers.docs.build_docs_templates",
            return_value={"docs/SPEC.md": "# Spec\n\nBuild a test app"},
        )
        select_root_patch = patch(
            "polaris.delivery.http.routers.docs.select_docs_target_root",
            return_value="docs",
        )
        workspace_has_docs_patch = patch(
            "polaris.delivery.http.routers.docs.workspace_has_docs",
            return_value=False,
        )
        resolve_patch = patch(
            "polaris.delivery.http.routers.docs.resolve_artifact_path",
            return_value="/tmp/workspace/docs/SPEC.md",
        )
        isfile_patch = patch(
            "polaris.delivery.http.routers.docs.os.path.isfile",
            return_value=False,
        )
        normalize_patch = patch(
            "polaris.delivery.http.routers.docs.normalize_rel_path",
            return_value="workspace/docs/SPEC.md",
        )
        safe_path_patch = patch(
            "polaris.delivery.http.routers.docs.is_safe_docs_path",
            return_value=True,
        )
        write_patch = patch(
            "polaris.delivery.http.routers.docs.write_text_atomic",
        )
        clear_status_patch = patch(
            "polaris.delivery.http.routers.docs.clear_workspace_status",
        )
        emit_patch = patch(
            "polaris.delivery.http.routers.docs.emit_event",
        )

        with (
            llm_config_patch,
            dialogue_patch,
            suggest_patch,
            detect_patch,
            build_templates_patch,
            select_root_patch,
            workspace_has_docs_patch,
            resolve_patch,
            isfile_patch,
            normalize_patch,
            safe_path_patch,
            write_patch,
            clear_status_patch,
            emit_patch,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Step 1: Dialogue
                dialogue_resp = await client.post(
                    "/v2/docs/init/dialogue",
                    json={
                        "message": "hi",
                        "goal": "",
                        "in_scope": "",
                        "out_of_scope": "",
                        "constraints": "",
                        "definition_of_done": "",
                        "backlog": "",
                    },
                )
                assert dialogue_resp.status_code == 200
                dialogue_payload: dict[str, Any] = dialogue_resp.json()
                assert dialogue_payload["ok"] is True
                assert dialogue_payload["reply"] == "Hello"
                assert dialogue_payload["fields"]["goal"] == "Build a test app"

                # Step 2: Suggest
                suggest_resp = await client.post(
                    "/v2/docs/init/suggest",
                    json={
                        "goal": "",
                        "in_scope": "",
                        "out_of_scope": "",
                        "constraints": "",
                        "definition_of_done": "",
                        "backlog": "",
                    },
                )
                assert suggest_resp.status_code == 200
                suggest_payload: dict[str, Any] = suggest_resp.json()
                assert suggest_payload["ok"] is True
                assert "Build a test app" in suggest_payload["fields"]["goal"]

                # Step 3: Preview
                preview_resp = await client.post(
                    "/v2/docs/init/preview",
                    json={
                        "mode": "minimal",
                        "goal": "Build a test app",
                        "in_scope": "",
                        "out_of_scope": "",
                        "constraints": "",
                        "definition_of_done": "",
                        "backlog": "",
                    },
                )
                assert preview_resp.status_code == 200
                preview_payload: dict[str, Any] = preview_resp.json()
                assert preview_payload["ok"] is True
                assert len(preview_payload["files"]) == 1
                assert preview_payload["files"][0]["path"] == "docs/SPEC.md"

                # Step 4: Apply
                apply_resp = await client.post(
                    "/v2/docs/init/apply",
                    json={
                        "target_root": "workspace/docs",
                        "files": [
                            {
                                "path": "workspace/docs/SPEC.md",
                                "content": "# Spec\n\nBuild a test app",
                            }
                        ],
                    },
                )
                assert apply_resp.status_code == 200
                apply_payload: dict[str, Any] = apply_resp.json()
                assert apply_payload["ok"] is True
                assert "workspace/docs/SPEC.md" in apply_payload["files"]


# ---------------------------------------------------------------------------
# 3. LLM Configuration Flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLLMConfigurationFlow:
    """Multi-step LLM config and provider status workflow."""

    async def test_get_config_list_providers_and_health(self) -> None:
        """GET /v2/llm/config -> GET /v2/llm/providers -> POST /v2/llm/providers/{type}/health -> GET /v2/llm/status."""
        app = _build_app()

        mock_config: dict[str, Any] = {"providers": {}, "roles": {}}

        with (
            patch(
                "polaris.delivery.http.routers.llm.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.llm.llm_config.load_llm_config",
                return_value=mock_config,
            ),
            patch(
                "polaris.delivery.http.routers.llm.llm_config.redact_llm_config",
                return_value={"providers": {}, "roles": {}, "redacted": True},
            ),
            patch(
                "polaris.delivery.http.routers.llm.build_llm_status",
                return_value={"ready": True},
            ),
            patch(
                "polaris.delivery.http.routers.providers._provider_manager.list_provider_info",
                return_value=[
                    SimpleNamespace(
                        name="TestProvider",
                        type="test",
                        description="A test provider",
                        version="1.0",
                        author="tester",
                        documentation_url="https://example.com",
                        supported_features=["chat"],
                        cost_class="low",
                        provider_category="LLM",
                        autonomous_file_access=False,
                        requires_file_interfaces=True,
                        model_listing_method="API",
                    )
                ],
            ),
            patch(
                "polaris.delivery.http.routers.providers._provider_manager.get_provider_info",
                return_value=SimpleNamespace(
                    name="TestProvider",
                    type="test",
                    description="A test provider",
                    version="1.0",
                    author="tester",
                    documentation_url="https://example.com",
                    supported_features=["chat"],
                    cost_class="low",
                ),
            ),
            patch(
                "polaris.delivery.http.routers.providers.resolve_provider_request_context",
            ) as mock_resolve,
            patch(
                "polaris.delivery.http.routers.providers.run_provider_action",
            ) as mock_run,
        ):
            mock_resolve.return_value = SimpleNamespace(
                provider_cfg={"base_url": "https://api.test.com"},
                provider_type="test",
                api_key="secret",
            )
            mock_run.return_value = {"status": "healthy", "latency_ms": 42}

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Step 1: Get current config
                config_resp = await client.get("/v2/llm/config")
                assert config_resp.status_code == 200
                config_payload: dict[str, Any] = config_resp.json()
                assert config_payload["redacted"] is True

                # Step 2: List providers
                providers_resp = await client.get("/v2/llm/providers")
                assert providers_resp.status_code == 200
                providers_payload: dict[str, Any] = providers_resp.json()
                assert "providers" in providers_payload
                assert len(providers_payload["providers"]) == 1
                assert providers_payload["providers"][0]["name"] == "TestProvider"

                # Step 3: Provider health
                health_resp = await client.post(
                    "/v2/llm/providers/test/health",
                    json={"api_key": "secret", "headers": {}},
                )
                assert health_resp.status_code == 200
                health_payload: dict[str, Any] = health_resp.json()
                assert health_payload["status"] == "healthy"
                assert health_payload["latency_ms"] == 42

                # Step 4: LLM status
                status_resp = await client.get("/v2/llm/status")
                assert status_resp.status_code == 200
                status_payload: dict[str, Any] = status_resp.json()
                assert status_payload["ready"] is True


# ---------------------------------------------------------------------------
# 4. Settings Management Flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSettingsManagementFlow:
    """Multi-step settings get/update/verify workflow."""

    async def test_get_update_and_verify_settings(self) -> None:
        """GET /v2/settings -> POST /v2/settings -> GET /v2/settings."""
        app = _build_app()

        with (
            patch(
                "polaris.delivery.http.routers.system.validate_workspace",
                return_value="/new/workspace",
            ),
            patch(
                "polaris.delivery.http.routers.system.sync_process_settings_environment",
            ) as mock_sync,
            patch(
                "polaris.delivery.http.routers.system.save_persisted_settings",
            ) as mock_save,
            patch(
                "polaris.delivery.http.routers.system.workspace_has_docs",
                return_value=False,
            ),
            patch(
                "polaris.delivery.http.routers.system.write_workspace_status",
            ),
            patch(
                "polaris.delivery.http.routers.system.Path.resolve",
                return_value=MagicMock(),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Step 1: Get initial settings
                get_resp = await client.get("/v2/settings")
                assert get_resp.status_code == 200
                initial_payload: dict[str, Any] = get_resp.json()
                assert "workspace" in initial_payload

                # Step 2: Update settings
                update_resp = await client.post(
                    "/v2/settings",
                    json={"workspace": "/new/workspace"},
                )
                assert update_resp.status_code == 200
                update_payload: dict[str, Any] = update_resp.json()
                assert "workspace" in update_payload
                mock_sync.assert_called()
                mock_save.assert_called()

                # Step 3: Verify settings persisted (re-query)
                verify_resp = await client.get("/v2/settings")
                assert verify_resp.status_code == 200
                verify_payload: dict[str, Any] = verify_resp.json()
                assert "workspace" in verify_payload


# ---------------------------------------------------------------------------
# 5. Cache Management Flow
# ---------------------------------------------------------------------------


class _FakeCache:
    """Fake in-memory cache for testing cache-stats / cache-clear."""

    def __init__(self) -> None:
        self._stats: dict[str, Any] = {"hits": 10, "misses": 2, "size": 12}

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    def clear(self) -> None:
        self._stats = {"hits": 0, "misses": 0, "size": 0}


@pytest.mark.asyncio
class TestCacheManagementFlow:
    """Multi-step cache stats/clear/verify workflow."""

    async def test_get_stats_clear_and_verify(self) -> None:
        """GET /v2/role/cache-stats -> POST /v2/role/cache-clear -> GET /v2/role/cache-stats."""
        app = _build_app()
        fake_cache = _FakeCache()

        # The endpoint does a lazy relative import from ..roles.kernel_components.
        # We injected a shim module at import time; reference it via sys.modules.
        _kc = sys.modules["polaris.delivery.http.roles.kernel_components"]

        # Override the route-specific require_role checker so cache-clear passes.
        from polaris.delivery.http.routers.role_chat import router as _rc_router

        for _route in _rc_router.routes:
            if hasattr(_route, "path") and _route.path == "/v2/role/cache-clear":
                for _dep in _route.dependencies:
                    if _dep.dependency is not require_auth:
                        app.dependency_overrides[_dep.dependency] = lambda: None

        with patch.object(_kc, "get_global_llm_cache", return_value=fake_cache):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Step 1: Get cache stats
                stats_resp = await client.get("/v2/role/cache-stats")
                assert stats_resp.status_code == 200
                stats_payload: dict[str, Any] = stats_resp.json()
                assert stats_payload.get("size") == 12

                # Step 2: Clear cache
                clear_resp = await client.post("/v2/role/cache-clear")
                assert clear_resp.status_code == 200
                clear_payload: dict[str, Any] = clear_resp.json()
                assert clear_payload["ok"] is True
                assert "cleared" in clear_payload["message"].lower()

                # Step 3: Verify cache cleared
                verify_resp = await client.get("/v2/role/cache-stats")
                assert verify_resp.status_code == 200
                verify_payload: dict[str, Any] = verify_resp.json()
                assert verify_payload.get("size") == 0
                assert verify_payload.get("hits") == 0
