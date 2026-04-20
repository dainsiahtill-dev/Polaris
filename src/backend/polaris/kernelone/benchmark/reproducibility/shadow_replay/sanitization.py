"""Sanitization integration for Chronos Mirror.

Reuses the existing SanitizationHook to redact sensitive information
from cassettes before persistence.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.audit.omniscient.adapters.sanitization_hook import (
    SanitizationConfig,
    SanitizationHook,
)

# Singleton sanitizer instance
_default_sanitizer: SanitizationHook | None = None


def get_sanitizer() -> SanitizationHook:
    """Get the default sanitizer for cassette sanitization.

    Returns:
        SanitizationHook instance configured for HTTP headers/tokens
    """
    global _default_sanitizer
    if _default_sanitizer is None:
        # Configure with HTTP-specific patterns
        config = SanitizationConfig(
            patterns=[
                # Existing defaults (api_key, token, password, etc.)
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
                # HTTP-specific patterns
                "authorization",
                "proxy-authorization",
                "www-authenticate",
                "proxy-authenticate",
            ],
            placeholder="[REDACTED]",
            max_preview_length=200,
        )
        _default_sanitizer = SanitizationHook(config)
    return _default_sanitizer


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Sanitize sensitive fields in HTTP headers.

    Args:
        headers: Original headers dict

    Returns:
        Sanitized headers dict (new copy, original unchanged)
    """
    sanitizer = get_sanitizer()
    return sanitizer.sanitize(dict(headers))


def sanitize_cassette_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a cassette entry before persistence.

    Args:
        entry: Cassette entry dict

    Returns:
        Sanitized entry dict
    """
    sanitizer = get_sanitizer()

    # Deep sanitize the entry
    sanitized = sanitizer.sanitize(entry)

    # Ensure request headers are sanitized
    if "request" in sanitized and "headers" in sanitized["request"]:
        sanitized["request"]["headers"] = sanitizer.sanitize(sanitized["request"]["headers"])

    # Ensure response headers are sanitized
    if "response" in sanitized and "headers" in sanitized["response"]:
        sanitized["response"]["headers"] = sanitizer.sanitize(sanitized["response"]["headers"])

    return sanitized


def reset_sanitizer() -> None:
    """Reset the default sanitizer instance.

    Primarily for testing.
    """
    global _default_sanitizer
    _default_sanitizer = None
