"""Prometheus Metrics Middleware and Endpoint

Provides Prometheus-compatible metrics for monitoring:
- Request counts by method, path, status
- Request latency histograms
- In-flight request tracking
- Custom application metrics

Configuration:
- POLARIS_METRICS_ENABLED: Enable/disable metrics (default: true)
- POLARIS_METRICS_ENDPOINT: Metrics endpoint path (default: /metrics)

Usage:
    from polaris.delivery.http.middleware.metrics import (
        get_metrics_middleware,
        metrics_router,
    )
    app.add_middleware(get_metrics_middleware(app))
    app.include_router(metrics_router)
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


@dataclass
class HistogramBucket:
    """Histogram bucket for latency tracking."""

    upper_bound: float
    count: int = 0


@dataclass
class RequestMetrics:
    """Metrics for a specific route/method combination."""

    request_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    latency_buckets: list = field(
        default_factory=lambda: [
            HistogramBucket(10),
            HistogramBucket(25),
            HistogramBucket(50),
            HistogramBucket(100),
            HistogramBucket(250),
            HistogramBucket(500),
            HistogramBucket(1000),
            HistogramBucket(2500),
            HistogramBucket(5000),
            HistogramBucket(10000),
            HistogramBucket(float("inf")),
        ]
    )


class MetricsCollector:
    """Thread-safe metrics collector."""

    # Standard Prometheus buckets for latency
    BUCKETS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, float("inf")]

    def __init__(self) -> None:
        self._metrics: dict[str, RequestMetrics] = defaultdict(RequestMetrics)
        self._inflight: dict[str, int] = defaultdict(int)
        self._lock = RLock()
        self._start_time = time.time()

    def record_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """Record a completed request."""
        key = f"{method.upper()} {path}"
        with self._lock:
            metric = self._metrics[key]
            metric.request_count += 1
            metric.total_latency_ms += duration_ms

            # Track errors (4xx/5xx)
            if status_code >= 400:
                metric.error_count += 1

            # Update histogram buckets
            for bucket in metric.latency_buckets:
                if duration_ms <= bucket.upper_bound:
                    bucket.count += 1

            # Decrement in-flight
            self._inflight[key] = max(0, self._inflight[key] - 1)

    def start_request(self, method: str, path: str) -> None:
        """Track in-flight request start."""
        key = f"{method.upper()} {path}"
        with self._lock:
            self._inflight[key] += 1

    def get_prometheus_format(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []
        lines.append("# HELP polaris_requests_total Total HTTP requests")
        lines.append("# TYPE polaris_requests_total counter")

        with self._lock:
            for key, metric in self._metrics.items():
                method, path = key.split(" ", 1)
                lines.append(f'polaris_requests_total{{method="{method}",path="{path}"}} {metric.request_count}')

            lines.append("\n# HELP polaris_request_errors_total Total HTTP errors (4xx/5xx)")
            lines.append("# TYPE polaris_request_errors_total counter")
            for key, metric in self._metrics.items():
                method, path = key.split(" ", 1)
                lines.append(
                    f'polaris_request_errors_total{{method="{method}",path="{path}"}} {metric.error_count}'
                )

            lines.append("\n# HELP polaris_request_duration_ms Request duration in milliseconds")
            lines.append("# TYPE polaris_request_duration_ms histogram")
            for key, metric in self._metrics.items():
                method, path = key.split(" ", 1)
                for bucket in metric.latency_buckets:
                    bound = bucket.upper_bound
                    if bound == float("inf"):
                        bound = "+Inf"
                    lines.append(
                        f'polaris_request_duration_ms_bucket{{method="{method}",path="{path}",le="{bound}"}} {bucket.count}'
                    )
                lines.append(
                    f'polaris_request_duration_ms_count{{method="{method}",path="{path}"}} {metric.request_count}'
                )
                if metric.request_count > 0:
                    lines.append(
                        f'polaris_request_duration_ms_sum{{method="{method}",path="{path}"}} {metric.total_latency_ms}'
                    )

            lines.append("\n# HELP polaris_requests_inflight Current in-flight requests")
            lines.append("# TYPE polaris_requests_inflight gauge")
            for key, count in self._inflight.items():
                method, path = key.split(" ", 1)
                lines.append(f'polaris_requests_inflight{{method="{method}",path="{path}"}} {count}')

            lines.append("\n# HELP polaris_uptime_seconds Process uptime in seconds")
            lines.append("# TYPE polaris_uptime_seconds gauge")
            uptime = time.time() - self._start_time
            lines.append(f"polaris_uptime_seconds {uptime}")

        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._metrics.clear()
            self._inflight.clear()
            self._start_time = time.time()


# Global metrics collector (lazy initialization)
_metrics_collector: MetricsCollector | None = None
_metrics_lock = RLock()


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector."""
    global _metrics_collector
    with _metrics_lock:
        if _metrics_collector is None:
            _metrics_collector = MetricsCollector()
        return _metrics_collector


