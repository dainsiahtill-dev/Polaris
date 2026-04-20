"""Tool execution core - DEPRECATED

Use AgentAccelToolExecutor from polaris.kernelone.llm.toolkit.executor.core instead.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_tool_plan(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """DEPRECATED: Use AgentAccelToolExecutor instead."""
    logger.warning("run_tool_plan is deprecated, use AgentAccelToolExecutor")
    return []


class ToolExecutor:
    """DEPRECATED: Use AgentAccelToolExecutor instead."""

    async def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        logger.warning("ToolExecutor is deprecated")
        return {"ok": False, "error": "Deprecated"}
