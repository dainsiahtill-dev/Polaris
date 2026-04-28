import importlib.util, pytest
if importlib.util.find_spec("core") is None:
    pytest.skip("Legacy module not available: core.startup", allow_module_level=True)

"""Tests for BackendBootstrapper - unified backend startup core.

This module tests the backend bootstrap functionality introduced
in Phase 2 of the "Thin CLI + Core OO" refactoring.
"""

import sys
from pathlib import Path

# Add src/backend to path
sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "backend"))

import pytest
import asyncio

from core.startup import ConfigLoader, BackendBootstrapper, ConfigLoadError
from application.dto.backend_launch import BackendLaunchRequest, BackendLaunchResult
from domain.models.config_snapshot import ConfigSnapshot, SourceType


class TestConfigLoader:
    """Test ConfigLoader functionality."""

    def test_load_defaults(self):
        """Test loading default configuration."""
        loader = ConfigLoader()
        snapshot = loader.load()

        # Check default values
        assert snapshot.get("server.host") == "127.0.0.1"
        assert snapshot.get("server.port") == 49977
        assert snapshot.get("pm.backend") == "auto"

    def test_load_with_cli_overrides(self):
        """Test CLI overrides take precedence."""
        loader = ConfigLoader()
        snapshot = loader.load(cli_overrides={"server.port": 8080})

        assert snapshot.get("server.port") == 8080
        assert snapshot.get_source("server.port") == SourceType.CLI

    def test_get_default(self):
        """Test retrieving individual defaults."""
        loader = ConfigLoader()

        assert loader.get_default("server.host") == "127.0.0.1"
        assert loader.get_default("nonexistent") is None


class TestBackendLaunchRequest:
    """Test BackendLaunchRequest DTO."""

    def test_basic_creation(self):
        """Test creating a launch request."""
        request = BackendLaunchRequest(
            host="127.0.0.1",
            port=8080,
            workspace=Path.cwd(),
        )

        assert request.host == "127.0.0.1"
        assert request.port == 8080
        assert request.workspace == Path.cwd()

    def test_validation_valid(self):
        """Test validation with valid request."""
        request = BackendLaunchRequest(
            host="127.0.0.1",
            port=8080,
            workspace=Path.cwd(),
        )

        result = request.validate()
        assert result.is_valid
        assert not result.errors

    def test_validation_invalid_port_negative(self):
        """Test validation with negative port."""
        request = BackendLaunchRequest(
            host="127.0.0.1",
            port=-1,
            workspace=Path.cwd(),
        )

        result = request.validate()
        assert not result.is_valid
        assert any("port" in e.lower() for e in result.errors)

    def test_validation_invalid_port_too_large(self):
        """Test validation with port > 65535."""
        request = BackendLaunchRequest(
            host="127.0.0.1",
            port=70000,
            workspace=Path.cwd(),
        )

        result = request.validate()
        assert not result.is_valid

    def test_with_port(self):
        """Test functional update of port."""
        request = BackendLaunchRequest(port=8080)
        new_request = request.with_port(9000)

        assert request.port == 8080  # Original unchanged
        assert new_request.port == 9000

    def test_to_uvicorn_options(self):
        """Test conversion to uvicorn options."""
        request = BackendLaunchRequest(
            host="127.0.0.1",
            port=8080,
            log_level="debug",
        )

        options = request.to_uvicorn_options()
        assert options["host"] == "127.0.0.1"
        assert options["port"] == 8080
        assert options["log_level"] == "debug"
        assert options["factory"] is True


class TestBackendLaunchResult:
    """Test BackendLaunchResult DTO."""

    def test_success_result(self):
        """Test successful result."""
        result = BackendLaunchResult(
            success=True,
            port=8080,
            process_handle={"pid": 12345},  # Mock handle
            startup_time_ms=100,
        )

        assert result.is_success()
        assert result.port == 8080
        assert result.get_error() == ""

    def test_failure_result(self):
        """Test failure result."""
        result = BackendLaunchResult(
            success=False,
            error_message="Port in use",
        )

        assert not result.is_success()
        assert result.get_error() == "Port in use"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = BackendLaunchResult(
            success=True,
            port=8080,
            startup_time_ms=100,
        )

        data = result.to_dict()
        assert data["success"] is True
        assert data["port"] == 8080
        assert data["startup_time_ms"] == 100

    def test_to_electron_event(self):
        """Test conversion to Electron event format."""
        result = BackendLaunchResult(
            success=True,
            port=8080,
        )

        event = result.to_electron_event()
        assert event["event"] == "backend_started"
        assert event["port"] == 8080
        assert "timestamp" in event


class TestBackendBootstrapperBasics:
    """Test BackendBootstrapper basic functionality."""

    def test_initialization(self):
        """Test bootstrapper initialization."""
        bootstrapper = BackendBootstrapper()

        assert bootstrapper is not None
        assert bootstrapper.get_default_options()["host"] == "127.0.0.1"

    def test_default_options(self):
        """Test default options retrieval."""
        bootstrapper = BackendBootstrapper()
        defaults = bootstrapper.get_default_options()

        assert "host" in defaults
        assert "port" in defaults
        assert "log_level" in defaults
        assert "cors_origins" in defaults

    def test_find_free_port(self):
        """Test free port selection."""
        bootstrapper = BackendBootstrapper()
        port = bootstrapper._find_free_port()

        assert port > 0
        assert port < 65536

    def test_is_port_available(self):
        """Test port availability check."""
        bootstrapper = BackendBootstrapper()

        # Find a free port
        free_port = bootstrapper._find_free_port()
        assert bootstrapper._is_port_available(free_port)

        # Occupied port should not be available
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            occupied_port = s.getsockname()[1]
            # Port is now occupied
            assert not bootstrapper._is_port_available(occupied_port)


