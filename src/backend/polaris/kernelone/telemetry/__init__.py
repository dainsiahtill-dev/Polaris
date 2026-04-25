"""KernelOne telemetry subsystem.

Provides unified metrics, structured logging, and trace context propagation.
All KernelOne subsystems use this layer for observability — no direct
logging.getLogger() calls outside of this module.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all file I/O uses encoding="utf-8"
"""

from __future__ import annotations

from .benchmark_runner import (
    BenchmarkResult,
    BenchmarkRunner,
    BenchmarkSample,
    benchmark,
    get_runner,
)
from .debug_stream import (
    debug_stream_session,
    emit_debug_event,
    is_debug_stream_enabled,
)
from .logging import KernelLogger, get_logger
from .metrics import (
    METRIC_AGENT_E2E_SUCCESS,
    METRIC_COGNITIVE_DRIFT,
    METRIC_CONTEXT_LATENCY_P95,
    METRIC_FALLBACK_SUCCESS,
    METRIC_HITL_TIMEOUT,
    METRIC_SEMANTIC_BOUNDARY,
    BusinessMetrics,
    Counter,
    Gauge,
    Histogram,
    MetricsRecorder,
    Timer,
    get_business_metrics,
    record_metric,
)
from .metrics_collector import (
    AggregatedMetric,
    MetricsCollector,
    MetricType,
    counted,
    gauge,
    get_collector,
    timed,
)
from .trace import TraceCarrier, TraceContext, get_trace_id, new_trace_id, set_trace_id, trace_context

__all__ = [
    "METRIC_AGENT_E2E_SUCCESS",
    "METRIC_COGNITIVE_DRIFT",
    "METRIC_CONTEXT_LATENCY_P95",
    "METRIC_FALLBACK_SUCCESS",
    "METRIC_HITL_TIMEOUT",
    "METRIC_SEMANTIC_BOUNDARY",
    "AggregatedMetric",
    "BenchmarkResult",
    "BenchmarkRunner",
    "BenchmarkSample",
    "BusinessMetrics",
    "Counter",
    "Gauge",
    "Histogram",
    "KernelLogger",
    "MetricType",
    "MetricsCollector",
    "MetricsRecorder",
    "Timer",
    "TraceCarrier",
    "TraceContext",
    "benchmark",
    "counted",
    "debug_stream_session",
    "emit_debug_event",
    "gauge",
    "get_business_metrics",
    "get_collector",
    "get_logger",
    "get_runner",
    "get_trace_id",
    "is_debug_stream_enabled",
    "new_trace_id",
    "record_metric",
    "set_trace_id",
    "timed",
    "trace_context",
]
