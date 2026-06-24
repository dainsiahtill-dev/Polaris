"""Tests for the opt-in /v2/context/admin/* endpoints."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.v2 import context as v2_context


@pytest.fixture
def client() -> TestClient:
    """Build a minimal FastAPI app with auth overridden for the tests."""
    app = FastAPI()
    app.include_router(v2_context.router)
    app.dependency_overrides[v2_context.require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(
            workspace=".",
            ramdisk_root="",
        )
    )
    # The router checks ``app.state.auth`` via require_auth, but we
    # override the dependency so this is only a safety net.
    app.state.auth = MagicMock()
    return TestClient(app)


@pytest.fixture
def disabled_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client with the admin surface disabled (default)."""
    monkeypatch.delenv("KERNELONE_CONTEXT_ADMIN_ENABLED", raising=False)
    app = FastAPI()
    app.include_router(v2_context.router)
    app.dependency_overrides[v2_context.require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=".", ramdisk_root=""))
    app.state.auth = MagicMock()
    return TestClient(app)


@pytest.fixture
def enabled_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client with the admin surface explicitly enabled."""
    monkeypatch.setenv("KERNELONE_CONTEXT_ADMIN_ENABLED", "1")
    app = FastAPI()
    app.include_router(v2_context.router)
    app.dependency_overrides[v2_context.require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=".", ramdisk_root=""))
    app.state.auth = MagicMock()
    return TestClient(app)


class TestContextAdminGate:
    """The admin endpoints are gated by the env var + require_auth."""

    def test_stats_enabled_when_env_unset(self, disabled_client: TestClient) -> None:
        """Default behavior: admin surface is enabled when env var is unset."""
        response = disabled_client.get("/v2/context/admin/stats")
        assert response.status_code == 200

    def test_stats_disabled_when_env_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicitly disabled: GET /admin/stats returns 404."""
        monkeypatch.setenv("KERNELONE_CONTEXT_ADMIN_ENABLED", "false")
        app = FastAPI()
        app.include_router(v2_context.router)
        app.dependency_overrides[v2_context.require_auth] = lambda: None
        app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=".", ramdisk_root=""))
        app.state.auth = MagicMock()
        client = TestClient(app)
        response = client.get("/v2/context/admin/stats")
        assert response.status_code == 404
        assert response.json().get("detail", {}).get("code") == "ADMIN_DISABLED"


