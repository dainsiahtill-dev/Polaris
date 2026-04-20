from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.contracts import ToolCall, ToolCallParserPort

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


class LLMToolkitParserAdapter(ToolCallParserPort):
    """Bridge parser port to canonical native tool-call parsing."""

    def parse_calls(
        self,
        *,
        text: str = "",
        native_tool_calls: Sequence[dict[str, Any]] = (),
        response_payload: dict[str, Any] | None = None,
        provider_hint: str = "auto",
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ToolCall]:
        from polaris.kernelone.llm.toolkit.parsers import parse_tool_calls

        parsed = parse_tool_calls(
            text=str(text or ""),
            tool_calls=list(native_tool_calls or []),
            response=response_payload,
            provider=str(provider_hint or "auto"),
            allowed_tool_names=allowed_tool_names,
        )
        return [self._to_tool_call(item, source="legacy_parser") for item in parsed]

    def extract_calls_and_remainder(
        self,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> tuple[list[ToolCall], str]:
        del allowed_tool_names
        return [], str(text or "")

    def _to_tool_call(self, parsed: Any, *, source: str) -> ToolCall:
        arguments = getattr(parsed, "arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        return ToolCall(
            id=str(getattr(parsed, "id", "") or ""),
            name=str(getattr(parsed, "name", "") or ""),
            arguments=dict(arguments),
            source=source,
            raw=str(getattr(parsed, "raw", "") or ""),
        )
