"""Shared utilities for the executor module.

Contains path-related helper functions and BudgetExceededError re-export.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.exceptions import BudgetExceededError

if TYPE_CHECKING:
    from pathlib import Path

    from polaris.kernelone.fs import KernelFileSystem

logger = logging.getLogger(__name__)

__all__ = ["BudgetExceededError"]


def resolve_workspace_path(kernel_fs: KernelFileSystem, file: str) -> Path:
    """Resolve a file path relative to workspace.

    Args:
        kernel_fs: KernelFileSystem instance
        file: File path to resolve

    Returns:
        Resolved Path object
    """
    return kernel_fs.resolve_workspace_path(file)


def to_workspace_relative_path(kernel_fs: KernelFileSystem, path: Path) -> str:
    """Convert an absolute path to workspace-relative path.

    Args:
        kernel_fs: KernelFileSystem instance
        path: Path to convert

    Returns:
        Workspace-relative path string
    """
    return kernel_fs.to_workspace_relative_path(str(path))


def get_budget_remaining_lines(budget_state: Any | None) -> int | None:
    """Get remaining context budget as approximate line count.

    Args:
        budget_state: Budget state object (or None)

    Returns:
        Approximate remaining lines budget, or None if no budget state is set.
    """
    if budget_state is None:
        return None

    try:
        # Support BudgetState-style objects
        max_tokens = getattr(budget_state, "max_tokens", None)
        total_tokens = getattr(budget_state, "total_tokens", 0)

        if max_tokens is None:
            # Fallback: try result_size_bytes
            max_bytes = getattr(budget_state, "max_result_size_bytes", None)
            if max_bytes is None:
                return None
            total_bytes = getattr(budget_state, "result_size_bytes", 0)
            return max(0, (max_bytes - total_bytes) // 104)  # ~104 bytes/line

        # tokens -> lines: ~4 chars/token, ~100 chars/line
        chars_per_token = 4
        chars_per_line = 100
        tokens_per_line = chars_per_line / chars_per_token
        remaining = max(0, max_tokens - total_tokens)
        return int(remaining / tokens_per_line)
    except (AttributeError, TypeError, ValueError):
        return None
