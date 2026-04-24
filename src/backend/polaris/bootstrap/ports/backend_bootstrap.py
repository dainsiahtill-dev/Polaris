"""Port interface for backend server bootstrap operations.

This module defines the contract for bootstrapping the Polaris
backend server. Different implementations can use different ASGI
servers (uvicorn, hypercorn, daphne).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

# Handle imports for both runtime and standalone usage
if TYPE_CHECKING:
    from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest, BackendLaunchResult
else:
    try:
        from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest, BackendLaunchResult
    except ImportError:
        # Fallback for development
        BackendLaunchRequest = Any
        BackendLaunchResult = Any


@runtime_checkable
class BackendBootstrapPort(Protocol):
    """Port for bootstrapping the backend server.

    This protocol defines the interface for starting, monitoring, and
    shutting down the backend server process. Implementations can use
    different ASGI servers or deployment strategies.

    Example:
        class UvicornBootstrapAdapter:
            async def bootstrap(self, request: BackendLaunchRequest) -> BackendLaunchResult:
                config = uvicorn.Config(...)
                server = uvicorn.Server(config)
                await server.serve()
                return BackendLaunchResult(success=True, ...)
    """

    async def bootstrap(self, request: BackendLaunchRequest) -> BackendLaunchResult:
        """Bootstrap the backend server.

        Args:
            request: Complete launch request with configuration

        Returns:
            BackendLaunchResult with process handle or error details

        Raises:
            BackendBootstrapError: If bootstrap fails catastrophically
        """
        ...

    async def health_check(self, port: int, timeout: float = 5.0) -> bool:
        """Check if backend is healthy and responding.

        Args:
            port: Port number where backend should be listening
            timeout: Maximum time to wait for response in seconds

        Returns:
            True if backend responds with 200 OK, False otherwise
        """
        ...

    async def shutdown(
        self,
        handle: Any,  # ProcessHandle
        timeout: float = 10.0,
        force: bool = False,
    ) -> bool:
        """Gracefully shutdown the backend.

        Args:
            handle: Process handle from bootstrap()
            timeout: Time to wait for graceful shutdown in seconds
            force: If True, force kill after timeout

        Returns:
            True if shutdown successful, False otherwise
        """
        ...

    def get_default_options(self) -> dict[str, Any]:
        """Get default bootstrap options.

        Returns:
            Dictionary of default configuration options
        """
        ...


# BackendBootstrapError is defined in polaris.kernelone.errors for consistency
# Import here for backwards compatibility
from polaris.kernelone.errors import BackendBootstrapError  # noqa: E402, F401

# Type alias for the port
BootstrapPort = BackendBootstrapPort
