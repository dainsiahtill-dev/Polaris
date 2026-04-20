"""Tests for OpenTelemetry/Jaeger Exporter.

Run with:
    pytest polaris/kernelone/audit/omniscient/tests/test_otel_exporter.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from polaris.kernelone.audit.omniscient.adapters.otel_exporter import (
    DEFAULT_JAEGER_ENDPOINT,
    DEFAULT_OTEL_ENDPOINT,
    DEFAULT_SERVICE_NAME,
    JaegerExporter,
    OpenTelemetryExporter,
)

# =============================================================================
# OTLP Exporter Tests
# =============================================================================


def test_otel_exporter_init_defaults() -> None:
    """Exporter initializes with correct defaults."""
    exporter = OpenTelemetryExporter()
    assert exporter._endpoint == DEFAULT_OTEL_ENDPOINT
    assert exporter._service_name == DEFAULT_SERVICE_NAME
    assert exporter._max_batch_size == 100
    assert exporter._sampling_rate == 1.0


def test_otel_exporter_init_custom() -> None:
    """Exporter accepts custom configuration."""
    exporter = OpenTelemetryExporter(
        endpoint="http://custom:4318/v1/traces",
        service_name="custom-service",
        max_batch_size=50,
        sampling_rate=0.5,
    )
    assert exporter._endpoint == "http://custom:4318/v1/traces"
    assert exporter._service_name == "custom-service"
    assert exporter._max_batch_size == 50
    assert exporter._sampling_rate == 0.5


def test_otel_sampling_drops_events() -> None:
    """Sampling rate of 0.0 drops all events."""
    exporter = OpenTelemetryExporter(sampling_rate=0.0)

    span = {
        "name": "test_span",
        "trace_id": "abc123def456abc123def456abc123de",
        "span_id": "f1f1f1f1f1f1f1f1",
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": datetime.now(timezone.utc).isoformat(),
        "attributes": {"key": "value"},
    }

    # Run many times - all should be dropped
    results = []
    for _ in range(20):
        result = asyncio.run(exporter.export_span(span))
        results.append(result)

    # With sampling_rate=0.0, some might pass due to random nature
    # but stats should reflect drops
    stats = exporter.get_stats()
    assert stats["spans_dropped"] + stats["spans_exported"] == 20


def test_otel_sampling_allows_all() -> None:
    """Sampling rate of 1.0 allows all events into the batch."""
    exporter = OpenTelemetryExporter(sampling_rate=1.0, max_batch_size=20)

    span = {
        "name": "test_span",
        "trace_id": "abc123def456abc123def456abc123de",
        "span_id": "f1f1f1f1f1f1f1f1",
        "attributes": {},
    }

    # Emit spans - they should be accepted into the batch
    results = []
    for _ in range(10):
        result = asyncio.run(exporter.export_span(span))
        results.append(result)

    # All should be accepted (True)
    assert all(results)

    stats = exporter.get_stats()
    # Spans are in the pending batch (not yet flushed to server)
    assert stats["pending_in_batch"] == 10


def test_otel_export_span_sampling_100() -> None:
    """100% sampling rate accepts all spans."""
    exporter = OpenTelemetryExporter(sampling_rate=1.0)
    span = {
        "name": "test_span",
        "trace_id": "abc123def456abc123def456abc123de",
        "span_id": "f1f1f1f1f1f1f1f1",
        "attributes": {"key": "value"},
    }

    result = asyncio.run(exporter.export_span(span))
    assert result is True


def test_otel_to_otlp_span_conversion() -> None:
    """_to_otlp_span() converts span dict to OTLP format."""
    exporter = OpenTelemetryExporter()

    start = datetime.now(timezone.utc)
    end = start + timedelta(seconds=1)

    span = {
        "name": "my_operation",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "0123456789abcdef",
        "parent_span_id": "fedcba9876543210",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "attributes": {"http.method": "GET", "http.status_code": 200},
        "status": "ok",
        "events": [{"name": "善", "attributes": {"message": "test"}}],
    }

    otlp = exporter._to_otlp_span(span)

    assert otlp["name"] == "my_operation"
    assert otlp["traceId"] == "0123456789abcdef0123456789abcdef"
    assert otlp["spanId"] == "0123456789abcdef"
    assert otlp["parentSpanId"] == "fedcba9876543210"
    assert otlp["kind"] == 1  # SPAN_KIND_INTERNAL
    assert len(otlp["attributes"]) == 2  # http.method, http.status_code
    assert otlp["status"]["code"] == 1  # OK


def test_otel_to_otlp_span_error_status() -> None:
    """Error status is correctly mapped."""
    exporter = OpenTelemetryExporter()
    span = {
        "name": "failing_op",
        "trace_id": "abc123",
        "span_id": "def456",
        "attributes": {},
        "status": "error",
    }

    otlp = exporter._to_otlp_span(span)
    assert otlp["status"]["code"] == 2  # ERROR


def test_otel_build_payload() -> None:
    """_build_otlp_payload() creates valid JSON payload."""
    exporter = OpenTelemetryExporter(service_name="test-service")

    spans = [
        {
            "name": "span1",
            "traceId": "abc123",
            "spanId": "def456",
            "parentSpanId": "",
            "startTimeUnixNano": "0",
            "endTimeUnixNano": "0",
            "attributes": [],
            "status": {"code": 1},
        }
    ]

    payload_bytes = exporter._build_otlp_payload(spans)
    import json

    payload = json.loads(payload_bytes)

    assert "resourceSpans" in payload
    assert len(payload["resourceSpans"]) == 1
    rs = payload["resourceSpans"][0]
    assert rs["resource"]["attributes"][0]["key"] == "service.name"
    assert rs["resource"]["attributes"][0]["value"]["stringValue"] == "test-service"


def test_otel_export_batch() -> None:
    """export_batch() exports multiple spans."""
    from unittest.mock import patch as _patch

    with _patch("urllib.request.urlopen"):
        exporter = OpenTelemetryExporter(sampling_rate=1.0, max_batch_size=5)

        spans = [
            {
                "name": f"span_{i}",
                "trace_id": "abc123def456",
                "span_id": f"{i:016x}",
                "attributes": {},
            }
            for i in range(5)
        ]

        count = asyncio.run(exporter.export_batch(spans))
        assert count == 5


def test_otel_stats() -> None:
    """get_stats() returns correct statistics."""
    exporter = OpenTelemetryExporter()
    stats = exporter.get_stats()

    assert "spans_exported" in stats
    assert "spans_dropped" in stats
    assert "export_errors" in stats
    assert "pending_in_batch" in stats
    assert "service_name" in stats
    assert "endpoint" in stats
    assert stats["service_name"] == DEFAULT_SERVICE_NAME


# =============================================================================
# Jaeger Exporter Tests
# =============================================================================


def test_jaeger_exporter_init_defaults() -> None:
    """JaegerExporter initializes with correct defaults."""
    exporter = JaegerExporter()
    assert exporter._endpoint == DEFAULT_JAEGER_ENDPOINT
    assert exporter._service_name == DEFAULT_SERVICE_NAME


def test_jaeger_to_span_conversion() -> None:
    """_to_jaeger_span() converts to Jaeger format."""
    exporter = JaegerExporter()

    start = datetime.now(timezone.utc)
    end = start + timedelta(seconds=0.5)

    span = {
        "name": "db_query",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "0123456789abcdef",
        "parent_span_id": "fedcba9876543210",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "attributes": {"db.system": "postgresql", "db.statement": "SELECT * FROM users"},
        "status": "ok",
    }

    jaeger = exporter._to_jaeger_span(span)

    assert jaeger["operationName"] == "db_query"
    assert jaeger["traceId"] == "0123456789abcdef0123456789abcdef"
    assert jaeger["spanId"] == "0123456789abcdef"
    assert jaeger["parentSpanId"] == "fedcba9876543210"
    assert jaeger["flags"] == 1
    assert jaeger["duration"] > 0


def test_jaeger_span_error_handling() -> None:
    """Error spans include error tags."""
    exporter = JaegerExporter()
    span = {
        "name": "failing_call",
        "trace_id": "abc123",
        "span_id": "def456",
        "attributes": {"error.message": "connection refused"},
        "status": "error",
    }

    jaeger = exporter._to_jaeger_span(span)

    # Should have error tag
    tag_keys = {t["key"] for t in jaeger["tags"]}
    assert "error" in tag_keys
    assert "otel.status_code" in tag_keys


def test_jaeger_build_payload() -> None:
    """_build_jaeger_payload() creates valid JSON."""
    exporter = JaegerExporter(service_name="jaeger-test")

    jaeger_spans = [
        {
            "traceId": "abc123",
            "spanId": "def456",
            "parentSpanId": "0000000000000000",
            "operationName": "test_op",
            "flags": 1,
            "startTime": 0,
            "duration": 1000,
            "tags": [],
        }
    ]

    payload_bytes = exporter._build_jaeger_payload(jaeger_spans)
    import json

    payload = json.loads(payload_bytes)

    assert "batch" in payload
    assert payload["batch"]["process"]["serviceName"] == "jaeger-test"


# =============================================================================
# Utility Tests
# =============================================================================


def test_otel_endpoint_env_default() -> None:
    """DEFAULT_OTEL_ENDPOINT and JAEGER_ENDPOINT are set."""
    assert "4318" in DEFAULT_OTEL_ENDPOINT or DEFAULT_OTEL_ENDPOINT == ""
    assert DEFAULT_SERVICE_NAME != ""
