"""Tests for polaris.application.health."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    def test_returns_dict(self) -> None:
        result = get_lancedb_status()
        assert isinstance(result, dict)
        assert "ok" in result
        assert "python" in result


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


class TestBuildRuntimeIssues:
    def test_returns_list(self) -> None:
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp"
        issues = build_runtime_issues(mock_settings, "/tmp")
        assert isinstance(issues, list)


class TestLogBackendError:
    def test_logs_without_error(self) -> None:
        log_backend_error("test_event", "test detail", extra_key="extra_value")

    def test_logs_with_none_extra(self) -> None:
        log_backend_error("test_event", "test detail", none_key=None)
