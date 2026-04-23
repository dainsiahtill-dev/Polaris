"""Safe Command Executor - Security-focused command execution.

This module provides a sandboxed command execution environment with:
- Command whitelist validation
- Timeout enforcement
- Resource limits
- Output capture and sanitization
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from polaris.cells.factory.verification_guard.public.contracts import (
    ExecutionResult,
    VerificationGuardErrorV1,
)

# Default whitelist of safe commands
DEFAULT_COMMAND_WHITELIST: frozenset[str] = frozenset(
    {
        # Python testing and linting
        "pytest",
        "python",
        "python3",
        "ruff",
        "mypy",
        "black",
        "isort",
        # Node.js testing
        "npm",
        "node",
        "jest",
        "vitest",
        # General verification
        "git",
        "cargo",
        "go",
        "make",
        # File inspection
        "cat",
        "head",
        "tail",
        "wc",
        "find",
        "ls",
    }
)

# Dangerous commands that are explicitly blocked
DANGEROUS_PATTERNS: tuple[str, ...] = (
    r"\brm\b",
    r"\bsudo\b",
    r"\bcurl\b.*\|\s*sh",
    r"\bwget\b.*\|\s*sh",
    r"\beval\b",
    r"\bexec\b",
    r"\bunlink\b",
    r"\bmkfs\b",
    r"\bdd\b",
    r"\bformat\b",
    r">\s*/dev/",
    r"\bchmod\b.*777",
    r"\bchown\b",
    r"\bmount\b",
    r"\bumount\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bkill\b",
    r"\bpkill\b",
    r"\bkillall\b",
)

# Maximum output size to prevent memory exhaustion
MAX_OUTPUT_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB


@dataclass(frozen=True)
class SafetyCheckResult:
    """Result of a command safety check."""

    is_safe: bool
    reason: str | None = None
    blocked_pattern: str | None = None


class SafeExecutor:
    """Secure command executor with whitelist and sandbox enforcement.

    This executor implements the defense-in-depth strategy:
    1. Command whitelist validation
    2. Dangerous pattern detection
    3. Timeout enforcement
    4. Output size limits
    5. Working directory restrictions

    """

    def __init__(
        self,
        *,
        whitelist: Sequence[str] | None = None,
        default_timeout_seconds: int = 60,
        max_output_size_bytes: int = MAX_OUTPUT_SIZE_BYTES,
        allowed_working_dirs: Sequence[str] | None = None,
    ) -> None:
        """Initialize the safe executor.

        Args:
            whitelist: List of allowed command names (defaults to DEFAULT_COMMAND_WHITELIST)
            default_timeout_seconds: Default timeout for command execution
            max_output_size_bytes: Maximum output size to capture
            allowed_working_dirs: Restrict execution to these directories

        """
        self._whitelist = frozenset(whitelist) if whitelist else DEFAULT_COMMAND_WHITELIST
        self._default_timeout = default_timeout_seconds
        self._max_output_size = max_output_size_bytes
        self._allowed_dirs = tuple(allowed_working_dirs) if allowed_working_dirs else None

    def validate_command_safety(self, command: str) -> SafetyCheckResult:
        """Validate that a command is safe to execute.

        Performs multiple safety checks:
        1. Checks against dangerous patterns
        2. Validates command is in whitelist
        3. Detects shell injection attempts

        Args:
            command: The command string to validate

        Returns:
            SafetyCheckResult indicating if the command is safe

        """
        if not command or not command.strip():
            return SafetyCheckResult(
                is_safe=False,
                reason="Empty command",
            )

        # Check for dangerous patterns
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return SafetyCheckResult(
                    is_safe=False,
                    reason=f"Command contains dangerous pattern: {pattern}",
                    blocked_pattern=pattern,
                )

        # Extract the base command (first word)
        base_command = command.strip().split()[0].lower()

        # Remove common prefixes like 'python -m'
        if base_command in ("python", "python3") and " -m " in command:
            parts = command.split(" -m ", 1)
            if len(parts) > 1:
                module_part = parts[1].strip().split()[0]
                # Check if the module is in whitelist
                if module_part not in self._whitelist:
                    return SafetyCheckResult(
                        is_safe=False,
                        reason=f"Module '{module_part}' not in whitelist",
                    )
                return SafetyCheckResult(is_safe=True)

        # Check if base command is in whitelist
        if base_command not in self._whitelist:
            return SafetyCheckResult(
                is_safe=False,
                reason=f"Command '{base_command}' not in whitelist",
            )

        # Additional shell injection check
        if self._detect_shell_injection(command):
            return SafetyCheckResult(
                is_safe=False,
                reason="Potential shell injection detected",
            )

        return SafetyCheckResult(is_safe=True)

    def _detect_shell_injection(self, command: str) -> bool:
        """Detect potential shell injection attempts.

        Looks for suspicious patterns like:
        - Command chaining with ; or &&
        - Subshell execution with $()
        - Backtick execution
        - Pipe to shell

        """
        # Check for truly dangerous patterns
        # Be careful not to flag patterns inside quoted strings
        dangerous_patterns = ["| sh", "| bash", "$(", "`", "${"]

        for pattern in dangerous_patterns:
            if pattern in command:
                return True

        # For ; && ||, only flag if they appear outside of quotes
        # Use proper quote-aware parsing that handles escaped characters
        in_single_quote = False
        in_double_quote = False
        i = 0
        while i < len(command):
            char = command[i]
            if char == "\\" and i + 1 < len(command):
                i += 2
                continue
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            elif (
                (char == ";" or (char in ("&", "|") and i + 1 < len(command) and command[i + 1] == char))
                and not in_single_quote
                and not in_double_quote
            ):
                return True
            i += 1

        return False

    def _split_command(self, command: str) -> list[str]:
        """Split command string into list with proper quote and escape handling.

        This method handles:
        - Split on whitespace outside of quotes
        - Preserves quoted strings as single arguments
        - Handles escape sequences within quoted strings (e.g., \\", \\n, \\\\)
        - Preserves backslashes in paths when NOT inside quotes

        Args:
            command: The command string to split

        Returns:
            List of command and arguments

        """
        result: list[str] = []
        current = ""
        in_single_quote = False
        in_double_quote = False
        i = 0

        while i < len(command):
            char = command[i]

            # Handle escape sequences inside double quotes
            if in_double_quote and char == "\\" and i + 1 < len(command):
                next_char = command[i + 1]
                if next_char == '"':
                    # Escaped quote - include the quote and continue
                    # This is \" which means a literal " inside double quotes
                    current += '"'
                    i += 2
                    continue
                elif next_char == "\\":
                    # Escaped backslash - include one backslash
                    current += "\\"
                    i += 2
                    continue
                elif next_char in ("n", "t", "r"):
                    # Common escape sequences
                    escape_map = {"n": "\n", "t": "\t", "r": "\r"}
                    current += escape_map[next_char]
                    i += 2
                    continue
                else:
                    # Preserve backslash for unknown escape sequences
                    current += char
                    i += 1
                    continue

            # Handle quote state changes
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                i += 1
                continue
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                i += 1
                continue

            # Split on whitespace when outside quotes
            if char in (" ", "\t", "\n", "\r") and not in_single_quote and not in_double_quote:
                if current:
                    result.append(current)
                    current = ""
                i += 1
                continue

            # Add character to current token
            current += char
            i += 1

        # Add final token
        if current:
            result.append(current)

        return result

    def execute(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute a command safely with timeout and resource limits.

        Args:
            command: The command to execute
            timeout_seconds: Override default timeout
            working_dir: Working directory for execution
            env: Environment variables to set

        Returns:
            ExecutionResult with output and status

        Raises:
            VerificationGuardErrorV1: If command fails safety checks

        """
        # Validate command safety
        safety = self.validate_command_safety(command)
        if not safety.is_safe:
            msg = safety.reason or "Command failed safety check"
            raise VerificationGuardErrorV1(
                message=msg,
                code="command_blocked",
                details={
                    "command": command,
                    "blocked_pattern": safety.blocked_pattern,
                },
            )

        # Validate working directory
        if working_dir and self._allowed_dirs:
            resolved_dir = Path(working_dir).resolve()
            allowed = any(resolved_dir.is_relative_to(Path(d).resolve()) for d in self._allowed_dirs)
            if not allowed:
                msg = f"Working directory '{working_dir}' not in allowed list"
                raise VerificationGuardErrorV1(
                    message=msg,
                    code="invalid_working_directory",
                    details={
                        "working_dir": working_dir,
                        "allowed_dirs": self._allowed_dirs,
                    },
                )

        timeout = timeout_seconds or self._default_timeout

        return self._execute_with_timeout(
            command,
            timeout=timeout,
            working_dir=working_dir,
            env=env,
        )

    def _execute_with_timeout(
        self,
        command: str,
        *,
        timeout: int,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute command with timeout enforcement.

        Uses subprocess with timeout to prevent hanging processes.
        Captures stdout/stderr with size limits.

        """
        start_time = time.time()
        timed_out = False
        stdout_data = ""
        stderr_data = ""
        return_code = -1

        try:
            # Prepare environment
            run_env = None
            if env:
                import os

                run_env = {**os.environ, **env}

            # SECURITY FIX: Convert command to list and use shell=False
            # This prevents command injection attacks that exploit shell=True.
            # With shell=False, the command and arguments are passed directly to exec()
            # without shell interpretation, blocking injection via |, ;, $, ``, etc.
            #
            # Use a custom splitter that respects quoted strings but preserves
            # backslashes in paths (unlike shlex.split which interprets backslashes
            # as escape characters on Windows, breaking paths like C:\Users\).
            cmd_list = self._split_command(command)

            # Execute with timeout
            # shell=False is critical for security: prevents shell injection attacks
            # where malicious input could execute arbitrary commands via shell metacharacters
            result = subprocess.run(
                cmd_list,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=working_dir,
                env=run_env,
            )

            return_code = result.returncode

            # Truncate output if too large
            stdout_data = result.stdout
            if len(stdout_data.encode("utf-8")) > self._max_output_size:
                stdout_data = stdout_data[: self._max_output_size // 2]
                stdout_data += "\n... [output truncated due to size limit] ..."

            stderr_data = result.stderr
            if len(stderr_data.encode("utf-8")) > self._max_output_size:
                stderr_data = stderr_data[: self._max_output_size // 2]
                stderr_data += "\n... [stderr truncated due to size limit] ..."

        except subprocess.TimeoutExpired as e:
            timed_out = True
            return_code = -1
            stdout_data = e.stdout.decode("utf-8", errors="replace") if e.stdout else ""
            stderr_data = f"Command timed out after {timeout} seconds"

        except Exception as e:  # noqa: BLE001
            return_code = -1
            stderr_data = f"Execution error: {type(e).__name__}: {e}"

        execution_time_ms = int((time.time() - start_time) * 1000)

        return ExecutionResult(
            command=command,
            stdout=stdout_data,
            stderr=stderr_data,
            return_code=return_code,
            execution_time_ms=execution_time_ms,
            timed_out=timed_out,
        )

    def get_whitelist(self) -> frozenset[str]:
        """Return the current command whitelist."""
        return self._whitelist

    def is_command_allowed(self, command: str) -> bool:
        """Check if a command is in the whitelist without executing."""
        safety = self.validate_command_safety(command)
        return safety.is_safe
