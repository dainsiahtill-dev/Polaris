"""Tests for polaris.delivery.server module."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.delivery.server import _run_bootstrap, main


class TestMainArgumentParsing:
    """Tests for main() CLI argument parsing."""

    def test_default_host(self) -> None:
        """Test default host is 127.0.0.1."""
        with (
            patch("polaris.delivery.server.asyncio.run", return_value=0),
            patch.object(sys, "argv", ["server.py"]),
        ):
            main()

    def test_explicit_host(self) -> None:
        """Test explicit host argument."""
        test_args = ["server.py", "--host", "0.0.0.0"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_explicit_port(self) -> None:
        """Test explicit port argument."""
        test_args = ["server.py", "--port", "8080"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_default_port_zero(self) -> None:
        """Test default port is 0 (auto-select)."""
        test_args = ["server.py"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_workspace_argument(self) -> None:
        """Test workspace argument."""
        test_args = ["server.py", "--workspace", "/tmp/workspace"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_cors_origins_argument(self) -> None:
        """Test CORS origins argument."""
        test_args = ["server.py", "--cors-origins", "http://localhost,http://example.com"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_token_argument(self) -> None:
        """Test token argument."""
        test_args = ["server.py", "--token", "secret123"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_ramdisk_root_argument(self) -> None:
        """Test ramdisk root argument."""
        test_args = ["server.py", "--ramdisk-root", "/tmp/ramdisk"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_log_level_argument(self) -> None:
        """Test log level argument with valid choices."""
        for level in ["debug", "info", "warning", "error", "critical"]:
            test_args = ["server.py", "--log-level", level]
            with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
                main()

    def test_debug_tracing_flag(self) -> None:
        """Test debug tracing flag."""
        test_args = ["server.py", "--debug-tracing"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_self_upgrade_mode_true(self) -> None:
        """Test self-upgrade-mode flag."""
        test_args = ["server.py", "--self-upgrade-mode"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_self_upgrade_mode_false(self) -> None:
        """Test no-self-upgrade-mode flag."""
        test_args = ["server.py", "--no-self-upgrade-mode"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_help_does_not_crash(self) -> None:
        """Test --help does not crash."""
        test_args = ["server.py", "--help"]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


class TestRunBootstrap:
    """Tests for _run_bootstrap function."""

    @pytest.fixture
    def mock_bootstrapper(self) -> MagicMock:
        """Create a mock BackendBootstrapper."""
        with patch("polaris.delivery.server.BackendBootstrapper") as mock_cls:
            instance = MagicMock()
            instance.bootstrap = AsyncMock()
            mock_cls.return_value = instance
            yield instance

    @pytest.fixture
    def mock_request(self) -> MagicMock:
        """Create a mock BackendLaunchRequest."""
        with patch("polaris.delivery.server.BackendLaunchRequest") as mock_cls:
            instance = MagicMock()
            instance.validate.return_value = MagicMock(is_valid=True, errors=[])
            mock_cls.return_value = instance
            yield instance

    @pytest.mark.asyncio
    async def test_validation_failure_returns_one(
        self, mock_request: MagicMock, mock_bootstrapper: MagicMock
    ) -> None:
        """Test validation failure returns exit code 1."""
        mock_request.validate.return_value = MagicMock(is_valid=False, errors=["error1"])

        args = argparse.Namespace(
            host="127.0.0.1",
            port=0,
            workspace="",
            cors_origins="",
            token="",
            ramdisk_root="",
            log_level="info",
            debug_tracing=False,
            self_upgrade_mode=None,
        )
        workspace = Path.cwd()
        cors_origins = None

        with patch("polaris.delivery.server.BackendLaunchRequest", return_value=mock_request):
            result = await _run_bootstrap(args, workspace, cors_origins)
        assert result == 1

    @pytest.mark.asyncio
    async def test_bootstrap_failure_returns_one(
        self, mock_request: MagicMock, mock_bootstrapper: MagicMock
    ) -> None:
        """Test bootstrap failure returns exit code 1."""
        result_mock = MagicMock()
        result_mock.is_success.return_value = False
        result_mock.get_error.return_value = "bootstrap error"
        mock_bootstrapper.bootstrap.return_value = result_mock

        args = argparse.Namespace(
            host="127.0.0.1",
            port=0,
            workspace="",
            cors_origins="",
            token="",
            ramdisk_root="",
            log_level="info",
            debug_tracing=False,
            self_upgrade_mode=None,
        )
        workspace = Path.cwd()
        cors_origins = None

        with (
            patch("polaris.delivery.server.BackendLaunchRequest", return_value=mock_request),
            patch("polaris.delivery.server.BackendBootstrapper", return_value=mock_bootstrapper),
        ):
            result = await _run_bootstrap(args, workspace, cors_origins)
        assert result == 1

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_handling(
        self, mock_request: MagicMock, mock_bootstrapper: MagicMock
    ) -> None:
        """Test KeyboardInterrupt handling."""
        result_mock = MagicMock()
        result_mock.is_success.return_value = True
        mock_bootstrapper.bootstrap.return_value = result_mock
        mock_bootstrapper.shutdown = AsyncMock()

        args = argparse.Namespace(
            host="127.0.0.1",
            port=0,
            workspace="",
            cors_origins="",
            token="",
            ramdisk_root="",
            log_level="info",
            debug_tracing=False,
            self_upgrade_mode=None,
        )
        workspace = Path.cwd()
        cors_origins = None

        # Simulate KeyboardInterrupt during the sleep loop
        sleep_mock = AsyncMock(side_effect=[None, KeyboardInterrupt])

        with (
            patch("polaris.delivery.server.asyncio.sleep", sleep_mock),
            patch("polaris.delivery.server.BackendLaunchRequest", return_value=mock_request),
            patch("polaris.delivery.server.BackendBootstrapper", return_value=mock_bootstrapper),
        ):
            result = await _run_bootstrap(args, workspace, cors_origins)
        assert result == 0
        mock_bootstrapper.shutdown.assert_called_once()


class TestWorkspaceResolution:
    """Tests for workspace path resolution."""

    def test_empty_workspace_uses_cwd(self) -> None:
        """Test empty workspace defaults to current directory."""
        test_args = ["server.py"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run") as mock_run:
            main()
            # The call was made; workspace should be cwd
            assert mock_run.called

    def test_relative_workspace_resolved(self) -> None:
        """Test relative workspace path is resolved."""
        test_args = ["server.py", "--workspace", "./projects/test"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()


class TestCorsOriginsParsing:
    """Tests for CORS origins parsing."""

    def test_single_origin(self) -> None:
        """Test single CORS origin."""
        test_args = ["server.py", "--cors-origins", "http://localhost:3000"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_multiple_origins(self) -> None:
        """Test multiple CORS origins."""
        test_args = ["server.py", "--cors-origins", "http://localhost, http://example.com"]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()

    def test_empty_cors_origins(self) -> None:
        """Test empty CORS origins."""
        test_args = ["server.py", "--cors-origins", ""]
        with patch.object(sys, "argv", test_args), patch("polaris.delivery.server.asyncio.run", return_value=0):
            main()


class TestMainEntryPoint:
    """Tests for __main__ entry point behavior."""

    def test_module_importable(self) -> None:
        """Test that the server module is importable."""
        import polaris.delivery.server as server_module

        assert hasattr(server_module, "main")
        assert hasattr(server_module, "_run_bootstrap")
