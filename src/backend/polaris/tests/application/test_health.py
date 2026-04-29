"""Tests for polaris.application.health module."""

from __future__ import annotations

import sys

import pytest
from polaris.application.health import (
    build_runtime_issues,
    check_backend_available,
    get_lancedb_status,
    log_backend_error,
    require_lancedb,
)
from polaris.domain.exceptions import ServiceUnavailableError


class TestGetLancedbStatus:
    def test_get_lancedb_status_ok(self):
        status = get_lancedb_status()
        assert isinstance(status, dict)
        assert "ok" in status
        assert "python" in status

    def test_get_lancedb_status_import_error(self, monkeypatch):
        import builtins

        original = builtins.__import__

        def broken_import(name, *args, **kwargs):
            if name == "lancedb":
                raise RuntimeError("Simulated lancedb import failure")
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", broken_import)
        status = get_lancedb_status()
        assert status["ok"] is False
        assert "Simulated lancedb import failure" in status["error"]
        assert status["python"] == sys.executable


class TestRequireLancedb:
    def test_require_lancedb_available(self):
        require_lancedb()

    def test_require_lancedb_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health.get_lancedb_status",
            lambda: {"ok": False, "error": "not found", "python": sys.executable},
        )
        with pytest.raises(ServiceUnavailableError) as exc_info:
            require_lancedb()
        assert exc_info.value.code == "SERVICE_UNAVAILABLE"
        assert "lancedb not available" in str(exc_info.value)


class TestCheckBackendAvailable:
    @pytest.fixture
    def mock_settings(self):
        from polaris.bootstrap.config import Settings

        return Settings(workspace="/tmp/test_workspace")

    def test_unconfigured_binding(self, mock_settings, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health._resolve_pm_runtime_binding",
            lambda _s: {"configured": False},
        )
        result = check_backend_available(mock_settings)
        assert result is not None
        assert "PM role mapping is missing" in result

    def test_codex_not_found(self, mock_settings, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health._resolve_pm_runtime_binding",
            lambda _s: {"configured": True, "kind": "codex"},
        )
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        result = check_backend_available(mock_settings)
        assert result is not None
        assert "codex command not found" in result

    def test_ollama_not_found(self, mock_settings, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health._resolve_pm_runtime_binding",
            lambda _s: {"configured": True, "kind": "ollama"},
        )
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        result = check_backend_available(mock_settings)
        assert result is not None
        assert "ollama command not found" in result

    def test_backend_available(self, mock_settings, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health._resolve_pm_runtime_binding",
            lambda _s: {"configured": True, "kind": "generic"},
        )
        result = check_backend_available(mock_settings)
        assert result is None


class TestBuildRuntimeIssues:
    @pytest.fixture
    def mock_settings(self):
        from polaris.bootstrap.config import Settings

        return Settings(workspace="/tmp/test_workspace")

    def test_pm_role_mapping_missing(self, mock_settings, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health._resolve_pm_runtime_binding",
            lambda _s: {"configured": False},
        )
        issues = build_runtime_issues(mock_settings, "/tmp/ws")
        assert len(issues) == 1
        assert issues[0]["code"] == "PM_ROLE_MAPPING_MISSING"

    def test_codex_missing(self, mock_settings, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health._resolve_pm_runtime_binding",
            lambda _s: {"configured": True, "kind": "codex"},
        )
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        issues = build_runtime_issues(mock_settings, "/tmp/ws")
        assert len(issues) == 1
        assert issues[0]["code"] == "CODEX_MISSING"

    def test_ollama_missing(self, mock_settings, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health._resolve_pm_runtime_binding",
            lambda _s: {"configured": True, "kind": "ollama"},
        )
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        issues = build_runtime_issues(mock_settings, "/tmp/ws")
        assert len(issues) == 1
        assert issues[0]["code"] == "OLLAMA_MISSING"

    def test_no_issues(self, mock_settings, monkeypatch):
        monkeypatch.setattr(
            "polaris.application.health._resolve_pm_runtime_binding",
            lambda _s: {"configured": True, "kind": "generic"},
        )
        issues = build_runtime_issues(mock_settings, "/tmp/ws")
        assert issues == []


class TestLogBackendError:
    def test_log_backend_error_basic(self, caplog):
        log_backend_error("test_event", "test detail")

    def test_log_backend_error_with_extra(self, caplog):
        log_backend_error("test_event", "test detail", run_id="r1", empty_str="", none_val=None)

    def test_log_backend_error_multiline_detail(self, caplog):
        log_backend_error("test_event", "line1\nline2\nline3")
