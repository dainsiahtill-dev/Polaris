"""Streaming tag parser for real-time <thinking> and <answer> tag parsing

This module provides a StreamingTagParser class that handles token-level
tag detection and parsing for streaming output, enabling structured
real-time display of thinking and answer content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.utils import utc_now_iso


class TagState(Enum):
    IDLE = "idle"
    IN_THINKING = "in_thinking"
    IN_ANSWER = "in_answer"
    IN_FINAL = "in_final"


THINKING_TAGS = ["thinking", "reasoning", "analysis", "think"]
ANSWER_TAGS = ["answer", "final", "response"]


@dataclass
class TagEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}


class StreamingTagParser:
    def __init__(self, flush_threshold: int = 20) -> None:
        self.flush_threshold = flush_threshold
        self.buffer = ""
        self.current_state = TagState.IDLE
        self.content_buffer = ""
        self.is_tag_mode = False
        self._tag_patterns = self._compile_patterns()

    def _compile_patterns(self) -> dict[str, re.Pattern]:
        return {
            "thinking_open": re.compile(rf"<({'|'.join(THINKING_TAGS)})[^>]*>", re.IGNORECASE),
            "thinking_close": re.compile(rf"</({'|'.join(THINKING_TAGS)})>", re.IGNORECASE),
            "answer_open": re.compile(rf"<({'|'.join(ANSWER_TAGS)})[^>]*>", re.IGNORECASE),
            "answer_close": re.compile(rf"</({'|'.join(ANSWER_TAGS)})>", re.IGNORECASE),
        }

    def detect_tag_mode(self, text: str) -> bool:
        if not text:
            return False
        thinking_match = self._tag_patterns["thinking_open"].search(text)
        answer_match = self._tag_patterns["answer_open"].search(text)
        return bool(thinking_match or answer_match)

    def process_chunk(self, chunk: str) -> list[TagEvent]:
        events: list[TagEvent] = []
        self.buffer += chunk

        while self.buffer:
            cycle_events: list[TagEvent] = []
            if self.current_state == TagState.IDLE:
                cycle_events = self._handle_idle_state()
            elif self.current_state == TagState.IN_THINKING:
                cycle_events = self._handle_thinking_state()
            elif self.current_state == TagState.IN_ANSWER:
                cycle_events = self._handle_answer_state()
            elif self.current_state == TagState.IN_FINAL:
                cycle_events = self._handle_final_state()

            events.extend(cycle_events)
            if not cycle_events:
                break

        return events

    def _handle_idle_state(self) -> list[TagEvent]:
        events: list[TagEvent] = []
        timestamp = self._utc_now()

        thinking_open = self._tag_patterns["thinking_open"].search(self.buffer)
        answer_open = self._tag_patterns["answer_open"].search(self.buffer)

        if thinking_open and (not answer_open or thinking_open.start() < answer_open.start()):
            self.current_state = TagState.IN_THINKING
            self.buffer = self.buffer[: thinking_open.start()] + self.buffer[thinking_open.end() :]
            events.append(TagEvent(type="thinking_start", data={"timestamp": timestamp}))
        elif answer_open:
            self.current_state = TagState.IN_ANSWER
            self.buffer = self.buffer[: answer_open.start()] + self.buffer[answer_open.end() :]
            events.append(TagEvent(type="answer_start", data={"timestamp": timestamp}))
        else:
            return events

        return events

    def _handle_thinking_state(self) -> list[TagEvent]:
        events: list[TagEvent] = []
        timestamp = self._utc_now()

        thinking_close = self._tag_patterns["thinking_close"].search(self.buffer)

        if thinking_close:
            content = self.buffer[: thinking_close.start()]
            self.buffer = self.buffer[thinking_close.end() :]

            if content:
                events.append(
                    TagEvent(
                        type="thinking_chunk",
                        data={"content": content, "is_complete": True},
                    )
                )

            events.append(TagEvent(type="thinking_end", data={"timestamp": timestamp}))
            self._reset_thinking_state()
        else:
            partial_content = self._drain_safe_content()
            if len(partial_content) >= self.flush_threshold:
                events.append(
                    TagEvent(
                        type="thinking_chunk",
                        data={"content": partial_content, "is_complete": False},
                    )
                )

        return events

    def _handle_answer_state(self) -> list[TagEvent]:
        events: list[TagEvent] = []
        timestamp = self._utc_now()

        answer_close = self._tag_patterns["answer_close"].search(self.buffer)

        if answer_close:
            content = self.buffer[: answer_close.start()]
            self.buffer = self.buffer[answer_close.end() :]

            if content:
                events.append(
                    TagEvent(
                        type="answer_chunk",
                        data={"content": content, "is_complete": True},
                    )
                )

            events.append(TagEvent(type="answer_end", data={"timestamp": timestamp}))
            self._reset_answer_state()
        else:
            partial_content = self._drain_safe_content()
            if len(partial_content) >= self.flush_threshold:
                events.append(
                    TagEvent(
                        type="answer_chunk",
                        data={"content": partial_content, "is_complete": False},
                    )
                )

        return events

    def _handle_final_state(self) -> list[TagEvent]:
        events: list[TagEvent] = []
        timestamp = self._utc_now()

        final_close = self._tag_patterns["answer_close"].search(self.buffer)

        if final_close:
            content = self.buffer[: final_close.start()]
            self.buffer = self.buffer[final_close.end() :]

            if content:
                events.append(
                    TagEvent(
                        type="answer_chunk",
                        data={"content": content, "is_complete": True},
                    )
                )

            events.append(TagEvent(type="answer_end", data={"timestamp": timestamp}))
            self._reset_answer_state()
        else:
            partial_content = self._drain_safe_content()
            if len(partial_content) >= self.flush_threshold:
                events.append(
                    TagEvent(
                        type="answer_chunk",
                        data={"content": partial_content, "is_complete": False},
                    )
                )

        return events

    def _reset_thinking_state(self) -> None:
        self.content_buffer = ""
        self.current_state = TagState.IDLE

    def _reset_answer_state(self) -> None:
        self.content_buffer = ""
        self.current_state = TagState.IDLE

    def _drain_safe_content(self) -> str:
        """Flush buffered plain content while preserving possible partial XML tags.

        If the buffer ends with an unfinished `<...` fragment, keep it for the next
        chunk so that close/open tags split across chunks are not emitted as content.
        """
        if not self.buffer:
            return ""

        drain_until = len(self.buffer)
        last_lt = self.buffer.rfind("<")
        if last_lt == -1:
            drain_until = len(self.buffer)
        else:
            trailing = self.buffer[last_lt:]
            if ">" not in trailing:
                drain_until = last_lt

        if drain_until < self.flush_threshold:
            return ""

        content = self.buffer[:drain_until]
        self.buffer = self.buffer[drain_until:]
        return content

    def flush(self) -> list[TagEvent]:
        events: list[TagEvent] = []

        if self.current_state == TagState.IN_THINKING and self.buffer:
            events.append(
                TagEvent(
                    type="thinking_chunk",
                    data={"content": self.buffer, "is_complete": False},
                )
            )
            events.append(TagEvent(type="thinking_end", data={"timestamp": self._utc_now()}))
        elif self.current_state == TagState.IN_ANSWER and self.buffer:
            events.append(
                TagEvent(
                    type="answer_chunk",
                    data={"content": self.buffer, "is_complete": False},
                )
            )
            events.append(TagEvent(type="answer_end", data={"timestamp": self._utc_now()}))

        self.reset()
        return events

    def reset(self) -> None:
        self.buffer = ""
        self.current_state = TagState.IDLE
        self.content_buffer = ""
        self.is_tag_mode = False

    @staticmethod
    def _utc_now() -> str:
        return utc_now_iso()


def create_tag_parser(flush_threshold: int = 20) -> StreamingTagParser:
    return StreamingTagParser(flush_threshold=flush_threshold)
