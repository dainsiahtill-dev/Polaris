from __future__ import annotations

import json
import re
from typing import Any


class LLMResponseParser:
    """Normalize provider responses into plain text/metadata for downstream callers."""

    _REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "analysis")
    _LENGTH_FINISH_REASONS = {"length", "max_tokens", "token_limit", "output_token_limit"}

    @classmethod
    def extract_text(cls, payload: Any) -> str:
        if isinstance(payload, str):
            return payload.strip()
        if not isinstance(payload, dict):
            return ""

        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        first_choice = cls._first_choice(payload)
        if isinstance(first_choice, dict):
            message = first_choice.get("message")
            text = cls._extract_message_content(message)
            if text:
                return text
            raw_text = first_choice.get("text")
            if isinstance(raw_text, str) and raw_text.strip():
                return raw_text.strip()

        message = payload.get("message")
        text = cls._extract_message_content(message)
        if text:
            return text

        content = payload.get("content")
        text = cls._stringify_content(content)
        if text:
            return text

        for key in ("text", "response", "output"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return ""

    @classmethod
    def extract_reasoning(cls, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        first_choice = cls._first_choice(payload)
        if isinstance(first_choice, dict):
            message = first_choice.get("message")
            reasoning = cls._extract_reasoning_from_message(message)
            if reasoning:
                return reasoning
            for key in cls._REASONING_KEYS:
                value = first_choice.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in cls._REASONING_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def extract_finish_reason(cls, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        first_choice = cls._first_choice(payload)
        if isinstance(first_choice, dict):
            value = first_choice.get("finish_reason") or first_choice.get("stop_reason")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        value = payload.get("finish_reason") or payload.get("stop_reason")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        return ""

    @classmethod
    def is_length_finish_reason(cls, reason: str) -> bool:
        return str(reason or "").strip().lower() in cls._LENGTH_FINISH_REASONS

    @classmethod
    def looks_truncated_json(cls, text: str) -> bool:
        body = str(text or "").strip()
        if not body or "{" not in body:
            return False
        if body.count("{") > body.count("}"):
            return True
        return body.endswith((",", ":", "[", "{", '"'))

    @classmethod
    def extract_json_object(cls, text: str) -> dict[str, Any] | None:
        body = str(text or "").strip()
        if not body:
            return None
        for candidate in cls._iter_json_candidates(body):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @classmethod
    def _iter_json_candidates(cls, text: str) -> list[str]:
        candidates: list[str] = []
        seen = set()

        def _append(value: str) -> None:
            candidate = str(value or "").strip()
            if not candidate or candidate in seen:
                return
            seen.add(candidate)
            candidates.append(candidate)

        _append(text)

        for match in re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE):
            _append(match.group(1))

        for fragment in cls._balanced_json_fragments(text):
            _append(fragment)

        return candidates

    @staticmethod
    def _first_choice(payload: dict[str, Any]) -> dict[str, Any] | None:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                return first
        return None

    @classmethod
    def _extract_message_content(cls, message: Any) -> str:
        if isinstance(message, dict):
            return cls._stringify_content(message.get("content"))
        return cls._stringify_content(message)

    @classmethod
    def _extract_reasoning_from_message(cls, message: Any) -> str:
        if isinstance(message, dict):
            for key in cls._REASONING_KEYS:
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @classmethod
    def _stringify_content(cls, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            items: list[str] = []
            for part in content:
                text = cls._extract_text_part(part)
                if text:
                    items.append(text)
            return "\n".join(items).strip()
        if isinstance(content, dict):
            for key in ("text", "content"):
                text = cls._stringify_content(content.get(key))
                if text:
                    return text
            if content.get("type") == "text":
                text_value = content.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    return text_value.strip()
        return ""

    @classmethod
    def _extract_text_part(cls, part: Any) -> str:
        if isinstance(part, str):
            return part.strip()
        if isinstance(part, dict):
            if part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
            for key in ("text", "content", "value"):
                value = part.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, (list, dict)):
                    nested = cls._stringify_content(value)
                    if nested:
                        return nested
        return ""

    @staticmethod
    def _balanced_json_fragments(text: str) -> list[str]:
        fragments: list[str] = []
        start = -1
        depth = 0
        in_string = False
        escape = False

        for idx, ch in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue

            if ch == "{":
                if depth == 0:
                    start = idx
                depth += 1
                continue

            if ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    fragments.append(text[start : idx + 1])
                    start = -1

        fragments.sort(key=len, reverse=True)
        return fragments
