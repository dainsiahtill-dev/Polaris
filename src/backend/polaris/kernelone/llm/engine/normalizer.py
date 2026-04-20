"""Polaris AI Platform - Response Normalizer

统一解析器：JSON 提取、标准化、截断修复。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .contracts import AIResponse, ErrorCategory, Usage


class ResponseNormalizer:
    """响应标准化器

    提供统一的 JSON 提取、结构标准化、截断修复能力。
    """

    _REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "analysis")
    _LENGTH_FINISH_REASONS = {"length", "max_tokens", "token_limit", "output_token_limit"}

    @classmethod
    def extract_text(cls, payload: Any) -> str:
        """从各种响应格式中提取文本"""
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
        """从响应中提取 reasoning/thinking 内容"""
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
        """提取 finish reason"""
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
        """判断是否为长度截断导致的 finish"""
        return str(reason or "").strip().lower() in cls._LENGTH_FINISH_REASONS

    @classmethod
    def looks_truncated_json(cls, text: str) -> bool:
        """检测 JSON 是否被截断"""
        body = str(text or "").strip()
        if not body or "{" not in body:
            return False
        if body.count("{") > body.count("}"):
            return True
        return body.endswith((",", ":", "[", "{", '"'))

    @classmethod
    def extract_json_object(cls, text: str) -> dict[str, Any] | None:
        """从文本中提取 JSON 对象"""
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
        """迭代可能的 JSON 候选"""
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
        """提取第一个 choice"""
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                return first
        return None

    @classmethod
    def _extract_message_content(cls, message: Any) -> str:
        """提取 message content"""
        if isinstance(message, dict):
            return cls._stringify_content(message.get("content"))
        return cls._stringify_content(message)

    @classmethod
    def _extract_reasoning_from_message(cls, message: Any) -> str:
        """从 message 提取 reasoning"""
        if isinstance(message, dict):
            for key in cls._REASONING_KEYS:
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @classmethod
    def _stringify_content(cls, content: Any) -> str:
        """将 content 转为字符串"""
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
        """提取 text part"""
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
        """提取平衡的 JSON 片段"""
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

    @classmethod
    def normalize_response(
        cls,
        raw_response: Any,
        latency_ms: int = 0,
        trace_id: str | None = None,
    ) -> AIResponse:
        """标准化原始响应为 AIResponse"""
        try:
            text = cls.extract_text(raw_response)
            reasoning = cls.extract_reasoning(raw_response)

            # 尝试提取 usage
            usage = cls._extract_usage(raw_response)

            return AIResponse.success(
                output=text,
                usage=usage,
                latency_ms=latency_ms,
                trace_id=trace_id,
                thinking=reasoning if reasoning else None,
                raw=raw_response if isinstance(raw_response, dict) else None,
            )
        except (ValueError, TypeError, AttributeError) as e:
            return AIResponse.failure(
                error=f"Failed to normalize response: {e}",
                category=ErrorCategory.INVALID_RESPONSE,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

    @classmethod
    def _extract_usage(cls, payload: Any) -> Usage:
        """从响应中提取 usage 信息"""
        if not isinstance(payload, dict):
            return Usage.estimate("", "")

        usage_data = payload.get("usage")
        if not isinstance(usage_data, dict):
            usage_data = {}

        cached_tokens = usage_data.get("cached_tokens") or usage_data.get("cached_prompt_tokens") or 0
        prompt_tokens = usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0
        completion_tokens = usage_data.get("completion_tokens") or usage_data.get("output_tokens") or 0

        return Usage(
            cached_tokens=int(cached_tokens) if cached_tokens else 0,
            prompt_tokens=int(prompt_tokens) if prompt_tokens else 0,
            completion_tokens=int(completion_tokens) if completion_tokens else 0,
            total_tokens=int(usage_data.get("total_tokens", 0) or 0)
            or (int(prompt_tokens or 0) + int(completion_tokens or 0)),
            estimated=False,
        )


def normalize_list(value: Any) -> list[str]:
    """标准化为字符串列表"""
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        return [value.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def truncate_text(text: str, limit: int = 80) -> str:
    """截断文本"""
    compact = re.sub(r"\s+", " ", str(text or "").strip())
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 1)] + "…"


def split_lines(value: str) -> list[str]:
    """分割为行列表"""
    return [line.strip() for line in str(value or "").replace("\r\n", "\n").split("\n") if line.strip()]
