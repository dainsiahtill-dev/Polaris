"""Navigation tool handlers.

Handles glob, list_directory, file_exists.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def register_handlers() -> dict[str, Any]:
    """Return a dict of handler names to handler methods."""
    return {
        "glob": _handle_glob,
        "list_directory": _handle_list_directory,
        "file_exists": _handle_file_exists,
    }


def _handle_glob(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle glob tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    pattern = kwargs.get("pattern", "")
    path = kwargs.get("path", ".")
    recursive = kwargs.get("recursive", False)
    include_hidden = kwargs.get("include_hidden", False)
    max_results = kwargs.get("max_results", 200)

    if not pattern or not isinstance(pattern, str):
        return {"ok": False, "error": "Missing or invalid pattern"}

    # Check for path traversal attempts in pattern
    normalized_pattern = str(pattern).replace("\\", "/").strip()
    # Comprehensive path traversal detection:
    # - Any path component equal to ".." (handles "foo/..", "../foo", "foo/../bar")
    # - Allow "...", ".hidden", etc. as legitimate names (not traversal)
    path_parts = normalized_pattern.split("/")
    if ".." in path_parts:
        # Security: reject patterns that attempt to escape workspace
        return {"ok": False, "error": "Path traversal not allowed"}

    from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path

    workspace_root = Path(self.workspace).resolve()
    search_path = resolve_workspace_path(self._kernel_fs, path)

    if not search_path.exists():
        return {"ok": False, "error": f"Path not found: {path}"}

    safe_max_results = max(1, min(int(max_results), 500))

    matches: list[str] = []
    try:
        # Determine glob pattern based on recursive flag and pattern content
        # Path.glob supports ** for recursive matching natively
        glob_pattern = _resolve_glob_pattern(normalized_pattern, recursive)

        # Use Path.glob for both recursive and non-recursive patterns
        for full_path in search_path.glob(glob_pattern):
            # Check hidden files filter: skip if the file itself OR any directory
            # directly within the search_path starts with '.' (consistent with list_directory behavior)
            # NOTE: We only check parents UP TO search_path, not the entire workspace_root.
            # This prevents filtering out files just because the workspace path contains
            # dot-prefixed directories (like .polaris).
            if not include_hidden:
                # Check if file/dir itself is hidden
                if full_path.name.startswith("."):
                    continue
                # Check if any parent directory DIRECTLY within search_path is hidden
                # Stop at search_path to avoid filtering based on workspace root path components
                in_hidden_dir = False
                for parent in full_path.parents:
                    if parent == search_path:
                        break  # Reached search_path, stop checking
                    if parent.name.startswith("."):
                        in_hidden_dir = True
                        break  # Found hidden dir between search_path and file, skip this file
                if in_hidden_dir:
                    continue

            # Get relative path from workspace root
            rel_path = full_path.relative_to(workspace_root).as_posix()

            # Note: Path.glob already handles non-recursive patterns correctly.
            # Pattern "src/*.py" only matches files directly in src/, not nested.
            # Pattern "*.py" only matches files in the search_path, not subdirectories.
            # No additional depth filtering needed since search_path is already
            # validated to be within workspace.

            matches.append(rel_path)
            if len(matches) >= safe_max_results:
                break

    except OSError as e:
        return {"ok": False, "error": f"Glob search failed: {e}"}

    return {
        "ok": True,
        "pattern": pattern,
        "path": path,
        "total_results": len(matches),
        "results": matches,
    }


def _resolve_glob_pattern(pattern: str, recursive: bool) -> str:
    """Resolve the glob pattern based on recursive flag.

    Args:
        pattern: The normalized pattern string
        recursive: Whether to search recursively

    Returns:
        The resolved glob pattern for Path.glob()
    """
    # If pattern contains **, it's already recursive
    if "**" in pattern:
        return pattern

    # If recursive is True, prepend **/ to make it recursive
    if recursive:
        return f"**/{pattern}"

    # Non-recursive: pattern is used as-is with Path.glob
    return pattern


def _handle_list_directory(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle list_directory tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    path = kwargs.get("path", ".")
    recursive = kwargs.get("recursive", False)
    include_hidden = kwargs.get("include_hidden", False)
    max_entries = kwargs.get("max_entries", 200)

    from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path

    try:
        dir_path = resolve_workspace_path(self._kernel_fs, path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not dir_path.exists():
        return {"ok": False, "error": f"Directory not found: {path}"}
    if not dir_path.is_dir():
        return {"ok": False, "error": f"Path is not a directory: {path}"}

    safe_max_entries = max(1, min(int(max_entries), 500))
    entries: list[dict[str, Any]] = []

    try:
        if recursive:
            for root, dirs, files in os.walk(dir_path):
                # Skip hidden directories
                if not include_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]

                current_dir = Path(root)

                for name in dirs:
                    if not include_hidden and name.startswith("."):
                        continue
                    full_path = current_dir / name
                    rel_path = full_path.relative_to(dir_path).as_posix()
                    entries.append(
                        {
                            "name": name,
                            "path": rel_path,
                            "type": "dir",
                        }
                    )
                    if len(entries) >= safe_max_entries:
                        break

                for name in files:
                    if not include_hidden and name.startswith("."):
                        continue
                    full_path = current_dir / name
                    rel_path = full_path.relative_to(dir_path).as_posix()
                    try:
                        size = full_path.stat().st_size
                    except OSError:
                        size = 0
                    entries.append(
                        {
                            "name": name,
                            "path": rel_path,
                            "type": "file",
                            "size": size,
                        }
                    )
                    if len(entries) >= safe_max_entries:
                        break

                if len(entries) >= safe_max_entries:
                    break
        else:
            for item in sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if not include_hidden and item.name.startswith("."):
                    continue
                rel_path = item.relative_to(dir_path).as_posix()
                entry: dict[str, Any] = {
                    "name": item.name,
                    "path": rel_path,
                    "type": "dir" if item.is_dir() else "file",
                }
                if item.is_file():
                    try:
                        entry["size"] = item.stat().st_size
                    except OSError:
                        entry["size"] = 0
                entries.append(entry)
                if len(entries) >= safe_max_entries:
                    break
    except OSError as e:
        return {"ok": False, "error": f"Directory listing failed: {e}"}

    return {
        "ok": True,
        "path": path,
        "total_entries": len(entries),
        "entries": entries,
    }


def _handle_file_exists(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle file_exists tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    path = kwargs.get("path", "")

    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing or invalid path"}

    from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path, to_workspace_relative_path

    try:
        target = resolve_workspace_path(self._kernel_fs, path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    rel = to_workspace_relative_path(self._kernel_fs, target)
    exists = self._kernel_fs.workspace_exists(rel)

    return {
        "ok": True,
        "path": path,
        "exists": exists,
        "is_file": self._kernel_fs.workspace_is_file(rel) if exists else False,
        "is_dir": self._kernel_fs.workspace_is_dir(rel) if exists else False,
    }
