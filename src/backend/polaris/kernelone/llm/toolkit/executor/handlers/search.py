"""Search tool handlers.

Handles search operations: search_code, grep, ripgrep.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Directories never worth scanning in the pure-Python search fallback.
_FALLBACK_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "node_modules",
        ".polaris",
        "build",
        "dist",
        ".eggs",
    }
)
# Bound the fallback so a huge repo cannot stall a turn (mirrors rg's 10s budget intent).
_FALLBACK_MAX_FILES = 20000
_FALLBACK_MAX_FILE_BYTES = 2_000_000

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

logger = __import__("logging").getLogger(__name__)


# Unified handler for all search tools
def _handle_unified_search(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle search_code / grep / ripgrep tool calls with unified logic.

    Args:
        self: Executor instance
        **kwargs: Tool arguments (query/search/pattern/keyword are aliases)

    Returns:
        Execution result dict
    """
    # NOTE: Do NOT strip trailing whitespace from query - patterns like "^def " have
    # trailing space as semantically significant regex. Only strip leading whitespace.
    query = kwargs.get("query") or kwargs.get("search") or kwargs.get("pattern") or kwargs.get("keyword")
    search_query = str(query or "").lstrip()
    if not search_query:
        return {"ok": False, "error": "Missing query"}

    return _run_rg_search(
        self,
        query=search_query,
        file_patterns=kwargs.get("file_patterns"),
        max_results=kwargs.get("max_results", 50),
        case_sensitive=kwargs.get("case_sensitive", False),
        context_lines=kwargs.get("context_lines", 0),
        path=kwargs.get("path"),
    )


def register_handlers() -> dict[str, Any]:
    """Return a dict of handler names to handler methods.

    NOTE: grep, search_code, ripgrep are now handled by repo_rg handlers
    in repo.py to ensure consistent normalization.
    """
    return {}


# Pattern for ripgrep output with context lines:
#   Normal match: file:line:content
#   Context line: file-line-content  (uses hyphen before line number with -C)
#   Separator:    --
_RG_MATCH_LINE = re.compile(r"^(.+?)[:\-](\d+)[:\-](.*)$")
_RG_CONTEXT_LINE = re.compile(r"^(.+?)-(\d+)-(.*)$")
# Single-file ripgrep output (search path points to one file) omits file name:
#   match line   -> "12:snippet"
#   context line -> "11-snippet"
_RG_SINGLE_FILE_MATCH_LINE = re.compile(r"^(\d+):(.*)$")
_RG_SINGLE_FILE_CONTEXT_LINE = re.compile(r"^(\d+)-(.*)$")
_RG_SEPARATOR = re.compile(r"^--+")


def _fallback_pattern_matches(rel_path: str, file_patterns: list[str] | None) -> bool:
    """Approximate ripgrep ``-g`` glob filtering with stdlib fnmatch.

    A pattern containing ``/`` is matched against the relative path; otherwise it
    is matched against the basename (so ``*.py`` behaves as expected).
    """
    if not file_patterns:
        return True
    base = rel_path.rsplit("/", 1)[-1]
    for raw in file_patterns:
        token = str(raw or "").strip()
        if not token:
            continue
        target = rel_path if "/" in token else base
        if fnmatch.fnmatch(target, token):
            return True
    return False


