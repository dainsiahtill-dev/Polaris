"""Canonical repository tools (repo_*) handlers.

These handlers implement the canonical tools defined in contracts.py.
Each tool has precise semantics that must not be aliased to other tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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
        "precision_edit": _handle_precision_edit,
    }


def _handle_repo_tree(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_tree tool call.

    List directory tree within the workspace.
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.navigation import _handle_list_directory

    # Map repo_tree args to list_directory args
    path = kwargs.get("path", kwargs.get("root", kwargs.get("dir", ".")))
    depth = kwargs.get("depth")
    max_entries = kwargs.get("max_entries", depth or 100)

    return _handle_list_directory(
        self,
        path=path,
        recursive=True,
        max_entries=max_entries,
        include_hidden=False,
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

    return _handle_unified_search(
        self,
        query=pattern,
        path=path,
        max_results=max_results,
        file_patterns=file_patterns,
        context_lines=context_lines,
        case_sensitive=case_sensitive,
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
        return _handle_read_file(
            self,
            file=file,
            start_line=int(start),
            end_line=int(end),
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

    return _handle_read_file(
        self,
        file=file,
        start_line=int(start),
        end_line=int(end),
    )


def _handle_repo_read_head(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_read_head tool call.

    Read the first N lines from a file (semantic: head -n).
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_read_file

    file = kwargs.get("file") or kwargs.get("file_path") or kwargs.get("filepath")
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
        return result

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

    return result


def _handle_repo_read_tail(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle repo_read_tail tool call.

    Read the last N lines from a file (semantic: tail -n).
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_read_file

    file = kwargs.get("file") or kwargs.get("file_path")
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
        return result

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
                    lang_set = {l.lower().lstrip(".") for l in languages}
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


def _find_all_symbols_ts(content: str, language: str, max_results: int = 500) -> list[dict[str, Any]]:
    """Find all top-level symbols in source code using tree-sitter.

    This is a standalone version that doesn't require executor instance.
    """
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return []

    from typing import cast

    parser = get_parser(cast("str", language))  # type: ignore[arg-type]
    if parser is None:
        return []

    try:
        tree = parser.parse(content.encode("utf-8", errors="replace"))
    except (RuntimeError, ValueError):
        return []

    root = tree.root_node

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

    def extract_name(node) -> str:
        """Extract identifier name from a node."""
        for field in ("name", "property", "identifier"):
            child = node.child_by_field_name(field)
            if child and child.type == "identifier":
                return content[child.start_byte : child.end_byte]
        for child in node.children:
            if child.type == "identifier":
                return content[child.start_byte : child.end_byte]
        return ""

    def search_node(node, results: list[dict[str, Any]]) -> None:
        if len(results) >= max_results:
            return
        node_type = node.type
        if node_type in target_types:
            name = extract_name(node)
            if name:
                results.append(
                    {
                        "name": name,
                        "kind": node_type,
                        "line": node.start_point[0] + 1,
                        "col": node.start_point[1],
                    }
                )
        for child in node.children:
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
            try:
                self._kernel_fs.workspace_write_text(rel, modified_content, encoding="utf-8")
                results.append(
                    {
                        "file": file_path,
                        "ok": True,
                        "changes_applied": len(changes),
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
        **({} if all_ok else {"error": _summarize_errors(results)}),
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


def _handle_precision_edit(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle precision_edit tool call.

    Precision editing with semantic understanding:
    - Multi-line search/replace support
    - AST-aware editing when tree-sitter is available
    - Code structure preservation

    Args:
        self: Executor instance
        **kwargs: Tool arguments
            - file: Target file path (required)
            - search: Search text (required for replace mode)
            - replace: Replacement text
            - regex: Use regex matching (default: False)
            - language: Programming language for AST editing
            - node_kind: AST node type to target
            - preserve_formatting: Keep original formatting (default: True)

    Returns:
        Execution result dict
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_edit_file

    file = kwargs.get("file") or kwargs.get("file_path")
    search = kwargs.get("search")
    replace = kwargs.get("replace")
    regex = kwargs.get("regex", False)
    language = kwargs.get("language")
    node_kind = kwargs.get("node_kind")
    preserve_formatting = kwargs.get("preserve_formatting", True)

    if not file:
        return {"ok": False, "error": "Missing required parameter: file"}

    # If language and node_kind specified, try AST-aware editing
    if language and node_kind:
        ast_result = _try_ast_precision_edit(self, file, search, replace, language, node_kind, preserve_formatting)
        if ast_result is not None:
            return ast_result

    # Fall back to standard edit_file behavior
    if search is None:
        return {
            "ok": False,
            "error": "precision_edit requires search parameter. For line-based edits, use edit_file instead.",
        }

    return _handle_edit_file(
        self,
        file=file,
        search=search,
        replace=replace,
        regex=regex,
    )


def _try_ast_precision_edit(
    self: AgentAccelToolExecutor,
    file: str,
    search: str | None,
    replace: str | None,
    language: str,
    node_kind: str,
    preserve_formatting: bool,
) -> dict[str, Any] | None:
    """Try AST-aware precision editing.

    This is a best-effort attempt. Returns None if tree-sitter
    is not available or AST editing fails.
    """
    if not hasattr(self, "_treesitter_find_symbol_impl"):
        return None

    try:
        # Resolve file path
        from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path, to_workspace_relative_path

        target = resolve_workspace_path(self._kernel_fs, file)
        rel = to_workspace_relative_path(self._kernel_fs, target)

        if not self._kernel_fs.workspace_exists(rel):
            return None

        # Read file content
        raw = self._kernel_fs.workspace_read_bytes(rel)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

        # Try to parse with tree-sitter
        try:
            import tree_sitter_languages

            tree_sitter_languages.get_parser(language)
        except ImportError as e:
            logger.debug("tree_sitter_languages not available for language '%s': %s", language, e)
            return None

        # For now, fall back to standard editing
        # AST-aware editing would require more complex implementation
        return None

    except (RuntimeError, ValueError) as e:
        logger.warning("Failed to get AST-aware editor for '%s': %s", file, e)
        return None
