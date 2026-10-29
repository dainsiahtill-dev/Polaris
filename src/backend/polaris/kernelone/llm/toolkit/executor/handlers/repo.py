"""Canonical repository tools (repo_*) handlers.

These handlers implement the canonical tools defined in contracts.py.
Each tool has precise semantics that must not be aliased to other tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def register_handlers() -> dict[str, Any]:
    """Return a dict of repo_* handler names to handler methods."""
    return {
        "repo_tree": _handle_repo_tree,
        "repo_rg": _handle_repo_rg,
        # ripgrep, search_code, and grep are deprecated aliases for repo_rg
        # but still need handlers to avoid "no handler found" errors
        "ripgrep": _handle_repo_rg,
        "search_code": _handle_repo_rg,
        "grep": _handle_repo_rg,
        "repo_read_around": _handle_repo_read_around,
        "repo_read_slice": _handle_repo_read_slice,
        "repo_read_head": _handle_repo_read_head,
        "repo_read_tail": _handle_repo_read_tail,
        "repo_diff": _handle_repo_diff,
        "repo_map": _handle_repo_map,
        "repo_symbols_index": _handle_repo_symbols_index,
        "repo_apply_diff": _handle_repo_apply_diff,
        "apply_patch": _handle_apply_patch,
    }


def _handle_repo_tree(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_tree tool call.

    List directory tree within the workspace.
    """
    from polaris.kernelone.exceptions import PathSecurityError
    from polaris.kernelone.llm.toolkit.executor.handlers.navigation import _handle_list_directory
    from polaris.kernelone.llm.toolkit.executor.read_scope import resolve_committed_read_paths

    path = kwargs.get("path", kwargs.get("root", kwargs.get("dir", ".")))
    try:
        authorized = resolve_committed_read_paths(self._kernel_fs, self._capability_token)
    except (OSError, ValueError, PathSecurityError):
        return {"ok": False, "error": "Missing or invalid committed read scope"}
    return cast(
        "dict[str, Any]",
        (
            _handle_list_directory(
                self,
                path=path,
                recursive=True,
                max_entries=kwargs.get("max_entries", 100),
                depth=kwargs.get("depth"),
                include_hidden=False,
                authorized_read_paths=authorized,
            )
        ),
    )


