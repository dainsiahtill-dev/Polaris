"""Sanitization hook for PII redaction.

Design:
- Pluggable sanitization pipeline
- Configurable field patterns
- Preserves structure (same keys/indices)
- Async-safe (no shared mutable state)
- Bypass option for critical security events

Usage:
    sanitizer = SanitizationHook()
    sanitized = sanitizer.sanitize(audit_event_dict)

    # Custom config
    config = SanitizationConfig(
        patterns=["password", "token", "secret"],
        placeholder="[REDACTED]",
    )
    sanitizer = SanitizationHook(config)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

_DEFAULT_PATTERNS: list[str] = [
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "key",
    "passphrase",
    "private_key",
    "privatekey",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "bearer",
    "session",
    "session_id",
    "sessionid",
    "cookie",
    "x_auth",
    "x-api-key",
    "x-api-token",
]

_TOKEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^[a-f0-9]{32,}$", re.IGNORECASE),
    re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"),
    re.compile(r"^[A-Za-z0-9+/]+=*$"),
]

_PLACEHOLDER = "[REDACTED]"


@dataclass
class SanitizationConfig:
    """Configuration for sanitization behavior.

    Attributes:
        patterns: List of regex patterns (case-insensitive) for sensitive keys.
        placeholder: Placeholder text for redacted values.
        max_preview_length: Maximum length for preview fields.
        skip_fields: Fields to never redact (e.g., field names that are OK).
        bypass_for_types: Event types that bypass sanitization.
        custom_sanitizers: Custom field sanitizers (field_name -> function).
    """

    patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_PATTERNS))
    placeholder: str = _PLACEHOLDER
    max_preview_length: int = 500
    skip_fields: list[str] = field(default_factory=list)
    bypass_for_types: list[str] = field(default_factory=lambda: ["security_violation"])
    custom_sanitizers: dict[str, Callable[[Any], Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._compiled_patterns: list[re.Pattern[str]] = []
        for pattern in self.patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._compiled_patterns.append(compiled)
            except re.error:
                pass


class SanitizationHook:
    """Hook for sanitizing audit events before persistence.

    Provides recursive redaction of sensitive fields while preserving
    data structure. Operates on copies without mutating input.
    """

    def __init__(self, config: SanitizationConfig | None = None) -> None:
        self._config = config or SanitizationConfig()

    @property
    def config(self) -> SanitizationConfig:
        return self._config

    def sanitize(self, event: Any) -> Any:
        if isinstance(event, dict):
            event_type = str(event.get("event_type", ""))
            if event_type in self._config.bypass_for_types:
                return event
        return self._sanitize_impl(event)

    def _sanitize_impl(self, data: Any) -> Any:
        if data is None:
            return None
        if isinstance(data, dict):
            return self._sanitize_dict(data)
        if isinstance(data, list):
            return self._sanitize_list(data)
        if isinstance(data, str):
            return self._sanitize_string(data)
        return data

    def _sanitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in self._config.custom_sanitizers:
                result[key] = self._config.custom_sanitizers[key](value)
            elif self._is_sensitive_key(key):
                result[key] = self._config.placeholder
            elif key.endswith("_preview") or key.endswith("_summary"):
                result[key] = self._truncate(value)
            else:
                result[key] = self._sanitize_impl(value)
        return result

    def _sanitize_list(self, data: list[Any]) -> list[Any]:
        return [self._sanitize_impl(item) for item in data]

    def _is_sensitive_key(self, key: str) -> bool:
        if not key:
            return False
        if key in self._config.skip_fields:
            return False
        return any(pattern.search(key) for pattern in self._config._compiled_patterns)

    def _sanitize_string(self, value: str) -> str:
        if not value:
            return value
        value_lower = value.lower()
        token_indicators = [
            "bearer ",
            "token ",
            "apikey ",
            "api-key ",
            "authorization:",
            "auth:",
        ]
        for indicator in token_indicators:
            if value_lower.startswith(indicator):
                return self._config.placeholder
        if len(value) >= 20 and self._looks_like_token(value):
            return self._config.placeholder
        return value

    def _looks_like_token(self, value: str) -> bool:
        return any(pattern.match(value) for pattern in _TOKEN_PATTERNS)

    def _truncate(self, value: Any) -> Any:
        if isinstance(value, str):
            return value[: self._config.max_preview_length]
        return value


_default_sanitizer: SanitizationHook | None = None


def get_default_sanitizer() -> SanitizationHook:
    global _default_sanitizer
    if _default_sanitizer is None:
        _default_sanitizer = SanitizationHook()
    return _default_sanitizer


def reset_default_sanitizer() -> None:
    global _default_sanitizer
    _default_sanitizer = None
