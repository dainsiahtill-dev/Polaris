"""Tests for telemetry and metrics middleware.

Covers:
- MetricsMiddleware: request counts, durations, status codes, endpoint labels
- RequestLoggingMiddleware: structured logging, error logging, header redaction
- AuditContextMiddleware: trace/run/task ID propagation
- Metrics endpoint: Prometheus format, cumulative metrics
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Response as FastAPIResponse
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.middleware.audit_context import (
    AuditContextMiddleware,
    _generate_run_id,
    _generate_task_id,
    _generate_trace_id,
)
from polaris.delivery.http.middleware.logging import (
    RequestLoggingMiddleware,
    get_logging_middleware,
)
from polaris.delivery.http.middleware.metrics import (
    MetricsCollector,
    MetricsMiddleware,
    get_metrics_collector,
    get_metrics_middleware,
    metrics_router,
    reset_metrics_for_testing,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_global_metrics() -> None:
    """Reset global metrics collector before each test for isolation."""
    reset_metrics_for_testing()


@pytest.fixture
def mock_collector() -> MagicMock:
    """Create a mocked MetricsCollector."""
    return MagicMock(spec=MetricsCollector)


@pytest.fixture
def app_with_metrics(mock_collector: MagicMock) -> FastAPI:
    """Create a minimal FastAPI app with metrics middleware."""
    app = FastAPI()
    app.add_middleware(MetricsMiddleware, collector=mock_collector)

    @app.get("/test")
    async def test_endpoint() -> dict[str, str]:
        return {"message": "ok"}

    @app.get("/test/{item_id}")
    async def test_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.post("/test")
    async def test_post() -> dict[str, str]:
        return {"created": "true"}

    @app.get("/error")
    async def error_endpoint() -> None:
        raise RuntimeError("intentional error")

    return app


@pytest.fixture
def app_with_logging() -> FastAPI:
    """Create a minimal FastAPI app with request logging middleware."""
    app = FastAPI()
    app.add_middleware(
        RequestLoggingMiddleware,
        log_requests=True,
        log_request_body=False,
        log_response_body=False,
        slow_request_ms=1000.0,
    )

    @app.get("/log-test")
    async def log_test() -> dict[str, str]:
        return {"message": "ok"}

    @app.get("/log-error")
    async def log_error() -> None:
        raise RuntimeError("boom")

    @app.get("/log-client-error")
    async def log_client_error() -> FastAPIResponse:
        return FastAPIResponse(content="bad request", status_code=400)

    @app.get("/log-server-error")
    async def log_server_error() -> FastAPIResponse:
        return FastAPIResponse(content="server error", status_code=500)

    return app


@pytest.fixture
def app_with_audit() -> FastAPI:
    """Create a minimal FastAPI app with audit context middleware."""
    app = FastAPI()
    app.add_middleware(AuditContextMiddleware, enabled=True)

    @app.get("/audit-test")
    async def audit_test() -> dict[str, str]:
        return {"message": "ok"}

    return app


@pytest.fixture
def app_with_metrics_endpoint() -> FastAPI:
    """Create a minimal FastAPI app exposing the metrics endpoint."""
    app = FastAPI()
    app.include_router(metrics_router)
    return app


# ---------------------------------------------------------------------------
# Metrics Middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_middleware_increments_request_count(
    app_with_metrics: FastAPI,
    mock_collector: MagicMock,
) -> None:
    """MetricsMiddleware should increment request count for each request."""
    async with AsyncClient(
        transport=ASGITransport(app_with_metrics),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/test")
        assert response.status_code == 200

    mock_collector.start_request.assert_called_once_with("GET", "/test")
    mock_collector.record_request.assert_called_once()
    args = mock_collector.record_request.call_args[0]
    assert args[0] == "GET"
    assert args[1] == "/test"
    assert args[2] == 200


@pytest.mark.asyncio
async def test_metrics_middleware_records_duration(
    app_with_metrics: FastAPI,
    mock_collector: MagicMock,
) -> None:
    """MetricsMiddleware should record request duration in milliseconds."""
    async with AsyncClient(
        transport=ASGITransport(app_with_metrics),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/test")
        assert response.status_code == 200

    mock_collector.record_request.assert_called_once()
    args = mock_collector.record_request.call_args[0]
    duration_ms = args[3]
    assert isinstance(duration_ms, float)
    assert duration_ms >= 0.0


@pytest.mark.asyncio
async def test_metrics_middleware_records_status_code(
    app_with_metrics: FastAPI,
    mock_collector: MagicMock,
) -> None:
    """MetricsMiddleware should record the response status code."""
    async with AsyncClient(
        transport=ASGITransport(app_with_metrics),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/test")
        assert response.status_code == 200

    args = mock_collector.record_request.call_args[0]
    assert args[2] == 200


@pytest.mark.asyncio
async def test_metrics_middleware_records_error_status_code(
    app_with_metrics: FastAPI,
    mock_collector: MagicMock,
) -> None:
    """MetricsMiddleware should record 500 when an exception is raised."""
    async with AsyncClient(
        transport=ASGITransport(app_with_metrics),
        base_url="http://test",
    ) as ac:
        with pytest.raises(RuntimeError):
            await ac.get("/error")

    mock_collector.record_request.assert_called_once()
    args = mock_collector.record_request.call_args[0]
    assert args[2] == 500


@pytest.mark.asyncio
async def test_metrics_middleware_endpoint_label_normalizes_ids(
    app_with_metrics: FastAPI,
    mock_collector: MagicMock,
) -> None:
    """MetricsMiddleware should normalize numeric IDs in path labels."""
    async with AsyncClient(
        transport=ASGITransport(app_with_metrics),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/test/42")
        assert response.status_code == 200

    mock_collector.start_request.assert_called_once_with("GET", "/test/{id}")
    args = mock_collector.record_request.call_args[0]
    assert args[1] == "/test/{id}"


@pytest.mark.asyncio
async def test_metrics_middleware_skips_excluded_paths(
    mock_collector: MagicMock,
) -> None:
    """MetricsMiddleware should not record metrics for excluded paths."""
    app = FastAPI()
    app.add_middleware(MetricsMiddleware, collector=mock_collector)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/health")
        assert response.status_code == 200

    mock_collector.start_request.assert_not_called()
    mock_collector.record_request.assert_not_called()


@pytest.mark.asyncio
async def test_metrics_middleware_disabled_via_env(mock_collector: MagicMock) -> None:
    """MetricsMiddleware should skip collection when disabled."""
    app = FastAPI()
    app.add_middleware(MetricsMiddleware, collector=mock_collector)

    @app.get("/data")
    async def data() -> dict[str, str]:
        return {"data": "value"}

    with patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}):
        async with AsyncClient(
            transport=ASGITransport(app),
            base_url="http://test",
        ) as ac:
            response = await ac.get("/data")
            assert response.status_code == 200

    mock_collector.start_request.assert_not_called()
    mock_collector.record_request.assert_not_called()


# ---------------------------------------------------------------------------
# Request Logging Middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logging_middleware_logs_request(
    app_with_logging: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RequestLoggingMiddleware should log method, path, status, and duration."""
    with caplog.at_level(logging.INFO, logger="polaris.delivery.http.middleware.logging"):
        async with AsyncClient(
            transport=ASGITransport(app_with_logging),
            base_url="http://test",
        ) as ac:
            response = await ac.get("/log-test")
            assert response.status_code == 200

    assert any("GET /log-test" in rec.message for rec in caplog.records)
    assert any("200" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_logging_middleware_logs_server_error_responses(
    app_with_logging: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RequestLoggingMiddleware should log 500 responses at ERROR level."""
    with caplog.at_level(logging.ERROR, logger="polaris.delivery.http.middleware.logging"):
        async with AsyncClient(
            transport=ASGITransport(app_with_logging),
            base_url="http://test",
        ) as ac:
            response = await ac.get("/log-server-error")

    assert response.status_code == 500
    assert any("Server error" in rec.message for rec in caplog.records)
    assert any("GET /log-server-error" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_logging_middleware_logs_client_error_responses(
    app_with_logging: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RequestLoggingMiddleware should log 4xx responses at WARNING level."""
    with caplog.at_level(logging.WARNING, logger="polaris.delivery.http.middleware.logging"):
        async with AsyncClient(
            transport=ASGITransport(app_with_logging),
            base_url="http://test",
        ) as ac:
            response = await ac.get("/log-client-error")

    assert response.status_code == 400
    assert any("Client error" in rec.message for rec in caplog.records)
    assert any("GET /log-client-error" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_logging_middleware_redacts_sensitive_headers(
    app_with_logging: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RequestLoggingMiddleware should redact Authorization and Cookie headers."""
    with caplog.at_level(logging.INFO, logger="polaris.delivery.http.middleware.logging"):
        async with AsyncClient(
            transport=ASGITransport(app_with_logging),
            base_url="http://test",
        ) as ac:
            response = await ac.get(
                "/log-test",
                headers={
                    "Authorization": "Bearer secret-token",
                    "Cookie": "session=abc123",
                    "X-Custom": "visible",
                },
            )
            assert response.status_code == 200

    # The middleware itself does not log headers directly in the message,
    # but _mask_sensitive_headers is available for structured log extras.
    # Verify the helper works correctly.
    middleware = RequestLoggingMiddleware(
        app=MagicMock(),
        log_requests=True,
    )
    masked = middleware._mask_sensitive_headers(
        {
            "Authorization": "Bearer secret",
            "Cookie": "session=abc",
            "X-Custom": "visible",
        }
    )
    assert masked["Authorization"] == "***REDACTED***"
    assert masked["Cookie"] == "***REDACTED***"
    assert masked["X-Custom"] == "visible"


@pytest.mark.asyncio
async def test_logging_middleware_adds_response_time_header(
    app_with_logging: FastAPI,
) -> None:
    """RequestLoggingMiddleware should add X-Response-Time header."""
    async with AsyncClient(
        transport=ASGITransport(app_with_logging),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/log-test")
        assert response.status_code == 200
        assert "x-response-time" in response.headers


@pytest.mark.asyncio
async def test_logging_middleware_skips_excluded_paths() -> None:
    """RequestLoggingMiddleware should skip excluded paths like /health."""
    app = FastAPI()
    app.add_middleware(
        RequestLoggingMiddleware,
        log_requests=True,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/health")
        assert response.status_code == 200
        assert "x-response-time" not in response.headers


@pytest.mark.asyncio
async def test_logging_middleware_disabled() -> None:
    """RequestLoggingMiddleware should do nothing when disabled."""
    app = FastAPI()
    app.add_middleware(
        RequestLoggingMiddleware,
        log_requests=False,
    )

    @app.get("/data")
    async def data() -> dict[str, str]:
        return {"data": "value"}

    async with AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/data")
        assert response.status_code == 200
        assert "x-response-time" not in response.headers


@pytest.mark.asyncio
async def test_logging_middleware_slow_request_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RequestLoggingMiddleware should warn on slow requests."""
    app = FastAPI()
    app.add_middleware(
        RequestLoggingMiddleware,
        log_requests=True,
        slow_request_ms=1.0,
    )

    @app.get("/slow")
    async def slow() -> dict[str, str]:
        import asyncio

        await asyncio.sleep(0.05)
        return {"message": "ok"}

    with caplog.at_level(logging.WARNING, logger="polaris.delivery.http.middleware.logging"):
        async with AsyncClient(
            transport=ASGITransport(app),
            base_url="http://test",
        ) as ac:
            response = await ac.get("/slow")
            assert response.status_code == 200

    assert any("Slow request" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Trace Context (Audit Context Middleware)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_context_propagates_trace_id(app_with_audit: FastAPI) -> None:
    """AuditContextMiddleware should propagate X-Trace-ID from request to response."""
    async with AsyncClient(
        transport=ASGITransport(app_with_audit),
        base_url="http://test",
    ) as ac:
        response = await ac.get(
            "/audit-test",
            headers={"X-Trace-ID": "trace-abc-123"},
        )
        assert response.status_code == 200
        assert response.headers["x-trace-id"] == "trace-abc-123"


@pytest.mark.asyncio
async def test_audit_context_propagates_run_id(app_with_audit: FastAPI) -> None:
    """AuditContextMiddleware should propagate X-Run-ID from request to response."""
    async with AsyncClient(
        transport=ASGITransport(app_with_audit),
        base_url="http://test",
    ) as ac:
        response = await ac.get(
            "/audit-test",
            headers={"X-Run-ID": "run-xyz-789"},
        )
        assert response.status_code == 200
        assert response.headers["x-run-id"] == "run-xyz-789"


@pytest.mark.asyncio
async def test_audit_context_propagates_task_id(app_with_audit: FastAPI) -> None:
    """AuditContextMiddleware should propagate X-Task-ID from request to response."""
    async with AsyncClient(
        transport=ASGITransport(app_with_audit),
        base_url="http://test",
    ) as ac:
        response = await ac.get(
            "/audit-test",
            headers={"X-Task-ID": "task-mno-456"},
        )
        assert response.status_code == 200
        assert response.headers["x-task-id"] == "task-mno-456"


@pytest.mark.asyncio
async def test_audit_context_generates_missing_ids(app_with_audit: FastAPI) -> None:
    """AuditContextMiddleware should auto-generate missing trace/run/task IDs."""
    async with AsyncClient(
        transport=ASGITransport(app_with_audit),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/audit-test")
        assert response.status_code == 200
        assert "x-trace-id" in response.headers
        assert "x-run-id" in response.headers
        assert "x-task-id" in response.headers
        assert len(response.headers["x-trace-id"]) > 0
        assert len(response.headers["x-run-id"]) > 0
        assert len(response.headers["x-task-id"]) > 0


@pytest.mark.asyncio
async def test_audit_context_excludes_health() -> None:
    """AuditContextMiddleware should skip excluded paths like /health."""
    app = FastAPI()
    app.add_middleware(AuditContextMiddleware, enabled=True)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/health")
        assert response.status_code == 200
        assert "x-trace-id" not in response.headers
        assert "x-run-id" not in response.headers
        assert "x-task-id" not in response.headers


@pytest.mark.asyncio
async def test_audit_context_disabled() -> None:
    """AuditContextMiddleware should do nothing when disabled."""
    app = FastAPI()
    app.add_middleware(AuditContextMiddleware, enabled=False)

    @app.get("/data")
    async def data() -> dict[str, str]:
        return {"data": "value"}

    async with AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/data")
        assert response.status_code == 200
        assert "x-trace-id" not in response.headers


# ---------------------------------------------------------------------------
# Metrics Collector (Unit)
# ---------------------------------------------------------------------------


def test_metrics_collector_record_request() -> None:
    """MetricsCollector should record requests accurately."""
    collector = MetricsCollector()
    collector.record_request("GET", "/api/test", 200, 45.0)

    output = collector.get_prometheus_format()
    assert 'polaris_requests_total{method="GET",path="/api/test"} 1' in output
    assert 'polaris_request_errors_total{method="GET",path="/api/test"} 0' in output
    assert 'polaris_request_duration_ms_count{method="GET",path="/api/test"} 1' in output
    assert 'polaris_request_duration_ms_sum{method="GET",path="/api/test"} 45.0' in output


def test_metrics_collector_records_errors() -> None:
    """MetricsCollector should count 4xx/5xx as errors."""
    collector = MetricsCollector()
    collector.record_request("POST", "/api/test", 500, 120.0)

    output = collector.get_prometheus_format()
    assert 'polaris_request_errors_total{method="POST",path="/api/test"} 1' in output


def test_metrics_collector_histogram_buckets() -> None:
    """MetricsCollector should place durations into correct histogram buckets."""
    collector = MetricsCollector()
    collector.record_request("GET", "/api/test", 200, 30.0)

    output = collector.get_prometheus_format()
    # 30ms falls into buckets <= 50
    assert 'polaris_request_duration_ms_bucket{method="GET",path="/api/test",le="50"} 1' in output
    # But not into buckets <= 25
    assert 'polaris_request_duration_ms_bucket{method="GET",path="/api/test",le="25"} 0' in output


def test_metrics_collector_cumulative_counts() -> None:
    """MetricsCollector should accumulate counts across multiple requests."""
    collector = MetricsCollector()
    collector.record_request("GET", "/api/test", 200, 10.0)
    collector.record_request("GET", "/api/test", 200, 20.0)
    collector.record_request("POST", "/api/test", 201, 15.0)

    output = collector.get_prometheus_format()
    assert 'polaris_requests_total{method="GET",path="/api/test"} 2' in output
    assert 'polaris_requests_total{method="POST",path="/api/test"} 1' in output


def test_metrics_collector_inflight_tracking() -> None:
    """MetricsCollector should track in-flight requests."""
    collector = MetricsCollector()
    collector.start_request("GET", "/api/test")
    collector.start_request("GET", "/api/test")

    output = collector.get_prometheus_format()
    assert 'polaris_requests_inflight{method="GET",path="/api/test"} 2' in output

    collector.record_request("GET", "/api/test", 200, 5.0)
    output = collector.get_prometheus_format()
    assert 'polaris_requests_inflight{method="GET",path="/api/test"} 1' in output


def test_metrics_collector_reset() -> None:
    """MetricsCollector reset should clear all metrics."""
    collector = MetricsCollector()
    collector.record_request("GET", "/api/test", 200, 10.0)
    collector.reset()

    output = collector.get_prometheus_format()
    assert "polaris_requests_total" in output
    # After reset, no entries for /api/test
    assert "/api/test" not in output


def test_metrics_collector_uptime() -> None:
    """MetricsCollector should export uptime metric."""
    collector = MetricsCollector()
    output = collector.get_prometheus_format()
    assert "# HELP polaris_uptime_seconds" in output
    assert "# TYPE polaris_uptime_seconds gauge" in output
    assert "polaris_uptime_seconds " in output


# ---------------------------------------------------------------------------
# Metrics Endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_format(
    app_with_metrics_endpoint: FastAPI,
) -> None:
    """GET /metrics should return Prometheus text format."""
    async with AsyncClient(
        transport=ASGITransport(app_with_metrics_endpoint),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        content = response.text
        assert "# HELP polaris_requests_total" in content
        assert "# TYPE polaris_requests_total counter" in content


@pytest.mark.asyncio
async def test_metrics_endpoint_cumulative(
    app_with_metrics_endpoint: FastAPI,
) -> None:
    """Metrics endpoint should return cumulative metrics after multiple requests."""
    # First, record some metrics via the global collector
    collector = get_metrics_collector()
    collector.record_request("GET", "/api/users", 200, 25.0)
    collector.record_request("GET", "/api/users", 200, 35.0)
    collector.record_request("POST", "/api/users", 201, 50.0)

    async with AsyncClient(
        transport=ASGITransport(app_with_metrics_endpoint),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/metrics")
        assert response.status_code == 200
        content = response.text
        assert 'polaris_requests_total{method="GET",path="/api/users"} 2' in content
        assert 'polaris_requests_total{method="POST",path="/api/users"} 1' in content


@pytest.mark.asyncio
async def test_metrics_endpoint_includes_histogram(
    app_with_metrics_endpoint: FastAPI,
) -> None:
    """Metrics endpoint should include histogram buckets."""
    collector = get_metrics_collector()
    collector.record_request("GET", "/api/items", 200, 15.0)

    async with AsyncClient(
        transport=ASGITransport(app_with_metrics_endpoint),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/metrics")
        assert response.status_code == 200
        content = response.text
        assert 'polaris_request_duration_ms_bucket{method="GET",path="/api/items",le="25"} 1' in content
        assert 'polaris_request_duration_ms_count{method="GET",path="/api/items"} 1' in content
        assert 'polaris_request_duration_ms_sum{method="GET",path="/api/items"} 15.0' in content


@pytest.mark.asyncio
async def test_metrics_endpoint_empty_collector(app_with_metrics_endpoint: FastAPI) -> None:
    """Metrics endpoint should still return valid Prometheus format with no data."""
    reset_metrics_for_testing()

    async with AsyncClient(
        transport=ASGITransport(app_with_metrics_endpoint),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/metrics")
        assert response.status_code == 200
        content = response.text
        assert "# HELP polaris_requests_total" in content
        assert "# TYPE polaris_requests_total counter" in content
        assert "# HELP polaris_uptime_seconds" in content


# ---------------------------------------------------------------------------
# Factory Functions
# ---------------------------------------------------------------------------


def test_get_metrics_middleware_factory() -> None:
    """get_metrics_middleware should return a MetricsMiddleware instance."""
    app = FastAPI()
    middleware = get_metrics_middleware(app)
    assert isinstance(middleware, MetricsMiddleware)


def test_get_logging_middleware_factory() -> None:
    """get_logging_middleware should return a RequestLoggingMiddleware instance."""
    app = FastAPI()
    middleware = get_logging_middleware(app, log_requests=True)
    assert isinstance(middleware, RequestLoggingMiddleware)
    assert middleware._log_requests is True


# ---------------------------------------------------------------------------
# ID Generators
# ---------------------------------------------------------------------------


def test_generate_trace_id_format() -> None:
    """_generate_trace_id should return a 16-character hex string."""
    trace_id = _generate_trace_id()
    assert len(trace_id) == 16
    assert all(c in "0123456789abcdef" for c in trace_id)


def test_generate_run_id_format() -> None:
    """_generate_run_id should return a valid UUID string."""
    run_id = _generate_run_id()
    parts = run_id.split("-")
    assert len(parts) == 5


def test_generate_task_id_format() -> None:
    """_generate_task_id should return a valid UUID string."""
    task_id = _generate_task_id()
    parts = task_id.split("-")
    assert len(parts) == 5