def _handle_repo_rg(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_rg tool call.

    Search for pattern matches using ripgrep.
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.search import _handle_unified_search

    # Map repo_rg args to unified search args
    pattern = (
        kwargs.get("pattern")
        or kwargs.get("query")
        or kwargs.get("text")
        or kwargs.get("search")
        or kwargs.get("keyword")
        or kwargs.get("q")
    )
    path = kwargs.get("path") or kwargs.get("file") or kwargs.get("file_path")
    max_results = kwargs.get("max_results", 50)
    file_patterns = [kwargs.get("glob")] if kwargs.get("glob") else None
    context_lines = kwargs.get("context_lines", 0)
    case_sensitive = kwargs.get("case_sensitive", False)

    return cast(
        "dict[str, Any]",
        (
            _handle_unified_search(
                self,
                query=pattern,
                path=path,
                max_results=max_results,
                file_patterns=file_patterns,
                context_lines=context_lines,
                case_sensitive=case_sensitive,
            )
        ),
    )


def _handle_repo_read_around(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_read_around tool call.

    Read a slice centered around a target line with configurable radius.
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_read_file

    file = kwargs.get("file") or kwargs.get("file_path") or kwargs.get("path")
    line = kwargs.get("line") or kwargs.get("center_line") or kwargs.get("around_line") or kwargs.get("line_number")
    radius = kwargs.get("radius", kwargs.get("window", 5))
    start = kwargs.get("start")
    end = kwargs.get("end")

    if not file:
        return {"ok": False, "error": "Missing required parameter: file"}

    # Calculate start/end from line + radius if not provided
    if line is not None and start is None and end is None:
        start = max(1, int(line) - radius)
        end = int(line) + radius

    if start is not None and end is not None:
        return cast(
            "dict[str, Any]",
            (
                _handle_read_file(
                    self,
                    file=file,
                    start_line=int(start),
                    end_line=int(end),
                )
            ),
        )

    return {"ok": False, "error": "Missing required parameters: line or start/end"}


def _handle_repo_read_slice(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_read_slice tool call.

    Read a precise line range [start, end] from a file.
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_read_file

    file = kwargs.get("file") or kwargs.get("file_path") or kwargs.get("path")
    start = kwargs.get("start") or kwargs.get("start_line")
    end = kwargs.get("end") or kwargs.get("end_line")

    if not file:
        return {"ok": False, "error": "Missing required parameter: file"}
    if start is None:
        return {"ok": False, "error": "Missing required parameter: start"}
    if end is None:
        return {"ok": False, "error": "Missing required parameter: end"}

    return cast(
        "dict[str, Any]",
        (
            _handle_read_file(
                self,
                file=file,
                start_line=int(start),
                end_line=int(end),
            )
        ),
    )


def _handle_repo_read_head(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_read_head tool call.

    Read the first N lines from a file (semantic: head -n).
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_read_file

    file = (
        kwargs.get("file")
        or kwargs.get("path")
        or kwargs.get("file_path")
        or kwargs.get("filepath")
        or kwargs.get("filename")
        or kwargs.get("target_file")
        or kwargs.get("target_path")
    )
    n = (
        kwargs.get("n")
        or kwargs.get("count")
        or kwargs.get("lines")
        or kwargs.get("max_lines")
        or kwargs.get("limit")
        or kwargs.get("max_bytes")
        or kwargs.get("first_n")
        or 50
    )

    if not file:
        return {"ok": False, "error": "Missing required parameter: file"}

    result = _handle_read_file(
        self,
        file=file,
        start_line=1,
        end_line=int(n),
    )

    if not result.get("ok"):
        return cast("dict[str, Any]", (result))

    # Enhance result with clear status message for LLM understanding
    content = result.get("content", "")
    line_count = result.get("line_count", 0)

    if not content or content.strip() == "":
        status_message = f"文件 '{file}' 内容为空（共 {line_count} 行）。这是正常结果，无需重试。"
    elif line_count < int(n):
        status_message = f"文件 '{file}' 共 {line_count} 行，已返回全部内容（请求前 {int(n)} 行）。"
    else:
        status_message = f"成功读取文件 '{file}' 前 {line_count} 行。"

    # Add status info to result
    result["status"] = "success"
    result["message"] = status_message

    return cast("dict[str, Any]", (result))


def _handle_repo_read_tail(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_read_tail tool call.

    Read the last N lines from a file (semantic: tail -n).
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_read_file

    file = (
        kwargs.get("file")
        or kwargs.get("path")
        or kwargs.get("file_path")
        or kwargs.get("filepath")
        or kwargs.get("filename")
        or kwargs.get("target_file")
        or kwargs.get("target_path")
    )
    n = (
        kwargs.get("n")
        or kwargs.get("count")
        or kwargs.get("lines")
        or kwargs.get("max_lines")
        or kwargs.get("limit")
        or 50
    )

    if not file:
        return {"ok": False, "error": "Missing required parameter: file"}

    # For tail, we need to read from the end
    # Read entire file and slice - this is a limitation
    result = _handle_read_file(self, file=file, start_line=1, end_line=9999999)
    if not result.get("ok"):
        return cast("dict[str, Any]", (result))

    # _handle_read_file returns content at top level, not nested in "result"
    content = result.get("content", "")
    lines = content.split("\n") if content else []
    tail_lines = lines[-int(n) :] if len(lines) > int(n) else lines
    tail_content = "\n".join(tail_lines)
    total_lines = len(lines)
    returned_lines = len(tail_lines)

    # Build clear status message for LLM understanding
    if not content or content.strip() == "":
        status_message = f"文件 '{file}' 内容为空（共 {total_lines} 行）。这是正常结果，无需重试。"
    elif returned_lines < int(n):
        status_message = f"文件 '{file}' 共 {total_lines} 行，已返回全部内容（请求最后 {int(n)} 行）。"
    else:
        status_message = f"成功读取文件 '{file}' 最后 {returned_lines} 行（共 {total_lines} 行）。"

    return {
        "ok": True,
        "result": {
            "file": file,
            "content": tail_content,
            "line_count": returned_lines,
            "total_lines": total_lines,
            "mode": "tail",
            "status": "success",
            "message": status_message,
        },
    }


def _handle_repo_diff(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_diff tool call.

    Show uncommitted changes using git diff.
    """
    import subprocess

    stat = kwargs.get("stat", False)
    mode = kwargs.get("mode", "unified")

    try:
        cmd = ["git", "diff"]
        if stat:
            cmd.append("--stat")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self._kernel_fs._workspace if hasattr(self._kernel_fs, "_workspace") else ".",
            timeout=30,
        )

        return {
            "ok": True,
            "result": {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "mode": mode,
            },
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git diff timed out"}
    except (RuntimeError, ValueError) as e:
        return {"ok": False, "error": f"git diff failed: {e}"}


def _handle_repo_map(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_map tool call.

    Build a code map showing file skeletons with top-level definitions.
    Extracts classes, functions, and other top-level symbols from source files.

    Args:
        self: Executor instance
        **kwargs: Tool arguments
            - root: Root directory to scan (default: ".")
            - max_files: Maximum number of files to process (default: 200)
            - languages: List of language extensions to include (e.g., ["py", "js"])
            - per_file_lines: Number of lines to show per symbol context (default: 12)
            - include_symbols: Include symbol definitions (default: True)

    Returns:
        Code map with file structure and top-level symbols
    """
    from pathlib import Path

    from polaris.kernelone.llm.toolkit.executor.handlers.navigation import _handle_list_directory

    root = kwargs.get("root", kwargs.get("path", kwargs.get("dir", ".")))
    max_files = kwargs.get("max_files", 200)
    languages = kwargs.get("languages")
    per_file_lines = kwargs.get("per_file_lines", 12)
    include_symbols = kwargs.get("include_symbols", True)

    # Normalize root path
    root_path = Path(root).as_posix().strip("/")

    # Collect all files recursively from directory tree
    files: list[dict[str, Any]] = []
    dirs_to_scan = [root_path]  # Use normalized root path

    while dirs_to_scan and len(files) < max_files:
        current_dir = dirs_to_scan.pop(0)

        # Get directory listing
        list_result = _handle_list_directory(
            self,
            path=current_dir,
            recursive=False,
            max_entries=max_files,
            include_hidden=False,
        )

        if not list_result.get("ok"):
            continue

        # Note: _handle_list_directory returns entries at top level, not under "result"
        entries = list_result.get("entries", [])

        for entry in entries:
            entry_type = entry.get("type", "")
            entry_name = entry.get("name", "")

            # IMPORTANT: entry_path is relative to current_dir
            # We need to join them to get the full path
            if entry_type == "dir":
                # Join current_dir with entry_name to get full subdirectory path
                subdir_path = f"{current_dir}/{entry_name}" if current_dir != "." else entry_name
                # Add subdirectory for scanning
                if len(files) < max_files:
                    dirs_to_scan.append(subdir_path)
            elif entry_type == "file":
                # Join current_dir with entry_name to get full file path
                file_path = f"{current_dir}/{entry_name}" if current_dir != "." else entry_name

                # Filter by language if specified
                if languages:
                    lang_set = {lang_item.lower().lstrip(".") for lang_item in languages}
                    if not any(entry_name.lower().endswith(f".{lang}") for lang in lang_set):
                        continue

                files.append(
                    {
                        "name": entry_name,
                        "path": file_path,
                        "type": "file",
                        "size": entry.get("size", 0),
                    }
                )

                if len(files) >= max_files:
                    break

    # Extract symbols from each file
    file_skeletons = []
    symbols_extracted = 0

    for file_entry in files[:max_files]:
        file_path = file_entry.get("path", "")
        file_name = file_entry.get("name", "")

        if not file_path:
            continue

        skeleton = {
            "file": file_path,
            "name": file_name,
            "symbols": [],
        }

        if include_symbols:
            # Try to extract top-level symbols
            symbols = _extract_top_level_symbols(self, file_path, per_file_lines)
            skeleton["symbols"] = symbols
            symbols_extracted += len(symbols)

        file_skeletons.append(skeleton)

    return {
        "ok": True,
        "root": root_path,
        "files_scanned": len(files),
        "files_with_symbols": sum(1 for f in file_skeletons if f.get("symbols")),
        "total_symbols": symbols_extracted,
        "file_skeletons": file_skeletons,
        "languages": languages,
        "per_file_lines": per_file_lines,
    }


def _extract_top_level_symbols(
    self: AgentAccelToolExecutor,
    file_path: str,
    context_lines: int = 12,
) -> list[dict[str, Any]]:
    """Extract top-level symbols from a source file.

    Args:
        self: Executor instance
        file_path: Path to the source file
        context_lines: Number of lines to include as preview

    Returns:
        List of symbol definitions with location and preview
    """
    from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path, to_workspace_relative_path

    try:
        target = resolve_workspace_path(self._kernel_fs, file_path)
        rel = to_workspace_relative_path(self._kernel_fs, target)
    except (ValueError, OSError):
        return []

    if not self._kernel_fs.workspace_exists(rel):
        return []

    try:
        raw = self._kernel_fs.workspace_read_bytes(rel)
        content = raw.decode("utf-8")
        lines = content.split("\n")
    except (OSError, UnicodeDecodeError):
        return []

    # Simple regex-based symbol extraction
    # This works for most languages but is not AST-aware
    symbols = []
    file_ext = file_path.lower().split(".")[-1] if "." in file_path else ""

    # Language-specific patterns
    patterns = _get_symbol_patterns(file_ext)

    for line_num, line in enumerate(lines, 1):
        for pattern_name, pattern_regex in patterns.items():
            import re

            match = re.match(pattern_regex, line.strip())
            if match:
                symbol_name = match.group(1) if match.groups() else line.strip()[:50]

                # Get preview (context around the symbol)
                start_idx = max(0, line_num - 1)
                end_idx = min(len(lines), line_num + context_lines)
                preview_lines = lines[start_idx:end_idx]

                symbols.append(
                    {
                        "name": symbol_name,
                        "kind": pattern_name,
                        "line": line_num,
                        "preview": "\n".join(preview_lines),
                    }
                )
                break  # Only first match per line

    return symbols


def _get_symbol_patterns(file_ext: str) -> dict[str, str]:
    """Get regex patterns for top-level symbols based on file extension.

    Args:
        file_ext: File extension (without dot)

    Returns:
        Dict of symbol_kind -> regex pattern
    """
    patterns = {
        # Python
        "py": {
            "class": r"^class\s+(\w+)",
            "def": r"^def\s+(\w+)\s*\(",
            "async_def": r"^async\s+def\s+(\w+)\s*\(",
            "decorator": r"^@\w+.*",
            "const": r"^[A-Z][A-Z0-9_]*\s*=",
        },
        # JavaScript/TypeScript
        "js": {
            "class": r"^class\s+(\w+)",
            "function": r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
            "const": r"^(?:export\s+)?const\s+(\w+)\s*=",
            "let": r"^(?:export\s+)?let\s+(\w+)\s*=",
        },
        "ts": {
            "class": r"^class\s+(\w+)",
            "interface": r"^interface\s+(\w+)",
            "type": r"^type\s+(\w+)",
            "function": r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
            "const": r"^(?:export\s+)?const\s+(\w+)\s*[=:]",
        },
        "tsx": {
            "class": r"^class\s+(\w+)",
            "function": r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
            "component": r"^(?:export\s+)?(?:const|function)\s+(\w+)\s*=",
        },
        "jsx": {
            "class": r"^class\s+(\w+)",
            "function": r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
            "component": r"^(?:export\s+)?(?:const|function)\s+(\w+)\s*=",
        },
        # Java
        "java": {
            "class": r"^(?:public\s+)?class\s+(\w+)",
            "interface": r"^(?:public\s+)?interface\s+(\w+)",
            "enum": r"^(?:public\s+)?enum\s+(\w+)",
        },
        # Go
        "go": {
            "func": r"^func\s+(\w+)\s*\(",
            "type": r"^type\s+(\w+)",
        },
        # Rust
        "rs": {
            "struct": r"^struct\s+(\w+)",
            "enum": r"^enum\s+(\w+)",
            "fn": r"^fn\s+(\w+)",
            "impl": r"^impl\s+(?:<[^>]+>\s+)?(\w+)",
            "trait": r"^trait\s+(\w+)",
        },
        # C/C++
        "c": {
            "struct": r"^struct\s+(\w+)",
            "typedef": r"^typedef\s+(\w+)",
            "function": r"^\w+\s+(\w+)\s*\(",
        },
        "cpp": {
            "class": r"^class\s+(\w+)",
            "struct": r"^struct\s+(\w+)",
            "function": r"^\w+\s+(\w+)\s*\(",
        },
        "h": {
            "class": r"^class\s+(\w+)",
            "struct": r"^struct\s+(\w+)",
        },
        "hpp": {
            "class": r"^class\s+(\w+)",
            "struct": r"^struct\s+(\w+)",
        },
        # Ruby
        "rb": {
            "class": r"^class\s+(\w+)",
            "module": r"^module\s+(\w+)",
            "def": r"^def\s+(\w+)",
        },
        # PHP
        "php": {
            "class": r"^class\s+(\w+)",
            "interface": r"^interface\s+(\w+)",
            "trait": r"^trait\s+(\w+)",
            "function": r"^function\s+(\w+)",
        },
        # C#
        "cs": {
            "class": r"^(?:public\s+)?class\s+(\w+)",
            "interface": r"^(?:public\s+)?interface\s+(\w+)",
            "struct": r"^(?:public\s+)?struct\s+(\w+)",
            "enum": r"^(?:public\s+)?enum\s+(\w+)",
        },
        # Shell
        "sh": {
            "function": r"^function\s+(\w+)|^\w+\s*\(\s*\)\s*\{",
            "alias": r"^alias\s+(\w+)=",
        },
        "bash": {
            "function": r"^function\s+(\w+)|^\w+\s*\(\s*\)\s*\{",
        },
        # YAML/JSON config
        "yaml": {
            "key": r"^(\w+):",
        },
        "yml": {
            "key": r"^(\w+):",
        },
        "json": {
            "key": r"^\s*\"(\w+)\"\s*:",
        },
        # Markdown
        "md": {
            "heading": r"^#+\s+(.+)",
        },
        # SQL
        "sql": {
            "create_table": r"^CREATE\s+TABLE\s+(\w+)",
            "create_index": r"^CREATE\s+INDEX\s+(\w+)",
            "create_view": r"^CREATE\s+VIEW\s+(\w+)",
            "procedure": r"^CREATE\s+PROCEDURE\s+(\w+)",
        },
    }

    # Return patterns for the extension, or empty dict for unknown
    return patterns.get(file_ext.lower(), {})


def _handle_repo_symbols_index(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_symbols_index tool call.

    Index and list all top-level symbols (classes, functions) across files using tree-sitter.
    """
    from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path, to_workspace_relative_path

    paths_input = kwargs.get("paths", ["."])
    max_results = kwargs.get("max_results", 500)

    # Normalize paths to list
    if isinstance(paths_input, str):
        paths_input = [paths_input]
    paths_input = [str(p) for p in paths_input if p]

    all_symbols = []
    files_processed = 0
    errors = []

    # Collect files to process
    files_to_process = []
    for path_spec in paths_input:
        try:
            target = resolve_workspace_path(self._kernel_fs, path_spec)
            rel = to_workspace_relative_path(self._kernel_fs, target)
        except (ValueError, OSError) as e:
            errors.append(f"Failed to resolve {path_spec}: {e}")
            continue

        if not self._kernel_fs.workspace_exists(rel):
            errors.append(f"Path not found: {path_spec}")
            continue

        # Check if it's a file or directory
        try:
            from polaris.kernelone.fs import KernelFileSystem

            is_dir = self._kernel_fs.workspace_is_dir(rel) if isinstance(self._kernel_fs, KernelFileSystem) else False
        except (RuntimeError, ValueError):
            is_dir = False

        if is_dir:
            # Recursively list all files in directory using os.walk
            import os
            from pathlib import Path

            try:
                dir_path = self._kernel_fs.resolve_workspace_path(rel)
                workspace_root = Path(self._kernel_fs.workspace).resolve()
                for root, dirs, files in os.walk(dir_path):
                    # Skip hidden directories
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for name in files:
                        if name.startswith("."):
                            continue
                        full_path = Path(root) / name
                        entry_path = full_path.relative_to(workspace_root).as_posix()
                        if _is_source_file(entry_path):
                            files_to_process.append(entry_path)
            except (RuntimeError, ValueError) as e:
                errors.append(f"Failed to list directory {path_spec}: {e}")
        elif _is_source_file(rel):
            files_to_process.append(rel)

    # Process each file
    remaining_results = max_results
    for file_path in files_to_process[:100]:  # Limit to prevent infinite recursion
        if remaining_results <= 0:
            break

        try:
            target = resolve_workspace_path(self._kernel_fs, file_path)
            rel = to_workspace_relative_path(self._kernel_fs, target)

            if not self._kernel_fs.workspace_exists(rel):
                continue

            raw = self._kernel_fs.workspace_read_bytes(rel)
            content = raw.decode("utf-8", errors="replace")

            # Determine language from extension
            lang = _detect_language(file_path)
            if not lang:
                continue

            # Get tree-sitter parser and extract all top-level symbols
            symbols = _find_all_symbols_ts(content, lang, remaining_results)
            for sym in symbols:
                sym["file"] = rel
                all_symbols.append(sym)
                remaining_results -= 1
                if remaining_results <= 0:
                    break

            files_processed += 1

        except (RuntimeError, ValueError) as e:
            errors.append(f"Failed to process {file_path}: {e}")

    result = {
        "ok": True,
        "files_processed": files_processed,
        "total_symbols": len(all_symbols),
        "symbols": all_symbols[:max_results],
    }
    if errors:
        result["warnings"] = errors[:5]  # Limit error数量
    return result


def _is_source_file(path: str) -> bool:
    """Check if path is a source code file."""
    source_extensions = {
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".swift",
        ".kt",
    }
    return any(str(path).lower().endswith(ext) for ext in source_extensions)


def _detect_language(file_path: str) -> str | None:
    """Detect language from file extension."""
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "jsx",
        ".tsx": "tsx",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".swift": "swift",
        ".kt": "kotlin",
    }
    for ext, lang in ext_map.items():
        if str(file_path).lower().endswith(ext):
            return lang
    return None


# ---------------------------------------------------------------------------
# tree-sitter binding adapters
#
# Two incompatible tree-sitter Python bindings exist in the wild and may be
# installed by ``tree_sitter_language_pack`` depending on platform/wheels:
#
#   * py-tree-sitter (pure-binding): ``Tree.root_node`` is a property, nodes
#     expose ``type`` / ``children`` / ``start_point`` / ``start_byte`` as
#     *properties*, and ``Parser.parse`` requires ``bytes``.
#   * Rust-backed binding (``builtins.Parser`` / ``builtins.Node``): the same
#     concepts are *methods* — ``root_node()`` / ``kind()`` /
#     ``start_position()`` / ``start_byte()`` — there is no ``.children``
#     attribute (iterate via ``child_count()`` + ``child(i)``), and
#     ``Parser.parse`` requires ``str``.
#
# The helpers below normalise across both so symbol extraction works
# regardless of which binding is present, instead of raising a ``TypeError``.
# ---------------------------------------------------------------------------


def _ts_call_or_value(obj: Any, name: str) -> Any:
    """Return ``obj.<name>`` whether it is a property or a zero-arg method."""
    value = getattr(obj, name)
    if callable(value):
        return value()
    return value


def _ts_node_kind(node: Any) -> str:
    """Return the grammar node type across both bindings (``type``/``kind``)."""
    if hasattr(node, "type"):
        return str(_ts_call_or_value(node, "type"))
    if hasattr(node, "kind"):
        return str(_ts_call_or_value(node, "kind"))
    return ""


def _ts_node_children(node: Any) -> list[Any]:
    """Return child nodes across both bindings."""
    if hasattr(node, "children"):
        children = _ts_call_or_value(node, "children")
        if children is not None:
            return list(children)
    if hasattr(node, "child_count") and hasattr(node, "child"):
        count = int(_ts_call_or_value(node, "child_count"))
        return [node.child(i) for i in range(count)]
    return []


def _ts_node_start_rowcol(node: Any) -> tuple[int, int]:
    """Return (row, col) of a node start across both bindings."""
    point: Any = None
    if hasattr(node, "start_point"):
        point = _ts_call_or_value(node, "start_point")
    elif hasattr(node, "start_position"):
        point = _ts_call_or_value(node, "start_position")
    if point is None:
        return (0, 0)
    # py-tree-sitter exposes a 2-tuple; the Rust binding exposes a Point object.
    if hasattr(point, "row") and hasattr(point, "column"):
        return (int(point.row), int(point.column))
    try:
        row, col = point[0], point[1]
        return (int(row), int(col))
    except (TypeError, IndexError, KeyError):
        return (0, 0)


def _ts_parse(parser: Any, source_bytes: bytes) -> Any:
    """Parse source, tolerating bindings that want ``str`` vs ``bytes``."""
    # Rust-backed binding wants str; py-tree-sitter wants bytes. Try both.
    try:
        return parser.parse(source_bytes)
    except TypeError:
        return parser.parse(source_bytes.decode("utf-8", errors="replace"))


def _find_all_symbols_ts(content: str, language: str, max_results: int = 500) -> list[dict[str, Any]]:
    """Find all top-level symbols in source code using tree-sitter.

    This is a standalone version that doesn't require executor instance.

    Fails *soft* (returns ``[]``) when the tree-sitter grammar/binding is
    unavailable or rejects the input, so the calling handler never raises.
    """
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        logger.debug("tree_sitter_language_pack not installed; symbols index unavailable")
        return []

    from typing import cast

    try:
        parser = get_parser(cast("str", language))
    except (LookupError, RuntimeError, ValueError) as e:
        logger.debug("tree-sitter grammar unavailable for language '%s': %s", language, e)
        return []
    if parser is None:
        return []

    source_bytes = content.encode("utf-8", errors="replace")
    try:
        tree = _ts_parse(parser, source_bytes)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.debug("tree-sitter parse failed for language '%s': %s", language, e)
        return []

    try:
        root = _ts_call_or_value(tree, "root_node")
    except (AttributeError, RuntimeError, ValueError):
        return []

    # Define symbol node types per language
    symbol_types = {
        "python": ("class_definition", "function_definition", "decorated_definition"),
        "javascript": (
            "class_declaration",
            "function_declaration",
            "function_definition",
            "arrow_function",
            "method_definition",
        ),
        "typescript": ("class_declaration", "function_declaration", "function_definition", "method_definition"),
        "jsx": ("class_declaration", "function_declaration", "function_definition"),
        "tsx": ("class_declaration", "function_declaration", "function_definition"),
        "java": ("class_declaration", "interface_declaration", "method_declaration"),
        "go": ("function_declaration", "type_declaration", "method_declaration"),
        "rust": ("function_item", "struct_item", "enum_item", "impl_item"),
        "c": ("function_definition", "struct_specifier"),
        "cpp": ("function_definition", "class_specifier", "struct_specifier"),
        "csharp": ("class_declaration", "method_declaration", "interface_declaration"),
    }

    target_types = symbol_types.get(
        language, ("function_declaration", "function_definition", "class_declaration", "class_definition")
    )

    def _slice_name(node: Any) -> str:
        # Byte offsets index into the UTF-8 bytes (correct for non-ASCII).
        start = int(_ts_call_or_value(node, "start_byte"))
        end = int(_ts_call_or_value(node, "end_byte"))
        return source_bytes[start:end].decode("utf-8", errors="replace")

    def extract_name(node: Any) -> str:
        """Extract identifier name from a node."""
        for field in ("name", "property", "identifier"):
            child = node.child_by_field_name(field)
            if child is not None and _ts_node_kind(child) == "identifier":
                return _slice_name(child)
        for child in _ts_node_children(node):
            if _ts_node_kind(child) == "identifier":
                return _slice_name(child)
        return ""

    def search_node(node: Any, results: list[dict[str, Any]]) -> None:
        if len(results) >= max_results:
            return
        node_type = _ts_node_kind(node)
        if node_type in target_types:
            name = extract_name(node)
            if name:
                row, col = _ts_node_start_rowcol(node)
                results.append(
                    {
                        "name": name,
                        "kind": node_type,
                        "line": row + 1,
                        "col": col,
                    }
                )
        for child in _ts_node_children(node):
            search_node(child, results)

    results: list[dict[str, Any]] = []
    search_node(root, results)
    return results


def _handle_repo_apply_diff(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_apply_diff tool call.

    Apply a unified diff to the repository.
    Supports:
    - Standard unified diff format
    - Context-only diffs
    - Multi-file diffs (returns results for each file)
    - Validation before applying

    Args:
        self: Executor instance
        **kwargs: Tool arguments
            - diff: Unified diff string (required)
            - patch: Alias for diff (optional)
            - dry_run: Validate without applying (default: False)
            - strict: Fail on hunk failures (default: True)

    Returns:
        Execution result dict with applied changes
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import (
        _emit_file_written_event,
        _validate_director_policy_for_write,
    )
    from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path, to_workspace_relative_path

    diff = kwargs.get("diff")
    patch = kwargs.get("patch")
    dry_run = kwargs.get("dry_run", False)
    strict = kwargs.get("strict", True)

    if not diff and not patch:
        return {"ok": False, "error": "Missing required parameter: diff or patch"}

    raw_content = diff or patch
    content = str(raw_content).strip() if raw_content else ""

    # Parse unified diff into hunks
    hunks = _parse_unified_diff(content)
    if not hunks:
        return {"ok": False, "error": "Invalid diff format: no valid hunks found"}

    # Mandatory read-before-edit enforcement for repo_apply_diff
    # Extract first file path from diff to check if file was recently read
    first_file = hunks[0]["file"] if hunks else None
    if first_file:
        try:
            first_target = resolve_workspace_path(self._kernel_fs, first_file)
            first_rel = to_workspace_relative_path(self._kernel_fs, first_target)
            last_read_seq = self._file_read_history.get(first_rel)
            if last_read_seq is None or (self._read_sequence - last_read_seq) > self._read_sequence_window:
                return {
                    "ok": False,
                    "error": (
                        f"Action Denied: You are attempting to apply a diff to '{first_rel}' "
                        "without a fresh read. "
                        "This is the primary cause of diff failures due to stale content. "
                        "MANDATORY: You MUST call read_file(file='{rel}') first to sync the exact file content, "
                        "then retry with a diff based on the verified content."
                    ).format(rel=first_rel),
                    "tool": "repo_apply_diff",
                    "error_type": "stale_edit",
                    "retryable": True,
                    "blocked": False,
                    "loop_break": False,
                }
        except (ValueError, OSError):
            pass  # Let the handler deal with invalid paths

    if dry_run:
        # Just validate the diff
        validation_results = []
        for hunk in hunks:
            validation_results.append(
                {
                    "file": hunk["file"],
                    "valid": True,
                    "hunks": len(hunk["changes"]),
                }
            )
        return {
            "ok": True,
            "files": len(hunks),
            "validation": validation_results,
        }

    # Apply each hunk
    results: list[dict[str, Any]] = []
    director_policy_denials: list[dict[str, Any]] = []
    all_ok = True

    for hunk in hunks:
        file_path = hunk["file"]
        changes = hunk["changes"]

        # Resolve file path
        try:
            target = resolve_workspace_path(self._kernel_fs, file_path)
            rel = to_workspace_relative_path(self._kernel_fs, target)
        except (ValueError, OSError):
            results.append(
                {
                    "file": file_path,
                    "ok": False,
                    "error": f"Invalid path: {file_path}",
                }
            )
            all_ok = False
            continue

        if not self._kernel_fs.workspace_exists(rel):
            results.append(
                {
                    "file": file_path,
                    "ok": False,
                    "error": f"File not found: {file_path}",
                }
            )
            all_ok = False
            continue

        # Read file content
        try:
            raw = self._kernel_fs.workspace_read_bytes(rel)
            file_content = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            results.append(
                {
                    "file": file_path,
                    "ok": False,
                    "error": f"Failed to read file: {e}",
                }
            )
            all_ok = False
            continue

        # Apply each change in the hunk
        modified_content = file_content
        file_write_needed = False
        for change in changes:
            search_text = change.get("search")
            replace_text = change.get("replace")

            if search_text is None:
                continue

            if replace_text is None:
                replace_text = ""

            if search_text in modified_content:
                modified_content = modified_content.replace(search_text, replace_text, 1)
                file_write_needed = True
            elif strict:
                results.append(
                    {
                        "file": file_path,
                        "ok": False,
                        "error": f"Search text not found in file: {search_text[:50]}...",
                    }
                )
                all_ok = False
                # Search failure with strict=True: skip write and don't append success result
                file_write_needed = False
                break

        # Write modified content only if at least one change was applied
        if file_write_needed:
            policy_result = _validate_director_policy_for_write(
                self,
                rel=rel,
                old_content=file_content,
                new_content=modified_content,
                operation="repo_apply_diff",
                tool_kwargs=kwargs,
            )
            if not policy_result.get("ok"):
                denial = {
                    "file": file_path,
                    "error": policy_result.get("error", "Director write policy denied"),
                    "director_policy": policy_result.get("director_policy"),
                }
                director_policy_denials.append(denial)
                results.append(
                    {
                        "file": file_path,
                        "ok": False,
                        "error": denial["error"],
                        "error_type": "director_write_policy_denied",
                        "director_policy": denial["director_policy"],
                    }
                )
                all_ok = False
                continue
            try:
                self._kernel_fs.workspace_write_text(rel, modified_content, encoding="utf-8")
                _emit_file_written_event(
                    self,
                    file_path=rel,
                    operation="modify",
                    old_content=file_content,
                    new_content=modified_content,
                )
                results.append(
                    {
                        "file": file_path,
                        "ok": True,
                        "changes_applied": len(changes),
                        "director_policy": policy_result.get("director_policy"),
                    }
                )
            except (OSError, UnicodeDecodeError) as e:
                results.append(
                    {
                        "file": file_path,
                        "ok": False,
                        "error": f"Failed to write file: {e}",
                    }
                )
                all_ok = False

    return {
        "ok": all_ok,
        "files_processed": len(results),
        "files_ok": sum(1 for r in results if r.get("ok")),
        "results": results,
        **({"director_policy_denials": director_policy_denials} if director_policy_denials else {}),
        **({"error_type": "director_write_policy_denied", "blocked": True} if director_policy_denials else {}),
        **({} if all_ok else {"error": _summarize_errors(results)}),
    }


def _handle_apply_patch(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle apply_patch formatted patch text through the unified write gate."""
    from polaris.kernelone.editing.patch_engine import extract_apply_patch_operations
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import (
        _emit_file_written_event,
        _validate_director_policy_for_write,
    )
    from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path, to_workspace_relative_path

    patch = kwargs.get("patch") or kwargs.get("diff") or kwargs.get("content") or kwargs.get("text")
    if not patch:
        return {"ok": False, "error": "Missing required parameter: patch"}

    operations = extract_apply_patch_operations(str(patch))
    if not operations:
        return {"ok": False, "error": "Invalid apply_patch format: no operations found"}

    results: list[dict[str, Any]] = []
    director_policy_denials: list[dict[str, Any]] = []
    all_ok = True

    for operation in operations:
        result = _apply_routed_patch_operation(
            self,
            operation=operation,
            tool_kwargs=kwargs,
            resolve_workspace_path=resolve_workspace_path,
            to_workspace_relative_path=to_workspace_relative_path,
            validate_policy=_validate_director_policy_for_write,
            emit_file_written=_emit_file_written_event,
        )
        results.append(result)
        if not result.get("ok"):
            all_ok = False
            if result.get("error_type") == "director_write_policy_denied":
                director_policy_denials.append(
                    {
                        "file": result.get("file", operation.path),
                        "error": result.get("error", "Director write policy denied"),
                        "director_policy": result.get("director_policy"),
                    }
                )

    # Aggregate the changed-file evidence over every operation that produced
    # a real file effect, including a partially applied move.  A failed
    # operation is not automatically a no-effect operation: a move whose
    # source deletion failed still wrote its destination, and that write must
    # stay visible instead of being reported as files_changed=[].
    changed_files: list[str] = []
    director_policy_by_file: dict[str, Any] = {}
    partial_effects: list[dict[str, Any]] = []
    for result in results:
        observed = _apply_patch_observed_changed_files(result)
        for path in observed:
            if path not in changed_files:
                changed_files.append(path)
        policy = result.get("director_policy")
        if policy:
            anchor = str(result.get("file") or "")
            if anchor:
                director_policy_by_file[anchor] = policy
        if result.get("partial"):
            partial_effects.append(
                {
                    "file": result.get("file"),
                    "source_file": result.get("source_file"),
                    "destination_file": result.get("destination_file"),
                    "error_type": result.get("error_type"),
                    "move_effects": result.get("move_effects"),
                    "files_changed": observed,
                }
            )

    effect_receipt: dict[str, Any] = {
        "operation": "apply_patch",
        "files_changed": changed_files,
        "director_policy": director_policy_by_file,
    }
    move_effects = _apply_patch_move_effects(results)
    if move_effects:
        effect_receipt["move_effects"] = move_effects
    if partial_effects:
        # The receipt states that some effects landed and some did not, so a
        # consumer can never read this receipt as a clean failure.
        effect_receipt["partial"] = True
        effect_receipt["partial_effects"] = partial_effects
    response: dict[str, Any] = {
        "ok": all_ok,
        "operations": len(operations),
        "files_ok": sum(1 for result in results if result.get("ok")),
        "results": results,
        "effect_receipt": effect_receipt,
        **({"director_policy_denials": director_policy_denials} if director_policy_denials else {}),
        **({"error_type": "director_write_policy_denied", "blocked": True} if director_policy_denials else {}),
        **({} if all_ok else {"error": _summarize_errors(results)}),
    }
    if partial_effects:
        # The outward projection keeps the real effect evidence and marks the
        # outcome as partial, so no consumer reads it as "nothing happened".
        response["partial"] = True
        response["partial_effects"] = partial_effects
        response["files_changed"] = changed_files
        if not director_policy_denials:
            # A policy denial keeps its own fail-closed error type; otherwise
            # the outward error type names the real partial outcome.
            response["error_type"] = _apply_patch_partial_error_type(results)
    return response


def _apply_patch_partial_error_type(results: list[dict[str, Any]]) -> str:
    """Return the outward error type for a partially applied patch.

    Args:
        results: The routed patch operation results.

    Returns:
        The error type of the first partial operation, or a generic partial
        marker when no operation carried a specific classification.
    """
    for result in results:
        if not result.get("partial"):
            continue
        error_type = str(result.get("error_type") or "").strip()
        if error_type:
            return error_type
    return "partial_apply_patch_effect"


def _apply_patch_move_effects(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Collect the per-effect move evidence of every move in a patch.

    The evidence is reused from the operation results rather than rebuilt, so
    the receipt and the operation result can never disagree.

    Args:
        results: The routed patch operation results.

    Returns:
        The merged move-effect evidence, or an empty mapping when the patch
        contained no move.
    """
    merged: dict[str, Any] = {}
    for result in results:
        effects = result.get("move_effects")
        if not isinstance(effects, dict):
            continue
        for key, value in effects.items():
            merged[f"{result.get('destination_file') or result.get('file')}:{key}"] = value
    return merged


def _apply_patch_observed_changed_files(result: dict[str, Any]) -> list[str]:
    """Return the files a single patch operation really changed.

    A successful operation reports its own file.  A partially applied move
    reports the paths it really changed even though the operation as a whole
    failed, because those writes are real, already-observed effects.  A
    failed operation without an observed effect reports nothing.

    Args:
        result: One routed patch operation result.

    Returns:
        The workspace-relative paths that the operation really changed.
    """
    observed = result.get("files_changed")
    if isinstance(observed, (list, tuple)):
        return [str(item).strip() for item in observed if str(item).strip()]
    if result.get("ok"):
        path = str(result.get("file") or "").strip()
        return [path] if path else []
    return []


def _apply_routed_patch_operation(
    self: AgentAccelToolExecutor,
    *,
    operation: Any,
    tool_kwargs: dict[str, Any],
    resolve_workspace_path: Any,
    to_workspace_relative_path: Any,
    validate_policy: Any,
    emit_file_written: Any,
) -> dict[str, Any]:
    """Apply one parsed apply_patch operation with policy validation."""
    try:
        target = resolve_workspace_path(self._kernel_fs, operation.path)
        rel = to_workspace_relative_path(self._kernel_fs, target)
    except (ValueError, OSError) as exc:
        return {
            "ok": False,
            "file": operation.path,
            "error": f"Invalid path: {operation.path}: {exc}",
        }

    if operation.kind == "create":
        return _apply_patch_create(
            self,
            rel=rel,
            content=str(operation.content or ""),
            tool_kwargs=tool_kwargs,
            validate_policy=validate_policy,
            emit_file_written=emit_file_written,
        )
    if operation.kind == "search_replace":
        return _apply_patch_update(
            self,
            rel=rel,
            search=str(operation.search or ""),
            replace=str(operation.replace or ""),
            move_to=str(operation.move_to or ""),
            tool_kwargs=tool_kwargs,
            resolve_workspace_path=resolve_workspace_path,
            to_workspace_relative_path=to_workspace_relative_path,
            validate_policy=validate_policy,
            emit_file_written=emit_file_written,
        )
    if operation.kind == "delete":
        return _apply_patch_delete(
            self,
            rel=rel,
            tool_kwargs=tool_kwargs,
            validate_policy=validate_policy,
            emit_file_written=emit_file_written,
        )
    return {
        "ok": False,
        "file": rel,
        "error": f"Unsupported apply_patch operation kind: {operation.kind}",
    }


def _apply_patch_create(
    self: AgentAccelToolExecutor,
    *,
    rel: str,
    content: str,
    tool_kwargs: dict[str, Any],
    validate_policy: Any,
    emit_file_written: Any,
) -> dict[str, Any]:
    if self._kernel_fs.workspace_exists(rel):
        return {"ok": False, "file": rel, "error": f"File already exists: {rel}"}

    policy_result = validate_policy(
        self,
        rel=rel,
        old_content="",
        new_content=content,
        operation="apply_patch:create",
        tool_kwargs=tool_kwargs,
    )
    if not policy_result.get("ok"):
        return _apply_patch_policy_denial(rel, policy_result)

    try:
        receipt = self._kernel_fs.workspace_write_text(rel, content, encoding="utf-8")
        emit_file_written(self, file_path=rel, operation="create", old_content="", new_content=content)
    except (OSError, ValueError) as exc:
        return {"ok": False, "file": rel, "error": f"Failed to create file: {exc}"}

    return {
        "ok": True,
        "file": rel,
        "operation": "create",
        "bytes_written": receipt.bytes_written,
        "director_policy": policy_result.get("director_policy"),
    }


def _apply_patch_update(
    self: AgentAccelToolExecutor,
    *,
    rel: str,
    search: str,
    replace: str,
    move_to: str,
    tool_kwargs: dict[str, Any],
    resolve_workspace_path: Any,
    to_workspace_relative_path: Any,
    validate_policy: Any,
    emit_file_written: Any,
) -> dict[str, Any]:
    if not self._kernel_fs.workspace_exists(rel) or not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "file": rel, "error": f"File not found: {rel}"}

    try:
        old_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "file": rel, "error": f"Failed to read file: {exc}"}

    if not search:
        return {"ok": False, "file": rel, "error": "apply_patch update requires non-empty search content"}
    if search not in old_content:
        return {"ok": False, "file": rel, "error": f"Search text not found in file: {search[:50]}..."}

    new_content = old_content.replace(search, replace, 1)
    if not move_to:
        policy_result = validate_policy(
            self,
            rel=rel,
            old_content=old_content,
            new_content=new_content,
            operation="apply_patch:update",
            tool_kwargs=tool_kwargs,
        )
        if not policy_result.get("ok"):
            return _apply_patch_policy_denial(rel, policy_result)
        try:
            receipt = self._kernel_fs.workspace_write_text(rel, new_content, encoding="utf-8")
            emit_file_written(self, file_path=rel, operation="modify", old_content=old_content, new_content=new_content)
        except (OSError, ValueError) as exc:
            return {"ok": False, "file": rel, "error": f"Failed to update file: {exc}"}
        return {
            "ok": True,
            "file": rel,
            "operation": "modify",
            "bytes_written": receipt.bytes_written,
            "director_policy": policy_result.get("director_policy"),
        }

    try:
        move_target = resolve_workspace_path(self._kernel_fs, move_to)
        move_rel = to_workspace_relative_path(self._kernel_fs, move_target)
    except (ValueError, OSError) as exc:
        return {"ok": False, "file": move_to, "error": f"Invalid move target: {move_to}: {exc}"}
    if self._kernel_fs.workspace_exists(move_rel):
        return {"ok": False, "file": move_rel, "error": f"Move target already exists: {move_rel}"}

    delete_policy = validate_policy(
        self,
        rel=rel,
        old_content=old_content,
        new_content="",
        operation="apply_patch:move_delete",
        tool_kwargs=tool_kwargs,
    )
    if not delete_policy.get("ok"):
        return _apply_patch_policy_denial(rel, delete_policy)
    create_policy = validate_policy(
        self,
        rel=move_rel,
        old_content="",
        new_content=new_content,
        operation="apply_patch:move_create",
        tool_kwargs=tool_kwargs,
    )
    if not create_policy.get("ok"):
        return _apply_patch_policy_denial(move_rel, create_policy)

    move_policy = {
        "delete": delete_policy.get("director_policy"),
        "create": create_policy.get("director_policy"),
    }

    # The move is two physical effects: the destination write and the source
    # deletion.  Each is executed and then observed, so a failed call can never
    # erase the observation of an effect that already happened.  A failed call
    # is not a no-effect: the adapter may have completed the effect and failed
    # afterwards, so the physical state is observed instead of assumed.
    # Nothing is rolled back automatically: the destination write is a real,
    # already-authorized effect, and removing it again would be a second
    # effect the caller never authorized.
    try:
        receipt = self._kernel_fs.workspace_write_text(move_rel, new_content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - the write effect may be unknown
        # No receipt means no confirmed byte count.  The destination did not
        # exist before this move, so an observed destination was created here.
        destination_exists = _observe_workspace_presence(self._kernel_fs, move_rel)
        logger.warning("apply_patch move could not write destination %s: %s", move_rel, exc)
        return _apply_patch_move_result(
            rel=rel,
            move_rel=move_rel,
            move_policy=move_policy,
            move_effects={
                "destination_write": "failed" if destination_exists is False else "unknown",
                "source_delete": "not_attempted",
                "source_exists_after_failure": True,
            },
            changed_paths=[move_rel] if destination_exists else [],
            ok=False,
            error=f"Failed to move file: {exc}",
            error_type=(
                "move_destination_write_failed" if destination_exists is False else "move_destination_write_unknown"
            ),
        )

    try:
        self._kernel_fs.workspace_remove(rel, missing_ok=False)
    except Exception as exc:  # noqa: BLE001 - the delete effect may be unknown
        # The destination write already succeeded, so the destination changed.
        # The source deletion failed, which does not prove the deletion had no
        # effect: the adapter may have removed the file and failed afterwards.
        # Presence or absence is an observation, never a fabricated successful
        # deletion receipt.
        source_exists = _observe_workspace_presence(self._kernel_fs, rel)
        logger.warning(
            "apply_patch move wrote destination %s but could not remove source %s: %s",
            move_rel,
            rel,
            exc,
        )
        reporting_failure = _emit_destination_write_event(
            self,
            emit_file_written=emit_file_written,
            move_rel=move_rel,
            new_content=new_content,
        )
        return _apply_patch_move_result(
            rel=rel,
            move_rel=move_rel,
            move_policy=move_policy,
            move_effects={
                "destination_write": "succeeded",
                "source_delete": "failed" if source_exists is True else "unknown",
                "source_exists_after_failure": source_exists,
            },
            changed_paths=[move_rel] + ([rel] if source_exists is False else []),
            ok=False,
            error=f"Failed to move file: {exc}",
            error_type=("partial_move_source_delete_failed" if source_exists is True else "move_source_delete_unknown"),
            bytes_written=receipt.bytes_written,
            reporting_failure=reporting_failure,
        )

    # Reporting runs after the physical effects.  The reporter is a realtime
    # projection, not the authority for the physical effect, so its failure is
    # recorded as evidence and never downgrades the observed effects.
    reporting_failure = _emit_move_write_events(
        self,
        emit_file_written=emit_file_written,
        move_rel=move_rel,
        rel=rel,
        old_content=old_content,
        new_content=new_content,
    )

    return _apply_patch_move_result(
        rel=rel,
        move_rel=move_rel,
        move_policy=move_policy,
        move_effects={
            "destination_write": "succeeded",
            "source_delete": "succeeded",
            "source_exists_after_failure": False,
        },
        # A completed move changed two paths: the destination was written and
        # the source was removed.  Both are confirmed changed paths.
        changed_paths=[move_rel, rel],
        ok=True,
        bytes_written=receipt.bytes_written,
        reporting_failure=reporting_failure,
    )


def _apply_patch_delete(
    self: AgentAccelToolExecutor,
    *,
    rel: str,
    tool_kwargs: dict[str, Any],
    validate_policy: Any,
    emit_file_written: Any,
) -> dict[str, Any]:
    if not self._kernel_fs.workspace_exists(rel) or not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "file": rel, "error": f"File not found: {rel}"}
    try:
        old_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "file": rel, "error": f"Failed to read file: {exc}"}

    policy_result = validate_policy(
        self,
        rel=rel,
        old_content=old_content,
        new_content="",
        operation="apply_patch:delete",
        tool_kwargs=tool_kwargs,
    )
    if not policy_result.get("ok"):
        return _apply_patch_policy_denial(rel, policy_result)

    try:
        self._kernel_fs.workspace_remove(rel, missing_ok=False)
        emit_file_written(self, file_path=rel, operation="delete", old_content=old_content, new_content="")
    except (OSError, ValueError) as exc:
        return {"ok": False, "file": rel, "error": f"Failed to delete file: {exc}"}

    return {
        "ok": True,
        "file": rel,
        "operation": "delete",
        "director_policy": policy_result.get("director_policy"),
    }


def _observe_workspace_presence(kernel_fs: Any, rel: str) -> bool | None:
    """Observe whether *rel* still exists without turning doubt into a claim.

    The source removal already failed, so its final state is not known by
    construction.  A successful observation reports the real presence; an
    observation that itself fails returns ``None`` so the caller reports an
    unknown source state instead of inventing one.

    Args:
        kernel_fs: The KernelOne filesystem boundary.
        rel: Workspace-relative path to observe.

    Returns:
        True/False for a successful observation, or None when the observation
        itself could not be completed.
    """
    try:
        return bool(kernel_fs.workspace_exists(rel))
    except Exception as exc:  # noqa: BLE001 - a failed observation leaves the effect unknown
        logger.warning("apply_patch could not observe source presence for %s: %s", rel, exc)
        return None


def _emit_move_write_events(
    executor: AgentAccelToolExecutor,
    *,
    emit_file_written: Any,
    move_rel: str,
    rel: str,
    old_content: str,
    new_content: str,
) -> dict[str, Any] | None:
    """Publish the two write events of a completed move.

    The physical move already happened, so both events are attempted even
    when the first one fails: the source deletion really did occur and its
    report must not be skipped because the destination report failed.  A
    reporting failure is recorded as evidence instead of being raised, so
    that observed effects are never discarded by a broken observer.

    Args:
        executor: The executor that owns the write event emitter.
        emit_file_written: Emitter used to publish the write events.
        move_rel: Workspace-relative destination path.
        rel: Workspace-relative source path.
        old_content: The original source content.
        new_content: The content written to the destination.

    Returns:
        The reporting failure evidence, or None when both events were emitted.
    """
    failures: list[dict[str, Any]] = []
    destination_failure = _emit_destination_write_event(
        executor,
        emit_file_written=emit_file_written,
        move_rel=move_rel,
        new_content=new_content,
    )
    if destination_failure is not None:
        failures.append(destination_failure)
    try:
        emit_file_written(executor, file_path=rel, operation="delete", old_content=old_content, new_content="")
    except Exception as exc:  # noqa: BLE001 - reporting must not discard observed effects
        logger.warning("apply_patch move delete reporting failed for %s: %s", rel, exc)
        failures.append(
            {
                "stage": "file_written_event",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "files_changed": [rel],
            }
        )
    if not failures:
        return None
    return {
        "stage": "file_written_event",
        "error": "; ".join(str(item.get("error")) for item in failures),
        "error_type": str(failures[0].get("error_type") or ""),
        # The reporting failure is about the observed reports, not about the
        # physical effect, so it lists the paths whose report failed.
        "files_changed": [path for item in failures for path in item.get("files_changed", [])],
        "failures": failures,
    }


def _emit_destination_write_event(
    executor: AgentAccelToolExecutor,
    *,
    emit_file_written: Any,
    move_rel: str,
    new_content: str,
) -> dict[str, Any] | None:
    """Publish the destination-write event of a move.

    The destination write is a real, already-observed effect, so it is
    published even when the rest of the move fails.  A reporting failure is
    recorded as evidence instead of being raised.

    Args:
        executor: The executor that owns the write event emitter.
        emit_file_written: Emitter used to publish the write event.
        move_rel: Workspace-relative destination path.
        new_content: The content written to the destination.

    Returns:
        The reporting failure evidence, or None when the event was emitted.
    """
    try:
        emit_file_written(executor, file_path=move_rel, operation="create", old_content="", new_content=new_content)
    except Exception as exc:  # noqa: BLE001 - reporting must not discard observed effects
        logger.warning("apply_patch move create reporting failed for %s: %s", move_rel, exc)
        return {
            "stage": "file_written_event",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "files_changed": [move_rel],
        }
    return None


def _apply_patch_move_result(
    *,
    rel: str,
    move_rel: str,
    move_policy: dict[str, Any],
    move_effects: dict[str, Any],
    changed_paths: list[str],
    ok: bool,
    error: str | None = None,
    error_type: str | None = None,
    bytes_written: int | None = None,
    reporting_failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one apply_patch move outcome.

    A single projection serves every move outcome so that the effect
    dictionaries are stated once.  ``changed_paths`` carries the paths the
    move really changed; it is empty only when no effect is confirmed.  A
    failed move keeps the confirmed destination write, and ``move_effects``
    states per-effect what is confirmed, what failed and what is unknown, so
    no consumer can read a failed call as a completed deletion.

    Args:
        rel: Workspace-relative source path.
        move_rel: Workspace-relative destination path.
        move_policy: Director policy evidence for both halves of the move.
        move_effects: Per-effect outcome of the destination write and the
            source deletion.
        changed_paths: The paths the move really changed.
        ok: Whether the move completed.
        error: The failure message, when the move did not complete.
        error_type: The failure classification, when the move did not complete.
        bytes_written: Confirmed bytes written, only when a receipt exists.
        reporting_failure: Evidence of a failed realtime report.

    Returns:
        The projected move result.
    """
    result: dict[str, Any] = {
        "ok": ok,
        # The destination is the file this operation changed, so it is the
        # operation's file identity; the source is reported separately.
        "file": move_rel,
        "source_file": rel,
        "destination_file": move_rel,
        "operation": "move",
        "files_changed": list(changed_paths),
        "move_effects": move_effects,
        "director_policy": move_policy,
    }
    if bytes_written is not None:
        result["bytes_written"] = bytes_written
    receipt: dict[str, Any] = {
        "operation": "apply_patch",
        "files_changed": list(changed_paths),
        "director_policy": {move_rel: move_policy},
        "move_effects": move_effects,
    }
    if not ok:
        result["error"] = error
        result["error_type"] = error_type
        # A move that changed a confirmed path is partial, not a no-effect.
        result["partial"] = bool(changed_paths)
        receipt["partial"] = bool(changed_paths)
    result["effect_receipt"] = receipt
    if reporting_failure:
        result["reporting_failure"] = reporting_failure
    return result


def _apply_patch_policy_denial(rel: str, policy_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "file": rel,
        "error": policy_result.get("error", "Director write policy denied"),
        "error_type": "director_write_policy_denied",
        "blocked": True,
        "director_policy": policy_result.get("director_policy"),
    }


def _summarize_errors(results: list[dict[str, Any]]) -> str:
    """Build a summary error message from individual file results."""
    failed = [r for r in results if not r.get("ok")]
    if not failed:
        return "Operation failed"
    if len(failed) == 1:
        return str(failed[0].get("error", "Unknown error"))
    return f"{len(failed)} files failed: " + "; ".join(
        f"{r.get('file', '?')}: {r.get('error', 'Unknown')}" for r in failed[:3]
    )


def _parse_unified_diff(content: str) -> list[dict[str, Any]]:
    """Parse unified diff format into structured hunks.

    Args:
        content: Unified diff string

    Returns:
        List of hunks with file path and changes
    """
    import re

    hunks: list[dict[str, Any]] = []
    lines = content.split("\n")
    current_file: str | None = None
    current_changes: list[dict[str, Any]] = []
    current_search: list[str] = []
    current_replace: list[str] = []
    in_hunk = False

    def _finalize_change() -> None:
        nonlocal current_search, current_replace, current_changes
        if current_search or current_replace:
            current_changes.append(
                {
                    "search": "\n".join(current_search) if current_search else None,
                    "replace": "\n".join(current_replace) if current_replace else None,
                }
            )
            current_search = []
            current_replace = []

    for line in lines:
        # File header
        file_match = re.match(r"^---\s+(?:a/)?(.+?)(?:\t.*)?$", line)
        if file_match:
            if current_file and current_changes:
                _finalize_change()
                hunks.append(
                    {
                        "file": current_file,
                        "changes": current_changes,
                    }
                )
            current_file = file_match.group(1)
            current_changes = []
            current_search = []
            current_replace = []
            in_hunk = False
            continue

        # Hunk header
        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match:
            if in_hunk:
                _finalize_change()
            in_hunk = True
            continue

        if not in_hunk:
            continue

        # Hunk content
        if line.startswith("-"):
            if current_replace:
                # New replacement block after search
                _finalize_change()
                current_search = []
            current_search.append(line[1:])
        elif line.startswith("+"):
            current_replace.append(line[1:])
        elif line.startswith(" "):
            # Context line - flush any pending change
            if current_search or current_replace:
                _finalize_change()
        elif line.startswith("\\"):
            # "\ No newline at end of file" - skip
            continue

    # Finalize last hunk
    if current_file and (current_changes or current_search or current_replace):
        _finalize_change()
        hunks.append(
            {
                "file": current_file,
                "changes": current_changes,
            }
        )

    return hunks
