"""Tree structure formatting utilities for LLM context injection.

This module provides standard tree structure formatting that prevents
LLM path hallucinations caused by ambiguous flat list representations.

Standard tree format example:
    .
    ├── backend/
    │   └── api.py
    ├── config.py
    └── README.md

LLMs are trained on tree command output and recognize ├── and └── as
standard hierarchy markers. This prevents the common "flat list misread
as hierarchy" hallucination.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path


def format_workspace_tree(
    workspace: Path,
    *,
    max_dirs: int = 20,
    max_files: int = 10,
    max_sub_items: int = 5,
    exclude_hidden: bool = True,
    exclude_dirs: tuple[str, ...] = (".github", ".vscode", "__pycache__", ".git"),
) -> str:
    """Generate a tree structure representation of a workspace.

    This function generates a standard tree-formatted string that clearly
    indicates directory hierarchy, preventing LLM path hallucinations
    where files in flat lists are misread as being inside directories.

    Args:
        workspace: Root directory to generate tree for.
        max_dirs: Maximum number of directories to show at root level.
        max_files: Maximum number of files to show at root level.
        max_sub_items: Maximum number of items to show inside each directory.
        exclude_hidden: Whether to exclude hidden files/directories.
        exclude_dirs: Directory names to always exclude.

    Returns:
        A tree-formatted string suitable for LLM context injection.

    Example output:
        .
        ├── backend/
        │   └── api.py
        ├── config.py
        └── README.md
    """
    try:
        # Collect root-level items
        root_dirs: list[Path] = []
        root_files: list[str] = []

        for item in sorted(workspace.iterdir()):
            name = item.name

            # Skip hidden items unless explicitly included
            if exclude_hidden and name.startswith(".") and name not in exclude_dirs:
                continue

            # Skip excluded directories
            if item.is_dir() and name in exclude_dirs:
                continue

            if item.is_dir():
                root_dirs.append(item)
            else:
                root_files.append(name)

        # Limit output size
        root_dirs = root_dirs[:max_dirs]
        root_files = root_files[:max_files]

        # Build tree lines
        lines = ["."]

        # Output directories with their contents
        for i, dir_path in enumerate(root_dirs):
            is_last_dir = (i == len(root_dirs) - 1) and len(root_files) == 0
            connector = "└── " if is_last_dir else "├── "
            lines.append(f"{connector}{dir_path.name}/")

            # List directory contents (up to max_sub_items)
            try:
                sub_items = sorted(
                    item for item in dir_path.iterdir() if exclude_hidden and not item.name.startswith(".")
                )
                for j, sub_item in enumerate(sub_items[:max_sub_items]):
                    is_last_sub = (j == len(sub_items) - 1) or (j == max_sub_items - 1)
                    indent = "    " if is_last_dir else "│   "
                    sub_connector = "└── " if is_last_sub else "├── "
                    suffix = "/" if sub_item.is_dir() else ""
                    lines.append(f"{indent}{sub_connector}{sub_item.name}{suffix}")

                # Show ellipsis if more items exist
                if len(sub_items) > max_sub_items:
                    indent = "    " if is_last_dir else "│   "
                    lines.append(f"{indent}    ... ({len(sub_items) - max_sub_items} more)")
            except PermissionError:
                indent = "    " if is_last_dir else "│   "
                lines.append(f"{indent}    [permission denied]")

        # Output root-level files
        for i, filename in enumerate(root_files):
            is_last = i == len(root_files) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{connector}{filename}")

        # Show ellipsis if more files exist
        total_items = len(root_dirs) + len(root_files)
        if total_items > max_dirs + max_files:
            lines.append(f"... ({total_items - max_dirs - max_files} more items omitted)")

        return "\n".join(lines)

    except PermissionError as exc:
        logger.warning("format_workspace_tree: permission denied accessing workspace: %r", exc)
        return "."
    except OSError as exc:
        logger.warning("format_workspace_tree: OS error while building tree: %r", exc)
        return "."
    except (RuntimeError, ValueError) as exc:
        logger.warning("format_workspace_tree: failed to format workspace tree: %r", exc)
        return "."


__all__ = ["format_workspace_tree"]
