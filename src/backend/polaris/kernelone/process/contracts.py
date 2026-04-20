"""Process control subsystem contracts for KernelOne.

This module defines the stable port surface for process lifecycle management.
Provides process spawning, termination, control, and safe command execution.

Architecture:
    - ProcessControlPort: abstract interface for process lifecycle
    - CommandExecutorPort: canonical safe subprocess execution interface
    - RuntimeControlAdapter: default local-process implementation

Design constraints:
    - KernelOne-only: no Polaris business semantics
    - All process operations must be async-first
    - Process stop flags must be workspace-scoped (not global)
    - Explicit UTF-8: all file I/O uses encoding="utf-8"
    - shell=True is permanently banned; all execution uses shell=False
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from polaris.kernelone.utils.time_utils import PROCESS_COMMAND_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from pathlib import Path

# Use unified timeout constant from time_utils
DEFAULT_TIMEOUT_SECONDS = PROCESS_COMMAND_TIMEOUT_SECONDS


# =============================================================================
# CommandExecutorPort — safe subprocess execution contract
# =============================================================================


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Immutable result of a subprocess execution.

    Attributes:
        command: The command string that was executed.
        exit_code: Process exit code. 0 = success.
        stdout: Captured standard output (decoded as UTF-8).
        stderr: Captured standard error (decoded as UTF-8).
        timed_out: True if the command exceeded its timeout.
        timeout_seconds: The timeout that was applied.
        cwd: The working directory at execution time, or None if inherited.
    """

    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    timeout_seconds: int
    cwd: Path | None = None

    @property
    def ok(self) -> bool:
        """True when exit_code == 0 and the command did not time out."""
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result to a dictionary.

        Returns:
            A dict containing command, exit_code, stdout, stderr, timed_out,
            timeout_seconds, cwd (as string or None), and ok (computed property).
        """
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "timeout_seconds": self.timeout_seconds,
            "cwd": str(self.cwd) if self.cwd else None,
            "ok": self.ok,
        }


# Re-export from unified kernelone.errors (ShellDisallowedError is canonical here)
from polaris.kernelone.errors import ShellDisallowedError  # noqa: E402


@runtime_checkable
class CommandExecutorPort(Protocol):
    """Protocol for safe subprocess execution within KernelOne.

    Security invariants enforced by this contract:

    1. **No shell execution**: ``shell=True`` is always rejected.
       Implementations MUST raise ``ShellDisallowedError`` if shell=True is attempted.

    2. **Mandatory timeout**: The ``timeout`` parameter is required.
       A timed-out command returns ``CommandResult(timed_out=True)``
       rather than blocking the runtime.

    3. **Full output capture**: stdout and stderr are always captured completely.
       No truncation. Decoded as UTF-8 with error replacement.

    4. **Explicit context**: cwd and env are explicit and auditable.

    Implementations:
        - ``SubprocessCommandExecutor``: Default subprocess.run() implementation.
        - ``DryRunCommandExecutor``: Returns mock results; use in tests.

    Example::

        result = executor.execute("make build", timeout=120, cwd=Path("/src"))
        if result.timed_out:
            raise TimeoutError("Build timed out")
        if not result.ok:
            raise RuntimeError(f"Build failed: {result.stderr}")
    """

    def execute(
        self,
        command: str,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute a single command with a mandatory timeout.

        Args:
            command: Command string to execute. Passed to subprocess with shell=False.
            timeout: Maximum seconds to wait. Must be > 0. Default is 30.
            cwd: Working directory for the subprocess. None = inherit.
            env: Environment variables. None = inherit from parent.

        Returns:
            CommandResult with captured output, exit code, and timeout flag.

        Raises:
            ShellDisallowedError: If shell=True is attempted.
            ValueError: If timeout <= 0 or command is empty.
        """
        ...

    def execute_batch(
        self,
        commands: list[str],
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> list[CommandResult]:
        """Execute multiple commands sequentially.

        Execution stops on the first timed-out command. Non-zero exit codes
        do NOT stop the batch — all commands are attempted.

        Args:
            commands: List of command strings to execute in order.
            timeout: Maximum seconds per individual command.
            cwd: Working directory for all commands.
            env: Environment variables for all commands.

        Returns:
            List of CommandResult, one per command, in execution order.
        """
        ...


class SubprocessCommandExecutor:
    """Default implementation of CommandExecutorPort using subprocess.run().

    This implementation:
    - Permanently disables shell execution (raises ShellDisallowedError).
    - Enforces the timeout via subprocess timeout parameter.
    - Captures stdout/stderr completely (no truncation).
    - Uses UTF-8 decoding with replacement for malformed bytes.
    """

    __slots__ = ()

    def execute(
        self,
        command: str,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")
        if not command:
            raise ValueError("command must not be empty")

        try:
            completed = subprocess.run(
                command,
                shell=False,  # SECURITY: shell is always disabled
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
                env=env,
            )
            return CommandResult(
                command=command,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timed_out=False,
                timeout_seconds=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            # Return timed-out result rather than raising; caller decides how to handle.
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout=exc.stdout.decode("utf-8", errors="replace") if exc.stdout else "",
                stderr=exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "",
                timed_out=True,
                timeout_seconds=timeout,
                cwd=cwd,
            )

    def execute_batch(
        self,
        commands: list[str],
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> list[CommandResult]:
        results: list[CommandResult] = []
        for cmd in commands:
            result = self.execute(cmd, timeout=timeout, cwd=cwd, env=env)
            results.append(result)
            if result.timed_out:
                break  # Stop batch on timeout
        return results


# =============================================================================
# ProcessControlPort — process lifecycle contract (legacy, retained for compat)
# =============================================================================


class ProcessControlPort(Protocol):
    """Abstract interface for process lifecycle management.

    Implementations: RuntimeControlAdapter (local), RemoteProcessAdapter (SSH).
    """

    async def spawn(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        """Spawn a subprocess and return its PID.

        Args:
            command: The command string to execute.
            cwd: Working directory for the subprocess. None = inherit.
            env: Environment variables. None = inherit from parent.

        Returns:
            The PID (process ID) of the newly spawned subprocess.
        """
        ...

    async def terminate(self, pid: int, *, force: bool = False) -> bool:
        """Terminate a process by PID.

        Args:
            pid: The PID of the process to terminate.
            force: If True, send SIGKILL instead of SIGTERM.

        Returns:
            True if the process was successfully terminated, False otherwise.
        """
        ...

    async def is_alive(self, pid: int) -> bool:
        """Check whether a process is still running.

        Args:
            pid: The PID of the process to check.

        Returns:
            True if the process is alive, False otherwise.
        """
        ...

    async def wait(self, pid: int, *, timeout: float | None = None) -> int:
        """Wait for a process to exit and return its exit code.

        Args:
            pid: The PID of the process to wait for.
            timeout: Maximum seconds to wait. None = wait indefinitely.

        Returns:
            The exit code of the process.
        """
        ...

    async def list_pids(self) -> list[int]:
        """List PIDs of all processes managed by this adapter.

        Returns:
            A list of PIDs currently tracked by this ProcessControlPort.
        """
        ...

    async def set_stop_flag(self, workspace: str) -> None:
        """Create a stop flag file for the workspace.

        Args:
            workspace: The workspace path for which to set the stop flag.
        """
        ...

    async def clear_stop_flag(self, workspace: str) -> None:
        """Remove the stop flag file for the workspace.

        Args:
            workspace: The workspace path for which to clear the stop flag.
        """
        ...

    async def is_stop_flag_set(self, workspace: str) -> bool:
        """Check whether a stop flag is set for the workspace.

        Args:
            workspace: The workspace path to check.

        Returns:
            True if the stop flag is set, False otherwise.
        """
        ...


@dataclass(frozen=True)
class ProcessInfo:
    """Immutable snapshot of a running process."""

    pid: int
    command: str
    cwd: str | None = None
    env: dict[str, str] | None = None


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "CommandResult",
    # CommandExecutorPort contract
    "CommandExecutorPort",
    # ProcessControlPort (legacy)
    "ProcessControlPort",
    "ProcessInfo",
    "ShellDisallowedError",
    "SubprocessCommandExecutor",
]
