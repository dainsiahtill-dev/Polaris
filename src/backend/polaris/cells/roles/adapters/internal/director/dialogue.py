"""LLM 对话相关函数

包含角色 LLM 调用和 Sequential Engine LLM caller。
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.bootstrap import config as backend_config

logger = logging.getLogger(__name__)


async def seq_llm_caller(
    workspace: str,
    role_id: str,
    prompt: str,
    context: dict[str, Any],
    call_role_llm_with_timeout: Any,
) -> str:
    """Async LLM caller for SequentialEngine.

    Args:
        workspace: Workspace path.
        role_id: Role identifier.
        prompt: Prompt text.
        context: Execution context.
        call_role_llm_with_timeout: Bound method for calling role LLM with timeout.

    Returns:
        LLM response content string.
    """
    call_context = dict(context)
    call_context["sequential_mode"] = True
    llm_result = await call_role_llm_with_timeout(prompt, context=call_context)
    if not isinstance(llm_result, dict):
        return str(llm_result or "")
    return str(llm_result.get("content") or llm_result.get("response") or llm_result.get("raw_response") or "")


def get_settings_safe() -> Any:
    """Get settings safely, returning None on error."""
    try:
        return backend_config.get_settings()
    except AttributeError:
        return None
