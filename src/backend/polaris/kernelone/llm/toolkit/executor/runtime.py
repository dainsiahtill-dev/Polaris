"""KernelToolCallingRuntime - Unified tool calling runtime.

This module provides the KernelOne runtime for one-round tool parsing/execution/feedback.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from time import perf_counter
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.contracts.tool import (
    ToolRoundOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from polaris.kernelone.llm.contracts.tool import (
        ToolCall,
        ToolCallParserPort,
        ToolExecutionResult,
        ToolExecutorPort,
        ToolPolicy,
        ToolRoundRequest,
    )


# Tool name pattern for validation
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _normalize_json_value(value: Any) -> Any:
    """Normalize a JSON-compatible value."""
    if isinstance(value, dict):
        return {str(k): _normalize_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_tool_name(value: str) -> str:
    """Normalize tool name to lowercase with underscores."""
    from polaris.kernelone.llm.toolkit.tool_normalization import (
        normalize_tool_name as toolkit_normalize,
    )

    token = toolkit_normalize(str(value or "").strip().lower())
    if not token:
        return ""
    if not _TOOL_NAME_PATTERN.fullmatch(token):
        return ""
    return token


def _normalize_tool_arguments(value: Any, tool_name: str = "") -> dict[str, Any]:
    """Normalize tool arguments."""
    from polaris.kernelone.llm.toolkit.tool_normalization import (
        normalize_tool_arguments as toolkit_normalize_args,
    )

    if not isinstance(value, dict):
        return {}
    normalized = toolkit_normalize_args(tool_name, value)
    if not isinstance(normalized, dict):
        return {}
    output: dict[str, Any] = {}
    for key, raw in normalized.items():
        safe_key = str(key or "").strip()
        if not safe_key:
            continue
        output[safe_key] = _normalize_json_value(raw)
    return output


def _normalize_tool_calls(calls: list[ToolCall]) -> list[ToolCall]:
    """Normalize a list of tool calls, deduplicating by signature."""
    normalized: list[ToolCall] = []
    seen: set[tuple[str, str]] = set()

    for call in calls:
        safe_name = _normalize_tool_name(call.name)
        if not safe_name:
            continue
        safe_args = _normalize_tool_arguments(call.arguments, tool_name=safe_name)
        signature = (
            safe_name,
            json.dumps(_normalize_json_value(safe_args), ensure_ascii=False, sort_keys=True),
        )
        if signature in seen:
            continue
        seen.add(signature)
        normalized.append(
            call.__class__(
                id=str(call.id or "").strip(),
                name=safe_name,
                arguments=safe_args,
                source=str(call.source or "").strip() or "unknown",
                raw=str(call.raw or ""),
                parse_error=call.parse_error,
            )
        )
    return normalized


def _allowed_tool_set(policy: ToolPolicy) -> set[str]:
    """Get the set of allowed tool names from policy."""
    return {_normalize_tool_name(name) for name in policy.allowed_tool_names if _normalize_tool_name(name)}


def _apply_call_limit(
    calls: list[ToolCall],
    policy: ToolPolicy,
) -> tuple[list[ToolCall], list[ToolCall]]:
    """Apply call limit policy, separating limited and overflow calls."""
    max_calls = int(policy.max_tool_calls or 0)
    if max_calls <= 0:
        return [], list(calls)
    allowed = list(calls[:max_calls])
    overflow = list(calls[max_calls:])
    return allowed, overflow


class KernelToolCallingRuntime:
    """KernelOne runtime for one-round tool parsing/execution/feedback.

    This class orchestrates the tool-calling workflow:
    1. Parse tool calls from various formats (text, native, etc.)
    2. Normalize and validate tool calls
    3. Apply policy (allowed tools, call limits)
    4. Execute tool calls
    5. Build feedback for the next round

    Args:
        parser: Tool call parser implementing ToolCallParserPort.
        executor: Tool executor implementing ToolExecutorPort.
    """

    def __init__(
        self,
        parser: ToolCallParserPort,
        executor: ToolExecutorPort,
    ) -> None:
        self._parser = parser
        self._executor = executor

    def execute_round(self, request: ToolRoundRequest) -> ToolRoundOutcome:
        """Execute one tool-calling round.

        Args:
            request: The round request containing input data.

        Returns:
            The round outcome with tool calls, results, and feedback.
        """
        parsed_calls: list[ToolCall] = []
        assistant_remainder = str(request.assistant_text or "").strip()

        # Parse from raw text (text-based tool calls)
        if request.assistant_text:
            text_calls, remainder = self._parser.extract_calls_and_remainder(
                request.assistant_text,
                allowed_tool_names=request.policy.allowed_tool_names,
            )
            parsed_calls.extend(text_calls)
            assistant_remainder = str(remainder or "").strip()

        # Parse from native format
        native_calls = self._parser.parse_calls(
            text="",
            native_tool_calls=request.native_tool_calls,
            response_payload=request.response_payload,
            provider_hint=request.provider_hint,
            allowed_tool_names=request.policy.allowed_tool_names,
        )
        parsed_calls.extend(native_calls)

        return self.execute_calls(
            workspace=request.workspace,
            calls=parsed_calls,
            policy=request.policy,
            assistant_remainder=assistant_remainder,
        )

    def execute_calls(
        self,
        *,
        workspace: str,
        calls: Iterable[ToolCall],
        policy: ToolPolicy,
        assistant_remainder: str = "",
    ) -> ToolRoundOutcome:
        """Execute a list of tool calls with policy enforcement.

        Args:
            workspace: Workspace path for execution.
            calls: Tool calls to execute.
            policy: Execution policy.
            assistant_remainder: Text remaining after tool extraction.

        Returns:
            Round outcome with results and feedback.
        """
        normalized_calls = _normalize_tool_calls(list(calls))
        tool_results: list[ToolExecutionResult] = []

        policy_allow = _allowed_tool_set(policy)
        executable_calls: list[ToolCall] = []

        # Check policy - block disallowed tools
        for call in normalized_calls:
            if policy_allow and call.name not in policy_allow:
                tool_results.append(
                    self._executor.execute_call(
                        workspace=workspace,
                        tool_call=call,
                    ).__class__(
                        tool_call_id=call.id,
                        name=call.name,
                        success=False,
                        error="tool_blocked_by_policy",
                        blocked=True,
                    )
                )
                continue
            executable_calls.append(call)

        # Apply call limit
        limited_calls, overflow_calls = _apply_call_limit(executable_calls, policy)
        for call in overflow_calls:
            tool_results.append(
                self._executor.execute_call(
                    workspace=workspace,
                    tool_call=call,
                ).__class__(
                    tool_call_id=call.id,
                    name=call.name,
                    success=False,
                    error="tool_call_limit_exceeded",
                    blocked=True,
                )
            )

        # Execute remaining calls
        for call in limited_calls:
            start = perf_counter()
            result = self._executor.execute_call(
                workspace=workspace,
                tool_call=call,
            )
            duration_ms = int((perf_counter() - start) * 1000)
            if result.duration_ms <= 0:
                result = replace(result, duration_ms=duration_ms)
            tool_results.append(result)
            if policy.fail_fast and not result.success:
                break

        feedback_text = _build_tool_feedback(tool_results)
        should_continue = bool(tool_results)
        return ToolRoundOutcome(
            tool_calls=limited_calls,
            tool_results=tool_results,
            assistant_remainder=str(assistant_remainder or "").strip(),
            feedback_text=feedback_text,
            should_continue=should_continue,
        )


def _build_tool_feedback(results: Iterable[ToolExecutionResult]) -> str:
    """Build human-readable feedback from tool results."""
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        status = "success" if result.success else "failed"
        line = f"{index}. {result.name}: {status}"
        if result.blocked:
            line += " (blocked)"
        if result.error:
            line += f"; error={result.error}"
        elif result.result:
            preview_keys = list(result.result.keys())[:4]
            if preview_keys:
                line += f"; result_keys={','.join(str(k) for k in preview_keys)}"
        lines.append(line)
    if not lines:
        return "- no tool results"
    return "\n".join(lines)


def build_tool_feedback(results: Iterable[ToolExecutionResult]) -> str:
    """Public API for building tool feedback.

    Args:
        results: Iterable of tool execution results.

    Returns:
        Formatted feedback string.
    """
    return _build_tool_feedback(results)
