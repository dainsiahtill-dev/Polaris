"""Contract tests for polaris.delivery.http.routers.tests module (LLM test endpoints)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import tests as tests_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(tests_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestLlmTestsRouter:
    """Contract tests for the LLM tests router."""

    def test_llm_test_validation_error(self) -> None:
        """POST /llm/test returns 422 for invalid payload."""
        client = _build_client()
        response = client.post(
            "/llm/test",
            json={"test_level": 123},  # test_level should be string
        )
        assert response.status_code == 422


class TestLlmTestReport:
    """Tests for GET /llm/test/{test_run_id} endpoint."""

    def test_llm_test_report_not_found(self) -> None:
        """GET /llm/test/{id} returns 404 when report not found."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.tests.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.tests.resolve_artifact_path",
                return_value="/tmp/report.json",
            ),
            patch(
                "os.path.isfile",
                return_value=False,
            ),
        ):
            response = client.get("/llm/test/nonexistent")

        assert response.status_code == 404
        assert response.json()["detail"] == "report not found"

    def test_llm_test_report_invalid_id(self) -> None:
        """GET /llm/test/{id} returns 400 for invalid test run id."""
        client = _build_client()
        response = client.get("/llm/test/invalid@id#")
        assert response.status_code == 400
        assert "invalid test run id" in response.json()["detail"]


class TestLlmTestTranscript:
    """Tests for GET /llm/test/{test_run_id}/transcript endpoint."""

    def test_llm_test_transcript_not_found(self) -> None:
        """GET /llm/test/{id}/transcript returns 404 when transcript not found."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.tests.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.tests.resolve_artifact_path",
                return_value="/tmp/transcript.md",
            ),
            patch(
                "os.path.isfile",
                return_value=False,
            ),
        ):
            response = client.get("/llm/test/test-123/transcript")

        assert response.status_code == 404
        assert response.json()["detail"] == "transcript not found"


class TestNormalizeReportPayload:
    """Unit tests for _normalize_report_payload helper function."""

    def test_normalize_legacy_format(self) -> None:
        """_normalize_report_payload handles legacy format correctly."""
        from polaris.delivery.http.routers.tests import _normalize_report_payload

        legacy_report = {
            "run_id": "test-123",
            "provider_id": "openai",
            "model": "gpt-4",
            "role": "director",
            "summary": {"ready": True, "grade": "PASS"},
            "suites": {},
        }

        result = _normalize_report_payload(legacy_report)

        assert result["schema_version"] == 1
        assert result["test_run_id"] == "test-123"
        assert result["target"]["provider_id"] == "openai"

    def test_normalize_modern_format(self) -> None:
        """_normalize_report_payload passes through modern format."""
        from polaris.delivery.http.routers.tests import _normalize_report_payload

        modern_report = {
            "test_run_id": "test-456",
            "target": {
                "role": "qa",
                "provider_id": "anthropic",
                "model": "claude-3",
            },
            "suites": {},
        }

        result = _normalize_report_payload(modern_report)

        # Modern format should be passed through
        assert result["test_run_id"] == "test-456"
        assert result["target"]["role"] == "qa"

    def test_normalize_with_suites_list(self) -> None:
        """_normalize_report_payload converts suite list to dict format."""
        from polaris.delivery.http.routers.tests import _normalize_report_payload

        report_with_suites = {
            "run_id": "test-789",
            "provider_id": "openai",
            "model": "gpt-4",
            "role": "director",
            "summary": {"ready": True},
            "suites": [
                {
                    "suite_name": "connectivity",
                    "total_cases": 5,
                    "passed_cases": 5,
                    "results": [],
                },
            ],
        }

        result = _normalize_report_payload(report_with_suites)

        assert "connectivity" in result["suites"]
        assert result["suites"]["connectivity"]["ok"] is True
        assert result["suites"]["connectivity"]["details"]["total_cases"] == 5

    def test_normalize_invalid_payload(self) -> None:
        """_normalize_report_payload handles invalid payload gracefully."""
        from polaris.delivery.http.routers.tests import _normalize_report_payload

        result = _normalize_report_payload("not a dict")
        assert result["ok"] is False

        result = _normalize_report_payload(None)
        assert result["ok"] is False


class TestResolveTestPath:
    """Unit tests for _resolve_test_path helper function."""

    def test_resolve_test_path_report(self) -> None:
        """_resolve_test_path returns correct path for report file."""
        from polaris.delivery.http.routers.tests import _resolve_test_path

        settings = MagicMock()
        settings.ramdisk_root = ""

        with (
            patch(
                "polaris.delivery.http.routers.tests.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.tests.resolve_artifact_path",
                return_value="/tmp/workspace/.polaris/runtime/llm_tests/test-123/LLM_TEST_REPORT.json",
            ),
            patch(
                "os.path.isfile",
                return_value=True,
            ),
        ):
            path = _resolve_test_path(settings, "test-123", "report", "/tmp/workspace")
            assert "test-123" in path

    def test_resolve_test_path_invalid_id(self) -> None:
        """_resolve_test_path raises HTTPException for invalid run id."""
        from fastapi import HTTPException
        from polaris.delivery.http.routers.tests import _resolve_test_path

        settings = MagicMock()
        settings.ramdisk_root = ""

        with pytest.raises(HTTPException) as exc_info:
            _resolve_test_path(settings, "invalid@id", "report", "/tmp/workspace")

        assert exc_info.value.status_code == 400
        assert "invalid test run id" in exc_info.value.detail


class TestMapProviderConfigError:
    """Unit tests for _map_provider_config_error helper function."""

    def test_map_provider_not_found(self) -> None:
        """_map_provider_config_error returns 404 for ProviderNotFoundError."""
        from fastapi import HTTPException
        from polaris.cells.llm.provider_config.public.contracts import ProviderNotFoundError
        from polaris.delivery.http.routers.tests import _map_provider_config_error

        exc = ProviderNotFoundError("test-provider")
        result = _map_provider_config_error(exc)

        assert isinstance(result, HTTPException)
        assert result.status_code == 404

    def test_map_role_not_configured(self) -> None:
        """_map_provider_config_error returns 404 for RoleNotConfiguredError."""
        from fastapi import HTTPException
        from polaris.cells.llm.provider_config.public.contracts import RoleNotConfiguredError
        from polaris.delivery.http.routers.tests import _map_provider_config_error

        exc = RoleNotConfiguredError("unknown-role")
        result = _map_provider_config_error(exc)

        assert isinstance(result, HTTPException)
        assert result.status_code == 404

    def test_map_validation_error(self) -> None:
        """_map_provider_config_error returns 400 for ProviderConfigValidationError."""
        from fastapi import HTTPException
        from polaris.cells.llm.provider_config.public.contracts import (
            ProviderConfigValidationError,
        )
        from polaris.delivery.http.routers.tests import _map_provider_config_error

        exc = ProviderConfigValidationError("Invalid config")
        result = _map_provider_config_error(exc)

        assert isinstance(result, HTTPException)
        assert result.status_code == 400
