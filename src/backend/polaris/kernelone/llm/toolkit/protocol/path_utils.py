"""Path utilities for the protocol module.

Contains path validation and security check functions.
"""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_path_safe(workspace: str, rel_path: str) -> tuple[bool, str]:
    """Check if path is safe (doesn't traverse workspace).

    Args:
        workspace: Workspace root directory
        rel_path: Relative path to check

    Returns:
        (is_safe, full_path or error_message)
    """
    try:
        workspace_real = Path(workspace).resolve()
        target = (workspace_real / rel_path).resolve()

        # Check if within workspace
        try:
            target.relative_to(workspace_real)
            return True, str(target)
        except ValueError:
            return False, ""
    except (RuntimeError, ValueError) as e:
        logger.warning("Path safety check failed: %s", e)
        return False, str(e)


def _detect_path_traversal(path: str) -> bool:
    """Detect path traversal attempts.

    Args:
        path: Path to check

    Returns:
        True if path traversal is detected
    """
    if not path:
        return False

    # Recursive URL decode until no changes (Path.resolve() does multi-layer decode)
    decoded = path
    for _ in range(10):  # Prevent infinite loop
        new_decoded = urllib.parse.unquote(decoded)
        if new_decoded == decoded:
            break
        decoded = new_decoded

    # Normalize backslashes for consistent checking
    normalized = decoded.replace("\\", "/")

    # Check for path traversal patterns (after full decoding)
    dangerous_patterns = ["../", "..\\", "..%", "..;", "..%00"]
    lower = normalized.lower()
    return any(p in lower for p in dangerous_patterns)
