"""Port interface for process execution operations.

This module defines the contract for managing subprocess lifecycle.
Different implementations can support local execution, remote execution,
or containerized execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from polaris.cells.orchestration.workflow_runtime.public.process_launch import (
        ProcessLaunchRequest,
        ProcessLaunchResult,
    )
else:
    try:
        from polaris.cells.orchestration.workflow_runtime.public.process_launch import (
            ProcessLaunchRequest,
            ProcessLaunchResult,
        )
    except ImportError:
        ProcessLaunchRequest = Any
        ProcessLaunchResult = Any


class ProcessStatus(Enum):
    """Status of a managed process."""

    PENDING = "pending"  # Launch requested but not started
    STARTING = "starting"  # In the process of starting
    RUNNING = "running"  # Process is running
    STOPPING = "stopping"  # In the process of stopping
    COMPLETED = "completed"  # Process exited successfully
    FAILED = "failed"  # Process exited with error
    TERMINATED = "terminated"  # Process was forcefully terminated
    UNKNOWN = "unknown"  # Status cannot be determined


@dataclass(frozen=True)
class ProcessHandle:
    """Handle to a managed process.

    This is an opaque reference that implementations use to track
    processes. Callers should treat this as an opaque token.
    """

    process_id: str
    pid: int | None = None
    name: str = ""
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@runtime_checkable
class ProcessRunnerPort(Protocol):
    """Port for running subprocesses.

    Abstracts subprocess management to enable:
    - Local process execution (subprocess)
    - Remote process execution (SSH, Docker)
    - Mocked execution for testing

    Example:
        class SubprocessRunnerAdapter:
            async def launch(self, request: ProcessLaunchRequest) -> ProcessLaunchResult:
                proc = await asyncio.create_subprocess_exec(*request.command)
                return ProcessLaunchResult(success=True, pid=proc.pid, ...)
    """

    async def launch(self, request: ProcessLaunchRequest) -> ProcessLaunchResult:
        """Launch a subprocess.

        Args:
            request: Process launch request with all parameters

        Returns:
            ProcessLaunchResult with handle and status
        """
        ...

    async def terminate(
        self,
        handle: ProcessHandle,
        timeout: float = 5.0,
    ) -> bool:
        """Terminate a running process.

        Args:
            handle: Process handle from launch()
            timeout: Time to wait for graceful termination in seconds

        Returns:
            True if terminated successfully
        """
        ...

    async def kill(self, handle: ProcessHandle) -> bool:
        """Forcefully kill a process.

        Args:
            handle: Process handle from launch()

        Returns:
            True if killed successfully
        """
        ...

    async def status(self, handle: ProcessHandle) -> ProcessStatus:
        """Get current process status.

        Args:
            handle: Process handle from launch()

        Returns:
            Current process status
        """
        ...

    async def wait_for(
        self,
        handle: ProcessHandle,
        timeout: float | None = None,
    ) -> bool:
        """Wait for process to complete.

        Args:
            handle: Process handle from launch()
            timeout: Maximum time to wait (None = forever)

        Returns:
            True if process completed, False if timeout
        """
        ...

    def list_active(self) -> list[ProcessHandle]:
        """List all active processes managed by this runner.

        Returns:
            List of active process handles
        """
        ...

    def get_logs(self, handle: ProcessHandle, lines: int = 100) -> list[str]:
        """Get recent log lines for a process.

        Args:
            handle: Process handle from launch()
            lines: Number of lines to return

        Returns:
            List of log lines
        """
        ...


class ProcessRunnerError(Exception):
    """Exception raised when process operations fail."""

    def __init__(self, message: str, handle: ProcessHandle | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.handle = handle