def _python_search_fallback(
    *,
    workspace: Path,
    query: str,
    file_patterns: list[str] | None,
    safe_max_results: int,
    case_sensitive: bool,
    search_path: str,
) -> dict[str, Any]:
    """Pure-Python recursive regex search used when the ``rg`` binary is absent.

    The product must not silently return zero results (which misleads the agent
    into believing the symbol does not exist). This fallback performs a real
    search over the workspace and returns the same payload shape as the rg path,
    tagged ``backend="python_fallback"``.
    """
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(query, flags)
        except re.error:
            # Treat an invalid regex as a literal substring search.
            matcher = re.compile(re.escape(query), flags)

        root = workspace
        if search_path and search_path != ".":
            candidate = (workspace / search_path).resolve()
            try:
                candidate.relative_to(workspace)
            except ValueError:
                return {
                    "ok": False,
                    "error": f"Invalid search path outside workspace: {search_path}",
                    "tool": "repo_rg",
                    "error_type": "invalid_path",
                    "retryable": False,
                }
            if not candidate.exists():
                return {
                    "ok": False,
                    "error": f"Search path not found: {search_path}",
                    "tool": "repo_rg",
                    "error_type": "invalid_path",
                    "retryable": False,
                }
            root = candidate

        results: list[dict[str, Any]] = []
        files_scanned = 0
        truncated = False
        # ADR-0090: a single-FILE search path must scan that file — os.walk on a
        # file yields nothing, silently returning zero results and misleading
        # the agent into believing the symbol is absent.
        if root.is_file():
            walk_iter: Any = [(str(root.parent), [], [root.name])]
        else:
            walk_iter = os.walk(root)
        for dirpath, dirnames, filenames in walk_iter:
            dirnames[:] = [d for d in dirnames if d not in _FALLBACK_SKIP_DIRS]
            for filename in filenames:
                if files_scanned >= _FALLBACK_MAX_FILES:
                    truncated = True
                    break
                files_scanned += 1
                abs_path = os.path.join(dirpath, filename)
                try:
                    rel_path = os.path.relpath(abs_path, workspace).replace("\\", "/")
                except ValueError:
                    continue
                if not _fallback_pattern_matches(rel_path, file_patterns):
                    continue
                try:
                    if os.path.getsize(abs_path) > _FALLBACK_MAX_FILE_BYTES:
                        continue
                    with open(abs_path, encoding="utf-8", errors="strict") as handle:
                        for line_no, raw_line in enumerate(handle, start=1):
                            if matcher.search(raw_line):
                                snippet = raw_line.rstrip("\n")
                                if snippet.strip():
                                    results.append({"file": rel_path, "line": line_no, "snippet": snippet[:500]})
                                if len(results) >= safe_max_results:
                                    truncated = True
                                    break
                except (OSError, UnicodeDecodeError):
                    # Unreadable or binary file: skip silently.
                    continue
                if len(results) >= safe_max_results:
                    break
            if len(results) >= safe_max_results or files_scanned >= _FALLBACK_MAX_FILES:
                break

        return {
            "ok": True,
            "result": {
                "query": query,
                "total_results": len(results),
                "returned_count": len(results),
                "results": results,
                "backend": "python_fallback",
                "truncated": truncated,
            },
        }
    except (OSError, ValueError) as exc:
        logger.warning("python search fallback failed: %s", exc)
        return {
            "ok": True,
            "result": {
                "query": query,
                "total_results": 0,
                "returned_count": 0,
                "results": [],
                "backend": "python_fallback_error",
                "error": f"fallback search failed: {type(exc).__name__}",
            },
        }


