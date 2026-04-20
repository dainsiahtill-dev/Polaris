"""Turn materialization - Build typed assistant-turn artifacts.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责：
    将 LLM 原始输出转换为结构化的 AssistantTurnArtifacts。
    包括 thinking 解析、内容消毒、native tool call 归一化。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

from .artifacts import AssistantTurnArtifacts
from .utils import merge_stream_thinking


class TurnMaterializer:
    """Builds typed assistant-turn bundles from raw LLM output."""

    def __init__(self, output_parser: Any | None = None) -> None:
        """Initialize with optional output parser for DI.

        Args:
            output_parser: Output parser with parse_thinking() method.
        """
        self._output_parser = output_parser

    def _get_parser(self, kernel: Any | None = None) -> Any:
        """Resolve output parser from injected instance, kernel, or lazy default."""
        if self._output_parser is not None:
            return self._output_parser
        if kernel is not None:
            parser = getattr(kernel, "_output_parser", None)
            if parser is not None:
                return parser
        # Fallback: lazy import default parser
        from polaris.cells.roles.kernel.internal.output_parser import OutputParser

        return OutputParser()

    @staticmethod
    def _sanitize_assistant_transcript_message(
        text: str,
        *,
        allowed_tool_names: list[str] | None = None,
    ) -> str:
        """Strip executable textual tool wrappers before transcript injection.

        The transcript should capture assistant reasoning/result text, not replay
        executable wrappers like ``[TOOL_CALL]{...}[/TOOL_CALL]``. Keeping raw
        wrappers in history increases loop risk (model re-emits historical calls).
        """
        token = str(text or "")
        if not token.strip():
            return ""
        try:
            from polaris.cells.roles.kernel.internal.tool_call_protocol import (
                CanonicalToolCallParser,
            )

            _, remainder = CanonicalToolCallParser.extract_text_calls_and_remainder(
                token,
                allowed_tool_names=allowed_tool_names,
            )
            return str(remainder or "").strip()
        except (ValueError, TypeError, AttributeError):
            return token.strip()

    def materialize(
        self,
        *,
        profile: RoleProfile,
        raw_output: str,
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_tool_provider: str = "auto",
        kernel: Any | None = None,
    ) -> AssistantTurnArtifacts:
        """Build one typed assistant-turn bundle.

        The ordering is the contract:
            raw_output -> parse thinking -> raw_content -> sanitize -> clean_content

        `raw_content` is retained for audit/debugging of protocol violations only.
        `clean_content` is the only prompt-facing text that may continue into
        runtime parsing and transcript persistence.

        Args:
            profile: RoleProfile instance.
            raw_output: Raw LLM output text.
            native_tool_calls: Optional native tool calls from provider.
            native_tool_provider: Provider hint for native tool calls.
            kernel: Optional kernel for resolving output parser.

        Returns:
            AssistantTurnArtifacts instance.
        """
        parser = self._get_parser(kernel)
        thinking_result = parser.parse_thinking(str(raw_output or ""))

        raw_content = str(thinking_result.clean_content or "")
        thinking_text = str(thinking_result.thinking or "")

        allowed_names = list(getattr(profile.tool_policy, "whitelist", []) or [])
        clean_content = self._sanitize_assistant_transcript_message(
            raw_content,
            allowed_tool_names=allowed_names,
        )

        normalized_native_tool_calls = tuple(
            dict(item) for item in list(native_tool_calls or []) if isinstance(item, dict)
        )
        return AssistantTurnArtifacts(
            raw_content=raw_content,
            clean_content=clean_content,
            thinking=thinking_text or None,
            native_tool_calls=normalized_native_tool_calls,
            native_tool_provider=str(native_tool_provider or "auto").strip() or "auto",
        )

    def materialize_stream_visible(
        self,
        *,
        profile: RoleProfile,
        raw_output: str,
        streamed_thinking_parts: list[str],
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_tool_provider: str = "auto",
        kernel: Any | None = None,
    ) -> AssistantTurnArtifacts:
        """Project one provider stream round into user-visible assistant text.

        Args:
            profile: RoleProfile instance.
            raw_output: Concatenated raw output from stream.
            streamed_thinking_parts: List of thinking chunks received during stream.
            native_tool_calls: Optional native tool calls from provider.
            native_tool_provider: Provider hint for native tool calls.
            kernel: Optional kernel for resolving output parser.

        Returns:
            AssistantTurnArtifacts instance with merged thinking.
        """
        turn = self.materialize(
            profile=profile,
            raw_output=raw_output,
            native_tool_calls=native_tool_calls,
            native_tool_provider=native_tool_provider,
            kernel=kernel,
        )
        merged_thinking = merge_stream_thinking(
            parsed_thinking=turn.thinking,
            streamed_thinking_parts=streamed_thinking_parts,
        )
        if merged_thinking == turn.thinking:
            return turn
        return AssistantTurnArtifacts(
            raw_content=turn.raw_content,
            clean_content=turn.clean_content,
            thinking=merged_thinking,
            native_tool_calls=turn.native_tool_calls,
            native_tool_provider=turn.native_tool_provider,
        )

    @staticmethod
    def parse_tool_calls(
        *,
        profile: RoleProfile,
        turn: AssistantTurnArtifacts,
        kernel: Any,
    ) -> list[Any]:
        """Parse tool calls from the sanitized assistant turn plus native payloads.

        Args:
            profile: RoleProfile instance.
            turn: AssistantTurnArtifacts instance.
            kernel: RoleExecutionKernel with _parse_content_and_thinking_tool_calls.

        Returns:
            List of parsed tool calls.
        """
        return kernel._parse_content_and_thinking_tool_calls(
            turn.clean_content,
            turn.thinking,
            profile,
            native_tool_calls=list(turn.native_tool_calls) or None,
            native_tool_provider=turn.native_tool_provider,
        )


__all__ = ["TurnMaterializer"]
