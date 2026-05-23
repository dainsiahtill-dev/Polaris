"""Contract tests for polaris.delivery.http.routers.pm_management module."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import pm_management as pm_management_router
from polaris.delivery.http.routers._shared import require_auth


def _build_app() -> FastAPI:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(pm_management_router.v2_router)
    app.include_router(pm_management_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = MagicMock()
    app.state.app_state.settings = MagicMock()
    app.state.app_state.settings.workspace = "."
    return app


def _mock_pm_instance() -> MagicMock:
    """Create a mock PM instance with common methods."""
    pm = MagicMock()
    pm.is_initialized.return_value = True
    pm.list_documents.return_value = {"documents": [], "pagination": {"total": 0}}
    pm.get_document.return_value = {
        "path": "test.md",
        "current_version": "1.0",
        "version_count": 1,
        "last_modified": "2026-01-01",
        "created_at": "2026-01-01",
        "content": "Test content",
    }
    pm.get_document_content.return_value = "Test content"
    pm.get_document_versions.return_value = []
    pm.compare_document_versions.return_value = {
        "old_version": "1.0",
        "new_version": "1.1",
        "diff_text": "diff",
        "changed_sections": [],
        "added_requirements": [],
        "removed_requirements": [],
        "impact_score": 0.0,
    }
    pm.search_documents.return_value = []
    pm.list_tasks.return_value = {"ok": True, "tasks": [], "pagination": {"total": 0}}
    pm.get_task.return_value = {
        "id": "task-1",
        "title": "Test Task",
        "description": "Test description",
        "status": "pending",
        "priority": "medium",
        "assignee": None,
        "assignee_type": None,
        "requirements": [],
        "dependencies": [],
        "estimated_effort": None,
        "actual_effort": None,
        "created_at": "2026-01-01",
        "updated_at": "2026-01-01",
        "assigned_at": None,
        "started_at": None,
        "completed_at": None,
        "result_summary": None,
        "artifacts": [],
        "metadata": {},
    }
    pm.get_task_history.return_value = {"tasks": [], "pagination": {"total": 0}}
    pm.get_director_task_history.return_value = {"tasks": [], "pagination": {"total": 0}}
    pm.get_task_assignments.return_value = []
    pm.search_tasks.return_value = []
    pm.list_requirements.return_value = {"ok": True, "requirements": [], "pagination": {"total": 0}}
    pm.get_requirement.return_value = {"id": "req-1", "title": "Test Req"}
    pm.get_status.return_value = {"initialized": True, "workspace": "."}
    pm.analyze_project_health.return_value = {
        "overall": "good",
        "components": {},
        "metrics": {},
        "recommendations": [],
    }
    pm.initialize.return_value = {"initialized": True, "workspace": ".", "message": "PM system initialized"}
    pm.create_or_update_document.return_value = MagicMock(version="1.0", checksum="abc123")
    pm.delete_document.return_value = True
    return pm


@pytest.mark.asyncio
class TestPMManagementRouter:
    """Contract tests for the PM management router."""

    async def test_list_documents_returns_200(self) -> None:
        """GET /pm/documents returns 200 with document list."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/documents")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "documents" in payload
        assert "pagination" in payload

    async def test_list_documents_returns_400_when_not_initialized(self) -> None:
        """GET /pm/documents returns 400 when PM not initialized."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.is_initialized.return_value = False
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=pm,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/documents")

        assert response.status_code == 400
        assert "PM system not initialized" in response.json()["error"]["message"]

    async def test_get_document_returns_200(self) -> None:
        """GET /pm/documents/{doc_path} returns 200 with document info."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.pm_management._get_pm_instance",
                return_value=_mock_pm_instance(),
            ),
            patch(
                "polaris.delivery.http.routers.pm_management._resolve_document_path",
                return_value="/test/path.md",
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/documents/test.md")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["path"] == "test.md"

    async def test_get_document_returns_404(self) -> None:
        """GET /pm/documents/{doc_path} returns 404 when not found."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.get_document.return_value = None
        with (
            patch(
                "polaris.delivery.http.routers.pm_management._get_pm_instance",
                return_value=pm,
            ),
            patch(
                "polaris.delivery.http.routers.pm_management._resolve_document_path",
                return_value="/test/missing.md",
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/documents/missing.md")

        assert response.status_code == 404
        assert "Document not found" in response.json()["error"]["message"]

    async def test_create_document_returns_200(self) -> None:
        """POST /pm/documents/{doc_path} returns 200 on success."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.pm_management._get_pm_instance",
                return_value=_mock_pm_instance(),
            ),
            patch(
                "polaris.delivery.http.routers.pm_management._resolve_document_path",
                return_value="/test/path.md",
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/pm/documents/test.md",
                    json={"content": "Test content", "change_summary": "Initial"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["success"] is True
        assert payload["version"] == "1.0"

    async def test_create_document_returns_422_without_content(self) -> None:
        """POST /pm/documents/{doc_path} returns 422 without required fields."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/pm/documents/test.md",
                json={},
            )

        assert response.status_code == 422

    async def test_delete_document_returns_200(self) -> None:
        """DELETE /pm/documents/{doc_path} returns 200 on success."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.pm_management._get_pm_instance",
                return_value=_mock_pm_instance(),
            ),
            patch(
                "polaris.delivery.http.routers.pm_management._resolve_document_path",
                return_value="/test/path.md",
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete("/pm/documents/test.md")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["success"] is True
        assert payload["deleted"] is True

    async def test_get_document_versions_returns_200(self) -> None:
        """GET /pm/documents/{doc_path}/versions returns 200."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.get_document_versions.return_value = [
            {
                "version": "1.0",
                "created_at": "2026-01-01",
                "created_by": "pm",
                "change_summary": "Initial",
                "checksum": "abc123",
            }
        ]
        with (
            patch(
                "polaris.delivery.http.routers.pm_management._get_pm_instance",
                return_value=pm,
            ),
            patch(
                "polaris.delivery.http.routers.pm_management._resolve_document_path",
                return_value="/test/path.md",
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/documents/test.md/versions")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["path"] == "/test/path.md"
        assert payload["versions"][0]["version"] == "1.0"
        pm.get_document_versions.assert_called_once_with("/test/path.md")

    async def test_compare_versions_returns_200(self) -> None:
        """GET /pm/documents/{doc_path}/compare returns 200."""
        app = _build_app()
        pm = _mock_pm_instance()
        with (
            patch(
                "polaris.delivery.http.routers.pm_management._get_pm_instance",
                return_value=pm,
            ),
            patch(
                "polaris.delivery.http.routers.pm_management._resolve_document_path",
                return_value="/test/path.md",
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/pm/documents/test.md/compare",
                    params={"old_version": "1.0", "new_version": "1.1"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["path"] == "/test/path.md"
        assert payload["old_version"] == "1.0"
        assert payload["new_version"] == "1.1"
        assert payload["diff_text"] == "diff"
        pm.compare_document_versions.assert_called_once_with("/test/path.md", "1.0", "1.1")

    async def test_compare_versions_returns_422_without_versions(self) -> None:
        """GET /pm/documents/{doc_path}/compare returns 422 without version params."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/documents/test.md/compare")

        assert response.status_code == 422

    async def test_search_documents_returns_200(self) -> None:
        """GET /pm/search/documents returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/pm/search/documents",
                    params={"q": "test"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["query"] == "test"

    async def test_search_documents_returns_422_without_query(self) -> None:
        """GET /pm/search/documents returns 422 without query param."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/pm/search/documents")

        assert response.status_code == 422

    async def test_list_tasks_returns_200(self) -> None:
        """GET /pm/tasks returns 200 with task list."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/tasks")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "tasks" in payload

    async def test_v2_list_tasks_uses_real_adapter_without_placeholder_import_error(self, tmp_path: Path) -> None:
        """GET /v2/pm/tasks uses the real adapter and returns an idle empty projection before PM starts."""
        app = _build_app()
        app.state.app_state.settings.workspace_path = str(tmp_path)
        app.state.app_state.settings.workspace = str(tmp_path)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v2/pm/tasks")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["tasks"] == []
        assert payload["items"] == []
        assert payload["total"] == 0
        assert payload["initialized"] is False
        assert payload["reason"] == "PM_NOT_INITIALIZED"

    async def test_v2_pm_desktop_list_projections_return_idle_empty_state(self, tmp_path: Path) -> None:
        """Desktop v2 PM list/history projections should not surface idle PM as transport errors."""
        app = _build_app()
        app.state.app_state.settings.workspace_path = str(tmp_path)
        app.state.app_state.settings.workspace = str(tmp_path)

        endpoints = [
            ("/v2/pm/documents", "documents"),
            ("/v2/pm/requirements", "requirements"),
            ("/v2/pm/tasks/history", "history"),
            ("/v2/pm/tasks/director", "iterations"),
        ]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path, collection_key in endpoints:
                response = await client.get(path)
                assert response.status_code == 200
                payload: dict[str, Any] = response.json()
                assert payload["ok"] is True
                assert payload[collection_key] == []
                assert payload["items"] == []
                assert payload["total"] == 0
                assert payload["initialized"] is False
                assert payload["reason"] == "PM_NOT_INITIALIZED"

            response = await client.get("/v2/pm/search/documents?q=plan")
            assert response.status_code == 200
            payload = response.json()
            assert payload["ok"] is True
            assert payload["query"] == "plan"
            assert payload["results"] == []
            assert payload["items"] == []
            assert payload["count"] == 0
            assert payload["total"] == 0
            assert payload["initialized"] is False
            assert payload["reason"] == "PM_NOT_INITIALIZED"

    async def test_get_task_returns_200(self) -> None:
        """GET /pm/tasks/{task_id} returns 200 with task info."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/tasks/task-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["id"] == "task-1"

    async def test_get_task_returns_404(self) -> None:
        """GET /pm/tasks/{task_id} returns 404 when not found."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.get_task.return_value = None
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=pm,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/tasks/missing-task")

        assert response.status_code == 404
        assert "Task not found" in response.json()["error"]["message"]

    async def test_get_task_history_returns_200(self) -> None:
        """GET /pm/tasks/history returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/tasks/history")

        assert response.status_code == 200

    async def test_get_director_task_history_returns_200(self) -> None:
        """GET /pm/tasks/director returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/tasks/director")

        assert response.status_code == 200

    async def test_get_task_assignments_returns_200(self) -> None:
        """GET /pm/tasks/{task_id}/assignments returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/tasks/task-1/assignments")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["task_id"] == "task-1"

    async def test_search_tasks_returns_200(self) -> None:
        """GET /pm/search/tasks returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/pm/search/tasks",
                    params={"q": "test"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["query"] == "test"

    async def test_search_tasks_returns_422_without_query(self) -> None:
        """GET /pm/search/tasks returns 422 without query param."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/pm/search/tasks")

        assert response.status_code == 422

    async def test_list_requirements_returns_200(self) -> None:
        """GET /pm/requirements returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/requirements")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "requirements" in payload

    async def test_get_requirement_returns_200(self) -> None:
        """GET /pm/requirements/{req_id} returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/requirements/req-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["id"] == "req-1"

    async def test_get_requirement_returns_404(self) -> None:
        """GET /pm/requirements/{req_id} returns 404 when not found."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.get_requirement.return_value = None
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=pm,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/requirements/missing-req")

        assert response.status_code == 404
        assert "Requirement not found" in response.json()["error"]["message"]

    async def test_get_pm_status_returns_200(self) -> None:
        """GET /pm/status returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["initialized"] is True

    async def test_get_pm_status_returns_not_initialized(self) -> None:
        """GET /pm/status returns not initialized status."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.is_initialized.return_value = False
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=pm,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["initialized"] is False

    async def test_get_pm_health_returns_200(self) -> None:
        """GET /pm/health returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/health")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "overall" in payload

    async def test_get_pm_health_returns_400_when_not_initialized(self) -> None:
        """GET /pm/health returns 400 when PM not initialized."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.is_initialized.return_value = False
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=pm,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pm/health")

        assert response.status_code == 400

    async def test_init_pm_returns_200(self) -> None:
        """POST /pm/init returns 200."""
        app = _build_app()
        pm = _mock_pm_instance()
        pm.is_initialized.return_value = False
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=pm,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/pm/init",
                    params={"project_name": "Test Project"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["initialized"] is True

    async def test_init_pm_returns_already_initialized(self) -> None:
        """POST /pm/init returns already initialized message."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_management._get_pm_instance",
            return_value=_mock_pm_instance(),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/pm/init")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["initialized"] is True
        assert "already initialized" in payload["message"]

    async def test_nonexistent_endpoint_returns_404(self) -> None:
        """GET /pm/nonexistent returns 404."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/pm/nonexistent")

        assert response.status_code == 404
