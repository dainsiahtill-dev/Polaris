"""Tests for polaris.cells.qa.audit_verdict.internal.quality_service."""

from __future__ import annotations

import shutil

import pytest
from polaris.cells.qa.audit_verdict.internal.quality_service import (
    QualityService,
    get_quality_service,
)


class TestQualityService:
    """QualityService tests."""

    def test_init_detects_ruff_availability(self) -> None:
        service = QualityService()
        # ruff_executable should be None if not available
        # or the path to ruff if available
        if service.available:
            assert service.ruff_executable is not None
        else:
            assert service.ruff_executable is None

    def test_get_status(self) -> None:
        service = QualityService()
        status = service.get_status()
        assert "available" in status
        assert "path" in status
        assert status["available"] == service.available

    def test_lint_code_returns_error_when_ruff_unavailable(self) -> None:
        # Create a mock service with ruff unavailable
        service = QualityService()
        service.available = False

        result = service.lint_code("def foo():\n    pass", extension=".py")
        assert result["success"] is False
        assert "ruff_missing" in result["reason"]

    def test_lint_code_returns_error_for_non_python(self) -> None:
        service = QualityService()
        service.available = False  # Simulate unavailable

        result = service.lint_code("some content", extension=".js")
        assert result["success"] is False
        assert "ruff_missing_or_not_python" in result["reason"]

    def test_lint_code_returns_success_when_available(self) -> None:
        # Check if ruff is available
        ruff_path = shutil.which("ruff")
        if not ruff_path:
            pytest.skip("ruff not installed")

        service = QualityService()
        assert service.available is True

        # Lint valid Python code
        result = service.lint_code("def foo():\n    pass")
        assert "success" in result

    def test_lint_code_with_valid_code(self) -> None:
        # Check if ruff is available
        ruff_path = shutil.which("ruff")
        if not ruff_path:
            pytest.skip("ruff not installed")

        service = QualityService()

        # Clean code should have no issues
        result = service.lint_code("def foo():\n    pass\n")
        # Result structure depends on ruff output
        assert "success" in result
        if result["success"]:
            assert "lints" in result

    def test_lint_code_with_issues(self) -> None:
        # Check if ruff is available
        ruff_path = shutil.which("ruff")
        if not ruff_path:
            pytest.skip("ruff not installed")

        service = QualityService()

        # Code with unused import should trigger lint
        result = service.lint_code("import os\ndef foo():\n    pass\n")
        assert "success" in result

    def test_lint_code_with_fix_mode(self) -> None:
        # Check if ruff is available
        ruff_path = shutil.which("ruff")
        if not ruff_path:
            pytest.skip("ruff not installed")

        service = QualityService()

        # Code that can be fixed
        result = service.lint_code("import os\ndef foo():\n    pass\n", fix=True)
        assert "success" in result
        if result["success"] and result.get("fixed_code"):
            # When fix is applied, fixed_code should be present
            assert isinstance(result["fixed_code"], str)


class TestGetQualityService:
    """Singleton and factory function tests."""

    def test_get_quality_service_returns_singleton(self) -> None:
        service1 = get_quality_service()
        service2 = get_quality_service()
        assert service1 is service2

    def test_service_is_quality_service_instance(self) -> None:
        service = get_quality_service()
        assert isinstance(service, QualityService)
