"""RedactionPolicy - deterministic redaction utilities.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import copy
import re
from typing import Any


class RedactionPolicy:
    """Compatibility redaction policy for legacy imports."""

    API_KEY_PATTERNS: tuple[tuple[str, str], ...] = (
        (r"sk-[A-Za-z0-9_-]{20,}", "***OPENAI_KEY***"),
        (r"github_pat_[A-Za-z0-9_]{20,}", "***GITHUB_TOKEN***"),
        (r"gh[pousr]_[A-Za-z0-9_]{20,}", "***GITHUB_TOKEN***"),
        (r"AIza[A-Za-z0-9_-]{20,}", "***GCP_API_KEY***"),
        (r"ya29\.[A-Za-z0-9_-]{20,}", "***GOOGLE_TOKEN***"),
        (r"Bearer\s+[A-Za-z0-9._-]{16,}", "Bearer ***TOKEN***"),
    )

    CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
        (r'("|\')?password("|\')?\s*[:=]\s*["\']?.+?["\']?(\s|$)', 'password="***" '),
        (r'("|\')?api_?key("|\')?\s*[:=]\s*["\']?.+?["\']?(\s|$)', 'api_key="***" '),
        (r'("|\')?secret("|\')?\s*[:=]\s*["\']?.+?["\']?(\s|$)', 'secret="***" '),
        (r'("|\')?token("|\')?\s*[:=]\s*["\']?.+?["\']?(\s|$)', 'token="***" '),
    )

    PII_PATTERNS: tuple[tuple[str, str], ...] = (
        (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[EMAIL_REDACTED]"),
        (r"\b1[3-9]\d{9}\b", "[PHONE_REDACTED]"),
        (r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "[INTERNAL_IP]"),
        (r"\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b", "[INTERNAL_IP]"),
        (r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "[INTERNAL_IP]"),
    )

    def __init__(self) -> None:
        self._compiled_patterns: list[tuple[re.Pattern[str], str]] = []
        for pattern, replacement in (
            *self.API_KEY_PATTERNS,
            *self.CREDENTIAL_PATTERNS,
            *self.PII_PATTERNS,
        ):
            self._compiled_patterns.append((re.compile(pattern, re.IGNORECASE), replacement))

    @classmethod
    def from_env(cls) -> RedactionPolicy:
        return cls()

    def redact_log(self, content: str) -> str:
        text = str(content or "")
        if not text:
            return ""
        for pattern, replacement in self._compiled_patterns:
            text = pattern.sub(replacement, text)
        return text

    def redact_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        payload = copy.deepcopy(dict(trace or {}))
        return self.redact_dict(payload)

    def redact_prompt(self, prompt: str) -> str:
        return self.redact_log(prompt)

    def redact_tool_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return self.redact_dict(dict(result or {}))

    def redact(self, text: str) -> str:
        """Compatibility alias with `layer.RedactionPolicy.redact()`."""
        return self.redact_log(text)

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in dict(data or {}).items():
            if isinstance(value, str):
                result[key] = self.redact_log(value)
            elif isinstance(value, dict):
                result[key] = self.redact_dict(value)
            elif isinstance(value, list):
                redacted_items: list[Any] = []
                for item in value:
                    if isinstance(item, str):
                        redacted_items.append(self.redact_log(item))
                    elif isinstance(item, dict):
                        redacted_items.append(self.redact_dict(item))
                    else:
                        redacted_items.append(item)
                result[key] = redacted_items
            else:
                result[key] = value
        return result
