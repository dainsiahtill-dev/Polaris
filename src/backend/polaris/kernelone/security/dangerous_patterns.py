"""Unified dangerous pattern detection (commands + path traversal).

This module provides canonical dangerous pattern detection for both:
1. Dangerous shell commands (rm -rf, mkfs, dd, shell injection, etc.)
2. Path traversal patterns (../, URL-encoded variants, null byte, etc.)

All dangerous pattern detection must use this module - 禁止在其他位置定义。

Merged patterns from:
- polaris/cells/roles/kernel/internal/policy/layer/budget.py
- polaris/cells/roles/kernel/internal/policy/sandbox_policy.py
- polaris/cells/roles/kernel/internal/output_parser.py (path patterns)
- polaris/cells/roles/kernel/internal/tool_gateway.py (path patterns)
- polaris/cells/roles/kernel/internal/quality_checker.py (path patterns)
- polaris/cells/roles/adapters/internal/schemas/director_schema.py (path patterns)
- polaris/cells/llm/dialogue/internal/role_dialogue.py (path patterns)
"""

from __future__ import annotations

import re
from typing import Final

# Canonical dangerous command patterns (shell commands + dangerous imports)
_DANGEROUS_PATTERNS: Final[list[str]] = [
    # rm -rf variants (most dangerous)
    r"rm\s+-rf\s+[/~]",
    r"rm\s+-rf\s+\$HOME",
    r"rm\s+-rf\s+\*",
    r"rm\s+-rf\s+\.",
    r"rm\s+-rf",
    # del and rmdir
    r"del\s+/[fqs]\s+",
    r"rmdir\s+/[s]",
    # Device/filesystem destruction
    r">\s*/dev/sd[a-z]",
    r"dd\s+if=/dev/(zero|urandom)",
    r"dd\s+if=/dev/",
    # Filesystem formatting
    r"mkfs\.",
    r"format\s+[a-z]:",
    # Shell injection
    r":\(\)\s*\{.*\|.*&.*\}",
    r"curl.*\|.*sh",
    r"wget.*\|.*sh",
    # Command execution
    r"powershell.*-enc",
    r"cmd\.exe\s+/c",
    r"bash\s+-c",
    r"sh\s+-c",
    # Permission escalation
    r"chmod\s+-R\s+777",
    r"chown\s+-R",
    r"chmod\s+000",
    # Python code injection (simple eval/exec patterns - covered by canonical)
    r"eval\s*\(",
    r"exec\s*\(",
    r"os\.system",
    r"subprocess\.call",
    r"__import__\('os'\)",
    # Sensitive file paths (schema validation)
    r"/etc/passwd",
    r"/etc/shadow",
    r"~/.ssh",
    r"\.env",
    r"\.env\.local",
]

# Canonical path traversal patterns
_PATH_TRAVERSAL_PATTERNS: Final[list[str]] = [
    r"\.\./",  # Standard Unix traversal
    r"\.\.[\\/]",  # Windows-style traversal
    r"%2e%2e%2f",  # URL-encoded ../
    r"%252e%252e%252f",  # Double URL-encoded ../
    r"%2e%2e%5c",  # URL-encoded ..\
    r"%252e%252e%255c",  # Double URL-encoded ..\
    r"\.\.",  # Bare parent directory (for strict matching contexts)
]

_COMMAND_CACHE: re.Pattern | None = None
_PATH_CACHE: re.Pattern | None = None


def _get_command_pattern() -> re.Pattern:
    """Get compiled command pattern (cached)."""
    global _COMMAND_CACHE
    if _COMMAND_CACHE is None:
        _COMMAND_CACHE = re.compile("|".join(_DANGEROUS_PATTERNS), re.IGNORECASE)
    return _COMMAND_CACHE


def _get_path_pattern() -> re.Pattern:
    """Get compiled path traversal pattern (cached)."""
    global _PATH_CACHE
    if _PATH_CACHE is None:
        _PATH_CACHE = re.compile("|".join(_PATH_TRAVERSAL_PATTERNS), re.IGNORECASE)
    return _PATH_CACHE


def is_dangerous_command(text: str) -> bool:
    """Check if command contains dangerous shell command patterns.

    Args:
        text: Command string to check.

    Returns:
        True if command is dangerous, False otherwise.
    """
    if not text:
        return False
    return bool(_get_command_pattern().search(text))


def is_path_traversal(text: str) -> bool:
    """Check if text contains path traversal patterns.

    Args:
        text: String to check (path, command, etc.).

    Returns:
        True if path traversal is detected, False otherwise.
    """
    if not text:
        return False
    return bool(_get_path_pattern().search(text))


def is_dangerous(text: str) -> bool:
    """Check if text contains any dangerous pattern (command or path).

    This is a convenience function that combines both command and path
    traversal checks.

    Args:
        text: String to check.

    Returns:
        True if any dangerous pattern is found, False otherwise.
    """
    return is_dangerous_command(text) or is_path_traversal(text)


__all__ = [
    "_DANGEROUS_PATTERNS",
    "_PATH_TRAVERSAL_PATTERNS",
    "is_dangerous",
    "is_dangerous_command",
    "is_path_traversal",
]
