"""Sanitization hook for PII redaction in audit events.

Design:
- Pluggable sanitization pipeline
- Configurable field patterns
- Preserves structure (same keys/indices)
- Async-safe (no shared mutable state)
- Bypass option for critical security events

Usage:
    sanitizer = SanitizationHook()  # default config
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

# Default patterns for sensitive fields (case-insensitive)
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

# Token patterns for string value detection
_TOKEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^[a-f0-9]{32,}$", re.IGNORECASE),  # Hex strings
    re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"),  # JWT
    re.compile(r"^[A-Za-z0-9+/]+=*$"),  # Base64
]

# Placeholder for redacted values
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
        """Compile patterns for efficiency."""
        self._compiled_patterns: list[re.Pattern[str]] = []
        for pattern in self.patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._compiled_patterns.append(compiled)
            except re.error:
                pass  # Skip invalid regex


class SanitizationHook:
    """Hook for sanitizing audit events before persistence.

    Provides recursive redaction of sensitive fields while preserving
    data structure. Operates on copies without mutating input.

    Usage:
        hook = SanitizationHook()
        sanitized = hook.sanitize(event_dict)

        # With custom config
        config = SanitizationConfig(patterns=["my_secret", "telemetry"])
        hook = SanitizationHook(config)
    """

    def __init__(self, config: SanitizationConfig | None = None) -> None:
        """Initialize sanitization hook.

        Args:
            config: Sanitization configuration. Uses default if None.
        """
        self._config = config or SanitizationConfig()

    @property
    def config(self) -> SanitizationConfig:
        """Get sanitization configuration."""
        return self._config

    def sanitize(self, event: Any) -> Any:
        """Sanitize an audit event.

        Args:
            event: Event to sanitize (dict, list, or primitive).

        Returns:
            Sanitized copy of input. Input is not modified.
        """
        # Check if event type bypasses sanitization
        if isinstance(event, dict):
            event_type = str(event.get("event_type", ""))
            if event_type in self._config.bypass_for_types:
                return event

        return self._sanitize_impl(event)

    def _sanitize_impl(self, data: Any) -> Any:
        """Internal sanitize implementation.

        Args:
            data: Data to sanitize.

        Returns:
            Sanitized data.
        """
        if data is None:
            return None

        if isinstance(data, dict):
            return self._sanitize_dict(data)

        if isinstance(data, list):
            return self._sanitize_list(data)

        if isinstance(data, str):
            return self._sanitize_string(data)

        # For primitives (int, float, bool), return as-is
        return data

    def _sanitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Sanitize a dictionary.

        Args:
            data: Dictionary to sanitize.

        Returns:
            Sanitized dictionary.
        """
        result: dict[str, Any] = {}
        for key, value in data.items():
            # Check for custom sanitizer
            if key in self._config.custom_sanitizers:
                result[key] = self._config.custom_sanitizers[key](value)
            # Check if key matches sensitive pattern
            elif self._is_sensitive_key(key):
                result[key] = self._config.placeholder
            # Special handling for certain keys
            elif key.endswith("_preview") or key.endswith("_summary"):
                # Truncate preview/summary fields
                result[key] = self._truncate(value)
            else:
                result[key] = self._sanitize_impl(value)
        return result

    def _sanitize_list(self, data: list[Any]) -> list[Any]:
        """Sanitize a list.

        Args:
            data: List to sanitize.

        Returns:
            Sanitized list.
        """
        return [self._sanitize_impl(item) for item in data]

    def _is_sensitive_key(self, key: str) -> bool:
        """Check if a key matches any sensitive pattern.

        Args:
            key: Key name to check.

        Returns:
            True if the key is sensitive.
        """
        if not key:
            return False

        # Check skip fields
        if key in self._config.skip_fields:
            return False

        # Check against all compiled patterns
        return any(pattern.search(key) for pattern in self._config._compiled_patterns)

    def _sanitize_string(self, value: str) -> str:
        """Sanitize a string value.

        This handles cases where sensitive data might be embedded
        in longer strings (e.g., authorization headers).

        Args:
            value: String value to sanitize.

        Returns:
            Potentially redacted string value.
        """
        if not value:
            return value

        value_lower = value.lower()

        # Check for common token indicators at start
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

        # Check if entire string looks like a token
        if len(value) >= 20 and self._looks_like_token(value):
            return self._config.placeholder

        return value

    def _looks_like_token(self, value: str) -> bool:
        """Check if a string looks like a token.

        Args:
            value: String to check.

        Returns:
            True if the string looks like a token.
        """
        return any(pattern.match(value) for pattern in _TOKEN_PATTERNS)

    def _truncate(self, value: Any) -> Any:
        """Truncate a value to max_preview_length.

        Args:
            value: Value to truncate.

        Returns:
            Truncated value.
        """
        if isinstance(value, str):
            return value[: self._config.max_preview_length]
        return value


# =============================================================================
# Module-level convenience
# =============================================================================

_default_sanitizer: SanitizationHook | None = None


def get_default_sanitizer() -> SanitizationHook:
    """Get the default sanitizer instance.

    Returns:
        Shared default sanitizer.
    """
    global _default_sanitizer
    if _default_sanitizer is None:
        _default_sanitizer = SanitizationHook()
    return _default_sanitizer


def reset_default_sanitizer() -> None:
    """Reset the default sanitizer instance.

    This is primarily for testing.
    """
    global _default_sanitizer
    _default_sanitizer = None
