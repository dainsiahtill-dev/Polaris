"""Uvicorn server handle for backend bootstrap.

This module provides UvicornServerHandle, an async wrapper around
uvicorn that integrates with the BackendBootstrapper.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class UvicornServerHandle:
    """Handle to a running uvicorn server.

    This class wraps a uvicorn server instance and provides
    async methods for starting, stopping, and monitoring.

    Attributes:
        app: FastAPI application
        host: Bind host
        port: Bind port
        log_level: Logging level
        _server: Uvicorn server instance
        _task: Asyncio task running the server
    """

    def __init__(
        self,
        app: Any,
        host: str = "127.0.0.1",
        port: int = 8000,
        log_level: str = "info",
    ) -> None:
        """Initialize server handle.

        Args:
            app: FastAPI application
            host: Bind host
            port: Bind port
            log_level: Logging level
        """
        self.app = app
        self.host = host
        self.port = port
        self.log_level = log_level
        self._server: Any | None = None
        self._task: asyncio.Task | None = None
        self._config: Any | None = None

    async def start(self) -> None:
        """Start the uvicorn server.

        This method starts the server in a background task.
        """
        import uvicorn

        # Create config
        self._config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level=self.log_level.lower(),
            access_log=self.log_level.lower() == "debug",
        )

        # Create server
        self._server = uvicorn.Server(self._config)

        # Run in background task
        self._task = asyncio.create_task(self._server.serve())

        # Wait a moment for server to start
        await asyncio.sleep(0.5)

    async def stop(self, timeout: float = 10.0) -> bool:
        """Stop the server gracefully.

        Args:
            timeout: Time to wait for graceful shutdown

        Returns:
            True if stopped successfully
        """
        if self._server:
            try:
                self._server.should_exit = True
                if self._task:
                    await asyncio.wait_for(self._task, timeout=timeout)
                return True
            except asyncio.TimeoutError:
                return False
        return True

    async def shutdown(self) -> None:
        """Force shutdown the server."""
        if self._server:
            self._server.should_exit = True
            if self._task:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._task is not None and not self._task.done()

    @property
    def pid(self) -> int | None:
        """Get process ID (for compatibility)."""
        import os

        return os.getpid()
