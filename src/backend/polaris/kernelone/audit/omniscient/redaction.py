"""SensitiveFieldRedactor — redact sensitive fields from audit payloads.

Design:
- Recursively redact sensitive fields in dicts/lists
- Default sensitive patterns: password, token, secret, api_key, etc.
- Preserve structure (same keys/indices) but redact values
- Handle all JSON-serializable types
"""

from __future__ import annotations

import re
from typing import Any

# =============================================================================
# Constants
# =============================================================================

REDACTED_PLACEHOLDER: str = "[REDACTED]"

# Default sensitive field patterns (case-insensitive)
DEFAULT_SENSITIVE_PATTERNS: list[str] = [
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


# =============================================================================
# Redactor Class
# =============================================================================


class SensitiveFieldRedactor:
    """Redact sensitive fields from audit payloads.

    Handles recursive redaction of sensitive values in dicts and lists
    while preserving structure. Operates on copies without mutating input.

    Attributes:
        sensitive_patterns: Compiled regex patterns for sensitive keys.
        placeholder: Replacement text for sensitive values.

    Usage:
        redactor = SensitiveFieldRedactor()
        redacted = redactor.redact({
            "username": "john",
            "password": "secret123",
            "config": {"api_key": "abc123"},
        })
        # Result: {"username": "john", "password": "[REDACTED]", "config": {"api_key": "[REDACTED]"}}
    """

    def __init__(
        self,
        extra_sensitive_patterns: list[str] | None = None,
        placeholder: str = REDACTED_PLACEHOLDER,
    ) -> None:
        """Initialize the redactor with sensitive patterns.

        Args:
            extra_sensitive_patterns: Additional patterns to treat as sensitive.
            placeholder: Text to replace sensitive values with.
        """
        self._placeholder = placeholder

        # Build combined list of patterns
        patterns = list(DEFAULT_SENSITIVE_PATTERNS)
        if extra_sensitive_patterns:
            patterns.extend(extra_sensitive_patterns)

        # Compile regex patterns (case-insensitive)
        self._sensitive_patterns: list[re.Pattern[str]] = []
        for pattern in patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._sensitive_patterns.append(compiled)
            except re.error:
                # Skip invalid regex patterns
                pass

    @property
    def placeholder(self) -> str:
        """Placeholder text for redacted values."""
        return self._placeholder

    def redact(self, data: Any) -> Any:
        """Recursively redact sensitive fields in data.

        Args:
            data: Input data to redact (dict, list, or primitive).

        Returns:
            Redacted copy of input data. Input is not modified.
        """
        if data is None:
            return None

        if isinstance(data, dict):
            return self._redact_dict(data)

        if isinstance(data, list):
            return self._redact_list(data)

        if isinstance(data, str):
            return self._redact_string(data)

        # For primitives (int, float, bool), return as-is
        return data

    def _redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive fields in a dictionary.

        Args:
            data: Dictionary to redact.

        Returns:
            New dictionary with sensitive values redacted.
        """
        result: dict[str, Any] = {}
        for key, value in data.items():
            if self._is_sensitive_key(key):
                result[key] = self._placeholder
            else:
                result[key] = self.redact(value)
        return result

    def _redact_list(self, data: list[Any]) -> list[Any]:
        """Redact sensitive fields in a list.

        Args:
            data: List to redact.

        Returns:
            New list with sensitive values redacted.
        """
        return [self.redact(item) for item in data]

    def _is_sensitive_key(self, key: str) -> bool:
        """Check if a key matches any sensitive pattern.

        Args:
            key: Key name to check.

        Returns:
            True if the key is sensitive, False otherwise.
        """
        if not key:
            return False

        # Check against all compiled patterns
        return any(pattern.search(key) for pattern in self._sensitive_patterns)

    def _redact_string(self, value: str) -> str:
        """Redact sensitive patterns in a string value.

        This handles cases where sensitive data might be embedded
        in longer strings (e.g., authorization headers).

        Args:
            value: String value to redact.

        Returns:
            Potentially redacted string value.
        """
        if not value:
            return value

        # Check if the entire string looks like a sensitive token
        value_lower = value.lower()

        # Check for common token patterns
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
                return self._placeholder

        # Check for long hex/string patterns that might be tokens
        # Heuristic: if string is 20+ chars and mostly alphanumeric,
        # it might be a token and should be redacted
        if len(value) >= 20 and self._looks_like_token(value):
            return self._placeholder

        return value

    def _looks_like_token(self, value: str) -> bool:
        """Check if a string looks like a token.

        Args:
            value: String to check.

        Returns:
            True if the string looks like a token.
        """
        # Common token patterns
        # Hex strings (like HMAC keys, hashes)
        if re.match(r"^[a-f0-9]{32,}$", value, re.IGNORECASE):
            return True

        # JWT tokens (three base64 sections separated by dots)
        if re.match(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$", value):
            return True

        # Base64-encoded strings (must have valid padding)
        return len(value) >= 32 and re.match(r"^[A-Za-z0-9+/]+=*$", value) is not None


# =============================================================================
# Module-level convenience functions
# =============================================================================

# Default redactor instance for convenience
_default_redactor: SensitiveFieldRedactor | None = None


def get_default_redactor() -> SensitiveFieldRedactor:
    """Get the default redactor instance.

    Returns:
        Shared default redactor instance.
    """
    global _default_redactor
    if _default_redactor is None:
        _default_redactor = SensitiveFieldRedactor()
    return _default_redactor


def redact_sensitive_data(data: Any) -> Any:
    """Convenience function to redact sensitive data.

    Uses the default redactor instance.

    Args:
        data: Data to redact.

    Returns:
        Redacted data.
    """
    return get_default_redactor().redact(data)


def reset_default_redactor() -> None:
    """Reset the default redactor instance.

    This is primarily for testing.
    """
    global _default_redactor
    _default_redactor = None
