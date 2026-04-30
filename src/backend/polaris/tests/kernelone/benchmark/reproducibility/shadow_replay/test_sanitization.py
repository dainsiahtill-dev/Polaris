"""Tests for shadow replay sanitization module."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.benchmark.reproducibility.shadow_replay.sanitization import (
    get_sanitizer,
    reset_sanitizer,
    sanitize_cassette_entry,
    sanitize_headers,
)


class TestGetSanitizer:
    """Tests for get_sanitizer function."""

    def test_returns_sanitization_hook(self) -> None:
        sanitizer = get_sanitizer()
        from polaris.kernelone.audit.omniscient.adapters.sanitization_hook import (
            SanitizationHook,
        )

        assert isinstance(sanitizer, SanitizationHook)

    def test_singleton_returns_same_instance(self) -> None:
        sanitizer1 = get_sanitizer()
        sanitizer2 = get_sanitizer()
        assert sanitizer1 is sanitizer2

    def test_after_reset_returns_new_instance(self) -> None:
        sanitizer1 = get_sanitizer()
        reset_sanitizer()
        sanitizer2 = get_sanitizer()
        assert sanitizer1 is not sanitizer2

    def test_has_config_attribute(self) -> None:
        sanitizer = get_sanitizer()
        assert hasattr(sanitizer, "config")

    def test_config_has_patterns(self) -> None:
        sanitizer = get_sanitizer()
        assert hasattr(sanitizer.config, "patterns")
        assert len(sanitizer.config.patterns) > 0

    def test_config_has_placeholder(self) -> None:
        sanitizer = get_sanitizer()
        assert hasattr(sanitizer.config, "placeholder")
        assert sanitizer.config.placeholder == "[REDACTED]"

    def test_config_has_max_preview_length(self) -> None:
        sanitizer = get_sanitizer()
        assert hasattr(sanitizer.config, "max_preview_length")
        assert sanitizer.config.max_preview_length == 200

    def test_includes_http_specific_patterns(self) -> None:
        sanitizer = get_sanitizer()
        patterns = sanitizer.config.patterns
        assert "authorization" in patterns
        assert "proxy-authorization" in patterns
        assert "www-authenticate" in patterns


class TestResetSanitizer:
    """Tests for reset_sanitizer function."""

    def test_reset_clears_singleton(self) -> None:
        sanitizer1 = get_sanitizer()
        reset_sanitizer()
        sanitizer2 = get_sanitizer()
        assert sanitizer1 is not sanitizer2

    def test_reset_idempotent(self) -> None:
        reset_sanitizer()
        reset_sanitizer()
        sanitizer = get_sanitizer()
        assert sanitizer is not None

    def test_after_reset_config_intact(self) -> None:
        reset_sanitizer()
        sanitizer = get_sanitizer()
        assert sanitizer.config.placeholder == "[REDACTED]"


class TestSanitizeHeaders:
    """Tests for sanitize_headers function."""

    def test_sanitizes_authorization_header(self) -> None:
        headers = {"Authorization": "Bearer secret-token-123"}
        result = sanitize_headers(headers)
        assert result["Authorization"] == "[REDACTED]"

    def test_sanitizes_api_key_header(self) -> None:
        headers = {"X-API-Key": "my-secret-key"}
        result = sanitize_headers(headers)
        assert result["X-API-Key"] == "[REDACTED]"

    def test_leaves_safe_headers_unchanged(self) -> None:
        headers = {"Content-Type": "application/json"}
        result = sanitize_headers(headers)
        assert result["Content-Type"] == "application/json"

    def test_returns_new_dict(self) -> None:
        headers = {"Authorization": "secret"}
        result = sanitize_headers(headers)
        assert result is not headers

    def test_does_not_mutate_original(self) -> None:
        headers = {"Authorization": "secret"}
        original = dict(headers)
        sanitize_headers(headers)
        assert headers == original

    def test_sanitizes_multiple_sensitive_headers(self) -> None:
        headers = {
            "Authorization": "Bearer token",
            "Cookie": "session=abc",
            "Content-Type": "application/json",
        }
        result = sanitize_headers(headers)
        assert result["Authorization"] == "[REDACTED]"
        assert result["Cookie"] == "[REDACTED]"
        assert result["Content-Type"] == "application/json"

    def test_empty_dict(self) -> None:
        result = sanitize_headers({})
        assert result == {}

    def test_case_insensitive_matching(self) -> None:
        headers = {"authorization": "secret"}
        result = sanitize_headers(headers)
        assert result["authorization"] == "[REDACTED]"


class TestSanitizeCassetteEntry:
    """Tests for sanitize_cassette_entry function."""

    def test_sanitizes_request_headers(self) -> None:
        entry: dict[str, Any] = {
            "request": {"headers": {"Authorization": "Bearer secret"}},
            "response": {"headers": {"Content-Type": "json"}},
        }
        result = sanitize_cassette_entry(entry)
        assert result["request"]["headers"]["Authorization"] == "[REDACTED]"

    def test_sanitizes_response_headers(self) -> None:
        entry: dict[str, Any] = {
            "request": {"headers": {"Accept": "json"}},
            "response": {"headers": {"Set-Cookie": "session=abc"}},
        }
        result = sanitize_cassette_entry(entry)
        assert result["response"]["headers"]["Set-Cookie"] == "[REDACTED]"

    def test_body_string_not_auto_redacted(self) -> None:
        # NOTE: String values are not auto-redacted unless they look like
        # tokens or start with token indicators. This is current behavior.
        entry: dict[str, Any] = {
            "request": {"body": '{"api_key": "super-secret", "data": "normal"}'},
        }
        result = sanitize_cassette_entry(entry)
        # Body string is preserved since it doesn't match token patterns
        assert result["request"]["body"] == '{"api_key": "super-secret", "data": "normal"}'

    def test_returns_new_dict(self) -> None:
        entry: dict[str, Any] = {"request": {"headers": {}}}
        result = sanitize_cassette_entry(entry)
        assert result is not entry

    def test_does_not_mutate_original(self) -> None:
        entry: dict[str, Any] = {"request": {"headers": {"Authorization": "secret"}}}
        original = {"request": {"headers": {"Authorization": "secret"}}}
        sanitize_cassette_entry(entry)
        assert entry == original

    def test_no_request_no_response(self) -> None:
        entry: dict[str, Any] = {"url": "http://example.com"}
        result = sanitize_cassette_entry(entry)
        assert result["url"] == "http://example.com"

    def test_empty_entry(self) -> None:
        result = sanitize_cassette_entry({})
        assert result == {}

    def test_nested_dict_sanitization(self) -> None:
        entry: dict[str, Any] = {
            "request": {
                "headers": {"Authorization": "secret"},
                "body": {"password": "12345"},
            }
        }
        result = sanitize_cassette_entry(entry)
        assert result["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert result["request"]["body"]["password"] == "[REDACTED]"

    def test_preserves_non_sensitive_data(self) -> None:
        entry: dict[str, Any] = {
            "request": {
                "url": "http://example.com/api",
                "method": "GET",
            }
        }
        result = sanitize_cassette_entry(entry)
        assert result["request"]["url"] == "http://example.com/api"
        assert result["request"]["method"] == "GET"


class TestSanitizerIntegration:
    """Integration tests for sanitization pipeline."""

    def test_full_cassette_sanitization(self) -> None:
        entry: dict[str, Any] = {
            "request": {
                "url": "http://api.example.com/v1/users",
                "method": "POST",
                "headers": {
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
                    "Content-Type": "application/json",
                    "X-API-Key": "secret-key-12345",
                },
                "body": '{"name": "John", "password": "hunter2"}',
            },
            "response": {
                "status": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Set-Cookie": "session_id=abc123; Path=/",
                },
                "body": '{"id": 1, "name": "John"}',
            },
        }
        result = sanitize_cassette_entry(entry)

        # Request headers sanitized
        assert result["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert result["request"]["headers"]["X-API-Key"] == "[REDACTED]"
        assert result["request"]["headers"]["Content-Type"] == "application/json"

        # Response headers sanitized
        assert result["response"]["headers"]["Set-Cookie"] == "[REDACTED]"
        assert result["response"]["headers"]["Content-Type"] == "application/json"

        # Body string preserved (not auto-redacted unless token pattern matches)
        assert "password" in result["request"]["body"]

        # Non-sensitive preserved
        assert result["request"]["url"] == "http://api.example.com/v1/users"
        assert result["response"]["status"] == 200

    def test_reset_between_tests(self) -> None:
        reset_sanitizer()
        sanitizer = get_sanitizer()
        assert sanitizer is not None
        headers = {"token": "secret"}
        result = sanitize_headers(headers)
        assert result["token"] == "[REDACTED]"
