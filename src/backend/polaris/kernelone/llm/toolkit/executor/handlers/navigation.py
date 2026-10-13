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
    depth = kwargs.get("depth")
    authorized = kwargs.get("authorized_read_paths")

    from polaris.kernelone.exceptions import PathSecurityError
    from polaris.kernelone.llm.toolkit.executor.read_scope import read_scope_permits
    from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path

    try:
        dir_path = resolve_workspace_path(self._kernel_fs, path)
        if authorized is not None and not read_scope_permits(
            self._kernel_fs, self._capability_token, [path], allow_filtered_ancestors=True
        ):
            return {"ok": False, "error": "Path outside committed read scope"}
        if not self._kernel_fs.workspace_exists(str(dir_path)):
            return {"ok": False, "error": f"Directory not found: {path}"}
        if not self._kernel_fs.workspace_is_dir(str(dir_path)):
            return {"ok": False, "error": f"Path is not a directory: {path}"}
        safe_max_entries = max(1, min(int(max_entries), 500))
        safe_depth = max(0, int(depth)) if depth is not None else None
    except (OSError, ValueError, TypeError, PathSecurityError) as e:
        return {"ok": False, "error": str(e)}

    entries: list[dict[str, Any]] = []
    candidates: set[Path] = set()

    def visible(item: Path) -> bool:
        relative = item.relative_to(dir_path)
        return (include_hidden or all(not part.startswith(".") for part in relative.parts)) and (
            safe_depth is None or len(relative.parts) <= safe_depth
        )

    def add_subtree(root: Path) -> None:
        for current, dirs, files in os.walk(root, followlinks=False):
            if len(candidates) >= safe_max_entries:
                break
            current_dir = Path(current)
            # Resolve every discovered path through KFS before metadata or traversal.
            safe_dirs: list[str] = []
            for name in sorted(dirs):
                if len(candidates) >= safe_max_entries:
                    break
                item = current_dir / name
                try:
                    resolved = resolve_workspace_path(self._kernel_fs, str(item))
                    if not visible(resolved):
                        continue
                except (OSError, ValueError, PathSecurityError):
                    continue
                candidates.add(resolved)
                if recursive and (safe_depth is None or len(resolved.relative_to(dir_path).parts) < safe_depth):
                    safe_dirs.append(name)
            dirs[:] = safe_dirs
            for name in sorted(files):
                if len(candidates) >= safe_max_entries:
                    break
                item = current_dir / name
                try:
                    resolved = resolve_workspace_path(self._kernel_fs, str(item))
                    if visible(resolved):
                        candidates.add(resolved)
                except (OSError, ValueError, PathSecurityError):
                    continue
            if not recursive:
                break

    try:
        if authorized is None:
            add_subtree(dir_path)
        else:
            # Exact files reveal only existing authorized paths and their ancestors.
            # Directory scopes permit their subtree, never unrelated root siblings.
            for allowed in authorized:
                if allowed.is_relative_to(dir_path):
                    for candidate in (allowed, *allowed.parents):
                        if candidate == dir_path:
                            break
                        if visible(candidate) and self._kernel_fs.workspace_exists(str(candidate)):
                            candidates.add(candidate)
                    if self._kernel_fs.workspace_is_dir(str(allowed)) and visible(allowed):
                        add_subtree(allowed)
                elif dir_path.is_relative_to(allowed) and self._kernel_fs.workspace_is_dir(str(allowed)):
                    add_subtree(dir_path)
        for item in sorted(candidates, key=lambda candidate: candidate.relative_to(dir_path).as_posix()):
            # Revalidate at materialization; disappeared or replaced symlinks stay hidden.
            item = resolve_workspace_path(self._kernel_fs, str(item))
            if not self._kernel_fs.workspace_exists(str(item)):
                continue
            is_dir = self._kernel_fs.workspace_is_dir(str(item))
            entry: dict[str, Any] = {
                "name": item.name,
                "path": item.relative_to(dir_path).as_posix(),
                "type": "dir" if is_dir else "file",
            }
            if not is_dir and self._kernel_fs.workspace_is_file(str(item)):
                entry["size"] = item.stat().st_size
            entries.append(entry)
            if len(entries) >= safe_max_entries:
                break
    except (OSError, ValueError, PathSecurityError) as e:
        return {"ok": False, "error": f"Directory listing failed: {e}"}

    result: dict[str, Any] = {
        "ok": True,
        "path": path,
        "total_entries": len(entries),
        "entries": entries,
    }
    if authorized is not None:
        result["scope_filtered"] = True
    return result


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
