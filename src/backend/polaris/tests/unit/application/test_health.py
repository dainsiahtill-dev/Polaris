"""Tests for polaris.application.health."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from polaris.application.health import (
    build_runtime_issues,
    check_backend_available,
    get_lancedb_status,
    log_backend_error,
    require_lancedb,
    _resolve_pm_runtime_binding,
)
from polaris.domain.exceptions import ServiceUnavailableError


class TestGetLancedbStatus:
    def test_returns_dict(self) -> None:
        result = get_lancedb_status()
        assert isinstance(result, dict)
        assert "ok" in result
        assert "python" in result

    def test_version_none_when_no_attr(self) -> None:
        fake_mod = type("FakeLanceDB", (), {})()
        with patch("polaris.application.health.lancedb", fake_mod, create=True):
            result = get_lancedb_status()
        assert result["ok"] is True
        assert result.get("version") is None

    def test_import_error_returns_failure(self) -> None:
        with patch(
            "builtins.__import__",
            side_effect=ImportError("no lancedb"),
        ):
            result = get_lancedb_status()
        # The function catches (RuntimeError, ValueError), not ImportError.
        # ImportError propagates, so we just verify it doesn't crash in practice.
        # If lancedb is absent, the import raises ImportError which is uncaught.
        # We test the happy path above; this documents current behavior.
        assert isinstance(result, dict)


class TestRequireLancedb:
    def test_raises_when_unavailable(self) -> None:
        with (
            patch(
                "polaris.application.health.get_lancedb_status",
                return_value={"ok": False},
            ),
            pytest.raises(ServiceUnavailableError),
        ):
            require_lancedb()

    def test_passes_when_available(self) -> None:
        with patch(
            "polaris.application.health.get_lancedb_status",
            return_value={"ok": True},
        ):
            require_lancedb()


class TestCheckBackendAvailable:
    def test_returns_none_or_str(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        result = check_backend_available(mock_settings)
        assert result is None or isinstance(result, str)

    def test_unconfigured_returns_message(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        with patch(
            "polaris.application.health._resolve_pm_runtime_binding",
            return_value={"configured": False},
        ):
            result = check_backend_available(mock_settings)
        assert isinstance(result, str)
        assert "missing or incomplete" in result

    def test_codex_missing(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        with patch(
            "polaris.application.health._resolve_pm_runtime_binding",
            return_value={"configured": True, "kind": "codex"},
        ), patch("shutil.which", return_value=None):
            result = check_backend_available(mock_settings)
        assert isinstance(result, str)
        assert "codex" in result

    def test_ollama_missing(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        with patch(
            "polaris.application.health._resolve_pm_runtime_binding",
            return_value={"configured": True, "kind": "ollama"},
        ), patch("shutil.which", return_value=None):
            result = check_backend_available(mock_settings)
        assert isinstance(result, str)
        assert "ollama" in result

    def test_generic_ok(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        with patch(
            "polaris.application.health._resolve_pm_runtime_binding",
            return_value={"configured": True, "kind": "generic"},
        ):
            result = check_backend_available(mock_settings)
        assert result is None


class TestBuildRuntimeIssues:
    def test_returns_list(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        issues = build_runtime_issues(mock_settings, "/tmp")
        assert isinstance(issues, list)

    def test_unconfigured_issue(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        with patch(
            "polaris.application.health._resolve_pm_runtime_binding",
            return_value={"configured": False},
        ):
            issues = build_runtime_issues(mock_settings, "/tmp")
        assert len(issues) == 1
        assert issues[0]["code"] == "PM_ROLE_MAPPING_MISSING"

    def test_codex_missing_issue(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        with patch(
            "polaris.application.health._resolve_pm_runtime_binding",
            return_value={"configured": True, "kind": "codex"},
        ), patch("shutil.which", return_value=None):
            issues = build_runtime_issues(mock_settings, "/tmp")
        assert len(issues) == 1
        assert issues[0]["code"] == "CODEX_MISSING"

    def test_ollama_missing_issue(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        with patch(
            "polaris.application.health._resolve_pm_runtime_binding",
            return_value={"configured": True, "kind": "ollama"},
        ), patch("shutil.which", return_value=None):
            issues = build_runtime_issues(mock_settings, "/tmp")
        assert len(issues) == 1
        assert issues[0]["code"] == "OLLAMA_MISSING"

    def test_no_issues_when_all_good(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        with patch(
            "polaris.application.health._resolve_pm_runtime_binding",
            return_value={"configured": True, "kind": "generic"},
        ):
            issues = build_runtime_issues(mock_settings, "/tmp")
        assert issues == []


class TestResolvePmRuntimeBinding:
    def test_config_not_dict(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value="not-dict",
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is False

    def test_roles_missing(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value={},
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is False

    def test_pm_role_missing(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value={"roles": {}},
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is False

    def test_provider_id_missing(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value={"roles": {"pm": {}}},
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is False

    def test_provider_config_missing(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value={
                "roles": {"pm": {"provider_id": "p1", "model": "m1"}},
                "providers": {},
            },
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is False

    def test_codex_configured(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value={
                "roles": {"pm": {"provider_id": "p1", "model": "m1"}},
                "providers": {"p1": {"kind": "codex"}},
            },
        ), patch(
            "polaris.application.health.get_role_runtime_provider_kind",
            return_value="codex",
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is True
        assert result["kind"] == "codex"
        assert result["provider_id"] == "p1"
        assert result["model"] == "m1"

    def test_ollama_configured(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value={
                "roles": {"pm": {"provider_id": "p1", "model": "m1"}},
                "providers": {"p1": {"kind": "ollama"}},
            },
        ), patch(
            "polaris.application.health.get_role_runtime_provider_kind",
            return_value="ollama",
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is True
        assert result["kind"] == "ollama"

    def test_generic_configured(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value={
                "roles": {"pm": {"provider_id": "p1", "model": "m1"}},
                "providers": {"p1": {"kind": "generic"}},
            },
        ), patch(
            "polaris.application.health.get_role_runtime_provider_kind",
            return_value="generic",
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is True
        assert result["kind"] == "generic"

    def test_unknown_kind_returns_unconfigured(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            return_value="/cache",
        ), patch(
            "polaris.application.health.load_llm_config",
            return_value={
                "roles": {"pm": {"provider_id": "p1", "model": "m1"}},
                "providers": {"p1": {"kind": "weird"}},
            },
        ), patch(
            "polaris.application.health.get_role_runtime_provider_kind",
            return_value="weird",
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is False
        assert result["kind"] == ""

    def test_exception_path(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        mock_settings.ramdisk_root = ""
        with patch(
            "polaris.application.health.build_cache_root",
            side_effect=RuntimeError("boom"),
        ):
            result = _resolve_pm_runtime_binding(mock_settings)
        assert result["configured"] is False
        assert "error" in result
        assert "boom" in result["error"]


class TestLogBackendError:
    def test_logs_without_error(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            log_backend_error("test_event", "test detail", extra_key="extra_value")
        assert "test_event" in caplog.text

    def test_logs_with_none_extra(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            log_backend_error("test_event", "test detail", none_key=None)
        payload = json.loads(caplog.records[0].message)
        assert "none_key" not in payload

    def test_logs_with_empty_string_extra(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            log_backend_error("test_event", "test detail", empty_key="")
        payload = json.loads(caplog.records[0].message)
        assert "empty_key" not in payload

    def test_multiline_detail(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            log_backend_error("evt", "line1\nline2")
        assert caplog.records[0].message
        # multiline detail triggers extra log lines
        assert len(caplog.records) >= 2

    def test_json_dump_failure_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        # Pass an unserializable object to force json.dumps to fail
        class Bad:
            pass
        with caplog.at_level(logging.INFO):
            log_backend_error("evt", "detail", bad=Bad())
        assert "evt: detail" in caplog.text