def reset_metrics_for_testing() -> None:
    """Reset global metrics collector for test isolation."""
    global _metrics_collector
    with _metrics_lock:
        _metrics_collector = MetricsCollector()


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware for collecting Prometheus metrics."""

    # Paths to exclude from metrics
    EXCLUDED_PATHS = {
        "/metrics",
        "/health",
        "/favicon.ico",
    }

    def __init__(
        self,
        app: ASGIApp,
        collector: MetricsCollector | None = None,
    ) -> None:
        super().__init__(app)
        self._collector = collector or get_metrics_collector()
        self._enabled = os.environ.get("POLARIS_METRICS_ENABLED", "true").lower() not in ("false", "0", "no", "off")

    def _should_collect(self, path: str) -> bool:
        """Check if path should be tracked."""
        return not any(path.startswith(excluded) for excluded in self.EXCLUDED_PATHS)

    def _normalize_path(self, request: Request) -> str:
        """Normalize path for metric labeling (remove IDs)."""
        path = request.url.path
        # Replace UUIDs
        path = re.sub(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "/{id}", path)
        # Replace numeric IDs
        path = re.sub(r"/\d+", "/{id}", path)
        return path

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with metrics collection."""
        if not self._enabled:
            return await call_next(request)

        path = self._normalize_path(request)

        if not self._should_collect(path):
            return await call_next(request)

        method = request.method

        # Track in-flight
        self._collector.start_request(method, path)

        start_time = time.time()

        try:
            response = await call_next(request)
            status_code = response.status_code
        except (RuntimeError, ValueError):
            status_code = 500
            raise
        finally:
            duration_ms = (time.time() - start_time) * 1000
            self._collector.record_request(method, path, status_code, duration_ms)

        return response


# Create metrics endpoint router
metrics_router = APIRouter(tags=["Monitoring"])


@metrics_router.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus metrics endpoint."""
    parts: list[str] = []

    collector = get_metrics_collector()
    parts.append(collector.get_prometheus_format())

    # Append kernel metrics if available.
    try:
        from polaris.cells.roles.kernel.public.service import get_kernel_metrics_collector

        kernel_collector = get_kernel_metrics_collector()
        kernel_text = kernel_collector.get_prometheus_format()
        if kernel_text:
            parts.append(kernel_text)
    except (ImportError, OSError, RuntimeError):
        pass

    # Append task_market business metrics if available.
    try:
        from polaris.cells.runtime.task_market.internal.metrics import get_task_market_metrics

        tm_metrics = get_task_market_metrics()
        tm_text = tm_metrics.get_prometheus_metrics()
        if tm_text:
            parts.append(tm_text)
    except (ImportError, OSError, RuntimeError):
        pass

    content = "\n".join(parts)
    return Response(
        content=content,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


def get_metrics_middleware(
    app: ASGIApp,
    collector: MetricsCollector | None = None,
) -> MetricsMiddleware:
    """Factory function to create metrics middleware.

    Args:
        app: The ASGI application
        collector: Optional custom metrics collector

    Returns:
        Configured MetricsMiddleware instance
    """
    return MetricsMiddleware(app=app, collector=collector)
