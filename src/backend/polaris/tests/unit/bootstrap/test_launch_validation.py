"""Tests for polaris.bootstrap.launch_validation."""

from __future__ import annotations

from dataclasses import dataclass

from polaris.bootstrap.launch_validation import (
    LaunchValidationResult,
    ValidationResult,
    bootstrap_validation,
    ensure_utf8_environment,
    validate_environment,
    validate_launch_request,
)


class TestLaunchValidationResult:
    def test_default_is_valid(self) -> None:
        result = LaunchValidationResult()
        assert result.is_valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_add_error(self) -> None:
        result = LaunchValidationResult()
        result.add_error("bad")
        assert result.is_valid is False
        assert result.errors == ["bad"]

    def test_add_warning(self) -> None:
        result = LaunchValidationResult()
        result.add_warning("warn")
        assert result.is_valid is True
        assert result.warnings == ["warn"]


class TestValidationResultAlias:
    def test_alias(self) -> None:
        assert ValidationResult is LaunchValidationResult


@dataclass
class _FakeRequest:
    workspace: str | None = None
    port: int | None = None
    host: str | None = None
    ramdisk_root: str | None = None
    log_level: str | None = None
    cors_origins: list[str] | None = None


class TestValidateLaunchRequest:
    def test_empty_request(self) -> None:
        req = _FakeRequest()
        result = validate_launch_request(req)
        assert result.is_valid is True

    def test_invalid_port(self) -> None:
        req = _FakeRequest(port=70000)
        result = validate_launch_request(req)
        assert result.is_valid is False
        assert "Invalid port" in result.errors[0]

    def test_privileged_port_warning(self) -> None:
        req = _FakeRequest(port=80)
        result = validate_launch_request(req)
        assert result.is_valid is True
        assert any("elevated privileges" in w for w in result.warnings)

    def test_invalid_log_level(self) -> None:
        req = _FakeRequest(log_level="verbose")
        result = validate_launch_request(req)
        assert result.is_valid is False
        assert "Invalid log level" in result.errors[0]

    def test_cors_origin_warning(self) -> None:
        req = _FakeRequest(cors_origins=["example.com"])
        result = validate_launch_request(req)
        assert result.is_valid is True
        assert any("protocol scheme" in w for w in result.warnings)


class TestValidateEnvironment:
    def test_returns_result(self) -> None:
        result = validate_environment()
        assert isinstance(result, LaunchValidationResult)


class TestBootstrapValidation:
    def test_combines_results(self) -> None:
        req = _FakeRequest(port=70000)
        result = bootstrap_validation(req)
        assert result.is_valid is False


class TestEnsureUtf8Environment:
    def test_sets_env(self) -> None:
        ensure_utf8_environment()
        import os

        assert os.environ.get("PYTHONUTF8") == "1"
