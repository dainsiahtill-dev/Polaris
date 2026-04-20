"""Tooling constants for KernelOne tool chain execution.

These constants define tool names, command whitelists, and parameter constraints.
They are KernelOne-level execution constants, independent of any business-layer cell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from polaris.kernelone.tool_execution.contracts import read_tool_names, write_tool_names

# Reading configuration
DEFAULT_READ_RADIUS = 80
MAX_TOOL_READ_LINES = 200
MAX_EVENT_CONTENT_LINES = 40

# Tool Chain Configuration
READ_ONLY_TOOLS: set[str] = set(read_tool_names())
WRITE_TOOLS: set[str] = set(write_tool_names())
ALLOWED_TOOLS: set[str] = READ_ONLY_TOOLS | WRITE_TOOLS

# Safe Executor - Blocked command patterns
BLOCKED_COMMAND_PATTERNS: tuple[str, ...] = (
    r"\brm\s+-rf\b",
    r"\brm\s+-r\b",
    r"\bdel\s+/s\b",
    r"\brmdir\s+/s\b",
    r"\bformat\s+[a-z]:",
    r"\bmkfs\b",
    r"\bdd\s+if=.*of=.*",
    r"\b:(){\s*:\|:\s*&\s*};:",  # Fork bomb
    r"\bcurl\s+.*\|.*sh\b",
    r"\bwget\s+.*\|.*sh\b",
    r"\bchmod\s+777\b",
    r"\bchmod\s+-R\s+777\b",
    r"\bsudo\s+rm\b",
    r"\bsudo\s+dd\b",
    r">/dev/sd[a-z]",
    r":\(\)\s*\{.*:.*\|.*:.*&.*\}",  # Fork bomb variant
)

# Development tool command whitelist
ALLOWED_EXECUTION_COMMANDS: frozenset[str] = frozenset(
    {
        # Git commands
        "git",
        "git clone",
        "git pull",
        "git push",
        "git fetch",
        "git checkout",
        "git branch",
        "git status",
        "git log",
        "git diff",
        "git merge",
        "git rebase",
        "git stash",
        # Package managers
        "npm",
        "npm install",
        "npm run",
        "npm test",
        "npm build",
        "npx",
        "npx tsc",
        "npx eslint",
        "npx prettier",
        "node",
        "node -e",
        "node -p",
        "node --version",
        "pip",
        "pip install",
        "pip freeze",
        "pip list",
        "poetry",
        "poetry install",
        "poetry run",
        # Code quality
        "ruff",
        "ruff check",
        "ruff format",
        "mypy",
        "pytest",
        "tsc",
        "typescript",
        "eslint",
        # File operations (restricted)
        "ls",
        "pwd",
        "cd",
        "mkdir",
        "mkdir -p",
        "touch",
        "cat",
        "cp",
        "cp -r",
        "mv",
        "rm",
        "rm -f",
        "rmdir",
        # Python tools
        "python",
        "python -m mypy",
        "python -m pytest",
        "python -m tools.main",
        "python -m ruff",
        "python -m ruff check",
        "python -m ruff format",
        # npm scripts
        "npm run test",
        "npm run build",
        "npm run lint",
        "npm run typecheck",
        "npm run dev",
        "npm run start",
        # yarn/pnpm
        "yarn",
        "yarn run",
        "pnpm",
        "pnpm run",
        # Network tools
        "curl",
        "curl -X",
        "curl -H",
        "curl --request",
        "curl --header",
        "wget",
        # Process management
        "ps",
        "ps aux",
        "kill",
        "kill -9",
        "pkill",
        # System info
        "uname",
        "whoami",
        "hostname",
        "netstat",
        "ping",
        # Docker
        "docker",
        "docker ps",
        "docker images",
        "docker-compose",
        "docker compose",
        # Build tools
        "make",
        "cmake",
        "gradle",
        "mvn",
        "msbuild",
        # Other dev tools
        "jq",
        "jq .",
        "grep",
        "find",
        "which",
        "xargs",
        # bun
        "bun",
        "bun run",
    }
)


@dataclass(frozen=True, slots=True)
class CommandValidationResult:
    """Result of command whitelist validation.

    Attributes:
        allowed: Whether the command is allowed.
        reason: Human-readable reason for the validation result.
        blocked_pattern: If blocked, the pattern that matched (if any).
    """

    allowed: bool
    reason: str
    blocked_pattern: str | None = None


class CommandWhitelistValidator:
    """Command whitelist validator for execute_command tool.

    Validates commands against:
    1. Blocked command patterns (dangerous commands)
    2. Allowed command whitelist (permitted commands)

    Usage:
        result = CommandWhitelistValidator.validate("pytest")
        if not result.allowed:
            print(f"Command blocked: {result.reason}")
    """

    @classmethod
    def validate(cls, command: str) -> CommandValidationResult:
        """Validate a command against the whitelist and blocked patterns.

        Args:
            command: The command string to validate.

        Returns:
            CommandValidationResult with allowed status and reason.
        """
        if not command or not command.strip():
            return CommandValidationResult(
                allowed=False,
                reason="Empty command",
            )

        # Check blocked patterns first
        blocked_result = cls._check_blocked_patterns(command)
        if blocked_result is not None:
            return blocked_result

        # Check whitelist
        whitelist_result = cls._check_whitelist(command)
        if whitelist_result is not None:
            return whitelist_result

        return CommandValidationResult(
            allowed=True,
            reason="Command is in whitelist",
        )

    @classmethod
    def _check_blocked_patterns(cls, command: str) -> CommandValidationResult | None:
        """Check if command matches any blocked pattern.

        Args:
            command: The command to check.

        Returns:
            CommandValidationResult if blocked, None otherwise.
        """
        command_lower = command.lower()
        for pattern in BLOCKED_COMMAND_PATTERNS:
            try:
                if re.search(pattern, command_lower, re.IGNORECASE):
                    return CommandValidationResult(
                        allowed=False,
                        reason="Command matches blocked pattern",
                        blocked_pattern=pattern,
                    )
            except re.error:
                continue
        return None

    @classmethod
    def _check_whitelist(cls, command: str) -> CommandValidationResult | None:
        """Check if command is in the allowed whitelist.

        Args:
            command: The command to check.

        Returns:
            CommandValidationResult if not in whitelist, None if allowed.
        """
        parts = command.strip().split()
        if not parts:
            return CommandValidationResult(
                allowed=False,
                reason="Empty command after splitting",
            )

        base_cmd = parts[0].lower()

        # Check exact match for full command
        if command in ALLOWED_EXECUTION_COMMANDS:
            return None

        # Check if base command is in whitelist (allows "mypy src/")
        if base_cmd in ALLOWED_EXECUTION_COMMANDS:
            return None

        # Check prefix match for multi-word commands like "python -m pytest"
        # Build progressively longer prefixes and check if any match
        for i in range(2, len(parts) + 1):
            prefix = " ".join(parts[:i])
            if prefix in ALLOWED_EXECUTION_COMMANDS:
                return None

        return CommandValidationResult(
            allowed=False,
            reason=f"Command '{base_cmd}' not in whitelist",
        )


# Key-value allowed keys for parsing
KV_ALLOWED_KEYS: set[str] = {
    "pattern",
    "p",
    "paths",
    "path",
    "dir",
    "directory",
    "file",
    "file_path",
    "line",
    "around",
    "around_line",
    "center_line",
    "radius",
    "start",
    "start_line",
    "end",
    "end_line",
    "depth",
    "max",
    "max_entries",
    "n",
    "lines",
    "count",
    "limit",
    "glob",
    "g",
    "query",
    "keyword",
    "search",
    "text",
    "include",
    "exclude",
    "languages",
    "lang",
    "max_files",
    "max_lines",
    "per_file_lines",
    "per_file",
    "recursive",
}
