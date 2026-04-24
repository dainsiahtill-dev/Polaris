"""Tests for polaris.infrastructure.messaging.nats.server_runtime pure logic helpers."""

from __future__ import annotations

from polaris.infrastructure.messaging.nats.server_runtime import (
    _first_nats_server_url,
    _parse_local_nats_endpoint,
    should_manage_local_nats,
)


class TestFirstNatsServerUrl:
    def test_empty_string_returns_default(self) -> None:
        assert _first_nats_server_url("") == "nats://127.0.0.1:4222"

    def test_none_returns_default(self) -> None:
        assert _first_nats_server_url(None) == "nats://127.0.0.1:4222"  # type: ignore[arg-type]

    def test_single_url(self) -> None:
        assert _first_nats_server_url("nats://127.0.0.1:4222") == "nats://127.0.0.1:4222"

    def test_single_url_with_whitespace(self) -> None:
        assert _first_nats_server_url("  nats://127.0.0.1:4222  ") == "nats://127.0.0.1:4222"

    def test_comma_separated_returns_first(self) -> None:
        raw = "nats://127.0.0.1:4222,nats://127.0.0.1:4223"
        assert _first_nats_server_url(raw) == "nats://127.0.0.1:4222"

    def test_comma_separated_strips_whitespace(self) -> None:
        raw = "  nats://127.0.0.1:4222  ,  nats://127.0.0.1:4223  "
        assert _first_nats_server_url(raw) == "nats://127.0.0.1:4222"

    def test_empty_after_comma_returns_default(self) -> None:
        assert _first_nats_server_url(",") == "nats://127.0.0.1:4222"


class TestParseLocalNatsEndpoint:
    def test_localhost_ipv4(self) -> None:
        result = _parse_local_nats_endpoint("nats://127.0.0.1:4222")
        assert result == ("127.0.0.1", 4222)

    def test_localhost_name(self) -> None:
        result = _parse_local_nats_endpoint("nats://localhost:4222")
        assert result == ("localhost", 4222)

    def test_ipv6_loopback(self) -> None:
        result = _parse_local_nats_endpoint("nats://[::1]:4222")
        assert result == ("::1", 4222)

    def test_remote_host_returns_none(self) -> None:
        assert _parse_local_nats_endpoint("nats://example.com:4222") is None

    def test_remote_ip_returns_none(self) -> None:
        assert _parse_local_nats_endpoint("nats://192.168.1.1:4222") is None

    def test_default_port_when_missing(self) -> None:
        result = _parse_local_nats_endpoint("nats://127.0.0.1")
        assert result == ("127.0.0.1", 4222)

    def test_comma_separated_uses_first(self) -> None:
        result = _parse_local_nats_endpoint("nats://127.0.0.1:4222,nats://example.com:4222")
        assert result == ("127.0.0.1", 4222)

    def test_comma_second_is_local(self) -> None:
        result = _parse_local_nats_endpoint("nats://example.com:4222,nats://127.0.0.1:4222")
        assert result is None

    def test_empty_string_returns_default_localhost(self) -> None:
        assert _parse_local_nats_endpoint("") == ("127.0.0.1", 4222)

    def test_malformed_url_returns_none(self) -> None:
        assert _parse_local_nats_endpoint("not-a-url") is None


class TestShouldManageLocalNats:
    def test_local_returns_true(self) -> None:
        assert should_manage_local_nats("nats://127.0.0.1:4222") is True

    def test_remote_returns_false(self) -> None:
        assert should_manage_local_nats("nats://example.com:4222") is False

    def test_empty_returns_true_because_default_is_local(self) -> None:
        assert should_manage_local_nats("") is True
