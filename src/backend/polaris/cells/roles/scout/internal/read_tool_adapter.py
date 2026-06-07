"""Real ReadToolPort: resolve read tools via ToolSpecRegistry handlers (UTF-8).

Read-only is enforced here: only tools whose ToolSpec category is `read` run.
"""
from __future__ import annotations

import importlib
from typing import Any

from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


class ReadOnlyViolation(RuntimeError):
    """Raised when a non-read tool is requested through the scout adapter."""


class RegistryReadTool:
    """ReadToolPort backed by the canonical ToolSpecRegistry handler map."""

    def __init__(self, workspace: str, timeout: int = 30) -> None:
        self._workspace = str(workspace or ".")
        self._timeout = int(timeout)

    def run(self, tool: str, args: list[str]) -> dict[str, Any]:
        spec = ToolSpecRegistry.get(tool)
        if spec is None:
            raise ReadOnlyViolation(f"unknown tool: {tool}")
        if not spec.is_read_tool():
            raise ReadOnlyViolation(f"tool {tool!r} is not a read tool (categories={spec.categories})")
        if not spec.handler_module or not spec.handler_function:
            raise ReadOnlyViolation(f"tool {tool!r} has no resolvable handler")
        module = importlib.import_module(spec.handler_module)
        handler = getattr(module, spec.handler_function)
        return handler(list(args), self._workspace, self._timeout)  # type: ignore[no-any-return]
