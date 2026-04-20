"""Security validation for director tool execution.

Command-level security checks for the director execution cell.
Provides shell metacharacter detection and command validation
to prevent command injection attacks.
"""

from __future__ import annotations

import re

_SHELL_META_RE = re.compile(r"(;|&&|\|\||\||`|\$\(|\r|\n|>|<)")


def _contains_shell_metacharacters(command: str) -> bool:
    """Return whether the command contains shell control operators."""
    return bool(_SHELL_META_RE.search(str(command or "")))


def validate_command_safety(command: str) -> None:
    """Validate that a command does not contain shell metacharacters.

    Args:
        command: The command string to validate.

    Raises:
        ValueError: If the command contains shell metacharacters.
    """
    if _contains_shell_metacharacters(command):
        raise ValueError(f"Command contains shell metacharacters: {command!r}")


def validate_commands_batch(commands: list[str]) -> list[str]:
    """Validate a batch of commands for shell safety.

    Args:
        commands: List of command strings to validate.

    Returns:
        List of unsafe commands found.
    """
    unsafe: list[str] = []
    for command in commands:
        if _contains_shell_metacharacters(command):
            unsafe.append(command)
    return unsafe
