"""Unit tests for sanitization."""

from __future__ import annotations

from polaris.kernelone.benchmark.reproducibility.shadow_replay.sanitization import (
    reset_sanitizer,
    sanitize_cassette_entry,
    sanitize_headers,
)


class TestSanitization:
    """Tests for sanitization functions."""

    def setup_method(self) -> None:
        """Reset sanitizer before each test."""
        reset_sanitizer()

    def test_sanitize_headers(self) -> None:
        """Test header sanitization."""
        headers = {
            "Authorization": "Bearer sk-secret-key-12345",
            "Content-Type": "application/json",
            "X-Api-Key": "my-api-key",
        }

        sanitized = sanitize_headers(headers)

        # Sensitive fields should be redacted
        assert sanitized["Authorization"] == "[REDACTED]"
        assert sanitized["X-Api-Key"] == "[REDACTED]"
        # Non-sensitive should be unchanged
        assert sanitized["Content-Type"] == "application/json"

    def test_sanitize_headers_no_sensitive(self) -> None:
        """Test that non-sensitive headers pass through."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        sanitized = sanitize_headers(headers)

        assert sanitized["Content-Type"] == "application/json"
        assert sanitized["Accept"] == "application/json"

    def test_sanitize_cassette_entry(self) -> None:
        """Test full cassette entry sanitization."""
        entry = {
            "sequence": 0,
            "request": {
                "method": "POST",
                "url": "https://api.openai.com/v1/chat/completions",
                "headers": {
                    "Authorization": "Bearer sk-12345",
                    "Content-Type": "application/json",
                },
                "body_hash": "abc123",
                "body_preview": '{"model": "gpt-4"}',
            },
            "response": {
                "status_code": 200,
                "headers": {
                    "Content-Type": "application/json",
                },
                "body_hash": "def456",
                "body_preview": '{"choices": []}',
            },
            "latency_ms": 100.0,
        }

        sanitized = sanitize_cassette_entry(entry)

        # Authorization should be redacted
        assert sanitized["request"]["headers"]["Authorization"] == "[REDACTED]"
        # Non-sensitive should remain
        assert sanitized["request"]["headers"]["Content-Type"] == "application/json"

    def test_sanitize_nested_headers(self) -> None:
        """Test that nested header dicts are sanitized."""
        entry = {
            "sequence": 0,
            "request": {
                "headers": {
                    "proxy-authorization": "Basic abc123",
                    "www-authenticate": "Bearer realm=test",
                },
            },
            "response": {
                "headers": {
                    "Set-Cookie": "session=abc123",
                },
            },
        }

        sanitized = sanitize_cassette_entry(entry)

        assert sanitized["request"]["headers"]["proxy-authorization"] == "[REDACTED]"
        assert sanitized["request"]["headers"]["www-authenticate"] == "[REDACTED]"
        # Note: Set-Cookie is not in default patterns, would need custom config
