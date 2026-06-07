"""Real ReadToolPort: execute read tools via the canonical executor (UTF-8).

Read-only is enforced here: only tools whose ToolSpec category is ``read`` are
allowed through this adapter. Execution itself goes through the canonical
``AgentAccelToolExecutor`` (the same path the roles use), which loads its own
handler modules — we do NOT depend on ``ToolSpec.handler_module`` (empty for
builtin tools).
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


class ReadOnlyViolationError(RuntimeError):
    """Raised when a non-read tool is requested through the scout adapter."""


# Public alias used in tests and plan code (ruff N818 requires *Error suffix on the canonical class).
ReadOnlyViolation = ReadOnlyViolationError


class RegistryReadTool:
    """ReadToolPort backed by the canonical AgentAccelToolExecutor.

    The read-only gate is enforced against ``ToolSpecRegistry`` before any
    execution; the executor then runs the tool against the workspace. A single
    executor instance is reused across calls and closed via :meth:`close`.
    """

    def __init__(self, workspace: str, timeout: int = 30) -> None:
        self._workspace = str(workspace or ".")
        self._timeout = int(timeout)
        self._executor: AgentAccelToolExecutor | None = None

    def _ensure_executor(self) -> AgentAccelToolExecutor:
        if self._executor is None:
            self._executor = AgentAccelToolExecutor(self._workspace)
        return self._executor

    def run(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a read tool and return a normalized, flat payload.

        Returns ``{"ok": True, **payload}`` on success (so ``results``/``hits``/
        ``entries``/``symbols`` are top-level), or ``{"ok": False, "error": ...}``
        on failure. Raises :class:`ReadOnlyViolationError` for non-read tools.
        """
        spec = ToolSpecRegistry.get(tool)
        if spec is None:
            raise ReadOnlyViolationError(f"unknown tool: {tool}")
        if not spec.is_read_tool():
            raise ReadOnlyViolationError(f"tool {tool!r} is not a read tool (categories={spec.categories})")

        executor = self._ensure_executor()
        try:
            outcome = executor.execute(tool, dict(args))
        except Exception as exc:  # noqa: BLE001 - fail-soft boundary for one read tool
            # A single buggy/unavailable handler must not abort the whole probe.
            # The executor normally returns error dicts, but handlers can still
            # raise (e.g. backend/library incompatibilities); contain it here.
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if not outcome.get("ok", False):
            return {"ok": False, "error": outcome.get("error") or "tool execution failed"}

        payload = outcome.get("result")
        if isinstance(payload, dict):
            return {"ok": True, **payload}
        # Some handlers return their fields at the top level (no nested "result").
        flat = {k: v for k, v in outcome.items() if k != "ok"}
        return {"ok": True, **flat}

    def close(self) -> None:
        """Release the underlying executor, if one was created."""
        if self._executor is not None:
            try:
                self._executor.close_sync()
            finally:
                self._executor = None
