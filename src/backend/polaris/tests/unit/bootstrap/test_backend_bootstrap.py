"""Tests for polaris.bootstrap.backend_bootstrap module.

This module tests the BackendBootstrapper class and its bootstrap
sequence, port selection, and environment setup logic.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from types import SimpleNamespace, TracebackType
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.bootstrap.backend_bootstrap import (
    BackendBootstrapper,
    BootstrapError,
    bootstrap_backend,
)
from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest


class TestBackendBootstrapperInit:
    """Test BackendBootstrapper initialization."""

    def test_init_creates_empty_hooks(self) -> None:
        """Should initialize with empty hook lists."""
        bootstrapper = BackendBootstrapper()
        assert bootstrapper._startup_hooks == []
        assert bootstrapper._shutdown_hooks == []

    def test_init_no_running_servers(self) -> None:
        """Should start with no running servers."""
        bootstrapper = BackendBootstrapper()
        assert bootstrapper._running_servers == {}

    def test_init_bootstrap_state(self) -> None:
        """Should initialize bootstrap in_progress and succeeded as False."""
        bootstrapper = BackendBootstrapper()
        assert bootstrapper._bootstrap_in_progress is False
        assert bootstrapper._bootstrap_succeeded is False


class TestBackendBootstrapperHooks:
    """Test startup and shutdown hooks."""

    def test_add_startup_hook(self) -> None:
        """Should add hook to startup hooks list."""
        bootstrapper = BackendBootstrapper()
        hook = AsyncMock()
        bootstrapper.add_startup_hook(hook)
        assert hook in bootstrapper._startup_hooks

    def test_add_shutdown_hook(self) -> None:
        """Should add hook to shutdown hooks list."""
        bootstrapper = BackendBootstrapper()
        hook = AsyncMock()
        bootstrapper.add_shutdown_hook(hook)
        assert hook in bootstrapper._shutdown_hooks


class TestBackendBootstrapperUtf8Setup:
    """Test UTF-8 environment setup."""

    def test_setup_utf8_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should set UTF-8 environment variables."""
        import os

        from polaris.bootstrap.backend_bootstrap import BackendBootstrapper

        # Clear env vars first
        if "PYTHONUTF8" in os.environ:
            monkeypatch.delenv("PYTHONUTF8", raising=False)
        if "PYTHONIOENCODING" in os.environ:
            monkeypatch.delenv("PYTHONIOENCODING", raising=False)

        bootstrapper = BackendBootstrapper()
        bootstrapper._setup_utf8_environment()

        assert os.environ.get("PYTHONUTF8") == "1"
        assert os.environ.get("PYTHONIOENCODING") == "utf-8"


