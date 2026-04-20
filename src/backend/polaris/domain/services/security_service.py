"""Security service for Polaris backend.

Provides path sandboxing and dangerous command filtering.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Dangerous command patterns that should be blocked
DANGEROUS_PATTERNS = [
    # Destructive file operations
    (r"rm\s+-rf\s+/(?!\s)", "Recursive delete of root directory"),
    (r"rm\s+(-rf|-fr)\s+/[^\s]+", "Force recursive delete of system paths"),
    (r"rm\s+(-r|-f)\s+/[^\s]+", "Recursive or force delete of system paths"),
    # Disk destruction
    (r"mkfs\.\w+\s+/dev/", "Format a device"),
    (r"dd\s+if=.*\s+of=/dev/[sh]d[a-z]", "Direct disk write"),
    (r">\s*/dev/sda", "Overwrite disk"),
    (r">\s*/dev/hda", "Overwrite disk"),
    (r">\s*/dev/nvme", "Overwrite NVMe disk"),
    # Fork bombs and resource exhaustion
    (r":\s*\(\s*\)\s*\{.*:\s*\|.*:\s*&\s*\};?\s*:", "Fork bomb"),
    (r"while\s+(true|:\s*\(\))\s*;?\s*do\s*:;\s*done", "CPU exhaustion loop"),
    # Permission changes on system paths
    (r"chmod\s+-R\s+777\s+/(?!\s)", "Recursive permission change on root"),
    (r"chmod\s+-R\s+777\s+/[^\s]+", "Recursive permission change on system paths"),
    # Dangerous moves and copies
    (r"mv\s+.*\s+/dev/null", "Move to null device"),
    (r"cp\s+/dev/zero\s+/dev", "Zero fill device"),
    (r"cp\s+/dev/random\s+/dev", "Random fill device"),
    # Remote code execution risks
    (r"curl\s+.*\s*\|\s*sh", "Pipe curl to shell"),
    (r"curl\s+.*\s*\|\s*bash", "Pipe curl to bash"),
    (r"wget\s+.*\s*\|\s*sh", "Pipe wget to shell"),
    (r"wget\s+.*\s*\|\s*bash", "Pipe wget to bash"),
    # Suspicious downloads and execution
    (r"eval\s*\$", "Eval of variable"),
    (r"eval\s*\`", "Eval of backticks"),
    (r"eval\s*\$\(", "Eval of command substitution"),
]


@dataclass
class SecurityCheckResult:
    """Result of a security check."""

    is_safe: bool
    reason: str = ""
    pattern_matched: str = ""
    suggested_alternative: str | None = None


class SecurityService:
    """Security service for command and path validation."""

    def __init__(self, workspace_root: Path | str) -> None:
        """Initialize security service.

        Args:
            workspace_root: The allowed workspace root directory
        """
        self.workspace_root = Path(workspace_root).resolve()
        self._dangerous_patterns = [
            (re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in DANGEROUS_PATTERNS
        ]

    def is_path_safe(self, path: str | Path) -> SecurityCheckResult:
        """Check if a path is within the workspace sandbox.

        Args:
            path: The path to check

        Returns:
            SecurityCheckResult indicating if path is safe
        """
        try:
            # Resolve to absolute path
            target_path = Path(path).expanduser()

            # If relative, resolve against workspace
            if not target_path.is_absolute():
                target_path = self.workspace_root / target_path

            # Get real path (resolves symlinks)
            real_path = target_path.resolve()
            real_workspace = self.workspace_root.resolve()

            # Check if path is within workspace
            # Using commonpath for reliable comparison
            try:
                common = os.path.commonpath([real_path, real_workspace])
                is_within = common == str(real_workspace)
            except ValueError:
                # On Windows, different drives will raise ValueError
                is_within = False

            if not is_within:
                return SecurityCheckResult(
                    is_safe=False,
                    reason=f"Path '{path}' is outside workspace '{self.workspace_root}'",
                )

            return SecurityCheckResult(is_safe=True)

        except (RuntimeError, ValueError) as e:
            logger.warning("Path validation error: %s", e)
            return SecurityCheckResult(
                is_safe=False,
                reason=f"Path validation error: {e}",
            )

    def is_command_safe(self, command: str) -> SecurityCheckResult:
        """Check if a command is safe to execute.

        Args:
            command: The command to check

        Returns:
            SecurityCheckResult indicating if command is safe
        """
        # Check against dangerous patterns
        for pattern, reason in self._dangerous_patterns:
            if pattern.search(command):
                return SecurityCheckResult(
                    is_safe=False,
                    reason=reason,
                    pattern_matched=pattern.pattern,
                )

        return SecurityCheckResult(is_safe=True)

    def validate_file_operation(
        self,
        operation: str,
        path: str | Path,
    ) -> SecurityCheckResult:
        """Validate a file operation.

        Args:
            operation: Operation type (read, write, edit, delete)
            path: Target path

        Returns:
            SecurityCheckResult
        """
        # First check path safety
        path_check = self.is_path_safe(path)
        if not path_check.is_safe:
            return path_check

        # Additional checks for write operations
        if operation in ("write", "edit", "delete"):
            # Ensure path is not a parent of workspace
            real_path = Path(path).resolve()
            if real_path == self.workspace_root.resolve():
                return SecurityCheckResult(
                    is_safe=False,
                    reason=f"Cannot {operation} workspace root directory",
                )

        return SecurityCheckResult(is_safe=True)

    def sanitize_path(self, path: str | Path) -> Path:
        """Sanitize and resolve a path within workspace.

        Args:
            path: Input path

        Returns:
            Resolved Path within workspace

        Raises:
            SecurityError: If path is outside workspace
        """
        result = self.is_path_safe(path)
        if not result.is_safe:
            from polaris.domain.exceptions import PermissionDeniedError

            raise PermissionDeniedError(
                f"Path security check failed: {result.reason}",
                action="access",
                resource=str(path),
            )

        target_path = Path(path).expanduser()
        if not target_path.is_absolute():
            target_path = self.workspace_root / target_path

        return target_path.resolve()


def is_dangerous_command(command: str) -> tuple[bool, str]:
    """Quick check if a command is dangerous (global function).

    Args:
        command: Command string to check

    Returns:
        Tuple of (is_dangerous, reason)
    """
    service = SecurityService(Path.cwd())
    result = service.is_command_safe(command)
    return not result.is_safe, result.reason


# Global instance for quick access
_security_service: SecurityService | None = None


def get_security_service(workspace_root: Path | str | None = None) -> SecurityService:
    """Get or create global security service instance.

    Args:
        workspace_root: Workspace root (uses current directory if None)

    Returns:
        SecurityService instance
    """
    global _security_service

    if _security_service is None or (
        workspace_root is not None and _security_service.workspace_root != Path(workspace_root).resolve()
    ):
        if workspace_root is None:
            workspace_root = Path.cwd()
        _security_service = SecurityService(workspace_root)

    return _security_service


def reset_security_service() -> None:
    """Reset global security service (for testing)."""
    global _security_service
    _security_service = None
