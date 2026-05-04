"""Shared backend tool runtime for Polaris role and workflow execution.

This runtime exposes a stable tool surface to role/workflow runtimes. It first
prefers the in-process KernelOne tool executor and only falls back to legacy
CLI-style handlers when those are still available.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from polaris.kernelone.constants import FILE_READ_HARD_LIMIT, FILE_READ_WARN_LINES
from polaris.kernelone.llm.exceptions import BudgetExceededError, ToolExecutionError

logger = logging.getLogger(__name__)

ToolFn = Callable[[list[str], str, int], dict[str, Any]]

_DIRECT_TOOL_NAMES: tuple[str, ...] = (
    "write_file",
    "read_file",
    "execute_command",
    "search_code",
    "glob",
    "list_directory",
    "file_exists",
    "grep",
    "ripgrep",
    "search_replace",
    "edit_file",
    "append_to_file",
)


class ReadBudgetGuard:
    """Layer-1 budget guard for read_file operations.

    Enforces line-count limits before files are read:
    - Hard limit: >2000 lines → rejection with BudgetExceededError
    - Warning zone: >500 lines → warning appended to result
    """

    _SAMPLE_SIZE: int = 4096
    _FALLBACK_BYTES_PER_LINE: int = 50

    def __init__(
        self,
        warn_lines: int = FILE_READ_WARN_LINES,
        hard_limit: int = FILE_READ_HARD_LIMIT,
    ) -> None:
        self._warn_lines = warn_lines
        self._hard_limit = hard_limit

    def _estimate_lines(self, path: str, file_size: int) -> int:
        """Estimate line count via sampling, with conservative fallback.

        Reads up to _SAMPLE_SIZE bytes from the start of the file,
        counts actual newlines, and extrapolates. If sampling fails,
        falls back to file_size // _FALLBACK_BYTES_PER_LINE.
        """
        if file_size <= 0:
            return 0
        try:
            sample_size = min(file_size, self._SAMPLE_SIZE)
            with open(path, "rb") as f:
                sample = f.read(sample_size)
            newline_count = sample.count(b"\n")
            if newline_count == 0:
                # No newlines in sample: either single-line file or binary.
                # Fall back to conservative heuristic.
                return max(1, file_size // self._FALLBACK_BYTES_PER_LINE)
            # Extrapolate: lines in sample / bytes in sample * total bytes
            estimated = int(newline_count / sample_size * file_size)
            return max(1, estimated)
        except OSError:
            return max(1, file_size // self._FALLBACK_BYTES_PER_LINE)

    def check_file_budget(self, file_arg: str, cwd: str) -> dict[str, Any] | None:
        """Check if a file is within read budget.

        Returns:
            None if the file is within budget (proceed normally).
            A dict error response if the file should be rejected/warned.
        """
        try:
            raw_path = str(file_arg or "").strip()
            if not raw_path:
                return None
            if os.path.isabs(raw_path):
                target = os.path.abspath(raw_path)
            else:
                target = os.path.abspath(os.path.join(cwd, raw_path))
            target = os.path.normpath(target)
            if not os.path.isfile(target):
                return None
            file_size = os.path.getsize(target)
            estimated_lines = self._estimate_lines(target, file_size)
            if estimated_lines > self._hard_limit:
                return {
                    "ok": False,
                    "tool": "read_file",
                    "error": (
                        f"BudgetExceededError: read_file hard limit exceeded. "
                        f"File has ~{estimated_lines} lines (limit: {self._hard_limit}). "
                        f"Large full-file reads exhaust context budget."
                    ),
                    "error_code": "BUDGET_EXCEEDED",
                    "file": raw_path,
                    "line_count": estimated_lines,
                    "limit": self._hard_limit,
                    "suggestion": (
                        f"Use repo_read_slice with {{'file': '{raw_path}', 'start': 1, 'end': 200}} "
                        f"for the first section. Use repo_read_around to examine specific locations."
                    ),
                }
            if estimated_lines > self._warn_lines:
                logger.warning(
                    "ReadBudgetGuard: file '%s' has ~%d lines (warn threshold: %d)",
                    raw_path,
                    estimated_lines,
                    self._warn_lines,
                )
            return None
        except (OSError, ValueError) as exc:
            logger.warning(
                "ReadBudgetGuard: failed to check budget for %s: %r",
                file_arg,
                exc,
            )
            return {
                "ok": False,
                "error_code": "BUDGET_CHECK_FAILED",
                "error": f"Failed to check budget for '{file_arg}': {type(exc).__name__}",
                "file": file_arg,
            }

    def raise_if_exceeded(self, check_result: dict[str, Any] | None) -> None:
        """Raise BudgetExceededError if check_result indicates a budget violation."""
        if check_result is None:
            return
        error_code = check_result.get("error_code")
        if error_code == "BUDGET_EXCEEDED":
            raise BudgetExceededError(
                check_result["error"],
                tool="read_file",
                file=check_result.get("file"),
                line_count=check_result.get("line_count", 0),
                limit=check_result.get("limit", 2000),
                suggestion=check_result.get("suggestion"),
            )
        if error_code == "BUDGET_CHECK_FAILED":
            raise BudgetExceededError(
                check_result["error"],
                tool="read_file",
                file=check_result.get("file"),
                line_count=0,
                limit=0,
                suggestion="Cannot verify budget - access denied or invalid path",
            )


class WorkspacePathResolver:
    """Resolves and validates paths within a workspace."""

    def __init__(self, workspace: str) -> None:
        self._workspace = str(workspace or ".")

    def resolve_workspace_path(self, path: str) -> Path:
        """Resolve and validate path inside current workspace."""
        workspace_ref = Path(self._workspace).expanduser()
        workspace_root = workspace_ref if workspace_ref.is_absolute() else (Path.cwd() / workspace_ref)
        workspace_root = Path(os.path.abspath(str(workspace_root)))
        raw_path = Path(str(path or "").strip())
        target = raw_path if raw_path.is_absolute() else (workspace_root / raw_path)
        target = Path(os.path.abspath(str(target)))

        if os.path.islink(target):
            target = Path(os.path.realpath(target))

        workspace_norm = os.path.normcase(os.path.normpath(str(workspace_root)))
        target_norm = os.path.normcase(os.path.normpath(str(target)))
        if target_norm != workspace_norm and not target_norm.startswith(workspace_norm + os.sep):
            raise ValueError(f"Path '{path}' is outside workspace")
        return target

    def resolve_tool_cwd(self, cwd: Any) -> str:
        """Resolve and validate tool working directory."""
        target = self.resolve_workspace_path(str(cwd or "."))
        if not target.exists() or not target.is_dir():
            raise ValueError(f"Working directory not found: {cwd}")
        return str(target)


class ToolArgumentNormalizer:
    """Normalizes tool arguments and resolves paths."""

    @staticmethod
    def normalize_timeout(value: Any, default: int = 30) -> int:
        """Normalize timeout value to integer seconds."""
        try:
            timeout = int(value)
        except (ValueError, TypeError):
            timeout = default
        if timeout <= 0:
            timeout = default
        return min(timeout, 600)

    @staticmethod
    def as_string_list(value: Any) -> list[str]:
        """Convert value to list of strings."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            out: list[str] = []
            for item in value:
                text = str(item).strip()
                if text:
                    out.append(text)
            return out
        text = str(value).strip()
        if not text:
            return []
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]

    def normalize_repo_tool_arguments(
        self,
        tool_name: str,
        args: dict[str, Any],
        cwd: str,
    ) -> dict[str, Any]:
        """Normalize repo tool arguments to workspace-relative paths."""
        tool = str(tool_name or "").strip().lower()
        if not (tool.startswith("repo_") or tool.startswith("treesitter_")):
            return args

        normalized = dict(args)
        keys = {"path", "paths", "file", "file_path", "root", "text_file", "text-file", "textFile"}
        for key in keys:
            if key not in normalized:
                continue
            value = normalized.get(key)
            if isinstance(value, (list, tuple, set)):
                normalized[key] = [self.workspace_to_repo_relative(str(item), cwd) for item in value]
                continue
            if value is None:
                continue
            normalized[key] = self.workspace_to_repo_relative(str(value), cwd)
        return normalized

    @staticmethod
    def workspace_to_repo_relative(path_value: str, cwd: str) -> str:
        """Convert workspace path to repo-relative path."""
        text = str(path_value or "").strip()
        if not text:
            return text
        if os.path.isabs(text):
            return text
        repo_root = ToolArgumentNormalizer.find_repo_root_path(cwd)
        absolute_path = os.path.abspath(os.path.join(cwd, text))
        try:
            rel = os.path.relpath(absolute_path, repo_root)
        except (RuntimeError, ValueError):
            return text
        if rel.startswith(".."):
            return text
        return rel.replace("\\", "/")

    @staticmethod
    def find_repo_root_path(start: str) -> str:
        """Find git repository root from starting path."""
        current = os.path.abspath(str(start or "."))
        while True:
            if os.path.isdir(os.path.join(current, ".git")) or os.path.isfile(os.path.join(current, ".git")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                return current
            current = parent


class ToolCliBuilder:
    """Builds CLI arguments for legacy tool handlers."""

    @staticmethod
    def build_backend_tool_args(tool_name: str, args: dict[str, Any]) -> list[str]:
        """Build CLI argument list for a tool."""
        if not args:
            return []
        raw_args = args.get("args")
        if isinstance(raw_args, list):
            return [str(x) for x in raw_args]
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                return [str(token) for token in shlex.split(raw_args)]
            except (RuntimeError, ValueError):
                return [token for token in raw_args.split(" ") if token]
        raw_argv = args.get("argv")
        if isinstance(raw_argv, list):
            return [str(x) for x in raw_argv]

        normalizer = ToolArgumentNormalizer()

        def first(*names: str) -> Any:
            for name in names:
                if name in args and args[name] is not None and str(args[name]).strip() != "":
                    return args[name]
            return None

        tool = str(tool_name or "").strip().lower()

        if tool in {
            "repo_tree",
            "repo_map",
            "repo_rg",
            "repo_read_around",
            "repo_read_slice",
            "repo_read_head",
            "repo_read_tail",
            "repo_diff",
        }:
            try:
                from polaris.kernelone.tool_execution import build_tool_cli_args

                built = build_tool_cli_args(tool, args)
                if built:
                    return [str(token) for token in built]
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to build tool CLI args: {e}")

        if tool == "repo_symbols_index":
            tokens = normalizer.as_string_list(first("paths", "path"))
            if not tokens:
                tokens = ["."]
            max_results = first("max_results", "max")
            if max_results is not None:
                tokens += ["--max", str(max_results)]
            glob_pat = first("glob")
            if glob_pat:
                tokens += ["--glob", str(glob_pat)]
            return tokens

        if tool == "treesitter_outline":
            language = first("language", "lang")
            file_path = first("file", "path")
            if language and file_path:
                return [str(language), str(file_path)]

        if tool == "treesitter_find_symbol":
            language = first("language", "lang")
            file_path = first("file", "path")
            symbol = first("symbol", "name")
            if language and file_path and symbol:
                tokens = [str(language), str(file_path), str(symbol)]
                kind = first("kind")
                if kind:
                    tokens += ["--kind", str(kind)]
                max_results = first("max", "max_results")
                if max_results is not None:
                    tokens += ["--max", str(max_results)]
                return tokens

        if tool == "treesitter_replace_node":
            language = first("language", "lang")
            file_path = first("file", "path")
            symbol = first("symbol", "name")
            if language and file_path and symbol:
                tokens = [str(language), str(file_path), str(symbol)]
                kind = first("kind")
                if kind:
                    tokens += ["--kind", str(kind)]
                index = first("index")
                if index is not None:
                    tokens += ["--index", str(index)]
                text_value = first("text", "code")
                if text_value is not None:
                    tokens += ["--text", str(text_value)]
                text_file = first("text_file", "text-file", "textFile")
                if text_file:
                    tokens += ["--text-file", str(text_file)]
                return tokens

        if tool == "treesitter_insert_method":
            language = first("language", "lang")
            file_path = first("file", "path")
            class_name = first("class_name", "class", "className")
            method_text = first("method_text", "method", "text")
            if language and file_path and class_name and method_text is not None:
                tokens = [str(language), str(file_path), str(class_name), str(method_text)]
                text_file = first("text_file", "text-file", "textFile")
                if text_file:
                    tokens += ["--text-file", str(text_file)]
                return tokens

        if tool == "treesitter_rename_symbol":
            language = first("language", "lang")
            file_path = first("file", "path")
            symbol = first("symbol", "name")
            new_name = first("new_name", "new", "to")
            if language and file_path and symbol and new_name:
                tokens = [str(language), str(file_path), str(symbol), str(new_name)]
                kind = first("kind")
                if kind:
                    tokens += ["--kind", str(kind)]
                return tokens

        return ToolCliBuilder._build_generic_named_args(args)

    @staticmethod
    def _build_generic_named_args(args: dict[str, Any]) -> list[str]:
        """Build generic named arguments as CLI tokens."""
        normalizer = ToolArgumentNormalizer()
        tokens: list[str] = []
        for key, value in args.items():
            if key in {"args", "argv", "cwd", "timeout"}:
                continue
            if value is None:
                continue
            if key == "paths":
                tokens.extend(normalizer.as_string_list(value))
                continue
            if key == "path" and isinstance(value, (list, tuple, set)):
                tokens.extend(normalizer.as_string_list(value))
                continue

            flag = f"--{str(key).replace('_', '-')}"
            if isinstance(value, bool):
                if value:
                    tokens.append(flag)
                continue
            if isinstance(value, (list, tuple, set)):
                joined = ",".join(normalizer.as_string_list(value))
                if joined:
                    tokens += [flag, joined]
                continue
            if isinstance(value, dict):
                tokens += [flag, json.dumps(value, ensure_ascii=False)]
                continue
            tokens += [flag, str(value)]
        return tokens


class BackendToolRuntime:
    """Load and execute backend tools for role runtimes.

    This class orchestrates tool discovery, argument normalization,
    path resolution, and execution through composed helper classes.
    """

    _EXECUTOR_CACHE_MAX: int = 4

    def __init__(self, workspace: str) -> None:
        self.workspace = str(workspace or ".")
        self._handlers: dict[str, ToolFn] | None = None
        self._executor_cache: dict[str, Any] = {}

        self._path_resolver = WorkspacePathResolver(self.workspace)
        self._argument_normalizer = ToolArgumentNormalizer()
        self._cli_builder = ToolCliBuilder()
        self._budget_guard = ReadBudgetGuard()

    def list_tools(self) -> dict[str, ToolFn]:
        """Return all available backend tool handlers."""
        if self._handlers is None:
            handlers = self._load_backend_tool_handlers()
            handlers.update(self._build_direct_tool_placeholders())
            self._handlers = handlers
        return dict(self._handlers)

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        cwd: Any = ".",
        timeout: Any = 30,
    ) -> dict[str, Any]:
        """Invoke one backend tool with unified argument mapping.

        Returns:
            dict with tool execution result.

        Raises:
            ToolExecutionError: If tool execution fails.
        """
        tool = str(tool_name or "").strip().lower()
        if not tool:
            raise ToolExecutionError(
                "missing tool name",
                tool_name=str(tool_name),
                retryable=False,
            )

        handlers = self.list_tools()
        handler = handlers.get(tool)
        if not callable(handler):
            raise ToolExecutionError(
                f"unknown tool: {tool}",
                tool_name=tool,
                retryable=False,
            )

        payload = dict(arguments or {})
        cwd_value = payload.pop("cwd", cwd)
        timeout_value = payload.pop("timeout", timeout)
        try:
            resolved_cwd = self._path_resolver.resolve_tool_cwd(cwd_value)
        except ValueError as exc:
            raise ToolExecutionError(
                f"failed to resolve cwd: {exc}",
                tool_name=tool,
                retryable=False,
            ) from exc
        except RuntimeError as exc:
            raise ToolExecutionError(
                f"unexpected cwd resolution error: {exc}",
                tool_name=tool,
                cause=exc,
                retryable=False,
            ) from exc

        timeout_sec = self._argument_normalizer.normalize_timeout(timeout_value)
        normalized = self._argument_normalizer.normalize_repo_tool_arguments(tool, payload, resolved_cwd)
        if tool in _DIRECT_TOOL_NAMES:
            return self._invoke_with_direct_executor(
                tool_name=tool,
                arguments=normalized,
                cwd=resolved_cwd,
                timeout_sec=timeout_sec,
            )

        cli_args = self._cli_builder.build_backend_tool_args(tool, normalized)
        try:
            result = handler(cli_args, resolved_cwd, timeout_sec)
        except (RuntimeError, ValueError) as exc:
            raise ToolExecutionError(
                f"tool handler failed: {exc}",
                tool_name=tool,
                cause=exc,
            ) from exc

        if not isinstance(result, dict):
            raise ToolExecutionError(
                "invalid tool result type",
                tool_name=tool,
                retryable=False,
            )
        result.setdefault("tool", tool)
        return result

    def _load_backend_tool_handlers(self) -> dict[str, ToolFn]:
        """Load legacy backend tool handlers when the old module still exists."""
        try:
            module = importlib.import_module("tools.main")
            raw_handlers = getattr(module, "TOOL_HANDLERS", None)

            if isinstance(raw_handlers, dict):
                loaded: dict[str, ToolFn] = {}
                for name, handler in raw_handlers.items():
                    key = str(name or "").strip().lower()
                    if key and callable(handler):
                        loaded[key] = handler
                return loaded
        except (ImportError, RuntimeError, ValueError) as e:
            logger.debug(f"Failed to load tool handlers: {e}")

        return {}

    def _build_direct_tool_placeholders(self) -> dict[str, ToolFn]:
        """Expose KernelOne-native tools through the legacy registration surface."""
        return {tool_name: self._make_direct_tool_placeholder(tool_name) for tool_name in _DIRECT_TOOL_NAMES}

    @staticmethod
    def _make_direct_tool_placeholder(tool_name: str) -> ToolFn:
        def _handler(_args: list[str], _cwd: str, _timeout: int) -> dict[str, Any]:
            return {
                "ok": False,
                "tool": tool_name,
                "error": "direct executor placeholder should not be called",
            }

        return _handler

    def _get_executor(self, cwd: str) -> Any:
        """Get or create a cached AgentAccelToolExecutor for the given cwd."""
        from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor

        if cwd in self._executor_cache:
            return self._executor_cache[cwd]

        # Evict oldest entry if at capacity
        if len(self._executor_cache) >= self._EXECUTOR_CACHE_MAX:
            oldest_cwd = next(iter(self._executor_cache))
            oldest_executor = self._executor_cache.pop(oldest_cwd)
            close_sync = getattr(oldest_executor, "close_sync", None)
            if callable(close_sync):
                with contextlib.suppress(Exception):
                    close_sync()

        executor = AgentAccelToolExecutor(workspace=cwd)
        self._executor_cache[cwd] = executor
        return executor

    def close(self) -> None:
        """Close all cached executors and release resources."""
        for executor in list(self._executor_cache.values()):
            close_sync = getattr(executor, "close_sync", None)
            if callable(close_sync):
                with contextlib.suppress(Exception):
                    close_sync()
        self._executor_cache.clear()

    def _invoke_with_direct_executor(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        cwd: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        """Execute a KernelOne-native tool directly without the deleted legacy module.

        Layer-1 budget guard: intercepts read_file before it reaches
        AgentAccelToolExecutor (Layer 2) to enforce line-count limits.

        Returns:
            dict with tool execution result.

        Raises:
            ToolExecutionError: If tool execution fails.
        """
        tool_arguments = dict(arguments)
        if tool_name == "execute_command":
            tool_arguments.setdefault("timeout", timeout_sec)

        if tool_name == "read_file":
            file_arg = arguments.get("file") if isinstance(arguments, dict) else None
            if not file_arg:
                file_arg = arguments.get("path") if isinstance(arguments, dict) else None
            if file_arg:
                check_result = self._budget_guard.check_file_budget(file_arg, cwd)
                if check_result is not None:
                    self._budget_guard.raise_if_exceeded(check_result)

        executor = self._get_executor(cwd)
        try:
            result = executor.execute(tool_name, tool_arguments)
        except BudgetExceededError:
            raise
        except ToolExecutionError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise ToolExecutionError(
                f"tool execution failed: {exc}",
                tool_name=tool_name,
                cause=exc,
            ) from exc

        if not isinstance(result, dict):
            raise ToolExecutionError(
                "invalid tool result type",
                tool_name=tool_name,
                retryable=False,
            )
        result.setdefault("tool", tool_name)
        return result
