"""Enhanced tool chain executor for KernelOne tool execution.

DEPRECATED (2026-04-05): This module is kept for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_tool_chain(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """DEPRECATED: Use AgentAccelToolExecutor instead."""
    logger.warning("run_tool_chain is deprecated, use AgentAccelToolExecutor")
    return {"ok": False, "error": "Deprecated: use AgentAccelToolExecutor"}


class ToolChainExecutor:
    """DEPRECATED: Use AgentAccelToolExecutor instead."""

    async def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        logger.warning("ToolChainExecutor is deprecated")
        return {"ok": False, "error": "Deprecated"}