class TestConfigLoaderWithEnvironment:
    """Test ConfigLoader environment variable handling."""

    def test_load_env_port(self, monkeypatch):
        """Test loading port from environment."""
        monkeypatch.setenv("KERNELONE_BACKEND_PORT", "8080")

        loader = ConfigLoader()
        snapshot = loader.load()

        assert snapshot.get("server.port") == 8080
        assert snapshot.get_source("server.port") == SourceType.ENV

    def test_load_env_log_level(self, monkeypatch):
        """Test loading log level from environment."""
        monkeypatch.setenv("KERNELONE_LOG_LEVEL", "debug")

        loader = ConfigLoader()
        snapshot = loader.load()

        assert snapshot.get("logging.level") == "debug"

    def test_load_env_cors_origins(self, monkeypatch):
        """Test loading CORS origins from environment."""
        monkeypatch.setenv(
            "KERNELONE_CORS_ORIGINS",
            "http://localhost:3000,http://localhost:3001"
        )

        loader = ConfigLoader()
        snapshot = loader.load()

        origins = snapshot.get("server.cors_origins")
        assert "http://localhost:3000" in origins
        assert "http://localhost:3001" in origins

    def test_env_overrides_default(self, monkeypatch):
        """Test environment overrides defaults."""
        monkeypatch.setenv("KERNELONE_BACKEND_PORT", "9000")

        loader = ConfigLoader()
        snapshot = loader.load(cli_overrides={"server.port": 8080})

        # CLI should win over ENV
        assert snapshot.get("server.port") == 8080
        assert snapshot.get_source("server.port") == SourceType.CLI


class TestBootstrapperConfiguration:
    """Test bootstrapper configuration loading."""

    @pytest.mark.asyncio
    async def test_load_configuration(self):
        """Test configuration loading in bootstrapper."""
        bootstrapper = BackendBootstrapper()

        request = BackendLaunchRequest(
            port=8080,
            workspace=Path.cwd(),
        )

        config = await bootstrapper._load_configuration(request)

        assert isinstance(config, ConfigSnapshot)
        assert config.get("server.port") == 8080

    @pytest.mark.asyncio
    async def test_load_configuration_with_config_snapshot(self):
        """Test loading with existing config snapshot."""
        bootstrapper = BackendBootstrapper()

        existing_config = ConfigSnapshot.merge_sources(
            default={"server.host": "0.0.0.0"}
        )

        request = BackendLaunchRequest(
            port=8080,
            workspace=Path.cwd(),
            config_snapshot=existing_config,
        )

        config = await bootstrapper._load_configuration(request)

        # CLI port should override
        assert config.get("server.port") == 8080
        # Original host should be preserved
        assert config.get("server.host") == "0.0.0.0"


class TestIntegration:
    """Integration tests for bootstrap flow."""

    @pytest.mark.asyncio
    async def test_bootstrap_with_invalid_workspace(self):
        """Test bootstrap fails with invalid workspace."""
        bootstrapper = BackendBootstrapper()

        request = BackendLaunchRequest(
            workspace=Path("/nonexistent/path/that/does/not/exist"),
        )

        result = await bootstrapper.bootstrap(request)

        assert not result.is_success()
        assert "Workspace does not exist" in result.get_error()

    @pytest.mark.asyncio
    async def test_bootstrap_request_validation(self):
        """Test request validation before bootstrap."""
        request = BackendLaunchRequest(
            port=70000,  # Invalid port
            workspace=Path.cwd(),
        )

        validation = request.validate()
        assert not validation.is_valid


if __name__ == "__main__":
    # Run tests without pytest
    print("Running BackendBootstrapper tests...")

    # ConfigLoader tests
    test = TestConfigLoader()
    test.test_load_defaults()
    test.test_load_with_cli_overrides()
    test.test_get_default()
    print("  ✓ ConfigLoader tests passed")

    # BackendLaunchRequest tests
    test = TestBackendLaunchRequest()
    test.test_basic_creation()
    test.test_validation_valid()
    test.test_validation_invalid_port_negative()
    test.test_validation_invalid_port_too_large()
    test.test_with_port()
    test.test_to_uvicorn_options()
    print("  ✓ BackendLaunchRequest tests passed")

    # BackendLaunchResult tests
    test = TestBackendLaunchResult()
    test.test_success_result()
    test.test_failure_result()
    test.test_to_dict()
    test.test_to_electron_event()
    print("  ✓ BackendLaunchResult tests passed")

    # BackendBootstrapper tests
    test = TestBackendBootstrapperBasics()
    test.test_initialization()
    test.test_default_options()
    test.test_find_free_port()
    test.test_is_port_available()
    print("  ✓ BackendBootstrapper tests passed")

    # Async tests
    async def run_async_tests():
        test = TestBootstrapperConfiguration()
        await test.test_load_configuration()
        await test.test_load_configuration_with_config_snapshot()

        test = TestIntegration()
        await test.test_bootstrap_with_invalid_workspace()
        await test.test_bootstrap_request_validation()

    asyncio.run(run_async_tests())
    print("  ✓ Async tests passed")

    print("\n✅ All tests passed!")
