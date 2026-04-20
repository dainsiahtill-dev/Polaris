from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import TYPE_CHECKING

from .contracts import (
    ToolCall,
    ToolCallParserPort,
    ToolExecutionResult,
    ToolExecutorPort,
    ToolPolicy,
    ToolRoundOutcome,
    ToolRoundRequest,
)
from .normalizer import allowed_tool_set, apply_call_limit, normalize_tool_calls

if TYPE_CHECKING:
    from collections.abc import Iterable


class KernelToolCallingRuntime:
    """KernelOne runtime for one-round tool parsing/execution/feedback."""

    def __init__(self, parser: ToolCallParserPort, executor: ToolExecutorPort) -> None:
        self._parser = parser
        self._executor = executor

    def execute_round(self, request: ToolRoundRequest) -> ToolRoundOutcome:
        parsed_calls: list[ToolCall] = []
        assistant_remainder = str(request.assistant_text or "").strip()

        if request.assistant_text:
            text_calls, remainder = self._parser.extract_calls_and_remainder(
                request.assistant_text,
                allowed_tool_names=request.policy.allowed_tool_names,
            )
            parsed_calls.extend(text_calls)
            assistant_remainder = str(remainder or "").strip()

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
        normalized_calls = normalize_tool_calls(list(calls))
        tool_results: list[ToolExecutionResult] = []

        policy_allow = allowed_tool_set(policy)
        executable_calls: list[ToolCall] = []
        for call in normalized_calls:
            if policy_allow and call.name not in policy_allow:
                tool_results.append(
                    ToolExecutionResult(
                        tool_call_id=call.id,
                        name=call.name,
                        success=False,
                        error="tool_blocked_by_policy",
                        blocked=True,
                    )
                )
                continue
            executable_calls.append(call)

        limited_calls, overflow_calls = apply_call_limit(executable_calls, policy)
        for call in overflow_calls:
            tool_results.append(
                ToolExecutionResult(
                    tool_call_id=call.id,
                    name=call.name,
                    success=False,
                    error="tool_call_limit_exceeded",
                    blocked=True,
                )
            )

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

        feedback_text = build_tool_feedback(tool_results)
        should_continue = bool(tool_results)
        return ToolRoundOutcome(
            tool_calls=limited_calls,
            tool_results=tool_results,
            assistant_remainder=str(assistant_remainder or "").strip(),
            feedback_text=feedback_text,
            should_continue=should_continue,
        )


def build_tool_feedback(results: Iterable[ToolExecutionResult]) -> str:
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
