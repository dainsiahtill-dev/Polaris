"""Security functions for KernelOne tool execution.

Command-level security checks: blocked-pattern matching and command whitelist
enforcement.
"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Set

from polaris.kernelone.tool_execution.constants import ALLOWED_EXECUTION_COMMANDS, BLOCKED_COMMAND_PATTERNS

_SHELL_META_RE = re.compile(r"(;|&&|\|\||\||`|\$\(|\r|\n|>|<)")


def _contains_shell_metacharacters(command: str) -> bool:
    """Return whether the command contains shell control operators."""
    return bool(_SHELL_META_RE.search(str(command or "")))


def is_command_blocked(command: str) -> bool:
    """Check if a command matches blocked patterns."""
    if not command:
        return True
    if _contains_shell_metacharacters(command):
        return True
    command_lower = command.lower()
    for pattern in BLOCKED_COMMAND_PATTERNS:
        try:
            if re.search(pattern, command_lower, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def is_command_allowed(command: str, allowed_commands: Set[str] | None = None) -> bool:
    """Check if a command is in the allowed whitelist."""
    if not command:
        return False
    if _contains_shell_metacharacters(command):
        return False
    if is_command_blocked(command):
        return False
    if allowed_commands is None:
        allowed_commands = ALLOWED_EXECUTION_COMMANDS
    command_tokens = _tokenize_command(command)
    if not command_tokens:
        return False
    return _matches_allowed_prefix(command_tokens, allowed_commands)


def _tokenize_command(command: str) -> list[str]:
    try:
        tokens = shlex.split(command.strip(), posix=False)
    except ValueError:
        tokens = command.strip().split()
    return [token.strip() for token in tokens if token.strip()]


def _matches_allowed_prefix(command_tokens: list[str], allowed_commands: Set[str]) -> bool:
    lowered_command = [token.lower() for token in command_tokens]
    for allowed in allowed_commands:
        allowed_tokens = _tokenize_command(str(allowed or ""))
        if not allowed_tokens:
            continue
        lowered_allowed = [token.lower() for token in allowed_tokens]
        if len(lowered_command) < len(lowered_allowed):
            continue
        if lowered_command[: len(lowered_allowed)] == lowered_allowed:
            return True
    return False
