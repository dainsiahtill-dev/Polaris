"""DTOs for process launch operations.

This module defines unified request and result types for launching
PM and Director subprocesses.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from polaris.kernelone.fs.text_ops import open_text_log_append

logger = logging.getLogger(__name__)


class RunMode(Enum):
    """Execution mode for process launch.

    This enum unifies the various execution modes across PM and Director:
    - PM: run_once (SINGLE), loop (LOOP)
    - Director: one-shot (ONE_SHOT), continuous (CONTINUOUS)
    """

    SINGLE = "single"  # Run once and exit
    LOOP = "loop"  # Run in a loop until stopped
    DAEMON = "daemon"  # Run as background daemon
    ONE_SHOT = "one_shot"  # Run single task (Director)
    CONTINUOUS = "continuous"  # Run continuously (Director)

    def __str__(self) -> str:
        return self.value

    def is_persistent(self) -> bool:
        """Check if this mode runs persistently (not just once)."""
        return self in (RunMode.LOOP, RunMode.DAEMON, RunMode.CONTINUOUS)

    def is_director_mode(self) -> bool:
        """Check if this is a Director-specific mode."""
        return self in (RunMode.ONE_SHOT, RunMode.CONTINUOUS)

    def is_pm_mode(self) -> bool:
        """Check if this is a PM-specific mode."""
        return self in (RunMode.SINGLE, RunMode.LOOP)


@dataclass(frozen=True)
class ProcessLaunchRequest:
    """Request to launch a subprocess (PM or Director).

    This unifies the launch semantics for:
    - PM CLI (loop-pm)
    - Director CLI (loop-director)
    - Backend-managed processes

    Attributes:
        mode: Execution mode
        command: Command and arguments to execute
        workspace: Working directory
        env_vars: Environment variables to set/override
        timeout: Timeout in seconds (None = no timeout)
        log_path: Path for combined stdout/stderr log
        stdout_path: Path for stdout log (if separate from log_path)
        stderr_path: Path for stderr log (if separate from log_path)
        stdin_input: Input to provide via stdin
        priority: Process priority/nice value
        dependencies: List of process IDs to wait for before starting
        name: Human-readable process name
        role: Role identifier (pm, director, etc.)
    """

    mode: RunMode = RunMode.SINGLE
    command: list[str] = field(default_factory=list)
    workspace: Path = field(default_factory=Path.cwd)
    env_vars: dict[str, str] = field(default_factory=dict)
    timeout: int | None = None
    log_path: Path | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    stdin_input: str | None = None
    priority: int = 0
    dependencies: list[str] = field(default_factory=list)
    name: str = ""
    role: str = ""  # "pm", "director", etc.

    def __post_init__(self) -> None:
        # Ensure workspace is absolute path
        if not self.workspace.is_absolute():
            object.__setattr__(self, "workspace", self.workspace.resolve())

        # Ensure command is a list
        if isinstance(self.command, str):
            import shlex

            object.__setattr__(self, "command", shlex.split(self.command))

    def validate(self) -> list[str]:
        """Validate the request, returning list of error messages.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors: list[str] = []

        if not self.command:
            errors.append("Command cannot be empty")

        if not self.workspace.exists():
            errors.append(f"Workspace does not exist: {self.workspace}")
        elif not self.workspace.is_dir():
            errors.append(f"Workspace is not a directory: {self.workspace}")

        if self.timeout is not None and self.timeout <= 0:
            errors.append(f"Invalid timeout: {self.timeout}")

        if self.log_path:
            log_dir = self.log_path.parent
            if not log_dir.exists():
                try:
                    log_dir.mkdir(parents=True, exist_ok=True)
                except (OSError, PermissionError) as e:
                    errors.append(f"Cannot create log directory {log_dir}: {e}")

        # Validate UTF-8 in env vars (Polaris requirement)
        for key, value in self.env_vars.items():
            try:
                key.encode("utf-8")
                value.encode("utf-8")
            except UnicodeEncodeError as e:
                errors.append(f"Non-UTF-8 value in env var {key}: {e}")

        return errors

    def to_subprocess_args(self) -> dict[str, Any]:
        """Convert to subprocess.Popen arguments.

        Returns:
            Dictionary suitable for passing to subprocess.Popen()
        """
        import subprocess

        # Build environment with UTF-8 enforcement
        env = self._build_utf8_env()

        args: dict[str, Any] = {
            "args": self.command,
            "cwd": self.workspace,
            "env": env,
        }

        # Handle I/O redirection using KernelOne log append for durability
        if self.log_path:
            # Combined output to single log file
            args["stdout"] = open_text_log_append(str(self.log_path))
            args["stderr"] = subprocess.STDOUT
        else:
            if self.stdout_path:
                args["stdout"] = open_text_log_append(str(self.stdout_path))
            if self.stderr_path:
                if self.stdout_path == self.stderr_path:
                    args["stderr"] = subprocess.STDOUT
                else:
                    args["stderr"] = open_text_log_append(str(self.stderr_path))

        if self.stdin_input:
            args["stdin"] = subprocess.PIPE

        # Platform-specific priority setting (best effort)
        if self.priority != 0 and os.name != "nt":
            # On Unix, we can use preexec_fn to set nice value
            # Note: This is done in the launcher, not here
            pass

        return args

    def _build_utf8_env(self) -> dict[str, str]:
        """Build environment dict with UTF-8 enforcement.

        Polaris requires explicit UTF-8 for all text handling.

        Returns:
            Environment dictionary with UTF-8 settings
        """
        # Start with current environment
        env = dict(os.environ)

        # Force UTF-8 mode
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        # Platform-specific UTF-8 settings
        if os.name == "nt":  # Windows
            env["CHCP"] = "65001"  # UTF-8 code page

        # Apply overrides from request
        env.update(self.env_vars)

        return env

    def with_timeout(self, timeout: int) -> ProcessLaunchRequest:
        """Create new request with specified timeout.

        Args:
            timeout: Timeout in seconds

        Returns:
            New ProcessLaunchRequest with updated timeout
        """
        return ProcessLaunchRequest(
            mode=self.mode,
            command=self.command,
            workspace=self.workspace,
            env_vars=self.env_vars,
            timeout=timeout,
            log_path=self.log_path,
            stdout_path=self.stdout_path,
            stderr_path=self.stderr_path,
            stdin_input=self.stdin_input,
            priority=self.priority,
            dependencies=self.dependencies,
            name=self.name,
            role=self.role,
        )

    def with_env(self, **kwargs: str) -> ProcessLaunchRequest:
        """Create new request with additional environment variables.

        Args:
            **kwargs: Environment variables to add

        Returns:
            New ProcessLaunchRequest with merged environment
        """
        new_env = dict(self.env_vars)
        new_env.update(kwargs)
        return ProcessLaunchRequest(
            mode=self.mode,
            command=self.command,
            workspace=self.workspace,
            env_vars=new_env,
            timeout=self.timeout,
            log_path=self.log_path,
            stdout_path=self.stdout_path,
            stderr_path=self.stderr_path,
            stdin_input=self.stdin_input,
            priority=self.priority,
            dependencies=self.dependencies,
            name=self.name,
            role=self.role,
        )

    def get_effective_command_line(self) -> str:
        """Get the full command line as string for logging.

        Returns:
            Command line string
        """
        import shlex

        return " ".join(shlex.quote(str(arg)) for arg in self.command)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation
        """
        return {
            "mode": self.mode.value,
            "command": self.command,
            "workspace": str(self.workspace),
            "env_vars": {
                k: "***" if "token" in k.lower() or "key" in k.lower() or "secret" in k.lower() else v
                for k, v in self.env_vars.items()
            },
            "timeout": self.timeout,
            "log_path": str(self.log_path) if self.log_path else None,
            "priority": self.priority,
            "dependencies": self.dependencies,
            "name": self.name,
            "role": self.role,
        }


@dataclass(frozen=True)
class ProcessLaunchResult:
    """Result of process launch attempt.

    Attributes:
        success: Whether launch was successful
        pid: Process ID (if successful)
        process_handle: Handle to manage the process
        log_path: Path to log file
        exit_code: Exit code (if process completed)
        error_message: Error message (if failed)
        start_time: When the process was started
        end_time: When the process ended (if completed)
    """

    success: bool
    pid: int | None = None
    process_handle: Any | None = None
    log_path: Path | None = None
    exit_code: int | None = None
    error_message: str | None = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None

    def is_success(self) -> bool:
        """Check if launch was successful.

        Returns:
            True if launch succeeded
        """
        return self.success and self.pid is not None

    def is_completed(self) -> bool:
        """Check if process has completed.

        Returns:
            True if process has finished
        """
        return self.exit_code is not None or self.end_time is not None

    def duration_ms(self) -> int:
        """Get execution duration in milliseconds.

        Returns:
            Duration in milliseconds
        """
        end = self.end_time or datetime.now()
        return int((end - self.start_time).total_seconds() * 1000)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation
        """
        return {
            "success": self.success,
            "pid": self.pid,
            "log_path": str(self.log_path) if self.log_path else None,
            "exit_code": self.exit_code,
            "error": self.error_message,
            "duration_ms": self.duration_ms(),
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }


if __name__ == "__main__":
    import sys

    # Test basic functionality
    req = ProcessLaunchRequest(
        mode=RunMode.SINGLE,
        command=[sys.executable, "-c", "print('hello')"],
        name="test_process",
    )
    logger.info("Request: %s", req.to_dict())
    logger.info("Command line: %s", req.get_effective_command_line())

    errors = req.validate()
    if errors:
        logger.info("Validation errors: %s", errors)
    else:
        logger.info("Validation passed!")

    # Test RunMode
    assert RunMode.LOOP.is_persistent()
    assert not RunMode.SINGLE.is_persistent()
    logger.info("RunMode tests passed!")
