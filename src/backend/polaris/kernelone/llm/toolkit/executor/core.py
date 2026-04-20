"""Core executor module.

This module contains the main AgentAccelToolExecutor class.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import ItemsView, KeysView, ValuesView
from pathlib import Path
from typing import Any

from polaris.kernelone.constants import (
    FILE_MAX_CONTEXT_LINES,
    FILE_READ_HARD_LIMIT,
    FILE_READ_SEQUENCE_WINDOW,
    FILE_READ_WARN_LINES,
)
from polaris.kernelone.llm.exceptions import BudgetExceededError

logger = logging.getLogger(__name__)

CODE_INTELLIGENCE_AVAILABLE = False


# Tools that read file content — tracked for mandatory read-before-edit enforcement.
# When an edit tool (edit_file, search_replace) is called, the target file must
# have been read via one of these tools within the recent read sequence window.
READ_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "repo_read_around",
        "repo_read_slice",
        "repo_read_head",
        "repo_read_tail",
    }
)

# Edit tools that require a recent file read before execution.
EDIT_TOOLS: frozenset[str] = frozenset({"edit_file", "search_replace"})

_HANDLER_MODULES_MAX_SIZE: int = 256


class _LRUHandlerCache:
    """LRU cache for handler modules with bounded size."""

    def __init__(self, max_size: int = _HANDLER_MODULES_MAX_SIZE) -> None:
        self._max_size = max_size
        self._cache: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = value

    def setdefault(self, key: str, default: Any) -> Any:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        self._cache[key] = default
        return default

    def items(self) -> ItemsView[str, Any]:
        return self._cache.items()

    def keys(self) -> KeysView[str]:
        return self._cache.keys()

    def values(self) -> ValuesView[Any]:
        return self._cache.values()

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    def __len__(self) -> int:
        return len(self._cache)


class AgentAccelToolExecutor:
    """Standard tool executor.

    Executes LLM tool calls for code intelligence tasks.
    """

    def __init__(
        self,
        workspace: str,
        message_bus: Any | None = None,
        worker_id: str = "llm_toolkit",
        budget_state: Any | None = None,
        session_id: str | None = None,
        session_memory_provider: Any | None = None,
        failure_budget: Any | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        self.workspace = str(Path(workspace).resolve())
        self._closed = False
        self._message_bus = message_bus
        self._worker_id = str(worker_id or "llm_toolkit").strip() or "llm_toolkit"
        self._budget_state = budget_state
        self._session_id = str(session_id or "").strip() or None
        self._session_memory_provider = session_memory_provider
        # Allow external FailureBudget for state persistence across tool calls (HALLUCINATION_LOOP detection)
        self._external_failure_budget = failure_budget
        # Runtime-enforced tool whitelist: when set, only these canonical tool names are executable.
        # This is the executor-level hard gate — role policies (RoleToolGateway / PolicyLayer)
        # run upstream as well, but this prevents any bypass path.
        self._allowed_tools = allowed_tools

        # Line-cost budget limits for read_file downgrade
        self._READ_WARN_LINES = FILE_READ_WARN_LINES
        self._READ_HARD_LIMIT = FILE_READ_HARD_LIMIT
        self._MAX_CONTEXT_LINES = FILE_MAX_CONTEXT_LINES

        from polaris.kernelone.fs import KernelFileSystem
        from polaris.kernelone.fs.registry import get_default_adapter
        from polaris.kernelone.process.command_executor import CommandExecutionService
        from polaris.kernelone.tool_execution.error_classifier import ToolErrorClassifier
        from polaris.kernelone.tool_execution.failure_budget import FailureBudget

        self._kernel_fs = KernelFileSystem(self.workspace, get_default_adapter())
        self._command_executor = CommandExecutionService(self.workspace)

        from polaris.kernelone.llm.toolkit.executor.handlers.treesitter import (
            TreeSitterSymbolHandler,
        )

        self._treesitter_handler = TreeSitterSymbolHandler(self._kernel_fs)

        # Error pattern classification and failure tracking
        self._error_classifier = ToolErrorClassifier()
        # Use external FailureBudget if provided (for state persistence across tool calls),
        # otherwise create a new instance
        self._failure_budget = failure_budget if failure_budget is not None else FailureBudget()

        # File read history for mandatory read-before-edit enforcement
        # Tracks the read sequence number when each file was last read successfully.
        # Edit tools (precision_edit, search_replace, edit_file) require the target
        # file to have been read within a recent window to prevent stale content edits.
        # P0 FIX: Use FailureBudget formal API for cross-call persistence.
        self._read_sequence_window: int = FILE_READ_SEQUENCE_WINDOW  # File must be read within last N reads
        if self._failure_budget is not None:
            self._file_read_history = self._failure_budget.get_file_read_history()
            self._read_sequence = self._failure_budget.get_file_read_sequence()
        else:
            self._file_read_history = {}
            self._read_sequence = 0

        # Import handlers lazily to avoid circular imports
        self._handler_modules = _LRUHandlerCache(max_size=_HANDLER_MODULES_MAX_SIZE)

    def _load_handler_modules(self) -> None:
        """Load all handlers from ToolHandlerRegistry.

        Phase 5: Uses ToolHandlerRegistry.load_all() for explicit handler
        registration. The registry imports all handler modules at once.
        """
        if len(self._handler_modules) == 0:
            from polaris.kernelone.llm.toolkit.executor.handlers.registry import (
                ToolHandlerRegistry,
            )

            for tool_name, handler in ToolHandlerRegistry.load_all().items():
                self._handler_modules.set(tool_name, handler)

            self._handler_modules.set("treesitter_find_symbol", self._handle_treesitter_find_symbol)

    def _record_file_read(self, file_path: str) -> None:
        """Record a successful file read for mandatory read-before-edit enforcement.

        Args:
            file_path: Workspace-relative file path that was successfully read.
        """
        self._read_sequence += 1
        self._file_read_history[file_path] = self._read_sequence
        if self._failure_budget is not None:
            self._failure_budget.set_file_read_sequence(self._read_sequence)

    def _is_file_stale_for_edit(self, file_path: str) -> bool:
        """Check if a file is considered stale (not recently read) for editing.

        An edit tool must not run on a file that hasn't been read within the
        current read sequence window. This prevents the LLM from editing content
        it hasn't verified, which is the primary cause of 'return0' class errors.

        Args:
            file_path: Workspace-relative file path to check.

        Returns:
            True if the file has NOT been read recently (edit should be blocked).
        """
        last_read_seq = self._file_read_history.get(file_path)
        if last_read_seq is None:
            return True  # Never read → stale
        return (self._read_sequence - last_read_seq) > self._read_sequence_window

    async def __aenter__(self) -> AgentAccelToolExecutor:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def __enter__(self) -> AgentAccelToolExecutor:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close_sync()

    def _validate_arguments(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        """Validate tool parameters using canonical contracts validation.

        Args:
            tool_name: Canonical tool name
            arguments: Arguments to validate

        Returns:
            Validation error message, or None if valid
        """
        from polaris.kernelone.tool_execution.contracts import validate_tool_step

        is_valid, _error_code, error_msg = validate_tool_step(tool_name, arguments)
        if is_valid:
            return None
        return error_msg

    @staticmethod
    def _drop_unknown_arguments(
        spec: dict[str, Any],
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Drop arguments not defined in tool spec."""
        allowed = {
            str(arg.get("name", "") or "").strip()
            for arg in spec.get("arguments", [])
            if str(arg.get("name", "") or "").strip()
        }
        if not allowed:
            return dict(arguments), []
        filtered: dict[str, Any] = {}
        dropped: list[str] = []
        for key, value in arguments.items():
            if key in allowed:
                filtered[key] = value
            else:
                dropped.append(str(key))
        return filtered, dropped

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call.

        Args:
            tool_name: Tool name
            arguments: Tool arguments

        Returns:
            Execution result
        """
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments
        from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        # Resolve aliases to canonical name using ToolSpecRegistry
        canonical_tool_name = canonicalize_tool_name(tool_name, keep_unknown=True)
        normalized_arguments = normalize_tool_arguments(
            canonical_tool_name,
            arguments if isinstance(arguments, dict) else {},
        )

        # Runtime-enforced tool whitelist check (executor-level hard gate).
        # This prevents role-policy bypass: even if an upstream gate is skipped,
        # the executor itself rejects disallowed tools.
        if self._allowed_tools is not None and canonical_tool_name not in self._allowed_tools:
            return {"ok": False, "error": f"Tool '{canonical_tool_name}' not allowed for this role"}

        # Get tool spec — canonical source is ToolSpecRegistry (no ToolDefinition intermediate)
        spec = ToolSpecRegistry.get_all_specs().get(canonical_tool_name)
        if spec is None:
            return {"ok": False, "error": f"Unknown tool: {canonical_tool_name}"}

        # Arguments must be a dict
        if not isinstance(arguments, dict):
            return {
                "ok": False,
                "error": "Parameter validation failed: arguments must be an object",
            }

        normalized_arguments, dropped_arguments = self._drop_unknown_arguments(
            spec,
            normalized_arguments,
        )
        if dropped_arguments:
            logger.debug(
                "Dropped unsupported tool arguments for %s: %s",
                canonical_tool_name,
                ", ".join(dropped_arguments),
            )

        # Argument validation
        validation_error = self._validate_arguments(canonical_tool_name, normalized_arguments)
        if validation_error:
            return {
                "ok": False,
                "error": f"Parameter validation failed: {validation_error}",
            }

        # Load handler modules if not already loaded
        self._load_handler_modules()

        # Get handler
        handler = self._handler_modules.get(canonical_tool_name)
        if handler is None:
            return {"ok": False, "error": f"Handler not implemented: {canonical_tool_name}"}

        # Mandatory read-before-edit enforcement: intercept edit tools if the target
        # file has not been freshly read. This transforms the task from "generate"
        # to "select" — the LLM must read the file before editing.
        # Precision errors like 'return0' (generated vs. recalled) are prevented here.
        if canonical_tool_name in EDIT_TOOLS:
            edit_file_arg = normalized_arguments.get("file", arguments.get("file"))
            if edit_file_arg:
                from polaris.kernelone.llm.toolkit.executor.utils import (
                    resolve_workspace_path,
                    to_workspace_relative_path,
                )

                try:
                    edit_target = resolve_workspace_path(self._kernel_fs, str(edit_file_arg))
                    edit_rel = to_workspace_relative_path(self._kernel_fs, edit_target)
                    if self._is_file_stale_for_edit(edit_rel):
                        stale_error = (
                            f"Action Denied: You are attempting to edit '{edit_rel}' "
                            "without a fresh read. "
                            "This is the primary cause of syntax errors like 'return0' (missing space). "
                            "MANDATORY: You MUST call read_file(file='{rel}') first to sync the exact characters "
                            "(spaces, indents, newlines), then retry this edit with the verified content."
                        ).format(rel=edit_rel)
                        logger.warning(
                            "[ToolExecutor] BLOCKED edit '%s' — file not recently read (stale). "
                            "Last read seq=%s, current seq=%s",
                            edit_rel,
                            self._file_read_history.get(edit_rel, 0),
                            self._read_sequence,
                        )
                        return {
                            "ok": False,
                            "error": stale_error,
                            "tool": canonical_tool_name,
                            "error_type": "stale_edit",
                            "retryable": True,
                            "blocked": False,
                            "loop_break": False,
                        }
                except (ValueError, OSError):
                    pass  # Let the handler deal with invalid paths

        try:
            result = handler(self, **normalized_arguments)
            if isinstance(result, dict) and isinstance(result.get("ok"), bool):
                ok = bool(result.get("ok"))
                if ok:
                    # Record successful read for mandatory read-before-edit tracking.
                    # All read tools (read_file, repo_read_*, etc.) return a "file" field.
                    if canonical_tool_name in READ_TOOLS and result.get("file"):
                        self._record_file_read(str(result["file"]).replace("\\", "/").lstrip("/"))
                    payload = dict(result)
                    payload.pop("ok", None)
                    # Avoid double-wrapping when handler already returned {"result": ...}
                    if list(payload.keys()) == ["result"]:
                        return {"ok": True, "result": payload["result"]}
                    return {"ok": True, "result": payload}
                error_message = str(result.get("error") or "").strip() or "Tool returned unsuccessful result"
                suggestion = result.get("suggestion")

                # Classify error and check failure budget for soft errors
                error_pattern = self._error_classifier.classify(canonical_tool_name, error_message)
                # Pass search fingerprint for sequence-break detection on no_match errors
                search_fp: str | None = None
                if error_pattern.error_type == "no_match":
                    search_val = normalized_arguments.get("search", arguments.get("search"))
                    if search_val:
                        search_fp = str(search_val)[:200]
                failure_result = self._failure_budget.record_failure(error_pattern, search_fingerprint=search_fp)

                # Use budget suggestion if no tool-provided suggestion
                if not suggestion and failure_result.suggestion:
                    suggestion = failure_result.suggestion

                if failure_result.decision == "BLOCK":
                    logger.warning(
                        "[ToolExecutor] BLOCKING %s after %d failures (pattern: %s)",
                        canonical_tool_name,
                        self._failure_budget.get_tool_failure_count(canonical_tool_name),
                        error_pattern.error_signature[:60],
                    )
                    return {
                        "ok": False,
                        "error": failure_result.suggestion
                        or f"Tool {canonical_tool_name} blocked due to repeated failures",
                        "tool": canonical_tool_name,
                        "blocked": True,
                        "failure_count": self._failure_budget.get_tool_failure_count(canonical_tool_name),
                        "error_type": failure_result.error_type,
                        "retryable": failure_result.retryable,
                        "loop_break": failure_result.loop_break,
                    }

                return {
                    "ok": False,
                    "error": error_message,
                    "tool": canonical_tool_name,
                    "suggestion": suggestion,
                    "error_type": failure_result.error_type,
                    "retryable": failure_result.retryable,
                    "loop_break": failure_result.loop_break,
                }
            return {"ok": True, "result": result}
        except BudgetExceededError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "error_code": "BUDGET_EXCEEDED",
                "tool": canonical_tool_name,
                "suggestion": exc.suggestion,
                "file": exc.file,
                "line_count": exc.line_count,
                "limit": exc.limit,
                "loop_break": False,
            }
        except (ValueError, OSError, PermissionError, UnicodeDecodeError) as e:
            logger.warning(
                "Tool execution failed for %s: %s (%s)",
                canonical_tool_name,
                type(e).__name__,
                e,
            )
            # Classify error and check failure budget
            error_pattern = self._error_classifier.classify(canonical_tool_name, e)
            failure_result = self._failure_budget.record_failure(error_pattern)

            if failure_result.decision == "BLOCK":
                logger.warning(
                    "[ToolExecutor] BLOCKING %s after %d failures (pattern: %s)",
                    canonical_tool_name,
                    self._failure_budget.get_tool_failure_count(canonical_tool_name),
                    error_pattern.error_signature[:60],
                )
                return {
                    "ok": False,
                    "error": failure_result.suggestion
                    or f"Tool {canonical_tool_name} blocked due to repeated failures",
                    "tool": canonical_tool_name,
                    "blocked": True,
                    "failure_count": self._failure_budget.get_tool_failure_count(canonical_tool_name),
                    "error_type": failure_result.error_type,
                    "retryable": failure_result.retryable,
                    "loop_break": failure_result.loop_break,
                }

            return {
                "ok": False,
                "error": str(e),
                "tool": canonical_tool_name,
                "suggestion": failure_result.suggestion,
                "error_type": failure_result.error_type,
                "retryable": failure_result.retryable,
                "loop_break": failure_result.loop_break,
            }

    # ========================================================================
    # Tree-sitter handlers (complex, kept in core for now)
    # ========================================================================

    def _handle_treesitter_find_symbol(self, **kwargs) -> dict[str, Any]:
        """Find symbol definitions using tree-sitter AST analysis with fuzzy match."""
        return self._treesitter_handler.find_symbol(**kwargs)

    async def execute_graph(
        self,
        graph: Any,
        *,
        initial_context: Any | None = None,
    ) -> dict[str, Any]:
        """Execute a tool call graph with parallel execution support.

        This method provides DAG-based tool call execution with:
        - Parallel execution of independent nodes
        - Conditional branching via edge conditions
        - Retry policies per node
        - Timeout control per node

        Args:
            graph: A ToolCallGraph instance defining the execution DAG.
            initial_context: Optional ExecutionContext with workspace and metadata.

        Returns:
            dict with execution result containing:
            - ok: bool indicating overall success
            - node_results: dict mapping node_id to NodeResult
            - total_nodes: int total node count
            - completed_nodes: int successfully completed nodes
            - failed_nodes: int failed nodes
            - skipped_nodes: int skipped nodes
            - duration_ms: int total execution time
        """
        from dataclasses import dataclass

        from polaris.kernelone.llm.contracts.tool import ToolCall, ToolExecutionResult
        from polaris.kernelone.tool_execution.graph import (
            ExecutionContext,
            GraphExecutor,
        )

        @dataclass(frozen=True)
        class _SyncToolExecutorAdapter:
            """Adapter to make AgentAccelToolExecutor compatible with ToolExecutorPort."""

            _executor: AgentAccelToolExecutor

            def execute_call(
                self,
                *,
                workspace: str,
                tool_call: ToolCall,
            ) -> ToolExecutionResult:
                """Execute a single tool call synchronously.

                Args:
                    workspace: Workspace path.
                    tool_call: ToolCall to execute.

                Returns:
                    ToolExecutionResult with execution outcome.
                """
                import time

                start = time.time()
                try:
                    result = self._executor.execute(
                        tool_name=tool_call.name,
                        arguments=dict(tool_call.arguments),
                    )
                    duration_ms = int((time.time() - start) * 1000)

                    if result.get("ok", False):
                        return ToolExecutionResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            success=True,
                            result=result.get("result", {}),
                            duration_ms=duration_ms,
                        )
                    else:
                        return ToolExecutionResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            success=False,
                            error=result.get("error", "unknown error"),
                            duration_ms=duration_ms,
                        )
                except (RuntimeError, ValueError) as exc:
                    duration_ms = int((time.time() - start) * 1000)
                    return ToolExecutionResult(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        success=False,
                        error=str(exc),
                        duration_ms=duration_ms,
                    )

        # Create execution context from initial_context if provided
        ctx: ExecutionContext | None = None
        if initial_context is not None:
            if isinstance(initial_context, ExecutionContext):
                ctx = initial_context
            elif isinstance(initial_context, dict):
                ctx = ExecutionContext(
                    workspace=initial_context.get("workspace", self.workspace),
                    node_results=initial_context.get("node_results", {}),
                    metadata=initial_context.get("metadata", {}),
                )

        # Create adapter and executor
        adapter = _SyncToolExecutorAdapter(_executor=self)
        graph_executor = GraphExecutor(executor=adapter)

        # Execute the graph
        result = await graph_executor.execute(graph, initial_context=ctx)

        # Convert to dict format for backward compatibility
        return {
            "ok": result.ok,
            "node_results": {
                node_id: {
                    "ok": nr.ok,
                    "result": nr.result,
                    "error": nr.error,
                    "skipped": nr.skipped,
                    "duration_ms": nr.duration_ms,
                }
                for node_id, nr in result.node_results.items()
            },
            "total_nodes": result.total_nodes,
            "completed_nodes": result.completed_nodes,
            "failed_nodes": result.failed_nodes,
            "skipped_nodes": result.skipped_nodes,
            "duration_ms": result.duration_ms,
        }

    def reset_failure_budget(self) -> None:
        """Reset the failure budget counter.

        Should be called at the start of each new turn to allow
        fresh failure tracking per turn.
        """
        self._failure_budget.reset()
        self._error_classifier.clear_cache()

    def get_failure_stats(self) -> dict[str, Any]:
        """Get current failure statistics for debugging.

        Returns:
            dict with total_failures, tool_failures, blocked_tools
        """
        return self._failure_budget.get_stats()

    async def close(self) -> None:
        """Close the executor asynchronously."""
        if self._closed:
            return
        self._closed = True

    def close_sync(self) -> None:
        """Close the executor synchronously."""
        if self._closed:
            return
        self._closed = True


# Convenience functions
def execute_tool_call(
    workspace: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single tool call.

    Args:
        workspace: Workspace directory
        tool_name: Tool name
        arguments: Tool arguments

    Returns:
        Execution result
    """
    executor = AgentAccelToolExecutor(workspace)
    try:
        return executor.execute(tool_name, arguments)
    finally:
        executor.close_sync()


def execute_tool_calls(
    workspace: str,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Execute multiple tool calls.

    Args:
        workspace: Workspace directory
        tool_calls: List of tool calls

    Returns:
        List of execution results
    """
    executor = AgentAccelToolExecutor(workspace)
    try:
        results = []
        for call in tool_calls:
            result = executor.execute(
                call.get("name", ""),
                call.get("arguments", {}),
            )
            results.append(
                {
                    "tool_call_id": call.get("id"),
                    "name": call.get("name"),
                    **result,
                }
            )
        return results
    finally:
        executor.close_sync()
