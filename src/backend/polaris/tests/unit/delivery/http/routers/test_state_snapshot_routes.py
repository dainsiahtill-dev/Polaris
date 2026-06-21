"""Non-mocked route tests for /state/snapshot and /v2/state/snapshot.

These tests verify that both endpoints return 200 with a stable payload
when using real temp workspace/runtime root directories, without mocking
``build_snapshot``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import system as system_router
from polaris.delivery.http.routers._shared import require_auth


@dataclass(frozen=True)
class _FakeWorkspaceCtx:
    """Minimal stand-in for WorkspaceRuntimeContext."""

    workspace: str
    runtime_root: str
    runtime_base: str = ""
    workspace_key: str = ""
    source: str = "test"
    configured_workspace: str = ""
    persisted_workspace: str = ""
    fallback_workspace: str = ""


def _build_app(workspace: str, runtime_root: str) -> tuple[FastAPI, MagicMock]:
    """Build a FastAPI app with system router and temp directories."""
    app = FastAPI()
    app.include_router(system_router.router)
    app.dependency_overrides[require_auth] = lambda: None

    mock_state = MagicMock()
    mock_state.settings = MagicMock()
    mock_state.settings.workspace = workspace
    mock_state.settings.ramdisk_root = ""
    mock_state.settings.to_payload.return_value = {"workspace": workspace}
    app.state.app_state = mock_state

    return app, mock_state


@pytest.mark.asyncio
async def test_state_snapshot_returns_200_with_real_dirs(tmp_path: Path) -> None:
    """GET /state/snapshot returns 200 with stable payload using real temp dirs."""
    workspace = str(tmp_path / "workspace")
    runtime_root = str(tmp_path / "runtime")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    Path(runtime_root).mkdir(parents=True, exist_ok=True)

    app, _ = _build_app(workspace, runtime_root)
    fake_ctx = _FakeWorkspaceCtx(workspace=workspace, runtime_root=runtime_root)

    with (
        patch(
            "polaris.delivery.http.routers.system.resolve_workspace_runtime_context",
            return_value=fake_ctx,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/state/snapshot")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data: dict[str, Any] = response.json()
    assert "tasks" in data, f"Missing 'tasks' in payload keys: {list(data.keys())}"
    assert isinstance(data["tasks"], list), "'tasks' should be a list"
    assert "pm_state" in data, f"Missing 'pm_state' in payload keys: {list(data.keys())}"
    assert "docs_present" in data, f"Missing 'docs_present' in payload keys: {list(data.keys())}"
    assert "workspace_status" in data, f"Missing 'workspace_status' in payload keys: {list(data.keys())}"


@pytest.mark.asyncio
async def test_v2_state_snapshot_returns_200_with_real_dirs(tmp_path: Path) -> None:
    """GET /v2/state/snapshot returns 200 with stable payload using real temp dirs."""
    workspace = str(tmp_path / "workspace")
    runtime_root = str(tmp_path / "runtime")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    Path(runtime_root).mkdir(parents=True, exist_ok=True)

    app, _ = _build_app(workspace, runtime_root)
    fake_ctx = _FakeWorkspaceCtx(workspace=workspace, runtime_root=runtime_root)

    with (
        patch(
            "polaris.delivery.http.routers.system.resolve_workspace_runtime_context",
            return_value=fake_ctx,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v2/state/snapshot")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data: dict[str, Any] = response.json()
    assert "tasks" in data, f"Missing 'tasks' in payload keys: {list(data.keys())}"
    assert isinstance(data["tasks"], list), "'tasks' should be a list"
    assert "pm_state" in data, f"Missing 'pm_state' in payload keys: {list(data.keys())}"
    assert "docs_present" in data, f"Missing 'docs_present' in payload keys: {list(data.keys())}"
    assert "workspace_status" in data, f"Missing 'workspace_status' in payload keys: {list(data.keys())}"


@pytest.mark.asyncio
async def test_state_snapshot_empty_workspace_no_docs(tmp_path: Path) -> None:
    """GET /state/snapshot with empty workspace (no docs/) returns NEEDS_DOCS_INIT."""
    workspace = str(tmp_path / "empty_ws")
    runtime_root = str(tmp_path / "runtime")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    Path(runtime_root).mkdir(parents=True, exist_ok=True)

    app, _ = _build_app(workspace, runtime_root)
    fake_ctx = _FakeWorkspaceCtx(workspace=workspace, runtime_root=runtime_root)

    with (
        patch(
            "polaris.delivery.http.routers.system.resolve_workspace_runtime_context",
            return_value=fake_ctx,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/state/snapshot")

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    assert data["docs_present"] is False
    assert data["workspace_status"]["status"] == "NEEDS_DOCS_INIT"


@pytest.mark.asyncio
async def test_v2_state_snapshot_empty_workspace_no_docs(tmp_path: Path) -> None:
    """GET /v2/state/snapshot with empty workspace (no docs/) returns NEEDS_DOCS_INIT."""
    workspace = str(tmp_path / "empty_ws")
    runtime_root = str(tmp_path / "runtime")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    Path(runtime_root).mkdir(parents=True, exist_ok=True)

    app, _ = _build_app(workspace, runtime_root)
    fake_ctx = _FakeWorkspaceCtx(workspace=workspace, runtime_root=runtime_root)

    with (
        patch(
            "polaris.delivery.http.routers.system.resolve_workspace_runtime_context",
            return_value=fake_ctx,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v2/state/snapshot")

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    assert data["docs_present"] is False
    assert data["workspace_status"]["status"] == "NEEDS_DOCS_INIT"


@pytest.mark.asyncio
async def test_state_snapshot_with_docs_dir(tmp_path: Path) -> None:
    """GET /state/snapshot with docs/ present returns docs_present=True."""
    workspace = str(tmp_path / "ws_with_docs")
    runtime_root = str(tmp_path / "runtime")
    docs_dir = Path(workspace) / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    Path(runtime_root).mkdir(parents=True, exist_ok=True)

    app, _ = _build_app(workspace, runtime_root)
    fake_ctx = _FakeWorkspaceCtx(workspace=workspace, runtime_root=runtime_root)

    with (
        patch(
            "polaris.delivery.http.routers.system.resolve_workspace_runtime_context",
            return_value=fake_ctx,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/state/snapshot")

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    assert data["docs_present"] is True
    assert data["workspace_status"]["status"] == "READY"
