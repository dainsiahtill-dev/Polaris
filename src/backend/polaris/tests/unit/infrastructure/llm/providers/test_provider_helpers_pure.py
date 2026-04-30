"""Tests for polaris.infrastructure.llm.providers.provider_helpers module (pure functions)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from polaris.infrastructure.llm.providers.provider_helpers import (
    CircuitBreaker,
    CircuitOpenError,
    _build_backoff_seconds,
    _is_retryable_network_error,
    _session_is_closed,
    _should_use_lightweight_stream_session_mode,
    get_circuit_breaker,
)


class TestShouldUseLightweightStreamSessionMode:
    def test_env_true_values(self, monkeypatch):
        for val in ["1", "true", "yes", "on"]:
            monkeypatch.setenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", val)
            assert _should_use_lightweight_stream_session_mode() is True

    def test_env_false_values(self, monkeypatch):
        for val in ["0", "false", "no", "off", ""]:
            monkeypatch.setenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", val)
            assert _should_use_lightweight_stream_session_mode() is False

    def test_env_unset_uses_pytest(self, monkeypatch):
        monkeypatch.delenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert _should_use_lightweight_stream_session_mode() is False

    def test_env_unset_but_pytest_set(self, monkeypatch):
        monkeypatch.delenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", raising=False)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_foo")
        assert _should_use_lightweight_stream_session_mode() is True


class TestSessionIsClosed:
    def test_none_is_closed(self):
        assert _session_is_closed(None) is True

    def test_bool_true(self):
        mock = MagicMock()
        mock.closed = True
        assert _session_is_closed(mock) is True

    def test_bool_false(self):
        mock = MagicMock()
        mock.closed = False
        assert _session_is_closed(mock) is False

    def test_no_closed_attr(self):
        mock = MagicMock(spec=[])
        assert _session_is_closed(mock) is False

    def test_non_bool_closed_attr(self):
        mock = MagicMock()
        mock.closed = "maybe"
        assert _session_is_closed(mock) is False


class TestBuildBackoffSeconds:
    def test_first_attempt(self):
        result = _build_backoff_seconds(attempt=1, base_delay_seconds=1.0, max_delay_seconds=30.0)
        assert 1.0 <= result <= 1.2  # base + 0-20% jitter

    def test_second_attempt(self):
        result = _build_backoff_seconds(attempt=2, base_delay_seconds=1.0, max_delay_seconds=30.0)
        assert 2.0 <= result <= 2.4  # 2x base + jitter

    def test_max_delay_capped(self):
        result = _build_backoff_seconds(attempt=100, base_delay_seconds=1.0, max_delay_seconds=5.0)
        assert 5.0 <= result <= 6.0  # capped at max + jitter

    def test_base_delay_minimum(self):
        result = _build_backoff_seconds(attempt=1, base_delay_seconds=0.5, max_delay_seconds=30.0)
        assert result >= 0.5

    def test_jitter_present(self):
        results = [
            _build_backoff_seconds(attempt=2, base_delay_seconds=1.0, max_delay_seconds=30.0)
            for _ in range(50)
        ]
        # With jitter, not all results should be identical
        assert len(set(round(r, 6) for r in results)) > 1


class TestIsRetryableNetworkError:
    def test_client_connector_error(self):
        exc = Exception("Connection failed")
        exc.__class__.__name__ = "ClientConnectorError"
        assert _is_retryable_network_error(exc) is True

    def test_connection_reset_error(self):
        exc = ConnectionResetError("Connection reset")
        assert _is_retryable_network_error(exc) is True

    def test_timeout_error(self):
        exc = TimeoutError("Timed out")
        assert _is_retryable_network_error(exc) is True

    def test_http_429_in_message(self):
        exc = Exception("429 Too Many Requests")
        assert _is_retryable_network_error(exc) is True

    def test_http_503_in_message(self):
        exc = Exception("503 Service Unavailable")
        assert _is_retryable_network_error(exc) is True

    def test_connection_refused_message(self):
        exc = Exception("connection refused")
        assert _is_retryable_network_error(exc) is True

    def test_not_retryable(self):
        exc = ValueError("Invalid argument")
        assert _is_retryable_network_error(exc) is False

    def test_ssl_handshake_message(self):
        exc = Exception("ssl handshake failed")
        assert _is_retryable_network_error(exc) is True

    def test_getaddrinfo_failed(self):
        exc = Exception("getaddrinfo failed")
        assert _is_retryable_network_error(exc) is True

    def test_empty_exception(self):
        exc = Exception()
        assert _is_retryable_network_error(exc) is False


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.snapshot()["state"] == "closed"

    def test_threshold_defaults(self):
        cb = CircuitBreaker()
        snap = cb.snapshot()
        assert snap["failure_threshold"] == 5
        assert snap["recovery_timeout_seconds"] == 60.0

    def test_custom_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=30.0)
        snap = cb.snapshot()
        assert snap["failure_threshold"] == 3
        assert snap["recovery_timeout_seconds"] == 30.0

    def test_before_call_closed_no_op(self):
        cb = CircuitBreaker()
        cb.before_call()  # Should not raise

    def test_on_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.on_failure()
        cb.on_failure()
        assert cb.snapshot()["state"] == "open"
        cb.on_success()
        snap = cb.snapshot()
        assert snap["state"] == "closed"
        assert snap["failure_count"] == 0

    def test_on_failure_opens_at_threshold(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.on_failure()
        assert cb.snapshot()["state"] == "closed"
        cb.on_failure()
        assert cb.snapshot()["state"] == "open"

    def test_open_blocks_calls(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.on_failure()
        with pytest.raises(CircuitOpenError):
            cb.before_call()

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.0)
        cb.on_failure()
        assert cb.snapshot()["state"] == "open"
        cb.before_call()  # Should transition to half_open
        assert cb.snapshot()["state"] == "half_open"

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.0)
        cb.on_failure()
        cb.before_call()  # half_open
        cb.on_failure()
        assert cb.snapshot()["state"] == "open"

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.0)
        cb.on_failure()
        cb.before_call()  # half_open
        cb.on_success()
        assert cb.snapshot()["state"] == "closed"

    def test_snapshot_isolation(self):
        cb = CircuitBreaker()
        snap1 = cb.snapshot()
        cb.on_failure()
        snap2 = cb.snapshot()
        assert snap1["failure_count"] == 0
        assert snap2["failure_count"] == 1


class TestGetCircuitBreaker:
    def test_returns_same_instance_for_same_key(self):
        cb1 = get_circuit_breaker("test-key")
        cb2 = get_circuit_breaker("test-key")
        assert cb1 is cb2

    def test_different_keys_return_different_instances(self):
        cb1 = get_circuit_breaker("key-a")
        cb2 = get_circuit_breaker("key-b")
        assert cb1 is not cb2

    def test_empty_key_defaults(self):
        cb1 = get_circuit_breaker("")
        cb2 = get_circuit_breaker("default")
        assert cb1 is cb2

    def test_custom_params_applied_to_new(self):
        cb = get_circuit_breaker("custom-key", failure_threshold=10, recovery_timeout_seconds=120.0)
        snap = cb.snapshot()
        assert snap["failure_threshold"] == 10
        assert snap["recovery_timeout_seconds"] == 120.0

    def test_existing_instance_ignores_new_params(self):
        cb1 = get_circuit_breaker("existing-key", failure_threshold=3)
        cb2 = get_circuit_breaker("existing-key", failure_threshold=10)
        snap = cb2.snapshot()
        assert snap["failure_threshold"] == 3
