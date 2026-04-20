from __future__ import annotations

from time import perf_counter
from typing import Any

from polaris.kernelone.llm.contracts import ToolCall, ToolExecutionResult, ToolExecutorPort


class LLMToolkitExecutorAdapter(ToolExecutorPort):
    """Bridge executor port to legacy `core.llm_toolkit.executor`."""

    def execute_call(
        self,
        *,
        workspace: str,
        tool_call: ToolCall,
    ) -> ToolExecutionResult:
        from polaris.kernelone.llm.toolkit.executor import execute_tool_call

        start = perf_counter()
        payload = execute_tool_call(
            str(workspace or "."),
            str(tool_call.name or ""),
            dict(tool_call.arguments or {}),
        )
        duration_ms = int((perf_counter() - start) * 1000)

        success = bool(isinstance(payload, dict) and payload.get("ok"))
        result: dict[str, Any] = {}
        error = ""
        blocked = False
        if isinstance(payload, dict):
            result_value = payload.get("result")
            if isinstance(result_value, dict):
                result = dict(result_value)
            elif result_value is not None:
                result = {"value": result_value}
            error = str(payload.get("error") or "")
            blocked = "blocked" in error.lower()
        else:
            error = "invalid_tool_execution_payload"

        return ToolExecutionResult(
            tool_call_id=str(tool_call.id or ""),
            name=str(tool_call.name or ""),
            success=success,
            result=result,
            error=error,
            duration_ms=duration_ms,
            blocked=blocked,
        )
