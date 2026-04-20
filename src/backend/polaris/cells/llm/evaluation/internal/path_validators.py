"""Path traversal vulnerability validators for evaluation module.

This module provides security validation functions to prevent path traversal
attacks through user-controlled path inputs (workspace_fixture, run_id,
base_workspace).

Security invariants:
- No path component may contain '..' (parent directory traversal)
- No path may be absolute or start with '/', './', '../'
- Identifiers must match safe patterns: [a-zA-Z0-9_-]+
- No null bytes or control characters
- base_workspace must be an existing, accessible directory
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

# Safe identifier pattern: alphanumeric, underscore, hyphen only
SAFE_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_-]+$")

# Patterns that indicate path traversal attempts
PATH_TRAVERSAL_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"\.\."),  # Double dot (anywhere)
    re.compile(r"^\.\./"),  # Leading ../
    re.compile(r"^\./"),  # Leading ./
    re.compile(r"^~"),  # Home directory expansion
]

# Patterns for filesystem paths (allows absolute paths, rejects traversal)
FILESYSTEM_PATH_TRAVERSAL_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"\.\."),  # Double dot
    re.compile(r"^\.\./"),  # Leading ../
    re.compile(r"^\./"),  # Leading ./
    re.compile(r"^~"),  # Home directory expansion
]

# Dangerous characters that could be used in path traversal or command injection
DANGEROUS_PATH_CHARS: Final[re.Pattern[str]] = re.compile(
    r"[\x00-\x1f\x7f-\x9f]|"  # Control characters
    r"[$`|;]|"  # Shell metacharacters
    r"[<>\"']"  # Quote/escape characters
)

# Maximum length for identifiers
MAX_IDENTIFIER_LENGTH: int = 128


class PathTraversalError(ValueError):
    """Raised when a path traversal attack is detected.

    Attributes:
        field_name: The name of the field that failed validation.
        value: The invalid value that was provided.
        reason: Human-readable explanation of why validation failed.
    """

    def __init__(
        self,
        message: str,
        field_name: str,
        value: str,
        reason: str,
    ) -> None:
        self.field_name = field_name
        self.value = value
        self.reason = reason
        super().__init__(message)

    def __repr__(self) -> str:
        return f"PathTraversalError(field_name={self.field_name!r}, value={self.value!r}, reason={self.reason!r})"


def validate_workspace_fixture(
    token: str,
    *,
    field_name: str = "workspace_fixture",
) -> str:
    """Validate workspace_fixture token for path traversal attempts.

    The workspace_fixture token is used to construct paths within the
    WORKSPACES_ROOT directory. This function ensures the token cannot
    be used for path traversal attacks.

    Args:
        token: The workspace_fixture token to validate.
        field_name: Name of the field for error messages.

    Returns:
        The validated token (stripped of whitespace).

    Raises:
        PathTraversalError: If the token contains path traversal patterns
            or dangerous characters.

    Examples:
        >>> validate_workspace_fixture("my_fixture")
        'my_fixture'
        >>> validate_workspace_fixture("safe_workspace_123")
        'safe_workspace_123'
    """
    if not token:
        return ""

    normalized = str(token).strip()

    if not normalized:
        return ""

    # Check length
    if len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise PathTraversalError(
            f"{field_name} exceeds maximum length of {MAX_IDENTIFIER_LENGTH}",
            field_name=field_name,
            value=token,
            reason=f"length={len(normalized)}, max={MAX_IDENTIFIER_LENGTH}",
        )

    # Check for dangerous characters
    if DANGEROUS_PATH_CHARS.search(normalized):
        raise PathTraversalError(
            f"{field_name} contains dangerous characters",
            field_name=field_name,
            value=token,
            reason="contains control characters or shell metacharacters",
        )

    # Check for path traversal patterns
    for pattern in PATH_TRAVERSAL_PATTERNS:
        if pattern.search(normalized):
            raise PathTraversalError(
                f"{field_name} contains path traversal pattern",
                field_name=field_name,
                value=token,
                reason=f"matched forbidden pattern: {pattern.pattern}",
            )

    # Check against safe identifier pattern (most restrictive)
    # workspace_fixture should only contain safe identifier characters
    if not SAFE_IDENTIFIER_PATTERN.match(normalized):
        raise PathTraversalError(
            f"{field_name} contains invalid characters for path component",
            field_name=field_name,
            value=token,
            reason="must match pattern: ^[a-zA-Z0-9_-]+$",
        )

    return normalized


def validate_run_id(
    run_id: str,
    *,
    field_name: str = "run_id",
) -> str:
    """Validate run_id for path traversal attempts.

    The run_id is used to construct paths within the evaluation runtime
    directory. This function ensures the run_id cannot be used for path
    traversal attacks.

    Args:
        run_id: The run_id to validate.
        field_name: Name of the field for error messages.

    Returns:
        The validated run_id (stripped of whitespace).

    Raises:
        PathTraversalError: If the run_id contains path traversal patterns
            or dangerous characters.

    Examples:
        >>> validate_run_id("abc123")
        'abc123'
        >>> validate_run_id("run-001")
        'run-001'
    """
    if not run_id:
        raise PathTraversalError(
            f"{field_name} cannot be empty",
            field_name=field_name,
            value=run_id,
            reason="empty value",
        )

    normalized = str(run_id).strip()

    if not normalized:
        raise PathTraversalError(
            f"{field_name} cannot be empty after normalization",
            field_name=field_name,
            value=run_id,
            reason="empty after strip",
        )

    # Check length
    if len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise PathTraversalError(
            f"{field_name} exceeds maximum length of {MAX_IDENTIFIER_LENGTH}",
            field_name=field_name,
            value=run_id,
            reason=f"length={len(normalized)}, max={MAX_IDENTIFIER_LENGTH}",
        )

    # Check for dangerous characters
    if DANGEROUS_PATH_CHARS.search(normalized):
        raise PathTraversalError(
            f"{field_name} contains dangerous characters",
            field_name=field_name,
            value=run_id,
            reason="contains control characters or shell metacharacters",
        )

    # Check for path traversal patterns
    for pattern in PATH_TRAVERSAL_PATTERNS:
        if pattern.search(normalized):
            raise PathTraversalError(
                f"{field_name} contains path traversal pattern",
                field_name=field_name,
                value=run_id,
                reason=f"matched forbidden pattern: {pattern.pattern}",
            )

    # run_id must strictly match safe identifier pattern
    if not SAFE_IDENTIFIER_PATTERN.match(normalized):
        raise PathTraversalError(
            f"{field_name} contains invalid characters",
            field_name=field_name,
            value=run_id,
            reason="must match pattern: ^[a-zA-Z0-9_-]+$",
        )

    return normalized


def validate_base_workspace(
    base_workspace: str,
    *,
    field_name: str = "base_workspace",
    must_exist: bool = True,
    must_be_dir: bool = True,
) -> Path:
    """Validate base_workspace path for path traversal attempts.

    The base_workspace is the root directory for evaluation runs. This
    function ensures the path cannot be used for path traversal attacks
    and optionally verifies the directory exists and is accessible.

    Args:
        base_workspace: The base workspace path to validate.
        field_name: Name of the field for error messages.
        must_exist: If True, raise error if path does not exist.
        must_be_dir: If True, raise error if path is not a directory.

    Returns:
        The validated Path object.

    Raises:
        PathTraversalError: If the path contains path traversal patterns,
            dangerous characters, or fails existence/directory checks.

    Examples:
        >>> path = validate_base_workspace("/path/to/workspace")
        >>> path = validate_base_workspace("./workspace", must_exist=False)
    """
    if not base_workspace:
        raise PathTraversalError(
            f"{field_name} cannot be empty",
            field_name=field_name,
            value=base_workspace,
            reason="empty value",
        )

    normalized = str(base_workspace).strip()

    if not normalized:
        raise PathTraversalError(
            f"{field_name} cannot be empty after normalization",
            field_name=field_name,
            value=base_workspace,
            reason="empty after strip",
        )

    # Check for dangerous characters (allow more for filesystem paths)
    path_dangerous: Final[re.Pattern[str]] = re.compile(
        r"[\x00-\x1f]|"  # Control characters except newlines
        r"[$`|;<>\"']"  # Shell metacharacters
    )
    if path_dangerous.search(normalized):
        raise PathTraversalError(
            f"{field_name} contains dangerous characters",
            field_name=field_name,
            value=base_workspace,
            reason="contains control characters or shell metacharacters",
        )

    # Check for path traversal patterns (filesystem version - allows absolute paths)
    for pattern in FILESYSTEM_PATH_TRAVERSAL_PATTERNS:
        if pattern.search(normalized):
            raise PathTraversalError(
                f"{field_name} contains path traversal pattern",
                field_name=field_name,
                value=base_workspace,
                reason=f"matched forbidden pattern: {pattern.pattern}",
            )

    try:
        path = Path(normalized).resolve()
    except (OSError, ValueError) as exc:
        raise PathTraversalError(
            f"{field_name} cannot be resolved to a valid path",
            field_name=field_name,
            value=base_workspace,
            reason=f"Path resolution failed: {exc}",
        ) from exc

    # Check existence
    if must_exist and not path.exists():
        raise PathTraversalError(
            f"{field_name} does not exist",
            field_name=field_name,
            value=base_workspace,
            reason="path does not exist",
        )

    # Check if it's a directory
    if must_be_dir and path.exists() and not path.is_dir():
        raise PathTraversalError(
            f"{field_name} is not a directory",
            field_name=field_name,
            value=base_workspace,
            reason="path exists but is not a directory",
        )

    return path


def validate_case_id(
    case_id: str,
    *,
    field_name: str = "case_id",
) -> str:
    """Validate case_id for path traversal attempts.

    Args:
        case_id: The case_id to validate.
        field_name: Name of the field for error messages.

    Returns:
        The validated case_id (stripped of whitespace).

    Raises:
        PathTraversalError: If the case_id contains invalid patterns.
    """
    if not case_id:
        raise PathTraversalError(
            f"{field_name} cannot be empty",
            field_name=field_name,
            value=case_id,
            reason="empty value",
        )

    normalized = str(case_id).strip()

    if not normalized:
        raise PathTraversalError(
            f"{field_name} cannot be empty after normalization",
            field_name=field_name,
            value=case_id,
            reason="empty after strip",
        )

    # Check length
    if len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise PathTraversalError(
            f"{field_name} exceeds maximum length of {MAX_IDENTIFIER_LENGTH}",
            field_name=field_name,
            value=case_id,
            reason=f"length={len(normalized)}, max={MAX_IDENTIFIER_LENGTH}",
        )

    # Check for dangerous characters
    if DANGEROUS_PATH_CHARS.search(normalized):
        raise PathTraversalError(
            f"{field_name} contains dangerous characters",
            field_name=field_name,
            value=case_id,
            reason="contains control characters or shell metacharacters",
        )

    # Check for path traversal patterns
    for pattern in PATH_TRAVERSAL_PATTERNS:
        if pattern.search(normalized):
            raise PathTraversalError(
                f"{field_name} contains path traversal pattern",
                field_name=field_name,
                value=case_id,
                reason=f"matched forbidden pattern: {pattern.pattern}",
            )

    # case_id should match safe identifier pattern
    if not SAFE_IDENTIFIER_PATTERN.match(normalized):
        raise PathTraversalError(
            f"{field_name} contains invalid characters",
            field_name=field_name,
            value=case_id,
            reason="must match pattern: ^[a-zA-Z0-9_-]+$",
        )

    return normalized


__all__ = [
    "DANGEROUS_PATH_CHARS",
    "MAX_IDENTIFIER_LENGTH",
    "SAFE_IDENTIFIER_PATTERN",
    "PathTraversalError",
    "validate_base_workspace",
    "validate_case_id",
    "validate_run_id",
    "validate_workspace_fixture",
]
