from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.infrastructure.llm.tools import create_kernel_tool_runtime
from polaris.kernelone.llm.contracts import ToolCall, ToolPolicy, ToolRoundRequest

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True)
class RoleToolRoundResult:
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    tool_feedback: str
    assistant_remainder: str
    should_continue: bool


class RoleToolRoundOrchestrator:
    """Application-level bridge for role dialogue and KernelOne tool runtime."""

    def __init__(self) -> None:
        self._runtime = create_kernel_tool_runtime()

    def run_round(
        self,
        *,
        workspace: str,
        assistant_text: str = "",
        native_tool_calls: Sequence[dict[str, Any]] = (),
        preparsed_tool_calls: Sequence[dict[str, Any]] = (),
        response_payload: dict[str, Any] | None = None,
        provider_hint: str = "auto",
        allowed_tools: Iterable[str] | None = None,
        max_tool_calls: int = 4,
        fail_fast: bool = False,
    ) -> RoleToolRoundResult:
        policy = ToolPolicy(
            allowed_tool_names=tuple(
                str(item or "").strip().lower() for item in (allowed_tools or []) if str(item or "").strip()
            ),
            max_tool_calls=int(max_tool_calls or 0),
            fail_fast=bool(fail_fast),
        )
        normalized_preparsed = self._normalize_preparsed_calls(preparsed_tool_calls)
        if normalized_preparsed:
            outcome = self._runtime.execute_calls(
                workspace=workspace,
                calls=normalized_preparsed,
                policy=policy,
                assistant_remainder=str(assistant_text or "").strip(),
            )
        else:
            outcome = self._runtime.execute_round(
                ToolRoundRequest(
                    workspace=workspace,
                    assistant_text=assistant_text,
                    native_tool_calls=native_tool_calls,
                    response_payload=response_payload,
                    provider_hint=provider_hint,
                    policy=policy,
                )
            )
        return RoleToolRoundResult(
            tool_calls=[
                {
                    "id": call.id,
                    "name": call.name,
                    "args": dict(call.arguments or {}),
                    "source": call.source,
                }
                for call in outcome.tool_calls
            ],
            tool_results=[
                {
                    "tool_call_id": item.tool_call_id,
                    "tool": item.name,
                    "success": item.success,
                    "result": dict(item.result or {}),
                    "error": item.error,
                    "duration_ms": item.duration_ms,
                    "blocked": item.blocked,
                }
                for item in outcome.tool_results
            ],
            tool_feedback=outcome.feedback_text,
            assistant_remainder=outcome.assistant_remainder,
            should_continue=outcome.should_continue,
        )

    @staticmethod
    def _normalize_preparsed_calls(items: Sequence[dict[str, Any]]) -> list[ToolCall]:
        normalized: list[ToolCall] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool") or item.get("name") or "").strip().lower()
            arguments = item.get("args")
            if not isinstance(arguments, dict):
                arguments = item.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            tool_call_id = str(item.get("id") or item.get("tool_call_id") or f"preparsed_{index + 1}").strip()
            if not name:
                continue
            normalized.append(
                ToolCall(
                    id=tool_call_id,
                    name=name,
                    arguments=dict(arguments),
                    source="preparsed",
                    raw="",
                )
            )
        return normalized
