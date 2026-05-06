"""Tests for Polaris PM management v2 routes in pm_management.py.

Covers v2 aliases: /pm/v2/pm/documents, /pm/v2/pm/tasks,
/pm/v2/pm/requirements, /pm/v2/pm/status, /pm/v2/pm/health,
/pm/v2/pm/init.
External PM service is mocked to avoid DI container and storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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


def _mock_pm_adapter(
    *,
    initialized: bool = True,
    list_documents_result: dict | None = None,
    get_document_result: dict | None = None,
    get_document_content_result: str | None = None,
    create_or_update_document_result: object | None = None,
    delete_document_result: bool = True,
    get_document_versions_result: list | None = None,
    compare_document_versions_result: object | None = None,
    search_documents_result: list | None = None,
    list_tasks_result: dict | None = None,
    get_task_history_result: dict | None = None,
    get_director_task_history_result: dict | None = None,
    get_task_result: object | None = None,
    get_task_assignments_result: list | None = None,
    search_tasks_result: list | None = None,
    list_requirements_result: dict | None = None,
    get_requirement_result: dict | None = None,
    get_status_result: dict | None = None,
    analyze_project_health_result: dict | None = None,
    initialize_result: dict | None = None,
) -> MagicMock:
    """Build a mocked ScriptsPMAdapter with common defaults."""
    mock_pm = MagicMock()
    mock_pm.is_initialized.return_value = initialized
    mock_pm.list_documents.return_value = list_documents_result or {
        "documents": [],
        "pagination": {"total": 0, "limit": 100, "offset": 0},
    }
    mock_pm.get_document.return_value = get_document_result
    mock_pm.get_document_content.return_value = get_document_content_result
    mock_pm.create_or_update_document.return_value = create_or_update_document_result
    mock_pm.delete_document.return_value = delete_document_result
    mock_pm.get_document_versions.return_value = get_document_versions_result or []
    mock_pm.compare_document_versions.return_value = compare_document_versions_result
    mock_pm.search_documents.return_value = search_documents_result or []
    mock_pm.list_tasks.return_value = list_tasks_result or {
        "tasks": [],
        "pagination": {"total": 0, "limit": 100, "offset": 0},
    }
    mock_pm.get_task_history.return_value = get_task_history_result or {
        "history": [],
        "pagination": {"total": 0, "limit": 100, "offset": 0},
    }
    mock_pm.get_director_task_history.return_value = get_director_task_history_result or {
        "iterations": [],
        "pagination": {"total": 0, "limit": 50, "offset": 0},
    }
    mock_pm.get_task.return_value = get_task_result
    mock_pm.get_task_assignments.return_value = get_task_assignments_result or []
    mock_pm.search_tasks.return_value = search_tasks_result or []
    mock_pm.list_requirements.return_value = list_requirements_result or {
        "requirements": [],
        "pagination": {"total": 0, "limit": 100, "offset": 0},
    }
    mock_pm.get_requirement.return_value = get_requirement_result
    mock_pm.get_status.return_value = get_status_result or {
        "initialized": True,
        "workspace": ".",
    }
    mock_pm.analyze_project_health.return_value = analyze_project_health_result or {
        "overall": "healthy",
        "components": {},
        "metrics": {},
        "recommendations": [],
    }
    mock_pm.initialize.return_value = initialize_result or {
        "initialized": True,
        "workspace": ".",
        "project_name": "Test",
    }
    return mock_pm


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_list_documents(client: AsyncClient) -> None:
    """GET /pm/v2/pm/documents should return document list."""
    mock_pm = _mock_pm_adapter(
        list_documents_result={
            "documents": [
                {
                    "path": "docs/readme.md",
                    "current_version": "1",
                    "version_count": 1,
                    "last_modified": "2026-01-01T00:00:00Z",
                    "created_at": "2026-01-01T00:00:00Z",
                },
            ],
            "pagination": {"total": 1, "limit": 100, "offset": 0},
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/documents")
        assert response.status_code == 200
        data = response.json()
        assert len(data["documents"]) == 1
        assert data["documents"][0]["path"] == "docs/readme.md"
        mock_pm.list_documents.assert_called_once_with(doc_type=None, pattern=None, limit=100, offset=0)


@pytest.mark.asyncio
async def test_v2_list_documents_with_filters(client: AsyncClient) -> None:
    """GET /pm/v2/pm/documents should pass query params to PM adapter."""
    mock_pm = _mock_pm_adapter()

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/documents?doc_type=md&pattern=*.md&limit=10&offset=5")
        assert response.status_code == 200
        mock_pm.list_documents.assert_called_once_with(doc_type="md", pattern="*.md", limit=10, offset=5)


@pytest.mark.asyncio
async def test_v2_get_document(client: AsyncClient) -> None:
    """GET /pm/v2/pm/documents/{path} should return document details."""
    mock_pm = _mock_pm_adapter(
        get_document_result={
            "path": "docs/readme.md",
            "current_version": "2",
            "version_count": 2,
            "last_modified": "2026-01-02T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
        },
        get_document_content_result="# Hello",
    )

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.get("/pm/v2/pm/documents/docs/readme.md")
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "docs/readme.md"
        assert data["content"] == "# Hello"
        mock_pm.get_document.assert_called_once_with("/workspace/docs/readme.md")
        mock_pm.get_document_content.assert_called_once_with("/workspace/docs/readme.md", None)


@pytest.mark.asyncio
async def test_v2_get_document_with_version(client: AsyncClient) -> None:
    """GET /pm/v2/pm/documents/{path} should accept version query."""
    mock_pm = _mock_pm_adapter(
        get_document_result={
            "path": "docs/readme.md",
            "current_version": "2",
            "version_count": 2,
            "last_modified": "2026-01-02T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
        },
        get_document_content_result="v1 content",
    )

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.get("/pm/v2/pm/documents/docs/readme.md?version=1")
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "docs/readme.md"
        assert data["content"] == "v1 content"
        mock_pm.get_document_content.assert_called_once_with("/workspace/docs/readme.md", "1")


@pytest.mark.asyncio
async def test_v2_get_document_not_found(client: AsyncClient) -> None:
    """GET /pm/v2/pm/documents/{path} should 404 when document missing."""
    mock_pm = _mock_pm_adapter(get_document_result=None)

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/missing.md",
        ),
    ):
        response = await client.get("/pm/v2/pm/documents/docs/missing.md")
        assert response.status_code == 404
        assert "DOCUMENT_NOT_FOUND" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_v2_create_or_update_document(client: AsyncClient) -> None:
    """POST /pm/v2/pm/documents/{path} should create or update document."""

    @dataclass
    class _FakeVersion:
        version: str = "3"
        checksum: str = "abc123"

    mock_pm = _mock_pm_adapter(
        create_or_update_document_result=_FakeVersion(),
    )

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.post(
            "/pm/v2/pm/documents/docs/readme.md",
            json={"content": "# Updated", "change_summary": "update test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["version"] == "3"
        assert data["checksum"] == "abc123"
        mock_pm.create_or_update_document.assert_called_once_with(
            doc_path="/workspace/docs/readme.md",
            content="# Updated",
            updated_by="api",
            change_summary="update test",
        )


@pytest.mark.asyncio
async def test_v2_create_or_update_document_failed(client: AsyncClient) -> None:
    """POST /pm/v2/pm/documents/{path} should 500 when PM operation fails."""
    mock_pm = _mock_pm_adapter(create_or_update_document_result=None)

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.post(
            "/pm/v2/pm/documents/docs/readme.md",
            json={"content": "# Updated"},
        )
        assert response.status_code == 500
        assert "PM_OPERATION_FAILED" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_v2_delete_document(client: AsyncClient) -> None:
    """DELETE /pm/v2/pm/documents/{path} should delete document."""
    mock_pm = _mock_pm_adapter(delete_document_result=True)

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.delete("/pm/v2/pm/documents/docs/readme.md")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted"] is True
        mock_pm.delete_document.assert_called_once_with("/workspace/docs/readme.md", delete_file=True)


@pytest.mark.asyncio
async def test_v2_delete_document_without_file_delete(client: AsyncClient) -> None:
    """DELETE /pm/v2/pm/documents/{path} should respect delete_file flag."""
    mock_pm = _mock_pm_adapter(delete_document_result=True)

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.delete("/pm/v2/pm/documents/docs/readme.md?delete_file=false")
        assert response.status_code == 200
        mock_pm.delete_document.assert_called_once_with("/workspace/docs/readme.md", delete_file=False)


@pytest.mark.asyncio
async def test_v2_delete_document_failed(client: AsyncClient) -> None:
    """DELETE /pm/v2/pm/documents/{path} should 500 when deletion fails."""
    mock_pm = _mock_pm_adapter(delete_document_result=False)

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.delete("/pm/v2/pm/documents/docs/readme.md")
        assert response.status_code == 500
        assert "PM_OPERATION_FAILED" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_v2_get_document_versions(client: AsyncClient) -> None:
    """GET /pm/v2/pm/documents/{path}/versions should return versions."""

    @dataclass
    class _FakeVersion:
        version: str = "1"
        created_at: str = "2026-01-01T00:00:00Z"
        created_by: str = "user"
        change_summary: str = "init"
        checksum: str = "chk1"

    mock_pm = _mock_pm_adapter(
        get_document_versions_result=[_FakeVersion()],
    )

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.get("/pm/v2/pm/documents/docs/readme.md/versions")
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "/workspace/docs/readme.md"
        assert len(data["versions"]) == 1
        assert data["versions"][0]["version"] == "1"
        mock_pm.get_document_versions.assert_called_once_with("/workspace/docs/readme.md")


@pytest.mark.asyncio
async def test_v2_compare_document_versions(client: AsyncClient) -> None:
    """GET /pm/v2/pm/documents/{path}/compare should return diff."""

    @dataclass
    class _FakeDiff:
        old_version: str = "1"
        new_version: str = "2"
        diff_text: str = "-old\n+new"
        changed_sections: list[str] = None
        added_requirements: list[str] = None
        removed_requirements: list[str] = None
        impact_score: float = 0.5

    diff = _FakeDiff()
    diff.changed_sections = ["section1"]
    diff.added_requirements = ["req1"]
    diff.removed_requirements = []

    mock_pm = _mock_pm_adapter(compare_document_versions_result=diff)

    with (
        patch(
            "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
            return_value=mock_pm,
        ),
        patch(
            "polaris.delivery.http.routers.pm_management.resolve_safe_path",
            return_value="/workspace/docs/readme.md",
        ),
    ):
        response = await client.get("/pm/v2/pm/documents/docs/readme.md/compare?old_version=1&new_version=2")
        assert response.status_code == 200
        data = response.json()
        assert data["old_version"] == "1"
        assert data["new_version"] == "2"
        assert data["diff_text"] == "-old\n+new"
        mock_pm.compare_document_versions.assert_called_once_with("/workspace/docs/readme.md", "1", "2")


@pytest.mark.asyncio
async def test_v2_search_documents(client: AsyncClient) -> None:
    """GET /pm/v2/pm/search/documents should search documents."""
    mock_pm = _mock_pm_adapter(
        search_documents_result=[{"path": "docs/readme.md", "snippet": "hello"}],
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/search/documents?q=hello&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "hello"
        assert data["count"] == 1
        mock_pm.search_documents.assert_called_once_with(query="hello", limit=10)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_list_tasks(client: AsyncClient) -> None:
    """GET /pm/v2/pm/tasks should return task list."""
    mock_pm = _mock_pm_adapter(
        list_tasks_result={
            "tasks": [
                {
                    "id": "task-1",
                    "title": "Task 1",
                    "status": "pending",
                    "priority": "high",
                },
            ],
            "pagination": {"total": 1, "limit": 100, "offset": 0},
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/tasks")
        assert response.status_code == 200
        data = response.json()
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["id"] == "task-1"
        mock_pm.list_tasks.assert_called_once_with(status=None, assignee=None, limit=100, offset=0)


@pytest.mark.asyncio
async def test_v2_list_tasks_with_filters(client: AsyncClient) -> None:
    """GET /pm/v2/pm/tasks should pass filters to PM adapter."""
    mock_pm = _mock_pm_adapter()

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/tasks?status=pending&assignee=user1&limit=20&offset=10")
        assert response.status_code == 200
        mock_pm.list_tasks.assert_called_once_with(status="pending", assignee="user1", limit=20, offset=10)


@pytest.mark.asyncio
async def test_v2_get_task_history(client: AsyncClient) -> None:
    """GET /pm/v2/pm/tasks/history should return task history."""
    mock_pm = _mock_pm_adapter(
        get_task_history_result={
            "history": [{"id": "task-1", "action": "created"}],
            "pagination": {"total": 1, "limit": 100, "offset": 0},
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get(
            "/pm/v2/pm/tasks/history?task_id=task-1&assignee=user1&status=done"
            "&start_date=2026-01-01&end_date=2026-12-31&limit=50&offset=10"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["history"]) == 1
        mock_pm.get_task_history.assert_called_once_with(
            task_id="task-1",
            assignee="user1",
            status="done",
            start_date="2026-01-01",
            end_date="2026-12-31",
            limit=50,
            offset=10,
        )


@pytest.mark.asyncio
async def test_v2_get_director_task_history(client: AsyncClient) -> None:
    """GET /pm/v2/pm/tasks/director should return director task history."""
    mock_pm = _mock_pm_adapter(
        get_director_task_history_result={
            "iterations": [{"iteration": 1, "tasks": []}],
            "pagination": {"total": 1, "limit": 50, "offset": 0},
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/tasks/director?iteration=1&limit=25&offset=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["iterations"]) == 1
        mock_pm.get_director_task_history.assert_called_once_with(iteration=1, limit=25, offset=5)


@pytest.mark.asyncio
async def test_v2_get_task(client: AsyncClient) -> None:
    """GET /pm/v2/pm/tasks/{task_id} should return task details."""

    @dataclass
    class _FakeTask:
        id: str = "task-1"
        title: str = "Task 1"
        description: str = "Desc"
        status: object = None
        priority: object = None
        assignee: str | None = "user1"
        assignee_type: object = None
        requirements: list[str] = field(default_factory=list)
        dependencies: list[str] = field(default_factory=list)
        estimated_effort: int = 1
        actual_effort: int = 0
        created_at: str = "2026-01-01T00:00:00Z"
        updated_at: str = "2026-01-01T00:00:00Z"
        assigned_at: str | None = None
        started_at: str | None = None
        completed_at: str | None = None
        result_summary: str | None = None
        artifacts: list[str] = field(default_factory=list)
        metadata: dict[str, Any] = field(default_factory=dict)

    task = _FakeTask()
    task.status = MagicMock()
    task.status.value = "pending"
    task.priority = MagicMock()
    task.priority.value = "high"
    task.assignee_type = MagicMock()
    task.assignee_type.value = "human"

    mock_pm = _mock_pm_adapter(get_task_result=task)

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/tasks/task-1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "task-1"
        assert data["status"] == "pending"
        assert data["priority"] == "high"
        mock_pm.get_task.assert_called_once_with("task-1")


@pytest.mark.asyncio
async def test_v2_get_task_not_found(client: AsyncClient) -> None:
    """GET /pm/v2/pm/tasks/{task_id} should 404 when task missing."""
    mock_pm = _mock_pm_adapter(get_task_result=None)

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/tasks/missing")
        assert response.status_code == 404
        assert "TASK_NOT_FOUND" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_v2_get_task_assignments(client: AsyncClient) -> None:
    """GET /pm/v2/pm/tasks/{task_id}/assignments should return assignments."""
    mock_pm = _mock_pm_adapter(
        get_task_assignments_result=[{"assignee": "user1", "at": "2026-01-01T00:00:00Z"}],
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/tasks/task-1/assignments?limit=50")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-1"
        assert data["count"] == 1
        mock_pm.get_task_assignments.assert_called_once_with(task_id="task-1", limit=50)


@pytest.mark.asyncio
async def test_v2_search_tasks(client: AsyncClient) -> None:
    """GET /pm/v2/pm/search/tasks should search tasks."""
    mock_pm = _mock_pm_adapter(
        search_tasks_result=[{"id": "task-1", "title": "Find me"}],
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/search/tasks?q=find&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "find"
        assert data["count"] == 1
        mock_pm.search_tasks.assert_called_once_with(query="find", limit=10)


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_list_requirements(client: AsyncClient) -> None:
    """GET /pm/v2/pm/requirements should return requirements list."""
    mock_pm = _mock_pm_adapter(
        list_requirements_result={
            "requirements": [
                {
                    "id": "req-1",
                    "title": "Req 1",
                    "status": "open",
                    "priority": "high",
                },
            ],
            "pagination": {"total": 1, "limit": 100, "offset": 0},
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/requirements")
        assert response.status_code == 200
        data = response.json()
        assert len(data["requirements"]) == 1
        assert data["requirements"][0]["id"] == "req-1"
        mock_pm.list_requirements.assert_called_once_with(status=None, priority=None, limit=100, offset=0)


@pytest.mark.asyncio
async def test_v2_list_requirements_with_filters(client: AsyncClient) -> None:
    """GET /pm/v2/pm/requirements should pass filters."""
    mock_pm = _mock_pm_adapter()

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/requirements?status=open&priority=high&limit=20&offset=5")
        assert response.status_code == 200
        mock_pm.list_requirements.assert_called_once_with(status="open", priority="high", limit=20, offset=5)


@pytest.mark.asyncio
async def test_v2_get_requirement(client: AsyncClient) -> None:
    """GET /pm/v2/pm/requirements/{req_id} should return requirement."""
    mock_pm = _mock_pm_adapter(
        get_requirement_result={
            "id": "req-1",
            "title": "Req 1",
            "status": "open",
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/requirements/req-1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "req-1"
        mock_pm.get_requirement.assert_called_once_with("req-1")


@pytest.mark.asyncio
async def test_v2_get_requirement_not_found(client: AsyncClient) -> None:
    """GET /pm/v2/pm/requirements/{req_id} should 404 when missing."""
    mock_pm = _mock_pm_adapter(get_requirement_result=None)

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/requirements/missing")
        assert response.status_code == 404
        assert "REQUIREMENT_NOT_FOUND" in response.json()["error"]["code"]


# ---------------------------------------------------------------------------
# Status / Health / Init
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_get_pm_status_initialized(client: AsyncClient) -> None:
    """GET /pm/v2/pm/status should return status when initialized."""
    mock_pm = _mock_pm_adapter(
        get_status_result={
            "initialized": True,
            "workspace": ".",
            "project": "Test",
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/status")
        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is True
        assert data["project"] == "Test"


@pytest.mark.asyncio
async def test_v2_get_pm_status_not_initialized(client: AsyncClient) -> None:
    """GET /pm/v2/pm/status should return minimal status when not initialized."""
    mock_pm = _mock_pm_adapter(initialized=False)

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/status")
        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is False
        assert data["workspace"] == "."


@pytest.mark.asyncio
async def test_v2_get_pm_health(client: AsyncClient) -> None:
    """GET /pm/v2/pm/health should return health analysis."""
    mock_pm = _mock_pm_adapter(
        analyze_project_health_result={
            "overall": "healthy",
            "components": {"docs": "ok"},
            "metrics": {"coverage": 0.9},
            "recommendations": [],
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/health")
        assert response.status_code == 200
        data = response.json()
        assert data["overall"] == "healthy"
        mock_pm.analyze_project_health.assert_called_once()


@pytest.mark.asyncio
async def test_v2_init_pm(client: AsyncClient) -> None:
    """POST /pm/v2/pm/init should initialize PM system."""
    mock_pm = _mock_pm_adapter(
        initialized=False,
        initialize_result={
            "initialized": True,
            "workspace": ".",
            "project_name": "My Project",
        },
    )

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.post("/pm/v2/pm/init?project_name=My%20Project&description=A%20test%20project")
        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is True
        assert data["project_name"] == "My Project"
        mock_pm.initialize.assert_called_once_with(project_name="My Project", description="A test project")


@pytest.mark.asyncio
async def test_v2_init_pm_already_initialized(client: AsyncClient) -> None:
    """POST /pm/v2/pm/init should return already-initialized message."""
    mock_pm = _mock_pm_adapter(initialized=True)

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.post("/pm/v2/pm/init")
        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is True
        assert "already initialized" in data["message"]


# ---------------------------------------------------------------------------
# PM Not Initialized Guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_list_documents_not_initialized(client: AsyncClient) -> None:
    """GET /pm/v2/pm/documents should 400 when PM not initialized."""
    mock_pm = _mock_pm_adapter(initialized=False)

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/documents")
        assert response.status_code == 400
        assert "PM_NOT_INITIALIZED" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_v2_get_health_not_initialized(client: AsyncClient) -> None:
    """GET /pm/v2/pm/health should 400 when PM not initialized."""
    mock_pm = _mock_pm_adapter(initialized=False)

    with patch(
        "polaris.delivery.http.routers.pm_management.ScriptsPMAdapter",
        return_value=mock_pm,
    ):
        response = await client.get("/pm/v2/pm/health")
        assert response.status_code == 400
        assert "PM_NOT_INITIALIZED" in response.json()["error"]["code"]
