"""Stream event handler - Normalize and yield streaming LLM events.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责:
    处理流式 LLM 响应的事件规范化、去重、SLO 追踪和 yield 生成。
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator, Generator

from polaris.kernelone.llm.providers.stream_thinking_parser import ChunkKind
from polaris.kernelone.llm.toolkit.streaming_patch_buffer import StreamingPatchBuffer

from .artifacts import _BracketToolWrapperFilter
from .utils import normalize_stream_tool_call_payload, tool_call_signature, visible_delta


class _OutputTagFilter:
    """Incrementally strip <output>...</output> tags while preserving inner text."""

    def __init__(self) -> None:
        self._buffer: str = ""
        self._inside_tag: bool = False
        self._content_buffer: list[str] = []

    def feed(self, token: str) -> str:
        chunk = str(token or "")
        if not chunk:
            return ""
        self._buffer += chunk
        visible_parts: list[str] = []

        while self._buffer:
            if self._inside_tag:
                close_match = re.search(r"</output\s*>", self._buffer, re.IGNORECASE)
                if close_match is not None:
                    inner = self._buffer[: close_match.start()]
                    self._content_buffer.append(inner)
                    visible_parts.append("".join(self._content_buffer))
                    self._content_buffer = []
                    self._buffer = self._buffer[close_match.end() :]
                    self._inside_tag = False
                    continue
                # No close yet: buffer everything for now
                self._content_buffer.append(self._buffer)
                self._buffer = ""
                break

            if "<" not in self._buffer:
                visible_parts.append(self._buffer)
                self._buffer = ""
                break

            open_match = re.search(r"<output\b[^>]*>", self._buffer, re.IGNORECASE)
            if open_match is not None:
                if open_match.start() > 0:
                    visible_parts.append(self._buffer[: open_match.start()])
                self._buffer = self._buffer[open_match.end() :]
                self._inside_tag = True
                self._content_buffer = []
                continue

            last_bracket = self._buffer.rfind("<")
            if last_bracket > 0:
                visible_parts.append(self._buffer[:last_bracket])
                self._buffer = self._buffer[last_bracket:]
            break

        return "".join(visible_parts)

    def flush(self) -> str:
        if self._inside_tag:
            inner = "".join(self._content_buffer) + self._buffer
            self._buffer = ""
            self._content_buffer = []
            self._inside_tag = False
            return inner
        token = self._buffer
        self._buffer = ""
        return token


class StreamEventHandler:
    """Handles normalization and incremental yielding of streaming LLM events."""

    def __init__(self, workspace: str) -> None:
        """Initialize stream handler with workspace for patch buffer.

        Args:
            workspace: Workspace path for StreamingPatchBuffer.
        """
        self._patch_buffer = StreamingPatchBuffer(workspace=workspace)
        self._output_filter = _OutputTagFilter()
        self._visible_filter = _BracketToolWrapperFilter()

    def reset(self) -> None:
        """Reset internal state for a new stream session."""
        # StreamingPatchBuffer does not have a reset method; we recreate on demand
        pass

    async def process_stream(
        self,
        stream_iterator: AsyncIterator[dict[str, Any]],
        *,
        round_index: int,
        start_time: float,
        profile: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Process a raw LLM stream and yield normalized events.

        Args:
            stream_iterator: Async iterator of raw stream chunks.
            round_index: Current turn round index.
            start_time: perf_counter start time for latency tracking.
            profile: RoleProfile for thinking parsing.

        Yields:
            Normalized event dicts.
        """
        from polaris.kernelone.llm.providers.stream_thinking_parser import (
            StreamThinkingParser,
        )

        visible_thinking_parser = StreamThinkingParser()
        full_content: list[str] = []
        thinking_content: list[str] = []
        native_tool_calls: list[dict[str, Any]] = []
        native_tool_provider = "auto"
        emitted_round_content = ""
        emitted_round_thinking = ""
        realtime_seen_tool_signatures: set[str] = set()

        async for event in stream_iterator:
            event_type = str(event.get("type") or "").strip()
            content = str(event.get("content") or "")

            if event_type == "reasoning_chunk":
                thinking_content.append(content)
                if content:
                    emitted_round_thinking += content
                    yield {"type": "thinking_chunk", "content": content, "iteration": round_index}
            elif event_type == "chunk":
                full_content.append(content)
                patch_visible, _ = self._patch_buffer.feed(content)
                output_visible = self._output_filter.feed(patch_visible)
                visible_chunk = self._visible_filter.feed(output_visible)
                if visible_chunk:
                    async for visible_kind, visible_text in visible_thinking_parser.feed(visible_chunk):
                        token = str(visible_text or "")
                        if not token:
                            continue
                        if visible_kind == ChunkKind.THINKING:
                            emitted_round_thinking += token
                            yield {"type": "thinking_chunk", "content": token, "iteration": round_index}
                        elif visible_kind == ChunkKind.TEXT:
                            if token.strip():
                                token = token.strip("\r\n")
                            if not token:
                                continue
                            emitted_round_content += token
                            yield {"type": "content_chunk", "content": token, "iteration": round_index}
            elif event_type == "tool_call":
                tool_name = str(event.get("tool") or "").strip()
                raw_args = event.get("args")
                tool_args = dict(raw_args) if isinstance(raw_args, dict) else {}
                call_id = str(event.get("call_id") or "").strip()
                raw_metadata = event.get("metadata")
                metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
                if tool_name:
                    normalized_call, normalized_provider = normalize_stream_tool_call_payload(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        call_id=call_id,
                        metadata=metadata,
                    )
                    if normalized_call is not None:
                        native_tool_calls.append(normalized_call)
                    if normalized_provider != "auto":
                        native_tool_provider = normalized_provider
                    signature = tool_call_signature(tool_name, tool_args)
                    if signature not in realtime_seen_tool_signatures:
                        realtime_seen_tool_signatures.add(signature)
                        yield {
                            "type": "tool_call",
                            "tool": tool_name,
                            "args": tool_args,
                            "call_id": call_id,
                            "iteration": round_index,
                        }
            elif event_type == "error":
                error_message = str(event.get("error") or event.get("message") or "unknown_error")
                yield {"type": "error", "error": error_message, "iteration": round_index}
                return
            elif event_type == "context_metadata":
                yield {
                    "type": "context_metadata",
                    "context_tokens": int(event.get("context_tokens", 0)),
                    "usage": event.get("usage"),
                    "iteration": round_index,
                }
            elif event_type == "complete":
                if content and not full_content:
                    full_content.append(content)

        # Flush trailing visible content
        output_trailing = self._output_filter.flush()
        if output_trailing:
            async for visible_kind, visible_text in visible_thinking_parser.feed(output_trailing):
                token = str(visible_text or "")
                if not token:
                    continue
                if visible_kind == ChunkKind.THINKING:
                    emitted_round_thinking += token
                    yield {"type": "thinking_chunk", "content": token, "iteration": round_index}
                elif visible_kind == ChunkKind.TEXT:
                    if token.strip():
                        token = token.strip("\r\n")
                    if not token:
                        continue
                    emitted_round_content += token
                    yield {"type": "content_chunk", "content": token, "iteration": round_index}

        trailing_visible = self._visible_filter.flush()
        if trailing_visible:
            async for visible_kind, visible_text in visible_thinking_parser.feed(trailing_visible):
                token = str(visible_text or "")
                if not token:
                    continue
                if visible_kind == ChunkKind.THINKING:
                    emitted_round_thinking += token
                    yield {"type": "thinking_chunk", "content": token, "iteration": round_index}
                elif visible_kind == ChunkKind.TEXT:
                    if token.strip():
                        token = token.strip("\r\n")
                    if not token:
                        continue
                    emitted_round_content += token
                    yield {"type": "content_chunk", "content": token, "iteration": round_index}

        async for visible_kind, visible_text in visible_thinking_parser.feed("", final=True):
            token = str(visible_text or "")
            if not token:
                continue
            if visible_kind == ChunkKind.THINKING:
                emitted_round_thinking += token
                yield {"type": "thinking_chunk", "content": token, "iteration": round_index}
            elif visible_kind == ChunkKind.TEXT:
                if token.strip():
                    token = token.strip("\r\n")
                if not token:
                    continue
                emitted_round_content += token
                yield {"type": "content_chunk", "content": token, "iteration": round_index}

        self._patch_buffer.flush()

        yield {
            "type": "_internal_materialize",
            "raw_output": "".join(full_content),
            "thinking_content": thinking_content,
            "native_tool_calls": native_tool_calls or None,
            "native_tool_provider": native_tool_provider,
            "emitted_round_content": emitted_round_content,
            "emitted_round_thinking": emitted_round_thinking,
        }

    @staticmethod
    def emit_deltas(
        final_content: str,
        final_thinking: str | None,
        emitted_content: str,
        emitted_thinking: str,
        *,
        round_index: int,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield delta events for any content/thinking not yet emitted.

        Args:
            final_content: Final sanitized content.
            final_thinking: Final merged thinking.
            emitted_content: Already-emitted content accumulator.
            emitted_thinking: Already-emitted thinking accumulator.
            round_index: Current turn round index.

        Yields:
            thinking_chunk or content_chunk delta events.
        """
        thinking_delta, _ = visible_delta(final_thinking, emitted_thinking)
        if thinking_delta:
            yield {"type": "thinking_chunk", "content": thinking_delta, "iteration": round_index}

        content_delta, _ = visible_delta(final_content, emitted_content)
        if content_delta:
            yield {"type": "content_chunk", "content": content_delta, "iteration": round_index}


__all__ = ["StreamEventHandler"]
