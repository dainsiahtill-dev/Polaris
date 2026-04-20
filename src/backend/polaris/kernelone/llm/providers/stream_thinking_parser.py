"""Stream thinking parser for LLM streaming responses.

This module provides a state machine parser for streaming LLM responses that may contain:
- Thinking tags: <thinking>...</thinking>
- XML format tool calls: <tool_call>...</tool_call>
- JSON format tool calls: {"name": "...", "arguments": {...}}
- Text content
- Tool results

The parser handles streaming by buffering content and emitting chunks
when complete units are identified.

Example:
    >>> parser = StreamThinkingParser()
    >>> async for chunk in parser.feed("<thinking>Let me", True):
    ...     print(chunk)
    >>> async for chunk in parser.feed(" think</thinking>", True):
    ...     print(chunk)
"""

from __future__ import annotations

import logging
import re
from enum import Enum, auto
from typing import TYPE_CHECKING

from polaris.kernelone.llm.toolkit.parsers.json_based import (
    is_json_tool_call,
    parse_json_tool_calls,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from polaris.kernelone.llm.toolkit.parsers.utils import ParsedToolCall

logger = logging.getLogger(__name__)


class ChunkKind(Enum):
    """Kind of chunk emitted by the parser."""

    TEXT = auto()
    THINKING = auto()
    TOOL_CALL_START = auto()
    TOOL_CALL_CONTENT = auto()
    TOOL_CALL_END = auto()
    TOOL_RESULT = auto()
    ERROR = auto()
    DONE = auto()


# Type alias for chunk tuples
Chunk = tuple[ChunkKind, str]


# XML tag patterns (supports both <think> and <thinking>)
THINKING_OPEN_RE = re.compile(r"<think(?:ing)?(?:[^>]*)>", re.IGNORECASE)
THINKING_CLOSE_RE = re.compile(r"</think(?:ing)?>", re.IGNORECASE)

# Tool call XML patterns
TOOL_CALL_OPEN_RE = re.compile(r"<tool_call(?:[^>]*)>", re.IGNORECASE)
TOOL_CALL_CLOSE_RE = re.compile(r"</tool_call>", re.IGNORECASE)

# Nested XML tag patterns for invoke/function
INVOKE_OPEN_RE = re.compile(r"<invoke(?:[^>]*)>", re.IGNORECASE)
INVOKE_CLOSE_RE = re.compile(r"</invoke>", re.IGNORECASE)
NAME_OPEN_RE = re.compile(r"<name(?:[^>]*)>", re.IGNORECASE)
NAME_CLOSE_RE = re.compile(r"</name>", re.IGNORECASE)
ARGS_OPEN_RE = re.compile(r"<args(?:[^>]*)>", re.IGNORECASE)
ARGS_CLOSE_RE = re.compile(r"</args>", re.IGNORECASE)

# Tool result patterns
TOOL_RESULT_OPEN_RE = re.compile(r"<tool_result(?:[^>]*)>", re.IGNORECASE)
TOOL_RESULT_CLOSE_RE = re.compile(r"</tool_result>", re.IGNORECASE)


class StreamThinkingParser:
    """State machine parser for streaming LLM responses.

    Handles streaming of thinking tags, XML tool calls, JSON tool calls,
    tool results, and text content.

    Attributes:
        THRESHOLD: Minimum length to consider text as real content.
        MAX_PENDING: Maximum pending text size before forcing flush.
    """

    THRESHOLD = 5
    MAX_PENDING = 2000

    def __init__(
        self,
        *,
        allowed_tool_names: list[str] | None = None,
    ) -> None:
        """Initialize the parser.

        Args:
            allowed_tool_names: Optional whitelist of tool names to parse.
        """
        self._state = "content"  # content | thinking | tool_xml | tool_json | tool_result
        self._allowed_names: set[str] | None = set(allowed_tool_names) if allowed_tool_names else None

        # Tool XML state
        self._tool_buffer: list[str] = []
        self._in_invoke = False
        self._in_name = False
        self._in_args = False

        # Tool JSON state - tracks incomplete JSON objects
        self._json_buffer: str = ""
        self._json_brace_depth = 0
        self._json_started = False

        # Pending text buffer
        self._pending_text: str = ""
        self._pending_thinking: str = ""

        # Streaming chunks accumulator
        self._chunks: list[Chunk] = []

        # Tool calls extracted from JSON
        self._extracted_json_calls: list[ParsedToolCall] = []

    @property
    def chunks(self) -> list[Chunk]:
        """Return accumulated chunks."""
        return self._chunks

    def reset(self) -> None:
        """Reset parser state to initial."""
        self._state = "content"
        self._tool_buffer.clear()
        self._in_invoke = False
        self._in_name = False
        self._in_args = False
        self._json_buffer = ""
        self._json_brace_depth = 0
        self._json_started = False
        self._pending_text = ""
        self._pending_thinking = ""
        self._chunks.clear()
        self._extracted_json_calls.clear()

    async def feed(
        self,
        text: str,
        final: bool = False,
    ) -> AsyncIterator[Chunk]:
        """Feed text to the parser.

        Args:
            text: Text chunk to process.
            final: True if this is the final chunk.

        Yields:
            Chunk tuples of (kind, content).
        """
        self._chunks.clear()

        if not text:
            if final:
                await self._finalize()
                for chunk in self._chunks:
                    yield chunk
            return

        # Process based on current state
        if self._state == "content":
            await self._process_content_state(text)
        elif self._state == "thinking":
            await self._process_thinking_state(text)
        elif self._state == "tool_xml":
            await self._process_tool_state(text)
        elif self._state == "tool_json":
            await self._process_json_tool_state(text)
        elif self._state == "tool_result":
            await self._process_tool_result_state(text)

        if final:
            await self._finalize()

        for chunk in self._chunks:
            yield chunk

    def _add_chunk(self, kind: ChunkKind, content: str) -> None:
        """Add a chunk to the accumulator."""
        if content or kind in (ChunkKind.TOOL_CALL_START, ChunkKind.TOOL_CALL_END):
            self._chunks.append((kind, content))

    async def _process_content_state(self, text: str) -> None:
        """Process text in content state.

        Detects and transitions to thinking, XML tool call, JSON tool call,
        or tool result states.

        Args:
            text: Text to process.
        """
        # First, flush any pending text
        if self._pending_text:
            self._add_chunk(ChunkKind.TEXT, self._pending_text)
            self._pending_text = ""

        # Process the new text
        content = text

        # Check for JSON tool call at the start
        if content.startswith("{"):
            # Try to detect and extract complete JSON tool call
            json_result = self._try_extract_json_tool_call(content)
            if json_result is not None:
                complete_json, remainder = json_result
                # Emit the JSON tool call
                self._emit_json_tool_call(complete_json)
                # Process remainder recursively
                if remainder:
                    await self._process_content_state(remainder)
                return
            else:
                # Incomplete JSON starting with { - buffer it in JSON state
                self._json_buffer = content
                self._json_brace_depth = self._count_brace_depth(content)
                self._json_started = True
                self._state = "tool_json"
                return

        # Check for thinking start
        thinking_match = THINKING_OPEN_RE.search(content)
        if thinking_match:
            # Emit pending text before thinking
            prefix = content[: thinking_match.start()]
            if prefix:
                self._pending_text = prefix
            # Start thinking
            self._pending_thinking = content[thinking_match.end() :]
            self._state = "thinking"
            # If closing tag already present in the same chunk, process immediately
            if THINKING_CLOSE_RE.search(self._pending_thinking):
                await self._process_thinking_state("")
            return

        # Check for XML tool call start
        tool_match = TOOL_CALL_OPEN_RE.search(content)
        if tool_match:
            # Flush pending text first
            prefix = content[: tool_match.start()]
            if prefix:
                self._pending_text = prefix
            # Start tool call
            self._tool_buffer.append(content[tool_match.start() : tool_match.end()])
            self._state = "tool_xml"
            remainder = content[tool_match.end() :]
            if remainder:
                await self._process_tool_state(remainder)
            return

        # Check for tool result start
        result_match = TOOL_RESULT_OPEN_RE.search(content)
        if result_match:
            # Flush pending text first
            prefix = content[: result_match.start()]
            if prefix:
                self._pending_text = prefix
            self._pending_thinking = content[result_match.end() :]
            self._state = "tool_result"
            # If closing tag already present in the same chunk, process immediately
            if TOOL_RESULT_CLOSE_RE.search(self._pending_thinking):
                await self._process_tool_result_state("")
            return

        # No special tags found, accumulate text
        self._pending_text = content

        # Force flush if too long
        if len(self._pending_text) > self.MAX_PENDING:
            self._add_chunk(ChunkKind.TEXT, self._pending_text)
            self._pending_text = ""

    def _try_extract_json_tool_call(self, content: str) -> tuple[str, str] | None:
        """Try to extract a complete JSON tool call from content.

        Uses brace counting to handle nested objects properly.

        Args:
            content: Text starting with '{'.

        Returns:
            Tuple of (complete_json, remainder) if found, None otherwise.
        """
        if not content.startswith("{"):
            return None

        # Quick validation: does it look like a tool call?
        if not is_json_tool_call(content):
            return None

        # Count braces to find complete JSON object
        brace_depth = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(content):
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if char == "{":
                if brace_depth == 0:
                    pass
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    # Found complete JSON object
                    json_str = content[: i + 1]
                    remainder = content[i + 1 :]
                    return json_str, remainder

        # Incomplete JSON - start tracking
        return None

    def _count_brace_depth(self, content: str) -> int:
        """Count the brace depth of JSON content.

        Args:
            content: JSON text to analyze.

        Returns:
            Net brace depth (positive means more opens than closes).
        """
        brace_depth = 0
        in_string = False
        escape_next = False

        for char in content:
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1

        return brace_depth

    async def _process_thinking_state(self, text: str) -> None:
        """Process text in thinking state.

        Args:
            text: Text to process.
        """
        self._pending_thinking += text

        # Check for thinking end
        close_match = THINKING_CLOSE_RE.search(self._pending_thinking)
        if close_match:
            # Extract thinking content (without the closing tag)
            thinking = self._pending_thinking[: close_match.start()]
            # Process remainder before clearing buffer
            remainder = self._pending_thinking[close_match.end() :]
            self._add_chunk(ChunkKind.THINKING, thinking)
            self._pending_thinking = ""
            self._state = "content"

            if remainder:
                await self._process_content_state(remainder)

    async def _process_tool_state(self, text: str) -> None:
        """Process text in XML tool call state.

        Handles nested tags for <invoke>, <name>, <args>.

        Args:
            text: Text to process.
        """
        self._tool_buffer.append(text)

        buffer = "".join(self._tool_buffer)

        # Track nested tag states
        if INVOKE_OPEN_RE.search(buffer):
            self._in_invoke = True
        if INVOKE_CLOSE_RE.search(buffer):
            self._in_invoke = False

        if NAME_OPEN_RE.search(buffer):
            self._in_name = True
        if NAME_CLOSE_RE.search(buffer):
            self._in_name = False

        if ARGS_OPEN_RE.search(buffer):
            self._in_args = True
        if ARGS_CLOSE_RE.search(buffer):
            self._in_args = False

        # Check for tool call end
        close_match = TOOL_CALL_CLOSE_RE.search(buffer)
        if close_match:
            # Emit the complete tool call
            self._add_chunk(ChunkKind.TOOL_CALL_START, "")
            self._add_chunk(
                ChunkKind.TOOL_CALL_CONTENT,
                buffer[: close_match.end()],
            )
            self._add_chunk(ChunkKind.TOOL_CALL_END, "")
            self._tool_buffer.clear()
            self._state = "content"

            # Process remainder
            remainder = buffer[close_match.end() :]
            if remainder:
                await self._process_content_state(remainder)

    async def _process_json_tool_state(self, text: str) -> None:
        """Process text in JSON tool call state.

        Handles streaming of JSON tool call objects by tracking
        brace depth to find complete JSON objects.

        Args:
            text: Text to process.
        """
        self._json_buffer += text

        # Count braces to find complete JSON object
        in_string = False
        escape_next = False
        brace_depth = self._json_brace_depth

        for char in self._json_buffer[len(self._json_buffer) - len(text) :]:
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1

        self._json_brace_depth = brace_depth

        # Check if we have a complete JSON object
        if brace_depth == 0 and self._json_buffer.startswith("{"):
            # Validate it looks like a tool call before emitting
            if is_json_tool_call(self._json_buffer):
                self._emit_json_tool_call(self._json_buffer)
            else:
                # Not a valid tool call, emit as text
                self._add_chunk(ChunkKind.TEXT, self._json_buffer)
            self._json_buffer = ""
            self._json_brace_depth = 0
            self._json_started = False
            self._state = "content"

    async def _process_tool_result_state(self, text: str) -> None:
        """Process text in tool result state.

        Args:
            text: Text to process.
        """
        self._pending_thinking += text

        # Check for tool result end
        close_match = TOOL_RESULT_CLOSE_RE.search(self._pending_thinking)
        if close_match:
            # Extract tool result content
            result = self._pending_thinking[: close_match.start()]
            # Process remainder before clearing buffer
            remainder = self._pending_thinking[close_match.end() :]
            self._add_chunk(ChunkKind.TOOL_RESULT, result)
            self._pending_thinking = ""
            self._state = "content"
            if remainder:
                await self._process_content_state(remainder)

    def _emit_json_tool_call(self, json_str: str) -> None:
        """Parse and emit a JSON tool call.

        Args:
            json_str: Complete JSON tool call string.
        """
        # Parse the JSON tool call
        calls = parse_json_tool_calls(
            json_str,
            allowed_tool_names=self._allowed_names,
        )

        for call in calls:
            # Emit tool call start
            self._add_chunk(ChunkKind.TOOL_CALL_START, "")

            # Emit tool call content as JSON
            self._add_chunk(ChunkKind.TOOL_CALL_CONTENT, json_str)

            # Emit tool call end
            self._add_chunk(ChunkKind.TOOL_CALL_END, "")

            # Store for later retrieval
            self._extracted_json_calls.append(call)

    async def _finalize(self) -> None:
        """Finalize parsing, flush any pending content."""
        # Flush pending thinking
        if self._pending_thinking:
            self._add_chunk(ChunkKind.THINKING, self._pending_thinking)
            self._pending_thinking = ""

        # Flush pending text
        if self._pending_text:
            self._add_chunk(ChunkKind.TEXT, self._pending_text)
            self._pending_text = ""

        # Handle incomplete JSON tool call at end
        if self._state == "tool_json" and self._json_buffer:
            # Validate before emitting
            if is_json_tool_call(self._json_buffer):
                self._emit_json_tool_call(self._json_buffer)
            else:
                # Not a valid tool call, emit as text
                self._add_chunk(ChunkKind.TEXT, self._json_buffer)
            self._json_buffer = ""
            self._json_brace_depth = 0

        # Handle unclosed XML tool call
        if self._state == "tool_xml" and self._tool_buffer:
            buffer = "".join(self._tool_buffer)
            # Try to extract what we can
            self._add_chunk(ChunkKind.TOOL_CALL_START, "")
            self._add_chunk(ChunkKind.TOOL_CALL_CONTENT, buffer)
            self._tool_buffer.clear()

        # Emit done
        self._add_chunk(ChunkKind.DONE, "")
        self._state = "content"

    def get_json_tool_calls(self) -> list[ParsedToolCall]:
        """Get all JSON tool calls parsed from the stream.

        Returns:
            List of parsed JSON tool calls.
        """
        return self._extracted_json_calls.copy()

    def flush(self) -> list[Chunk]:
        """Flush any remaining buffered content synchronously.

        This method is provided for compatibility with legacy code that
        expects a synchronous flush method. It returns the remaining
        buffered content without requiring async iteration.

        Returns:
            List of chunks from remaining buffered content.
        """
        chunks: list[Chunk] = []

        # Flush pending thinking
        if self._pending_thinking:
            chunks.append((ChunkKind.THINKING, self._pending_thinking))
            self._pending_thinking = ""

        # Flush pending text
        if self._pending_text:
            chunks.append((ChunkKind.TEXT, self._pending_text))
            self._pending_text = ""

        # Handle incomplete JSON tool call at end
        if self._state == "tool_json" and self._json_buffer:
            if is_json_tool_call(self._json_buffer):
                # Emit as tool call
                chunks.append((ChunkKind.TOOL_CALL_START, ""))
                chunks.append((ChunkKind.TOOL_CALL_CONTENT, self._json_buffer))
                chunks.append((ChunkKind.TOOL_CALL_END, ""))
            else:
                chunks.append((ChunkKind.TEXT, self._json_buffer))
            self._json_buffer = ""
            self._json_brace_depth = 0

        # Handle unclosed XML tool call
        if self._state == "tool_xml" and self._tool_buffer:
            buffer = "".join(self._tool_buffer)
            chunks.append((ChunkKind.TOOL_CALL_START, ""))
            chunks.append((ChunkKind.TOOL_CALL_CONTENT, buffer))
            self._tool_buffer.clear()

        # Emit done
        chunks.append((ChunkKind.DONE, ""))
        self._state = "content"

        return chunks

    def feed_sync(self, text: str, final: bool = False) -> list[Chunk]:
        """Feed text to the parser synchronously.

        This method is provided for compatibility with legacy code that
        expects synchronous iteration. It processes the text and returns
        the chunks immediately.

        Args:
            text: Text chunk to process.
            final: True if this is the final chunk.

        Returns:
            List of chunks from processing the text.
        """
        self._chunks.clear()

        if not text:
            if final:
                return self.flush()
            return []

        # Process based on current state (simplified synchronous version)
        if self._state == "content":
            self._process_content_state_sync(text)
        elif self._state == "thinking":
            self._process_thinking_state_sync(text)
        elif self._state == "tool_xml":
            self._process_tool_state_sync(text)
        elif self._state == "tool_json":
            self._process_json_tool_state_sync(text)
        elif self._state == "tool_result":
            self._process_tool_result_state_sync(text)

        if final:
            flushed = self.flush()
            # Remove internal DONE chunk from final output
            if flushed and flushed[-1][0] == ChunkKind.DONE:
                flushed = flushed[:-1]
            return self._chunks.copy() + flushed

        return self._chunks.copy()

    def _process_content_state_sync(self, text: str) -> None:
        """Synchronous version of content state processing."""
        if self._pending_text:
            self._add_chunk(ChunkKind.TEXT, self._pending_text)
            self._pending_text = ""

        content = text

        # Check for JSON tool call at the start
        if content.startswith("{"):
            json_result = self._try_extract_json_tool_call(content)
            if json_result is not None:
                complete_json, remainder = json_result
                self._emit_json_tool_call(complete_json)
                if remainder:
                    self._process_content_state_sync(remainder)
                return
            else:
                self._json_buffer = content
                self._json_brace_depth = self._count_brace_depth(content)
                self._json_started = True
                self._state = "tool_json"
                return

        # Check for thinking start
        thinking_match = THINKING_OPEN_RE.search(content)
        if thinking_match:
            prefix = content[: thinking_match.start()]
            if prefix:
                self._pending_text = prefix
            self._pending_thinking = content[thinking_match.end() :]
            self._state = "thinking"
            if THINKING_CLOSE_RE.search(self._pending_thinking):
                self._process_thinking_state_sync("")
            return

        # Check for XML tool call start
        tool_match = TOOL_CALL_OPEN_RE.search(content)
        if tool_match:
            prefix = content[: tool_match.start()]
            if prefix:
                self._pending_text = prefix
            self._tool_buffer.append(content[tool_match.start() : tool_match.end()])
            self._state = "tool_xml"
            remainder = content[tool_match.end() :]
            if remainder:
                self._process_tool_state_sync(remainder)
            return

        # Check for tool result start
        result_match = TOOL_RESULT_OPEN_RE.search(content)
        if result_match:
            prefix = content[: result_match.start()]
            if prefix:
                self._pending_text = prefix
            self._pending_thinking = content[result_match.end() :]
            self._state = "tool_result"
            if TOOL_RESULT_CLOSE_RE.search(self._pending_thinking):
                self._process_tool_result_state_sync("")
            return

        # Check for XML tool call start
        tool_match = TOOL_CALL_OPEN_RE.search(content)
        if tool_match:
            prefix = content[: tool_match.start()]
            if prefix:
                self._pending_text = prefix
            self._tool_buffer.append(content[tool_match.start() : tool_match.end()])
            self._state = "tool_xml"
            remainder = content[tool_match.end() :]
            if remainder:
                self._process_tool_state_sync(remainder)
            return

        # Check for tool result start
        result_match = TOOL_RESULT_OPEN_RE.search(content)
        if result_match:
            prefix = content[: result_match.start()]
            if prefix:
                self._pending_text = prefix
            self._pending_thinking = content[result_match.start() : result_match.end()]
            self._state = "tool_result"
            remainder = content[result_match.end() :]
            if remainder:
                self._process_tool_result_state_sync(remainder)
            return

        # No special tags found, accumulate text
        self._pending_text = content

        if len(self._pending_text) > self.MAX_PENDING:
            self._add_chunk(ChunkKind.TEXT, self._pending_text)
            self._pending_text = ""

    def _process_thinking_state_sync(self, text: str) -> None:
        """Synchronous version of thinking state processing."""
        self._pending_thinking += text

        close_match = THINKING_CLOSE_RE.search(self._pending_thinking)
        if close_match:
            thinking = self._pending_thinking[: close_match.start()]
            remainder = self._pending_thinking[close_match.end() :]
            self._add_chunk(ChunkKind.THINKING, thinking)
            self._pending_thinking = ""
            self._state = "content"
            if remainder:
                self._process_content_state_sync(remainder)

    def _process_tool_state_sync(self, text: str) -> None:
        """Synchronous version of tool state processing."""
        self._tool_buffer.append(text)

        buffer = "".join(self._tool_buffer)

        if INVOKE_OPEN_RE.search(buffer):
            self._in_invoke = True
        if INVOKE_CLOSE_RE.search(buffer):
            self._in_invoke = False

        if NAME_OPEN_RE.search(buffer):
            self._in_name = True
        if NAME_CLOSE_RE.search(buffer):
            self._in_name = False

        if ARGS_OPEN_RE.search(buffer):
            self._in_args = True
        if ARGS_CLOSE_RE.search(buffer):
            self._in_args = False

        close_match = TOOL_CALL_CLOSE_RE.search(buffer)
        if close_match:
            self._add_chunk(ChunkKind.TOOL_CALL_START, "")
            self._add_chunk(ChunkKind.TOOL_CALL_CONTENT, buffer[: close_match.end()])
            self._add_chunk(ChunkKind.TOOL_CALL_END, "")
            self._tool_buffer.clear()
            self._state = "content"
            remainder = buffer[close_match.end() :]
            if remainder:
                self._process_content_state_sync(remainder)

    def _process_json_tool_state_sync(self, text: str) -> None:
        """Synchronous version of JSON tool state processing."""
        self._json_buffer += text

        in_string = False
        escape_next = False
        brace_depth = self._json_brace_depth

        for char in self._json_buffer[len(self._json_buffer) - len(text) :]:
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1

        self._json_brace_depth = brace_depth

        if brace_depth == 0 and self._json_buffer.startswith("{"):
            if is_json_tool_call(self._json_buffer):
                self._emit_json_tool_call(self._json_buffer)
            else:
                self._add_chunk(ChunkKind.TEXT, self._json_buffer)
            self._json_buffer = ""
            self._json_brace_depth = 0
            self._json_started = False
            self._state = "content"

    def _process_tool_result_state_sync(self, text: str) -> None:
        """Synchronous version of tool result state processing."""
        self._pending_thinking += text

        close_match = TOOL_RESULT_CLOSE_RE.search(self._pending_thinking)
        if close_match:
            result = self._pending_thinking[: close_match.start()]
            remainder = self._pending_thinking[close_match.end() :]
            self._add_chunk(ChunkKind.TOOL_RESULT, result)
            self._pending_thinking = ""
            self._state = "content"
            if remainder:
                self._process_content_state_sync(remainder)


__all__ = [
    "Chunk",
    "ChunkKind",
    "StreamThinkingParser",
]