class TestContextAdminStats:
    """GET /v2/context/admin/stats returns the schema fields."""

    def test_basic_stats_route_is_not_captured_by_hash_route(
        self,
        disabled_client: TestClient,
        tmp_path: Any,
    ) -> None:
        """GET /v2/context/stats must resolve to stats, not /{hash}."""
        from polaris.kernelone.storage.io_paths import build_cache_root

        workspace = str(tmp_path)
        with (
            patch.object(v2_context, "_resolve_workspace", return_value=workspace),
            patch.object(v2_context, "_build_retention") as build_mock,
        ):
            cfg = v2_context.ContextStoreRetentionConfig(
                ttl_seconds=86400,
                max_total_bytes=1024,
                max_files=10,
                sweep_min_interval_seconds=0,
                enabled=True,
            )
            retention = v2_context.ContextStoreRetention(
                workspace=workspace,
                config=cfg,
                runtime_base=build_cache_root("", workspace),
            )
            build_mock.return_value = retention
            response = disabled_client.get("/v2/context/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["workspace"] == workspace
        assert data["last_sweep_report"] is None
        assert data.get("detail", {}).get("code") != "INVALID_HASH"

    def test_stats_returns_expected_schema(self, enabled_client: TestClient, tmp_path: Any) -> None:
        """The stats endpoint returns the schema when enabled."""
        # Plant a fake context store so the gate has something to count.
        from polaris.kernelone.storage import StorageLayout
        from polaris.kernelone.storage.io_paths import build_cache_root

        workspace = str(tmp_path)
        layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
        shard = "ab"
        contexts_dir = layout.get_path("runtime", f"contexts/{shard}")
        contexts_dir.mkdir(parents=True, exist_ok=True)
        (contexts_dir / "abcdef0000000000000000aa").write_text("x", encoding="utf-8")

        with (
            patch.object(v2_context, "_resolve_workspace", return_value=workspace),
            patch.object(v2_context, "_build_retention") as build_mock,
        ):
            cfg = v2_context.ContextStoreRetentionConfig(
                ttl_seconds=86400,
                max_total_bytes=1024,
                max_files=10,
                sweep_min_interval_seconds=0,
                enabled=True,
            )
            retention = v2_context.ContextStoreRetention(
                workspace=workspace,
                config=cfg,
                runtime_base=build_cache_root("", workspace),
            )
            build_mock.return_value = retention
            response = enabled_client.get("/v2/context/admin/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["workspace"] == workspace
        assert "contexts_root" in data
        assert data["file_count"] == 1
        assert data["total_bytes"] == 1
        assert "oldest_mtime" in data
        assert "config" in data
        assert data["config"]["ttl_seconds"] == 86400
        assert data["config"]["max_files"] == 10
        assert data["config"]["enabled"] is True


class TestContextAdminSweep:
    """POST /v2/context/admin/sweep runs a sweep and returns a SweepReport."""

    def test_sweep_returns_report(self, enabled_client: TestClient, tmp_path: Any) -> None:
        """The sweep endpoint returns a SweepReport-shaped body."""
        from polaris.kernelone.storage.io_paths import build_cache_root

        workspace = str(tmp_path)
        cfg = v2_context.ContextStoreRetentionConfig(
            ttl_seconds=10**9,
            max_total_bytes=10**9,
            max_files=1000,
            sweep_min_interval_seconds=0,
        )

        with (
            patch.object(v2_context, "_resolve_workspace", return_value=workspace),
            patch.object(v2_context, "_build_retention") as build_mock,
        ):
            retention = v2_context.ContextStoreRetention(
                workspace=workspace,
                config=cfg,
                runtime_base=build_cache_root("", workspace),
            )
            build_mock.return_value = retention
            response = enabled_client.post("/v2/context/admin/sweep", json={"triggers": ["admin"]})

        assert response.status_code == 200
        data = response.json()
        for key in (
            "scanned_files",
            "removed_files",
            "removed_bytes",
            "kept_files",
            "total_bytes_after",
            "elapsed_ms",
            "triggers",
        ):
            assert key in data
        assert "admin" in data["triggers"]


class TestContextAdminAuth:
    """The admin endpoints require auth like the rest of /v2/*."""

    def test_stats_unauthorized_without_token_override(self, tmp_path: Any) -> None:
        """Without dependency override, require_auth returns 401."""
        from polaris.kernelone.llm.engine.context_store_retention import (
            ContextStoreRetention,
            ContextStoreRetentionConfig,
        )
        from polaris.kernelone.storage.io_paths import build_cache_root

        os.environ.pop("KERNELONE_CONTEXT_ADMIN_ENABLED", None)
        os.environ["KERNELONE_CONTEXT_ADMIN_ENABLED"] = "1"

        app = FastAPI()
        app.include_router(v2_context.router)
        # NOTE: no dependency override on require_auth → real 401 path.
        app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=".", ramdisk_root=""))
        app.state.auth = MagicMock()
        # require_auth checks ``app.state.auth.check`` — make it return
        # False so the request is rejected.
        app.state.auth.check = MagicMock(return_value=False)
        client = TestClient(app)

        with patch.object(v2_context, "_build_retention") as build_mock:
            cfg = ContextStoreRetentionConfig()
            retention = ContextStoreRetention(
                workspace=str(tmp_path),
                config=cfg,
                runtime_base=build_cache_root("", str(tmp_path)),
            )
            build_mock.return_value = retention
            response = client.get("/v2/context/admin/stats")

        # require_auth raises 401 when no token provided.
        assert response.status_code == 401

        # Clean up
        os.environ.pop("KERNELONE_CONTEXT_ADMIN_ENABLED", None)
