"""ADR-0090 I3: one corrective re-ask before a decode failure kills the turn.

Weak local models frequently emit tool calls whose arguments fail strict JSON
parsing, or return an entirely empty response. Before this contract the kernel
silently dropped the calls and escalated to ASK_USER (suspending the whole
session) — the model never learned WHAT was wrong. This module decides, from a
probe decode of the raw response, whether the orchestrator should re-ask the
model exactly once with the precise error quoted back.

Pure functions only: no ledger writes, no LLM calls, no state transitions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_MAX_QUOTED_FAILURES = 5
_MAX_ERROR_CHARS = 240


@dataclass(frozen=True, slots=True)
class CorrectiveAsk:
    """A single corrective re-ask the orchestrator should perform."""

    reason: str
    content: str


def _field(payload: Any, key: str) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key)
    return getattr(payload, key, None)


def _decision_kind_value(decision: Any) -> str:
    kind = _field(decision, "kind")
    value = getattr(kind, "value", kind)
    return str(value or "")


def _format_failures(failures: list[Any]) -> str:
    lines: list[str] = []
    for failure in failures[:_MAX_QUOTED_FAILURES]:
        if not isinstance(failure, Mapping):
            continue
        tool = str(failure.get("tool") or "unknown_tool").strip() or "unknown_tool"
        error = str(failure.get("error") or "unparseable arguments")[:_MAX_ERROR_CHARS]
        lines.append(f"- {tool}: {error}")
    return "\n".join(lines)


def _has_exposed_tools(tool_definitions: list[dict[str, Any]] | None) -> bool:
    if tool_definitions is None:
        return True
    return bool(tool_definitions)


def _empty_response_corrective_content(*, tool_definitions: list[dict[str, Any]] | None) -> str:
    if _has_exposed_tools(tool_definitions):
        return (
            "[EMPTY RESPONSE] Your previous response was completely empty. "
            "Respond NOW: either call exactly one appropriate tool with valid "
            "JSON arguments, or answer the user's request directly in text. "
            "An empty response is never acceptable."
        )
    return (
        "[EMPTY RESPONSE] Your previous response was completely empty. "
        "Respond NOW with the requested answer directly in the required format. "
        "No tools are available in this request; do not emit tool calls or tool-call syntax. "
        "An empty response is never acceptable."
    )


def evaluate_decode_corrective(
    decision: Any,
    response: Any,
    *,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> CorrectiveAsk | None:
    """Return the corrective re-ask for a degraded decode, or None.

    Fires in exactly two narrow situations (fail-closed otherwise):

    1. ``tool_call_decode_failure`` — the model attempted native tool calls but
       NONE survived decoding (no tool batch authorized). The corrective message
       quotes each parse error verbatim so the model can transcribe a fixed call.
    2. ``empty_response`` — no content, no thinking, no tool calls. One direct
       re-ask replaces an immediate WAITING_HUMAN suspension.
    """
    metadata = _field(decision, "metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    raw_failures = metadata.get("decode_failures")
    failures = list(raw_failures) if isinstance(raw_failures, list) else []
    native_calls = _field(response, "native_tool_calls")
    native_attempted = len(native_calls) if isinstance(native_calls, list) else 0
    tool_batch = _field(decision, "tool_batch")

    if failures and native_attempted and not tool_batch:
        quoted = _format_failures(failures)
        return CorrectiveAsk(
            reason="tool_call_decode_failure",
            content=(
                "[TOOL_CALL FORMAT ERROR] Your previous response attempted "
                f"{native_attempted} tool call(s) but NONE could be executed — "
                "the arguments failed to parse:\n"
                f"{quoted}\n"
                "Re-issue the SAME intended tool call(s) right now. Arguments MUST be "
                'a single valid JSON object, for example: {"pattern": "class Foo", '
                '"path": "src/"}. Use double quotes, no trailing commas, and escape '
                "newlines inside strings as \\n. Do NOT apologize or explain — just "
                "emit the corrected tool call."
            ),
        )

    if (
        _decision_kind_value(decision) == "ask_user"
        and str(metadata.get("source") or "") == "clarification_needed"
        and native_attempted == 0
    ):
        content_text = str(_field(response, "content") or "").strip()
        thinking_text = str(_field(response, "thinking") or "").strip()
        if not content_text and not thinking_text:
            return CorrectiveAsk(
                reason="empty_response",
                content=_empty_response_corrective_content(tool_definitions=tool_definitions),
            )

    return None


def build_corrective_context(context: list[dict[str, Any]], ask: CorrectiveAsk) -> list[dict[str, Any]]:
    """Return a copy of the decision context with the corrective system message appended."""
    corrected = list(context)
    corrected.append({"role": "system", "content": ask.content})
    return corrected
