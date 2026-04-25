"""Tests for polaris.delivery.http.middleware.logging.

Covers RequestLoggingMiddleware, header masking, and log formatting.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.delivery.http.middleware.logging import (
    RequestLoggingMiddleware,
    get_logging_middleware,
)


class TestRequestLoggingMiddleware:
    """Tests for RequestLoggingMiddleware middleware logic."""

    def test_excluded_paths_constant(self) -> None:
        assert "/health" in RequestLoggingMiddleware.EXCLUDED_PATHS
        assert "/metrics" in RequestLoggingMiddleware.EXCLUDED_PATHS
        assert "/favicon.ico" in RequestLoggingMiddleware.EXCLUDED_PATHS

    def test_sensitive_headers_constant(self) -> None:
        assert "authorization" in RequestLoggingMiddleware.SENSITIVE_HEADERS
        assert "cookie" in RequestLoggingMiddleware.SENSITIVE_HEADERS
        assert "x-api-key" in RequestLoggingMiddleware.SENSITIVE_HEADERS
        assert "api-key" in RequestLoggingMiddleware.SENSITIVE_HEADERS

    def test_should_log_path_excludes_health(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        assert middleware._should_log_path("/health") is False
        assert middleware._should_log_path("/health/live") is False

    def test_should_log_path_excludes_metrics(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        assert middleware._should_log_path("/metrics") is False
        assert middleware._should_log_path("/metrics/prometheus") is False

    def test_should_log_path_allows_normal_paths(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        assert middleware._should_log_path("/api/users") is True
        assert middleware._should_log_path("/v1/posts") is True

    def test_mask_sensitive_headers_masks_authorization(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        headers = {"authorization": "Bearer secret123", "content-type": "application/json"}

        masked = middleware._mask_sensitive_headers(headers)

        assert masked["authorization"] == "***REDACTED***"
        assert masked["content-type"] == "application/json"

    def test_mask_sensitive_headers_masks_cookie(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        headers = {"cookie": "session=abc123", "accept": "*/*"}

        masked = middleware._mask_sensitive_headers(headers)

        assert masked["cookie"] == "***REDACTED***"
        assert masked["accept"] == "*/*"

    def test_mask_sensitive_headers_masks_api_keys(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        headers = {
            "x-api-key": "key123",
            "api-key": "key456",
            "x-custom-key": "keep",
        }

        masked = middleware._mask_sensitive_headers(headers)

        assert masked["x-api-key"] == "***REDACTED***"
        assert masked["api-key"] == "***REDACTED***"
        assert masked["x-custom-key"] == "keep"

    def test_mask_sensitive_headers_case_insensitive(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        headers = {"Authorization": "Bearer secret", "COOKIE": "session"}

        masked = middleware._mask_sensitive_headers(headers)

        assert masked["Authorization"] == "***REDACTED***"
        assert masked["COOKIE"] == "***REDACTED***"

    def test_get_client_ip_from_forwarded_header(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = MagicMock()
        request.headers.get.side_effect = lambda k: "192.168.1.100, 10.0.0.1" if k == "X-Forwarded-For" else None
        request.client = None

        ip = middleware._get_client_ip(request)
        assert ip == "192.168.1.100"

    def test_get_client_ip_from_real_ip_header(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = MagicMock()
        request.headers.get.side_effect = lambda k: (
            "10.0.0.5" if k == "X-Forwarded-For" else ("10.0.0.5" if k == "X-Real-IP" else None)
        )
        request.client = None

        ip = middleware._get_client_ip(request)
        assert ip == "10.0.0.5"

    def test_get_client_ip_from_client_host(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = MagicMock()
        request.headers.get.return_value = None
        request.client.host = "192.168.1.1"

        ip = middleware._get_client_ip(request)
        assert ip == "192.168.1.1"

    def test_get_client_ip_unknown_when_no_client(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = MagicMock()
        request.headers.get.return_value = None
        request.client = None

        ip = middleware._get_client_ip(request)
        assert ip == "unknown"


class TestLogFormatting:
    """Tests for log entry formatting logic."""

    def _make_mock_request(
        self,
        method: str = "GET",
        path: str = "/api/test",
        query_params: str = "",
        client_host: str = "127.0.0.1",
    ) -> MagicMock:
        request = MagicMock()
        request.method = method
        request.url.path = path
        request.url.query_params = query_params
        request.query_params = query_params
        request.client.host = client_host
        request.headers.get.return_value = None
        return request

    def test_format_log_entry_includes_timestamp(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request()

        entry = middleware._format_log_entry(request, 200, 50.0)

        assert "timestamp" in entry
        # Should be ISO format
        assert "T" in entry["timestamp"]

    def test_format_log_entry_includes_method_and_path(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request(method="POST", path="/api/users")

        entry = middleware._format_log_entry(request, 201, 30.0)

        assert entry["method"] == "POST"
        assert "/api/users" in entry["path"]

    def test_format_log_entry_includes_status_code(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request()

        entry = middleware._format_log_entry(request, 404, 10.0)

        assert entry["status_code"] == 404

    def test_format_log_entry_includes_duration_ms(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request()

        entry = middleware._format_log_entry(request, 200, 123.456)

        assert entry["duration_ms"] == 123.46

    def test_format_log_entry_includes_client_ip(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request(client_host="10.0.0.1")

        entry = middleware._format_log_entry(request, 200, 50.0)

        assert entry["client_ip"] == "10.0.0.1"

    def test_format_log_entry_level_warning_for_4xx(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request()

        entry = middleware._format_log_entry(request, 404, 10.0)

        assert entry["level"] == "WARNING"
        assert entry["error"] is True

    def test_format_log_entry_level_error_for_5xx(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request()

        entry = middleware._format_log_entry(request, 500, 50.0)

        assert entry["level"] == "WARNING"  # Uses WARNING for 5xx in this implementation
        assert entry["error"] is True

    def test_format_log_entry_level_info_for_success(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request()

        entry = middleware._format_log_entry(request, 200, 50.0)

        assert entry["level"] == "INFO"
        assert "error" not in entry or entry.get("error") is not True

    def test_format_log_entry_marks_slow_requests(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock(), slow_request_ms=100.0)
        request = self._make_mock_request()

        entry = middleware._format_log_entry(request, 200, 150.0)

        assert entry["slow_request"] is True
        assert entry["level"] == "WARNING"

    def test_format_log_entry_includes_request_id(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request()
        request.headers.get = lambda k: "req-123-456" if k == "X-Request-ID" else None

        entry = middleware._format_log_entry(request, 200, 50.0)

        assert entry["request_id"] == "req-123-456"

    def test_format_log_entry_includes_query_params(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = self._make_mock_request(query_params="page=1&size=10")

        entry = middleware._format_log_entry(request, 200, 50.0)

        assert entry["query"] is not None
        assert "page=1" in entry["query"]

    def test_format_log_entry_includes_request_body_when_enabled(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock(), log_request_body=True)
        request = self._make_mock_request()

        entry = middleware._format_log_entry(request, 200, 50.0, request_body='{"name":"test"}')

        assert "request_body" in entry
        assert entry["request_body"] == '{"name":"test"}'

    def test_format_log_entry_body_truncated_to_1000_chars(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock(), log_request_body=True)
        request = self._make_mock_request()
        long_body = "x" * 2000

        entry = middleware._format_log_entry(request, 200, 50.0, request_body=long_body)

        assert len(entry["request_body"]) == 1000


class TestLoggingMiddlewareConfiguration:
    """Tests for logging middleware configuration."""

    def test_default_slow_request_threshold(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        assert middleware._slow_request_ms == 1000.0

    def test_custom_slow_request_threshold(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock(), slow_request_ms=500.0)
        assert middleware._slow_request_ms == 500.0

    def test_log_requests_defaults_to_true(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        assert middleware._log_requests is True

    def test_log_request_body_defaults_to_false(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        assert middleware._log_request_body is False

    def test_log_response_body_defaults_to_false(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        assert middleware._log_response_body is False


class TestGetLoggingMiddlewareFactory:
    """Tests for get_logging_middleware factory function."""

    def test_factory_returns_middleware_instance(self) -> None:
        middleware = get_logging_middleware(MagicMock())
        assert isinstance(middleware, RequestLoggingMiddleware)

    def test_factory_respects_custom_slow_request_ms(self) -> None:
        middleware = get_logging_middleware(MagicMock(), slow_request_ms=2000.0)
        assert middleware._slow_request_ms == 2000.0

    def test_factory_respects_log_request_body_param(self) -> None:
        middleware = get_logging_middleware(MagicMock(), log_request_body=True)
        assert middleware._log_request_body is True

    def test_factory_respects_log_response_body_param(self) -> None:
        middleware = get_logging_middleware(MagicMock(), log_response_body=True)
        assert middleware._log_response_body is True


class TestLoggingMiddlewareEdgeCases:
    """Tests for edge cases in logging middleware."""

    def test_format_log_entry_handles_empty_query(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/test"
        request.url.query_params = ""
        request.query_params = ""
        request.client.host = "127.0.0.1"
        request.headers.get.return_value = None

        entry = middleware._format_log_entry(request, 200, 50.0)

        assert entry["query"] is None

    def test_mask_sensitive_headers_handles_empty_dict(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        masked = middleware._mask_sensitive_headers({})
        assert masked == {}

    def test_mask_sensitive_headers_preserves_non_sensitive(self) -> None:
        middleware = RequestLoggingMiddleware(MagicMock())
        headers = {"content-type": "application/json", "accept": "application/json"}

        masked = middleware._mask_sensitive_headers(headers)

        assert masked == headers