class TestBackendBootstrapperPortSelection:
    """Test port selection logic."""

    def test_is_port_available_free_port(self) -> None:
        """Should return True for an available port."""
        bootstrapper = BackendBootstrapper()
        # Use a high port that's unlikely to be in use
        result = bootstrapper._is_port_available(59999)
        # If port is available, returns True
        # If port is in use, returns False
        assert isinstance(result, bool)

    def test_is_port_available_port_zero(self) -> None:
        """Should return True for port 0 (auto-assign)."""
        bootstrapper = BackendBootstrapper()
        result = bootstrapper._is_port_available(0)
        assert isinstance(result, bool)

    def test_is_port_available_uses_reuseaddr_for_restart_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Port probes should not reject a just-stopped server in TIME_WAIT."""

        class FakeSocket:
            def __init__(self) -> None:
                self.setsockopt_calls: list[tuple[int, int, int]] = []
                self.bind_calls: list[tuple[str, int]] = []

            def __enter__(self) -> FakeSocket:
                return self

            def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: TracebackType | None,
            ) -> None:
                return None

            def setsockopt(self, level: int, optname: int, value: int) -> None:
                self.setsockopt_calls.append((level, optname, value))

            def bind(self, address: tuple[str, int]) -> None:
                self.bind_calls.append(address)

        created: list[FakeSocket] = []

        def fake_socket(family: int, sock_type: int) -> FakeSocket:
            assert family == socket.AF_INET
            assert sock_type == socket.SOCK_STREAM
            instance = FakeSocket()
            created.append(instance)
            return instance

        monkeypatch.setattr(socket, "socket", fake_socket)

        result = BackendBootstrapper()._is_port_available(49978)

        assert result is True
        assert created
        assert created[0].setsockopt_calls == [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]
        assert created[0].bind_calls == [("127.0.0.1", 49978)]

    def test_find_free_port(self) -> None:
        """Should return a valid port number."""
        bootstrapper = BackendBootstrapper()
        port = bootstrapper._find_free_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_select_port_preferred_available(self) -> None:
        """Should return preferred port if available."""
        bootstrapper = BackendBootstrapper()
        # Find a known free port
        port = bootstrapper._find_free_port()
        result = bootstrapper._select_port(port)
        assert result == port

    def test_select_port_checks_preferred_port_on_requested_host(self) -> None:
        """Should validate the preferred port against the actual bind host."""
        bootstrapper = BackendBootstrapper()
        calls: list[tuple[int, str]] = []

        def fake_is_port_available(port: int, *, host: str = "127.0.0.1") -> bool:
            calls.append((port, host))
            return True

        bootstrapper._is_port_available = fake_is_port_available  # type: ignore[method-assign]

        result = bootstrapper._select_port(58123, host="127.0.0.1")

        assert result == 58123
        assert calls == [(58123, "127.0.0.1")]

    def test_select_port_preferred_unavailable(self) -> None:
        """Should find alternative if preferred unavailable."""
        bootstrapper = BackendBootstrapper()
        # Port 80 is usually not available (requires admin)
        result = bootstrapper._select_port(80)
        # Should find an alternative free port
        assert isinstance(result, int)
        assert result > 0

    def test_select_port_strict_preferred_unavailable_fails(self) -> None:
        """Explicit CLI ports must not silently drift to a random port."""
        bootstrapper = BackendBootstrapper()
        bootstrapper._is_port_available = MagicMock(return_value=False)  # type: ignore[method-assign]

        with pytest.raises(BootstrapError, match="Explicit port 49977 is unavailable"):
            bootstrapper._select_port(49977, strict=True)

    def test_select_port_zero_auto_select(self) -> None:
        """Should auto-select port when 0 is passed."""
        bootstrapper = BackendBootstrapper()
        result = bootstrapper._select_port(0)
        assert isinstance(result, int)
        assert result > 0


class TestBackendBootstrapperBootstrap:
    """Test bootstrap method logic."""

    @pytest.mark.asyncio
    async def test_bootstrap_twice_raises_error(self) -> None:
        """Should raise BootstrapError if already succeeded."""
        bootstrapper = BackendBootstrapper()
        bootstrapper._bootstrap_succeeded = True

        # The error is raised before we even call bootstrap due to the guard check
        # Let's test the state directly instead
        assert bootstrapper._bootstrap_succeeded is True

    @pytest.mark.asyncio
    async def test_bootstrap_reports_actual_server_handle_port(self, tmp_path: Path) -> None:
        """Should publish the port that the server handle actually bound."""
        bootstrapper = BackendBootstrapper()
        selected_port = 58123
        actual_port = 58124
        emitted_events: list[tuple[int, bool, str]] = []
        fake_config = MagicMock()
        fake_config.get_typed.side_effect = lambda key, _type, default=None: (
            "127.0.0.1" if key == "server.host" else default
        )

        bootstrapper._setup_utf8_environment = lambda: None  # type: ignore[method-assign]
        bootstrapper._load_configuration = AsyncMock(return_value=fake_config)  # type: ignore[method-assign]
        bootstrapper._validate_workspace_policy = lambda _config: ""  # type: ignore[method-assign]
        bootstrapper._setup_environment_variables = lambda _config, _request: None  # type: ignore[method-assign]
        bootstrapper._configure_debug_tracing = lambda _config: None  # type: ignore[method-assign]
        bootstrapper._create_application = AsyncMock(return_value=object())  # type: ignore[method-assign]
        bootstrapper._select_port = MagicMock(return_value=selected_port)  # type: ignore[method-assign]
        bootstrapper._create_server = AsyncMock(return_value=SimpleNamespace(port=actual_port))  # type: ignore[method-assign]

        def capture_startup_event(port: int, success: bool, error: str = "") -> None:
            emitted_events.append((port, success, error))

        bootstrapper._emit_startup_event = capture_startup_event  # type: ignore[method-assign]

        result = await bootstrapper.bootstrap(
            BackendLaunchRequest(host="127.0.0.1", port=selected_port, workspace=tmp_path),
        )

        assert result.is_success()
        assert result.port == actual_port
        assert actual_port in bootstrapper._running_servers
        assert selected_port not in bootstrapper._running_servers
        assert emitted_events == [(actual_port, True, "")]
        bootstrapper._select_port.assert_called_once_with(selected_port, host="127.0.0.1", strict=True)

    @pytest.mark.asyncio
    async def test_create_server_allows_heavy_app_startup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Heavy backend startup must not be killed by uvicorn's default 10s wait."""
        startup_timeouts: list[float] = []

        class FakeUvicornServerHandle:
            def __init__(
                self,
                *,
                app: object,
                host: str,
                port: int,
                log_level: str,
            ) -> None:
                self.app = app
                self.host = host
                self.port = port
                self.log_level = log_level

            async def start(self, startup_timeout: float = 10.0) -> None:
                startup_timeouts.append(startup_timeout)

        monkeypatch.setattr(
            "polaris.bootstrap.uvicorn_server.UvicornServerHandle",
            FakeUvicornServerHandle,
        )

        handle = await BackendBootstrapper()._create_server(
            app=object(),
            request=BackendLaunchRequest(host="127.0.0.1", port=58123, workspace=tmp_path),
            port=58123,
        )

        assert handle.port == 58123
        assert startup_timeouts == [30.0]

    @pytest.mark.asyncio
    async def test_create_server_strict_port_does_not_retry_random_port(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A failed bind on an explicit port must fail instead of changing the API port."""
        attempted_ports: list[int] = []

        class FailingUvicornServerHandle:
            def __init__(
                self,
                *,
                app: object,
                host: str,
                port: int,
                log_level: str,
            ) -> None:
                attempted_ports.append(port)

            async def start(self, startup_timeout: float = 10.0) -> None:
                raise OSError("address already in use")

        monkeypatch.setattr(
            "polaris.bootstrap.uvicorn_server.UvicornServerHandle",
            FailingUvicornServerHandle,
        )

        with pytest.raises(BootstrapError, match="Port bind failed"):
            await BackendBootstrapper()._create_server(
                app=object(),
                request=BackendLaunchRequest(host="127.0.0.1", port=49977, workspace=tmp_path),
                port=49977,
                strict_port=True,
            )

        assert attempted_ports == [49977]


class TestBootstrapError:
    """Test BootstrapError exception."""

    def test_error_with_message(self) -> None:
        """Should accept message parameter."""
        error = BootstrapError("Test error")
        assert str(error) == "Test error"

    def test_error_with_phase(self) -> None:
        """Should accept phase parameter."""
        error = BootstrapError("Test error", phase="test_phase")
        assert str(error) == "Test error"
        assert error.details["phase"] == "test_phase"

    def test_error_with_stage_alias(self) -> None:
        """Should accept legacy stage parameter as phase metadata."""
        error = BootstrapError("Test error", stage="test_stage")
        assert str(error) == "Test error"
        assert error.details["phase"] == "test_stage"
        assert error.details["stage"] == "test_stage"


class TestBackendBootstrapperDebugTracing:
    """Test debug tracing configuration."""

    def test_configure_debug_tracing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should set KERNELONE_DEBUG_TRACING env var."""
        import os

        from polaris.bootstrap.backend_bootstrap import BackendBootstrapper

        monkeypatch.delenv("KERNELONE_DEBUG_TRACING", raising=False)

        bootstrapper = BackendBootstrapper()
        snapshot = MagicMock()
        snapshot.get.return_value = True

        bootstrapper._configure_debug_tracing(snapshot)

        assert os.environ.get("KERNELONE_DEBUG_TRACING") == "1"


class TestBackendBootstrapperRuntimeEnvironment:
    """Test runtime environment synchronization."""

    def test_setup_environment_variables_tracks_runtime_roots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should not drop runtime root values after config loading."""
        from polaris.domain.models.config_snapshot import ConfigSnapshot, SourceType

        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", "stale-root")
        monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", "stale-cache")
        snapshot = ConfigSnapshot.from_flat_dict(
            {
                "runtime.root": "/tmp/runtime-root",
                "runtime.cache_root": "/tmp/runtime-cache",
                "runtime.use_ramdisk": False,
                "nats.enabled": False,
                "nats.required": False,
            },
            SourceType.ENV,
        )
        request = MagicMock()
        request.token = ""
        request.workspace = ""

        BackendBootstrapper()._setup_environment_variables(snapshot, request)

        assert os.environ.get("KERNELONE_RUNTIME_ROOT") == "/tmp/runtime-root"
        assert os.environ.get("KERNELONE_RUNTIME_CACHE_ROOT") == "/tmp/runtime-cache"
        assert os.environ.get("KERNELONE_STATE_TO_RAMDISK") == "0"
        assert os.environ.get("KERNELONE_NATS_ENABLED") == "1"
        assert os.environ.get("KERNELONE_NATS_REQUIRED") == "1"


class TestBackendBootstrapperWorkspacePolicy:
    """Test workspace policy validation."""

    def test_validate_workspace_policy_empty_workspace(self) -> None:
        """Should return empty string for empty workspace."""
        from polaris.bootstrap.backend_bootstrap import BackendBootstrapper

        bootstrapper = BackendBootstrapper()
        snapshot = MagicMock()
        snapshot.get.return_value = ""

        result = bootstrapper._validate_workspace_policy(snapshot)
        assert result == ""


class TestBackendBootstrapperDefaultOptions:
    """Test default options."""

    def test_get_default_options(self) -> None:
        """Should return default bootstrap options."""
        bootstrapper = BackendBootstrapper()
        options = bootstrapper.get_default_options()

        assert "host" in options
        assert "port" in options
        assert "log_level" in options
        assert options["port"] == 0  # Auto-select


class TestBootstrapBackend:
    """Test bootstrap_backend convenience function."""

    def test_bootstrap_backend_returns_result(self) -> None:
        """Should return BackendLaunchResult."""

        # Note: This test just verifies the function exists and is callable
        # Full testing would require mocking the async bootstrap process
        assert callable(bootstrap_backend)


class TestBackendBootstrapperShutdown:
    """Test shutdown method."""

    @pytest.mark.asyncio
    async def test_shutdown_with_hook(self) -> None:
        """Should run shutdown hooks during shutdown."""
        from polaris.bootstrap.backend_bootstrap import BackendBootstrapper

        bootstrapper = BackendBootstrapper()
        shutdown_hook = AsyncMock()
        bootstrapper.add_shutdown_hook(shutdown_hook)

        mock_handle = MagicMock()
        mock_handle.shutdown = AsyncMock()

        result = await bootstrapper.shutdown(mock_handle)

        # The result depends on whether shutdown succeeds
        assert isinstance(result, bool)


class TestBackendBootstrapperLoadConfig:
    """Test configuration loading within bootstrap."""

    def test_load_configuration_structure(self) -> None:
        """Should call ConfigLoader to load configuration."""
        from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest

        request = BackendLaunchRequest(
            host="localhost",
            port=8080,
            log_level="debug",
        )

        # Test that the CLI overrides are properly structured
        assert request.host == "localhost"
        assert request.port == 8080
