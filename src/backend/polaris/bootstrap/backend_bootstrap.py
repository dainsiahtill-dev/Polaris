"""Backend bootstrap core - unified server initialization.

This module provides BackendBootstrapper, the core service for starting
the Polaris backend server. It encapsulates all startup logic that
was previously scattered in server.py and other entry points.

Example:
    >>> from polaris.bootstrap import BackendBootstrapper
    >>> from application.dto import BackendLaunchRequest
    >>>
    >>> bootstrapper = BackendBootstrapper()
    >>> request = BackendLaunchRequest(port=8080, workspace=Path("."))
    >>> result = await bootstrapper.bootstrap(request)
    >>> print(f"Server started on port {result.port}")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest, BackendLaunchResult
from polaris.cells.policy.workspace_guard.service import SELF_UPGRADE_MODE_ENV, ensure_workspace_target_allowed
from polaris.domain.models.config_snapshot import ConfigSnapshot, SourceType
from polaris.infrastructure.llm.provider_bootstrap import inject_kernelone_provider_runtime

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# BootstrapError is defined in polaris.kernelone.errors for consistency
# Import here for backwards compatibility
from polaris.kernelone.errors import BootstrapError  # noqa: E402


class BackendBootstrapper:
    """Unified backend server bootstrap service.

    This class encapsulates the complete server startup logic including:
    - UTF-8 environment setup
    - Workspace validation
    - Configuration loading and merging
    - Free port selection
    - FastAPI application creation
    - Uvicorn server startup
    - Health checking

    It implements the BackendBootstrapPort interface defined in the
    application layer.

    Attributes:
        _config_loader: ConfigLoader instance for loading settings
        _startup_hooks: List of callable hooks to run during startup
        _shutdown_hooks: List of callable hooks to run during shutdown
    """

    def __init__(self) -> None:
        """Initialize the bootstrapper."""
        self._startup_hooks: list = []
        self._shutdown_hooks: list = []
        self._running_servers: dict[int, Any] = {}
        # BUG-002 fix: distinguish "in progress" from "succeeded" so that
        # a failed attempt does not permanently lock out retries.
        self._bootstrap_in_progress = False
        self._bootstrap_succeeded = False

    async def bootstrap(self, request: BackendLaunchRequest) -> BackendLaunchResult:
        """Bootstrap the backend server.

        NOTE: A successfully started server cannot be started again on the
        same instance; use a new BackendBootstrapper for each server lifecycle.
        Failed attempts can be retried.

        This is the main entry point for starting the server. It performs
        all necessary initialization steps and returns a result object
        containing the server handle and port.

        Args:
            request: Complete launch request with all configuration

        Returns:
            BackendLaunchResult with server handle or error details

        Raises:
            BootstrapError: If bootstrap fails catastrophically
        """
        start_time = time.time()
        # BUG-002: guard against re-entry after success or concurrent calls,
        # but allow retries after a previous failure.
        if self._bootstrap_succeeded:
            raise BootstrapError(
                "Server already running - cannot bootstrap again on the same instance",
                stage="guard_check",
            )
        if self._bootstrap_in_progress:
            raise BootstrapError(
                "Bootstrap already in progress",
                stage="guard_check",
            )
        self._bootstrap_in_progress = True

        stage = "initialization"

        try:
            # Stage 1: Setup UTF-8 environment
            stage = "utf8_setup"
            self._setup_utf8_environment()

            # Stage 2: Validate workspace
            stage = "workspace_validation"
            workspace = request.workspace
            if not workspace.exists():
                return BackendLaunchResult(
                    success=False,
                    error_message=f"Workspace does not exist: {workspace}",
                )

            # Stage 3: Load and merge configuration
            stage = "config_loading"
            config = await self._load_configuration(request)

            policy_error = self._validate_workspace_policy(config)
            if policy_error:
                return BackendLaunchResult(
                    success=False,
                    error_message=policy_error,
                )

            # Stage 4: Setup environment variables
            stage = "env_setup"
            self._setup_environment_variables(config, request)

            # Stage 5: Configure debug tracing
            stage = "debug_setup"
            self._configure_debug_tracing(config)

            # Stage 6: Create FastAPI application
            stage = "app_creation"
            app = await self._create_application(config)

            # Stage 7: Select port
            # BUG-004 fix: use merged config port when request.port is 0 so
            # that persisted / env-var port configuration is respected.
            stage = "port_selection"
            config_port = config.get_typed("server.port", int, 0) or 0
            effective_port = request.port if request.port > 0 else config_port
            port = self._select_port(effective_port)

            # Stage 8: Create server handle
            stage = "server_creation"
            server_handle = await self._create_server(app, request, port)

            # Stage 9: Run startup hooks
            stage = "startup_hooks"
            for hook in self._startup_hooks:
                await hook(config, server_handle)

            # Calculate startup time
            startup_time_ms = int((time.time() - start_time) * 1000)

            # Store running server
            self._running_servers[port] = server_handle

            # BUG-002 fix: only mark succeeded after all startup steps complete.
            self._bootstrap_succeeded = True

            # Emit backend_started event (for Electron compatibility)
            self._emit_startup_event(port, True)

            return BackendLaunchResult(
                success=True,
                port=port,
                process_handle=server_handle,
                startup_time_ms=startup_time_ms,
                config_snapshot=config,
            )

        except (RuntimeError, ValueError) as e:
            logger.exception("bootstrap failed: %s", e)
            # Emit failure event
            self._emit_startup_event(request.port or 0, False, str(e))

            return BackendLaunchResult(
                success=False,
                error_message=f"Bootstrap failed at stage '{stage}': {e!s}",
            )

        finally:
            # BUG-002 fix: always release the in-progress lock so failed
            # attempts can be retried without restarting the process.
            self._bootstrap_in_progress = False

    async def health_check(self, port: int, timeout: float = 5.0) -> bool:
        """Check if backend is healthy and responding.

        Args:
            port: Port number where backend should be listening
            timeout: Maximum time to wait for response

        Returns:
            True if backend responds with 200 OK, False otherwise
        """
        import urllib.request

        def _check_health(url: str, socket_timeout: float) -> int:
            """在线程内执行健康检查，设置socket超时并确保关闭response。"""
            req = urllib.request.Request(url, method="GET")
            # 设置socket超时，避免线程内无限阻塞
            response = urllib.request.urlopen(req, timeout=socket_timeout)
            try:
                return response.getcode()
            finally:
                response.close()

        try:
            url = f"http://127.0.0.1:{port}/health"
            # socket超时稍大于wait_for超时，确保wait_for先触发
            status_code = await asyncio.wait_for(
                asyncio.to_thread(_check_health, url, timeout + 1.0),
                timeout=timeout,
            )
            return status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug("health_check failed for port %d: %s", port, exc)
            return False

    async def shutdown(
        self,
        handle: Any,
        timeout: float = 10.0,
        force: bool = False,
    ) -> bool:
        """Gracefully shutdown the backend.

        Args:
            handle: Server handle from bootstrap()
            timeout: Time to wait for graceful shutdown
            force: If True, force kill after timeout

        Returns:
            True if shutdown successful, False otherwise
        """
        try:
            # Run shutdown hooks
            for hook in self._shutdown_hooks:
                try:
                    await hook()
                except (RuntimeError, ValueError) as e:
                    logger.error("Shutdown hook failed: %s", e)

            # Shutdown the server
            if hasattr(handle, "shutdown"):
                await handle.shutdown()
            elif hasattr(handle, "close"):
                handle.close()

            return True
        except (RuntimeError, ValueError):
            logger.exception("shutdown error: force=%s", force)
            return force

    def add_startup_hook(self, hook: Callable[..., Any]) -> None:
        """Add a hook to run during startup.

        Args:
            hook: Async callable that receives (config, server_handle)
        """
        self._startup_hooks.append(hook)

    def add_shutdown_hook(self, hook: Callable[..., Any]) -> None:
        """Add a hook to run during shutdown.

        Args:
            hook: Async callable
        """
        self._shutdown_hooks.append(hook)

    def _setup_utf8_environment(self) -> None:
        """Setup UTF-8 encoding environment.

        Polaris requires explicit UTF-8 for all text handling.
        """
        # Force UTF-8 mode for Python
        os.environ["PYTHONUTF8"] = "1"
        os.environ["PYTHONIOENCODING"] = "utf-8"

        # Platform-specific settings
        if os.name == "nt":  # Windows
            os.environ["CHCP"] = "65001"

    async def _load_configuration(self, request: BackendLaunchRequest) -> ConfigSnapshot:
        """Load and merge configuration.

        Args:
            request: Launch request with base configuration

        Returns:
            Merged ConfigSnapshot
        """
        from .config_loader import ConfigLoader

        loader = ConfigLoader()

        cli_overrides: dict[str, Any] = {}

        if request.host:
            cli_overrides["server.host"] = request.host
        if request.port != 0:
            cli_overrides["server.port"] = request.port
        if request.log_level:
            cli_overrides["logging.level"] = request.log_level.upper()
        if request.debug_tracing:
            cli_overrides["logging.enable_debug_tracing"] = True
        if request.cors_origins:
            cli_overrides["server.cors_origins"] = request.cors_origins
        if request.explicit_workspace and request.workspace:
            cli_overrides["workspace"] = str(request.workspace)
        if request.self_upgrade_mode is not None:
            cli_overrides["self_upgrade_mode"] = bool(request.self_upgrade_mode)
        if request.ramdisk_root:
            cli_overrides["runtime.ramdisk_root"] = str(request.ramdisk_root)

        if request.config_snapshot:
            return request.config_snapshot.with_override(cli_overrides, SourceType.CLI)

        return loader.load(
            workspace=request.workspace,
            cli_overrides=cli_overrides,
        )

    def _setup_environment_variables(self, config: ConfigSnapshot, request: BackendLaunchRequest) -> None:
        """Setup environment variables from configuration.

        Args:
            config: Configuration snapshot
            request: Launch request
        """
        # Set token (Canonical KERNELONE_* only)
        token = request.token or config.get("security.token", "")
        if token:
            os.environ["KERNELONE_TOKEN"] = token

        self_upgrade_mode = config.get_typed("self_upgrade_mode", bool, False)
        if self_upgrade_mode:
            os.environ[SELF_UPGRADE_MODE_ENV] = "1"
        else:
            os.environ.pop(SELF_UPGRADE_MODE_ENV, None)

        # Set workspace
        resolved_workspace = str(config.get("workspace") or request.workspace or "").strip()
        if resolved_workspace:
            os.environ["KERNELONE_WORKSPACE"] = resolved_workspace
        else:
            os.environ.pop("KERNELONE_WORKSPACE", None)

        # Set ramdisk root if configured
        runtime_root = config.get("runtime.root")
        if runtime_root:
            os.environ["KERNELONE_RUNTIME_ROOT"] = str(runtime_root)
        else:
            os.environ.pop("KERNELONE_RUNTIME_ROOT", None)

        runtime_cache_root = config.get("runtime.cache_root")
        if runtime_cache_root:
            os.environ["KERNELONE_RUNTIME_CACHE_ROOT"] = str(runtime_cache_root)
        else:
            os.environ.pop("KERNELONE_RUNTIME_CACHE_ROOT", None)

        if config.has("runtime.use_ramdisk"):
            state_to_ramdisk = "1" if config.get_typed("runtime.use_ramdisk", bool, True) else "0"
            os.environ["KERNELONE_STATE_TO_RAMDISK"] = state_to_ramdisk
        else:
            os.environ.pop("KERNELONE_STATE_TO_RAMDISK", None)

        ramdisk_root = config.get("runtime.ramdisk_root")
        if ramdisk_root:
            os.environ["KERNELONE_RAMDISK_ROOT"] = str(ramdisk_root)
        else:
            os.environ.pop("KERNELONE_RAMDISK_ROOT", None)

        # NATS configuration
        nats_enabled = "1" if config.get_typed("nats.enabled", bool, True) else "0"
        os.environ["KERNELONE_NATS_ENABLED"] = nats_enabled

        nats_required = "1" if config.get_typed("nats.required", bool, True) else "0"
        os.environ["KERNELONE_NATS_REQUIRED"] = nats_required

        nats_url = str(config.get("nats.url") or "").strip()
        if nats_url:
            os.environ["KERNELONE_NATS_URL"] = nats_url
        else:
            os.environ.pop("KERNELONE_NATS_URL", None)

        nats_user = str(config.get("nats.user") or "").strip()
        if nats_user:
            os.environ["KERNELONE_NATS_USER"] = nats_user
        else:
            os.environ.pop("KERNELONE_NATS_USER", None)

        nats_password = str(config.get("nats.password") or "").strip()
        if nats_password:
            os.environ["KERNELONE_NATS_PASSWORD"] = nats_password
        else:
            os.environ.pop("KERNELONE_NATS_PASSWORD", None)

        nats_connect_timeout = str(float(config.get("nats.connect_timeout_sec") or 3.0))
        os.environ["KERNELONE_NATS_CONNECT_TIMEOUT"] = nats_connect_timeout

        nats_reconnect_wait = str(float(config.get("nats.reconnect_wait_sec") or 1.0))
        os.environ["KERNELONE_NATS_RECONNECT_WAIT"] = nats_reconnect_wait

        nats_max_reconnect = str(int(config.get("nats.max_reconnect_attempts") or -1))
        os.environ["KERNELONE_NATS_MAX_RECONNECT"] = nats_max_reconnect

        nats_stream_name = str(config.get("nats.stream_name") or "").strip()
        if nats_stream_name:
            os.environ["KERNELONE_NATS_STREAM_NAME"] = nats_stream_name
        else:
            os.environ.pop("KERNELONE_NATS_STREAM_NAME", None)

    def _configure_debug_tracing(self, config: ConfigSnapshot) -> None:
        """Configure debug tracing if enabled.

        Args:
            config: Configuration snapshot
        """
        enable_tracing = config.get("logging.enable_debug_tracing", False)
        tracing_value = "1" if enable_tracing else "0"
        os.environ["KERNELONE_DEBUG_TRACING"] = tracing_value

    def _validate_workspace_policy(self, config: ConfigSnapshot) -> str:
        workspace = str(config.get("workspace") or "").strip()
        if not workspace:
            return ""
        try:
            ensure_workspace_target_allowed(
                workspace,
                self_upgrade_mode=config.get_typed("self_upgrade_mode", bool, False),
            )
        except ValueError as exc:
            return str(exc)
        return ""

    async def _create_application(self, config: ConfigSnapshot) -> Any:
        """Create FastAPI application.

        Args:
            config: Configuration snapshot

        Returns:
            FastAPI application instance
        """
        # Import here to avoid circular dependencies
        try:
            # Ensure KernelFileSystem adapter is available before importing app modules.
            # Some modules access KFS during import-time initialization.
            from polaris.infrastructure.storage import LocalFileSystemAdapter
            from polaris.kernelone.fs import set_default_adapter

            set_default_adapter(LocalFileSystemAdapter())
            inject_kernelone_provider_runtime()

            from polaris.bootstrap.config import Settings
            from polaris.delivery.http.app_factory import create_app

            # Materialize the app settings from the merged configuration snapshot
            # so workspace / llm / runtime / workflow settings stay aligned with
            # the bootstrap-selected configuration.
            mutable = config.to_mutable_dict()
            # BUG-001 fix: empty string ramdisk_root must become None before
            # reaching RuntimeConfig.validate_path, otherwise Path("").resolve()
            # silently sets it to CWD instead of disabling ramdisk usage.
            runtime_cfg = mutable.get("runtime")
            if isinstance(runtime_cfg, dict) and runtime_cfg.get("ramdisk_root") == "":
                runtime_cfg["ramdisk_root"] = None
            settings = Settings(**mutable)

            # Create app
            app = create_app(settings)
            return app

        except ImportError as e:
            raise BootstrapError(
                f"Failed to import FastAPI components: {e}",
                stage="app_creation",
            ) from e

    def _select_port(self, preferred_port: int = 0) -> int:
        """Select an available port.

        Args:
            preferred_port: Preferred port (0 for auto-selection)

        Returns:
            Available port number
        """
        if preferred_port and preferred_port > 0 and self._is_port_available(preferred_port):
            return preferred_port

        # Auto-select port
        return self._find_free_port()

    def _is_port_available(self, port: int) -> bool:
        """Check if a port is available.

        Args:
            port: Port number to check

        Returns:
            True if port is available
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return True
        except OSError:
            return False

    def _find_free_port(self) -> int:
        """Find a free port.

        Returns:
            Free port number
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    async def _create_server(
        self,
        app: Any,
        request: BackendLaunchRequest,
        port: int,
    ) -> Any:
        """Create the server handle.

        Args:
            app: FastAPI application
            request: Launch request
            port: Port to listen on

        Returns:
            Server handle
        """
        # Create a server handle that can be used to control the server
        # This is a wrapper around the uvicorn server
        from .uvicorn_server import UvicornServerHandle

        host = request.host or "127.0.0.1"
        log_level = request.log_level or "info"

        # BUG-003 fix: _select_port uses a TOCTOU best-effort check; the port
        # may be taken by the time uvicorn actually binds.  Retry with a newly
        # selected free port up to 3 times before propagating the error.
        last_error: Exception | None = None
        for attempt in range(3):
            current_port = port if attempt == 0 else self._find_free_port()
            try:
                handle = UvicornServerHandle(
                    app=app,
                    host=host,
                    port=current_port,
                    log_level=log_level,
                )
                await handle.start()
                # Return the handle; caller records the actual port from
                # _select_port return value, but we expose the real port via
                # the handle itself if UvicornServerHandle surfaces it.
                return handle
            except OSError as exc:
                last_error = exc
                logger.warning(
                    "Port %d bind failed (attempt %d/3): %s",
                    current_port,
                    attempt + 1,
                    exc,
                )

        raise BootstrapError(
            f"Port bind failed after 3 attempts: {last_error}",
            stage="server_creation",
        )

    def _emit_startup_event(self, port: int, success: bool, error: str = "") -> None:
        """Emit backend_started event for Electron compatibility.

        Args:
            port: Server port
            success: Whether startup succeeded
            error: Error message if failed
        """
        event = {
            "event": "backend_started" if success else "backend_failed",
            "port": port,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if error:
            event["error"] = error

        payload = json.dumps(event, ensure_ascii=False)
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        logger.info(payload)

    def get_default_options(self) -> dict[str, Any]:
        """Get default bootstrap options.

        Returns:
            Dictionary of default options
        """
        return {
            "host": "127.0.0.1",
            "port": 0,  # Auto-select
            "log_level": "info",
            "cors_origins": [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ],
        }


# Convenience function for simple use cases
async def bootstrap_backend(
    workspace: Path | None = None,
    port: int = 0,
    **kwargs: Any,
) -> BackendLaunchResult:
    """Bootstrap backend with minimal configuration.

    Args:
        workspace: Workspace path
        port: Server port (0 for auto-select)
        **kwargs: Additional configuration

    Returns:
        BackendLaunchResult
    """
    request = BackendLaunchRequest(
        workspace=workspace or Path.cwd(),
        port=port,
        **kwargs,
    )

    bootstrapper = BackendBootstrapper()
    return await bootstrapper.bootstrap(request)


if __name__ == "__main__":
    # Test bootstrap
    logger.info("Testing BackendBootstrapper...")

    async def test() -> None:
        bootstrapper = BackendBootstrapper()

        # Test defaults
        defaults = bootstrapper.get_default_options()
        logger.info("Default host: %s", defaults["host"])
        logger.info("Default port: %s", defaults["port"])

        # Test port selection
        free_port = bootstrapper._find_free_port()
        logger.info("Found free port: %s", free_port)

        logger.info("BackendBootstrapper tests passed!")

    asyncio.run(test())
