from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polaris.kernelone.utils.json_utils import parse_json_payload


@dataclass(frozen=True)
class FinalizedResponse:
    """Outcome of the canonical reasoning-aware response finalization (DEFECT 2).

    ``output`` is the visible answer (possibly recovered from the reasoning
    channel); ``thinking`` carries the reasoning blob when it was recovered or
    when the turn failed mid-reasoning (for downstream salvage); ``ok`` is False
    only when the model exhausted the budget inside its reasoning channel and
    left no complete answer (fail-closed, with a descriptive ``error``).
    """

    output: str
    thinking: str | None
    ok: bool
    error: str | None


class LLMResponseParser:
    """Normalize provider responses into plain text/metadata for downstream callers."""

    _REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "analysis")
    _LENGTH_FINISH_REASONS = {
        "length",
        "max_tokens",
        "max_output_tokens",
        "model_context_window_exceeded",
        "token_limit",
        "output_token_limit",
    }

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

        output = payload.get("output")
        text = cls._stringify_content(output)
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
            if isinstance(value, (list, dict)):
                text = cls._stringify_content(value)
                if text:
                    return text
        output = payload.get("output")
        reasoning = cls._extract_reasoning_from_output(output)
        if reasoning:
            return reasoning
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
        incomplete = payload.get("incomplete_details")
        if isinstance(incomplete, dict):
            reason = incomplete.get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip().lower()
        return ""

    @classmethod
    def is_length_finish_reason(cls, reason: str) -> bool:
        return str(reason or "").strip().lower() in cls._LENGTH_FINISH_REASONS

    @classmethod
    def finalize_response(cls, payload: Any, *, visible_text: str | None = None) -> FinalizedResponse:
        """THE single reasoning-aware finalization (DEFECT 2 SSoT).

        Every provider/path routes empty-vs-reasoning handling through here so a
        reasoning model that returns ``content:null`` with the answer in its
        reasoning channel is never silently dropped as empty. The branch table is
        defined exactly once:

        - visible content present  -> ok, return it (reasoning NOT surfaced — a
          chain-of-thought leak guard: recovery is strictly gated on EMPTY output);
        - empty + reasoning + finish_reason==length -> fail-closed (the answer was
          truncated mid-reasoning); carry ``thinking`` for downstream salvage;
        - empty + reasoning (not length) -> recover the reasoning as the answer;
        - empty + no reasoning -> empty (ok, the caller decides what empty means).

        ``visible_text`` lets a provider pass its own content extraction (some
        providers extract content differently than :meth:`extract_text`); the
        reasoning/finish_reason channels always come from ``payload``.
        """
        visible = (cls.extract_text(payload) if visible_text is None else str(visible_text)).strip()
        if visible:
            return FinalizedResponse(output=visible, thinking=None, ok=True, error=None)

        reasoning = cls.extract_reasoning(payload)
        if reasoning:
            finish_reason = cls.extract_finish_reason(payload)
            if cls.is_length_finish_reason(finish_reason):
                return FinalizedResponse(
                    output="",
                    thinking=reasoning,
                    ok=False,
                    error=(
                        "Empty visible output (reasoning truncated, "
                        f"finish_reason={finish_reason or 'unknown'}, reasoning_chars={len(reasoning)})"
                    ),
                )
            return FinalizedResponse(output=reasoning, thinking=reasoning, ok=True, error=None)

        return FinalizedResponse(output="", thinking=None, ok=True, error=None)

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
        return parse_json_payload(text)

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
                if isinstance(value, (list, dict)):
                    text = cls._stringify_content(value)
                    if text:
                        return text
        return ""

    @classmethod
    def _extract_reasoning_from_output(cls, output: Any) -> str:
        if isinstance(output, list):
            items: list[str] = []
            for item in output:
                text = cls._extract_reasoning_from_output(item)
                if text:
                    items.append(text)
            return "\n".join(items).strip()
        if isinstance(output, dict):
            item_type = str(output.get("type") or "").strip().lower()
            if "reasoning" in item_type or "thinking" in item_type:
                for key in ("summary", "text", "content"):
                    value = output.get(key)
                    text = cls._stringify_content(value)
                    if text:
                        return text
            for key in cls._REASONING_KEYS:
                value = output.get(key)
                text = cls._stringify_content(value)
                if text:
                    return text
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