def _run_rg_search(
    self: AgentAccelToolExecutor,
    *,
    query: str,
    file_patterns: list[str] | None,
    max_results: int,
    case_sensitive: bool = False,
    context_lines: int = 0,
    path: str | None = None,
) -> dict[str, Any]:
    """Execute ripgrep search.

    Args:
        self: Executor instance
        query: Search query
        file_patterns: File patterns to filter
        max_results: Maximum number of results
        case_sensitive: Case sensitive search
        context_lines: Context lines around matches
        path: Search path

    Returns:
        Search results dict
    """
    safe_max_results = max(1, min(int(max_results), 100))
    command: list[str] = [
        "rg",
        "--line-number",
        "--no-heading",
        "--max-count",
        str(safe_max_results),
    ]

    # Case sensitivity option
    if not case_sensitive:
        command.append("--ignore-case")

    # Context lines
    if context_lines > 0:
        command.extend(["-C", str(min(context_lines, 5))])

    if isinstance(file_patterns, list):
        for pattern in file_patterns[:12]:
            token = str(pattern or "").strip()
            if token:
                command.extend(["-g", token])

    # Search path
    search_path = "."
    if path:
        try:
            from polaris.kernelone.llm.toolkit.executor.utils import resolve_workspace_path

            resolved = resolve_workspace_path(self._kernel_fs, path)
            if not resolved.exists():
                return {
                    "ok": False,
                    "error": f"Search path not found: {path}",
                    "tool": "repo_rg",
                    "error_type": "invalid_path",
                    "retryable": False,
                }
            rel_path = resolved.relative_to(Path(self.workspace).resolve())
            search_path = str(rel_path).replace("\\", "/") or "."
        except (ValueError, OSError) as exc:
            return {
                "ok": False,
                "error": f"Invalid search path '{path}': {exc}",
                "tool": "repo_rg",
                "error_type": "invalid_path",
                "retryable": False,
            }

    # Use -- to prevent query injection (query starting with - treated as flag)
    command.extend(["--", query, search_path])

    try:
        completed = subprocess.run(
            command,
            cwd=str(Path(self.workspace).resolve()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except FileNotFoundError:
        # ripgrep is not installed on this deployment. Degrade to a real
        # pure-Python search instead of returning an empty (and misleading)
        # success result that makes the agent believe the symbol is absent.
        return _python_search_fallback(
            workspace=Path(self.workspace).resolve(),
            query=query,
            file_patterns=file_patterns,
            safe_max_results=safe_max_results,
            case_sensitive=case_sensitive,
            search_path=search_path,
        )
    except subprocess.TimeoutExpired:
        return {
            "query": query,
            "returned_count": 0,
            "results": [],
            "backend": "rg",
            "error": "Search timed out after 10 seconds. Please narrow your search scope.",
            "truncated": True,
        }

    stderr_text = str(completed.stderr or "").strip()
    lowered_stderr = stderr_text.lower()
    permission_denied = any(
        marker in lowered_stderr for marker in ("permission denied", "access is denied", "os error 5")
    )
    if completed.returncode not in (0, 1) and not permission_denied:
        raise RuntimeError(completed.stderr.strip() or f"rg exited with {completed.returncode}")

    parsed_results: list[dict[str, Any]] = []
    # Determine which line number pattern to use based on context_lines
    # With context (-C), ripgrep uses "file-line-content" for context lines
    use_context_format = context_lines > 0

    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Skip group separators (--)
        if _RG_SEPARATOR.match(line):
            continue

        # Try match line format: file:line:content or file-line-content
        # The match line can have colons in the content, so we use a more precise regex
        # that captures file path, line number, and content separately
        if use_context_format:
            # With context: match lines are "file:line:content", context lines are "file-line-content"
            # First try to match as a normal match (colon separator before line)
            colon_match = re.match(r"^(.+?):(\d+):(.*)$", line)
            if colon_match:
                file_path, line_no_text, snippet = colon_match.groups()
            else:
                # Try hyphen format for context lines
                hyphen_match = re.match(r"^(.+?)-(\d+)-(.*)$", line)
                if hyphen_match:
                    file_path, line_no_text, snippet = hyphen_match.groups()
                else:
                    # Single-file search output omits file path: "12:..." / "11-..."
                    single_colon_match = _RG_SINGLE_FILE_MATCH_LINE.match(line)
                    if single_colon_match:
                        line_no_text, snippet = single_colon_match.groups()
                        file_path = search_path
                    else:
                        single_hyphen_match = _RG_SINGLE_FILE_CONTEXT_LINE.match(line)
                        if single_hyphen_match:
                            line_no_text, snippet = single_hyphen_match.groups()
                            file_path = search_path
                        else:
                            continue
        else:
            # Without context: simple "file:line:content" format
            # Single-file search output can be "12:snippet" when rg receives one file path.
            # Detect it first to avoid mis-parsing snippets that also contain ":".
            single_colon_match = _RG_SINGLE_FILE_MATCH_LINE.match(line)
            if single_colon_match and search_path != ".":
                line_no_text, snippet = single_colon_match.groups()
                file_path = search_path
            else:
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                file_path, line_no_text, snippet = parts

        try:
            line_no = int(line_no_text)
        except ValueError:
            line_no = 0

        # Skip lines with empty snippets - these are blank context lines
        # that provide no useful information to the LLM
        if not snippet.strip():
            continue

        parsed_results.append(
            {
                "file": file_path,
                "line": line_no,
                "snippet": snippet,
            }
        )
        if len(parsed_results) >= safe_max_results:
            break

    payload = {
        "ok": True,
        "result": {
            "query": query,
            "total_results": len(parsed_results),
            "returned_count": len(parsed_results),
            "results": parsed_results,
            "backend": "rg",
            "truncated": len(parsed_results) >= safe_max_results,
        },
    }
    if permission_denied and stderr_text:
        payload["warning"] = stderr_text
    return payload
