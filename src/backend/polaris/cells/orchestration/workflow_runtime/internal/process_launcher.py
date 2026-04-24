"""Unified process launcher for Polaris.

This module provides ProcessLauncher, a unified service for launching
PM and Director subprocesses with consistent UTF-8 handling, timeout
management, and audit logging.

Migration status: process lifecycle now routes through
``runtime.execution_broker`` (cell layer), which is backed by
``kernelone.runtime.ExecutionFacade``.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add parent to path for polaris imports
sys.path.insert(0, str(Path(__file__).parents[2]))

from polaris.cells.orchestration.workflow_runtime.public.process_launch import (
    ProcessLaunchRequest,
    ProcessLaunchResult,
    RunMode,
)
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessHandleV1,
    ExecutionProcessStatusV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import (
    ExecutionBrokerService,
    get_execution_broker_service,
)

logger = logging.getLogger(__name__)

# Default timeout for launched subprocesses (seconds).
_LAUNCH_TIMEOUT_SECONDS = 300


class LauncherError(Exception):
    """Exception raised when process launch fails."""

    def __init__(self, message: str, stage: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.details = details or {}


class ProcessLauncher:
    """Unified process launcher for PM and Director.

    This class provides a consistent interface for launching subprocesses
    with proper UTF-8 handling, environment setup, and result tracking.

    Attributes:
        _active_processes: Dictionary of active process handles
        _launch_history: List of recent launch results
    """

    def __init__(self, broker: ExecutionBrokerService | None = None) -> None:
        """Initialize the process launcher."""
        self._broker = broker
        self._active_processes: dict[str, ExecutionProcessHandleV1] = {}
        self._launch_history: list[ProcessLaunchResult] = []
        self._max_history = 100

    async def launch(self, request: ProcessLaunchRequest) -> ProcessLaunchResult:
        """Launch a subprocess with the given request.

        Internally routes process lifecycle to `runtime.execution_broker`, which
        uses KernelOne `ExecutionFacade` process lane and unified runtime status.

        Args:
            request: Process launch request with all parameters

        Returns:
            ProcessLaunchResult with handle and status

        Raises:
            LauncherError: If launch fails
        """
        start_time = datetime.now()
        try:
            # Validate request
            errors = request.validate()
            if errors:
                return ProcessLaunchResult(
                    success=False,
                    error_message=f"Validation failed: {', '.join(errors)}",
                    start_time=start_time,
                )

            # Prepare environment with UTF-8 enforcement
            env = self._build_utf8_env(request.env_vars)

            # Prepare stdin lines if input provided
            stdin_lines: list[str] | None = None
            if request.stdin_input:
                stdin_lines = request.stdin_input.splitlines(keepends=True)
                if stdin_lines and not stdin_lines[-1].endswith("\n"):
                    stdin_lines[-1] += "\n"

            log_path = str(request.log_path or request.stdout_path) if request.log_path or request.stdout_path else None
            command = LaunchExecutionProcessCommandV1(
                name=request.name or request.role or "workflow-runtime-process",
                args=tuple(request.command),
                workspace=str(request.workspace),
                env=env,
                stdin_input="".join(stdin_lines) if stdin_lines else None,
                timeout_seconds=_LAUNCH_TIMEOUT_SECONDS,
                log_path=log_path,
                metadata={
                    "role": request.role,
                    "run_mode": request.mode.value if hasattr(request.mode, "value") else str(request.mode),
                    "workspace": str(request.workspace),
                },
            )
            launch_result = await self._require_broker().launch_process(command)
            if not launch_result.success or launch_result.handle is None:
                return ProcessLaunchResult(
                    success=False,
                    error_message=launch_result.error_message or "Execution broker launch failed",
                    start_time=start_time,
                )

            process_handle = launch_result.handle
            process_id = process_handle.execution_id

            self._active_processes[process_id] = process_handle

            # Create result
            result = ProcessLaunchResult(
                success=True,
                pid=process_handle.pid,
                process_handle={
                    "id": process_id,
                    "pid": process_handle.pid,
                    "name": request.name,
                    "role": request.role,
                    "execution_id": process_id,
                },
                log_path=request.log_path,
                start_time=start_time,
            )

            # Add to history
            self._launch_history.append(result)
            if len(self._launch_history) > self._max_history:
                self._launch_history.pop(0)

            return result

        except (RuntimeError, ValueError) as e:
            logger.warning("Process launch failed for %s: %s", request.name, e)
            raise LauncherError(
                f"Failed to launch process: {e}",
                stage="launch",
                details={"command": request.command},
            ) from e

    async def terminate(
        self,
        process_handle: dict[str, Any],
        timeout: float = 5.0,
    ) -> bool:
        """Terminate a running process gracefully.

        Args:
            process_handle: Process handle from launch()
            timeout: Time to wait for graceful termination

        Returns:
            True if terminated successfully
        """
        process_id = process_handle.get("id")
        if not process_id or process_id not in self._active_processes:
            return False

        handle = self._active_processes[process_id]

        try:
            terminated = await self._require_broker().terminate_process(
                handle,
                timeout_seconds=timeout,
            )
            if terminated:
                self._active_processes.pop(process_id, None)
            return terminated
        except (RuntimeError, ValueError) as e:
            logger.warning("terminate failed for process %s: %s", process_id, e)
            return False

    async def wait_for(
        self,
        process_handle: dict[str, Any],
        timeout: float | None = 300.0,
    ) -> ProcessLaunchResult:
        """Wait for process to complete.

        Args:
            process_handle: Process handle from launch()
            timeout: Maximum time to wait (None = forever)

        Returns:
            Updated ProcessLaunchResult with exit code
        """
        process_id = process_handle.get("id")
        if not process_id or process_id not in self._active_processes:
            return ProcessLaunchResult(
                success=False,
                error_message="Process not found",
            )

        handle = self._active_processes[process_id]

        try:
            wait_result = await self._require_broker().wait_process(
                handle,
                timeout_seconds=timeout,
            )
            if wait_result.status in {
                ExecutionProcessStatusV1.SUCCESS,
                ExecutionProcessStatusV1.FAILED,
                ExecutionProcessStatusV1.CANCELLED,
                ExecutionProcessStatusV1.TIMED_OUT,
            }:
                self._active_processes.pop(process_id, None)
            # Find original result from history
            original = None
            for candidate in reversed(self._launch_history):
                if candidate.process_handle and candidate.process_handle.get("id") == process_id:
                    original = candidate
                    break
            if original is not None:
                return ProcessLaunchResult(
                    success=wait_result.success,
                    pid=original.pid,
                    process_handle=process_handle,
                    log_path=original.log_path,
                    exit_code=wait_result.exit_code,
                    error_message=wait_result.error_message,
                    start_time=original.start_time,
                    end_time=datetime.now(),
                )
            return ProcessLaunchResult(
                success=wait_result.success,
                exit_code=wait_result.exit_code,
                error_message=wait_result.error_message,
            )
        except TimeoutError:
            return ProcessLaunchResult(
                success=False,
                error_message="Timeout waiting for process",
                process_handle=process_handle,
            )
        except (RuntimeError, ValueError) as e:
            logger.warning("wait_for failed for process %s: %s", process_id, e)
            return ProcessLaunchResult(
                success=False,
                error_message=str(e),
                process_handle=process_handle,
            )

    async def get_active_processes(self) -> list[dict[str, Any]]:
        """Get list of active process handles.

        Returns:
            List of process handle dictionaries
        """
        result: list[dict[str, Any]] = []
        if self._broker is None:
            try:
                self._broker = get_execution_broker_service()
            except RuntimeError:
                return [
                    {"id": item.execution_id, "pid": item.pid, "poll": None} for item in self._active_processes.values()
                ]
        active_handles = await self._broker.list_active_processes()
        self._active_processes = {item.execution_id: item for item in active_handles}
        for handle in active_handles:
            result.append({"id": handle.execution_id, "pid": handle.pid, "poll": None})
        return result

    def _require_broker(self) -> ExecutionBrokerService:
        if self._broker is None:
            self._broker = get_execution_broker_service()
        return self._broker

    def _build_utf8_env(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        """Build environment dict with UTF-8 enforcement.

        Args:
            overrides: Environment variable overrides

        Returns:
            Environment dictionary
        """
        # Start with current environment
        env = dict(os.environ)

        # Force UTF-8 mode
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        # Platform-specific
        if os.name == "nt":
            env["CHCP"] = "65001"

        # Apply overrides
        if overrides:
            env.update(overrides)

        return env

    def launch_pm(
        self,
        workspace: Path,
        mode: RunMode = RunMode.SINGLE,
        **kwargs: Any,
    ) -> ProcessLaunchRequest:
        """Build a PM launch request.

        Args:
            workspace: Workspace path
            mode: Execution mode
            **kwargs: Additional parameters

        Returns:
            ProcessLaunchRequest configured for PM
        """
        # Get PM script path
        backend_root = Path(__file__).parents[2]
        pm_script = backend_root / "scripts" / "pm" / "cli.py"

        command = [sys.executable, str(pm_script), "--workspace", str(workspace)]

        if mode == RunMode.LOOP:
            command.append("--loop")

        # Add iterations if specified
        iterations = kwargs.get("iterations")
        if iterations:
            command.extend(["--iterations", str(iterations)])

        # Add backend if specified
        backend = kwargs.get("backend")
        if backend:
            command.extend(["--pm-backend", backend])

        return ProcessLaunchRequest(
            mode=mode,
            command=command,
            workspace=workspace,
            name="pm",
            role="pm",
            **{k: v for k, v in kwargs.items() if k not in ("iterations", "backend")},
        )

    def launch_director(
        self,
        workspace: Path,
        mode: RunMode = RunMode.ONE_SHOT,
        **kwargs: Any,
    ) -> ProcessLaunchRequest:
        """Build a Director launch request.

        Args:
            workspace: Workspace path
            mode: Execution mode
            **kwargs: Additional parameters

        Returns:
            ProcessLaunchRequest configured for Director
        """
        # Get Director script path
        backend_root = Path(__file__).parents[2]
        director_script = backend_root / "scripts" / "loop-director.py"

        command = [
            sys.executable,
            str(director_script),
            "--workspace",
            str(workspace),
        ]

        # Add iterations
        iterations = kwargs.get("iterations", 1)
        command.extend(["--iterations", str(iterations)])

        return ProcessLaunchRequest(
            mode=mode,
            command=command,
            workspace=workspace,
            name="director",
            role="director",
            **{k: v for k, v in kwargs.items() if k != "iterations"},
        )


# Convenience functions
async def launch_pm_once(
    workspace: Path,
    **kwargs: Any,
) -> ProcessLaunchResult:
    """Convenience function to launch PM once.

    Args:
        workspace: Workspace path
        **kwargs: Additional parameters

    Returns:
        ProcessLaunchResult
    """
    launcher = ProcessLauncher()
    request = launcher.launch_pm(workspace, RunMode.SINGLE, **kwargs)
    return await launcher.launch(request)


async def launch_director_once(
    workspace: Path,
    iterations: int = 1,
    **kwargs: Any,
) -> ProcessLaunchResult:
    """Convenience function to launch Director once.

    Args:
        workspace: Workspace path
        iterations: Number of iterations
        **kwargs: Additional parameters

    Returns:
        ProcessLaunchResult
    """
    launcher = ProcessLauncher()
    request = launcher.launch_director(workspace, RunMode.ONE_SHOT, iterations=iterations, **kwargs)
    return await launcher.launch(request)
