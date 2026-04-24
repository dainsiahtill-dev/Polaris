"""Tests for polaris.infrastructure.llm.providers.http_utils."""

from __future__ import annotations

from polaris.infrastructure.llm.providers.http_utils import (
    _is_blocked_ip,
    join_url,
    merge_headers,
    normalize_base_url,
    validate_base_url_for_ssrf,
)


class TestNormalizeBaseUrl:
    def test_strips_trailing_slash(self) -> None:
        assert normalize_base_url("https://example.com/") == "https://example.com"

    def test_uses_default(self) -> None:
        assert normalize_base_url("", default="https://default.com") == "https://default.com"

    def test_empty_default(self) -> None:
        assert normalize_base_url("", default="") == ""


class TestJoinUrl:
    def test_basic_join(self) -> None:
        assert join_url("https://example.com", "/path") == "https://example.com/path"

    def test_path_without_leading_slash(self) -> None:
        assert join_url("https://example.com", "path") == "https://example.com/path"

    def test_absolute_path_returns_unchanged(self) -> None:
        assert join_url("https://example.com", "https://other.com/path") == "https://other.com/path"

    def test_strip_prefixes(self) -> None:
        assert join_url("https://example.com/v1", "/v1/path", strip_prefixes=["v1"]) == "https://example.com/v1/path"


class TestMergeHeaders:
    def test_none_inputs(self) -> None:
        assert merge_headers() == {}

    def test_merges_extra(self) -> None:
        assert merge_headers({"a": "1"}, {"b": "2"}) == {"a": "1", "b": "2"}

    def test_skips_none_values(self) -> None:
        assert merge_headers({"a": "1"}, {"b": None}) == {"a": "1"}


class TestIsBlockedIp:
    def test_private_ip_blocked(self) -> None:
        assert _is_blocked_ip("10.0.0.1") is True
        assert _is_blocked_ip("192.168.1.1") is True
        assert _is_blocked_ip("172.16.0.1") is True

    def test_loopback_blocked(self) -> None:
        assert _is_blocked_ip("127.0.0.1") is True

    def test_public_ip_allowed(self) -> None:
        assert _is_blocked_ip("8.8.8.8") is False

    def test_invalid_ip(self) -> None:
        assert _is_blocked_ip("not-an-ip") is False


class TestValidateBaseUrlForSsrf:
    def test_empty_url(self) -> None:
        ok, reason = validate_base_url_for_ssrf("")
        assert ok is False
        assert "empty" in reason.lower()

    def test_http_without_localhost(self) -> None:
        ok, _reason = validate_base_url_for_ssrf("http://example.com")
        assert ok is False

    def test_http_with_localhost_allowed(self) -> None:
        ok, reason = validate_base_url_for_ssrf("http://localhost", allow_localhost=True)
        assert ok is True
        assert reason == ""

    def test_http_with_127_allowed(self) -> None:
        ok, reason = validate_base_url_for_ssrf("http://127.0.0.1", allow_localhost=True)
        assert ok is True
        assert reason == ""

    def test_http_localhost_with_port_blocked(self) -> None:
        # Implementation extracts host as "localhost:8080" which is not in the allow-list
        ok, _reason = validate_base_url_for_ssrf("http://localhost:8080", allow_localhost=True)
        assert ok is False

    def test_https_public_allowed(self) -> None:
        ok, reason = validate_base_url_for_ssrf("https://api.openai.com")
        assert ok is True
        assert reason == ""

    def test_private_ip_blocked(self) -> None:
        ok, reason = validate_base_url_for_ssrf("https://192.168.1.1")
        assert ok is False
        assert "blocked" in reason.lower()

    def test_localhost_blocked_by_default(self) -> None:
        ok, reason = validate_base_url_for_ssrf("https://localhost")
        assert ok is False
        assert "localhost" in reason.lower()

    def test_invalid_scheme(self) -> None:
        ok, reason = validate_base_url_for_ssrf("ftp://example.com")
        assert ok is False
        assert "scheme" in reason.lower()
