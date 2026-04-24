"""Tests for polaris.bootstrap.runtime_health re-exports."""

from __future__ import annotations

import pytest
from polaris.bootstrap.runtime_health import (
    build_runtime_issues,
    check_backend_available,
    get_lancedb_status,
    log_backend_error,
    require_lancedb,
)
from polaris.domain.exceptions import ServiceUnavailableError


class TestGetLancedbStatus:
    def test_returns_dict_with_ok_key(self) -> None:
        result = get_lancedb_status()
        assert isinstance(result, dict)
        assert "ok" in result
        assert "python" in result


class TestRequireLancedb:
    def test_raises_or_returns_none(self) -> None:
        status = get_lancedb_status()
        if not status.get("ok"):
            with pytest.raises(ServiceUnavailableError):
                require_lancedb()
        else:
            require_lancedb()


class TestCheckBackendAvailable:
    def test_with_none_settings(self) -> None:
        result = check_backend_available(None)  # type: ignore[arg-type]
        assert result is None or isinstance(result, str)


class TestBuildRuntimeIssues:
    def test_with_none_settings(self) -> None:
        issues = build_runtime_issues(None, "/tmp")  # type: ignore[arg-type]
        assert isinstance(issues, list)


class TestLogBackendError:
    def test_logs_without_error(self) -> None:
        log_backend_error("test_event", "test detail", extra_key="extra_value")

    def test_logs_with_none_extra(self) -> None:
        log_backend_error("test_event", "test detail", none_key=None)
